"""Filesystem locations for VAbk Studio (config dir, app root, ffmpeg cache).

Kept dependency-free (stdlib only) so settings.py, provisioning.py and
video_builder.py can all import it without circular imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Filesystem-safe identifier (no spaces) used for the per-user config/cache folder.
APP_DIRNAME = "VAbkStudio"


def config_dir() -> Path:
    """Per-user config/data directory, created if missing.

    Windows: %APPDATA%\\VAbkStudio
    macOS:   ~/Library/Application Support/VAbkStudio
    Linux:   $XDG_CONFIG_HOME/VAbkStudio  (or ~/.config/VAbkStudio)
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / APP_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_root() -> Path:
    """The folder the app lives in.

    Frozen (PyInstaller): the executable's directory.
    From source: the project root — the folder containing run.py and the `app` package.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ffmpeg_cache_dir() -> Path:
    """Where auto-downloaded ffmpeg/ffprobe binaries are cached."""
    d = config_dir() / "ffmpeg"
    d.mkdir(parents=True, exist_ok=True)
    return d
