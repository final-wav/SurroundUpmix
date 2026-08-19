"""Vocal width classification - a short-delay-doubled vocal must stay forward.

Ported from the original detect_vocal.py (GCC-PHAT + side-autocorrelation)
into numpy. A doubled/Haas'd vocal has correlated-but-delayed channels: its
zero-lag coherence is lowered, so the direct/ambient split would leak some of
it into 'ambient' and spread it -> comb-filter mush. We detect that case and
tell the router to keep the vocal forward (no ambient spread).

Returns one of 'double' | 'reverb' | 'dry'.
"""
import numpy as np


def classify_vocal_width(st):
    sr = st.sr
    L = st.L.astype(np.float64)
    R = st.R.astype(np.float64)

    # analyse up to 30 s from the middle
    maxn = sr * 30
    if L.size > maxn:
        s = (L.size - maxn) // 2
        L, R = L[s:s + maxn], R[s:s + maxn]
    if L.size < sr or (np.abs(L).max() < 2.5e-4 and np.abs(R).max() < 2.5e-4):
        return "dry"

    L = L - L.mean()
    R = R - R.mean()
    S = L - R
    M = L + R
    side = float(np.sqrt(np.mean(S ** 2)) / (np.sqrt(np.mean(M ** 2)) + 1e-12))
    if side < 0.03:
        return "dry"  # essentially mono -> nothing to spread

    N = L.size
    fftlen = 1 << int(np.ceil(np.log2(2 * N)))
    lo, hi = int(0.002 * sr), int(0.045 * sr)

    # (a) GCC-PHAT L/R peak
    G = np.fft.rfft(L, fftlen) * np.conj(np.fft.rfft(R, fftlen))
    G = G / (np.abs(G) + 1e-6)
    cc = np.fft.irfft(G, fftlen)
    maxlag = int(0.05 * sr)
    pos = np.abs(cc[1:maxlag + 1])[lo:hi]
    neg = np.abs(cc[fftlen - maxlag:][::-1])[lo:hi]
    band = np.maximum(pos, neg)
    peak = float(band.max()) if band.size else 0.0

    # (b) side (L-R) short vs long lag energy
    Sn = S / (np.sqrt(np.sum(S ** 2)) + 1e-12)
    sac = np.abs(np.fft.irfft(np.abs(np.fft.rfft(Sn, fftlen)) ** 2, fftlen))
    short_e = float(np.mean(sac[int(0.002 * sr):int(0.045 * sr)]))
    long_e = float(np.mean(sac[int(0.08 * sr):int(0.40 * sr)]))
    ratio = short_e / (long_e + 1e-12)

    if peak >= 0.15 or ratio >= 2.4:
        return "double"   # structured width -> keep forward
    return "reverb"       # diffuse width -> safe to spread
