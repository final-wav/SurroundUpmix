"""Synthetic-signal tests for the SurroundUpmix v2 engine.

These prove the *physics* of the approach without needing real audio or a
listening test: coherent content must land at the front, decorrelated content
must wrap to the rears, bass must stay out of the rears, the auto-balance must
hit its target, and the files must carry the right channel counts and mask.

Run with `pytest`, or directly: `python tests/test_engine.py`.
"""
import os
import struct
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from surroundupmix.io import Stereo, write_surround
from surroundupmix.layouts import LAYOUTS, channel_mask
from surroundupmix import decompose as dc
from surroundupmix.detect import classify_vocal_width
from surroundupmix.engine import upmix_folder

SR = 44100


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x) ** 2)))


def _db(a, b):
    return 20 * np.log10((_rms(a) + 1e-12) / (_rms(b) + 1e-12))


# ----------------------------------------------------------------- decompose
def test_coherent_goes_direct():
    rng = np.random.default_rng(0)
    m = rng.standard_normal(SR * 2).astype("float32") * 0.2
    st = Stereo(np.stack([m, m], 1), SR)
    d, a = dc.decompose(st)
    assert _rms(d.data) > _rms(a.data) * 8, "mono source must be ~all direct"


def test_decorrelated_goes_ambient():
    rng = np.random.default_rng(1)
    st = Stereo((rng.standard_normal((SR * 2, 2)) * 0.2).astype("float32"), SR)
    d, a = dc.decompose(st)
    # compare energy (mean square): independent L/R lands ~all in ambient
    ea = float(np.mean(a.data ** 2)); ed = float(np.mean(d.data ** 2))
    assert ea > ed * 3, "independent L/R must be ~all ambient"


def test_energy_conserved():
    rng = np.random.default_rng(2)
    m = rng.standard_normal(SR * 2).astype("float32") * 0.2
    amb = rng.standard_normal((SR * 2, 2)).astype("float32") * 0.15
    st = Stereo(np.stack([m, m], 1) + amb, SR)
    d, a = dc.decompose(st)
    ein = float(np.mean(st.data ** 2))
    erec = float(np.mean(d.data ** 2) + np.mean(a.data ** 2))
    assert 0.9 < erec / ein < 1.1, "split must roughly conserve energy"


def test_pan_detection():
    rng = np.random.default_rng(3)
    m = rng.standard_normal(SR).astype("float32") * 0.2
    right = Stereo(np.stack([m * 0.2, m], 1), SR)
    left = Stereo(np.stack([m, m * 0.2], 1), SR)
    assert dc.lateral_pan(right) > 0.5
    assert dc.lateral_pan(left) < -0.5


# ----------------------------------------------------------------- detector
def _voice_like(n, rng):
    """Broadband, amplitude-modulated 'voice' (band-limited noise)."""
    from scipy import signal
    sos = signal.butter(4, 4000 / (SR / 2), "low", output="sos")
    v = signal.sosfilt(sos, rng.standard_normal(n)).astype("float32")
    v *= 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 3 * np.arange(n) / SR))
    return (v * 0.3 / (np.abs(v).max() + 1e-9)).astype("float32")


def test_doubled_vocal_detected():
    rng = np.random.default_rng(7)
    v = _voice_like(SR * 3, rng)
    lag = int(0.012 * SR)                      # 12 ms Haas double
    R = np.concatenate([np.zeros(lag, "float32"), v])[:len(v)]
    st = Stereo(np.stack([v, R], 1), SR)
    assert classify_vocal_width(st) == "double"


def test_diffuse_vocal_is_reverb():
    rng = np.random.default_rng(4)
    v = _voice_like(SR * 3, rng)
    rev = rng.standard_normal((len(v), 2)) * 0.05
    st = Stereo((np.stack([v, v], 1) + rev).astype("float32"), SR)
    assert classify_vocal_width(st) in ("reverb", "dry")


