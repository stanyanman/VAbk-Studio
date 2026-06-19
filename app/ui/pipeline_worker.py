"""Workers for the full pipeline (Abogen generation -> ffmpeg video) and for
provisioning the Abogen runtime.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from ..abogen_client import build_spec, extract_chapters, generate
from ..provisioning import ensure_ffmpeg, provision_abogen
from ..video_builder import MediaPair, VideoSettings, render, set_ass_font_size


def _move_into(src, dst_dir, new_stem: str) -> Path:
    """Move src into dst_dir as <new_stem><src ext>, flat, overwriting. Cross-drive safe."""
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (new_stem + src.suffix)
    if dst.resolve() == src.resolve():
        return dst
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))
    return dst


class PipelineWorker(QThread):
    """For each input: generate audio+subtitles with Abogen, then render the video."""

    item_status = pyqtSignal(int, str)
    audio_progress = pyqtSignal(int, float)   # row, 0-100 (Abogen step)
    video_progress = pyqtSignal(int, float)   # row, 0-100 (ffmpeg step)
    item_done = pyqtSignal(int, bool, str)
    log = pyqtSignal(str)
    all_done = pyqtSignal()

    def __init__(self, inputs, abogen_python, abogen_cfg, video_settings: VideoSettings,
                 out_dir, ffmpeg, ffprobe, selections=None, parallel=1,
                 audio_out_dir=None, audio_only=False, parent=None):
        super().__init__(parent)
        self._inputs = list(inputs)
        self._abopy = abogen_python
        self._abogen_cfg = abogen_cfg
        self._video = video_settings
        self._out_dir = out_dir                       # final video (.mp4) destination
        self._audio_out_dir = audio_out_dir or out_dir  # .m4b + .ass destination
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._selections = selections or {}  # input path -> list[int] of enabled chapters
        self._parallel = max(1, int(parallel))
        self._audio_only = bool(audio_only)  # Abogen tab: stop after .m4b + .ass
        self._font_size = int(abogen_cfg.get("subtitle_font_size", 32) or 0)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        if not self._audio_only:  # video step needs ffmpeg — fetch it if missing
            try:
                self._ffmpeg, self._ffprobe = ensure_ffmpeg(
                    self._ffmpeg, self._ffprobe, on_log=self.log.emit)
            except Exception as exc:  # noqa: BLE001
                self.log.emit(f"ffmpeg setup failed: {exc}")
        with ThreadPoolExecutor(max_workers=self._parallel) as ex:
            list(ex.map(lambda ri: self._process_one(*ri), enumerate(self._inputs)))
        self.all_done.emit()

    def _process_one(self, row: int, inp: str) -> None:
        if self._cancel.is_set():
            self.item_status.emit(row, "Cancelled")
            return
        base = Path(inp).stem
        self.log.emit(f"\n=== {base} ===")
        # Abogen always nests output in a timestamped subfolder, so generate into a
        # throwaway temp dir, then drop the finished files flat into the chosen folder.
        work = Path(tempfile.mkdtemp(prefix="vas_pipe_"))
        try:
            self.item_status.emit(row, "Generating audio…")
            spec = build_spec(inp, str(work), self._abogen_cfg, title=base,
                              selected_indices=self._selections.get(inp))
            res = generate(
                self._abopy, spec,
                on_progress=lambda p, r=row: self.audio_progress.emit(r, p),
                on_log=lambda m, b=base: self.log.emit(f"[{b}] {m}"),
                cancel=self._cancel,
            )
            self.audio_progress.emit(row, 100.0)
            if not res.audio:
                raise RuntimeError("Abogen produced no audio file.")

            # Place audio + subtitles flat in the audio folder.
            audio_dst = _move_into(res.audio, self._audio_out_dir, base)
            self.log.emit(f"[{base}] Saved audio: {audio_dst}")
            ass_dst = None
            if res.subtitles:
                ass_dst = _move_into(res.subtitles[0], self._audio_out_dir, base)
                if self._font_size:
                    set_ass_font_size(ass_dst, self._font_size)
                self.log.emit(f"[{base}] Saved subtitles: {ass_dst}")

            if self._audio_only:
                self.item_status.emit(row, "Done")
                self.item_done.emit(row, True, str(audio_dst))
                return

            self.item_status.emit(row, "Rendering video…")
            pair = MediaPair(str(audio_dst), str(ass_dst) if ass_dst else None, base)
            out = render(
                pair, self._video, self._out_dir,
                ffmpeg=self._ffmpeg, ffprobe=self._ffprobe,
                on_progress=lambda p, r=row: self.video_progress.emit(r, p),
                on_log=lambda m, b=base: self.log.emit(f"[{b}] {m}"),
                cancel=self._cancel,
            )
            self.video_progress.emit(row, 100.0)
            self.item_status.emit(row, "Done")
            self.item_done.emit(row, True, out)
        except Exception as exc:  # noqa: BLE001
            self.item_status.emit(row, "Failed")
            self.item_done.emit(row, False, str(exc))
            self.log.emit(f"[{base}] ERROR: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


class ExtractWorker(QThread):
    """Phase 1: extract a single book's chapter list (for the picker dialog)."""

    chapters = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, abogen_python, spec, parent=None):
        super().__init__(parent)
        self._abopy = abogen_python
        self._spec = spec

    def run(self) -> None:
        try:
            self.chapters.emit(extract_chapters(self._abopy, self._spec))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ProvisionWorker(QThread):
    """Install/update the Abogen runtime from GitHub via uv (multi-GB first run)."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, runtime_dir, pin, use_gpu, parent=None):
        super().__init__(parent)
        self._runtime = runtime_dir
        self._pin = pin
        self._use_gpu = use_gpu

    def run(self) -> None:
        try:
            py = provision_abogen(self._runtime, pin=self._pin, use_gpu=self._use_gpu,
                                  on_log=lambda m: self.log.emit(m))
            self.done.emit(True, str(py))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))


class FfmpegWorker(QThread):
    """Auto-download the pinned static ffmpeg off the UI thread."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str, str)  # ok, ffmpeg path, ffprobe path

    def __init__(self, ffmpeg_pref="", ffprobe_pref="", parent=None):
        super().__init__(parent)
        self._ff = ffmpeg_pref
        self._fp = ffprobe_pref

    def run(self) -> None:
        try:
            ff, fp = ensure_ffmpeg(self._ff, self._fp, on_log=self.log.emit)
            self.done.emit(True, ff, fp)
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"ffmpeg setup failed: {exc}")
            self.done.emit(False, "", "")
