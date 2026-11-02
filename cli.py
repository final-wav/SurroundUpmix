#!/usr/bin/env python3
"""
cli.py - Headless Command-Line Interface for SurroundUpmix v2.0.
Supports 30-channel Dolby Atmos ADM BWF, E-AC-3 JOC (.m4a via DEE), 7.1.2, and 5.1 surround.
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pipeline import AtmosPipeline
from binaural_matrix import PRESET_MATRICES
import config

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aif", ".aiff"}


class TerminalColors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def print_banner():
    print(f"{TerminalColors.CYAN}{TerminalColors.BOLD}")
    print("=" * 68)
    print("   SURROUND UPMIX v2.0 - COMMAND LINE PRODUCTION INTERFACE")
    print("   Standard DSP Core | 30-Channel ADM BWF | Dolby Atmos E-AC-3 JOC")
    print("=" * 68)
    print(f"{TerminalColors.RESET}")


def progress_display(stage: str, percent: float, msg: str):
    bar_len = 26
    filled = int(round(bar_len * max(0.0, min(100.0, percent)) / 100))
    bar = "=" * filled + "-" * (bar_len - filled)
    color = TerminalColors.CYAN if percent < 90 else TerminalColors.GREEN
    sys.stdout.write(f"\r  [{color}{bar}{TerminalColors.RESET}] {percent:5.1f}% | {TerminalColors.BOLD}{stage:<12}{TerminalColors.RESET} | {msg[:42]:<42}")
    sys.stdout.flush()
    if percent >= 100.0:
        sys.stdout.write("\n")


def collect_audio_files(inputs, batch=False, force_multitrack=False):
    from surroundupmix.stemnames import folder_stem_map
    files = []
    for inp in inputs:
        p = Path(inp).resolve()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(p)
        elif p.is_dir():
            stem_map = folder_stem_map(str(p))
            if stem_map or force_multitrack:
                files.append(p)
            elif batch:
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(f)
                    elif f.is_dir() and folder_stem_map(str(f)):
                        files.append(f)
            else:
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(f)
    return files


def main():
    parser = argparse.ArgumentParser(description="Headless SurroundUpmix v2.0 CLI Production Converter")
    parser.add_argument("inputs", nargs="*", help="Audio file(s) or multitrack directory to process")
    parser.add_argument("--batch", action="store_true", help="Batch process directory")
    parser.add_argument("--multitrack", action="store_true", help="Treat input directory as a multitrack stem folder")
    parser.add_argument("-o", "--output-dir", default=None, help="Custom output directory")

    # Format selection (matches Weg B)
    parser.add_argument("--format", default=None, help="Output format(s): 'adm', 'ec3', '7.1.2', '5.1', or comma-separated list (e.g. 'adm,ec3')")
    parser.add_argument("--adm", action="store_true", help="Export 30-channel Dolby Atmos ADM BWF Master (.wav)")
    parser.add_argument("--ec3", action="store_true", help="Export Dolby Atmos E-AC-3 JOC (.m4a bitstream via DEE)")
    parser.add_argument("--712", dest="f_712", action="store_true", help="Export 7.1.2 Discrete Multichannel (.wav)")
    parser.add_argument("--51", dest="f_51", action="store_true", help="Export 5.1 Standard Surround Sound (.wav)")

    # Spatial & Acoustics
    parser.add_argument("-p", "--preset", default="immersive", choices=["focus", "immersive", "concert", "envelop"], help="Spatial upmix preset (default: immersive)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="Compute device for neural separation (default: auto)")
    parser.add_argument("--objects", action="store_true", default=True, help="Enable dynamic 3D audio object tracking")
    parser.add_argument("--no-objects", dest="objects", action="store_false", help="Disable dynamic 3D motion objects")
    parser.add_argument("--motion-mode", default="dynamic", choices=["subtle", "dynamic", "expressive"], help="3D motion behavior (default: dynamic)")
    parser.add_argument("--motion-intensity", type=float, default=1.0, help="3D object motion depth multiplier (default: 1.0)")
    parser.add_argument("--vocal-mode", default="auto", choices=["auto", "spread", "forward"], help="Vocal placement style")
    parser.add_argument("--split-vocals", default="auto", choices=["auto", "on", "off"], help="Vocal separation (Lead vs Backing)")
    parser.add_argument("--no-split-drums", action="store_true", help="Disable sub-drum splitting into kick, snare, cymbals, toms")
    parser.add_argument("-m", "--model", default="htdemucs_6s", choices=["htdemucs_6s", "htdemucs_ft"], help="Stem separation model (default: htdemucs_6s)")
    parser.add_argument("--decorrelate", default="on", choices=["on", "off"], help="Phase-safe surround decorrelation")
    parser.add_argument("--recover-detail", default="on", choices=["on", "off"], help="Demucs residual detail restore")
    parser.add_argument("--binaural", type=int, default=0, help="Binaural depth steer (0-100, default: 0)")
    parser.add_argument("--binaural-preset", default="punchy_direct", choices=list(PRESET_MATRICES.keys()), help="Binaural HRTF Matrix preset")
    parser.add_argument("--binaural-matrix", default=None, help="Custom JSON string overriding individual objects")

    # External Tools & Encoding
    parser.add_argument("-b", "--bitrate", default="768", choices=["384", "448", "576", "640", "768"], help="Atmos E-AC-3 JOC bit rate (default: 768 kbps)")
    parser.add_argument("-d", "--drc", default="music_light", choices=["music_light", "music_standard", "none"], help="Dynamic Range Compression profile")
    parser.add_argument("--dee-path", default=None, help="Path to Dolby Encoding Engine (dee.exe)")
    parser.add_argument("--vst3-path", default=None, help="Path to custom 64-bit spatial VST3 plugin")

    # Metadata Overrides
    parser.add_argument("--artist", default=None, help="Override Artist metadata")
    parser.add_argument("--album", default=None, help="Override Album metadata")
    parser.add_argument("--title", default=None, help="Override Track title")
    parser.add_argument("--track", type=int, default=None, help="Override Track number")
    parser.add_argument("--cover", default=None, help="Custom album cover image file")

    args = parser.parse_args()
    print_banner()

    if not args.inputs:
        print(f"{TerminalColors.YELLOW}No input files provided.{TerminalColors.RESET}")
        print("Usage: python cli.py <song.flac> [--format adm,ec3] [--preset immersive]")
        return 1

    files = collect_audio_files(args.inputs, batch=args.batch, force_multitrack=args.multitrack)
    if not files:
        print(f"{TerminalColors.RED}No valid audio files found in inputs.{TerminalColors.RESET}")
        return 1

    # Configure external tools
    if args.dee_path:
        config.set_config_value("dee_path", args.dee_path)
    if args.vst3_path:
        config.set_config_value("custom_vst3_path", args.vst3_path)

    dee_avail = config.is_dee_available()

    # Determine Deliverable Formats (Weg B)
    exp_adm = False
    exp_ec3 = False
    exp_712 = False
    exp_51 = False

    if args.format:
        fmts = [f.strip().lower() for f in args.format.split(",")]
        if "adm" in fmts or "all" in fmts: exp_adm = True
        if "ec3" in fmts or "eac3" in fmts or "all" in fmts: exp_ec3 = True
        if "7.1.2" in fmts or "712" in fmts or "all" in fmts: exp_712 = True
        if "5.1" in fmts or "51" in fmts or "all" in fmts: exp_51 = True
    else:
        # Check individual boolean flags
        if args.adm: exp_adm = True
        if args.ec3: exp_ec3 = True
        if args.f_712: exp_712 = True
        if args.f_51: exp_51 = True

    # Default deliverable resolution if nothing specified
    if not (exp_adm or exp_ec3 or exp_712 or exp_51):
        exp_adm = True
        if dee_avail:
            exp_ec3 = True

    if exp_ec3 and not dee_avail:
        print(f"{TerminalColors.YELLOW}Warning: E-AC-3 JOC selected but DEE (dee.exe) was not found. Falling back to ADM BWF Master.{TerminalColors.RESET}")
        exp_ec3 = False
        exp_adm = True

    # Resolve Binaural Matrix
    binaural_config = args.binaural_preset
    if args.binaural_matrix:
        try:
            binaural_config = json.loads(args.binaural_matrix)
        except Exception as e:
            print(f"{TerminalColors.YELLOW}Warning: Invalid JSON in --binaural-matrix: {e}. Using {args.binaural_preset}.{TerminalColors.RESET}")

    # Set compute device
    if args.device != "auto":
        os.environ["CUDA_VISIBLE_DEVICES"] = "0" if args.device == "cuda" else ""

    deliv_names = []
    if exp_ec3: deliv_names.append("Dolby Atmos E-AC-3 JOC (.m4a)")
    if exp_adm: deliv_names.append("30-Ch ADM BWF (.wav Master)")
    if exp_712: deliv_names.append("7.1.2 Discrete (.wav)")
    if exp_51: deliv_names.append("5.1 Surround (.wav)")

    out_dest = args.output_dir or "Output/ (relative to source)"
    print(f"Deliverables: {TerminalColors.GREEN}{', '.join(deliv_names)}{TerminalColors.RESET}")
    print(f"Preset: {TerminalColors.CYAN}{args.preset}{TerminalColors.RESET} | Motion Objects: {TerminalColors.MAGENTA}{'Enabled' if args.objects else 'Disabled'}{TerminalColors.RESET}")
    print(f"Queue: {TerminalColors.BOLD}{len(files)}{TerminalColors.RESET} item(s) to process.")
    print(f"Output Destination: {TerminalColors.GREEN}{out_dest}{TerminalColors.RESET}\n")

    pipeline = AtmosPipeline()
    success = 0
    t0_all = time.time()

    for idx, f in enumerate(files, 1):
        print(f"{TerminalColors.MAGENTA}{TerminalColors.BOLD}[{idx}/{len(files)}] Processing:{TerminalColors.RESET} {f.name}")
        custom_meta = {}
        if args.artist: custom_meta["artist"] = args.artist
        if args.album: custom_meta["album"] = args.album
        if args.title: custom_meta["title"] = args.title
        if args.track: custom_meta["track"] = args.track

        try:
            out_file = pipeline.run(
                input_file=f,
                preset=args.preset,
                motion_mode=args.motion_mode if args.objects else "subtle",
                motion_intensity=args.motion_intensity if args.objects else 0.0,
                vocal_mode=args.vocal_mode,
                split_vocals=args.split_vocals,
                split_drums=(not args.no_split_drums),
                separation_model=args.model,
                decorrelate=(args.decorrelate == "on"),
                recover_detail=(args.recover_detail == "on"),
                binaural=args.binaural,
                binaural_matrix=binaural_config,
                bitrate=args.bitrate,
                drc=args.drc,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                custom_metadata=custom_meta,
                custom_cover=Path(args.cover) if args.cover else None,
                export_adm=exp_adm,
                export_ec3=exp_ec3,
                export_712=exp_712,
                export_51=exp_51,
                dee_path=(args.dee_path or config.get_config_value("dee_path", None)),
                use_nugen=bool(args.vst3_path or config.get_config_value("custom_vst3_path", None)),
                progress_cb=progress_display
            )
            success += 1
            print(f"  {TerminalColors.GREEN}✔ Successfully completed:{TerminalColors.RESET} {out_file}\n")
        except Exception as e:
            print(f"\n  {TerminalColors.RED}✘ Error processing {f.name}: {e}{TerminalColors.RESET}\n")

    total_time = time.time() - t0_all
    print(f"{TerminalColors.CYAN}{TerminalColors.BOLD}" + "=" * 68)
    print(f"  Batch complete: {success}/{len(files)} track(s) converted in {total_time:.1f}s.")
    print("=" * 68 + f"{TerminalColors.RESET}\n")
    return 0 if success == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
