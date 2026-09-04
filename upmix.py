#!/usr/bin/env python3
"""upmix.py - upmix a folder of Demucs stems to a surround file.

    python upmix.py <stems-folder> [--format 5.1|7.1|7.1.2] [--preset ...]

The stems folder holds bass/drums/vocals/other (+ optional guitar/piano, and
backing/vocals_full from a karaoke split) as .flac or .wav.
"""
import argparse
import sys

from surroundupmix.engine import upmix_folder
from surroundupmix.presets import PRESETS, DEFAULT_PRESET


def main(argv=None):
    ap = argparse.ArgumentParser(description="Upmix Demucs stems to surround.")
    ap.add_argument("stems_folder")
    ap.add_argument("-f", "--format", default="5.1",
                    choices=["5.1", "7.1", "7.1.2"])
    ap.add_argument("-p", "--preset", default=DEFAULT_PRESET,
                    choices=list(PRESETS))
    ap.add_argument("-o", "--out-dir", default=None)
    ap.add_argument("--track-label", default=None)
    ap.add_argument("--rear-gain", type=float, default=0.0,
                    help="taste offset (dB) on the whole rear field")
    ap.add_argument("--rear-below-front", type=float, default=None,
                    help="override the preset's rear-under-front target (dB)")
    ap.add_argument("--vocal-mode", default="auto",
                    choices=["auto", "spread", "forward"])
    ap.add_argument("--vocal-roles", default="auto",
                    choices=["auto", "keep", "swap"],
                    help="check the karaoke split from the signal: auto swaps a "
                         "mislabelled lead/backing (or skips the split for a wet "
                         "wash); keep trusts the model; swap forces a swap")
    ap.add_argument("--backing-gain", default="auto",
                    help="'auto' or a dB number for split-out backing vocals")
    ap.add_argument("--lfe-cross", type=int, default=None)
    ap.add_argument("--norm-level", type=float, default=-1.0)
    ap.add_argument("--wav", action="store_true",
                    help="force WAV output even for <=8 channels")
    ap.add_argument("--adm", action="store_true",
                    help="write a Dolby-Atmos ADM BWF master (7.1.2 bed, 48 kHz) "
                         "for the Dolby Atmos Renderer instead of FLAC/WAV")
    ap.add_argument("--adm-order", default="playback",
                    choices=["playback", "renderer"],
                    help="ADM bed order: playback = rears at 5/6 (correct on the "
                         "speaker rig); renderer = Dolby order, sides at 5/6 "
                         "(correct when imported into the Dolby Atmos Renderer)")
    ap.add_argument("--adm-objects", action="store_true",
                    help="export split-out backing vocals as discrete 3D Atmos objects "
                         "instead of folding them into the 7.1.2 bed")
    ap.add_argument("--all-objects", action="store_true",
                    help="write modern 20-channel All-Objects Dolby Atmos master "
                         "(14 speaker anchors + 6 dynamic moving 3D objects, 0 bed channels)")
    ap.add_argument("--original", default=None,
                    help="path to the original master; HF air restore uses it to "
                         "reinject the highs the separator lost (always applied when given)")
    ap.add_argument("--recover-detail", default="on", choices=["on", "off"],
                    help="use a residual.flac (original - sum of stems) in the "
                         "folder to reinject the detail Demucs lost; off ignores it")
    ap.add_argument("--binaural", type=int, default=0, metavar="0-100",
                    help="front/back depth steer for BINAURAL material (0-100%%). "
                         "Gated by a measured binaural confidence, so a normal "
                         "pan-pot song is untouched even at 100. 0 = off")
    ap.add_argument("--decorrelate", default="on", choices=["on", "off"],
                    help="phase-safe decorrelation of the backs/heights from the "
                         "sides, so the rear field envelops instead of collapsing "
                         "onto the sides (7.1/7.1.2 only). off = the previous behaviour")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--overrides", default=None, metavar="FILE.json",
                    help="per-instrument settings (zone/level/mute/...) as JSON; "
                         "sits on top of the preset. See surroundupmix/overrides.py")
    ap.add_argument("--split-vocals", default="off", choices=["auto", "on", "off"],
                    help="split the vocals stem into lead + backing (Roformer "
                         "karaoke) before upmixing; auto/on need the splitter venv")
    ap.add_argument("--split-python", default=None,
                    help="interpreter with audio-separator (the splitter venv); "
                         "auto-detected from bin/splitter_venv if omitted")
    _place_stems = ("vocals", "bass", "drums", "other", "guitar", "piano")
    for stem in _place_stems:
        ap.add_argument("--place-%s" % stem, default="auto",
                        choices=["auto", "front", "side", "rear"],
                        help="force %s to a zone (auto = song-adaptive)" % stem)
    args = ap.parse_args(argv)
    place = {s: getattr(args, "place_" + s) for s in _place_stems}
    overrides = None
    if args.overrides:
        from surroundupmix.overrides import load as _load_ov
        overrides = _load_ov(args.overrides)

    try:
        out = upmix_folder(
            args.stems_folder, fmt=args.format, preset=args.preset,
            out_dir=args.out_dir, track_label=args.track_label, overrides=overrides,
            split_vocals=args.split_vocals, split_python=args.split_python,
            rear_gain=args.rear_gain, rear_below_front=args.rear_below_front,
            vocal_mode=args.vocal_mode, backing_gain=args.backing_gain,
            lfe_cross=args.lfe_cross, norm_level=args.norm_level,
            force_wav=args.wav, place=place, adm=args.adm,
            adm_order=args.adm_order, adm_objects=args.adm_objects,
            adm_all_objects=args.all_objects,
            original=args.original,
            decorrelate=(args.decorrelate == "on"),
            vocal_roles=args.vocal_roles,
            recover_detail=(args.recover_detail == "on"),
            binaural_amount=max(0.0, min(1.0, args.binaural / 100.0)),
            verbose=not args.quiet)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
