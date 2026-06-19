"""Filesystem locations for VAbk Studio (config dir, app root, ffmpeg cache).

Kept dependency-free (stdlib only) so settings.py, provisioning.py and
video_builder.py can all import it without circular imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def app_root() -> Path:
    """The folder the app lives in.

    Frozen (PyInstaller): the executable's directory.
    From source: the project root — the folder containing run.py and the `app` package.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """App data directory — self-contained inside the app folder by default (`<app>/data`).

    Holds everything the app generates: config.json, the provisioned Abogen
    environment, the ffmpeg cache, and uv's package cache. Keeping it in the folder
    means a clone is fully self-contained (delete the folder = clean uninstall) and
    avoids the cloud-managed user profile (where uv hardlinks fail, os error 396).
    Override with the VABK_DATA_DIR environment variable if the app folder isn't writable.
    """
    override = os.environ.get("VABK_DATA_DIR", "").strip()
    base = Path(override) if override else (app_root() / "data")
    base.mkdir(parents=True, exist_ok=True)
    return base


def ffmpeg_cache_dir() -> Path:
    """Where auto-downloaded ffmpeg/ffprobe binaries are cached."""
    d = config_dir() / "ffmpeg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def uv_cache_dir() -> Path:
    """uv's package cache, kept inside the app data dir so it's self-contained and
    co-located with the Abogen venv — which lets uv hardlink instead of copy."""
    d = config_dir() / "uv-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d
