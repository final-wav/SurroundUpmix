"""Dynamic 3D trajectory generation and psychoacoustic motion engine for Dolby Atmos objects.

Features:
1. Full-Room 3D Spatial Trajectories: Objects naturally utilize the full room volume
   (Front, Sides, Rear corners, and Heights) across X, Y in [-1.0, 1.0] and Z in [0.0, 1.0].
2. Profile-Aware Musical Motion:
   - "guitar": Deep side/rear accompaniment with energetic solo sweeps and diagonal glides.
   - "keys": Expansive diagonal breathing, wide side/rear envelopment, and shimmer elevation.
   - "backing": Rear surround arch with chorus expansion and 360° vocal orbit.
   - "vocal": Front center anchor with intimate whisper proximity and pitch elevation.
   - "fx": Full 3D room traveler (spiral risers, corner-to-corner delay sweeps).
3. Song-Synchronized Trajectory Curves: Smooth phrase modulation driven by energy onsets,
   spectral brightness, and panning dynamics.
"""
import numpy as np


def compute_short_time_features(data, sr, block_sec=0.20, gate_db=-60.0):
    """Compute short-time pan, RMS energy, pan velocity, whisper intimacy, and centroid.

    data: (n, 2) float32 stereo array.
    Returns:
      pans_smooth: (num_blocks,) array in [-1.0, 1.0]
      energies: (num_blocks,) RMS amplitude
      velocities: (num_blocks,) derivative dp/dt
      intimacies: (num_blocks,) whisper/intimacy score in [0.0, 1.0]
      centroids: (num_blocks,) spectral centroid in Hz
      peakinesses: (num_blocks,) peak-to-mean spectral ratio (tonal vs noise)
    """
    n = len(data)
    block_samples = max(1, int(sr * block_sec))
    num_blocks = int(np.ceil(n / block_samples))
    if num_blocks == 0:
        z = np.zeros(0, dtype=np.float32)
        return z, z, z, z, z, z

    pans = np.zeros(num_blocks, dtype=np.float32)
    energies = np.zeros(num_blocks, dtype=np.float32)
    intimacies = np.zeros(num_blocks, dtype=np.float32)
    centroids = np.zeros(num_blocks, dtype=np.float32)
    peakinesses = np.zeros(num_blocks, dtype=np.float32)

    last_active_pan = 0.0
    gate_thresh = 10.0 ** (gate_db / 10.0)

    freqs = np.fft.rfftfreq(block_samples, 1.0 / sr)
    low_mask = freqs < 600.0
    high_mask = (freqs >= 2500.0) & (freqs <= 9500.0)

    for i in range(num_blocks):
        s = i * block_samples
        e = min(n, s + block_samples)
        chunk = data[s:e]
        if chunk.ndim == 1 or chunk.shape[1] < 2:
            el = er = float(np.mean(chunk ** 2))
            mono_chunk = chunk
        else:
            el = float(np.mean(chunk[:, 0] ** 2))
            er = float(np.mean(chunk[:, 1] ** 2))
            mono_chunk = (chunk[:, 0] + chunk[:, 1]) * 0.5

        tot = el + er
        energies[i] = np.sqrt(tot * 0.5)

        if tot < gate_thresh:
            last_active_pan *= 0.85
            pans[i] = last_active_pan
            intimacies[i] = 0.0
            centroids[i] = 1000.0
            peakinesses[i] = 1.0
        else:
            p = (er - el) / (tot + 1e-12)
            pans[i] = float(np.clip(p, -1.0, 1.0))
            last_active_pan = pans[i]

            # Spectral analysis
            if len(mono_chunk) < block_samples:
                mono_chunk = np.pad(mono_chunk, (0, block_samples - len(mono_chunk)))
            spec = np.abs(np.fft.rfft(mono_chunk)) + 1e-12
            spec_pow = spec ** 2
            sum_spec = float(np.sum(spec))

            # Spectral centroid
            centroids[i] = float(np.sum(freqs * spec) / sum_spec)

            # Peakiness: tonal pure tones have high peakiness (>40), whispers have low peakiness (<30)
            peakiness = float(np.max(spec) / np.mean(spec))
            peakinesses[i] = peakiness

            # Intimacy (Whisper cue: breathy wideband high-frequency dominance)
            p_low = float(np.sum(spec_pow[low_mask]))
            p_high = float(np.sum(spec_pow[high_mask]))
            if p_high > 1.4 * (p_low + 1e-6) and peakiness < 35.0 and energies[i] > 1e-4:
                ratio = p_high / (p_low + 1e-6)
                intimacies[i] = float(np.clip((ratio - 1.4) * 0.35, 0.0, 1.0))
            else:
                intimacies[i] = 0.0

    # Smooth pan curve with a 3-tap filter
    if num_blocks >= 3:
        kernel = np.array([0.15, 0.70, 0.15], dtype=np.float32)
        pans_smooth = np.convolve(pans, kernel, mode="same")
    else:
        pans_smooth = pans

    # Compute pan velocity (derivative dp/dt)
    velocities = np.zeros(num_blocks, dtype=np.float32)
    if num_blocks >= 2:
        velocities[:-1] = (pans_smooth[1:] - pans_smooth[:-1]) / block_sec
        velocities[-1] = velocities[-2] if num_blocks > 2 else 0.0
    if num_blocks >= 3:
        velocities = np.convolve(velocities, np.array([0.2, 0.6, 0.2], dtype=np.float32), mode="same")

    return pans_smooth, energies, velocities, intimacies, centroids, peakinesses


