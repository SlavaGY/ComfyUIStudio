"""
named_text_list_editor.py (Qt)
Редактор структуры {name: long_text, ...} (+ опционально default) —
используется для negative_presets.

Вынесен из promptbuilder_tab.py на этапе R9 плана рефакторинга без
изменения кода класса; единственное отличие — имя: было приватное
_NamedTextListEditor, в отдельном модуле оно стало публичным.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPushButton, QTextEdit, QVBoxLayout,
)


class NamedTextListEditor(QGroupBox):
    """Редактор структуры {name: long_text, ...} (+ опционально default) —
    используется для negative_presets."""

    def __init__(self, title: str, on_dirty, with_default: bool = False,
                 default_label: str = "По умолчанию:", loc=None, parent=None):
        super().__init__(title, parent)
        self.on_dirty = on_dirty
        self.with_default = with_default
        self.loc = loc
        self._data: dict[str, str] = {}

        layout = QHBoxLayout(self)

        self.listbox = QListWidget()
        self.listbox.setMaximumWidth(220)
        self.listbox.currentRowChanged.connect(self._on_select)
        layout.addWidget(self.listbox)

        right = QVBoxLayout()
        name_row = QHBoxLayout()
        self.name_label = QLabel(self._tr("Название пресета:"))
        name_row.addWidget(self.name_label)
        self.name_edit = QLineEdit()
        name_row.addWidget(self.name_edit)
        right.addLayout(name_row)

        self.text_edit = QTextEdit()
        self.text_edit.setAcceptRichText(False)
        right.addWidget(self.text_edit)

        btns = QHBoxLayout()
        self.add_btn = QPushButton(self._tr("+ Добавить"))
        self.add_btn.clicked.connect(self._add)
        self.upd_btn = QPushButton(self._tr("Обновить"))
        self.upd_btn.clicked.connect(self._update)
        self.del_btn = QPushButton(self._tr("Удалить"))
        self.del_btn.setObjectName("dangerButton")
        self.del_btn.clicked.connect(self._delete)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.upd_btn)
        btns.addWidget(self.del_btn)
        right.addLayout(btns)

        self._default_label_text = default_label
        if with_default:
            self.default_row_label = QLabel(default_label)
            default_row = QHBoxLayout()
            default_row.addWidget(self.default_row_label)
            self.default_combo = QComboBox()
            self.default_combo.currentTextChanged.connect(lambda _t: self.on_dirty())
            default_row.addWidget(self.default_combo)
            right.addLayout(default_row)

        layout.addLayout(right, 1)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self, title: str, default_label: Optional[str] = None):
        self.setTitle(title)
        self.name_label.setText(self._tr("Название пресета:"))
        self.add_btn.setText(self._tr("+ Добавить"))
        self.upd_btn.setText(self._tr("Обновить"))
        self.del_btn.setText(self._tr("Удалить"))
        if self.with_default and default_label is not None:
            self.default_row_label.setText(default_label)

    def _on_select(self, row: int):
        if row < 0 or row >= self.listbox.count():
            return
        name = self.listbox.item(row).text()
        self.name_edit.setText(name)
        self.text_edit.setPlainText(self._data.get(name, ""))

    def _refresh_list(self, keep: Optional[str] = None):
        self.listbox.clear()
        for name in self._data.keys():
            self.listbox.addItem(name)
        if self.with_default:
            current = self.default_combo.currentText()
            self.default_combo.blockSignals(True)
            self.default_combo.clear()
            self.default_combo.addItems(list(self._data.keys()))
            if current in self._data:
                self.default_combo.setCurrentText(current)
            self.default_combo.blockSignals(False)
        if keep and keep in self._data:
            self.listbox.setCurrentRow(list(self._data.keys()).index(keep))

    def _add(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self._data[name] = self.text_edit.toPlainText()
        self._refresh_list(keep=name)
        self.on_dirty()

    def _update(self):
        row = self.listbox.currentRow()
        if row < 0:
            return
        old_name = self.listbox.item(row).text()
        new_name = self.name_edit.text().strip() or old_name
        text = self.text_edit.toPlainText()
        if new_name != old_name:
            ordered = {}
            for k, v in self._data.items():
                ordered[new_name if k == old_name else k] = (text if k == old_name else v)
            self._data = ordered
        else:
            self._data[old_name] = text
        self._refresh_list(keep=new_name)
        self.on_dirty()

    def _delete(self):
        row = self.listbox.currentRow()
        if row < 0:
            return
        name = self.listbox.item(row).text()
        self._data.pop(name, None)
        self._refresh_list()
        self.on_dirty()

    def load(self, data: dict, default: str = ""):
        self._data = dict(data or {})
        self._refresh_list()
        if self.with_default and default:
            self.default_combo.setCurrentText(default)

    def to_raw(self):
        if self.with_default:
            return dict(self._data), self.default_combo.currentText()
        return dict(self._data)
