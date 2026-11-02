"""
NUGEN Halo Upmix VST3 Integration for Dolby Atmos Production.
Uses host VST3 plugin via pedalboard to generate pristine 7.1 surround
soundfields for acoustic/electric guitars, pianos, synth textures, and backing vocals.
"""

import os
from pathlib import Path
import numpy as np

NUGEN_VST3_PATHS = [
    Path(r"C:\Program Files\Common Files\VST3\NUGEN Audio\NUGEN Halo Upmix 64bit.vst3"),
    Path(r"C:\Program Files\Common Files\VST3\NUGEN Halo Upmix 64bit.vst3"),
]

_PLUGIN_INSTANCE = None
_PEDALBOARD_AVAILABLE = None


def is_nugen_available() -> bool:
    """Return True if NUGEN Halo Upmix VST3 exists on system and pedalboard is installed."""
    global _PEDALBOARD_AVAILABLE
    if _PEDALBOARD_AVAILABLE is None:
        try:
            import pedalboard
            _PEDALBOARD_AVAILABLE = True
        except ImportError:
            _PEDALBOARD_AVAILABLE = False

    if not _PEDALBOARD_AVAILABLE:
        return False

    return any(p.exists() for p in NUGEN_VST3_PATHS)


def get_nugen_path() -> Path | None:
    for p in NUGEN_VST3_PATHS:
        if p.exists():
            return p
    return None


def get_plugin_instance():
    """Get or initialize singleton NUGEN Halo Upmix plugin instance."""
    global _PLUGIN_INSTANCE
    if _PLUGIN_INSTANCE is not None:
        return _PLUGIN_INSTANCE

    if not is_nugen_available():
        return None

    import pedalboard
    vst_path = get_nugen_path()
    try:
        plugin = pedalboard.load_plugin(str(vst_path))
        # Default safety config: Mute LFE to prevent low-end mud in surround bed
        if hasattr(plugin, "mute_lfe"):
            plugin.mute_lfe = 1.0
        if hasattr(plugin, "exclude_lfe"):
            plugin.exclude_lfe = 1.0
        _PLUGIN_INSTANCE = plugin
        return _PLUGIN_INSTANCE
    except Exception as e:
        print(f"[NUGEN] Warning: Failed to load NUGEN Halo Upmix VST3: {e}")
        return None


def process_stem(audio_stereo: np.ndarray, sr: int = 48000,
                 diffusion: float = 0.35, space: float = 0.70) -> dict | None:
    """
    Process a 2-channel stereo audio array through NUGEN Halo Upmix.
    Input:
        audio_stereo: np.ndarray of shape (N, 2) or (2, N), float32
        sr: sample rate (must be 48000 for Atmos)
        diffusion: NUGEN diffusion control (0.0 to 1.0)
        space: NUGEN space control (0.0 to 1.0)
    Returns:
        dict with channels:
            FL, FR, FC, LFE, SL, SR, BL, BR (each mono 1D float32 array)
        or None if NUGEN unavailable.
    """
    plugin = get_plugin_instance()
    if plugin is None:
        return None

    # Ensure shape is (2, N)
    if audio_stereo.ndim != 2:
        return None
    if audio_stereo.shape[0] != 2 and audio_stereo.shape[1] == 2:
        arr_2ch = audio_stereo.T.astype(np.float32)
    else:
        arr_2ch = audio_stereo.astype(np.float32)

    n_samples = arr_2ch.shape[1]
    if n_samples == 0:
        return None

    # Configure parameters
    try:
        if hasattr(plugin, "diffusion"):
            plugin.diffusion = max(0.0, min(1.0, float(diffusion)))
        if hasattr(plugin, "space"):
            plugin.space = max(0.0, min(1.0, float(space)))
        if hasattr(plugin, "mute_lfe"):
            plugin.mute_lfe = 1.0
    except Exception:
        pass

    # Build 8-channel input buffer (Stereo on Ch 0 & Ch 1, zeros on Ch 2..7)
    in_8ch = np.zeros((8, n_samples), dtype=np.float32)
    in_8ch[0] = arr_2ch[0]
    in_8ch[1] = arr_2ch[1]

    try:
        out_8ch = plugin(in_8ch, sr)
        if out_8ch.shape[0] < 8:
            return None

        return {
            "FL": np.ascontiguousarray(out_8ch[0]),
            "FR": np.ascontiguousarray(out_8ch[1]),
            "FC": np.ascontiguousarray(out_8ch[2]),
            "LFE": np.ascontiguousarray(out_8ch[3]),
            "SL": np.ascontiguousarray(out_8ch[4]),
            "SR": np.ascontiguousarray(out_8ch[5]),
            "BL": np.ascontiguousarray(out_8ch[6]),
            "BR": np.ascontiguousarray(out_8ch[7]),
        }
    except Exception as e:
        print(f"[NUGEN] Processing error: {e}")
        return None
