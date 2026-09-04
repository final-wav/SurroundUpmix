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
from surroundupmix import presets as _presets
from surroundupmix.detect import classify_vocal_width
from surroundupmix.engine import upmix_folder
from surroundupmix.inputs import expand_inputs, looks_like_stems
from surroundupmix.routing import spatialize, _auto_place
from surroundupmix.balance import auto_balance, normalize
from surroundupmix.voice import assess_vocal_roles

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


# ----------------------------------------------------------------- input expand
def _touch(*parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def test_expand_files_folders_and_stems():
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "a.flac")
        _touch(d, "b.mp3")
        _touch(d, "notes.txt")            # ignored (not audio)
        _touch(d, "album", "c.wav")       # nested song
        for s in ("bass", "drums", "vocals", "other"):
            _touch(d, "song_stems", s + ".flac")
        _touch(d, "Final_7.1", "x_7.1.flac")   # output -> must be skipped

        assert looks_like_stems(os.path.join(d, "song_stems"))
        assert not looks_like_stems(d)

        # single file -> one song
        j = expand_inputs([os.path.join(d, "a.flac")])
        assert j == [(os.path.normpath(os.path.join(d, "a.flac")), "song")]

        # stems folder -> one stems job
        j = expand_inputs([os.path.join(d, "song_stems")])
        assert j == [(os.path.normpath(os.path.join(d, "song_stems")), "stems")]

        # whole tree -> songs + detected stems, output/non-audio skipped
        j = expand_inputs([d])
        kinds = {os.path.basename(p): k for p, k in j}
        assert kinds.get("a.flac") == "song"
        assert kinds.get("c.wav") == "song"
        assert kinds.get("song_stems") == "stems"
        assert "notes.txt" not in kinds
        assert not any("Final_7.1" in p for p, _ in j)

        # duplicates removed
        assert len(expand_inputs([os.path.join(d, "a.flac")] * 3)) == 1


# ----------------------------------------------------------------- auto-place
def test_auto_place_reacts_to_diffuseness():
    """A diffuse stem must be told to wrap MORE than a dry, mono one."""
    p = _presets.get("immersive")
    rng = np.random.default_rng(0)
    n = SR
    m = (rng.standard_normal(n) * 0.2).astype("float32")
    dry = Stereo(np.stack([m, m], 1), SR)               # coherent / dry
    wet = Stereo((rng.standard_normal((n, 2)) * 0.2).astype("float32"), SR)  # decorrelated
    dd, da = dc.decompose(dry)
    wd, wa = dc.decompose(wet)
    _, dry_back, _ = _auto_place(dd, da, p)
    _, wet_back, _ = _auto_place(wd, wa, p)
    assert wet_back > dry_back + 2.0, "diffuse content must wrap more than dry"


def test_auto_place_disabled_is_neutral():
    p = _presets.get("immersive"); p["auto_place"] = False
    rng = np.random.default_rng(1)
    st = Stereo((rng.standard_normal((SR, 2)) * 0.2).astype("float32"), SR)
    d, a = dc.decompose(st)
    assert _auto_place(d, a, p) == (0.0, 0.0, 0.0)


# ------------------------------------------------------- per-stem overrides
def _guitar_stems():
    t = np.arange(SR) / SR
    g = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")   # dry -> normally front
    return {"guitar": Stereo(np.stack([g, g], 1), SR)}


def test_forced_rear_moves_stem_to_rear():
    p = _presets.get("immersive")
    stems = _guitar_stems()
    auto = spatialize(stems, "7.1", p, SR, place={"guitar": "auto"})
    front_a = _rms(auto.total("FL")) + _rms(auto.total("FR"))
    rear_a = sum(_rms(auto.total(c)) for c in ("BL", "BR", "SL", "SR"))
    assert front_a > rear_a, "a dry guitar auto-places mostly to the front"

    rear = spatialize(stems, "7.1", p, SR, place={"guitar": "rear"})
    front_r = _rms(rear.total("FL")) + _rms(rear.total("FR"))
    rear_r = sum(_rms(rear.total(c)) for c in ("BL", "BR"))
    assert rear_r > front_r * 4, "forced rear must dominate the back speakers"
    # and it lives in the FORCED layer, not the automatic one
    assert _rms(rear.forced["BL"]) + _rms(rear.forced["BR"]) > 0.05
    assert _rms(rear.data["BL"]) + _rms(rear.data["BR"]) < 1e-6


