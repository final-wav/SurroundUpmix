"""Lead/backing vocal split for the stems path.

The full chain (allinone.py) splits the vocal into lead + backing on disk before
the upmix. When you bring your OWN stems, that hasn't happened - so this runs the
same Roformer karaoke split in memory on the loaded vocals stem (whatever it came
from: one file, or a Left/Right pair already combined by load_stems), and injects
lead -> vocals, the rest -> backing, keeping the full vocal as the blend bed.

It shells out to split_vocals.py using the isolated splitter venv (audio-separator
lives there). If the splitter isn't installed it's a graceful no-op.
"""
import os
import shutil
import subprocess
import tempfile

import soundfile as sf

from .io import Stereo, load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_split_python():
    """The isolated splitter venv python (has audio-separator), if present."""
    for rel in (["bin", "splitter_venv", "Scripts", "python.exe"],
                ["bin", "splitter_venv", "bin", "python"]):
        cand = os.path.join(ROOT, *rel)
        if os.path.isfile(cand):
            return cand
    return None


def maybe_split_vocals(stems, sr, mode="off", split_python=None, log=lambda m: None):
    """Split stems['vocals'] into lead + backing in place. mode auto|on|off.
    No-op if off, if there's no vocals stem, or if it's already been split
    (a 'backing' stem is present). Returns the (possibly updated) stems dict."""
    if mode == "off" or "vocals" not in stems or "backing" in stems:
        return stems
    script = os.path.join(ROOT, "split_vocals.py")
    py = split_python or default_split_python()
    if not py or not os.path.isfile(script):
        if mode == "on":
            log("  (split-vocals on, but the Roformer splitter isn't installed - skipped)")
        return stems
    tmp = tempfile.mkdtemp(prefix="su_split_")
    try:
        vpath = os.path.join(tmp, "vocals.wav")
        sf.write(vpath, stems["vocals"].data, sr, subtype="PCM_24")
        out = os.path.join(tmp, "out")
        models = os.path.join(ROOT, "bin", "splitter_models")
        log("  splitting vocals into lead + backing (Roformer karaoke)")
        r = subprocess.run([py, script, vpath, out, models])
        lead = os.path.join(out, "lead.flac")
        backing = os.path.join(out, "backing.flac")
        if r.returncode == 0 and os.path.isfile(lead) and os.path.isfile(backing):
            stems["vocals_full"] = stems["vocals"]   # keep the full vocal (blend bed)
            stems["vocals"] = load(lead)             # lead -> front
            stems["backing"] = load(backing)         # backing -> surround wrap
            log("  lead -> vocals (front), backing -> surround wrap")
        elif mode == "on":
            log("  (vocal split failed - continuing with the full vocal)")
    except Exception as e:
        if mode == "on":
            log("  (vocal split error: %s - continuing with the full vocal)" % e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return stems
