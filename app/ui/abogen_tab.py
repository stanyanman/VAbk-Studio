"""'Abogen' tab — manual EPUB/PDF/TXT -> .m4b audio + .ass subtitles (no video).

A focused, audio-only sibling of the Full Pipeline tab: it runs only Abogen and
drops the finished .m4b + .ass into the chosen output folder. It subclasses
PipelineTab to reuse the runtime/provisioning, voice-loading, chapter-selection
and input-table machinery, overriding only the layout and the run step (which
uses PipelineWorker with audio_only=True, so the ffmpeg render is skipped).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..provisioning import accelerator_label, resolve_abogen_python
from .pipeline_tab import (
    COMMON_VOICES, LANGUAGES, SUBTITLE_MODES, InputTable, PipelineTab,
)
from .pipeline_worker import PipelineWorker


class AbogenTab(PipelineTab):
    """Audio-only tab: Abogen generates .m4b + .ass; no ffmpeg step.

    Inherits the runtime status / provisioning, voice loading, chapter selection
    and input-table handling from PipelineTab; only the layout and the run step
    differ (no video folder, no video column, audio_only worker).
    """

    # ---- UI --------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        # Runtime status row (identical to Full Pipeline)
        self._status_row = QHBoxLayout()
        self.status_label = QLabel("Checking Abogen runtime…")
        self.setup_btn = QPushButton("Set up / Update Abogen")
        self.setup_btn.clicked.connect(self._setup_abogen)
        self._status_row.addWidget(self.status_label, 1)
        self._status_row.addWidget(self.setup_btn)
        root.addLayout(self._status_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Drag EPUB / PDF / TXT here, or use the buttons below.")
        hint.setStyleSheet("color:#888;")
        lv.addWidget(hint)
        self.table = InputTable(self._add_paths)
        self.table.setColumnHidden(InputTable.COL_VIDEO, True)  # audio-only: hide video column
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

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output folder (.m4b + .ass):"))
        self.out_edit = QLineEdit(self.cfg.get("abogen_output_folder", ""))
        out_row.addWidget(self.out_edit, 1)
        ob = QPushButton("Browse…")
        ob.clicked.connect(self._pick_out)
        out_row.addWidget(ob)
        root.addLayout(out_row)

        run_row = QHBoxLayout()
        self.start_btn = QPushButton("Generate audiobooks")
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
        self.parallel_spin.setRange(1, 12)
        self.parallel_spin.setToolTip("How many books to process at once (sweet spot ~8; >12 degrades)")
        self.parallel_spin.setValue(int(self.cfg.get("parallel_jobs", 3)))
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
        codes = [c for c, _ in LANGUAGES]
        cur_lang = ab.get("language", "a")
        self.lang_combo.setCurrentIndex(codes.index(cur_lang) if cur_lang in codes else 0)
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

        note = QLabel(
            "Output: M4B (with chapters) + ASS (centered wide). "
            "Audio only — no video is rendered on this tab.<br>"
            'Narration &amp; captions by '
            '<a href="https://github.com/denizsafak/abogen">Abogen</a> '
            "(Deniz Şafak · MIT).")
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setOpenExternalLinks(True)
        note.setStyleSheet("color:#888; font-size:11px;")
        note.setWordWrap(True)
        form.addRow(note)
        return box

    # ---- run -------------------------------------------------------------
    def _persist(self):
        self.cfg["abogen"] = self.gather_abogen_cfg()
        self.cfg["abogen_output_folder"] = self.out_edit.text().strip()
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
            QMessageBox.warning(self, "No output folder", "Choose an output folder.")
            return
        self._persist()

        for r in range(self.table.rowCount()):
            self._set_status(r, "Pending")
            self.table.cellWidget(r, InputTable.COL_AUDIO).setValue(0)

        # video_settings/ffmpeg/ffprobe are unused on the audio_only path.
        self.worker = PipelineWorker(
            self.inputs, abopy, self.gather_abogen_cfg(), None,
            out_dir, "", "",
            selections=dict(self.selections),
            parallel=self.parallel_spin.value(),
            audio_out_dir=out_dir, audio_only=True)
        self.worker.item_status.connect(self._set_status)
        self.worker.audio_progress.connect(
            lambda r, p: self.table.cellWidget(r, InputTable.COL_AUDIO).setValue(int(p)))
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

    def _on_all_done(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.information(self, "Finished", "Audio generation complete.")
