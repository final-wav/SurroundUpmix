"""Dynamic 3D trajectory generation and psychoacoustic motion engine for Dolby Atmos objects.

Features:
1. Short-Time Pan Tracking: Reconstructs real L/R panning automation over time.
2. 360° Orbit & Swirl: Translates fast panning / ping-pong delays into seamless 360°
   circular orbits around the listener's head.
3. Intimacy & Whisper Proximity: Detects dry unvoiced whisper / breath cues and pulls
   the object from the rear directly next to the listener's ear (ASMR goosebumps effect).
4. Pitch-to-Elevation: High shimmering frequencies (chimes, sparkles, high synths)
   dynamically elevate towards the ceiling speakers.
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


def build_dynamic_blocks(data, sr, base_x, base_y, base_z, block_sec=0.20,
                         pan_range=0.45, z_lift=0.15,
                         orbit=True, intimacy_proximity=True, pitch_elevation=True):
    """Build [(rtime, duration, x, y, z), ...] blocks with psychoacoustic 3D motion.

    base_x: nominal resting X position (-0.85 Left, +0.85 Right, 0.0 Center)
    base_y: resting Y position (e.g. -0.85 for rear envelope)
    base_z: resting Z position (e.g. 0.35 elevated)
    pan_range: how far the object moves laterally in reaction to stereo pan
    z_lift: dynamic elevation increase during energetic phrases
    orbit: whether active panning / ping-pong delays curve into 360° circular orbits
    intimacy_proximity: whether whispers/intimate breath pull the object near the ear
    pitch_elevation: whether high shimmering frequencies float towards the ceiling
    """
    pans, energies, vels, intimacies, centroids, peakinesses = compute_short_time_features(
        data, sr, block_sec=block_sec)
    n = len(data)
    total_sec = n / float(sr)
    num_blocks = len(pans)
    blocks = []

    max_e = float(np.max(energies)) if len(energies) and np.max(energies) > 1e-6 else 1.0

    for i in range(num_blocks):
        rt = i * block_sec
        dur = min(block_sec, total_sec - rt)
        if dur <= 0:
            break

        p = float(pans[i])
        vel = float(vels[i])
        intim = float(intimacies[i])
        cent = float(centroids[i])
        peak = float(peakinesses[i])
        rel_e = float(energies[i] / max_e) if max_e > 0 else 0.0

        # 1. Resting Coordinates
        x_rest = float(np.clip(base_x + p * pan_range, -1.0, 1.0))
        y_rest = float(base_y)

        x = x_rest
        y = y_rest

        # Feature A: 360° Orbit Mode on Active Panning (Ping-Pong / Sweeps)
        if orbit:
            # When sound is actively moving across the stereo stage (|velocity| > 0.35 / sec)
            motion_speed = float(np.clip((abs(vel) - 0.35) * 1.6, 0.0, 1.0))
            if motion_speed > 0:
                # Full 360° circular orbit around listener: X^2 + Y^2 = R^2 (R ~ 0.85)
                # Left -> Right (vel > 0): Arcs across the Front wall (Y > 0)
                # Right -> Left (vel < 0): Arcs across the Rear wall (Y < 0)
                r_circ = 0.85
                x_orbit = float(np.clip(p * r_circ, -r_circ, r_circ))
                y_circ_mag = float(np.sqrt(max(0.01, r_circ ** 2 - x_orbit ** 2)))
                y_orbit = y_circ_mag if vel >= 0 else -y_circ_mag

                x = (1.0 - motion_speed) * x_rest + motion_speed * x_orbit
                y = (1.0 - motion_speed) * y_rest + motion_speed * y_orbit

        # Feature B: Intimacy & Whisper Near-Field Proximity
        if intimacy_proximity and intim > 0.05:
            # Whisper pulls the object right next to the listener's ear/shoulder (Y ~ -0.10)
            y_ear = -0.10
            y = (1.0 - intim) * y + intim * y_ear

        x = float(np.clip(x, -1.0, 1.0))
        y = float(np.clip(y, -1.0, 1.0))

        # 3. Elevation Z Coordinate
        z = base_z + rel_e * z_lift

        # Feature C: Pitch-to-Elevation (High shimmering tones float towards ceiling)
        if pitch_elevation and cent > 2500.0 and peak >= 25.0:
            high_factor = float(np.clip((cent - 2500.0) / 4500.0, 0.0, 1.0))
            z += high_factor * 0.30

        # Whispers settle to direct ear level (Z ~ 0.05)
        if intimacy_proximity and intim > 0.05:
            z = (1.0 - intim) * z + intim * 0.05

        z = float(np.clip(z, 0.0, 1.0))

        blocks.append((rt, dur, x, y, z))

    return blocks
