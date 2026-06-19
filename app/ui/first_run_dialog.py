"""First-run dialog: choose the workspace base where Input / Output / Visual
Audiobooks folders are created. Defaults to inside the app folder."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)


class FirstRunDialog(QDialog):
    """Pick a single workspace base folder. `chosen_base()` returns the selection."""

    def __init__(self, default_base: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to VAbk Studio")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)

        intro = QLabel(
            "<b>Where should VAbk Studio keep your files?</b><br><br>"
            "It will create three folders inside the location you choose:")
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        folders = QLabel(
            "&nbsp;&nbsp;• <b>Input</b> — your EPUB / PDF / TXT books<br>"
            "&nbsp;&nbsp;• <b>Output</b> — generated .m4b audio + .ass captions<br>"
            "&nbsp;&nbsp;• <b>Visual Audiobooks</b> — the finished .mp4 videos")
        folders.setTextFormat(Qt.TextFormat.RichText)
        folders.setWordWrap(True)
        root.addWidget(folders)

        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.edit = QLineEdit(default_base)
        row.addWidget(self.edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        root.addLayout(row)

        hint = QLabel("You can change this anytime on the Settings tab.")
        hint.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create folders & continue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose workspace folder", self.edit.text())
        if d:
            self.edit.setText(d)

    def chosen_base(self) -> str:
        return self.edit.text().strip()
