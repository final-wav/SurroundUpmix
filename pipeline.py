#!/usr/bin/env python3
"""
Dolby Atmos All-in-One Automated Pipeline Engine
Combines:
  1. AI Stem Separation (Demucs)
  2. Karaoke Vocal Split (MelBandRoformer)
  3. 3D Spatial Audio Synthesis (7.1.2 Bed Carrier + 14 7.1.6 Anchors + 6 Dynamic Objects)
  4. Granular Binaural Render Mode Matrix (Off, Near, Mid, Far per object)
  5. Auto Dolby dbmd Authoring Signature Injection
  6. Dolby Encoding Engine 5.2.1 (E-AC-3 JOC 768 kbps)
  7. FFmpeg Packaging (.m4a) & Mutagen Tagging
  8. Flexible Output Destination (Custom Directory or Modern Music Player Library)
"""

import os
import sys
import time
import shutil
import subprocess
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Union

from mutagen import File as MutagenFile
from mutagen.mp4 import MP4, MP4Cover
import config

# Setup Environment
BASE_DIR = Path(__file__).resolve().parent
APPS_DIR = BASE_DIR / "apps"
DEE_EXE = APPS_DIR / "dee" / "dee.exe"
FFMPEG_EXE = APPS_DIR / "ffmpeg" / "ffmpeg.exe"
MEDIAINFO_EXE = APPS_DIR / "dee" / "MediaInfo.exe"
SPLITTER_VENV_PY = BASE_DIR / "bin" / "splitter_venv" / "Scripts" / "python.exe"
SPLITTER_MODELS_DIR = BASE_DIR / "bin" / "splitter_models"
DRUMSEP_REPO = SPLITTER_MODELS_DIR / "drumsep"
DRUMSEP_MODEL_FILE = DRUMSEP_REPO / "49469ca8.th"
SPLIT_SCRIPT = BASE_DIR / "split_vocals.py"


def ensure_drumsep_model() -> bool:
    DRUMSEP_REPO.mkdir(parents=True, exist_ok=True)
    if DRUMSEP_MODEL_FILE.is_file() and DRUMSEP_MODEL_FILE.stat().st_size > 100_000_000:
        return True
    try:
        import requests
        print("[DrumSep] Downloading DrumSep model checkpoint (49469ca8.th, ~160 MB)...")
        r = requests.get('https://huggingface.co/vincewin/drumsep/resolve/main/49469ca8.th', stream=True)
        r.raise_for_status()
        with open(DRUMSEP_MODEL_FILE, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"[DrumSep] Warning: Could not download DrumSep model: {e}")
        return False
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

# Add local surroundupmix package to path
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from surroundupmix.engine import upmix_folder
from surroundupmix.presets import PRESETS, DEFAULT_PRESET
from binaural_matrix import build_custom_dbmd, get_default_matrix, OBJECT_KEYS, PRESET_MATRICES


def get_short_path(p: Union[str, Path]) -> str:
    """Resolve Windows 8.3 short path name to avoid DEE space parsing bugs."""
    path_str = str(Path(p).resolve())
    if sys.platform != "win32":
        return path_str
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(1024)
        _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        _GetShortPathNameW.restype = wintypes.DWORD
        ret = _GetShortPathNameW(path_str, buf, 1024)
        if ret > 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return path_str


def log(msg: str, cb: Optional[Callable[[str, float, str], None]] = None, stage: str = "Info", pct: float = 0.0):
    print(f"[{stage}] {msg}")
    if cb:
        cb(stage, pct, msg)


