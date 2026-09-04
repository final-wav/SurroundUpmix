"""Generate a multi-phase 3D Dolby Atmos demo showcasing:
1. Intimate Whisper Proximity (0s - 4s): Unvoiced breath pulls the 3D object right next to the ear.
2. 360° Circular Orbit (4s - 9s): Fast stereo ping-pong sweeps curve into a full 360° circle around the head.
3. Pitch-to-Elevation (9s - 14s): High shimmering chimes (6 kHz) elevate towards the ceiling.
"""
import os
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt
from surroundupmix.engine import upmix_folder

def main():
    sr = 48000
    dur = 14.0
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)

    base_dir = r"J:\TOOLS\Surround Upmix\Demo_Moving_Objects"
    stems_dir = os.path.join(base_dir, "stems")
    out_dir = os.path.join(base_dir, "Output")
    os.makedirs(stems_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    # 1. Drums (gentle beat)
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 1.0) * 12)) * 0.4
    hihat = (np.random.rand(n).astype(np.float32) * 2 - 1) * np.exp(-((t % 0.5) * 40)) * 0.08
    drums = np.stack([kick + hihat, kick + hihat], axis=1)

    # 2. Bass (warm 80 Hz)
    bass_sig = np.sin(2 * np.pi * 80 * t) * 0.35
    bass = np.stack([bass_sig, bass_sig], axis=1)

    # 3. Vocals (lead melody, centered)
    lead_sig = np.sin(2 * np.pi * 330 * t) * 0.25 * (0.8 + 0.2 * np.sin(2 * np.pi * 4 * t))
    vocals = np.stack([lead_sig, lead_sig], axis=1)

    # 4. Other (ambient pad)
    pad_l = np.sin(2 * np.pi * 220 * t) * 0.15
    pad_r = np.sin(2 * np.pi * 222 * t) * 0.15
    other = np.stack([pad_l, pad_r], axis=1)

    # 5. Backing Vocals / 3D Objects:
    # Phase 1 (0s - 4s): Intimate Whisper (breathy ASMR proximity next to ear)
    # Phase 2 (4s - 9s): 360° Orbit (ping-pong sweep circling around the head)
    # Phase 3 (9s - 14s): Pitch-to-Elevation (6.5 kHz chime floating to ceiling)
    backing = np.zeros((n, 2), dtype=np.float32)

    s4 = int(4 * sr)
    s9 = int(9 * sr)

    # --- Phase 1: Whisper (0s - 4s) ---
    noise = np.random.randn(s4).astype(np.float32) * 0.45
    sos = butter(4, [2800, 8500], btype="bandpass", fs=sr, output="sos")
    whisper = sosfilt(sos, noise).astype(np.float32)
    # Modulate with breathing envelope
    env_w = (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t[:s4])) * 0.65
    whisper *= env_w
    backing[:s4, 0] = whisper * 0.2
    backing[:s4, 1] = whisper * 0.9  # close to right ear

    # --- Phase 2: 360° Circular Orbit (4s - 9s) ---
    dur_sweep = 5.0
    n_sweep = s9 - s4
    t_sw = t[s4:s9] - 4.0
    # 0.4 Hz ping-pong sweep
    p_sw = np.sin(2 * np.pi * 0.4 * t_sw).astype(np.float32)
    synth_core = (np.sin(2 * np.pi * 440 * t_sw) + 0.5 * np.sin(2 * np.pi * 554 * t_sw)) * 0.35
    gl = np.sqrt(np.clip((1.0 - p_sw) / 2.0, 0.0, 1.0))
    gr = np.sqrt(np.clip((1.0 + p_sw) / 2.0, 0.0, 1.0))
    backing[s4:s9, 0] = synth_core * gl
    backing[s4:s9, 1] = synth_core * gr

    # --- Phase 3: Pitch-to-Elevation (9s - 14s) ---
    t_ch = t[s9:] - 9.0
    chime = (np.sin(2 * np.pi * 6000 * t_ch) + 0.6 * np.sin(2 * np.pi * 7500 * t_ch)) * 0.35
    crescendo = np.linspace(0.6, 1.0, len(t_ch))
    chime *= crescendo
    backing[s9:, 0] = chime * 0.7
    backing[s9:, 1] = chime * 0.7

    # Write stems
    sf.write(os.path.join(stems_dir, "drums.flac"), drums, sr)
    sf.write(os.path.join(stems_dir, "bass.flac"), bass, sr)
    sf.write(os.path.join(stems_dir, "vocals.flac"), vocals, sr)
    sf.write(os.path.join(stems_dir, "other.flac"), other, sr)
    sf.write(os.path.join(stems_dir, "backing.flac"), backing, sr)

    print("Stems generated in:", stems_dir)

    # 1. Render Dolby Atmos ADM BWF (Playback Order)
    out_playback = upmix_folder(
        stems_dir,
        preset="immersive",
        out_dir=out_dir,
        track_label="Dolby_Atmos_Smart_3D_Demo_Playback",
        adm=True,
        adm_order="playback",
        adm_objects=True,
    )

    # 2. Render Dolby Atmos ADM BWF (ADM Renderer Order)
    out_renderer = upmix_folder(
        stems_dir,
        preset="immersive",
        out_dir=out_dir,
        track_label="Dolby_Atmos_Smart_3D_Demo_Renderer",
        adm=True,
        adm_order="renderer",
        adm_objects=True,
    )

    # 3. Render Modern 20-Channel All-Objects Master (Zero Bed Layer, 14 Anchors + 6 Dynamic Objects)
    out_all_objects = upmix_folder(
        stems_dir,
        preset="immersive",
        out_dir=out_dir,
        track_label="Dolby_Atmos_20ch_All_Objects_Master",
        adm=True,
        adm_all_objects=True,
    )

    print("\n" + "=" * 60)
    print("SUCCESS: Generated Smart 3D Dolby Atmos Demo Masters:")
    print("  1. Playback Master:     ", out_playback)
    print("  2. ADM Renderer Master: ", out_renderer)
    print("  3. 20ch All-Objects Master:", out_all_objects)
    print(f"  Duration: {dur:.2f} seconds | Sample rate: {sr} Hz")
    print("=" * 60)

    # Inspect coordinates from generated blocks
    from surroundupmix.motion import build_dynamic_blocks
    blocks_r = build_dynamic_blocks(backing, sr, base_x=0.85, base_y=-0.85, base_z=0.35)
    print("\n=== Backing Right Trajektorie (Smart 3D Koordinaten) ===")
    for sec, label in [(1.4, "1. Flüstern (Intim am Ohr): "),
                       (6.4, "2. 360° Orbit (Front-Wand): "),
                       (7.4, "2. 360° Orbit (Rück-Wand):  "),
                       (12.0, "3. Elevation (Decken-Dome): ")]:
        idx = min(len(blocks_r) - 1, int(sec / 0.20))
        b = blocks_r[idx]
        print(f"  {label} @ {b[0]:.1f}s -> X={b[2]:+.3f}, Y={b[3]:+.3f}, Z={b[4]:+.3f}")

if __name__ == "__main__":
    main()
