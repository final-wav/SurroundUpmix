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
from .dsp import db_to_lin, decorrelate, highpass, lowpass
from .io import Stereo
from .layouts import LAYOUTS, has_backs, has_heights, side_pair, surround_pair

TEXTURE = ("other", "guitar", "piano")


class Channels:
    """Lazy per-channel accumulator over a fixed layout.

    Two layers: `data` holds the automatically-placed material (which the
    front/rear auto-balance governs) and `forced` holds manual per-stem
    overrides. Keeping them apart lets the auto-balance measure and trim ONLY
    the automatic wrap, so a deliberately placed source is never fought or
    over-corrected by the global balance. The output is data + forced.
    """

    def __init__(self, fmt, n):
        self.fmt = fmt
        self.n = n
        self.data = {ch: np.zeros(n, dtype=np.float32) for ch in LAYOUTS[fmt]}
        self.forced = {ch: np.zeros(n, dtype=np.float32) for ch in LAYOUTS[fmt]}

    def add(self, ch, arr, gain_db=0.0, forced=False):
        if ch not in self.data or arr is None:
            return
        target = self.forced if forced else self.data
        g = db_to_lin(gain_db)
        m = min(self.n, len(arr))
        if g == 1.0:
            target[ch][:m] += arr[:m]
        else:
            target[ch][:m] += arr[:m] * g

    def total(self, ch):
        """The full signal for a channel: automatic + forced."""
        return self.data[ch] + self.forced[ch]


def _route_direct(chans, name, direct, p, forced=False, center=None):
    """DIRECT -> front image."""
    if name == "vocals":
        c = float(p["vocal_center"]) if center is None else float(center)
        g = p["vocal_front"]
        chans.add("FC", 0.5 * (direct.L + direct.R), g + _c2db(c), forced=forced)
        chans.add("FL", direct.L, g + _c2db(1 - c), forced=forced)
        chans.add("FR", direct.R, g + _c2db(1 - c), forced=forced)
    else:
        chans.add("FL", direct.L, 0.0, forced=forced)
        chans.add("FR", direct.R, 0.0, forced=forced)


def _c2db(frac):
    frac = max(1e-3, min(1.0, frac))
    return 20.0 * np.log10(frac)


def _diffuseness(direct, ambient):
    """0..1: share of this stem's energy that is decorrelated (ambient)."""
    ed = float(np.mean(direct.L ** 2 + direct.R ** 2))
    ea = float(np.mean(ambient.L ** 2 + ambient.R ** 2))
    return ea / (ed + ea + 1e-12)


def _auto_place(direct, ambient, p):
    """Song-adaptive wrap deltas (dB) for a texture stem, from *measurable*
    features - not instrument labels. A diffuse/reverberant part wraps more; a
    dry, centred part is held forward. A clearly panned part gets extra to its
    sides. Returns (side, back, height) dB deltas added on top of the preset.
    Demucs never labels a "trumpet", so we don't pretend to - we react to how
    the signal actually behaves per song. Disable with preset auto_place=False.
    """
    if not p.get("auto_place", True):
        return 0.0, 0.0, 0.0
    d = _diffuseness(direct, ambient)
    pan = abs(lateral_pan(direct))
    kd = p.get("ap_k", 6.0); d0 = p.get("ap_d0", 0.45)
    kp = p.get("ap_kp", 4.0); p0 = p.get("ap_p0", 0.25)
    lo = p.get("ap_min", -6.0); hi = p.get("ap_max", 5.0)
    base = max(lo, min(hi, kd * (d - d0)))     # diffuseness -> all zones wrap more/less
    wide = max(0.0, kp * (pan - p0))           # clear pan -> extra to the sides only
    return base + wide, base, base