def compute_short_time_pan(data, sr, block_sec=0.20, gate_db=-60.0):
    """Backward-compatible helper returning (pans_smooth, energies, block_samples)."""
    pans, energies, _, _, _, _ = compute_short_time_features(data, sr, block_sec=block_sec, gate_db=gate_db)
    return pans, energies, max(1, int(sr * block_sec))


def build_dynamic_blocks(data, sr, base_x=0.0, base_y=0.0, base_z=0.25, block_sec=0.20,
                         pan_range=0.50, z_lift=0.20,
                         orbit=True, intimacy_proximity=True, pitch_elevation=True,
                         profile="auto", motion_mode="dynamic", intensity=1.0):
    """Build [(rtime, duration, x, y, z), ...] blocks with psychoacoustic full-room 3D motion.

    base_x: nominal resting X position (-1.0 Left to +1.0 Right)
    base_y: resting Y position (-1.0 Rear to +1.0 Front)
    base_z: resting Z position (0.0 Floor to 1.0 Ceiling)
    profile: "guitar", "keys", "backing", "vocal", "fx", "auto"
    motion_mode: "subtle", "dynamic" (default), "expressive"
    intensity: motion depth scaling multiplier (default 1.0)
    """
    pans, energies, vels, intimacies, centroids, peakinesses = compute_short_time_features(
        data, sr, block_sec=block_sec)
    n = len(data)
    total_sec = n / float(sr)
    num_blocks = len(pans)
    if num_blocks == 0:
        return [(0.0, total_sec, base_x, base_y, base_z)]

    max_e = float(np.max(energies)) if len(energies) and np.max(energies) > 1e-6 else 1.0

    # Motion mode scaling
    mode_scale = 1.0
    if motion_mode == "subtle":
        mode_scale = 0.50
    elif motion_mode == "expressive":
        mode_scale = 1.50
    eff_intensity = float(np.clip(intensity * mode_scale, 0.0, 2.5))

    # Compute energy envelope onsets and smoothed dynamic curve
    energy_env = energies / max_e
    if num_blocks >= 5:
        # Smooth energy tracking for phrase progression
        e_kernel = np.ones(5, dtype=np.float32) / 5.0
        energy_smooth = np.convolve(energy_env, e_kernel, mode="same")
    else:
        energy_smooth = energy_env

    # Detect onsets / energy bursts
    onsets = np.zeros(num_blocks, dtype=np.float32)
    if num_blocks >= 2:
        onsets[1:] = np.maximum(0.0, energy_env[1:] - energy_env[:-1])

    # Musical phrase slow drift accumulator (cycles over ~12-16 seconds)
    cycle_period_sec = 14.0
    t_blocks = np.arange(num_blocks, dtype=np.float32) * block_sec
    phrase_phase = (2.0 * np.pi * t_blocks / cycle_period_sec)

    raw_x = np.zeros(num_blocks, dtype=np.float32)
    raw_y = np.zeros(num_blocks, dtype=np.float32)
    raw_z = np.zeros(num_blocks, dtype=np.float32)

    for i in range(num_blocks):
        p = float(pans[i])
        vel = float(vels[i])
        intim = float(intimacies[i])
        cent = float(centroids[i])
        peak = float(peakinesses[i])
        e = float(energy_smooth[i])
        ons = float(onsets[i])
        ph = float(phrase_phase[i])

        # Default resting coordinates
        x = base_x + p * pan_range * eff_intensity
        y = base_y
        z = base_z + e * z_lift * eff_intensity

        # -------------------------------------------------------------
        # PROFILE-SPECIFIC FULL-ROOM 3D TRAJECTORY ENGINES
        # -------------------------------------------------------------

        if profile == "guitar":
            # Guitar Profile: Side/Rear corner presence on rhythm, dynamic diagonal sweeps on solos
            # Base resting zone: deep left or right (e.g. base_x ~ -0.75, base_y ~ -0.65)
            # 1. Depth modulation: High energy (solos, riffs) pushes forward along diagonal; rhythm sits deep rear
            y_depth_sweep = -0.70 + (e * 0.85 + ons * 0.40) * eff_intensity
            # 2. Diagonal drift across musical phrases
            x_diag_drift = base_x + 0.35 * np.sin(ph) * eff_intensity + p * 0.40
            y_diag_drift = y_depth_sweep + 0.25 * np.cos(ph) * eff_intensity

            x = x_diag_drift
            y = y_diag_drift
            # Solo elevation on screaming notes (bends / high harmonics)
            if cent > 2000.0:
                z += float(np.clip((cent - 2000.0) / 3500.0, 0.0, 0.45)) * eff_intensity

        elif profile == "keys":
            # Piano / Keys / Synth: Expansive diagonal breathing, wide side/rear envelopment, shimmer lift
            # Base resting zone: side/rear right (base_x ~ +0.75, base_y ~ -0.50)
            x_drift = base_x + 0.30 * np.cos(ph) * eff_intensity + p * 0.40
            y_drift = -0.55 + 0.45 * np.sin(ph * 0.7) * eff_intensity + (e * 0.30)
            x = x_drift
            y = y_drift
            # Shimmering height on bright synth filters and high arpeggios
            if cent > 2200.0 and peak >= 20.0:
                high_factor = float(np.clip((cent - 2200.0) / 4000.0, 0.0, 0.50))
                z += high_factor * eff_intensity

        elif profile == "backing":
            # Backing Vocals: Rear surround arch with chorus expansion and 360° orbit
            # Base resting zone: rear surround (base_x ~ +-0.85, base_y ~ -0.60)
            # Expands along side walls towards front when chorus/energy swells
            y_arch = base_y + (e * 0.50 + ons * 0.25) * eff_intensity
            x_arch = base_x * (1.0 + 0.20 * np.sin(ph * 0.5)) + p * 0.30
            x = x_arch
            y = y_arch
            z = base_z + (e * 0.30) * eff_intensity

        elif profile == "vocal":
            # Lead Vocal: Stable Front Center anchor with whisper near-field proximity
            x = base_x + p * 0.15 * eff_intensity   # subtle micro-sway, strictly centered
            y = base_y
            z = base_z
            # Pitch elevation on high belted notes
            if cent > 2800.0 and e > 0.3:
                z += float(np.clip((cent - 2800.0) / 3000.0, 0.0, 0.35)) * eff_intensity

        elif profile == "fx":
            # Ear Candy / FX: Full 3D room traveler (spiral risers, corner-to-corner delay sweeps)
            r_orbit = 0.85 * eff_intensity
            x = float(np.clip(r_orbit * np.sin(ph * 1.5) + p * 0.5, -1.0, 1.0))
            y = float(np.clip(r_orbit * np.cos(ph * 1.5), -1.0, 1.0))
            z = 0.30 + 0.50 * np.abs(np.sin(ph * 0.75)) * eff_intensity

        else: # "auto" / generic
            # Blend base coordinates with energy depth breathing
            y_travel = base_y + (e * 0.40 * (1.0 if base_y < 0 else -0.30)) * eff_intensity
            x = base_x + p * pan_range * eff_intensity
            y = y_travel

        # -------------------------------------------------------------
        # UNIVERSAL PSYCHOACOUSTIC OVERLAYS
        # -------------------------------------------------------------

        # Overlay 1: 360° Orbit Mode on Active Panning (Ping-Pong / Sweeps)
        if orbit and abs(vel) > 0.30:
            motion_speed = float(np.clip((abs(vel) - 0.30) * 1.8, 0.0, 1.0)) * eff_intensity
            if motion_speed > 0:
                r_circ = 0.85
                x_orbit = float(np.clip(p * r_circ, -r_circ, r_circ))
                y_circ_mag = float(np.sqrt(max(0.01, r_circ ** 2 - x_orbit ** 2)))
                y_orbit = y_circ_mag if vel >= 0 else -y_circ_mag
                x = (1.0 - motion_speed) * x + motion_speed * x_orbit
                y = (1.0 - motion_speed) * y + motion_speed * y_orbit

        # Overlay 2: Intimacy & Whisper Near-Field Proximity (pulls directly to ear for vocals)
        if intimacy_proximity and intim > 0.05 and profile in ("vocal", "auto"):
            y_ear = -0.10
            y = (1.0 - intim) * y + intim * y_ear
            z = (1.0 - intim) * z + intim * 0.05

        # Overlay 3: High Shimmering Pitch Elevation
        if pitch_elevation and cent > 3000.0 and peak >= 25.0 and profile != "guitar" and profile != "keys":
            high_factor = float(np.clip((cent - 3000.0) / 4000.0, 0.0, 0.35)) * eff_intensity
            z += high_factor

        raw_x[i] = float(np.clip(x, -1.0, 1.0))
        raw_y[i] = float(np.clip(y, -1.0, 1.0))
        raw_z[i] = float(np.clip(z, 0.0, 1.0))

    # -----------------------------------------------------------------
    # TRAJECTORY SMOOTHING (Ensures fluid motion without jitter)
    # -----------------------------------------------------------------
    if num_blocks >= 3:
        t_kernel = np.array([0.20, 0.60, 0.20], dtype=np.float32)
        smooth_x = np.convolve(raw_x, t_kernel, mode="same")
        smooth_y = np.convolve(raw_y, t_kernel, mode="same")
        smooth_z = np.convolve(raw_z, t_kernel, mode="same")
    else:
        smooth_x, smooth_y, smooth_z = raw_x, raw_y, raw_z

    blocks = []
    for i in range(num_blocks):
        rt = i * block_sec
        dur = min(block_sec, total_sec - rt)
        if dur <= 0:
            break
        blocks.append((rt, dur, float(smooth_x[i]), float(smooth_y[i]), float(smooth_z[i])))

    return blocks
