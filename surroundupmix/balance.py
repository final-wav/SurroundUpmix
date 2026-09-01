"""LFE derivation, front/rear auto-balance, and final normalisation."""
import numpy as np

from .dsp import db_to_lin, highpass, lowpass, mono, rms
from .layouts import FRONT_SET, REAR_SET, LAYOUTS


# The LFE channel is REPRODUCED +10 dB louder than the mains (ITU-R BR.1384; AVRs
# and the Dolby Atmos renderer both apply it), so it must be RECORDED 10 dB lower
# or it plays back far too hot. On top of that the bass is already full-range in
# the mains and bass management feeds the sub from those, so a loud LFE doubles
# the low end. So bake the -10 dB reproduction offset into the written LFE.
LFE_REPRO_OFFSET_DB = -10.0


def build_lfe(stems, cross, sr, gain_db=-3.0):
    """LFE from the real low end of BASS + kick (drums low band), not a
    low-pass of the whole mix - keeps it tight instead of muddy. The written
    level carries the -10 dB LFE reproduction offset (see above)."""
    n = max(len(s) for s in stems.values())
    acc = np.zeros(n, dtype=np.float32)
    if "bass" in stems:
        m = mono(stems["bass"])
        acc[:len(m)] += m
    if "drums" in stems:
        m = mono(stems["drums"])
        acc[:len(m)] += m * 0.7   # kick body, a touch under the bass
    if "bass" not in stems and "drums" not in stems:
        # fall back to the full mix low end
        for st in stems.values():
            m = mono(st)
            acc[:len(m)] += m / max(1, len(stems))
    low = lowpass(acc, cross, sr)
    low = highpass(low, 20.0, sr)          # drop infrasonic mess below 20 Hz
    return (low * db_to_lin(gain_db + LFE_REPRO_OFFSET_DB)).astype(np.float32)


def auto_balance(chans, rear_below_front, rear_gain=0.0):
    """Trim the automatic rear field to sit `rear_below_front` dB under the
    front, per song (front/rear material differs track to track). `rear_gain`
    is the user's taste offset on top.

    Only the AUTOMATIC layer (`chans.data`) is measured and trimmed - manually
    forced per-stem placements live in `chans.forced` and are deliberately left
    untouched, so a source you put in the rear is not fought or over-corrected
    by the global balance. Modifies channels in place; returns info dict.
    """
    if rear_below_front <= 0:
        return {"applied": False}
    fe = sum(rms(chans.data[c]) ** 2 for c in LAYOUTS[chans.fmt] if c in FRONT_SET)
    re = sum(rms(chans.data[c]) ** 2 for c in LAYOUTS[chans.fmt] if c in REAR_SET)
    if fe <= 1e-12 or re <= 1e-12:
        return {"applied": False}
    front_db = 10.0 * np.log10(fe)
    rear_db = 10.0 * np.log10(re)
    trim = (front_db - rear_below_front + rear_gain) - rear_db
    trim = float(max(-24.0, min(12.0, trim)))
    g = db_to_lin(trim)
    for c in LAYOUTS[chans.fmt]:
        if c in REAR_SET:
            chans.data[c] *= g
    return {"applied": True, "front_db": front_db, "rear_db": rear_db, "trim": trim}


def normalize(chans, peak_db=-0.1):
    """Peak-normalise all channels together to `peak_db` dBFS (LFE included,
    matching the old single 'gain -n' over the merged file). Peak is taken over
    the full signal (automatic + forced) and both layers are scaled equally."""
    peak = 0.0
    for c in LAYOUTS[chans.fmt]:
        arr = chans.total(c)
        if arr is not None and len(arr):
            peak = max(peak, float(np.max(np.abs(arr))))
    if peak <= 0:
        return 1.0
    g = db_to_lin(peak_db) / peak
    for c in LAYOUTS[chans.fmt]:
        chans.data[c] *= g
        chans.forced[c] *= g
    return g
