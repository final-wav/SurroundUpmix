"""Detail recovery: reinject what the separator failed to reproduce.

A neural separator (Demucs) does NOT reconstruct the mixture perfectly - the
sum of its stems is a few dB short of the master, and the shortfall is a broad-
band, low-level layer (~ -19 dB on measured material): fine transients, air,
room, breaths, the quiet detail that lives 15-30 dB down. The band *balance*
looks fine because loud content dominates it, but that quiet detail is exactly
where the reconstruction error sits, so it is the first thing to vanish.

Because we still have the ORIGINAL master, that lost layer is recoverable
*exactly*:  residual = original - sum(stems).  Adding the residual back makes
stems + residual == original again. The engine then routes the residual by the
same direct/ambient rule as everything else (its coherent part rebuilds the
front image, its diffuse part wraps), so the detail returns where it belongs.
"""
import glob
import os
import numpy as np
import soundfile as sf
from scipy.signal import correlate

# the raw Demucs stems whose sum should approximate the master (NOT the karaoke
# split's lead/backing/vocals_full, which would double-count the vocal)
RAW_STEMS = ("bass", "drums", "vocals", "other", "guitar", "piano")


def _load(path, sr):
    x, s = sf.read(path, dtype="float32", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    if s != sr:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(sr, s)
        x = resample_poly(x, sr // g, s // g, axis=0).astype("float32")
    return x


def compute_residual(stems_dir, original_path, min_atten_db=9.0):
    """residual = original - sum(raw stems), integer-lag aligned.

    Returns (residual (n, 2) float32, sr) or (None, None) if it can't be built
    or can't be TRUSTED. Uses gain 1.0 (no fit) so stems + residual reconstruct
    the master exactly.

    Trust gate: the stems must actually explain the master. We fit the best gain
    g and measure how far below the original the best-fit residual sits; if it's
    not at least `min_atten_db` dB down (or g is wild), the "residual" is really
    misalignment / a lossy-encode or level mismatch (e.g. an MP3 with a big
    encoder delay), NOT recoverable detail - reinjecting it would add noise, so
    we refuse. Lossless masters (FLAC/WAV) pass cleanly (~ -15..-20 dB); a source
    the stems don't line up with is rejected (returns None)."""
    stem_files = [p for p in glob.glob(os.path.join(stems_dir, "*.flac"))
                  if os.path.splitext(os.path.basename(p))[0].lower() in RAW_STEMS]
    if not stem_files or not os.path.isfile(original_path):
        return None, None
    sr = sf.info(stem_files[0]).samplerate
    mix = None
    for p in stem_files:
        x = _load(p, sr)
        if mix is None:
            mix = x
        else:
            m = min(len(mix), len(x))
            mix = mix[:m] + x[:m]
    orig = _load(original_path, sr)
    n = min(len(orig), len(mix))
    orig, mix = orig[:n], mix[:n]

    # integer-lag align (mid 60 s, mono) so a decoder delay can't wreck the null
    mo, mm = orig[:, 0] + orig[:, 1], mix[:, 0] + mix[:, 1]
    a = max(0, n // 2 - 30 * sr)
    b = min(n, a + 60 * sr)
    c = correlate(mo[a:b], mm[a:b], mode="full", method="fft")
    lag = int(np.argmax(np.abs(c))) - (b - a - 1)
    if lag > 0:
        mix = mix[lag:]
        orig = orig[:len(mix)]
    elif lag < 0:
        orig = orig[-lag:]
        mix = mix[:len(orig)]
    n = min(len(orig), len(mix))
    orig, mix = orig[:n], mix[:n]

    # trust gate: do the stems actually explain the master?
    g = float(np.sum(orig * mix) / (np.sum(mix * mix) + 1e-20))
    fit = orig - g * mix
    ro = float(np.sqrt(np.mean(orig ** 2)) + 1e-12)
    rf = float(np.sqrt(np.mean(fit ** 2)) + 1e-12)
    atten = 20.0 * np.log10(ro / rf)          # how far the best-fit residual is down
    if atten < min_atten_db or not (0.5 <= g <= 2.0):
        return None, None                     # untrustworthy -> don't recover

    residual = (orig - mix).astype("float32")  # g=1 so stems+residual == master
    return residual, sr


def write_residual(stems_dir, original_path):
    """Compute the residual and write it as residual.flac into `stems_dir`.
    Returns the path, or None if it couldn't be built."""
    residual, sr = compute_residual(stems_dir, original_path)
    if residual is None:
        return None
    out = os.path.join(stems_dir, "residual.flac")
    sf.write(out, residual, sr, subtype="PCM_24")
    return out