def resolve_demucs_cmd(override: Optional[str] = None):
    if override:
        return override.split()
    if shutil.which("demucs"):
        return ["demucs"]
    for cand in (["py", "-3.10", "-m", "demucs"], ["python", "-m", "demucs"], [sys.executable, "-m", "demucs"]):
        try:
            r = subprocess.run(cand + ["--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return cand
        except Exception:
            pass
    return None


def extract_metadata(file_path: Path) -> Dict[str, Any]:
    """Extract metadata (Title, Artist, Album, Track, Cover) from source file or multitrack directory."""
    file_path = Path(file_path).resolve()
    is_dir = file_path.is_dir()

    meta = {
        "title": file_path.name if is_dir else file_path.stem,
        "artist": "Unknown Artist",
        "album": "Dolby Atmos Music",
        "track": 1,
        "total_tracks": 1,
        "year": str(time.localtime().tm_year),
        "cover_data": None,
        "cover_mime": "image/jpeg"
    }

    # If directory, look inside for tagged audio stems
    sample_file = file_path
    if is_dir:
        cand_files = [f for f in file_path.glob("*.*") if f.suffix.lower() in (".flac", ".wav", ".mp3", ".m4a")]
        sample_file = cand_files[0] if cand_files else None

    if sample_file and sample_file.is_file():
        try:
            mf = MutagenFile(str(sample_file))
            if mf:
                tags = mf.tags or {}
                for key in ("artist", "ARTIST", "\xa9ART", "Author", "albumartist", "ALBUMARTIST"):
                    if key in tags and tags[key]:
                        val = tags[key][0] if isinstance(tags[key], list) else str(tags[key])
                        meta["artist"] = str(val).strip()
                        break

                for key in ("title", "TITLE", "\xa9nam", "Title"):
                    if key in tags and tags[key] and not is_dir:
                        val = tags[key][0] if isinstance(tags[key], list) else str(tags[key])
                        meta["title"] = str(val).strip()
                        break

                for key in ("album", "ALBUM", "\xa9alb", "WM/AlbumTitle"):
                    if key in tags and tags[key]:
                        val = tags[key][0] if isinstance(tags[key], list) else str(tags[key])
                        meta["album"] = str(val).strip()
                        break

                for key in ("tracknumber", "TRACKNUMBER", "trkn"):
                    if key in tags and tags[key]:
                        val = tags[key][0] if isinstance(tags[key], list) else tags[key]
                        if isinstance(val, tuple):
                            meta["track"] = int(val[0])
                            if len(val) > 1 and val[1]:
                                meta["total_tracks"] = int(val[1])
                        else:
                            m = re.search(r"^(\d+)(?:/(\d+))?", str(val))
                            if m:
                                meta["track"] = int(m.group(1))
                                if m.group(2):
                                    meta["total_tracks"] = int(m.group(2))
                        break

                for key in ("date", "DATE", "\xa9day", "year", "YEAR"):
                    if key in tags and tags[key]:
                        val = str(tags[key][0] if isinstance(tags[key], list) else tags[key])
                        m = re.search(r"\b(\d{4})\b", val)
                        if m:
                            meta["year"] = m.group(1)
                        break

                if hasattr(mf, "pictures") and mf.pictures:
                    pic = mf.pictures[0]
                    meta["cover_data"] = pic.data
                    meta["cover_mime"] = pic.mime
                elif "covr" in tags and tags["covr"]:
                    meta["cover_data"] = bytes(tags["covr"][0])
                    meta["cover_mime"] = "image/png" if tags["covr"][0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        except Exception as e:
            print(f"Warning extracting metadata: {e}")

    # Fallback heuristic for multitrack directory names
    if is_dir:
        target = file_path
        while target.name.lower() in ("flac", "grouped", "raw", "mogg", "alt. grouping", "wav") and target.parent:
            target = target.parent
        
        m_num = re.match(r"^(\d+)\s*[-–—.]\s*(.*)$", target.name)
        if m_num:
            meta["track"] = int(m_num.group(1))
            name_rest = m_num.group(2).strip()
        else:
            name_rest = target.name

        m_art = re.match(r"^(.*?)\s*[-–—]\s*(.*)$", name_rest)
        if m_art:
            if meta["artist"] == "Unknown Artist":
                meta["artist"] = m_art.group(1).strip()
            meta["title"] = m_art.group(2).strip()
        else:
            if meta["artist"] == "Unknown Artist" and target.parent and target.parent.name.lower() not in ("multitracks", "multitrack"):
                meta["artist"] = target.parent.name
            meta["title"] = name_rest

        # Also inspect audio file names inside if artist/title still generic
        if meta["artist"] == "Unknown Artist" and sample_file:
            m_stem = re.match(r"^(.*?)\s*[-–—]\s*(.*?)(?:\s*\([^)]*\))?$", sample_file.stem)
            if m_stem:
                meta["artist"] = m_stem.group(1).strip()
                if meta["title"] == file_path.name:
                    meta["title"] = m_stem.group(2).strip()

        if meta["album"] == "Dolby Atmos Music":
            meta["album"] = meta["title"]

    # Cover search in directory or parent directory
    if not meta["cover_data"]:
        search_dirs = [file_path] if is_dir else [file_path.parent]
        if is_dir and file_path.parent:
            search_dirs.append(file_path.parent)
        for parent in search_dirs:
            for cand in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"):
                cand_p = parent / cand
                if cand_p.is_file():
                    try:
                        meta["cover_data"] = cand_p.read_bytes()
                        meta["cover_mime"] = "image/png" if cand_p.suffix.lower() == ".png" else "image/jpeg"
                        break
                    except Exception:
                        pass
            if meta["cover_data"]:
                break

    return meta


def resolve_smart_export_path(
    input_file: Union[str, Path],
    meta: Optional[Dict[str, Any]] = None,
    library_root: Optional[Union[str, Path]] = None
) -> Path:
    """
    Intelligently maps an input audio file into the Modern Music Player library structure:
    Pattern: M:\\audio\\library\\music_2026\\<Artist>\\Dolby Atmos\\<Category>\\<Album> [Dolby Atmos]\\
    Auto-detects Categories (Albums, Singles, EPs, Mixtapes, Sessions, Music Videos),
    strips redundant artist prefixes, auto-appends '[Dolby Atmos]' or merges with existing folders.
    """
    lib_root = Path(library_root or DEFAULT_LIBRARY_ROOT).resolve()
    p = Path(input_file).resolve()
    parts = list(p.parts)

    known_cats = {"albums", "singles", "eps", "mixtapes", "sessions", "music videos", "soundtracks", "compilations"}
    cat_idx = None
    for idx, part in enumerate(parts):
        if part.lower() in known_cats:
            cat_idx = idx
            break

    if cat_idx is not None and cat_idx >= 1:
        artist = parts[cat_idx - 1]
        category = parts[cat_idx]
        if cat_idx + 1 < len(parts) - 1:
            album_folder = parts[cat_idx + 1]
        else:
            album_folder = (meta.get("album") if meta else None) or parts[-2]
    else:
        # Fallback to metadata or folder names
        artist = (meta.get("artist") if meta else None) or (parts[-3] if len(parts) >= 3 else "Unknown Artist")
        album_folder = (meta.get("album") if meta else None) or (parts[-2] if len(parts) >= 2 else "Unknown Album")
        category = "Albums"

    safe_artist = "".join(c for c in str(artist) if c not in r'\/:*?"<>|').strip() or "Unknown Artist"
    clean_album = "".join(c for c in str(album_folder) if c not in r'\/:*?"<>|').strip() or "Unknown Album"

    # Strip redundant artist prefix (e.g. 'AURORA - A Different Kind...' -> 'A Different Kind...')
    prefix_pat = re.compile(rf'^{re.escape(safe_artist)}\s*[-–—]\s*', re.IGNORECASE)
    clean_album = prefix_pat.sub('', clean_album).strip()

    # Normalize category capitalization
    cat_lower = category.lower()
    if cat_lower == "music videos":
        category_normalized = "Music Videos"
    elif cat_lower == "eps":
        category_normalized = "EPs"
    elif cat_lower == "mixtapes":
        category_normalized = "Mixtapes"
    elif cat_lower == "sessions":
        category_normalized = "Sessions"
    elif cat_lower == "singles":
        category_normalized = "Singles"
    else:
        category_normalized = "Albums"

    cat_parent_dir = lib_root / safe_artist / "Dolby Atmos" / category_normalized

    # Check if an existing directory inside the category folder matches this album
    target_album_folder = None
    if cat_parent_dir.is_dir():
        for sub_dir in cat_parent_dir.iterdir():
            if sub_dir.is_dir():
                sub_name = sub_dir.name
                if sub_name.lower() == clean_album.lower() or sub_name.lower() == f"{clean_album} [dolby atmos]".lower():
                    target_album_folder = sub_name
                    break
                if clean_album.lower() in sub_name.lower():
                    target_album_folder = sub_name
                    break

    # If no existing folder found, format with [Dolby Atmos] tag
    if not target_album_folder:
        if not re.search(r'\[(Dolby\s+)?Atmos\]|\((Dolby\s+)?Atmos\)', clean_album, re.IGNORECASE):
            target_album_folder = f"{clean_album} [Dolby Atmos]"
        else:
            target_album_folder = clean_album

    final_dir = cat_parent_dir / target_album_folder
    return final_dir


class AtmosPipeline:
    def __init__(self, work_root: Optional[Path] = None, default_output_dir: Optional[Path] = None):
        self.work_root = work_root or (BASE_DIR / "temp")
        self.default_output_dir = default_output_dir or (BASE_DIR / "output")
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.default_output_dir.mkdir(parents=True, exist_ok=True)
        self._check_bins()

    def _check_bins(self):
        ff = config.resolve_ffmpeg()
        if not ff:
            raise FileNotFoundError(
                "FFmpeg executable not found. Please install FFmpeg (e.g. 'winget install ffmpeg') "
                "or place ffmpeg.exe into apps/ffmpeg/ or configure its path in config.json."
            )
        dee = config.resolve_dee()
        if not dee:
            print("[Pipeline Info] Dolby Encoding Engine (DEE) not detected. Operating in 'Dolby ADM BWF Master Mode' (30-channel .adm.wav output).")

    def run(
        self,
        input_file: Path,
        preset: str = "immersive",
        motion_mode: str = "dynamic",
        motion_intensity: float = 1.0,
        vocal_mode: str = "auto",
        split_vocals: str = "auto",
        split_drums: bool = True,
        separation_model: str = "htdemucs_6s",
        decorrelate: bool = True,
        recover_detail: bool = True,
        binaural: int = 0,
        binaural_matrix: Optional[Union[Dict[str, str], str]] = "punchy_direct",
        bitrate: str = "768",
        drc: str = "music_light",
        deliver_to_library: bool = False,
        library_root: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        smart_export: bool = False,
        overrides: Optional[Dict[str, Any]] = None,
        dee_path: Optional[Union[str, Path]] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
        custom_cover: Optional[Path] = None,
        export_adm: bool = True,
        export_ec3: bool = True,
        export_712: bool = False,
        export_51: bool = False,
        use_nugen: bool = False,
        nugen_diffusion: float = 0.35,
        nugen_space: float = 0.70,
        progress_cb: Optional[Callable[[str, float, str], None]] = None
    ) -> Path:
        t_start = time.time()
        input_file = Path(input_file).resolve()
        if not (input_file.is_file() or input_file.is_dir()):
            raise FileNotFoundError(f"Source file not found: {input_file}")

        track_slug = input_file.stem
        # Clean slug without spaces for work directories and DEE XML paths (DEE parser truncates at spaces)
        safe_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', track_slug)
        work_dir = self.work_root / f"job_{int(time.time()*1000)}_{safe_slug}"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Enforce valid Dolby E-AC-3 JOC bitrates (Dolby schema restricts to 384-768 kbps)
        valid_bitrates = {"384", "448", "576", "640", "768"}
        str_bitrate = str(bitrate).strip()
        if str_bitrate not in valid_bitrates:
            str_bitrate = "768"
        bitrate = str_bitrate

        meta = extract_metadata(input_file)
        if custom_metadata:
            meta.update({k: v for k, v in custom_metadata.items() if v is not None})
        if custom_cover and Path(custom_cover).is_file():
            meta["cover_data"] = Path(custom_cover).read_bytes()
            meta["cover_mime"] = "image/png" if Path(custom_cover).suffix.lower() == ".png" else "image/jpeg"

        # Determine target output folder
        if output_dir:
            target_dest_dir = Path(output_dir).resolve()
        else:
            if input_file.is_file():
                target_dest_dir = (input_file.parent / "Output").resolve()
            else:
                target_dest_dir = self.default_output_dir.resolve()

        target_dest_dir.mkdir(parents=True, exist_ok=True)

        safe_artist = "".join(c for c in meta["artist"] if c not in r'\/:*?"<>|').strip() or "Unknown Artist"
        safe_title = "".join(c for c in meta["title"] if c not in r'\/:*?"<>|').strip() or track_slug
        final_filename = f"{int(meta['track']):02d}. {safe_artist} - {safe_title}.m4a"
        final_dest_path = target_dest_dir / final_filename

        log(f"Starting Atmos All-in-One processing for: {input_file.name}", progress_cb, "Init", 0.0)
        log(f"Target Output: {final_dest_path}", progress_cb, "Init", 1.0)
        log(f"Metadata: Artist='{meta['artist']}', Album='{meta['album']}', Title='{meta['title']}', Track={meta['track']}", progress_cb, "Init", 2.0)

        is_multitrack = input_file.is_dir()
        master_orig_file = input_file

        if is_multitrack:
            # ---------------------------------------------------------
            # MULTITRACK DIRECT INGESTION (Skip Demucs)
            # ---------------------------------------------------------
            log(f"Stage 1/5: Ingesting pre-separated Multitrack stems from: {input_file.name}...", progress_cb, "Multitrack", 5.0)
            from surroundupmix.io import load_stems
            raw_stems, stem_sr = load_stems(str(input_file))
            stems_dir = work_dir / "stems"
            stems_dir.mkdir(parents=True, exist_ok=True)
            import soundfile as sf
            import numpy as np

            max_len = max(len(s) for s in raw_stems.values())
            master_mix = np.zeros((max_len, 2), dtype=np.float32)

            for s_name, s_obj in raw_stems.items():
                out_path = stems_dir / f"{s_name}.flac"
                sf.write(str(out_path), s_obj.data, stem_sr)
                master_mix[:len(s_obj)] += s_obj.data

            # Create mixed master for HF air recovery and transient analysis
            peak = float(np.max(np.abs(master_mix)))
            if peak > 1.0:
                master_mix /= peak
            master_orig_file = work_dir / "master_mix.flac"
            sf.write(str(master_orig_file), master_mix, stem_sr)

            found_names = list(raw_stems.keys())
            log(f"Multitrack stems loaded successfully: {', '.join(found_names)}.", progress_cb, "Multitrack", 25.0)
        else:
            # ---------------------------------------------------------
            # STAGE 1: AI Stem Separation (Demucs)
            # ---------------------------------------------------------
            sep_model_name = separation_model or "htdemucs_6s"
            log(f"Stage 1/5: Separating stems with Demucs AI ({sep_model_name})...", progress_cb, "Demucs", 5.0)
            sep_dir = work_dir / "stems"
            sep_dir.mkdir(parents=True, exist_ok=True)
            demucs_cmd = resolve_demucs_cmd()
            if not demucs_cmd:
                raise RuntimeError("Demucs not found. Please install Demucs into Python 3.10 environment.")

            dargs = demucs_cmd + ["-n", sep_model_name, "--flac", "-o", str(sep_dir), str(input_file)]
            res = subprocess.run(dargs, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode != 0:
                log("Demucs failed on primary device. Retrying on CPU...", progress_cb, "Demucs", 10.0)
                dargs_cpu = demucs_cmd + ["-n", sep_model_name, "--flac", "-o", str(sep_dir), "-d", "cpu", str(input_file)]
                res_cpu = subprocess.run(dargs_cpu, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res_cpu.returncode != 0:
                    raise RuntimeError(f"Demucs failed: {res_cpu.stderr}")

            # Locate separated stems
            model_sub = sep_dir / sep_model_name
            stems_dir = None
            if model_sub.is_dir():
                candidates = [d for d in model_sub.iterdir() if d.is_dir()]
                if candidates:
                    stems_dir = candidates[0]
            if not stems_dir or not (stems_dir / "vocals.flac").is_file():
                raise RuntimeError(f"Separated stems not found under {sep_dir}")

            log("Stems separated successfully.", progress_cb, "Demucs", 25.0)

        # ---------------------------------------------------------
        # STAGE 2: Vocal Karaoke Split (Lead vs Backing)
        # ---------------------------------------------------------
        vocals_path = stems_dir / "vocals.flac"
        backing_path = stems_dir / "backing.flac"
        if backing_path.is_file() and split_vocals != "force":
            log("Discrete backing vocal stem already present in multitrack (Lead and Backing preserved).", progress_cb, "VocalSplit", 38.0)
        elif split_vocals != "off" and vocals_path.is_file():
            log("Stage 2/5: Splitting vocals into Lead & Backing (MelBandRoformer)...", progress_cb, "VocalSplit", 28.0)
            vsplit_out = work_dir / "vocalsplit"
            vsplit_out.mkdir(parents=True, exist_ok=True)
            split_py = str(SPLITTER_VENV_PY) if SPLITTER_VENV_PY.is_file() else sys.executable
            split_res = subprocess.run([
                split_py,
                str(SPLIT_SCRIPT),
                str(vocals_path),
                str(vsplit_out),
                str(SPLITTER_MODELS_DIR)
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            
            lead_f = vsplit_out / "lead.flac"
            backing_f = vsplit_out / "backing.flac"
            if split_res.returncode == 0 and lead_f.is_file() and backing_f.is_file():
                shutil.copyfile(str(vocals_path), str(stems_dir / "vocals_full.flac"))
                shutil.copyfile(str(lead_f), str(vocals_path))
                shutil.copyfile(str(backing_f), str(stems_dir / "backing.flac"))
                log("Vocal separation successful: Lead (Front/Center) + Backing (Surround Arch).", progress_cb, "VocalSplit", 38.0)
            else:
                log("Vocal split skipped or unavailable, continuing with full vocal.", progress_cb, "VocalSplit", 38.0)
        else:
            log("Stage 2/5: Vocal split skipped.", progress_cb, "VocalSplit", 38.0)

        # ---------------------------------------------------------
        # STAGE 2.5: Sub-Drum Separation (Kick, Snare, Cymbals, Toms)
        # ---------------------------------------------------------
        drums_path = stems_dir / "drums.flac"
        has_subdrums = any((stems_dir / f"{s}.flac").is_file() for s in ("kick", "snare", "cymbals", "toms"))
        if has_subdrums:
            log("Discrete sub-drum stems (Kick/Snare/Cymbals/Toms) already present in multitrack.", progress_cb, "DrumSplit", 39.0)
        elif split_drums and drums_path.is_file():
            log("Stage 2.5/5: Separating Drums into Kick, Snare, Cymbals & Toms (DrumSep AI)...", progress_cb, "DrumSplit", 39.0)
            if ensure_drumsep_model():
                dsplit_out = work_dir / "drumsplit"
                dsplit_out.mkdir(parents=True, exist_ok=True)
                dargs = demucs_cmd + ["--repo", str(DRUMSEP_REPO), "-n", "49469ca8", "--flac", "-o", str(dsplit_out), str(drums_path)]
                res = subprocess.run(dargs, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode != 0:
                    dargs_cpu = demucs_cmd + ["--repo", str(DRUMSEP_REPO), "-n", "49469ca8", "--flac", "-o", str(dsplit_out), "-d", "cpu", str(drums_path)]
                    res = subprocess.run(dargs_cpu, capture_output=True, text=True, encoding="utf-8", errors="replace")

                drum_cands = list((dsplit_out / "49469ca8").glob("**/bombo.flac"))
                if drum_cands:
                    cand_dir = drum_cands[0].parent
                    bombo_f = cand_dir / "bombo.flac"
                    snare_f = cand_dir / "redoblante.flac"
                    cymbals_f = cand_dir / "platillos.flac"
                    toms_f = cand_dir / "toms.flac"
                    if bombo_f.is_file() and snare_f.is_file() and cymbals_f.is_file() and toms_f.is_file():
                        shutil.copyfile(str(drums_path), str(stems_dir / "drums_full.flac"))
                        shutil.copyfile(str(bombo_f), str(stems_dir / "kick.flac"))
                        shutil.copyfile(str(snare_f), str(stems_dir / "snare.flac"))
                        shutil.copyfile(str(cymbals_f), str(stems_dir / "cymbals.flac"))
                        shutil.copyfile(str(toms_f), str(stems_dir / "toms.flac"))
                        try:
                            drums_path.unlink()
                        except Exception:
                            pass
                        log("Sub-drum separation successful: 4 discrete drum stems (Kick, Snare, Cymbals, Toms) created.", progress_cb, "DrumSplit", 41.0)
                    else:
                        log("Drum split missing components, continuing with full drums.", progress_cb, "DrumSplit", 41.0)
                else:
                    log("Drum split output folder not found, continuing with full drums.", progress_cb, "DrumSplit", 41.0)
            else:
                log("DrumSep model unavailable, continuing with full drums.", progress_cb, "DrumSplit", 41.0)
        else:
            log("Stage 2.5/5: Sub-drum separation skipped.", progress_cb, "DrumSplit", 41.0)

        # ---------------------------------------------------------
        # STAGE 3: 3D ADM BWF All-Objects Synthesis + Custom Binaural Matrix
        # ---------------------------------------------------------
        log("Stage 3/5: Synthesizing 30-channel 3D Spatial Master (7.1.2 Bed + 14 Anchors + 6 Dynamic Objects)...", progress_cb, "SpatialADM", 42.0)
        adm_out_dir = work_dir / "adm_out"
        adm_out_dir.mkdir(parents=True, exist_ok=True)

        # Generate custom Dolby dbmd chunk with user's binaural render mode matrix
        custom_dbmd = build_custom_dbmd(binaural_matrix)

        adm_file = upmix_folder(
            str(stems_dir),
            fmt="7.1.2",
            preset=preset,
            out_dir=str(adm_out_dir),
            track_label=safe_slug,
            force_wav=True,
            adm=True,
            adm_order="renderer",
            adm_all_objects=True,
            motion_mode=motion_mode,
            motion_intensity=motion_intensity,
            vocal_mode=vocal_mode,
            original=str(master_orig_file),
            decorrelate=decorrelate,
            recover_detail=recover_detail,
            binaural_amount=max(0.0, min(1.0, binaural / 100.0)),
            dbmd=custom_dbmd,
            overrides=overrides,
            use_nugen=use_nugen,
            nugen_diffusion=nugen_diffusion,
            nugen_space=nugen_space
        )
        adm_file_path = Path(adm_file)
        if not adm_file_path.is_file():
            raise FileNotFoundError(f"Generated ADM BWF not found: {adm_file}")

        # DEE's XML schema defines file_name as stringlist.type which splits on spaces!
        # Always rename to a clean, space-free filename so DEE never sees multiple tokens.
        clean_adm_path = adm_out_dir / "atmos_master.wav"
        if adm_file_path.resolve() != clean_adm_path.resolve():
            if clean_adm_path.exists():
                clean_adm_path.unlink()
            adm_file_path.rename(clean_adm_path)
            adm_file_path = clean_adm_path

        log("30-channel Dolby ADM BWF Master created with custom Binaural Render Mode Matrix and certified dbmd signature.", progress_cb, "SpatialADM", 55.0)

        if dee_path and Path(dee_path).is_file():
            dee_exe = Path(dee_path).resolve()
        else:
            dee_exe = config.resolve_dee()
        ffmpeg_exe = config.resolve_ffmpeg()
        if (not export_ec3) or (not dee_exe):
            # ---------------------------------------------------------
            # ADM BWF MASTER MODE (No proprietary DEE required)
            # ---------------------------------------------------------
            log("Stage 4/5: Dolby Encoding Engine (DEE) not detected. Finalizing 30-channel Dolby ADM BWF Master...", progress_cb, "ExportADM", 80.0)
            adm_dest_filename = f"{int(meta['track']):02d}. {safe_artist} - {safe_title}.adm.wav"
            final_adm_path = target_dest_dir / adm_dest_filename
            shutil.copy2(str(adm_file_path), str(final_adm_path))
            log(f"30-channel Dolby ADM BWF Master created: {final_adm_path.name}", progress_cb, "ExportADM", 95.0)
            log("(Compatible with Dolby Atmos Renderer, PreSonus Studio One, Apple Logic Pro, Pro Tools & DaVinci Resolve)", progress_cb, "Complete", 100.0)

            if export_712:
                log("Rendering discrete 7.1.2 multichannel WAV...", progress_cb, "Multichannel712", 96.0)
                upmix_folder(str(stems_dir), fmt="7.1.2", preset=preset, out_dir=str(target_dest_dir),
                             track_label=f"{int(meta['track']):02d}. {safe_artist} - {safe_title}_7.1.2", force_wav=True,
                             overrides=overrides, binaural_amount=max(0.0, min(1.0, binaural / 100.0)))
            if export_51:
                log("Rendering discrete 5.1 surround WAV...", progress_cb, "Multichannel51", 98.0)
                upmix_folder(str(stems_dir), fmt="5.1", preset=preset, out_dir=str(target_dest_dir),
                             track_label=f"{int(meta['track']):02d}. {safe_artist} - {safe_title}_5.1", force_wav=True,
                             overrides=overrides, binaural_amount=max(0.0, min(1.0, binaural / 100.0)))

            try:
                shutil.rmtree(str(work_dir), ignore_errors=True)
            except Exception:
                pass

            elapsed = time.time() - t_start
            log(f"Pipeline complete in {elapsed:.1f}s -> {final_adm_path}", progress_cb, "Complete", 100.0)
            return final_adm_path

        # ---------------------------------------------------------
        # STAGE 4: Dolby Encoding Engine 5.2.1 (E-AC-3 JOC) & MP4 Muxing
        # ---------------------------------------------------------
        log(f"Stage 4/5: Encoding to Dolby Atmos E-AC-3 JOC ({bitrate} kbps, DRC: {drc})...", progress_cb, "DolbyDEE", 58.0)
        staged_ec3 = work_dir / f"{safe_slug}.ec3"
        job_xml = work_dir / f"dee_job_{int(time.time()*1000)}.xml"

        # DEE's XML parser breaks on spaces in <path> tags!
        # Always resolve to 8.3 short paths (e.g. SURROU~1) to guarantee zero spaces.
        dee_input_dir = get_short_path(adm_file_path.parent)
        dee_output_dir = get_short_path(staged_ec3.parent)
        dee_temp_dir = get_short_path(work_dir)

        xml_body = f"""<?xml version="1.0"?>
<job_config>
  <input>
    <audio>
      <adm version="1">
        <file_name>{adm_file_path.name}</file_name>
        <timecode_frame_rate>not_indicated</timecode_frame_rate>
        <offset>auto</offset>
        <ffoa>auto</ffoa>
        <storage>
          <local>
            <path>{dee_input_dir}</path>
          </local>
        </storage>
      </adm>
    </audio>
  </input>
  <filter>
    <audio>
      <encode_to_atmos_ddp version="1">
        <loudness>
          <measure_only>
            <metering_mode>1770-4</metering_mode>
            <dialogue_intelligence>false</dialogue_intelligence>
            <speech_threshold>100</speech_threshold>
          </measure_only>
        </loudness>
        <data_rate>{bitrate}</data_rate>
        <timecode_frame_rate>not_indicated</timecode_frame_rate>
        <start>first_frame_of_action</start>
        <end>end_of_file</end>
        <time_base>file_position</time_base>
        <prepend_silence_duration>0.0</prepend_silence_duration>
        <append_silence_duration>0.0</append_silence_duration>
        <drc>
          <line_mode_drc_profile>{drc}</line_mode_drc_profile>
          <rf_mode_drc_profile>{drc}</rf_mode_drc_profile>
        </drc>
        <downmix>
          <loro_center_mix_level>-3</loro_center_mix_level>
          <loro_surround_mix_level>-3</loro_surround_mix_level>
          <ltrt_center_mix_level>-3</ltrt_center_mix_level>
          <ltrt_surround_mix_level>-3</ltrt_surround_mix_level>
          <preferred_downmix_mode>loro</preferred_downmix_mode>
        </downmix>
      </encode_to_atmos_ddp>
    </audio>
  </filter>
  <output>
    <ec3 version="1">
      <file_name>{staged_ec3.name}</file_name>
      <storage>
        <local>
          <path>{dee_output_dir}</path>
        </local>
      </storage>
    </ec3>
  </output>
  <misc>
    <temp_dir>
      <clean_temp>true</clean_temp>
      <path>{dee_temp_dir}</path>
    </temp_dir>
  </misc>
</job_config>
"""
        job_xml.write_text(xml_body, encoding="utf-8")

        dee_cmd = [get_short_path(dee_exe), "-x", get_short_path(job_xml)]
        proc = subprocess.Popen(
            dee_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        re_prog = re.compile(r"Stage progress:\s*([0-9.]+)[,\s]+Overall progress:\s*([0-9.]+)")
        re_step = re.compile(r"Step:\s*([a-zA-Z0-9_]+)")
        cur_step = "DEE Processing"
        dee_output_lines = []
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                dee_output_lines.append(line)
                m_st = re_step.search(line)
                if m_st:
                    cur_step = m_st.group(1).capitalize()
                m_pr = re_prog.search(line)
                if m_pr:
                    try:
                        p_val = float(m_pr.group(2))
                        overall = 58.0 + (p_val * 0.27)
                        log(f"DEE {cur_step}: {p_val:5.1f}%", progress_cb, "DolbyDEE", overall)
                    except ValueError:
                        pass
        rc = proc.wait()
        if rc != 0 or not staged_ec3.is_file():
            err_details = "".join([l for l in dee_output_lines if "ERROR:" in l][-8:])
            raise RuntimeError(f"Dolby Encoding Engine failed with code {rc}:\n{err_details}")

        log("Dolby Atmos E-AC-3 JOC bitstream generated successfully.", progress_cb, "DolbyDEE", 85.0)

        # Mux to .m4a container with FFmpeg directly to final destination
        log("Packaging JOC bitstream into .m4a container...", progress_cb, "Packaging", 86.0)
        ff_cmd = [
            str(ffmpeg_exe),
            "-y",
            "-i", str(staged_ec3),
            "-c:a", "copy",
            "-f", "mp4",
            str(final_dest_path)
        ]
        mux_res = subprocess.run(ff_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if mux_res.returncode != 0:
            raise RuntimeError(f"FFmpeg muxing failed: {mux_res.stderr}")

        # ---------------------------------------------------------
        # STAGE 5: Mutagen Tagging
        # ---------------------------------------------------------
        log("Stage 5/5: Writing Atmos metadata, tags, and album artwork...", progress_cb, "Tagging", 90.0)
        mp4 = MP4(str(final_dest_path))
        mp4["\xa9nam"] = [meta["title"]]
        mp4["\xa9ART"] = [meta["artist"]]
        mp4["\xa9alb"] = [meta["album"]]
        mp4["trkn"] = [(int(meta["track"]), int(meta.get("total_tracks", 1)))]
        if meta.get("year"):
            mp4["\xa9day"] = [str(meta["year"])]
        if meta.get("cover_data"):
            is_png = meta.get("cover_mime", "").endswith("png")
            covr_fmt = MP4Cover.FORMAT_PNG if is_png else MP4Cover.FORMAT_JPEG
            mp4["covr"] = [MP4Cover(meta["cover_data"], imageformat=covr_fmt)]
        mp4.save()

        # Export ADM BWF (.wav) Master if requested (for Studio One / Pro Tools)
        if export_adm and adm_file_path.is_file():
            adm_dest_filename = f"{int(meta['track']):02d}. {safe_artist} - {safe_title}.adm.wav"
            # When delivering to library or using smart export, keep the huge raw ADM master in the local output dir
            adm_out_dir = self.default_output_dir if ((smart_export or deliver_to_library) and not output_dir) else target_dest_dir
            adm_final_path = adm_out_dir / adm_dest_filename
            log(f"Exporting ADM BWF Master -> {adm_final_path.name} in {adm_out_dir.name}/", progress_cb, "ExportADM", 96.0)
            shutil.copy2(str(adm_file_path), str(adm_final_path))

        if export_712:
            log("Rendering discrete 7.1.2 multichannel WAV...", progress_cb, "Multichannel712", 97.0)
            upmix_folder(str(stems_dir), fmt="7.1.2", preset=preset, out_dir=str(target_dest_dir),
                         track_label=f"{int(meta['track']):02d}. {safe_artist} - {safe_title}_7.1.2", force_wav=True,
                         overrides=overrides, binaural_amount=max(0.0, min(1.0, binaural / 100.0)))
        if export_51:
            log("Rendering discrete 5.1 surround WAV...", progress_cb, "Multichannel51", 98.0)
            upmix_folder(str(stems_dir), fmt="5.1", preset=preset, out_dir=str(target_dest_dir),
                         track_label=f"{int(meta['track']):02d}. {safe_artist} - {safe_title}_5.1", force_wav=True,
                         overrides=overrides, binaural_amount=max(0.0, min(1.0, binaural / 100.0)))

        # Cleanup temp job folder
        try:
            shutil.rmtree(str(work_dir), ignore_errors=True)
        except Exception:
            pass

        elapsed = time.time() - t_start
        log(f"Pipeline complete in {elapsed:.1f}s -> {final_dest_path}", progress_cb, "Complete", 100.0)
        return final_dest_path
