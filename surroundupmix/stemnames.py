"""Recognise stems from descriptive filenames.

Demucs writes tidy names (bass.flac, drums.flac, ...), but stems from a DAW or
another separator often look like

    01-Olivia Rodrigo - deja vu Drums Left.wav
    01-Olivia Rodrigo - deja vu Drums Right.wav
    01-Olivia Rodrigo - deja vu Bass.wav

so we pull the instrument word out of the name and map it to our canonical stem
(bass / drums / vocals / other / guitar / piano, plus backing). Left/Right mono
files for the same instrument are paired into one stereo stem.

Two safeguards against a song TITLE that happens to contain an instrument word:
the role word almost always trails the title, so among competing matches the
RIGHTMOST wins; and 'backing vocals' is resolved to backing before a plain
'vocals' match can grab it.
"""
import os
import re

# stem files may be lossy too; the instrument-word requirement (below) keeps a
# folder of ordinary songs from being mistaken for stems
AUDIO_EXTS = (".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma")

# canonical -> keyword patterns (word-boundary). Order only matters for 'backing',
# which is checked first so "backing vocals" doesn't read as "vocals".
_KW = {
    "vocals": [r"lead\s*vocals?", r"\bvocals?\b", r"\bvox\b", r"\bvoice\b"],
    "drums":  [r"\bdrums?\b", r"\bpercussion\b", r"\bperc\b"],
    "bass":   [r"bass\s*guitar", r"\bbassline\b", r"\bbass\b"],
    "guitar": [r"\bguitars?\b", r"\bgtr\b"],
    "piano":  [r"\bpiano\b", r"\bkeys?\b", r"\bkeyboard\b"],
    "other":  [r"\bother\b", r"\binstrumental\b", r"\binstruments?\b", r"\bsynths?\b"],
}
_BACKING = [r"backing\s*vocals?", r"\bbgv\b", r"\bback\s*vox\b", r"\bharmon(?:y|ies)\b"]


def parse_stem_filename(fname):
    """Return (canonical | None, channel 'L'/'R'/None) for one filename."""
    s = os.path.splitext(os.path.basename(fname))[0].lower()
    s = s.replace("_", " ").replace("-", " ")

    channel = None
    if re.search(r"\bright\b", s) or re.search(r"\br\s*$", s):
        channel = "R"
    elif re.search(r"\bleft\b", s) or re.search(r"\bl\s*$", s):
        channel = "L"

    if any(re.search(p, s) for p in _BACKING):
        return "backing", channel

    best = None  # (start_index, canonical)
    for canon, pats in _KW.items():
        for pat in pats:
            for m in re.finditer(pat, s):
                if best is None or m.start() > best[0]:
                    best = (m.start(), canon)
    return (best[1] if best else None), channel


def folder_stem_map(folder):
    """{canonical: [(channel, path), ...]} for the audio files in `folder`
    whose names name an instrument. Empty if nothing matches."""
    out = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for f in names:
        if os.path.splitext(f)[1].lower() not in AUDIO_EXTS:
            continue
        canon, chan = parse_stem_filename(f)
        if canon:
            out.setdefault(canon, []).append((chan, os.path.join(folder, f)))
    return out
