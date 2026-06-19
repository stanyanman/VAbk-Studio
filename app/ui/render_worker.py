"""Background worker that renders a queue of media pairs to videos, one at a time.

Renders run sequentially (a single GPU encoder session at a time avoids contention).
All UI updates happen via Qt signals, which are delivered to the GUI thread.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QThread, pyqtSignal

from ..provisioning import ensure_ffmpeg
from ..video_builder import MediaPair, VideoSettings, render


class RenderWorker(QThread):
    item_status = pyqtSignal(int, str)        # row, status text
    item_progress = pyqtSignal(int, float)    # row, percent
    item_done = pyqtSignal(int, bool, str)    # row, ok, output-path-or-error
    log = pyqtSignal(str)
    all_done = pyqtSignal()

    def __init__(self, items, settings: VideoSettings, out_dir, ffmpeg, ffprobe,
                 test_seconds=None, parallel=1, parent=None):
        super().__init__(parent)
        self._items: list[MediaPair] = list(items)
        self._settings = settings
        self._out_dir = out_dir
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._test_seconds = test_seconds
        self._parallel = max(1, int(parallel))
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        # Fetch ffmpeg if it isn't already present (one-time auto-download).
        try:
            self._ffmpeg, self._ffprobe = ensure_ffmpeg(
                self._ffmpeg, self._ffprobe, on_log=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"ffmpeg setup failed: {exc}")
        # Renders run concurrently (each ffmpeg is mostly single-threaded on CPU for
        # libass, so N at once ~N× throughput up to core/GPU limits).
        with ThreadPoolExecutor(max_workers=self._parallel) as ex:
            list(ex.map(lambda ri: self._render_one(*ri), enumerate(self._items)))
        self.all_done.emit()

    def _render_one(self, row: int, pair: MediaPair) -> None:
        if self._cancel.is_set():
            self.item_status.emit(row, "Cancelled")
            return
        self.item_status.emit(row, "Rendering…")
        self.log.emit(f"\n=== {pair.base} ===")
        try:
            out = render(
                pair, self._settings, self._out_dir,
                ffmpeg=self._ffmpeg, ffprobe=self._ffprobe,
                on_progress=lambda p, r=row: self.item_progress.emit(r, p),
                on_log=lambda m, b=pair.base: self.log.emit(f"[{b}] {m}"),
                cancel=self._cancel,
                test_seconds=self._test_seconds,
            )
            self.item_status.emit(row, "Done")
            self.item_done.emit(row, True, out)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.item_status.emit(row, "Failed")
            self.item_done.emit(row, False, str(exc))
            self.log.emit(f"[{pair.base}] ERROR: {exc}")
