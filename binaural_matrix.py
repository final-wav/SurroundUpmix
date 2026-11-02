"""
binaural_matrix.py - Dolby Atmos Binaural Render Mode Matrix Manager
Controls the 3D Headphone HRTF processing (Off, Near, Mid, Far) per Object.
"""

import os
import struct
from pathlib import Path
from typing import Dict, List, Optional, Union

BASE_DIR = Path(__file__).resolve().parent
SIG_FILE = BASE_DIR / "surroundupmix" / "dolby_signature_30ch.bin"

# The 20 discrete 3D spatial objects in the 30-channel master
OBJECT_KEYS = [
    # 14 Fixed 7.1.6 Anchors
    ("fl", "Front Left"),
    ("fr", "Front Right"),
    ("fc", "Center"),
    ("lfe", "LFE / Subwoofer"),
    ("sl", "Side Surround Left"),
    ("sr", "Side Surround Right"),
    ("bl", "Rear Surround Left"),
    ("br", "Rear Surround Right"),
    ("tfl", "Top Front Left"),
    ("tfr", "Top Front Right"),
    ("tml", "Top Middle Left"),
    ("tmr", "Top Middle Right"),
    ("trl", "Top Rear Left"),
    ("trr", "Top Rear Right"),
    # 6 Dynamic Moving Objects
    ("backing_l", "Backing Left"),
    ("backing_r", "Backing Right"),
    ("lead_vocal", "Lead Vocal"),
    ("guitar", "Guitar / Solo"),
    ("piano", "Piano / Synth"),
    ("fx", "Ear Candy / FX"),
]

# Mode to byte mapping:
# 0x80 (128) = Off / Bypass (Direct feed, zero HRTF filtering)
# 0x81 (129) = Near (Intimate proximity, vocal clarity)
# 0x82 (130) = Mid (Natural stereo/surround speaker distance)
# 0x84 (132) = Far (Diffuse 3D delay & ceiling height canopy)
MODE_MAP = {
    "off": 128,
    "near": 129,
    "mid": 130,
    "far": 132
}

BYTE_TO_MODE = {v: k for k, v in MODE_MAP.items()}

# Predefined standard matrices
PRESET_MATRICES = {
    "punchy_direct": {
        "fl": "off", "fr": "off", "fc": "off", "lfe": "off",
        "sl": "near", "sr": "near", "bl": "far", "br": "far",
        "tfl": "far", "tfr": "far", "tml": "far", "tmr": "far",
        "trl": "far", "trr": "far",
        "backing_l": "far", "backing_r": "far", "lead_vocal": "off",
        "guitar": "mid", "piano": "mid", "fx": "mid"
    },
    "studio_master": {
        "fl": "mid", "fr": "mid", "fc": "near", "lfe": "off",
        "sl": "mid", "sr": "mid", "bl": "far", "br": "far",
        "tfl": "far", "tfr": "far", "tml": "far", "tmr": "far",
        "trl": "far", "trr": "far",
        "backing_l": "far", "backing_r": "far", "lead_vocal": "near",
        "guitar": "mid", "piano": "mid", "fx": "far"
    },
    "all_mid": {k: ("off" if k == "lfe" else "mid") for k, _ in OBJECT_KEYS},
    "deep_immersion": {
        k: ("off" if k == "lfe" else ("near" if k in ("fc", "lead_vocal") else "far"))
        for k, _ in OBJECT_KEYS
    },
    "bypass": {k: "off" for k, _ in OBJECT_KEYS}
}


def get_default_matrix(preset: str = "punchy_direct") -> Dict[str, str]:
    base = PRESET_MATRICES.get(preset, PRESET_MATRICES["punchy_direct"])
    return dict(base)


def build_custom_dbmd(matrix: Optional[Union[Dict[str, str], str]] = None) -> bytes:
    """
    Build a certified 564-byte Dolby Metadata (dbmd) chunk with custom
    Binaural Render Modes (Off, Near, Mid, Far) per spatial object.
    """
    if isinstance(matrix, str) and matrix in PRESET_MATRICES:
        mat_dict = PRESET_MATRICES[matrix]
    elif isinstance(matrix, dict):
        mat_dict = get_default_matrix("punchy_direct")
        mat_dict.update({k.lower(): v.lower() for k, v in matrix.items()})
    else:
        mat_dict = get_default_matrix("punchy_direct")

    # 10 Bed channels (channels 1-10): Carrier beds
    bed_modes = [132, 132, 132, 128, 132, 132, 132, 132, 132, 132]

    # 20 Object channels (channels 11-30)
    obj_modes = []
    for key, _ in OBJECT_KEYS:
        mode_str = mat_dict.get(key, "mid").lower()
        byte_val = MODE_MAP.get(mode_str, 130)
        obj_modes.append(byte_val)

    total_30_modes = bed_modes + obj_modes
    assert len(total_30_modes) == 30, f"Expected 30 channel modes, got {len(total_30_modes)}"

    if not SIG_FILE.is_file():
        raise FileNotFoundError(f"Reference Dolby signature not found at {SIG_FILE}")

    d = bytearray(SIG_FILE.read_bytes())

    # Segment 10 contains object metadata and binaural modes
    idx = 4
    found = False
    while idx < len(d) - 2:
        sid = d[idx]
        slen = struct.unpack("<H", d[idx+1:idx+3])[0]
        if sid == 10:
            payload = d[idx+3:idx+3+slen]
            payload[-30:] = bytes(total_30_modes)
            d[idx+3:idx+3+slen] = payload
            # Two's complement checksum
            new_chk = (-(len(payload) + sum(payload))) & 0xFF
            d[idx+3+slen] = new_chk
            found = True
            break
        idx += 3 + slen + 1

    if not found:
        raise ValueError("Segment 10 not found in Dolby signature template.")

    return bytes(d)