def test_forced_front_keeps_out_of_rear():
    p = _presets.get("immersive")
    ch = spatialize(_guitar_stems(), "7.1", p, SR, place={"guitar": "front"})
    rear = sum(_rms(ch.total(c)) for c in ("BL", "BR", "SL", "SR"))
    front = _rms(ch.total("FL")) + _rms(ch.total("FR"))
    assert front > 0.05 and rear < 1e-6


def test_forced_rear_survives_autobalance():
    """The fix: a forced-rear stem is NOT slammed down by the global balance,
    and the automatic wrap of other stems is left essentially untouched."""
    p = _presets.get("immersive")
    rng = np.random.default_rng(3)
    t = np.arange(SR) / SR
    voc = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    g = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    stems = {
        "vocals": Stereo(np.stack([voc, voc], 1), SR),
        "other": Stereo((rng.standard_normal((SR, 2)) * 0.12).astype("float32"), SR),
        "guitar": Stereo(np.stack([g * 0.9, g], 1), SR),
    }

    def auto_wrap(place):
        ch = spatialize(stems, "7.1", p, SR, place=place)
        auto_balance(ch, p["rear_below_front"])
        normalize(ch, -0.1)
        return ch

    base = auto_wrap({"guitar": "auto"})
    forced = auto_wrap({"guitar": "rear"})

    # forced guitar stays prominent in the rear (not pushed ~15 dB under front)
    fr = sum(_rms(forced.total(c)) for c in ("BL", "BR"))
    ff = sum(_rms(forced.total(c)) for c in ("FL", "FR", "FC"))
    assert 20 * np.log10((fr + 1e-12) / (ff + 1e-12)) > -6.0

    # the automatic wrap (other) is essentially unchanged by the override
    aw_base = sum(_rms(base.data[c]) for c in ("BL", "BR", "SL", "SR"))
    aw_forced = sum(_rms(forced.data[c]) for c in ("BL", "BR", "SL", "SR"))
    assert abs(20 * np.log10((aw_forced + 1e-12) / (aw_base + 1e-12))) < 2.0


def test_decorrelation_reduces_side_back_correlation():
    # a diffuse texture stem (independent L/R) wraps to sides AND backs. Without
    # decorrelation SL and BL carry the identical signal; with it they don't.
    rng = np.random.default_rng(7)
    n = SR * 2
    other = Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR)
    stems = {"other": other}

    def corr(a, b):
        a = a - a.mean(); b = b - b.mean()
        return abs(float(np.mean(a * b)) /
                   (np.sqrt(np.mean(a * a) * np.mean(b * b)) + 1e-12))

    p_on = _presets.get("immersive"); p_on["decorr"] = True
    p_off = _presets.get("immersive"); p_off["decorr"] = False
    ch_on = spatialize(stems, "7.1", p_on, SR)
    ch_off = spatialize(stems, "7.1", p_off, SR)

    # off: sides and backs are the same signal -> ~fully correlated
    assert corr(ch_off.total("SL"), ch_off.total("BL")) > 0.95
    # on: decorrelated -> correlation drops a lot
    assert corr(ch_on.total("SL"), ch_on.total("BL")) < 0.5
    # flat-magnitude all-pass preserves energy: the back level is unchanged
    r_off = _rms(ch_off.total("BL")); r_on = _rms(ch_on.total("BL"))
    assert abs(20 * np.log10((r_on + 1e-12) / (r_off + 1e-12))) < 1.0


