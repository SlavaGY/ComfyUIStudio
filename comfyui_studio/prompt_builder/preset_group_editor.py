"""
preset_group_editor.py (Qt)
Редактор структуры {"presets": {label: tags}, "default": label} —
используется для quality_prefix и source.

Вынесен из promptbuilder_tab.py на этапе R9 плана рефакторинга без
изменения кода класса; единственное отличие — имя: было приватное
_PresetGroupEditor, в отдельном модуле оно стало публичным.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class PresetGroupEditor(QGroupBox):
    """Редактор структуры {"presets": {label: tags}, "default": label} —
    используется для quality_prefix и source."""

    def __init__(self, title: str, on_dirty, loc=None, parent=None):
        super().__init__(title, parent)
        self.on_dirty = on_dirty
        self.loc = loc
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([self._tr("Название"), self._tr("Теги")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table)

        form = QHBoxLayout()
        self.name_label = QLabel(self._tr("Название:"))
        form.addWidget(self.name_label)
        self.label_edit = QLineEdit()
        form.addWidget(self.label_edit)
        layout.addLayout(form)

        form2 = QHBoxLayout()
        self.tags_label = QLabel(self._tr("Теги:"))
        form2.addWidget(self.tags_label)
        self.tags_edit = QLineEdit()
        form2.addWidget(self.tags_edit)
        layout.addLayout(form2)

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
        layout.addLayout(btns)

        default_row = QHBoxLayout()
        self.default_label = QLabel(self._tr("По умолчанию:"))
        default_row.addWidget(self.default_label)
        self.default_combo = QComboBox()
        self.default_combo.currentTextChanged.connect(lambda _t: self.on_dirty())
        default_row.addWidget(self.default_combo)
        layout.addLayout(default_row)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self, title: str):
        self.setTitle(title)
        self.table.setHorizontalHeaderLabels([self._tr("Название"), self._tr("Теги")])
        self.name_label.setText(self._tr("Название:"))
        self.tags_label.setText(self._tr("Теги:"))
        self.add_btn.setText(self._tr("+ Добавить"))
        self.upd_btn.setText(self._tr("Обновить"))
        self.del_btn.setText(self._tr("Удалить"))
        self.default_label.setText(self._tr("По умолчанию:"))

    def _on_select(self):
        rows = self.table.selectedItems()
        if not rows:
            return
        row = self.table.currentRow()
        self.label_edit.setText(self.table.item(row, 0).text())
        self.tags_edit.setText(self.table.item(row, 1).text())

    def _refresh_default_options(self):
        current = self.default_combo.currentText()
        labels = [self.table.item(r, 0).text() for r in range(self.table.rowCount())]
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItems(labels)
        if current in labels:
            self.default_combo.setCurrentText(current)
        self.default_combo.blockSignals(False)

    def _add(self):
        label = self.label_edit.text().strip()
        if not label:
            return
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == label:
                QMessageBox.information(
                    self, self._tr("Пресет"),
                    self._tr("Пресет '{}' уже существует, используйте «Обновить».").format(label),
                )
                return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(label))
        self.table.setItem(row, 1, QTableWidgetItem(self.tags_edit.text()))
        self._refresh_default_options()
        self.on_dirty()

    def _update(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.setItem(row, 0, QTableWidgetItem(self.label_edit.text().strip()))
        self.table.setItem(row, 1, QTableWidgetItem(self.tags_edit.text()))
        self._refresh_default_options()
        self.on_dirty()

    def _delete(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.removeRow(row)
        self._refresh_default_options()
        self.on_dirty()

    def load(self, data: dict):
        self.table.setRowCount(0)
        for label, tags in (data.get("presets") or {}).items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(tags))
        self._refresh_default_options()
        default = data.get("default", "")
        if default:
            self.default_combo.setCurrentText(default)

    def to_raw(self) -> dict:
        presets = {}
        for r in range(self.table.rowCount()):
            presets[self.table.item(r, 0).text()] = self.table.item(r, 1).text()
        return {"presets": presets, "default": self.default_combo.currentText()}
