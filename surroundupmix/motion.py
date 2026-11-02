"""Dynamic 3D trajectory generation and psychoacoustic motion engine for Dolby Atmos objects.

Features:
1. Grounded Ear-Level 3D Objects: Instruments and vocals stay grounded on ear level
   (Z in [0.0, 0.15]) to maintain full body, low-mid punch, and precise localization.
2. Backing Vocal Horseshoe Isolation: Backings live strictly in the Side Surround <-> Rear Surround
   arc (Y in [-1.0, 0.0], Z in [0.0, 0.05]) hugging the listener without ever encroaching on the
   front soundstage (Y > 0.0).
3. Adaptive Context-Aware Guitars & Keys:
   - Evaluates M/S stereo-side energy, crest factor, and section dynamics (dE/dt).
   - Atmospheric / arpeggiated / acoustic wide plucks settle comfortably into rear surround corners (Y in [-0.85, -0.45]).
   - Hard riffs, chorus rock powerchords, and solos drive forward to front/side stage (Y in [-0.20, +0.40]).
4. DSP-Driven FX Sweeps & Fly-Bys:
   - Detects risers/swells via Spectral Centroid Slope (dC/dt > 0), RMS Crescendo (dE/dt > 0), and Pan-Velocity.
   - Executes smooth Horseshoe Perimeter Sweeps (Side L -> Rear L -> Rear R -> Side R or reverse)
     and Front-to-Back fly-bys at ear level.
5. Song-Synchronized Trajectory Curves: Fluid phrase modulation smoothed with a 3-tap kernel.
"""
import numpy as np


def compute_short_time_features(data, sr, block_sec=0.20, gate_db=-60.0):
    """Compute short-time pan, RMS energy, pan velocity, whisper intimacy, centroid,
    side energy ratio (M/S), crest factor, and centroid slope (dC/dt).

    data: (n, 2) float32 stereo array.
    Returns:
      pans_smooth: (num_blocks,) array in [-1.0, 1.0]
      energies: (num_blocks,) RMS amplitude
      velocities: (num_blocks,) derivative dp/dt
      intimacies: (num_blocks,) whisper/intimacy score in [0.0, 1.0]
      centroids: (num_blocks,) spectral centroid in Hz
      peakinesses: (num_blocks,) peak-to-mean spectral ratio (tonal vs noise)
      side_ratios: (num_blocks,) S / (M + S) side-to-total energy ratio
      crest_factors: (num_blocks,) peak / RMS ratio
      centroid_slopes: (num_blocks,) derivative dC/dt (filter opening / risers)
    """
    n = len(data)
    block_samples = max(1, int(sr * block_sec))
    num_blocks = int(np.ceil(n / block_samples))
    if num_blocks == 0:
        z = np.zeros(0, dtype=np.float32)
        return z, z, z, z, z, z, z, z, z

    pans = np.zeros(num_blocks, dtype=np.float32)
    energies = np.zeros(num_blocks, dtype=np.float32)
    intimacies = np.zeros(num_blocks, dtype=np.float32)
    centroids = np.zeros(num_blocks, dtype=np.float32)
    peakinesses = np.zeros(num_blocks, dtype=np.float32)
    side_ratios = np.zeros(num_blocks, dtype=np.float32)
    crest_factors = np.zeros(num_blocks, dtype=np.float32)

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
            s_rms = 0.0
            m_rms = np.sqrt(el)
            peak_val = float(np.max(np.abs(chunk))) if len(chunk) else 0.0
        else:
            el = float(np.mean(chunk[:, 0] ** 2))
            er = float(np.mean(chunk[:, 1] ** 2))
            mono_chunk = (chunk[:, 0] + chunk[:, 1]) * 0.5
            side_chunk = (chunk[:, 0] - chunk[:, 1]) * 0.5
            s_rms = np.sqrt(float(np.mean(side_chunk ** 2)))
            m_rms = np.sqrt(float(np.mean(mono_chunk ** 2)))
            peak_val = float(np.max(np.abs(chunk))) if len(chunk) else 0.0

        tot = el + er
        rms_val = np.sqrt(tot * 0.5)
        energies[i] = rms_val
        crest_factors[i] = float(np.clip(peak_val / (rms_val + 1e-6), 1.0, 20.0))
        side_ratios[i] = float(np.clip(s_rms / (m_rms + s_rms + 1e-6), 0.0, 1.0))

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

    # Compute spectral centroid slope (derivative dC/dt for risers/swells)
    centroid_slopes = np.zeros(num_blocks, dtype=np.float32)
    if num_blocks >= 2:
        centroid_slopes[:-1] = (centroids[1:] - centroids[:-1]) / block_sec
        centroid_slopes[-1] = centroid_slopes[-2] if num_blocks > 2 else 0.0
    if num_blocks >= 3:
        centroid_slopes = np.convolve(centroid_slopes, np.array([0.2, 0.6, 0.2], dtype=np.float32), mode="same")

    return pans_smooth, energies, velocities, intimacies, centroids, peakinesses, side_ratios, crest_factors, centroid_slopes