def _route_ambient(chans, name, ambient, direct, p, sr, keep_vocal_forward,
                   wrap_db=0.0, no_wrap=False):
    """AMBIENT -> surround field, by category modulation. `wrap_db` is a
    per-instrument spread offset (dB) added to every wrap gain; `no_wrap` holds
    the stem fully forward (spread = 0)."""
    fmt = chans.fmt
    sl, sr_ch = side_pair(fmt)
    bl, br = surround_pair(fmt)
    heights = has_heights(fmt)

    if name == "bass" or no_wrap:
        return  # bass (or spread 0) stays front + LFE, never wraps

    if name == "drums":
        chans.add(sl, ambient.L, p["drum_side"] + wrap_db)
        chans.add(sr_ch, ambient.R, p["drum_side"] + wrap_db)
        return

    if name == "vocals":
        if keep_vocal_forward:
            return  # doubled vocal: no spread (would comb-filter)
        chans.add(sl, ambient.L, p["voc_side"] + wrap_db)
        chans.add(sr_ch, ambient.R, p["voc_side"] + wrap_db)
        return

    if name in TEXTURE:
        # full wrap: sides + backs + heights, song-adaptive per stem (auto-place)
        ds, dbk, dh = _auto_place(direct, ambient, p)
        # binaural front/back depth lean (0 unless a binaural cue was detected)
        b_side = p.get("binaural_side_db", 0.0)
        b_back = p.get("binaural_back_db", 0.0)
        chans.add(sl, ambient.L, p["amb_side"] + ds + b_side + wrap_db)
        chans.add(sr_ch, ambient.R, p["amb_side"] + ds + b_side + wrap_db)
        # backs get a DECORRELATED copy of the same ambient, so the rear field
        # envelops instead of collapsing onto the sides (SL and BL would be the
        # identical signal otherwise). Only where backs are their own speakers -
        # on 5.1 bl/br IS the side pair, so decorrelating there would just smear
        # the one surround channel against itself.
        do_dec = p.get("decorr", True)
        if do_dec and has_backs(fmt):
            aL_b, aR_b = decorrelate(ambient.L, sr, 0), decorrelate(ambient.R, sr, 1)
        else:
            aL_b, aR_b = ambient.L, ambient.R
        chans.add(bl, aL_b, p["amb_back"] + dbk + b_back + wrap_db)
        chans.add(br, aR_b, p["amb_back"] + dbk + b_back + wrap_db)
        if heights:
            hL = highpass(ambient.L, p["height_hp"], sr)
            hR = highpass(ambient.R, p["height_hp"], sr)
            if do_dec:                       # heights decorrelated from the sides too
                hL, hR = decorrelate(hL, sr, 2), decorrelate(hR, sr, 3)
            chans.add("TFL", hL, p["amb_height"] + dh + wrap_db)
            chans.add("TFR", hR, p["amb_height"] + dh + wrap_db)
        _lateral_arc(chans, direct, p)
        return

    # any other stem name: treat as texture-lite (sides only)
    chans.add(sl, ambient.L, p["amb_side"] + wrap_db)
    chans.add(sr_ch, ambient.R, p["amb_side"] + wrap_db)


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


def _route_residual(chans, stems, p, sr):
    """Reinject the separation residual (original - sum of stems): the detail
    Demucs failed to reproduce. Routed by the same direct/ambient rule - its
    coherent part rebuilds the FRONT image (L/C/R matrix, forced so the balance
    leaves this fidelity correction alone), its diffuse part gently wraps
    (decorrelated), so lost air/room returns around the listener too."""
    r = stems.get("residual")
    if r is None:
        return
    rg = float(p.get("recover_gain", 0.0))
    cross = float(p.get("recover_cross", 9000.0))   # HF-air restore owns the top
    direct, ambient = decompose(r)
    fmt = chans.fmt
    sl, sr_ch = side_pair(fmt)
    bl, br = surround_pair(fmt)
    # coherent detail -> front, as L/C/R so the centre isn't double-counted.
    # Band-limit the FRONT injection below the air crossover: above it the HF-air
    # restore already replaces the front's highs from the master, so we'd double
    # the top end otherwise. The diffuse wrap below stays full-band (air restore
    # only touches the front, so recovered HF air is free to surround the listener).
    mid = lowpass(0.5 * (direct.L + direct.R), cross, sr)
    side = lowpass(0.5 * (direct.L - direct.R), cross, sr)
    chans.add("FC", mid, rg, forced=True)
    chans.add("FL", side, rg, forced=True)
    chans.add("FR", -side, rg, forced=True)
    # diffuse detail (air/room) -> gentle decorrelated wrap
    chans.add(sl, ambient.L, rg - 3)
    chans.add(sr_ch, ambient.R, rg - 3)
    if has_backs(fmt):
        chans.add(bl, decorrelate(ambient.L, sr, 0), rg - 6)
        chans.add(br, decorrelate(ambient.R, sr, 1), rg - 6)
    if has_heights(fmt):
        chans.add("TFL", highpass(decorrelate(ambient.L, sr, 2), p["height_hp"], sr), rg - 8)
        chans.add("TFR", highpass(decorrelate(ambient.R, sr, 3), p["height_hp"], sr), rg - 8)