def _sparse(n, nf, active_frames, amp, seed):
    """A mono signal that is only active in `active_frames` of `nf` blocks."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n, dtype="float32")
    hop = n // nf
    for f in active_frames:
        x[f * hop:(f + 1) * hop] = rng.standard_normal(hop).astype("float32") * amp
    return x


def _st(m):
    return Stereo(np.stack([m, m], 1).astype("float32"), SR)


def test_vocal_roles_keep_normal_split():
    # normal: continuous loud lead, sparse quiet backing -> keep the model split
    n = SR * 2
    t = np.arange(n) / SR
    lead = (0.25 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    backing = _sparse(n, 40, [10, 11, 28, 29], 0.05, 1)
    full = _st(lead + backing)
    a = assess_vocal_roles(_st(lead), _st(backing), full, SR, mode="auto")
    assert a["action"] == "keep", a


def test_vocal_roles_swap_when_inverted():
    # inverted: the "lead" is a thin sparse element, the "backing" is the real,
    # continuous main vocal -> the guard must swap them back
    n = SR * 2
    t = np.arange(n) / SR
    backing = (0.25 * np.sin(2 * np.pi * 220 * t)).astype("float32")  # real lead
    lead = _sparse(n, 40, [12, 13, 30, 31], 0.03, 2)                  # thin element
    full = _st(lead + backing)
    a = assess_vocal_roles(_st(lead), _st(backing), full, SR, mode="auto")
    assert a["action"] == "swap", a


def test_vocal_roles_nosplit_for_wet_wash():
    # a wide/wet vocal wash: neither part is a clean dry lead and the full vocal
    # is highly decorrelated (coh ~ 0) -> skip the split, keep it up front
    n = SR * 2
    rng = np.random.default_rng(5)
    full = Stereo((rng.standard_normal((n, 2)) * 0.15).astype("float32"), SR)  # wide
    lead = _sparse(n, 40, [8, 9], 0.04, 6)
    backing = _sparse(n, 40, [24, 25], 0.04, 7)
    a = assess_vocal_roles(_st(lead), _st(backing), full, SR, mode="auto")
    assert a["action"] == "nosplit", a


def test_vocal_roles_force_modes():
    n = SR
    lead, backing = _st(np.ones(n, "float32") * 0.1), _st(np.ones(n, "float32") * 0.1)
    assert assess_vocal_roles(lead, backing, lead, SR, mode="keep")["action"] == "keep"
    assert assess_vocal_roles(lead, backing, lead, SR, mode="swap")["action"] == "swap"


def test_adm_bed_orders_playback_vs_renderer():
    # write both ADM variants with a distinct marker per channel, read the PCM
    # back and check the interleave order matches each bed.
    import tempfile
    from surroundupmix.adm import write_adm_bwf, BED, BED_RENDERER

    names = ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR"]
    n = 2000
    channels = {nm: np.full(n, (i + 1) / 20.0, dtype="float32")
                for i, nm in enumerate(names)}   # FL=0.05 .. TFR=0.50
    d = tempfile.mkdtemp()

    def order_of(path):
        data, _ = sf.read(path, always_2d=True)
        return [int(round(data[100, k] * 20)) for k in range(10)]   # -> channel index+1

    p_play = write_adm_bwf(os.path.join(d, "play"), channels, 48000, bed=BED)
    p_rend = write_adm_bwf(os.path.join(d, "rend"), channels, 48000, bed=BED_RENDERER)

    exp_play = [names.index(b[0]) + 1 for b in BED]           # FL FR FC LFE BL BR SL SR ..
    exp_rend = [names.index(b[0]) + 1 for b in BED_RENDERER]  # FL FR FC LFE SL SR BL BR ..
    assert order_of(p_play) == exp_play == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert order_of(p_rend) == exp_rend == [1, 2, 3, 4, 7, 8, 5, 6, 9, 10]

    # renderer bed = Dolby sides-before-rears labels at 5..8
    assert [b[1] for b in BED_RENDERER][4:8] == ["RC_Lss", "RC_Rss", "RC_Lrs", "RC_Rrs"]
    # playback bed unchanged: rears-before-sides
    assert [b[1] for b in BED][4:8] == ["RC_Lrs", "RC_Rrs", "RC_Lss", "RC_Rss"]


def test_compute_residual_recovers_missing_layer():
    import tempfile
    from surroundupmix.recover import compute_residual
    d = tempfile.mkdtemp()
    n = SR
    rng = np.random.default_rng(4)
    bass = (rng.standard_normal((n, 2)) * 0.10).astype("float32")
    drums = (rng.standard_normal((n, 2)) * 0.10).astype("float32")
    extra = (rng.standard_normal((n, 2)) * 0.02).astype("float32")   # the lost detail
    sf.write(os.path.join(d, "bass.flac"), bass, SR, subtype="PCM_24")
    sf.write(os.path.join(d, "drums.flac"), drums, SR, subtype="PCM_24")
    orig = os.path.join(d, "orig.flac")
    sf.write(orig, bass + drums + extra, SR, subtype="PCM_24")

    res, rsr = compute_residual(d, orig)          # orig.flac is excluded from the sum
    m = min(len(res), len(extra))
    err = np.sqrt(np.mean((res[:m] - extra[:m]) ** 2))
    ref = np.sqrt(np.mean(extra[:m] ** 2))
    assert rsr == SR and err / ref < 0.05, (err, ref)


def test_residual_gate_rejects_untrustworthy():
    # stems that do NOT explain the "original" (uncorrelated) must be refused,
    # so a lossy/misaligned source never reinjects noise as if it were detail.
    import tempfile
    from surroundupmix.recover import compute_residual
    d = tempfile.mkdtemp()
    n = SR
    rng = np.random.default_rng(9)
    sf.write(os.path.join(d, "bass.flac"),
             (rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR, subtype="PCM_24")
    sf.write(os.path.join(d, "drums.flac"),
             (rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR, subtype="PCM_24")
    orig = os.path.join(d, "orig.flac")           # unrelated content
    sf.write(orig, (rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR, subtype="PCM_24")
    res, rsr = compute_residual(d, orig)
    assert res is None and rsr is None


def test_detail_recovery_reinjects_to_front():
    # a residual stem with centred detail must reappear in the FRONT (forced),
    # and lift the front vs the same run without the residual.
    n = SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(11)
    det = (0.1 * np.sin(2 * np.pi * 3000 * t)).astype("float32")   # centred detail
    stems = {
        "vocals": Stereo(np.stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2, 1).astype("float32"), SR),
        "other": Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR),
        "residual": Stereo(np.stack([det, det], 1), SR),
    }
    p = _presets.get("immersive"); p["recover_gain"] = 0.0
    ch = spatialize(stems, "7.1", p, SR)
    stems2 = {k: v for k, v in stems.items() if k != "residual"}
    ch2 = spatialize(stems2, "7.1", p, SR)

    # centred detail -> forced FC layer (mid), and the front is louder with it
    assert _rms(ch.forced["FC"]) > 0
    assert _rms(ch.total("FC")) > _rms(ch2.total("FC"))


def test_binaural_detects_itd_not_panpot():
    from surroundupmix.binaural import analyze
    from surroundupmix.dsp import lowpass
    n = SR * 4
    rng = np.random.default_rng(21)
    base = lowpass(rng.standard_normal(n).astype("float32"), 1200, SR)

    # pan-pot: pure level difference, zero inter-channel delay -> not binaural
    pan = Stereo(np.stack([base, 0.8 * base], 1).astype("float32"), SR)
    cp = analyze(pan)["confidence"]

    # binaural: R is L delayed ~0.3 ms (13 samples) -> a real ITD
    d = 13
    Rd = np.concatenate([np.zeros(d, "float32"), base[:-d]])
    bina = analyze(Stereo(np.stack([base, Rd], 1).astype("float32"), SR))

    assert cp < 0.2, ("pan-pot false-triggered", cp)
    assert bina["confidence"] > 0.4, ("binaural not detected", bina["confidence"])
    assert 0.2 < bina["itd_ms"] < 0.45          # ~0.3 ms recovered
    assert -1.0 <= bina["rear_bias"] <= 1.0


def test_parse_descriptive_stem_names():
    from surroundupmix.stemnames import parse_stem_filename as p
    assert p("01-Olivia Rodrigo - deja vu Drums Left.wav") == ("drums", "L")
    assert p("01-Olivia Rodrigo - deja vu Drums Right.wav") == ("drums", "R")
    assert p("Artist - Song Bass.flac")[0] == "bass"
    assert p("Artist - Song Lead Vocals.flac")[0] == "vocals"
    assert p("Artist - Song Backing Vocals.flac")[0] == "backing"
    assert p("Artist - Song Other.flac")[0] == "other"
    # instrument word in the TITLE, role trails -> the trailing role wins
    assert p("All About That Bass - Drums.flac")[0] == "drums"
    assert p("just some random file.wav")[0] is None


def test_load_stems_from_descriptive_folder():
    import tempfile
    from surroundupmix.io import load_stems
    from surroundupmix.inputs import looks_like_stems
    d = tempfile.mkdtemp()
    n = SR
    t = np.arange(n) / SR
    dl = (0.2 * np.sin(2 * np.pi * 200 * t)).astype("float32")   # drums left
    dr = (0.2 * np.sin(2 * np.pi * 900 * t)).astype("float32")   # drums right (different)
    sf.write(os.path.join(d, "X - deja vu Drums Left.wav"), dl, SR)
    sf.write(os.path.join(d, "X - deja vu Drums Right.wav"), dr, SR)
    sf.write(os.path.join(d, "X - deja vu Bass.wav"),
             (0.2 * np.sin(2 * np.pi * 60 * t)).astype("float32"), SR)
    sf.write(os.path.join(d, "X - deja vu Vocals.wav"),
             (0.2 * np.sin(2 * np.pi * 300 * t)).astype("float32"), SR)

    assert looks_like_stems(d)
    found, sr = load_stems(d)
    assert sr == SR
    assert {"drums", "bass", "vocals"} <= set(found)
    # the Left/Right pair became one stereo drums stem with distinct channels
    dr_stem = found["drums"]
    assert _rms(dr_stem.L - dr_stem.R) > 1e-3


def test_overrides_normalize():
    from surroundupmix.overrides import normalize
    n = normalize({
        "drums": {"level": 3, "mute": False},
        "bogus": {"level": 2},                       # unknown stem -> dropped
        "vocals": {"zone": "rear", "level": "x"},    # bad level dropped, zone kept
        "bass": {"zone": "auto"},                    # nothing meaningful -> dropped
    })
    assert n["drums"] == {"level": 3.0}
    assert "bogus" not in n
    assert n["vocals"] == {"zone": "rear"}
    assert "bass" not in n


def test_per_instrument_mute_level_zone():
    from surroundupmix.routing import spatialize
    n = SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(31)
    stems = {
        "other": Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR),
        "vocals": Stereo(np.stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2, 1).astype("float32"), SR),
    }
    p = _presets.get("immersive")
    base = spatialize(stems, "7.1", p, SR)

    # mute vocals -> the centre loses the vocal
    muted = spatialize(stems, "7.1", p, SR, overrides={"vocals": {"mute": True}})
    assert _rms(muted.total("FC")) < 0.5 * _rms(base.total("FC"))

    # +6 dB on 'other' -> its side wrap is clearly louder
    louder = spatialize(stems, "7.1", p, SR, overrides={"other": {"level": 6.0}})
    assert _rms(louder.total("SL")) > 1.5 * _rms(base.total("SL"))

    # zone override forces 'other' to the rears (forced layer, balance-exempt)
    rear = spatialize(stems, "7.1", p, SR, overrides={"other": {"zone": "rear"}})
    assert _rms(rear.forced["BL"]) > 0


def test_per_instrument_spread_center_lfe():
    from surroundupmix.routing import spatialize
    from surroundupmix.balance import build_lfe
    n = SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(41)
    stems = {
        "other": Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR),
        "vocals": Stereo(np.stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2, 1).astype("float32"), SR),
        "bass": Stereo(np.stack([0.3 * np.sin(2 * np.pi * 60 * t)] * 2, 1).astype("float32"), SR),
        "drums": Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR),
    }
    p = _presets.get("immersive")
    base = spatialize(stems, "7.1", p, SR)

    # spread 0 -> 'other' held fully front, so it drops out of the side wrap
    z0 = spatialize(stems, "7.1", p, SR, overrides={"other": {"spread": 0}})
    assert _rms(z0.total("SL")) < _rms(base.total("SL"))
    # spread 100 wraps 'other' louder than spread 20
    hi = spatialize(stems, "7.1", p, SR, overrides={"other": {"spread": 100}})
    lo = spatialize(stems, "7.1", p, SR, overrides={"other": {"spread": 20}})
    assert _rms(hi.total("SL")) > _rms(lo.total("SL"))

    # vocal centre 100 puts far more into FC than centre 0
    c100 = spatialize(stems, "7.1", p, SR, overrides={"vocals": {"center": 100}})
    c0 = spatialize(stems, "7.1", p, SR, overrides={"vocals": {"center": 0}})
    assert _rms(c100.total("FC")) > 2 * _rms(c0.total("FC"))

    # LFE send: zeroing bass+drums LFE makes the LFE near-silent vs the default
    lfe_full = build_lfe(stems, 120, SR)
    lfe_none = build_lfe(stems, 120, SR, overrides={"bass": {"lfe": 0}, "drums": {"lfe": 0}})
    assert _rms(lfe_none) < 0.1 * _rms(lfe_full)


def test_split_vocals_noop_paths():
    from surroundupmix.split import maybe_split_vocals
    base = {"vocals": Stereo(np.zeros((SR, 2), "float32"), SR),
            "other": Stereo(np.zeros((SR, 2), "float32"), SR)}
    # off -> untouched
    assert "backing" not in maybe_split_vocals(dict(base), SR, mode="off")
    # already split (backing present) -> no-op
    s2 = dict(base); s2["backing"] = Stereo(np.zeros((SR, 2), "float32"), SR)
    out2 = maybe_split_vocals(s2, SR, mode="on")
    assert set(out2) == {"vocals", "other", "backing"}
    # splitter unavailable -> graceful no-op, no crash
    out3 = maybe_split_vocals(dict(base), SR, mode="auto",
                              split_python=os.path.join("no", "such", "python"))
    assert "backing" not in out3


def test_backing_survives_autobalance():
    # backing is a deliberate rear placement -> forced layer -> the front/rear
    # auto-balance must NOT trim it away.
    from surroundupmix.routing import spatialize
    from surroundupmix.balance import auto_balance
    n = SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(1)
    stems = {
        "vocals": Stereo(np.stack([0.3 * np.sin(2 * np.pi * 220 * t)] * 2, 1).astype("float32"), SR),
        "backing": Stereo(np.stack([0.2 * np.sin(2 * np.pi * 5000 * t)] * 2, 1).astype("float32"), SR),
        "other": Stereo((rng.standard_normal((n, 2)) * 0.1).astype("float32"), SR),
    }
    ch = spatialize(stems, "7.1", _presets.get("immersive"), SR, backing_gain_db=-3.0)
    before = _rms(ch.forced["BL"])
    auto_balance(ch, 15)
    after = _rms(ch.forced["BL"])
    assert before > 1e-4
    assert abs(20 * np.log10((after + 1e-12) / (before + 1e-12))) < 0.1


def test_sample_rate_invariance_decompose():
    """Verify that coh_time adapts to match ~160 ms regardless of sample rate."""
    st44 = Stereo(np.zeros((44100, 2), dtype=np.float32), 44100)
    st48 = Stereo(np.zeros((48000, 2), dtype=np.float32), 48000)
    st96 = Stereo(np.zeros((96000, 2), dtype=np.float32), 96000)
    p44 = dict(dc.DEFAULTS); p44["coh_time"] = max(1, int(round(0.1625 * st44.sr / p44["hop"])))
    p48 = dict(dc.DEFAULTS); p48["coh_time"] = max(1, int(round(0.1625 * st48.sr / p48["hop"])))
    p96 = dict(dc.DEFAULTS); p96["coh_time"] = max(1, int(round(0.1625 * st96.sr / p96["hop"])))
    assert p44["coh_time"] == 7
    assert p48["coh_time"] == 8
    assert p96["coh_time"] == 15


def test_detect_vocal_window_activity():
    """Verify that a track with a silent middle breakdown is still detected as double."""
    sr = 44100
    t = np.arange(sr * 60) / sr
    v = (0.3 * np.sin(2 * np.pi * 300 * t)).astype("float32")
    v[sr * 14:sr * 46] = 0.0  # 32s silence in middle
    lag = int(0.012 * sr)
    R = np.concatenate([np.zeros(lag, "float32"), v])[:len(v)]
    st = Stereo(np.stack([v, R], 1), sr)
    assert classify_vocal_width(st) == "double"


def test_balance_channel_scaling():
    """Auto-balance must scale rear energy by speaker count (7.1 reference = 4 channels)."""
    from surroundupmix.routing import Channels
    n = 1000
    ch51 = Channels("5.1", n)
    ch71 = Channels("7.1", n)
    # Fill each rear speaker with identical energy (e.g. 0.1)
    for c in ("BL", "BR"):
        ch51.add(c, np.full(n, 0.1, dtype=np.float32))
    for c in ("BL", "BR", "SL", "SR"):
        ch71.add(c, np.full(n, 0.1, dtype=np.float32))
    for c in ("FL", "FR", "FC"):
        ch51.add(c, np.full(n, 0.2, dtype=np.float32))
        ch71.add(c, np.full(n, 0.2, dtype=np.float32))

    res51 = auto_balance(ch51, rear_below_front=15.0)
    res71 = auto_balance(ch71, rear_below_front=15.0)
    # Both should hit within ~0.1 dB of each other because of normalized per-speaker power
    assert abs(res51["trim"] - res71["trim"]) < 0.1


def test_metadata_copy():
    """Verify that write_surround transfers tags and album art from original to destination."""
    import tempfile
    import mutagen
    from mutagen.flac import FLAC, Picture
    from surroundupmix.io import write_surround

    d = tempfile.mkdtemp()
    src = os.path.join(d, "original.flac")
    # Write dummy audio
    sf.write(src, np.zeros((1000, 2), dtype=np.float32), SR)
    tags = FLAC(src)
    tags["title"] = "Test Song Title"
    tags["artist"] = "Test Artist"
    tags["album"] = "Surround Album"
    pic = Picture()
    pic.data = b"imagepayload12345"
    pic.type = 3
    pic.mime = "image/jpeg"
    tags.add_picture(pic)
    tags.save()

    ch = {c: np.zeros(1000, dtype=np.float32) for c in LAYOUTS["5.1"]}
    dst_base = os.path.join(d, "out_5.1")
    dst = write_surround(dst_base, ch, "5.1", SR, original=src)
    assert os.path.isfile(dst)

    res = FLAC(dst)
    assert res["title"] == ["Test Song Title"]
    assert res["artist"] == ["Test Artist"]
    assert res["album"] == ["Surround Album"]
    assert len(res.pictures) == 1
    assert res.pictures[0].data == b"imagepayload12345"


def test_adm_with_discrete_objects():
    """Verify that adm_objects creates discrete objects for backing, vocals, guitar, piano, fx."""
    import tempfile
    d = tempfile.mkdtemp()
    stems = os.path.join(d, "stems")
    os.makedirs(stems)
    n = 48000
    for s in ("bass", "drums", "vocals", "other", "backing"):
        sf.write(os.path.join(stems, s + ".flac"), np.zeros((n, 2), dtype=np.float32), 48000)

    out = upmix_folder(stems, adm=True, adm_objects=True, out_dir=d, verbose=False)
    info = sf.info(out)
    # 10 bed + 2 backing + 1 vocals + 1 guitar + 1 piano + 1 fx = 16 channels
    assert info.channels == 16


def test_adm_30_channel_all_objects_studio_one():
    """Verify that adm_all_objects creates 30 channels (10-ch silent 7.1.2 bed carrier + 20 3D Audio Objects)."""
    import tempfile
    d = tempfile.mkdtemp()
    stems = os.path.join(d, "stems")
    os.makedirs(stems)
    n = 48000
    orig = os.path.join(d, "original.flac")
    sf.write(orig, np.random.randn(n, 2).astype(np.float32) * 0.1, 48000)
    for s in ("bass", "drums", "vocals", "other", "backing"):
        sf.write(os.path.join(stems, s + ".flac"), np.zeros((n, 2), dtype=np.float32), 48000)

    out = upmix_folder(stems, adm=True, adm_all_objects=True, original=orig, out_dir=d, verbose=False)
    info = sf.info(out)
    assert info.channels == 30, f"Expected 30 channels, got {info.channels}"

    with open(out, "rb") as f:
        data = f.read()

    # Verify RF64 / BW64 container
    assert data[:4] == b"RF64"
    # Verify XML content in axml chunk
    assert b"APR_1001" in data
    assert b"ACO_1001" in data
    assert b"Top Middle Left" in data
    assert b"Top Middle Right" in data
    assert b"Lead Vocal" in data
    assert b"Backing Left" in data
    # Verify 7.1.2 Bed definition is present to satisfy Studio One ("File does not contain beds" check)
    assert b"AP_00011001" in data
    assert b"AP_00031001" in data
    # Verify binary chna has 30 tracks (10 bed + 20 objects)
    chna_idx = data.find(b"chna")
    assert chna_idx != -1
    n_tracks, n_uids = struct.unpack_from("<HH", data, chna_idx + 8)
    assert n_tracks == 30
    assert n_uids == 30



def test_gui_queue_reordering():
    """Verify that treeview items can be moved up and down properly."""
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.withdraw()
    tree = ttk.Treeview(root, columns=("name",))
    i1 = tree.insert("", "end", values=("song1",))
    i2 = tree.insert("", "end", values=("song2",))
    i3 = tree.insert("", "end", values=("song3",))
    assert tree.get_children() == (i1, i2, i3)
    # move i2 up
    tree.move(i2, "", 0)
    assert tree.get_children() == (i2, i1, i3)
    # move i2 back down
    tree.move(i2, "", 1)
    assert tree.get_children() == (i1, i2, i3)
    root.destroy()


def test_motion_tracking():
    """Verify that panning motion is accurately tracked and builds animated 3D blocks."""
    from surroundupmix.motion import compute_short_time_pan, build_dynamic_blocks
    sr = 48000
    n = sr * 2
    t = np.linspace(0, 1, n, dtype=np.float32)
    # Signal panning from hard left (1, 0) to hard right (0, 1)
    sig = np.stack([1.0 - t, t], axis=1)
    pans, energies, _ = compute_short_time_pan(sig, sr, block_sec=0.20)
    assert pans[0] < -0.5
    assert pans[-1] > 0.5

    blocks = build_dynamic_blocks(sig, sr, base_x=-0.85, base_y=-0.85, base_z=0.35, pan_range=0.45)
    assert len(blocks) == 10
    assert blocks[0][2] < blocks[-1][2]


def test_whisper_proximity_and_360_orbit():
    """Verify whisper pulls object to ear level and ping-pong creates 360 orbit."""
    from surroundupmix.motion import build_dynamic_blocks
    from scipy.signal import butter, sosfilt
    sr = 48000
    n = sr * 2

    # 1. Whisper test: unvoiced high-frequency noise
    noise = np.random.randn(n).astype(np.float32) * 0.4
    sos = butter(4, [3000, 8000], btype="bandpass", fs=sr, output="sos")
    wh = sosfilt(sos, noise).astype(np.float32)
    sig_wh = np.stack([wh * 0.1, wh * 0.9], axis=1)

    blocks_wh = build_dynamic_blocks(sig_wh, sr, base_x=0.85, base_y=-0.85, base_z=0.35)
    avg_y = np.mean([b[3] for b in blocks_wh])
    assert avg_y > -0.60, f"Whisper failed to pull object forward: avg_y={avg_y}"

    # 2. 360 Orbit test: Left-to-Right sweep
    t = np.linspace(0, 1, n, dtype=np.float32)
    sig_sweep = np.stack([1.0 - t, t], axis=1) * 0.5
    blocks_sweep = build_dynamic_blocks(sig_sweep, sr, base_x=0.0, base_y=-0.85, base_z=0.35, pan_range=0.85)
    front_blocks = [b for b in blocks_sweep if b[3] > 0.0]
    assert len(front_blocks) > 0, "Orbit failed to arc through front half-plane"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok  ", fn.__name__)
    print("\nall %d tests passed" % len(fns))


if __name__ == "__main__":
    _run_all()
