"""Spatialisation: turn decomposed stems into per-channel signals.

The rule is the same for every stem and comes straight from the direct/ambient
split, not from the stem's name:

  * DIRECT  -> front image (FL/FR, plus FC for the lead vocal), keeping its
              left<->right position. A panned source may extend a little into
              its own side (lateral_arc) - reconstructing real L->R movement,
              never inventing it.
  * AMBIENT -> the surround field (sides / backs / heights), same-side, so the
              decorrelated room wraps around the listener.

Per-stem *modulation* (not placement) then applies the few things that really
are category-specific: bass never wraps (it stays front + LFE), drums only
wash the sides, and the heights are fed from the decorrelated ambient of the
texture stems (other/guitar/piano) - which is what real height channels want.
"""
import numpy as np

from .decompose import decompose, lateral_pan
from .dsp import db_to_lin, highpass
from .layouts import LAYOUTS, has_heights, side_pair, surround_pair

TEXTURE = ("other", "guitar", "piano")


class Channels:
    """Lazy per-channel accumulator over a fixed layout."""

    def __init__(self, fmt, n):
        self.fmt = fmt
        self.n = n
        self.data = {ch: np.zeros(n, dtype=np.float32) for ch in LAYOUTS[fmt]}

    def add(self, ch, arr, gain_db=0.0):
        if ch not in self.data or arr is None:
            return
        g = db_to_lin(gain_db)
        m = min(self.n, len(arr))
        if g == 1.0:
            self.data[ch][:m] += arr[:m]
        else:
            self.data[ch][:m] += arr[:m] * g


def _route_direct(chans, name, direct, p):
    """DIRECT -> front image."""
    if name == "vocals":
        c = float(p["vocal_center"])
        g = p["vocal_front"]
        chans.add("FC", 0.5 * (direct.L + direct.R), g + _c2db(c))
        chans.add("FL", direct.L, g + _c2db(1 - c))
        chans.add("FR", direct.R, g + _c2db(1 - c))
    else:
        chans.add("FL", direct.L, 0.0)
        chans.add("FR", direct.R, 0.0)


def _c2db(frac):
    frac = max(1e-3, min(1.0, frac))
    return 20.0 * np.log10(frac)


def _route_ambient(chans, name, ambient, direct, p, sr, keep_vocal_forward):
    """AMBIENT -> surround field, by category modulation."""
    fmt = chans.fmt
    sl, sr_ch = side_pair(fmt)
    bl, br = surround_pair(fmt)
    heights = has_heights(fmt)

    if name == "bass":
        return  # bass stays front + LFE, never wraps

    if name == "drums":
        chans.add(sl, ambient.L, p["drum_side"])
        chans.add(sr_ch, ambient.R, p["drum_side"])
        return

    if name == "vocals":
        if keep_vocal_forward:
            return  # doubled vocal: no spread (would comb-filter)
        chans.add(sl, ambient.L, p["voc_side"])
        chans.add(sr_ch, ambient.R, p["voc_side"])
        return

    if name in TEXTURE:
        # full wrap: sides + backs + heights (heights from decorrelated air)
        chans.add(sl, ambient.L, p["amb_side"])
        chans.add(sr_ch, ambient.R, p["amb_side"])
        if bl != sl:  # discrete backs exist (7.1/7.1.2)
            chans.add(bl, ambient.L, p["amb_back"])
            chans.add(br, ambient.R, p["amb_back"])
        else:         # 5.1: fold the back send onto the single surround pair
            chans.add(bl, ambient.L, p["amb_back"])
            chans.add(br, ambient.R, p["amb_back"])
        if heights:
            chans.add("TFL", highpass(ambient.L, p["height_hp"], sr), p["amb_height"])
            chans.add("TFR", highpass(ambient.R, p["height_hp"], sr), p["amb_height"])
        _lateral_arc(chans, direct, p)
        return

    # any other stem name: treat as texture-lite (sides only)
    chans.add(sl, ambient.L, p["amb_side"])
    chans.add(sr_ch, ambient.R, p["amb_side"])


def _lateral_arc(chans, direct, p):
    """Extend a clearly panned direct source a little into its own side,
    so a real left->right position/movement opens into the room. Broadband
    and subtle - it honours movement that is in the signal, never invents it.
    """
    arc = float(p.get("lateral_arc", 0.0))
    if arc <= 0:
        return
    pan = lateral_pan(direct)               # -1 left .. +1 right
    amt = arc * max(0.0, abs(pan) - 0.2)    # only when clearly off-centre
    if amt <= 0:
        return
    sl, sr_ch = side_pair(chans.fmt)
    bl, br = surround_pair(chans.fmt)
    g = 20.0 * np.log10(max(1e-3, amt))
    if pan > 0:
        chans.add(sr_ch, direct.R, g)
        if br != sr_ch:
            chans.add(br, direct.R, g - 4)
    else:
        chans.add(sl, direct.L, g)
        if bl != sl:
            chans.add(bl, direct.L, g - 4)


def _route_backing(chans, stems, p, backing_gain_db):
    """Backing vocals (from the karaoke split) are their OWN content, so they
    wrap the rears full-range with zero phase risk. A quiet front anchor keeps
    word transitions from jumping, and the full clean vocal is laid underneath
    as a bed to mask the split's artifacts (the 'blend' idea, kept)."""
    backing = stems.get("backing")
    if backing is None:
        return
    bg = backing_gain_db
    bl, br = surround_pair(chans.fmt)
    heights = has_heights(chans.fmt)
    # front anchor (divergence) + dry localisable choir behind
    chans.add("FL", backing.L, bg - 9)
    chans.add("FR", backing.R, bg - 9)
    chans.add(bl, backing.L, bg)
    chans.add(br, backing.R, bg)
    # blend bed: the full clean vocal, quiet, under the backing
    bed = stems.get("vocals_full")
    if bed is not None:
        chans.add(bl, bed.L, bg - 10)
        chans.add(br, bed.R, bg - 10)
        if heights:
            chans.add("TFL", highpass(bed.L, 3000, backing.sr), bg - 13)
            chans.add("TFR", highpass(bed.R, 3000, backing.sr), bg - 13)


def spatialize(stems, fmt, preset, sr, vocal_class="reverb", backing_gain_db=-6.0):
    """Build the spatial channels (everything except the final LFE + balance).
    Returns a Channels object.
    """
    n = max(len(s) for s in stems.values())
    chans = Channels(fmt, n)
    keep_vocal_forward = (vocal_class == "double")

    for name, st in stems.items():
        if name in ("backing", "vocals_full"):
            continue
        direct, ambient = decompose(st)
        _route_direct(chans, name, direct, preset)
        _route_ambient(chans, name, ambient, direct, preset, sr, keep_vocal_forward)

    _route_backing(chans, stems, preset, backing_gain_db)
    return chans
