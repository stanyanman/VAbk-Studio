"""Dialog to choose which chapters of a book to convert (mirrors Abogen's picker)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)


class ChapterSelectDialog(QDialog):
    """chapters: list of {index, title, characters, enabled, preview}.
    preselected: set/list of indices to check, or None to use each chapter's default.
    """

    def __init__(self, chapters: list[dict], preselected=None, title="Select chapters", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(880, 560)
        self._chapters = chapters
        pre = set(preselected) if preselected is not None else None

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        for text, slot in [("Select all", self._select_all),
                           ("Clear", self._clear),
                           ("Restore defaults", self._defaults)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        top.addStretch(1)
        self.count_label = QLabel("")
        top.addWidget(self.count_label)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        for ch in chapters:
            idx = ch["index"]
            chars = ch.get("characters", 0)
            item = QListWidgetItem(f"{ch.get('title', f'Chapter {idx+1}')}   ·   {chars:,} chars")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = (idx in pre) if pre is not None else bool(ch.get("enabled", True))
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._show_preview)
        self.list.itemChanged.connect(lambda *_: self._update_count())
        split.addWidget(self.list)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if chapters:
            self.list.setCurrentRow(0)
        self._update_count()

    def _show_preview(self, row: int):
        if 0 <= row < len(self._chapters):
            self.preview.setPlainText(self._chapters[row].get("preview", "") or "(no preview)")

    def _set_all(self, state: Qt.CheckState):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _select_all(self):
        self._set_all(Qt.CheckState.Checked)

    def _clear(self):
        self._set_all(Qt.CheckState.Unchecked)

    def _defaults(self):
        for i in range(self.list.count()):
            default_on = bool(self._chapters[i].get("enabled", True))
            self.list.item(i).setCheckState(
                Qt.CheckState.Checked if default_on else Qt.CheckState.Unchecked)

    def _update_count(self):
        n = sum(1 for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked)
        self.count_label.setText(f"{n} / {self.list.count()} selected")

    def selected_indices(self) -> list[int]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return out