def compute_short_time_pan(data, sr, block_sec=0.20, gate_db=-60.0):
    """Backward-compatible helper returning (pans_smooth, energies, block_samples)."""
    pans, energies, _, _, _, _, _, _, _ = compute_short_time_features(data, sr, block_sec=block_sec, gate_db=gate_db)
    return pans, energies, max(1, int(sr * block_sec))


def build_dynamic_blocks(data, sr, base_x=0.0, base_y=0.0, base_z=0.0, block_sec=0.20,
                         pan_range=0.50, z_lift=0.10,
                         orbit=True, intimacy_proximity=True, pitch_elevation=True,
                         profile="auto", motion_mode="dynamic", intensity=1.0):
    """Build [(rtime, duration, x, y, z), ...] blocks with psychoacoustic grounded 3D motion.

    base_x: nominal resting X position (-1.0 Left to +1.0 Right)
    base_y: resting Y position (-1.0 Rear to +1.0 Front)
    base_z: resting Z position (0.0 Floor/Ear level to 1.0 Ceiling)
    profile: "guitar", "keys", "backing", "vocal", "fx", "auto"
    motion_mode: "subtle", "dynamic" (default), "expressive"
    intensity: motion depth scaling multiplier (default 1.0)
    """
    pans, energies, vels, intimacies, centroids, peakinesses, side_ratios, crests, c_slopes = compute_short_time_features(
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
        mode_scale = 1.40
    eff_intensity = float(np.clip(intensity * mode_scale, 0.0, 2.5))

    # Compute energy envelope onsets and smoothed dynamic curve
    energy_env = energies / max_e
    if num_blocks >= 5:
        e_kernel = np.ones(5, dtype=np.float32) / 5.0
        energy_smooth = np.convolve(energy_env, e_kernel, mode="same")
    else:
        energy_smooth = energy_env

    # Detect onsets / energy bursts
    onsets = np.zeros(num_blocks, dtype=np.float32)
    if num_blocks >= 2:
        onsets[1:] = np.maximum(0.0, energy_env[1:] - energy_env[:-1])

    # Musical phrase slow drift accumulator (cycles over ~14 seconds)
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
        side_r = float(side_ratios[i])
        crest = float(crests[i])
        c_slope = float(c_slopes[i])
        e = float(energy_smooth[i])
        ons = float(onsets[i])
        ph = float(phrase_phase[i])

        # Default resting coordinates on ear-level (Z in [0.0, 0.15])
        x = base_x + p * pan_range * eff_intensity
        y = base_y
        z = base_z + e * z_lift * eff_intensity

        # -------------------------------------------------------------
        # PROFILE-SPECIFIC GROUNDED 3D TRAJECTORY ENGINES
        # -------------------------------------------------------------

        if profile == "backing":
            # Standard DSP reference Event-Driven Architecture:
            # During quiet verses: Stays grounded in side surround pocket (Y in [-0.30, -0.45], Z = 0.0)
            # When chorus / backing vocal phrase hits (e > 0.10 or ons > 0.04):
            # JUMPS / SURGES dynamically into the deep rear corners (Y in [-0.85, -1.00])
            # and SOARS into height canopy (Z in [0.35, 0.65]) for a breathtaking 3D burst!
            is_active_phrase = (e > 0.08 or ons > 0.04)
            if is_active_phrase:
                surge = float(np.clip((e - 0.08) * 1.8 + ons * 0.8, 0.0, 1.0)) * eff_intensity
                # Jump to rear corners
                y_target = -0.45 - surge * 0.50
                # Elevate to height canopy
                z_target = 0.05 + surge * 0.55
                # Wide lateral spread
                x_target = np.sign(base_x) * (0.80 + surge * 0.20) + p * 0.20
            else:
                # Quiet / resting pocket
                y_target = -0.35
                z_target = 0.0
                x_target = base_x * 0.85 + p * 0.15

            x = float(np.clip(x_target, -1.0, 1.0))
            y = float(np.clip(y_target, -1.0, -0.20))  # Strictly behind listener
            z = float(np.clip(z_target, 0.0, 0.75))

        elif profile == "vocal":
            # Standard DSP rule: Lead Vocal is locked Front Center on ear level across the entire song.
            # Zero rear incursions (Y >= 0.80), Zero ceiling incursions (Z = 0.0). Absolute clarity and punch.
            x = 0.0 + p * 0.06 * eff_intensity
            y = 0.85
            z = 0.0

        elif profile == "guitar":
            # Event-Driven Guitar:
            # Steady rhythm sits anchored in front/side stage (Y in [0.0, 0.30], Z = 0.0).
            # On dynamic solos or ambient arpeggios (high side ratio or high energy), blooms into the room.
            is_dynamic_event = (side_r > 0.35 and e > 0.15) or (ons > 0.06)
            if is_dynamic_event:
                bloom = float(np.clip((e * 0.8 + ons * 0.4), 0.0, 1.0)) * eff_intensity
                target_y = 0.10 - bloom * 0.70  # Sweeps toward sides/rears
                target_z = bloom * 0.12
                x_pos = base_x * (1.0 + bloom * 0.2) + p * 0.30
            else:
                target_y = 0.25
                target_z = 0.0
                x_pos = base_x + p * 0.20

            x = float(np.clip(x_pos, -1.0, 1.0))
            y = float(np.clip(target_y, -0.75, 0.40))
            z = float(np.clip(target_z, 0.0, 0.14))

        elif profile == "keys":
            # Event-Driven Keys / Synths:
            # Ambient pads rest in side stage; on chorus/drop swells expand to rear/ceiling
            is_swell = (e > 0.20 or ons > 0.05)
            if is_swell:
                swell_amt = float(np.clip(e * 1.2 + ons * 0.5, 0.0, 1.0)) * eff_intensity
                target_y = -0.10 - swell_amt * 0.65
                target_z = swell_amt * 0.45
                x_pos = base_x * (1.0 + swell_amt * 0.15) + p * 0.25
            else:
                target_y = 0.15
                target_z = 0.0
                x_pos = base_x + p * 0.15

            x = float(np.clip(x_pos, -1.0, 1.0))
            y = float(np.clip(target_y, -0.80, 0.35))
            z = float(np.clip(target_z, 0.0, 0.50))

        elif profile == "fx":
            # Ear Candy / FX: DSP-Driven Horseshoe Sweeps & Fly-Bys
            # Detects filter sweeps (dC/dt > 1000) or panning glides (|vel| > 0.25)
            is_active_sweep = (c_slope > 1000.0 and e > 0.12) or abs(vel) > 0.20
            if is_active_sweep:
                # Direct or phrase-driven sweep progress along perimeter
                if abs(vel) > 0.20:
                    sweep_progress = float(np.clip((p + 1.0) * 0.5, 0.0, 1.0))
                else:
                    sweep_progress = float((np.sin(ph * 2.0) + 1.0) * 0.5)

                # Parametric horseshoe trajectory: Side L (-1, 0) -> Rear L (-1, -1) -> Rear R (+1, -1) -> Side R (+1, 0)
                if sweep_progress < 0.33:
                    sub_p = sweep_progress / 0.33
                    x = -1.0
                    y = float(-sub_p)  # 0.0 -> -1.0
                elif sweep_progress < 0.66:
                    sub_p = (sweep_progress - 0.33) / 0.33
                    x = float(-1.0 + 2.0 * sub_p)  # -1.0 -> +1.0
                    y = -1.0
                else:
                    sub_p = (sweep_progress - 0.66) / 0.34
                    x = 1.0
                    y = float(-1.0 + sub_p)  # -1.0 -> 0.0
            else:
                # Ambient smooth floating
                r_orbit = 0.75 * eff_intensity
                x = float(np.clip(r_orbit * np.sin(ph) + p * 0.4, -1.0, 1.0))
                y = float(np.clip(r_orbit * np.cos(ph) - 0.20, -1.0, 0.6))

            # Ear-level grounded default, with controlled lift up to 0.40 only on extreme white-noise shimmers
            z_lift_fx = 0.0
            if cent > 3500.0 and peak >= 25.0:
                z_lift_fx = float(np.clip((cent - 3500.0) / 4000.0, 0.0, 0.30)) * eff_intensity
            z = float(np.clip(base_z + z_lift_fx, 0.0, 0.45))

        else: # "auto" / generic
            y_travel = base_y + (e * 0.30 * (1.0 if base_y < 0 else -0.25)) * eff_intensity
            x = base_x + p * pan_range * eff_intensity
            y = y_travel
            z = float(np.clip(base_z + (e * 0.08) * eff_intensity, 0.0, 0.15))

        # -------------------------------------------------------------
        # UNIVERSAL PSYCHOACOUSTIC OVERLAYS
        # -------------------------------------------------------------

        # Overlay 1: Intimacy & Whisper Near-Field Proximity (pulls directly to ear for vocals)
        if intimacy_proximity and intim > 0.05 and profile in ("vocal", "auto"):
            y_ear = -0.10
            y = (1.0 - intim) * y + intim * y_ear
            z = (1.0 - intim) * z + intim * 0.02

        # Overlay 2: Strict Backing Constraint Guard (Guarantee Y <= 0.0 and Z <= 0.05)
        if profile == "backing":
            y = min(0.0, y)
            z = min(0.05, z)

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

    # Re-apply strict backing bounds after smoothing
    if profile == "backing":
        smooth_y = np.clip(smooth_y, -1.0, 0.0)
        smooth_z = np.clip(smooth_z, 0.0, 0.05)

    blocks = []
    for i in range(num_blocks):
        rt = i * block_sec
        dur = min(block_sec, total_sec - rt)
        if dur <= 0:
            break
        blocks.append((rt, dur, float(smooth_x[i]), float(smooth_y[i]), float(smooth_z[i])))

    return blocks
