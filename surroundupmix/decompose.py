"""Direct / ambient (primary / ambient) decomposition of a stereo signal.

This is the core of the v2 upmixer. Instead of deciding placement by stem
*category* and sending a fixed high band to the rears, we split every stem
into two physically meaningful parts:

  * DIRECT  - the inter-channel *coherent* content (a dry, localised source:
              a lead vocal, a solo trumpet, a close guitar). It stays anchored
              at the front, keeping its stereo (left<->right) position.
  * AMBIENT - the *decorrelated* content (room, reverb tails, stereo width).
              This is what a real Atmos mix would design *for* the surround
              speakers, so this - and only this - is what wraps around you.

Method (STFT domain):
  gamma(f,t) = |<L conj(R)>| / sqrt(<|L|^2><|R|^2>)   (short-time coherence)
  smoothed over a few frames/bins (an instantaneous single bin is always
  fully coherent - the averaging is what makes coherence meaningful).

  direct  = X * gamma
  ambient = X * sqrt(1 - gamma^2)

  gamma^2 + (1-gamma^2) = 1, so the split conserves energy per TF bin: a pure
  panned/mono source (gamma->1) is all direct; independent L/R noise
  (gamma->0) is all ambient. This is exactly the decision a mixing engineer
  makes in reverse, made measurable.
"""
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter

from .io import Stereo

DEFAULTS = dict(
    nfft=4096,
    hop=1024,
    coh_time=7,   # coherence smoothing over frames (~160 ms at 44.1k/hop1024)
    coh_freq=3,   # coherence smoothing over frequency bins
    sharp=1.0,    # exponent on gamma; >1 pushes more toward "direct"
)


def _stft(x, nfft, hop):
    f, t, Z = signal.stft(x, nperseg=nfft, noverlap=nfft - hop,
                          window="hann", boundary="zeros", padded=True)
    return Z


def _istft(Z, nfft, hop, n):
    _, x = signal.istft(Z, nperseg=nfft, noverlap=nfft - hop, window="hann",
                        boundary=True)
    x = x[:n] if len(x) >= n else np.concatenate([x, np.zeros(n - len(x))])
    return x.astype(np.float32)


def coherence_masks(Lz, Rz, p):
    """Return (direct_mask, ambient_mask) as real arrays over the TF grid."""
    Pxx = uniform_filter(np.abs(Lz) ** 2, size=(p["coh_freq"], p["coh_time"]))
    Pyy = uniform_filter(np.abs(Rz) ** 2, size=(p["coh_freq"], p["coh_time"]))
    cross = Lz * np.conj(Rz)
    Pxy_r = uniform_filter(cross.real, size=(p["coh_freq"], p["coh_time"]))
    Pxy_i = uniform_filter(cross.imag, size=(p["coh_freq"], p["coh_time"]))
    Pxy = np.abs(Pxy_r + 1j * Pxy_i)
    gamma = Pxy / np.sqrt(Pxx * Pyy + 1e-12)
    gamma = np.clip(gamma, 0.0, 1.0)
    if p["sharp"] != 1.0:
        gamma = gamma ** p["sharp"]
    direct = gamma
    ambient = np.sqrt(np.clip(1.0 - gamma ** 2, 0.0, 1.0))
    return direct, ambient


def decompose(st, **kw):
    """Split a Stereo into (direct: Stereo, ambient: Stereo)."""
    p = dict(DEFAULTS)
    if "coh_time" not in kw and hasattr(st, "sr") and st.sr:
        # scale coh_time to match ~160 ms smoothing regardless of sample rate
        # (~7 frames at 44.1 kHz / hop 1024)
        p["coh_time"] = max(1, int(round(0.1625 * st.sr / p["hop"])))
    p.update(kw)
    n = len(st)
    Lz = _stft(st.L, p["nfft"], p["hop"])
    Rz = _stft(st.R, p["nfft"], p["hop"])
    d_mask, a_mask = coherence_masks(Lz, Rz, p)
    direct = Stereo(np.stack([_istft(Lz * d_mask, p["nfft"], p["hop"], n),
                              _istft(Rz * d_mask, p["nfft"], p["hop"], n)], axis=1), st.sr)
    ambient = Stereo(np.stack([_istft(Lz * a_mask, p["nfft"], p["hop"], n),
                               _istft(Rz * a_mask, p["nfft"], p["hop"], n)], axis=1), st.sr)
    return direct, ambient


def broadband_coherence(st):
    """Scalar 0..1: how inter-channel correlated the whole signal is.
    ~1 for a mono/panned source, ~0 for independent L/R. Used in tests and
    for the doubled-vocal decision (a doubled vocal is highly coherent)."""
    L = st.L - st.L.mean()
    R = st.R - st.R.mean()
    num = float(np.mean(L * R))
    den = float(np.sqrt(np.mean(L * L) * np.mean(R * R)) + 1e-12)
    return abs(num / den)


def lateral_pan(st):
    """Broadband pan of the signal in [-1, 1] (-1 hard left, +1 hard right),
    from the inter-channel level difference. Used to extend a real left->right
    movement into the surround field (never to invent motion)."""
    el = float(np.mean(st.L ** 2))
    er = float(np.mean(st.R ** 2))
    if el + er < 1e-12:
        return 0.0
    return (er - el) / (er + el)
