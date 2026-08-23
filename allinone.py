#!/usr/bin/env python3
"""allinone.py - full chain: a stereo song -> Demucs -> (karaoke split) -> surround.

    python allinone.py song.flac --format 7.1.2 --preset immersive --device cuda

Requires Demucs (`pip install demucs`) on the machine. The optional lead/backing
karaoke split uses split_vocals.py (audio-separator); point --split-python at the
interpreter that has it (e.g. the isolated splitter venv on Windows).
"""
import argparse
import os
import shutil
import subprocess
import sys

from surroundupmix.engine import upmix_folder
from surroundupmix.presets import PRESETS, DEFAULT_PRESET

HERE = os.path.dirname(os.path.abspath(__file__))
PLACE_STEMS = ("vocals", "bass", "drums", "other", "guitar", "piano")
PLACE_ZONES = ["auto", "front", "side", "rear"]


def run(cmd, **kw):
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, **kw)


def resolve_demucs(override):
    if override:
        return override.split()
    if shutil.which("demucs"):
        return ["demucs"]
    for cand in (["py", "-3.10", "-m", "demucs"], ["python", "-m", "demucs"],
                 [sys.executable, "-m", "demucs"]):
        try:
            r = subprocess.run(cand + ["--help"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return cand
        except Exception:
            pass
    return None


def find_stems_dir(sep_root, model, track):
    cand = os.path.join(sep_root, model, track)
    if os.path.isfile(os.path.join(cand, "vocals.flac")):
        return cand
    model_dir = os.path.join(sep_root, model)
    if os.path.isdir(model_dir):
        subs = [d for d in os.listdir(model_dir)
                if os.path.isdir(os.path.join(model_dir, d))]
        if len(subs) == 1:
            return os.path.join(model_dir, subs[0])
    return cand


def default_split_python():
    """The isolated splitter venv (has audio-separator), if present next to us."""
    for rel in (["bin", "splitter_venv", "Scripts", "python.exe"],
                ["bin", "splitter_venv", "bin", "python"]):
        cand = os.path.join(HERE, *rel)
        if os.path.isfile(cand):
            return cand
    return None


def do_split(split_python, vocals_flac, work):
    split_script = os.path.join(HERE, "split_vocals.py")
    if not os.path.isfile(split_script):
        return None
    py = split_python or default_split_python() or sys.executable
    out = os.path.join(work, "vocalsplit")
    models = os.path.join(HERE, "bin", "splitter_models")
    r = run([py, split_script, vocals_flac, out, models])
    lead = os.path.join(out, "lead.flac")
    backing = os.path.join(out, "backing.flac")
    if r.returncode == 0 and os.path.isfile(lead) and os.path.isfile(backing):
        return lead, backing
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Song -> Demucs -> surround upmix.")
    ap.add_argument("song")
    ap.add_argument("-f", "--format", default="5.1", choices=["5.1", "7.1", "7.1.2"])
    ap.add_argument("-p", "--preset", default=DEFAULT_PRESET, choices=list(PRESETS))
    ap.add_argument("--model", default="htdemucs_ft")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--split-vocals", default="auto",
                    choices=["auto", "on", "off"])
    ap.add_argument("--split-python", default=None,
                    help="interpreter with audio-separator (the splitter venv)")
    ap.add_argument("--demucs-cmd", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--rear-gain", type=float, default=0.0)
    ap.add_argument("--rear-below-front", type=float, default=None)
    ap.add_argument("--vocal-mode", default="auto",
                    choices=["auto", "spread", "forward"])
    ap.add_argument("--backing-gain", default="auto")
    ap.add_argument("--keep-stems", action="store_true")
    ap.add_argument("--wav", action="store_true")
    ap.add_argument("--adm", action="store_true",
                    help="write a Dolby-Atmos ADM BWF master (7.1.2 bed, 48 kHz) "
                         "for the Dolby Atmos Renderer instead of FLAC/WAV")
    for stem in PLACE_STEMS:
        ap.add_argument("--place-%s" % stem, default="auto", choices=PLACE_ZONES,
                        help="force %s to a zone (auto = song-adaptive)" % stem)
    args = ap.parse_args(argv)
    place = {s: getattr(args, "place_" + s) for s in PLACE_STEMS}

    song = os.path.abspath(args.song)
    if not os.path.isfile(song):
        print("ERROR: song not found:", song, file=sys.stderr)
        return 1
    track = os.path.splitext(os.path.basename(song))[0]
    song_dir = os.path.dirname(song)
    work = args.work_dir or os.path.join(song_dir, "SurroundUpmix_work")
    sep = os.path.join(work, "stems")
    os.makedirs(sep, exist_ok=True)
    out_dir = args.out_dir or os.path.join(
        song_dir, "Final_Atmos" if args.adm else "Final_%s" % args.format)

    demucs = resolve_demucs(args.demucs_cmd)
    if not demucs:
        print("ERROR: Demucs not found. Install with: pip install -U demucs",
              file=sys.stderr)
        return 1

    print("==== Separating stems (%s, device=%s) ====" % (args.model, args.device))
    dargs = demucs + ["-n", args.model, "--flac", "-o", sep]
    if args.device != "auto":
        dargs += ["-d", args.device]
    dargs += [song]
    if run(dargs).returncode != 0:
        print("ERROR: Demucs failed.", file=sys.stderr)
        return 1

    stems_dir = find_stems_dir(sep, args.model, track)
    vocals = os.path.join(stems_dir, "vocals.flac")
    if not os.path.isfile(vocals):
        print("ERROR: could not find separated stems under", sep, file=sys.stderr)
        return 1

    # lead/backing karaoke split
    if args.split_vocals != "off":
        print("==== Splitting vocal into lead + backing ====")
        res = do_split(args.split_python, vocals, work)
        if res:
            lead, backing = res
            shutil.copyfile(vocals, os.path.join(stems_dir, "vocals_full.flac"))
            shutil.copyfile(lead, vocals)                       # lead -> vocals stem
            shutil.copyfile(backing, os.path.join(stems_dir, "backing.flac"))
            print("  lead -> vocals (front),  backing -> surround wrap")
        elif args.split_vocals == "on":
            print("  (split requested but unavailable - continuing without it)")

    print("==== Upmixing to %s ====" % ("Atmos ADM BWF (7.1.2 bed)" if args.adm
                                        else args.format))
    out = upmix_folder(
        stems_dir, fmt=args.format, preset=args.preset, out_dir=out_dir,
        track_label=track, rear_gain=args.rear_gain,
        rear_below_front=args.rear_below_front, vocal_mode=args.vocal_mode,
        backing_gain=args.backing_gain, force_wav=args.wav, place=place,
        adm=args.adm)

    if not args.keep_stems:
        shutil.rmtree(work, ignore_errors=True)
        print("  cleaned up", work)
    print("==== Done ->", out, "====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
