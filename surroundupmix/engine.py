"""End-to-end upmix from a folder of stems to a surround file."""
import os
import numpy as np

from . import presets as _presets
from .balance import auto_balance, build_lfe, normalize
from .detect import classify_vocal_width
from .dsp import rms
from .io import load_stems, write_surround
from .layouts import LAYOUTS
from .routing import spatialize


def _log(verbose, msg):
    if verbose:
        print(msg)


def upmix_folder(stems_folder, fmt="5.1", preset="immersive", out_dir=None,
                 track_label=None, rear_gain=0.0, rear_below_front=None,
                 vocal_mode="auto", backing_gain="auto", backing_below_lead=8.0,
                 lfe_cross=None, norm_level=-0.1, force_wav=False, place=None,
                 adm=False, adm_bits=24, verbose=True):
    """Upmix a Demucs stems folder. Returns the output path.

    adm=True writes a Dolby-Atmos ADM BWF master (a 7.1.2 bed, 48 kHz) that
    opens directly in the Dolby Atmos Renderer - no channel mapping. It forces
    the 7.1.2 layout and resamples to 48 kHz if needed (Atmos requirement).
    """
    if adm:
        fmt = "7.1.2"   # the Atmos bed is 7.1.2
    stems, sr = load_stems(stems_folder)
    p = _presets.get(preset)
    if rear_below_front is not None:
        p["rear_below_front"] = rear_below_front
    if lfe_cross is not None:
        p["lfe_cross"] = lfe_cross

    _log(verbose, "SurroundUpmix v2  |  %s  |  preset: %s" % (fmt, preset))
    _log(verbose, "  stems: %s  (%d Hz)" % (", ".join(stems), sr))

    # vocal width: keep a doubled vocal forward (no spread -> no comb filter)
    vocal_class = "reverb"
    if "vocals" in stems:
        if vocal_mode == "auto":
            vocal_class = classify_vocal_width(stems["vocals"])
            _log(verbose, "  vocal width: %s%s" % (
                vocal_class, "  -> kept forward" if vocal_class == "double" else ""))
        elif vocal_mode == "forward":
            vocal_class = "double"
        else:
            vocal_class = "reverb"

    # adaptive backing level: sit a set amount under the lead, per song
    backing_gain_db = -6.0
    if "backing" in stems:
        if backing_gain != "auto":
            backing_gain_db = float(backing_gain)
        else:
            lr = rms(stems["vocals"].data) if "vocals" in stems else 0.0
            br = rms(stems["backing"].data)
            if lr > 0 and br > 0:
                g = 20 * np.log10(lr) - 20 * np.log10(br) - backing_below_lead
                backing_gain_db = float(max(-12.0, min(6.0, g)))
                _log(verbose, "  adaptive backing level: %.1f dB" % backing_gain_db)

    # spatialise
    chans = spatialize(stems, fmt, p, sr, vocal_class=vocal_class,
                       backing_gain_db=backing_gain_db, place=place)

    # LFE
    lfe = build_lfe(stems, p["lfe_cross"], sr)
    chans.data["LFE"][:len(lfe)] += lfe[:chans.n]

    # front/rear auto-balance
    info = auto_balance(chans, p["rear_below_front"], rear_gain)
    if info.get("applied"):
        _log(verbose, "  balance: front %.1f / rear %.1f dB -> trim rears %.1f dB"
             % (info["front_db"], info["rear_db"], info["trim"]))

    normalize(chans, norm_level)

    # write
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(stems_folder)),
                                      "Final_%s" % fmt)
    os.makedirs(out_dir, exist_ok=True)
    track_label = track_label or os.path.basename(os.path.normpath(stems_folder))
    channels = {c: chans.total(c) for c in LAYOUTS[fmt]}

    if adm:
        from .adm import write_adm_bwf
        sr_out = sr
        if sr != 48000:                       # Atmos requires 48 kHz
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(48000, sr)
            up, down = 48000 // g, sr // g
            channels = {k: resample_poly(v, up, down).astype(np.float32)
                        for k, v in channels.items()}
            _log(verbose, "  resampled %d -> 48000 Hz (Atmos)" % sr)
            sr_out = 48000
        base = os.path.join(out_dir, "%s_atmos" % track_label)
        out = write_adm_bwf(base, channels, sr_out, objects=None,
                            bits=adm_bits, program_name=track_label)
        _log(verbose, "  wrote %s (ADM BWF, 7.1.2 bed, %d-bit)" % (out, adm_bits))
        return out

    base = os.path.join(out_dir, "%s_%s" % (track_label, fmt))
    out = write_surround(base, channels, fmt, sr, bits=24, force_wav=force_wav)
    _log(verbose, "  wrote %s (%d ch)" % (out, len(LAYOUTS[fmt])))
    return out
