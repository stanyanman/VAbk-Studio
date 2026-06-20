"""'Full Pipeline' tab — EPUB/PDF/text -> Abogen audio+subtitles -> video.

Reuses the video settings configured on the Make Video tab (cfg['video']); adds an
Abogen generation settings panel and a runtime status / setup row.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..abogen_client import build_spec, gpu_status, list_voices
from ..provisioning import (
    DEFAULT_PIN, accelerator_label, detect_accelerator, env_python, is_provisioned,
    detect_existing_abogen, resolve_abogen_python,
)
from ..video_builder import VideoSettings, find_executable
from .chapter_dialog import ChapterSelectDialog
from .pipeline_worker import ExtractWorker, FfmpegWorker, PipelineWorker, ProvisionWorker

INPUT_EXTS = (".epub", ".pdf", ".txt", ".md", ".markdown")
SUBTITLE_MODES = ["Disabled", "Line", "Sentence", "Sentence + Comma",
                  "Sentence + Highlighting", "1 word", "2 words", "3 words"]
LANGUAGES = [("a", "American English"), ("b", "British English"), ("e", "Spanish"),
             ("f", "French"), ("h", "Hindi"), ("i", "Italian"), ("j", "Japanese"),
             ("p", "Brazilian Portuguese"), ("z", "Mandarin Chinese")]
COMMON_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael",
                 "am_fenrir", "am_puck", "bf_emma", "bm_george"]


class _VoiceLoader(QThread):
    loaded = pyqtSignal(list)

    def __init__(self, abopy, parent=None):
        super().__init__(parent)
        self._abopy = abopy

    def run(self):
        self.loaded.emit(list_voices(self._abopy))


class _GpuStatusLoader(QThread):
    """Probe Abogen for its real active device (e.g. 'CUDA GPU available and enabled.')."""
    loaded = pyqtSignal(str)

    def __init__(self, abopy, use_gpu, parent=None):
        super().__init__(parent)
        self._abopy = abopy
        self._use_gpu = use_gpu

    def run(self):
        self.loaded.emit(gpu_status(self._abopy, self._use_gpu))


AUDIO_COLOR = "#2d8cff"   # Abogen step
VIDEO_COLOR = "#1a9e4b"   # ffmpeg step


class InputTable(QTableWidget):
    COL_TITLE, COL_CHAPTERS, COL_STATUS, COL_AUDIO, COL_VIDEO = 0, 1, 2, 3, 4

    def __init__(self, on_paths, parent=None):
        super().__init__(0, 5, parent)
        self._on_paths = on_paths
        self.setHorizontalHeaderLabels(
            ["Title", "Chapters", "Step", "Audio (Abogen)", "Video (ffmpeg)"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        self._on_paths([u.toLocalFile() for u in e.mimeData().urls()])


class PipelineTab(QWidget):
    def __init__(self, cfg: dict, save_cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._save_cfg = save_cfg
        self.inputs: list[str] = []
        self.selections: dict[str, list[int]] = {}  # input path -> enabled chapter indices
        self.worker: PipelineWorker | None = None
        self.prov: ProvisionWorker | None = None
        self.extractor: ExtractWorker | None = None

        self._build_ui()
        self._refresh_runtime_status()

    # ---- UI --------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.addWidget(self._build_status_row(include_ffmpeg=True))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Drag EPUB / PDF / TXT here, or use the buttons below.")
        hint.setStyleSheet("color:#888;")
        lv.addWidget(hint)
        self.table = InputTable(self._add_paths)
        self.table.cellDoubleClicked.connect(lambda *_: self._select_chapters())
        lv.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, slot in [("Add files…", self._pick_files),
                           ("Select chapters…", self._select_chapters),
                           ("Remove", self._remove_selected),
                           ("Clear", self._clear)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        lv.addLayout(btns)
        splitter.addWidget(left)
        splitter.addWidget(self._build_settings())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Audio + subtitles folder:"))
        self.audio_edit = QLineEdit(self.cfg.get("pipeline_audio_folder", ""))
        audio_row.addWidget(self.audio_edit, 1)
        ab = QPushButton("Browse…")
        ab.clicked.connect(self._pick_audio_out)
        audio_row.addWidget(ab)
        root.addLayout(audio_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Video (.mp4) folder:"))
        self.out_edit = QLineEdit(self.cfg.get("output_folder", ""))
        out_row.addWidget(self.out_edit, 1)
        ob = QPushButton("Browse…")
        ob.clicked.connect(self._pick_out)
        out_row.addWidget(ob)
        root.addLayout(out_row)

        run_row = QHBoxLayout()
        self.start_btn = QPushButton("Start full pipeline")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        self.overall = QProgressBar()
        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(QLabel("Parallel:"))
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 999)
        self.parallel_spin.setToolTip("How many books to process at once (sweet spot ~8; >12 degrades)")
        self.parallel_spin.setValue(int(self.cfg.get("parallel_jobs", 3)))
        self.parallel_spin.valueChanged.connect(self._save_parallel)
        run_row.addWidget(self.parallel_spin)
        run_row.addWidget(self.overall, 1)
        root.addLayout(run_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(8000)
        self.log_view.setFixedHeight(150)
        root.addWidget(self.log_view)

    def _build_settings(self) -> QWidget:
        box = QGroupBox("Audiobook (Abogen) settings")
        form = QFormLayout(box)
        ab = self.cfg.get("abogen", {})

        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.addItems(COMMON_VOICES)
        self.voice_combo.setCurrentText(ab.get("voice", "af_heart"))
        form.addRow("Voice:", self.voice_combo)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.1, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setValue(float(ab.get("speed", 1.25)))
        form.addRow("Speed:", self.speed_spin)

        self.submode_combo = QComboBox()
        self.submode_combo.addItems(SUBTITLE_MODES)
        self.submode_combo.setCurrentText(ab.get("subtitle_mode", "Sentence + Highlighting"))
        form.addRow("Subtitles:", self.submode_combo)

        self.lang_combo = QComboBox()
        for code, desc in LANGUAGES:
            self.lang_combo.addItem(f"{desc} ({code})", code)
        idx = max(0, [c for c, _ in LANGUAGES].index(ab.get("language", "a"))
                  if ab.get("language", "a") in [c for c, _ in LANGUAGES] else 0)
        self.lang_combo.setCurrentIndex(idx)
        form.addRow("Language:", self.lang_combo)

        self.gpu_chk = QCheckBox("Use GPU acceleration")
        self.gpu_chk.setChecked(bool(ab.get("use_gpu", True)))
        form.addRow(self.gpu_chk)
        self.device_label = QLabel(f"Detected: {accelerator_label()}")
        self.device_label.setStyleSheet("color:#888; font-size:11px;")
        self.device_label.setWordWrap(True)
        form.addRow("", self.device_label)

        self.fontsize_spin = QSpinBox()
        self.fontsize_spin.setRange(8, 200)
        self.fontsize_spin.setValue(int(ab.get("subtitle_font_size", 32)))
        self.fontsize_spin.setToolTip("Font size written into the generated .ass subtitle file")
        form.addRow("Subtitle font size:", self.fontsize_spin)

        note = QLabel("Output: M4B (with chapters) + ASS (centered wide).\n"
                      "Video encoding uses the Make Video tab's settings.")
        note.setStyleSheet("color:#888; font-size:11px;")
        note.setWordWrap(True)
        form.addRow(note)
        return box

    # ---- abogen settings <-> cfg ----------------------------------------
    def gather_abogen_cfg(self) -> dict:
        ab = dict(self.cfg.get("abogen", {}))
        ab.update({
            "voice": self.voice_combo.currentText().strip() or "af_heart",
            "speed": round(self.speed_spin.value(), 2),
            "subtitle_mode": self.submode_combo.currentText(),
            "language": self.lang_combo.currentData(),
            "use_gpu": self.gpu_chk.isChecked(),
            "subtitle_font_size": self.fontsize_spin.value(),
            "output_format": "m4b",
            "subtitle_format": ab.get("subtitle_format", "ASS (centered wide)"),
            "replace_single_newlines": ab.get("replace_single_newlines", True),
        })
        return ab

    # ---- runtime status / dependencies ----------------------------------
    _STATUS_STYLE = {
        "ok": "color:#1a9e4b; font-weight:600;",       # green
        "missing": "color:#d23b3b; font-weight:600;",  # red
        "busy": "color:#c08000; font-weight:600;",     # amber
    }

    def _build_status_row(self, include_ffmpeg: bool) -> QWidget:
        """Dependency status indicators + a single 'Download Dependencies' button."""
        self._include_ffmpeg = include_ffmpeg
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        self.abogen_status = QLabel("Abogen: checking…")
        row.addWidget(self.abogen_status)
        if include_ffmpeg:
            dot = QLabel("·")
            dot.setStyleSheet("color:#888;")
            row.addWidget(dot)
            self.ffmpeg_status = QLabel("FFmpeg: checking…")
            row.addWidget(self.ffmpeg_status)
        row.addStretch(1)
        self.use_existing_btn = QPushButton("Use existing")
        self.use_existing_btn.setVisible(False)
        self.use_existing_btn.clicked.connect(self._adopt_existing)
        row.addWidget(self.use_existing_btn)
        self.setup_btn = QPushButton("Download Dependencies")
        self.setup_btn.clicked.connect(self._download_dependencies)
        row.addWidget(self.setup_btn)
        return w

    def _set_dep_status(self, label: QLabel, state: str, text: str) -> None:
        label.setText(text)
        label.setStyleSheet(self._STATUS_STYLE.get(state, "color:#888;"))

    def _abogen_ready(self) -> bool:
        return resolve_abogen_python(self.cfg) is not None

    def _ffmpeg_ready(self) -> bool:
        return find_executable("ffmpeg", self.cfg.get("ffmpeg_path", "")) is not None

    def _refresh_runtime_status(self):
        abopy = resolve_abogen_python(self.cfg)
        if abopy:
            self._set_dep_status(self.abogen_status, "ok", "Abogen: ✓ ready")
            self.use_existing_btn.setVisible(False)
            self._load_voices(abopy)
        else:
            existing = detect_existing_abogen()
            self.use_existing_btn.setVisible(bool(existing))
            self._set_dep_status(
                self.abogen_status, "missing",
                "Abogen: ✗ not installed" + ("  (existing found)" if existing else ""))
        if self._include_ffmpeg:
            if self._ffmpeg_ready():
                self._set_dep_status(self.ffmpeg_status, "ok", "FFmpeg: ✓ ready")
            else:
                self._set_dep_status(self.ffmpeg_status, "missing", "FFmpeg: ✗ not installed")
        self._update_download_btn()

    def _update_download_btn(self):
        missing = (not self._abogen_ready()) or (self._include_ffmpeg and not self._ffmpeg_ready())
        self.setup_btn.setText("Download Dependencies" if missing else "Re-check / update")

    def _adopt_existing(self):
        existing = detect_existing_abogen()
        if existing:
            self.cfg["abogen_python"] = existing
            self._save_cfg(self.cfg)
            self._refresh_runtime_status()

    def _load_voices(self, abopy: str):
        self._voice_loader = _VoiceLoader(abopy)
        self._voice_loader.loaded.connect(self._on_voices)
        self._voice_loader.start()
        self._gpu_loader = _GpuStatusLoader(abopy, self.gpu_chk.isChecked())
        self._gpu_loader.loaded.connect(self._on_gpu_status)
        self._gpu_loader.start()

    def _on_gpu_status(self, msg: str):
        if msg:
            self.device_label.setText(f"Abogen device: {msg}")

    def _on_voices(self, voices: list):
        if not voices:
            return
        cur = self.voice_combo.currentText()
        self.voice_combo.clear()
        self.voice_combo.addItems(voices)
        self.voice_combo.setCurrentText(cur if cur in voices else "af_heart")

    # ---- download dependencies (Abogen + ffmpeg) ------------------------
    def _download_dependencies(self):
        if self.prov and self.prov.isRunning():
            return
        need_ab = not self._abogen_ready()
        need_ff = self._include_ffmpeg and not self._ffmpeg_ready()
        if not need_ab and not need_ff:
            self._refresh_runtime_status()
            QMessageBox.information(self, "Dependencies", "Everything is already set up and ready.")
            return
        if need_ab:
            msg = ("This downloads the heavy dependencies into the app's data folder "
                   "(one-time):\n\n• Abogen + PyTorch + voice models (several GB)\n"
                   + ("• ffmpeg (~106 MB)\n" if need_ff else "") + "\nContinue?")
            if QMessageBox.question(self, "Download Dependencies",
                                    msg) != QMessageBox.StandardButton.Yes:
                return
        self.setup_btn.setEnabled(False)
        self._dep_jobs = 0
        if need_ff:
            self._dep_jobs += 1
            self._set_dep_status(self.ffmpeg_status, "busy", "FFmpeg: downloading…")
            self._ffmpeg_worker = FfmpegWorker(self.cfg.get("ffmpeg_path", ""),
                                               self.cfg.get("ffprobe_path", ""))
            self._ffmpeg_worker.log.connect(self.log_view.appendPlainText)
            self._ffmpeg_worker.done.connect(self._on_ffmpeg_downloaded)
            self._ffmpeg_worker.start()
        if need_ab:
            self._dep_jobs += 1
            self.use_existing_btn.setVisible(False)
            self._set_dep_status(self.abogen_status, "busy", "Abogen: installing… (see log)")
            self.prov = ProvisionWorker(self.cfg.get("abogen_runtime_dir", ""),
                                        self.cfg.get("abogen_pin", DEFAULT_PIN),
                                        detect_accelerator() != "cpu")
            self.prov.log.connect(self.log_view.appendPlainText)
            self.prov.done.connect(self._on_provisioned)
            self.prov.start()

    def _on_ffmpeg_downloaded(self, ok: bool, ff: str, fp: str):
        if ok and ff:
            self.cfg["ffmpeg_path"] = ff
            self.cfg["ffprobe_path"] = fp
            self._save_cfg(self.cfg)
            self._set_dep_status(self.ffmpeg_status, "ok", "FFmpeg: ✓ ready")
        else:
            self._set_dep_status(self.ffmpeg_status, "missing", "FFmpeg: ✗ download failed")
        self._dep_done()

    def _on_provisioned(self, ok: bool, info: str):
        if ok:
            self.cfg["abogen_python"] = ""  # use provisioned env
            self._save_cfg(self.cfg)
            self._set_dep_status(self.abogen_status, "ok", "Abogen: ✓ ready")
            abopy = resolve_abogen_python(self.cfg)
            if abopy:
                self._load_voices(abopy)
        else:
            self._set_dep_status(self.abogen_status, "missing", "Abogen: ✗ install failed")
            QMessageBox.warning(self, "Abogen setup failed", info)
        self._dep_done()

    def _dep_done(self):
        self._dep_jobs = getattr(self, "_dep_jobs", 1) - 1
        if self._dep_jobs <= 0:
            self.setup_btn.setEnabled(True)
            self._update_download_btn()

    # ---- inputs ----------------------------------------------------------
    def _add_paths(self, paths):
        for p in paths:
            pp = Path(p)
            if pp.is_file() and pp.suffix.lower() in INPUT_EXTS:
                self._add_input(str(pp))

    @staticmethod
    def _make_bar(color: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setMinimumWidth(130)
        bar.setFormat("%p%")
        bar.setStyleSheet(
            "QProgressBar{border:1px solid #bbb;border-radius:3px;text-align:center;height:16px;}"
            f"QProgressBar::chunk{{background-color:{color};border-radius:2px;}}")
        return bar

    def _add_input(self, path: str):
        if any(Path(x) == Path(path) for x in self.inputs):
            return
        self.inputs.append(path)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, InputTable.COL_TITLE, QTableWidgetItem(Path(path).stem))
        self.table.setItem(r, InputTable.COL_CHAPTERS, QTableWidgetItem("auto"))
        self.table.setItem(r, InputTable.COL_STATUS, QTableWidgetItem("Pending"))
        self.table.setCellWidget(r, InputTable.COL_AUDIO, self._make_bar(AUDIO_COLOR))
        self.table.setCellWidget(r, InputTable.COL_VIDEO, self._make_bar(VIDEO_COLOR))

    _STATUS_COLORS = {
        "Generating audio…": AUDIO_COLOR,
        "Rendering video…": VIDEO_COLOR,
        "Done": "#888888",
        "Failed": "#d23b3b",
        "Cancelled": "#d23b3b",
    }

    def _set_status(self, row: int, text: str):
        item = self.table.item(row, InputTable.COL_STATUS)
        if item is None:
            return
        item.setText(text)
        color = self._STATUS_COLORS.get(text)
        item.setForeground(QColor(color) if color else QColor())

    # ---- chapter selection ----------------------------------------------
    def _select_chapters(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(rows) != 1:
            QMessageBox.information(self, "Select one book",
                                   "Select a single book, then choose its chapters.")
            return
        abopy = resolve_abogen_python(self.cfg)
        if not abopy:
            QMessageBox.warning(self, "Abogen not ready",
                                "Set up the Abogen runtime first (button at the top).")
            return
        row = rows[0]
        inp = self.inputs[row]
        spec = build_spec(inp, str(Path(self.out_edit.text().strip() or ".") / "_audio"),
                          self.gather_abogen_cfg(), title=Path(inp).stem)
        self.table.item(row, InputTable.COL_CHAPTERS).setText("reading…")
        self.setEnabled(False)
        self.extractor = ExtractWorker(abopy, spec)
        self.extractor.chapters.connect(lambda chs, r=row, p=inp: self._on_chapters(r, p, chs))
        self.extractor.failed.connect(lambda msg, r=row: self._on_extract_failed(r, msg))
        self.extractor.start()

    def _on_chapters(self, row, inp, chapters):
        self.setEnabled(True)
        if not chapters:
            self.table.item(row, InputTable.COL_CHAPTERS).setText("—")
            QMessageBox.warning(self, "No chapters", "No chapters were found in this file.")
            return
        pre = self.selections.get(inp)
        dlg = ChapterSelectDialog(chapters, preselected=pre,
                                  title=f"Select chapters — {Path(inp).stem}", parent=self)
        if dlg.exec():
            chosen = dlg.selected_indices()
            self.selections[inp] = chosen
            self.table.item(row, InputTable.COL_CHAPTERS).setText(f"{len(chosen)} / {len(chapters)}")
        else:
            self.table.item(row, InputTable.COL_CHAPTERS).setText(
                f"{len(pre)} / {len(chapters)}" if pre is not None else "auto")

    def _on_extract_failed(self, row, msg):
        self.setEnabled(True)
        self.table.item(row, InputTable.COL_CHAPTERS).setText("error")
        QMessageBox.warning(self, "Could not read chapters", msg)

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose books", self.cfg.get("input_folder", ""),
            "Books (*.epub *.pdf *.txt *.md);;All files (*.*)")
        self._add_paths(files)

    def _remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.selections.pop(self.inputs[r], None)
            self.table.removeRow(r)
            del self.inputs[r]

    def _clear(self):
        self.table.setRowCount(0)
        self.inputs.clear()
        self.selections.clear()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Video output folder", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def _pick_audio_out(self):
        d = QFileDialog.getExistingDirectory(self, "Audio + subtitles folder", self.audio_edit.text())
        if d:
            self.audio_edit.setText(d)

    # ---- run -------------------------------------------------------------
    def _save_parallel(self, v: int):
        """Persist the Parallel value immediately (not only when a job starts)."""
        self.cfg["parallel_jobs"] = int(v)
        self._save_cfg(self.cfg)

    def _persist(self):
        self.cfg["abogen"] = self.gather_abogen_cfg()
        self.cfg["output_folder"] = self.out_edit.text().strip()
        self.cfg["pipeline_audio_folder"] = self.audio_edit.text().strip()
        self.cfg["parallel_jobs"] = self.parallel_spin.value()
        self._save_cfg(self.cfg)

    def _start(self):
        abopy = resolve_abogen_python(self.cfg)
        if not abopy:
            QMessageBox.warning(self, "Abogen not ready",
                                "Set up the Abogen runtime first (button at the top).")
            return
        if not self.inputs:
            QMessageBox.information(self, "Nothing to do", "Add at least one book.")
            return
        out_dir = self.out_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No output folder", "Choose a video output folder.")
            return
        self._persist()

        ffmpeg = find_executable("ffmpeg", self.cfg.get("ffmpeg_path", "")) or self.cfg.get("ffmpeg_path", "")
        ffprobe = find_executable("ffprobe", self.cfg.get("ffprobe_path", "")) or self.cfg.get("ffprobe_path", "")
        video = VideoSettings.from_dict(self.cfg.get("video", {}))

        for r in range(self.table.rowCount()):
            self._set_status(r, "Pending")
            self.table.cellWidget(r, InputTable.COL_AUDIO).setValue(0)
            self.table.cellWidget(r, InputTable.COL_VIDEO).setValue(0)

        audio_dir = self.audio_edit.text().strip() or out_dir
        self.worker = PipelineWorker(self.inputs, abopy, self.gather_abogen_cfg(), video,
                                     out_dir, ffmpeg, ffprobe,
                                     selections=dict(self.selections),
                                     parallel=self.parallel_spin.value(),
                                     audio_out_dir=audio_dir)
        self.worker.item_status.connect(self._set_status)
        self.worker.audio_progress.connect(
            lambda r, p: self.table.cellWidget(r, InputTable.COL_AUDIO).setValue(int(p)))
        self.worker.video_progress.connect(
            lambda r, p: self.table.cellWidget(r, InputTable.COL_VIDEO).setValue(int(p)))
        self.worker.item_done.connect(self._on_item_done)
        self.worker.log.connect(self.log_view.appendPlainText)
        self.worker.all_done.connect(self._on_all_done)
        self._done = 0
        self.overall.setMaximum(len(self.inputs))
        self.overall.setValue(0)
        self.overall.setFormat("%v / %m")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.worker.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)

    def _on_item_done(self, row, ok, info):
        self._done += 1
        self.overall.setValue(self._done)

    def _on_all_done(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.information(self, "Finished", "Pipeline complete.")
