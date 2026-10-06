"""
lora_table_editor.py (Qt)
Редактор списка LoRA вида ["name:strength", ...] для опций блока.

Вынесен из promptbuilder_tab.py на этапе R9 плана рефакторинга без
изменения кода класса. Здесь только Qt-обвязка: разбор и сборка самих
записей "name:strength" живут в logic.py (parse_lora_entry /
format_lora_entry).
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from comfyui_studio.prompt_builder.logic import format_lora_entry, parse_lora_entry
from comfyui_studio.prompt_builder.lora_combo import LoraFileCombo


class LoraTableEditor(QWidget):
    """Редактор списка LoRA вида ["name:strength", ...] для опций блока."""

    def __init__(self, on_change=None, loc=None, parent=None):
        super().__init__(parent)
        self.on_change = on_change
        self.loc = loc

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([self._tr("LoRA"), self._tr("Сила")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMaximumHeight(120)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.name_edit = LoraFileCombo()
        self.name_edit.setPlaceholderText(self._tr("имя LoRA"))
        self.strength_edit = QLineEdit("1.0")
        self.strength_edit.setMaximumWidth(60)
        self.add_btn = QPushButton(self._tr("+ Добавить"))
        self.add_btn.clicked.connect(self._add)
        self.remove_btn = QPushButton(self._tr("Удалить"))
        self.remove_btn.clicked.connect(self._remove)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.strength_edit)
        row.addWidget(self.add_btn)
        row.addWidget(self.remove_btn)
        layout.addLayout(row)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.table.setHorizontalHeaderLabels([self._tr("LoRA"), self._tr("Сила")])
        self.name_edit.setPlaceholderText(self._tr("имя LoRA"))
        self.add_btn.setText(self._tr("+ Добавить"))
        self.remove_btn.setText(self._tr("Удалить"))

    def set_entries(self, entries: list[str]):
        self.table.setRowCount(0)
        for entry in entries or []:
            name, strength = parse_lora_entry(entry)
            self._append_row(name, strength)

    def _append_row(self, name: str, strength: float):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(f"{strength:g}"))

    def get_entries(self) -> list[str]:
        result = []
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text()
            strength = self.table.item(row, 1).text()
            try:
                strength_f = float(strength)
            except ValueError:
                strength_f = 1.0
            result.append(format_lora_entry(name, strength_f))
        return result

    def _add(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        try:
            strength = float(self.strength_edit.text().strip() or "1.0")
        except ValueError:
            strength = 1.0
        self._append_row(name, strength)
        self.name_edit.clear()
        self.strength_edit.setText("1.0")
        if self.on_change:
            self.on_change()

    def _remove(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            self.table.removeRow(r)
        if self.on_change:
            self.on_change()
