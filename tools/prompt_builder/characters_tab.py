"""
characters_tab.py (Qt)
Редактор characters.json — плоский словарь key -> теги (+ опциональная LoRA).
Формат записи — см. logic.CharacterEntry / utils/char_utils.py расширения.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSizePolicy, QSplitter, QTextEdit, QVBoxLayout,
    QWidget,
)

from prompt_builder.logic import CharacterEntry, validate_tags_text
from prompt_builder.lora_combo import LoraFileCombo


class CharactersTab(QWidget):
    def __init__(self, on_dirty=None, loc=None, parent=None):
        super().__init__(parent)
        self.on_dirty = on_dirty
        self.loc = loc
        self.path: Optional[str] = None
        self.data: dict[str, CharacterEntry] = {}
        self._current_key: Optional[str] = None
        self._suspend = False

        self._build_ui()
        self._set_form_enabled(False)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # --- левая панель ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        header_row = QHBoxLayout()
        self.title_label = QLabel(self._tr("Персонажи"))
        self.title_label.setObjectName("headingLabel")
        self.count_label = QLabel("")
        self.count_label.setObjectName("mutedLabel")
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)
        header_row.addWidget(self.count_label)
        left_layout.addLayout(header_row)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self._tr("Поиск по ключу или тегам..."))
        self.search_edit.textChanged.connect(self._refresh_list)
        left_layout.addWidget(self.search_edit)

        filter_row = QHBoxLayout()
        self.filter_lora_check = QCheckBox(self._tr("Только с LoRA"))
        self.filter_lora_check.stateChanged.connect(self._refresh_list)
        filter_row.addWidget(self.filter_lora_check)
        self.filter_errors_check = QCheckBox(self._tr("Только с ошибками"))
        self.filter_errors_check.stateChanged.connect(self._refresh_list)
        filter_row.addWidget(self.filter_errors_check)
        filter_row.addStretch(1)
        left_layout.addLayout(filter_row)

        self.listbox = QListWidget()
        self.listbox.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.listbox, 1)

        btn_row = QHBoxLayout()
        self.new_btn = QPushButton(self._tr("+ Новый"))
        self.new_btn.clicked.connect(self._new_character)
        self.dup_btn = QPushButton(self._tr("Дублировать"))
        self.dup_btn.clicked.connect(self._duplicate_character)
        self.del_btn = QPushButton(self._tr("Удалить"))
        self.del_btn.setObjectName("dangerButton")
        self.del_btn.clicked.connect(self._delete_character)
        for b in (self.new_btn, self.dup_btn, self.del_btn):
            btn_row.addWidget(b)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # --- правая панель ---
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.key_label = QLabel(self._tr("Ключ (имя персонажа)"))
        right_layout.addWidget(self.key_label)
        self.key_edit = QLineEdit()
        self.key_edit.textEdited.connect(self._on_key_changed)
        right_layout.addWidget(self.key_edit)

        self.tags_label = QLabel(self._tr("Теги (через запятую, как в промпте)"))
        right_layout.addWidget(self.tags_label)
        self.tags_edit = QTextEdit()
        self.tags_edit.setAcceptRichText(False)
        self.tags_edit.textChanged.connect(self._on_tags_changed)
        right_layout.addWidget(self.tags_edit, 1)

        self.warn_label = QLabel("")
        self.warn_label.setObjectName("warnLabel")
        self.warn_label.setWordWrap(True)
        right_layout.addWidget(self.warn_label)

        self.lora_group = QGroupBox(self._tr("Привязанная LoRA (опционально)"))
        lora_layout = QHBoxLayout(self.lora_group)
        self.lora_file_label = QLabel(self._tr("Файл LoRA:"))
        lora_layout.addWidget(self.lora_file_label)
        self.lora_edit = LoraFileCombo()
        self.lora_edit.editTextChanged.connect(self._on_lora_changed)
        lora_layout.addWidget(self.lora_edit, 1)
        self.lora_strength_label = QLabel(self._tr("Сила:"))
        lora_layout.addWidget(self.lora_strength_label)
        self.strength_spin = QDoubleSpinBox()
        self.strength_spin.setRange(0.0, 10.0)
        self.strength_spin.setSingleStep(0.05)
        self.strength_spin.setValue(1.0)
        self.strength_spin.valueChanged.connect(self._on_strength_changed)
        lora_layout.addWidget(self.strength_spin)
        right_layout.addWidget(self.lora_group)

        self.status_label = QLabel(self._tr("Выберите персонажа слева или создайте нового"))
        self.status_label.setObjectName("mutedLabel")
        right_layout.addWidget(self.status_label)
        right_layout.addStretch(1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 700])

        self._form_widgets = [self.key_edit, self.tags_edit, self.lora_edit, self.strength_spin]

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты этой вкладки после смены
        языка — сам факт выбора языка не обновляет текст уже созданных
        виджетов."""
        self.title_label.setText(self._tr("Персонажи"))
        self.search_edit.setPlaceholderText(self._tr("Поиск по ключу или тегам..."))
        self.filter_lora_check.setText(self._tr("Только с LoRA"))
        self.filter_errors_check.setText(self._tr("Только с ошибками"))
        self.new_btn.setText(self._tr("+ Новый"))
        self.dup_btn.setText(self._tr("Дублировать"))
        self.del_btn.setText(self._tr("Удалить"))
        self.key_label.setText(self._tr("Ключ (имя персонажа)"))
        self.tags_label.setText(self._tr("Теги (через запятую, как в промпте)"))
        self.lora_group.setTitle(self._tr("Привязанная LoRA (опционально)"))
        self.lora_file_label.setText(self._tr("Файл LoRA:"))
        self.lora_strength_label.setText(self._tr("Сила:"))
        self.count_label.setText(self._tr("{} персонажей").format(len(self.data)))
        if not self._current_key:
            self.status_label.setText(self._tr("Выберите персонажа слева или создайте нового"))
        else:
            self.status_label.setText(self._tr("Редактируется: {}").format(self._current_key))
        self._update_warnings()

    # ------------------------------------------------------------- helpers
    def _mark_dirty(self):
        if not self._suspend and self.on_dirty:
            self.on_dirty()

    def _set_form_enabled(self, enabled: bool):
        for w in self._form_widgets:
            w.setEnabled(enabled)

    def _filtered_keys(self) -> list[str]:
        query = self.search_edit.text().strip().lower()
        only_lora = self.filter_lora_check.isChecked()
        only_errors = self.filter_errors_check.isChecked()

        keys = sorted(self.data.keys())
        if query:
            keys = [k for k in keys if query in k.lower() or query in self.data[k].tags.lower()]
        if only_lora:
            keys = [k for k in keys if self.data[k].lora]
        if only_errors:
            keys = [k for k in keys if validate_tags_text(self.data[k].tags)]
        return keys

    def _refresh_list(self, keep_selection: Optional[str] = None):
        keys = self._filtered_keys()
        self._suspend = True
        self.listbox.clear()
        for k in keys:
            entry = self.data[k]
            suffix = "  [LoRA]" if entry.lora else ""
            self.listbox.addItem(f"{k}{suffix}")
        self._suspend = False
        self.count_label.setText(self._tr("{} персонажей").format(len(self.data)))

        target = keep_selection or self._current_key
        if target and target in keys:
            self.listbox.setCurrentRow(keys.index(target))

    # ------------------------------------------------------------- events
    def _on_select(self, row: int):
        if self._suspend:
            return
        keys = self._filtered_keys()
        if row < 0 or row >= len(keys):
            return
        self._load_into_form(keys[row])

    def _load_into_form(self, key: str):
        self._current_key = key
        entry = self.data[key]
        self._suspend = True
        self.key_edit.setText(key)
        self.lora_edit.setText(entry.lora)
        self.strength_spin.setValue(entry.strength)
        self.tags_edit.setPlainText(entry.tags)
        self._suspend = False
        self._set_form_enabled(True)
        self._update_warnings()
        self.status_label.setText(self._tr("Редактируется: {}").format(key))

    def _on_tags_changed(self):
        if self._suspend or not self._current_key:
            return
        self.data[self._current_key].tags = self.tags_edit.toPlainText()
        self._update_warnings()
        self._mark_dirty()

    def _on_key_changed(self, text: str):
        if self._suspend or not self._current_key:
            return
        old_key = self._current_key
        new_key = text.strip().lower()
        if not new_key or new_key == old_key:
            return
        if new_key in self.data:
            self.status_label.setText(self._tr("Ключ '{}' уже занят — выберите другой").format(new_key))
            return
        renamed = {}
        for k, v in self.data.items():
            renamed[new_key if k == old_key else k] = v
        self.data = renamed
        self._current_key = new_key
        self._refresh_list(keep_selection=new_key)
        self.status_label.setText(self._tr("Редактируется: {}").format(new_key))
        self._mark_dirty()

    def _on_lora_changed(self, text: str):
        if self._suspend or not self._current_key:
            return
        self.data[self._current_key].lora = text.strip()
        self._mark_dirty()

    def _on_strength_changed(self, value: float):
        if self._suspend or not self._current_key:
            return
        self.data[self._current_key].strength = value
        self._mark_dirty()

    def _update_warnings(self):
        if not self._current_key:
            self.warn_label.setText("")
            return
        tags = self.data[self._current_key].tags
        issues = validate_tags_text(tags)
        if not tags.strip():
            self.warn_label.setObjectName("warnLabel")
            self.warn_label.setText(self._tr("⚠ отсутствуют теги"))
        elif issues:
            self.warn_label.setObjectName("warnLabel")
            self.warn_label.setText("⚠ " + "; ".join(issues))
        else:
            self.warn_label.setObjectName("mutedLabel")
            self.warn_label.setText(self._tr("✓ теги выглядят корректно"))
        self.warn_label.style().unpolish(self.warn_label)
        self.warn_label.style().polish(self.warn_label)

    # -------------------------------------------------------------- CRUD
    def _unique_key(self, base: str) -> str:
        base = base or "new_character"
        if base not in self.data:
            return base
        i = 2
        while f"{base}_{i}" in self.data:
            i += 1
        return f"{base}_{i}"

    def _new_character(self):
        key = self._unique_key("new_character")
        self.data[key] = CharacterEntry(tags="")
        self.search_edit.setText("")
        self._refresh_list(keep_selection=key)
        self._load_into_form(key)
        self.key_edit.setFocus()
        self.key_edit.selectAll()
        self._mark_dirty()

    def _duplicate_character(self):
        if not self._current_key:
            return
        src = self.data[self._current_key]
        key = self._unique_key(f"{self._current_key}_copy")
        self.data[key] = CharacterEntry(tags=src.tags, lora=src.lora, strength=src.strength)
        self._refresh_list(keep_selection=key)
        self._load_into_form(key)
        self._mark_dirty()

    def _delete_character(self):
        if not self._current_key:
            return
        key = self._current_key
        if QMessageBox.question(
            self, self._tr("Удалить персонажа"), self._tr("Удалить '{}' из базы?").format(key)
        ) != QMessageBox.Yes:
            return
        del self.data[key]
        self._current_key = None
        self._set_form_enabled(False)
        self._suspend = True
        self.key_edit.clear()
        self.lora_edit.clear()
        self.strength_spin.setValue(1.0)
        self.tags_edit.clear()
        self._suspend = False
        self.status_label.setText(self._tr("Выберите персонажа слева или создайте нового"))
        self._refresh_list()
        self._mark_dirty()

    # --------------------------------------------------------- load/save
    def load(self, path: str, raw: dict):
        self.path = path
        self.data = {}
        for key, value in raw.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            self.data[key] = CharacterEntry.from_raw(value)
        self._current_key = None
        self._suspend = True
        self.search_edit.setText("")
        self._suspend = False
        self._refresh_list()
        self._set_form_enabled(False)
        self.status_label.setText(self._tr("Выберите персонажа слева или создайте нового"))

    def to_raw(self) -> dict:
        return {key: entry.to_raw() for key, entry in self.data.items()}

    def has_data(self) -> bool:
        return self.path is not None
