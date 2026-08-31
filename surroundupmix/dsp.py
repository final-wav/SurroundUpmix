"""Small DSP helpers (filters, gain, mono)."""
import numpy as np
from scipy import signal


def db_to_lin(db):
    return float(10.0 ** (db / 20.0))


def mono(st):
    """0.5*(L+R) as a mono array."""
    return (0.5 * (st.L + st.R)).astype(np.float32)


def _butter_sos(cutoff, sr, btype, order=4):
    nyq = 0.5 * sr
    wn = max(1e-4, min(0.999, cutoff / nyq))
    return signal.butter(order, wn, btype=btype, output="sos")


def highpass(x, cutoff, sr, order=4):
    if cutoff <= 0:
        return np.asarray(x, dtype=np.float32)
    sos = _butter_sos(cutoff, sr, "highpass", order)
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def lowpass(x, cutoff, sr, order=4):
    if cutoff <= 0:
        return np.asarray(x, dtype=np.float32)
    sos = _butter_sos(cutoff, sr, "lowpass", order)
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def rms(x):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


# ---------------------------------------------------------------- decorrelation
# Schroeder all-pass cascade. An all-pass has |H(e^jw)| = 1 at every frequency,
# so it changes NO tone and adds NO energy (rms is preserved) - it only scrambles
# phase. That is exactly what we need to make the back/height feed independent of
# the side feed: same spectrum, decorrelated, so the rear field envelops instead
# of collapsing onto the sides. Because the magnitude is flat there is no comb
# filter and, on the already-diffuse ambient layer, no audible discrete echo -
# it reads as natural room decorrelation. The delay sets differ per variant so
# every rear speaker (BL/BR/TFL/TFR) is decorrelated from the others too.
_G = 0.55
_DECORR_DELAYS_44K = {          # prime delays in samples @ 44.1 kHz, per variant
    0: (149, 271, 419),         # BL
    1: (163, 293, 433),         # BR
    2: (181, 311, 457),         # TFL
    3: (197, 331, 479),         # TFR
}


def _allpass(x, m, g):
    """One Schroeder all-pass of delay `m` samples: H(z)=(z^-m - g)/(1 - g z^-m)."""
    b = np.zeros(m + 1, dtype=np.float64); b[0] = -g; b[m] = 1.0
    a = np.zeros(m + 1, dtype=np.float64); a[0] = 1.0; a[m] = -g
    return signal.lfilter(b, a, x)


def decorrelate(x, sr, variant=0, g=_G):
    """Phase-only decorrelation (flat magnitude, energy-preserving). `variant`
    (0..3) picks a distinct delay set so different rear speakers stay mutually
    decorrelated. Delays scale with the sample rate."""
    delays = _DECORR_DELAYS_44K.get(variant, _DECORR_DELAYS_44K[0])
    y = np.asarray(x, dtype=np.float64)
    for base in delays:
        m = max(1, int(round(base * sr / 44100.0)))
        y = _allpass(y, m, g)
    return y.astype(np.float32)
