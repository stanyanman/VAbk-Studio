"""Settings tab — tool paths and default folders."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ..settings import default_config
from ..video_builder import (
    available_encoders, default_video_settings, find_executable,
)
from .pipeline_worker import FfmpegWorker


class _PathRow(QWidget):
    def __init__(self, value, pick_dir=False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        lay.addWidget(self.edit, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse_dir if pick_dir else self._browse_file)
        lay.addWidget(btn)

    def _browse_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose executable", self.edit.text(),
                                           "Executable (*.exe);;All files (*.*)")
        if f:
            self.edit.setText(f)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text())
        if d:
            self.edit.setText(d)


class SettingsTab(QWidget):
    def __init__(self, cfg: dict, save_cfg, on_reset_video, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._save_cfg = save_cfg
        self._on_reset_video = on_reset_video

        root = QVBoxLayout(self)
        tools = QGroupBox("Tool paths")
        f = QFormLayout(tools)
        self.ffmpeg = _PathRow(cfg.get("ffmpeg_path", ""))
        self.ffprobe = _PathRow(cfg.get("ffprobe_path", ""))
        f.addRow("ffmpeg:", self.ffmpeg)
        f.addRow("ffprobe:", self.ffprobe)
        self.enc_label = QLabel("")
        self.enc_label.setStyleSheet("color:#888;")
        f.addRow("Detected:", self.enc_label)
        self.ffmpeg_btn = QPushButton("Set up ffmpeg (auto-download)")
        self.ffmpeg_btn.setToolTip("Download a pinned static ffmpeg into the app's data "
                                   "folder if one isn't already on PATH. Leave the paths "
                                   "blank to auto-resolve (PATH, then this download).")
        self.ffmpeg_btn.clicked.connect(self._setup_ffmpeg)
        f.addRow("", self.ffmpeg_btn)
        root.addWidget(tools)

        folders = QGroupBox("Default folders")
        f2 = QFormLayout(folders)
        self.audio_dir = _PathRow(cfg.get("audio_folder", ""), pick_dir=True)
        self.input_dir = _PathRow(cfg.get("input_folder", ""), pick_dir=True)
        self.out_dir = _PathRow(cfg.get("output_folder", ""), pick_dir=True)
        f2.addRow("Audio (.m4b/.ass):", self.audio_dir)
        f2.addRow("EPUB input:", self.input_dir)
        f2.addRow("Video output:", self.out_dir)
        root.addWidget(folders)

        row = QHBoxLayout()
        save = QPushButton("Save settings")
        save.clicked.connect(self._save)
        reset = QPushButton("Restore preset defaults")
        reset.clicked.connect(self._reset)
        row.addWidget(save)
        row.addWidget(reset)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        self._refresh_encoders()

    def _refresh_encoders(self):
        ff = find_executable("ffmpeg", self.ffmpeg.edit.text())
        if ff:
            encs = available_encoders(ff)
            wanted = [e for e in ("hevc_nvenc", "h264_nvenc", "av1_nvenc",
                                  "hevc_videotoolbox", "h264_videotoolbox",
                                  "libx265", "libx264", "libaom-av1", "libopus") if e in encs]
            self.enc_label.setText(", ".join(wanted) or "none found")
        else:
            self.enc_label.setText("ffmpeg not found — click 'Set up ffmpeg' below")

    def _save(self):
        self.cfg["ffmpeg_path"] = self.ffmpeg.edit.text().strip()
        self.cfg["ffprobe_path"] = self.ffprobe.edit.text().strip()
        self.cfg["audio_folder"] = self.audio_dir.edit.text().strip()
        self.cfg["input_folder"] = self.input_dir.edit.text().strip()
        self.cfg["output_folder"] = self.out_dir.edit.text().strip()
        self._save_cfg(self.cfg)
        self._refresh_encoders()

    def _setup_ffmpeg(self):
        if getattr(self, "_ff_worker", None) and self._ff_worker.isRunning():
            return
        self.ffmpeg_btn.setEnabled(False)
        self.enc_label.setText("Setting up ffmpeg…")
        self._ff_worker = FfmpegWorker(self.ffmpeg.edit.text().strip(),
                                       self.ffprobe.edit.text().strip())
        self._ff_worker.log.connect(self.enc_label.setText)
        self._ff_worker.done.connect(self._on_ffmpeg_done)
        self._ff_worker.start()

    def _on_ffmpeg_done(self, ok: bool, ff: str, fp: str):
        self.ffmpeg_btn.setEnabled(True)
        if ok and ff:
            self.ffmpeg.edit.setText(ff)
            self.ffprobe.edit.setText(fp)
            self.cfg["ffmpeg_path"] = ff
            self.cfg["ffprobe_path"] = fp
            self._save_cfg(self.cfg)
        self._refresh_encoders()

    def _reset(self):
        s = default_video_settings()
        self.cfg["video"] = s.to_dict()
        self._save_cfg(self.cfg)
        self._on_reset_video(s)
