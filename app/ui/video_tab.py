"""'Make Video' tab — batch ffmpeg rendering with toggleable settings.

This is Phase 1: turn existing .m4b + .ass pairs into visual-audiobook videos.
Defaults reproduce the user's preferred preset.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..video_builder import (
    VIDEO_CODECS, MediaPair, VideoSettings, available_encoders, default_video_codec,
    find_executable, pair_from_audio, presets_for, quality_label, scan_folder, AUDIO_EXTS,
)
from .render_worker import RenderWorker

COMMON_HEIGHTS = {"2160p (4K)": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}

# One-click speed presets -> (height, fps). Encoding speed scales with fps x pixels;
# captions on black don't need 24fps, so these are big, lossless-looking speedups.
SPEED_PRESETS = {
    "Best quality - 1080p / 24fps": (1080, 24),
    "Balanced - 1080p / 15fps (~1.6x faster)": (1080, 15),
    "Fast - 720p / 15fps (~2.7x faster)": (720, 15),
    "Fastest - 720p / 10fps (~4x faster)": (720, 10),
}
SPEED_CUSTOM = "Custom"


class DropTable(QTableWidget):
    """Table that accepts dropped files/folders."""

    def __init__(self, on_paths, parent=None):
        super().__init__(0, 4, parent)
        self._on_paths = on_paths
        self.setHorizontalHeaderLabels(["Title", "Subs", "Status", "Progress"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self._on_paths(paths)


class VideoTab(QWidget):
    def __init__(self, cfg: dict, save_cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._save_cfg = save_cfg
        self.pairs: list[MediaPair] = []
        self.worker: RenderWorker | None = None
        self._encoders = set()

        ffmpeg = find_executable("ffmpeg", cfg.get("ffmpeg_path", "")) or cfg.get("ffmpeg_path", "")
        if ffmpeg:
            self._encoders = available_encoders(ffmpeg)

        self._build_ui()
        self.apply_settings(VideoSettings.from_dict(cfg.get("video", {})))
        self.out_edit.setText(cfg.get("output_folder", ""))

    # ---- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left: input list + buttons
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Drag .m4b/.ass here, or use the buttons below.")
        hint.setStyleSheet("color:#888;")
        lv.addWidget(hint)
        self.table = DropTable(self._add_paths)
        lv.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, slot in [
            ("Add files…", self._pick_files),
            ("Add folder…", self._pick_folder),
            ("Scan audio folder", self._scan_default),
            ("Remove", self._remove_selected),
            ("Clear", self._clear),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        lv.addLayout(btns)
        splitter.addWidget(left)

        # Right: settings
        splitter.addWidget(self._build_settings())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Output folder row
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output folder:"))
        self.out_edit = QLineEdit()
        out_row.addWidget(self.out_edit, 1)
        ob = QPushButton("Browse…")
        ob.clicked.connect(self._pick_out)
        out_row.addWidget(ob)
        root.addLayout(out_row)

        # Run row
        run_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        self.overall = QProgressBar()
        self.overall.setTextVisible(True)
        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(QLabel("Parallel:"))
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 12)
        self.parallel_spin.setToolTip("How many videos to encode at once (scales well to ~12; 14 can crash NVENC)")
        self.parallel_spin.setValue(int(self.cfg.get("parallel_jobs", 3)))
        run_row.addWidget(self.parallel_spin)
        run_row.addWidget(self.overall, 1)
        root.addLayout(run_row)

        # Log
        self.log_toggle = QPushButton("Show log ▾")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        root.addWidget(self.log_toggle)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setVisible(False)
        self.log_view.setFixedHeight(160)
        root.addWidget(self.log_view)

    def _build_settings(self) -> QWidget:
        box = QGroupBox("Video settings")
        form = QFormLayout(box)
        self._loading_preset = False

        self.speed_combo = QComboBox()
        self.speed_combo.addItems(list(SPEED_PRESETS) + [SPEED_CUSTOM])
        self.speed_combo.activated.connect(self._apply_speed_preset)
        form.addRow("Speed preset:", self.speed_combo)

        self.res_combo = QComboBox()
        self.res_combo.addItems(COMMON_HEIGHTS.keys())
        self.res_combo.setCurrentText("1080p")
        self.res_combo.currentTextChanged.connect(self._mark_custom_speed)
        form.addRow("Resolution:", self.res_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.setEditable(True)
        self.fps_combo.addItems(["10", "12", "15", "24", "25", "30", "60"])
        self.fps_combo.currentTextChanged.connect(self._mark_custom_speed)
        form.addRow("Frame rate:", self.fps_combo)

        self.codec_combo = QComboBox()
        for label, enc in VIDEO_CODECS.items():
            if enc in self._encoders or not self._encoders:
                self.codec_combo.addItem(label, enc)
        self.codec_combo.currentIndexChanged.connect(self._codec_changed)
        form.addRow("Video codec:", self.codec_combo)

        self.preset_combo = QComboBox()
        form.addRow("Encoder preset:", self.preset_combo)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 100)  # CRF/CQ ~0-63; VideoToolbox -q:v ~1-100
        self.quality_label = QLabel("Quality (CQ):")
        form.addRow(self.quality_label, self.quality_spin)

        self.pix_combo = QComboBox()
        self.pix_combo.addItems(["yuv420p", "yuv420p10le", "yuv444p"])
        form.addRow("Pixel format:", self.pix_combo)

        # Audio
        self.audio_combo = QComboBox()
        self.audio_combo.addItems(["opus", "aac", "copy"])
        self.audio_combo.currentTextChanged.connect(self._audio_changed)
        form.addRow("Audio:", self.audio_combo)
        self.abitrate_combo = QComboBox()
        self.abitrate_combo.setEditable(True)
        self.abitrate_combo.addItems(["32k", "48k", "64k", "96k", "128k"])
        form.addRow("Audio bitrate:", self.abitrate_combo)
        self.achan_combo = QComboBox()
        self.achan_combo.addItem("Mono", 1)
        self.achan_combo.addItem("Stereo", 2)
        form.addRow("Audio channels:", self.achan_combo)

        # Background
        self.bg_combo = QComboBox()
        self.bg_combo.addItems(["black", "color", "image"])
        self.bg_combo.currentTextChanged.connect(self._bg_changed)
        form.addRow("Background:", self.bg_combo)
        self.color_btn = QPushButton("Pick color…")
        self.color_btn.clicked.connect(self._pick_color)
        self._bg_color = "0x000000"
        form.addRow("", self.color_btn)
        img_row = QHBoxLayout()
        self.image_edit = QLineEdit()
        img_btn = QPushButton("…")
        img_btn.setFixedWidth(30)
        img_btn.clicked.connect(self._pick_image)
        img_row.addWidget(self.image_edit)
        img_row.addWidget(img_btn)
        self.image_row_w = QWidget()
        self.image_row_w.setLayout(img_row)
        form.addRow("Image:", self.image_row_w)

        # Toggles
        self.burn_chk = QCheckBox("Burn subtitles into the video")
        self.burn_chk.setChecked(True)
        form.addRow(self.burn_chk)
        self.chapters_chk = QCheckBox("Carry chapter markers into the video")
        self.chapters_chk.setChecked(True)
        form.addRow(self.chapters_chk)
        self.overwrite_chk = QCheckBox("Overwrite existing output files")
        self.overwrite_chk.setChecked(True)
        form.addRow(self.overwrite_chk)

        self.style_edit = QLineEdit()
        self.style_edit.setPlaceholderText("optional, e.g. Fontsize=42,Outline=2")
        form.addRow("Subtitle style override:", self.style_edit)

        self.name_preview = QLabel("")
        self.name_preview.setStyleSheet("color:#888; font-size:11px;")
        self.name_preview.setWordWrap(True)
        form.addRow("Output name:", self.name_preview)

        for w in (self.res_combo, self.fps_combo, self.codec_combo, self.preset_combo,
                  self.quality_spin, self.audio_combo, self.abitrate_combo):
            if isinstance(w, QComboBox):
                w.currentTextChanged.connect(self._update_preview)
            else:
                w.valueChanged.connect(self._update_preview)
        return box

    # ---- settings <-> widgets -------------------------------------------
    def gather_settings(self) -> VideoSettings:
        s = VideoSettings()
        s.height = COMMON_HEIGHTS[self.res_combo.currentText()]
        s.width = round(s.height * 16 / 9 / 2) * 2
        try:
            s.fps = int(float(self.fps_combo.currentText()))
        except ValueError:
            s.fps = 24
        s.video_codec = self.codec_combo.currentData() or default_video_codec()
        s.preset = self.preset_combo.currentText()
        s.quality = self.quality_spin.value()
        s.pix_fmt = self.pix_combo.currentText()
        s.audio_mode = self.audio_combo.currentText()
        s.audio_bitrate = self.abitrate_combo.currentText()
        s.audio_channels = self.achan_combo.currentData()
        s.background = self.bg_combo.currentText()
        s.background_color = self._bg_color
        s.background_image = self.image_edit.text().strip()
        s.burn_subtitles = self.burn_chk.isChecked()
        s.subtitle_style_override = self.style_edit.text().strip()
        s.carry_chapters = self.chapters_chk.isChecked()
        s.overwrite = self.overwrite_chk.isChecked()
        s.container = "mkv" if not s.burn_subtitles else "mp4"
        return s

    def apply_settings(self, s: VideoSettings) -> None:
        for label, h in COMMON_HEIGHTS.items():
            if h == s.height:
                self.res_combo.setCurrentText(label)
        self.fps_combo.setCurrentText(str(s.fps))
        idx = self.codec_combo.findData(s.video_codec)
        if idx >= 0:
            self.codec_combo.setCurrentIndex(idx)
        self._codec_changed()
        self.preset_combo.setCurrentText(s.preset)
        self.quality_spin.setValue(s.quality)
        self.pix_combo.setCurrentText(s.pix_fmt)
        self.audio_combo.setCurrentText(s.audio_mode)
        self.abitrate_combo.setCurrentText(s.audio_bitrate)
        self.achan_combo.setCurrentIndex(0 if s.audio_channels == 1 else 1)
        self.bg_combo.setCurrentText(s.background)
        self._bg_color = s.background_color
        self.image_edit.setText(s.background_image)
        self.burn_chk.setChecked(s.burn_subtitles)
        self.chapters_chk.setChecked(s.carry_chapters)
        self.overwrite_chk.setChecked(s.overwrite)
        self.style_edit.setText(s.subtitle_style_override)
        self._bg_changed(s.background)
        self._sync_speed_combo()
        self._update_preview()

    # ---- speed presets ---------------------------------------------------
    def _apply_speed_preset(self, *_):
        name = self.speed_combo.currentText()
        if name not in SPEED_PRESETS:
            return
        height, fps = SPEED_PRESETS[name]
        self._loading_preset = True
        for label, h in COMMON_HEIGHTS.items():
            if h == height:
                self.res_combo.setCurrentText(label)
        self.fps_combo.setCurrentText(str(fps))
        self._loading_preset = False
        self._update_preview()

    def _mark_custom_speed(self, *_):
        if not self._loading_preset:
            self.speed_combo.setCurrentText(SPEED_CUSTOM)

    def _sync_speed_combo(self):
        try:
            height = COMMON_HEIGHTS[self.res_combo.currentText()]
            fps = int(float(self.fps_combo.currentText()))
        except (KeyError, ValueError):
            self.speed_combo.setCurrentText(SPEED_CUSTOM)
            return
        for name, (h, f) in SPEED_PRESETS.items():
            if h == height and f == fps:
                self.speed_combo.setCurrentText(name)
                return
        self.speed_combo.setCurrentText(SPEED_CUSTOM)

    # ---- reactions -------------------------------------------------------
    def _codec_changed(self, *_):
        codec = self.codec_combo.currentData() or default_video_codec()
        presets = presets_for(codec)
        cur = self.preset_combo.currentText()
        self.preset_combo.clear()
        if presets:
            self.preset_combo.addItems(presets)
            self.preset_combo.setEnabled(True)
            self.preset_combo.setCurrentText(cur if cur in presets else
                                             ("p5" if "p5" in presets else presets[len(presets) // 2]))
        else:
            self.preset_combo.setEnabled(False)
        self.quality_label.setText(f"Quality ({quality_label(codec)}):")
        self._update_preview()

    def _audio_changed(self, mode: str):
        is_copy = mode == "copy"
        self.abitrate_combo.setEnabled(not is_copy)
        self.achan_combo.setEnabled(not is_copy)

    def _bg_changed(self, mode: str):
        self.color_btn.setVisible(mode == "color")
        self.image_row_w.setVisible(mode == "image")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(0, 0, 0), self, "Background color")
        if c.isValid():
            self._bg_color = "0x%02X%02X%02X" % (c.red(), c.green(), c.blue())
            self.color_btn.setText(f"Color: {self._bg_color}")

    def _update_preview(self, *_):
        try:
            from ..video_builder import output_name
            self.name_preview.setText(output_name("<title>", self.gather_settings()))
        except Exception:  # noqa: BLE001
            pass

    # ---- input management ------------------------------------------------
    def _add_paths(self, paths):
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                for pair in scan_folder(str(pp)):
                    self._add_pair(pair)
            elif pp.suffix.lower() in AUDIO_EXTS:
                self._add_pair(pair_from_audio(str(pp)))
        self._update_preview()

    def _add_pair(self, pair: MediaPair):
        if any(Path(x.audio) == Path(pair.audio) for x in self.pairs):
            return
        self.pairs.append(pair)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(pair.base))
        self.table.setItem(r, 1, QTableWidgetItem("✓" if pair.has_subs else "—"))
        self.table.setItem(r, 2, QTableWidgetItem("Pending"))
        bar = QProgressBar()
        bar.setValue(0)
        self.table.setCellWidget(r, 3, bar)

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose audio files", self.cfg.get("audio_folder", ""),
            "Audio (*.m4b *.m4a *.mp3 *.opus *.flac *.wav);;All files (*.*)")
        self._add_paths(files)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.cfg.get("audio_folder", ""))
        if d:
            self._add_paths([d])

    def _scan_default(self):
        self._add_paths([self.cfg.get("audio_folder", "")])

    def _remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
            del self.pairs[r]

    def _clear(self):
        self.table.setRowCount(0)
        self.pairs.clear()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def _pick_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "Background image", "",
                                           "Images (*.png *.jpg *.jpeg *.bmp)")
        if f:
            self.image_edit.setText(f)

    def _toggle_log(self, on: bool):
        self.log_view.setVisible(on)
        self.log_toggle.setText("Hide log ▴" if on else "Show log ▾")

    # ---- run -------------------------------------------------------------
    def _persist(self):
        self.cfg["video"] = self.gather_settings().to_dict()
        self.cfg["output_folder"] = self.out_edit.text().strip()
        self.cfg["parallel_jobs"] = self.parallel_spin.value()
        self._save_cfg(self.cfg)

    def _start(self):
        if not self.pairs:
            QMessageBox.information(self, "Nothing to do", "Add at least one audio file.")
            return
        out_dir = self.out_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No output folder", "Choose an output folder.")
            return
        missing = [p.base for p in self.pairs if not p.has_subs]
        s = self.gather_settings()
        if missing and s.burn_subtitles:
            ok = QMessageBox.question(
                self, "Missing subtitles",
                "These have no subtitle file and will render without captions:\n  "
                + "\n  ".join(missing[:10]) + ("\n  …" if len(missing) > 10 else ""))
            if ok != QMessageBox.StandardButton.Yes:
                return
        self._persist()

        ffmpeg = find_executable("ffmpeg", self.cfg.get("ffmpeg_path", "")) or self.cfg.get("ffmpeg_path", "")
        ffprobe = find_executable("ffprobe", self.cfg.get("ffprobe_path", "")) or self.cfg.get("ffprobe_path", "")
        for r in range(self.table.rowCount()):
            self.table.item(r, 2).setText("Pending")
            self.table.cellWidget(r, 3).setValue(0)

        self.worker = RenderWorker(self.pairs, s, out_dir, ffmpeg, ffprobe,
                                   parallel=self.parallel_spin.value())
        self.worker.item_status.connect(self._on_status)
        self.worker.item_progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_done)
        self.worker.log.connect(self._on_log)
        self.worker.all_done.connect(self._on_all_done)
        self._done_count = 0
        self.overall.setMaximum(len(self.pairs))
        self.overall.setValue(0)
        self.overall.setFormat("%v / %m")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.worker.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)

    def _on_status(self, row, text):
        self.table.item(row, 2).setText(text)

    def _on_progress(self, row, pct):
        self.table.cellWidget(row, 3).setValue(int(pct))

    def _on_done(self, row, ok, info):
        self._done_count += 1
        self.overall.setValue(self._done_count)
        if not ok:
            self.table.item(row, 2).setText("Failed")

    def _on_log(self, text):
        self.log_view.appendPlainText(text)

    def _on_all_done(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.information(self, "Finished", "All renders complete.")
