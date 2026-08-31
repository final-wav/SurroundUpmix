"""Audio I/O: load Demucs stems, write multichannel surround files.

FLAC for <=8 channels (5.1 / 7.1 - the FLAC channel order matches WAVE for
those counts), and a hand-written 24-bit WAVEFORMATEXTENSIBLE for 7.1.2 so
the ten channels carry a correct speaker mask and players route them right.
"""
import os
import struct
import numpy as np
import soundfile as sf

from .layouts import LAYOUTS, channel_mask


class Stereo:
    """A stereo (or mono-as-stereo) signal, float32, shape (n, 2)."""

    def __init__(self, data, sr):
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 1:
            data = np.stack([data, data], axis=1)
        elif data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        self.data = data
        self.sr = sr

    @property
    def L(self):
        return self.data[:, 0]

    @property
    def R(self):
        return self.data[:, 1]

    def __len__(self):
        return self.data.shape[0]


# stem names Demucs may produce (4-stem and 6-stem models), plus our split extras
# and the recovered separation residual (original - sum of stems)
STEM_NAMES = ("bass", "drums", "vocals", "other", "guitar", "piano",
              "backing", "vocals_full", "residual")


def find_stem(folder, name):
    for ext in ("flac", "wav"):
        p = os.path.join(folder, name + "." + ext)
        if os.path.isfile(p):
            return p
    return None


def load(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return Stereo(data, sr)


def load_stems(folder):
    """Return {name: Stereo} for every stem present, plus the common sr.
    Raises if stems disagree on sample rate. Lengths are padded to the max.
    """
    found = {}
    sr = None
    for name in STEM_NAMES:
        p = find_stem(folder, name)
        if not p:
            continue
        s = load(p)
        if sr is None:
            sr = s.sr
        elif s.sr != sr:
            raise ValueError(
                "stem '%s' is %d Hz but others are %d Hz" % (name, s.sr, sr))
        found[name] = s
    if not found:
        raise ValueError("no stems found in %r (need bass/drums/vocals/other)" % folder)
    n = max(len(s) for s in found.values())
    for name, s in found.items():
        if len(s) < n:
            pad = np.zeros((n - len(s), 2), dtype=np.float32)
            found[name] = Stereo(np.concatenate([s.data, pad], axis=0), sr)
    return found, sr


def _float_to_pcm24_bytes(x):
    """Interleaved float array (n, ch) in [-1, 1] -> packed 24-bit LE bytes."""
    x = np.clip(x, -1.0, 1.0)
    ints = np.round(x * 8388607.0).astype(np.int32)          # 2**23 - 1
    ints = ints.reshape(-1)                                   # interleaved
    # take the low 3 bytes of each little-endian int32
    b = ints.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes()


def _write_wav_extensible(path, interleaved, sr, mask, bits=24):
    """Write a 24-bit PCM WAVE_FORMAT_EXTENSIBLE file with an explicit
    speaker channel mask. interleaved: (n, ch) float32 in [-1, 1]."""
    n, ch = interleaved.shape
    block_align = ch * bits // 8
    byte_rate = sr * block_align
    data_bytes = _float_to_pcm24_bytes(interleaved)
    data_size = len(data_bytes)
    # WAVEFORMATEXTENSIBLE: cbSize=22, valid bits, channel mask, subformat GUID
    KSDATAFORMAT_SUBTYPE_PCM = (
        b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    fmt_chunk = struct.pack(
        "<HHIIHH", 0xFFFE, ch, sr, byte_rate, block_align, bits)
    fmt_ext = struct.pack("<HHI", 22, bits, mask) + KSDATAFORMAT_SUBTYPE_PCM
    fmt_body = fmt_chunk + fmt_ext
    # pad data chunk to even size
    pad = b"\x00" if (data_size % 2) else b""
    riff_size = 4 + (8 + len(fmt_body)) + (8 + data_size + len(pad))
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", riff_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", len(fmt_body)))
        f.write(fmt_body)
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(data_bytes)
        f.write(pad)


def write_surround(path_no_ext, channels, fmt, sr, bits=24, force_wav=False):
    """Write the surround file. `channels` is {name: mono np.array}; missing
    channels are written silent. Returns the actual output path.

    <=8 channels -> FLAC (unless force_wav); 7.1.2 -> WAV extensible.
    """
    order = LAYOUTS[fmt]
    n = max((len(v) for v in channels.values() if v is not None), default=0)
    cols = []
    for ch in order:
        v = channels.get(ch)
        if v is None or len(v) == 0:
            v = np.zeros(n, dtype=np.float32)
        elif len(v) < n:
            v = np.concatenate([v, np.zeros(n - len(v), dtype=np.float32)])
        cols.append(v[:n])
    interleaved = np.stack(cols, axis=1).astype(np.float32)

    use_wav = force_wav or len(order) > 8
    if use_wav:
        path = path_no_ext + ".wav"
        _write_wav_extensible(path, interleaved, sr, channel_mask(fmt), bits)
    else:
        path = path_no_ext + ".flac"
        subtype = "PCM_24" if bits == 24 else "PCM_16"
        sf.write(path, interleaved, sr, subtype=subtype)
    return path
