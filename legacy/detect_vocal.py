#!/usr/bin/env python3
# detect_vocal.py <analysis.wav>
# Classifies the stereo width of a vocal stem so the upmixer can avoid smearing an
# already short-delay-doubled vocal into a phasing / "many voices" mess.
#
# Prints exactly one line:
#   RESULT <DOUBLE|REVERB|DRY> lag=<ms> prom=<x> side=<0..1>
#
# Method: GCC-PHAT between L and R (whitened cross-correlation). A short-delay double
# (Haas / doubler) shows a sharp, prominent peak at a small NON-zero lag; diffuse reverb
# does not; a mono/centred vocal has almost no side energy. Whitening removes the signal's
# own spectral colour, so a mono signal no longer looks like a delay (fixes the naive-xcorr
# false positive).
#
# Decision the caller acts on:  REVERB -> may spread to the rears;  DOUBLE/DRY -> keep forward.
import sys, wave, array, math

def emit(kind, lag=0.0, ratio=0.0, side=0.0, peak=0.0):
    print("RESULT %s lag=%.1f ratio=%.2f peak=%.3f side=%.3f" % (kind, lag, ratio, peak, side))
    sys.exit(0)

try:
    import torch
except Exception:
    emit("DRY")

def load_wav(path):
    w = wave.open(path, 'rb')
    ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n); w.close()
    if sw != 2:
        emit("DRY")
    a = array.array('h'); a.frombytes(raw)
    t = torch.tensor(a, dtype=torch.float32)
    if ch >= 2:
        return t[0::ch], t[1::ch], sr
    return t, t.clone(), sr

def main():
    if len(sys.argv) < 2:
        emit("DRY")
    L, R, sr = load_wav(sys.argv[1])

    maxn = sr * 30
    if L.numel() > maxn:
        s = (L.numel() - maxn) // 2
        L, R = L[s:s + maxn], R[s:s + maxn]
    if L.numel() < sr or (L.abs().max() < 8.0 and R.abs().max() < 8.0):
        emit("DRY")

    L = L - L.mean(); R = R - R.mean()
    # side / mid energy ratio: how much stereo width is there at all
    S = L - R; M = L + R
    side = (S.pow(2).mean().sqrt() / (M.pow(2).mean().sqrt() + 1e-9)).item()
    if side < 0.03:
        emit("DRY", 0.0, 0.0, side)          # essentially mono -> nothing to spread

    N = L.numel()
    fftlen = 1 << int(math.ceil(math.log2(2 * N)))
    lo, hi = int(0.002 * sr), int(0.045 * sr)

    # (a) GCC-PHAT L/R peak: catches clean/strong short-delay doubles
    G = torch.fft.rfft(L, fftlen) * torch.conj(torch.fft.rfft(R, fftlen))
    G = G / (G.abs() + 1e-6)
    cc = torch.fft.irfft(G, fftlen)
    maxlag = int(0.05 * sr)
    band = torch.maximum(cc[1:maxlag + 1].abs()[lo:hi], cc[fftlen - maxlag:].flip(0).abs()[lo:hi])
    peak = band.max().item()
    lag_ms = (lo + band.argmax().item()) / sr * 1000.0

    # (b) side (L-R) autocorrelation: short-lag vs long-lag energy.
    # Structured width (doubling / short widener) piles the side energy into short lags
    # -> comb-filter-prone; diffuse reverb spreads it into long lags. This catches the
    # "messy" real-world doubles that (a) misses. Calibrated on real material.
    Sn = S / (S.pow(2).sum().sqrt() + 1e-9)
    sac = torch.fft.irfft(torch.fft.rfft(Sn, fftlen) * torch.conj(torch.fft.rfft(Sn, fftlen)), fftlen).abs()
    short_e = sac[int(0.002 * sr):int(0.045 * sr)].mean().item()
    long_e = sac[int(0.08 * sr):int(0.40 * sr)].mean().item()
    ratio = short_e / (long_e + 1e-9)

    if peak >= 0.15 or ratio >= 2.4:
        emit("DOUBLE", lag_ms, ratio, side, peak)   # structured width -> keep forward
    else:
        emit("REVERB", lag_ms, ratio, side, peak)   # diffuse width -> safe to spread

main()