def _route_backing(chans, stems, p, backing_gain_db, ov=None):
    """Backing vocals (from the karaoke split) are their OWN content, so they
    wrap the rears full-range with zero phase risk. A quiet front anchor keeps
    word transitions from jumping, and the full clean vocal is laid underneath
    as a bed to mask the split's artifacts (the 'blend' idea, kept)."""
    backing = stems.get("backing")
    if backing is None:
        return
    ov = ov or {}
    if ov.get("mute"):
        return
    bg = backing_gain_db + ov.get("level", 0.0)
    bl, br = surround_pair(chans.fmt)
    heights = has_heights(chans.fmt)
    # Backing is a DELIBERATE placement (you want the choir behind you), so it
    # goes to the FORCED layer - the front/rear auto-balance trims only the
    # automatic wrap, so it won't bury the backing in the rears. Without this the
    # balance pulled the rears ~15 dB under the front and the backing vanished.
    chans.add("FL", backing.L, bg - 9, forced=True)   # small front anchor
    chans.add("FR", backing.R, bg - 9, forced=True)
    chans.add(bl, backing.L, bg, forced=True)          # the choir, behind you
    chans.add(br, backing.R, bg, forced=True)
    # blend bed: the full clean vocal, quiet, under the backing
    bed = stems.get("vocals_full")
    if bed is not None:
        chans.add(bl, bed.L, bg - 10, forced=True)
        chans.add(br, bed.R, bg - 10, forced=True)
        if heights:
            chans.add("TFL", highpass(bed.L, 3000, backing.sr), bg - 13, forced=True)
            chans.add("TFR", highpass(bed.R, 3000, backing.sr), bg - 13, forced=True)


def _route_forced(chans, name, direct, ambient, zone, p, sr):
    """Manual per-stem placement override (a taste decision no metric can make):
    put the whole stem (direct + its ambient) into a chosen zone.
      front -> hold at the front image      side -> the side pair
      rear  -> the back pair (+ height air)
    """
    fmt = chans.fmt
    sl, sr_ch = side_pair(fmt)
    bl, br = surround_pair(fmt)
    # everything here goes to the FORCED layer, so the auto-balance leaves it be
    if zone == "front":
        _route_direct(chans, name, direct, p, forced=True)
        chans.add("FL", ambient.L, -3.0, forced=True)
        chans.add("FR", ambient.R, -3.0, forced=True)
    elif zone == "side":
        chans.add(sl, direct.L, 0.0, forced=True); chans.add(sr_ch, direct.R, 0.0, forced=True)
        chans.add(sl, ambient.L, 0.0, forced=True); chans.add(sr_ch, ambient.R, 0.0, forced=True)
    elif zone == "rear":
        chans.add(bl, direct.L, 0.0, forced=True); chans.add(br, direct.R, 0.0, forced=True)
        chans.add(bl, ambient.L, 0.0, forced=True); chans.add(br, ambient.R, 0.0, forced=True)
        if has_heights(fmt):
            chans.add("TFL", highpass(ambient.L, p["height_hp"], sr), -3.0, forced=True)
            chans.add("TFR", highpass(ambient.R, p["height_hp"], sr), -3.0, forced=True)


def spatialize(stems, fmt, preset, sr, vocal_class="reverb", backing_gain_db=-6.0,
               place=None, overrides=None):
    """Build the spatial channels (everything except the final LFE + balance).
    `place` is {stem: 'auto'|'front'|'side'|'rear'} of manual placement.
    `overrides` is {stem: {zone,level,mute,...}} of per-instrument settings that
    sit on top of the preset. Returns a Channels object.
    """
    n = max(len(s) for s in stems.values())
    chans = Channels(fmt, n)
    keep_vocal_forward = (vocal_class == "double")
    place = place or {}
    overrides = overrides or {}

    for name, st in stems.items():
        if name in ("backing", "vocals_full", "residual"):
            continue
        ov = overrides.get(name, {})
        if ov.get("mute"):
            continue                                   # instrument dropped
        lv = ov.get("level", 0.0)
        if lv:
            st = Stereo(st.data * db_to_lin(lv), st.sr)  # per-instrument level trim
        direct, ambient = decompose(st)
        mode = str(ov.get("zone", place.get(name, "auto"))).lower()
        if mode in ("front", "side", "rear"):
            _route_forced(chans, name, direct, ambient, mode, preset, sr)
        else:
            # per-instrument spread (how far the ambient wraps) and vocal centre
            wrap_db, no_wrap = 0.0, False
            spread = ov.get("spread")
            if spread is not None:
                if spread <= 0:
                    no_wrap = True                     # hold fully forward
                else:
                    wrap_db = (float(spread) - 50.0) * 0.12   # +-6 dB around neutral
            center = ov.get("center")
            center = None if center is None else float(center) / 100.0
            _route_direct(chans, name, direct, preset, center=center)
            _route_ambient(chans, name, ambient, direct, preset, sr,
                           keep_vocal_forward, wrap_db=wrap_db, no_wrap=no_wrap)

    _route_backing(chans, stems, preset, backing_gain_db, overrides.get("backing", {}))
    _route_residual(chans, stems, preset, sr)
    return chans
