"""User preferences and presets, persisted under the per-user config directory.

Holds tool paths, the default video preset (the user's preferred ffmpeg settings),
the default Abogen generation settings, the workspace folders, and remembered
locations. Config dir / app root live in `paths.py` (dependency-free).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_root, config_dir  # re-exported for callers that import them here
from .provisioning import DEFAULT_PIN
from .video_builder import (
    DEFAULT_FFMPEG, DEFAULT_FFPROBE, VideoSettings, default_video_settings,
)

CONFIG_PATH = config_dir() / "config.json"


# Abogen generation defaults — mirror the user's Abogen GUI screenshot.
DEFAULT_ABOGEN = {
    "voice": "af_heart",
    "speed": 1.25,
    "subtitle_mode": "Sentence + Highlighting",
    "output_format": "m4b",
    "subtitle_format": "ASS (centered wide)",
    "replace_single_newlines": True,
    "use_gpu": True,
    "language": "a",  # American English
    "subtitle_font_size": 32,  # rewrite the generated .ass style to this size
}


def derive_workspace(base) -> dict[str, str]:
    """Map a chosen workspace base folder to the Input/Output/Visual Audiobooks paths.

    Input  -> source EPUB/PDF/TXT
    Output -> generated .m4b + .ass
    Visual Audiobooks -> final .mp4
    """
    base = Path(base)
    return {
        "workspace_base": str(base),
        "input_folder": str(base / "Input"),
        "audio_folder": str(base / "Output"),
        "pipeline_audio_folder": str(base / "Output"),
        "abogen_output_folder": str(base / "Output"),
        "output_folder": str(base / "Visual Audiobooks"),
    }


def default_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "ffmpeg_path": DEFAULT_FFMPEG,    # empty -> auto-resolved (PATH / download cache)
        "ffprobe_path": DEFAULT_FFPROBE,
        "parallel_jobs": 3,               # how many renders/pipelines to run at once
        # Abogen runtime: isolated env managed by the app
        "abogen_runtime_dir": str(config_dir() / "abogen-runtime"),
        "abogen_python": "",              # explicit interpreter override (optional)
        "abogen_pin": DEFAULT_PIN,        # commit/tag the app installs from GitHub
        # Workspace: chosen on first run (see ui/first_run_dialog.py). Until then,
        # default to folders inside the app directory.
        "workspace_configured": False,
        "video": default_video_settings().to_dict(),
        "abogen": dict(DEFAULT_ABOGEN),
    }
    cfg.update(derive_workspace(app_root()))
    return cfg


def _merge(base: dict, override: dict) -> dict:
    """Deep-merge override into a copy of base (so new keys survive upgrades)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg = default_config()
    if CONFIG_PATH.is_file():
        try:
            cfg = _merge(cfg, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def video_settings(cfg: dict[str, Any]) -> VideoSettings:
    return VideoSettings.from_dict(cfg.get("video", {}))
