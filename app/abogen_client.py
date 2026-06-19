"""GUI-side client that drives Abogen generation via the in-process driver.

Launches `abogen_driver.py` with the provisioned Abogen environment's Python as a
subprocess, parses the sentinel-tagged JSON events for progress/logs, and returns
the produced .m4b + .ass paths. Also exposes the available voice list.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .abogen_driver import EVENT_SENTINEL

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _driver_env() -> dict:
    """Environment for the Abogen interpreter.

    On Apple Silicon, set PYTORCH_ENABLE_MPS_FALLBACK=1. Abogen sets this in its
    `main.py`, but the driver imports Abogen modules directly and never runs
    `main.py`; without it, Kokoro's MPS-unsupported ops would hard-error instead of
    falling back per-op to CPU. (Harmless on other platforms — it's never read.)
    """
    env = dict(os.environ)
    if sys.platform == "darwin" and platform.machine() == "arm64":
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    return env


def gpu_status(abogen_python: str, use_gpu: bool = True) -> str:
    """Abogen's own active-device message (e.g. 'MPS GPU available and enabled.').

    Runs `abogen.utils.get_gpu_acceleration` inside the Abogen interpreter so the
    UI can show the real backend and confirm acceleration is on (not silently CPU).
    """
    code = ("import json,sys;from abogen.utils import get_gpu_acceleration;"
            f"msg,_=get_gpu_acceleration({bool(use_gpu)});"
            "sys.stdout.write('@@G@@'+json.dumps(msg))")
    try:
        r = subprocess.run([abogen_python, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_driver_env(),
                           creationflags=CREATE_NO_WINDOW, timeout=120)
        for line in r.stdout.splitlines():
            if "@@G@@" in line:
                return str(json.loads(line.split("@@G@@", 1)[1]))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return ""

# Web-UI subtitle-format display label -> internal constant key (abogen.constants).
SUBTITLE_FORMAT_KEYS = {
    "SRT (standard)": "srt",
    "ASS (wide)": "ass_wide",
    "ASS (narrow)": "ass_narrow",
    "ASS (centered wide)": "ass_centered_wide",
    "ASS (centered narrow)": "ass_centered_narrow",
}


@dataclass
class AbogenResult:
    audio: str = ""
    subtitles: list[str] = field(default_factory=list)


def driver_path() -> str:
    """Locate abogen_driver.py both in dev and when frozen by PyInstaller."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for cand in (Path(base) / "app" / "abogen_driver.py", Path(base) / "abogen_driver.py"):
            if cand.is_file():
                return str(cand)
    return str(Path(__file__).with_name("abogen_driver.py"))


def list_voices(abogen_python: str) -> list[str]:
    code = ("import json,sys;from abogen.constants import VOICES_INTERNAL;"
            "sys.stdout.write('@@V@@'+json.dumps(list(VOICES_INTERNAL)))")
    try:
        r = subprocess.run([abogen_python, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_driver_env(),
                           creationflags=CREATE_NO_WINDOW, timeout=120)
        for line in r.stdout.splitlines():
            if "@@V@@" in line:
                return list(json.loads(line.split("@@V@@", 1)[1]))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return []


def build_spec(input_path, out_dir, abogen_cfg: dict, *, title: Optional[str] = None,
               selected_indices: Optional[list] = None) -> dict:
    sub_label = abogen_cfg.get("subtitle_format", "ASS (centered wide)")
    spec = {
        "input": str(input_path),
        "output_dir": str(out_dir),
        "title": title,
        "voice": abogen_cfg.get("voice", "af_heart"),
        "speed": abogen_cfg.get("speed", 1.25),
        "subtitle_mode": abogen_cfg.get("subtitle_mode", "Sentence + Highlighting"),
        "language": abogen_cfg.get("language", "a"),
        "settings": {
            "output_format": abogen_cfg.get("output_format", "m4b"),
            "subtitle_format": SUBTITLE_FORMAT_KEYS.get(sub_label, sub_label),
            "use_gpu": abogen_cfg.get("use_gpu", True),
            "replace_single_newlines": abogen_cfg.get("replace_single_newlines", True),
        },
    }
    if selected_indices is not None:
        spec["selected_indices"] = list(selected_indices)
    return spec


def _run_driver(abogen_python, spec, *, on_progress=None, on_log=None,
                on_chapters=None, cancel=None) -> AbogenResult:
    """Run the driver once, dispatching events. Returns the (possibly empty) result."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spec, tmp)
    tmp.close()
    result = AbogenResult()
    error: Optional[str] = None
    try:
        proc = subprocess.Popen(
            [abogen_python, driver_path(), tmp.name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, env=_driver_env(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise RuntimeError("Cancelled by user.")
            if EVENT_SENTINEL in line:
                try:
                    ev = json.loads(line.split(EVENT_SENTINEL, 1)[1].strip())
                except json.JSONDecodeError:
                    continue
                kind = ev.get("event")
                if kind == "progress" and on_progress:
                    on_progress(float(ev.get("percent", 0.0)))
                elif kind == "log" and on_log:
                    on_log(str(ev.get("message", "")))
                elif kind == "chapters" and on_chapters:
                    on_chapters(list(ev.get("chapters", [])))
                elif kind == "done":
                    result.audio = ev.get("audio", "")
                    result.subtitles = list(ev.get("subtitles", []))
                elif kind == "error":
                    error = str(ev.get("message", "Abogen error"))
            elif on_log:
                stripped = line.rstrip()
                if stripped:
                    on_log(stripped)
        proc.wait()
        if error or proc.returncode != 0:
            raise RuntimeError(error or f"Abogen exited with code {proc.returncode}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return result


def extract_chapters(abogen_python: str, spec: dict, *, cancel=None) -> list[dict]:
    """Phase 1: return the book's chapters with Abogen's smart pre-selection."""
    captured: list[dict] = []
    probe = dict(spec)
    probe["mode"] = "extract"
    _run_driver(abogen_python, probe, on_chapters=captured.extend, cancel=cancel)
    return captured


def generate(
    abogen_python: str,
    spec: dict,
    *,
    on_progress: Optional[Callable[[float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> AbogenResult:
    """Phase 2: run one Abogen generation; returns the produced audio + subtitle paths."""
    return _run_driver(abogen_python, spec, on_progress=on_progress,
                       on_log=on_log, cancel=cancel)
