"""Binaural cue detection + a gentle, gated front/back depth steer.

Ordinary stereo (pan-pot) places a source by LEVEL only: the two channels are
scaled copies of one mono source, so the interaural PHASE is ~0 - the source
sits at zero inter-channel delay and carries no front/back information.

Binaural material (dummy-head, or an HRTF / binaural panner) places sources
with a real interaural TIME difference (ITD, up to ~0.8 ms). That ITD is exactly
what a pan-pot mix does NOT have, so it is a safe tell: we look for COHERENT
content sitting at a consistent sub-millisecond delay. Decorrelated width/reverb
has low coherence and is excluded, so a normal reverberant pop mix scores ~0 and
cannot trigger the steer.

analyze() -> {confidence 0..1, rear_bias -1..+1, itd_ms}. The steer is ALWAYS
applied as  confidence * user_amount  and hard-bounded, so a non-binaural song
(confidence ~0) is untouched no matter where the slider sits.
"""
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter


def analyze(st, max_seconds=90):
    sr = st.sr
    L = st.L.astype(np.float64)
    R = st.R.astype(np.float64)
    n = min(len(L), int(sr * max_seconds))
    if n < sr:
        return {"confidence": 0.0, "rear_bias": 0.0, "itd_ms": 0.0}
    s = (len(L) - n) // 2
    L, R = L[s:s + n], R[s:s + n]
    if max(np.abs(L).max(), np.abs(R).max()) < 1e-5:
        return {"confidence": 0.0, "rear_bias": 0.0, "itd_ms": 0.0}

    nfft, hop = 4096, 1024
    f, _, Lz = signal.stft(L, fs=sr, nperseg=nfft, noverlap=nfft - hop,
                           window="hann", boundary=None)
    _, _, Rz = signal.stft(R, fs=sr, nperseg=nfft, noverlap=nfft - hop,
                           window="hann", boundary=None)
    Pxx = uniform_filter(np.abs(Lz) ** 2, size=(1, 5))
    Pyy = uniform_filter(np.abs(Rz) ** 2, size=(1, 5))
    cross = Lz * np.conj(Rz)
    Pxy = (uniform_filter(cross.real, size=(1, 5))
           + 1j * uniform_filter(cross.imag, size=(1, 5)))
    coh = np.abs(Pxy) / np.sqrt(Pxx * Pyy + 1e-12)
    ipd = np.angle(Pxy)

    # unambiguous band: below ~1 kHz the interaural phase is < pi for ITDs up to
    # ~0.8 ms, so ITD = IPD / (2*pi*f) is not phase-wrapped
    band = (f >= 200) & (f <= 1000)
    fb = f[band][:, None]
    itd = ipd[band, :] / (2 * np.pi * fb + 1e-12)          # seconds, per TF
    w = np.where(coh[band, :] > 0.5, coh[band, :], 0.0)     # weight by coherence

    wsum = w.sum(axis=0)                                    # per frame
    good = wsum > 1e-3
    if not np.any(good):
        return {"confidence": 0.0, "rear_bias": 0.0, "itd_ms": 0.0}
    m = np.sum(w * itd, axis=0) / (wsum + 1e-12)            # weighted mean ITD/frame
    var = np.sum(w * (itd - m) ** 2, axis=0) / (wsum + 1e-12)
    consistent = np.sqrt(var) < 0.25e-3                     # ITD agrees across freq
    binaural = good & consistent & (np.abs(m) > 0.06e-3) & (np.abs(m) < 0.9e-3)

    frac = float(np.sum(binaural)) / float(np.sum(good))
    confidence = float(np.clip((frac - 0.15) / 0.45, 0.0, 1.0))
    itd_ms = float(np.median(np.abs(m[binaural])) * 1000) if np.any(binaural) else 0.0

    # rear_bias: spectral tilt of the wide (side) content. Rear sources are
    # pinna-shadowed (darker); a dark side field leans rear (+), a bright one
    # front (-). Heuristic - only ever used scaled by confidence and the slider.
    S = L - R
    Sf = np.abs(np.fft.rfft(S))
    ff = np.fft.rfftfreq(len(S), 1 / sr)
    elo = float(np.sum(Sf[(ff >= 200) & (ff < 2000)] ** 2)) + 1e-12
    ehi = float(np.sum(Sf[(ff >= 5000) & (ff < 15000)] ** 2)) + 1e-12
    hf_ratio = ehi / (elo + ehi)
    rear_bias = float(np.clip((0.18 - hf_ratio) / 0.18, -1.0, 1.0))
    return {"confidence": confidence, "rear_bias": rear_bias, "itd_ms": itd_ms}