# ----------------------------------------------------------------- I/O mask
def test_wav_extensible_mask():
    ch = {c: np.zeros(1000, "float32") for c in LAYOUTS["7.1.2"]}
    with tempfile.TemporaryDirectory() as d:
        out = write_surround(os.path.join(d, "x"), ch, "7.1.2", SR)
        assert out.endswith(".wav")
        with open(out, "rb") as f:
            b = f.read(80)
        i = b.find(b"fmt ")
        tag = struct.unpack("<H", b[i + 8:i + 10])[0]
        mask = struct.unpack("<I", b[i + 28:i + 32])[0]
        assert tag == 0xFFFE
        assert mask == channel_mask("7.1.2")


# ----------------------------------------------------------------- full engine
def _make_stems(d):
    rng = np.random.default_rng(5)
    n = SR * 2
    t = np.arange(n) / SR
    bass = (0.3 * np.sin(2 * np.pi * 80 * t)).astype("float32")
    sf.write(os.path.join(d, "bass.flac"), np.stack([bass, bass], 1), SR)
    imp = np.zeros(n, "float32"); imp[::SR // 2] = 0.3
    drums = np.stack([imp, imp], 1) + rng.standard_normal((n, 2)) * 0.02
    sf.write(os.path.join(d, "drums.flac"), drums.astype("float32"), SR)
    voc = (0.25 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    sf.write(os.path.join(d, "vocals.flac"),
             (np.stack([voc, voc], 1) + rng.standard_normal((n, 2)) * 0.02).astype("float32"), SR)
    other = rng.standard_normal((n, 2)).astype("float32") * 0.15   # decorrelated texture
    sf.write(os.path.join(d, "other.flac"), other, SR)


def test_channel_counts_and_container():
    with tempfile.TemporaryDirectory() as d:
        stems = os.path.join(d, "stems"); os.makedirs(stems)
        _make_stems(stems)
        for fmt, nch, ext in [("5.1", 6, ".flac"), ("7.1", 8, ".flac"),
                              ("7.1.2", 10, ".wav")]:
            out = upmix_folder(stems, fmt=fmt, out_dir=os.path.join(d, fmt),
                               verbose=False)
            assert out.endswith(ext)
            x, _ = sf.read(out)
            assert x.shape[1] == nch


def test_bass_stays_front_and_ambient_wraps():
    with tempfile.TemporaryDirectory() as d:
        stems = os.path.join(d, "stems"); os.makedirs(stems)
        _make_stems(stems)
        out = upmix_folder(stems, fmt="7.1", out_dir=os.path.join(d, "o"),
                           verbose=False)
        x, _ = sf.read(out)
        idx = {c: i for i, c in enumerate(LAYOUTS["7.1"])}
        # the decorrelated 'other' must have created real rear energy
        rear = _rms(x[:, idx["SL"]]) + _rms(x[:, idx["SR"]])
        assert rear > 1e-3, "decorrelated texture must reach the surrounds"
        # LFE must carry low-frequency energy
        assert _rms(x[:, idx["LFE"]]) > 1e-3


def test_auto_balance_hits_target():
    with tempfile.TemporaryDirectory() as d:
        stems = os.path.join(d, "stems"); os.makedirs(stems)
        _make_stems(stems)
        out = upmix_folder(stems, fmt="7.1", out_dir=os.path.join(d, "o"),
                           rear_below_front=16, verbose=False)
        x, _ = sf.read(out)
        idx = {c: i for i, c in enumerate(LAYOUTS["7.1"])}
        front = np.concatenate([x[:, idx["FL"]], x[:, idx["FR"]], x[:, idx["FC"]]])
        rear = np.concatenate([x[:, idx[c]] for c in ("BL", "BR", "SL", "SR")])
        # rear field should sit roughly 16 dB under the front (within a few dB)
        assert -24 < _db(rear, front) < -10


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok  ", fn.__name__)
    print("\nall %d tests passed" % len(fns))


if __name__ == "__main__":
    _run_all()
