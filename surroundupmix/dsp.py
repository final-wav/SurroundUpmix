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
