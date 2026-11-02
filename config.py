"""
config.py - Dynamic Environment & Path Configuration for Dolby Atmos All-in-One.
Supports automatic discovery of tools (FFmpeg, Dolby DEE, NUGEN VST3) and user overrides via config.json.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "dee_path": "",
    "ffmpeg_path": "",
    "custom_vst3_path": "",
    "default_preset": "immersive",
    "default_binaural_preset": "punchy_direct",
    "auto_nugen_if_available": True,
}


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, merging with defaults."""
    if not CONFIG_FILE.is_file():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data)
        return cfg
    except Exception as e:
        print(f"[Config] Warning: Failed to read {CONFIG_FILE}: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    """Save configuration dictionary to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[Config] Warning: Failed to write {CONFIG_FILE}: {e}")


def get_config_value(key: str, default: Any = None) -> Any:
    cfg = load_config()
    return cfg.get(key, default)


def set_config_value(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)


def resolve_ffmpeg() -> Optional[Path]:
    """
    Resolve ffmpeg executable in priority order:
    1. User custom path in config.json
    2. Local apps/ffmpeg/ffmpeg.exe
    3. System PATH (shutil.which)
    """
    cfg = load_config()
    user_p = cfg.get("ffmpeg_path", "").strip()
    if user_p:
        p = Path(user_p)
        if p.is_file():
            return p

    # Local bundled directory
    local_p = BASE_DIR / "apps" / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local_p.is_file():
        return local_p

    # System PATH
    sys_which = shutil.which("ffmpeg")
    if sys_which:
        return Path(sys_which).resolve()

    return None


def resolve_dee() -> Optional[Path]:
    """
    Resolve Dolby Encoding Engine (DEE) executable in priority order:
    1. User custom path in config.json
    2. Local apps/dee/dee.exe
    3. System PATH (shutil.which)
    """
    cfg = load_config()
    user_p = cfg.get("dee_path", "").strip()
    if user_p:
        p = Path(user_p)
        if p.is_file():
            return p

    # Local bundled directory
    local_p = BASE_DIR / "apps" / "dee" / ("dee.exe" if sys.platform == "win32" else "dee")
    if local_p.is_file():
        return local_p

    # System PATH
    sys_which = shutil.which("dee")
    if sys_which:
        return Path(sys_which).resolve()

    return None


def is_dee_available() -> bool:
    """Return True if Dolby Encoding Engine (DEE) is present and usable."""
    p = resolve_dee()
    return p is not None and p.is_file()


def is_nugen_available() -> bool:
    """Return True if NUGEN Halo Upmix is available."""
    try:
        from surroundupmix.nugen import is_nugen_available as _nug_check
        return _nug_check()
    except Exception:
        return False
