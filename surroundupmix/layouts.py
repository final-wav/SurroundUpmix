"""Speaker layouts and Microsoft/WAVE channel-mask definitions.

The channel order used throughout the engine is the canonical WAVE order
(ascending speaker-mask bit), so a 5.1 file is FL FR FC LFE BL BR and a
7.1 file is FL FR FC LFE BL BR SL SR - exactly what FLAC and every decoder
expect for those channel counts. 7.1.2 (10 ch) can't be FLAC, so it is
written as a WAV with an explicit WAVEFORMATEXTENSIBLE channel mask.
"""

# Microsoft KSAUDIO speaker position bits (dwChannelMask)
SPEAKER_BITS = {
    "FL":  0x1,      # front left
    "FR":  0x2,      # front right
    "FC":  0x4,      # front centre
    "LFE": 0x8,      # low-frequency effects
    "BL":  0x10,     # back left
    "BR":  0x20,     # back right
    "SL":  0x200,    # side left
    "SR":  0x400,    # side right
    "TFL": 0x1000,   # top front left
    "TFR": 0x4000,   # top front right
}

# Ordered channel list per output format (canonical WAVE order).
# 5.1 uses the BL/BR positions for its surround pair (mask 0x3F, the most
# widely recognised "5.1"); 7.1 adds the side pair; 7.1.2 adds two heights.
LAYOUTS = {
    "5.1":   ["FL", "FR", "FC", "LFE", "BL", "BR"],
    "7.1":   ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
    "7.1.2": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR"],
}

# Which speakers count as "front" vs "rear field" for the auto-balance.
FRONT_SET = ("FL", "FR", "FC")
REAR_SET = ("BL", "BR", "SL", "SR", "TFL", "TFR")


def channel_mask(fmt):
    """dwChannelMask for a format (OR of its speaker bits)."""
    m = 0
    for ch in LAYOUTS[fmt]:
        m |= SPEAKER_BITS[ch]
    return m


def has_heights(fmt):
    return any(ch in ("TFL", "TFR") for ch in LAYOUTS[fmt])


def has_backs(fmt):
    """True when the format has a discrete side pair in addition to backs,
    i.e. backs and sides are separate speakers (7.1 / 7.1.2)."""
    chs = LAYOUTS[fmt]
    return "SL" in chs and "BL" in chs


def surround_pair(fmt):
    """The primary rear pair to place discrete rear objects on:
    the back speakers when present, otherwise the 5.1 surround pair."""
    return ("BL", "BR")


def side_pair(fmt):
    """The pair that receives the gentle same-side wrap. On 7.1/7.1.2 these
    are the true sides; on 5.1 the single surround pair (BL/BR) does both."""
    chs = LAYOUTS[fmt]
    if "SL" in chs:
        return ("SL", "SR")
    return ("BL", "BR")
