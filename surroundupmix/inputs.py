"""Turn dropped/added paths into upmix jobs (pure logic, no GUI deps).

Used by the GUI's queue and its drag & drop. Each input is auto-classified:
a folder holding Demucs stems becomes one 'stems' job; any other folder is
scanned recursively for audio files (each a 'song' job); an audio file is a
'song' job. Output/work folders are skipped.
"""
import os

AUDIO_EXT = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
SKIP_DIRS = {"SurroundUpmix_work", "__pycache__", ".git"}


def _has_stem(d, stem):
    return any(os.path.isfile(os.path.join(d, stem + e)) for e in (".flac", ".wav"))


def looks_like_stems(d):
    """A folder that directly contains a vocals stem plus at least one more."""
    return _has_stem(d, "vocals") and (
        _has_stem(d, "bass") or _has_stem(d, "drums") or _has_stem(d, "other"))


def expand_inputs(paths):
    """Return a list of (path, kind) jobs; kind is 'song' or 'stems'.
    Order preserved, duplicates removed."""
    jobs = []
    seen = set()

    def add(p, kind):
        key = (os.path.normpath(p), kind)
        if key not in seen:
            seen.add(key)
            jobs.append((os.path.normpath(p), kind))

    for p in paths:
        if not p:
            continue
        if os.path.isfile(p):
            if os.path.splitext(p)[1].lower() in AUDIO_EXT:
                add(p, "song")
        elif os.path.isdir(p):
            if looks_like_stems(p):
                add(p, "stems")
                continue
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in sorted(dirs)
                           if d not in SKIP_DIRS and not d.startswith("Final_")
                           and not d.startswith(".")]
                if root != p and looks_like_stems(root):
                    add(root, "stems")
                    dirs[:] = []          # don't descend into a stems folder
                    continue
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                        add(os.path.join(root, f), "song")
    return jobs
