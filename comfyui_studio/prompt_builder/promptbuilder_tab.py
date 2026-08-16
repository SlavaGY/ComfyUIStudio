"""
promptbuilder_tab.py (Qt)
Блочный редактор prompt_builder_config.json.

Дерево категорий (см. prompt_builder_node.py / utils/prompt_logic.py):
  group        — { id, label, type:"group", children:[...] }        — вложенная вкладка
  multi_select — { id, label, type, max_random, options:[...] }      — можно выбрать несколько + рандом
  single_select— { id, label, type, default, options:[...] }         — выбор одного варианта
  free_text    — { id, label, type, placeholder }                    — свободный ввод, без options

  option (внутри multi/single_select):
    { label, tags, loras: ["name:strength", ...] }

Плюс верхнеуровневые настройки: quality_prefix, source (пресеты с default),
negative_presets (набор именованных негативных промптов) + negative_default.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from comfyui_studio.prompt_builder.logic import (
    display_loras_for, find_legacy_lora_options, format_lora_entry,
    migrate_legacy_lora_option, new_id, parse_lora_entry,
)
from comfyui_studio.prompt_builder.lora_combo import LoraFileCombo

NODE_TYPES = ["multi_select", "single_select", "free_text"]
TYPE_LABELS = {
    "multi_select": "☑ Множественный выбор",
    "single_select": "◉ Одиночный выбор",
    "free_text": "✎ Свободный текст",
}
NODE_ROLE = Qt.UserRole


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


class PromptBuilderTab(QWidget):
    def __init__(self, on_dirty=None, loc=None, parent=None):
        super().__init__(parent)
        self.on_dirty = on_dirty
        self.loc = loc
        self.path: Optional[str] = None
        self.raw: dict[str, Any] = {}
        self.categories: list[dict] = []
        self._suspend = False
        # PySide6 не сохраняет identity питоновских dict через
        # QTreeWidgetItem.setData/data (значение проходит через QVariant и
        # на выходе получается копия) — поэтому в дерево кладём не сам dict,
        # а целочисленный ключ в этот реестр; мутируем/сравниваем по ссылке
        # уже здесь, в чистом Python.
        self._node_registry: dict[int, dict] = {}
        self._registry_counter = 0

        self._build_ui()

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_tabs = QTabWidget()
        outer_layout.addWidget(self.outer_tabs)

        self.categories_page = QWidget()
        self.presets_page = QWidget()
        self.negatives_page = QWidget()
        self.outer_tabs.addTab(self.categories_page, self._tr("Блоки промпта"))
        self.outer_tabs.addTab(self.presets_page, self._tr("Пресеты качества/источника"))
        self.outer_tabs.addTab(self.negatives_page, self._tr("Негативные пресеты"))

        self._build_categories_page()
        self._build_presets_page()
        self._build_negatives_page()

    def _mark_dirty(self):
        if not self._suspend and self.on_dirty:
            self.on_dirty()

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты этой вкладки после смены
        языка — сам факт выбора языка не обновляет текст уже созданных
        виджетов."""
        self.outer_tabs.setTabText(0, self._tr("Блоки промпта"))
        self.outer_tabs.setTabText(1, self._tr("Пресеты качества/источника"))
        self.outer_tabs.setTabText(2, self._tr("Негативные пресеты"))

        # --- дерево блоков ---
        self.tree_heading.setText(self._tr("Дерево блоков"))
        self.add_group_btn.setText(self._tr("+ Группа"))
        self.add_cat_btn.setText(self._tr("+ Блок"))
        self.add_opt_btn.setText(self._tr("+ Вариант"))
        self.del_node_btn.setText(self._tr("Удалить"))
        self.up_btn.setText(self._tr("▲ Выше"))
        self.down_btn.setText(self._tr("▼ Ниже"))
        self.migrate_btn.setText(self._tr("⇪ Старые LoRA (lora/lora_strength → loras)"))
        self.empty_page_label.setText(self._tr("Выберите блок в дереве слева, либо создайте новый."))

        # --- форма группы ---
        self.g_heading.setText(self._tr("Группа"))
        self.g_id_label.setText(self._tr("id:"))
        self.g_label_label.setText(self._tr("Название вкладки:"))
        self.g_hint.setText(self._tr(
            "Группа объединяет вложенные блоки в отдельную под-вкладку\n"
            "в интерфейсе билдера. Добавляйте дочерние блоки кнопкой\n"
            "«+ Блок» / «+ Группа», когда группа выбрана в дереве."
        ))

        # --- форма блока (категории) ---
        self.c_heading.setText(self._tr("Блок (категория)"))
        self.c_id_label.setText(self._tr("id:"))
        self.c_label_label.setText(self._tr("Название:"))
        self.c_type_label.setText(self._tr("Тип:"))
        current_type_idx = self.c_type_combo.currentIndex()
        self.c_type_combo.blockSignals(True)
        self.c_type_combo.clear()
        self.c_type_combo.addItems([self._tr(TYPE_LABELS[t]) for t in NODE_TYPES])
        if current_type_idx >= 0:
            self.c_type_combo.setCurrentIndex(current_type_idx)
        self.c_type_combo.blockSignals(False)
        self.c_random_label.setText(self._tr("Макс. случайных выборов (0 = не участвует в рандоме):"))
        self.c_default_label.setText(self._tr("Вариант по умолчанию (метка):"))
        self.c_required_check.setText(self._tr("Обязательный блок (required)"))
        self.c_placeholder_label.setText(self._tr("Placeholder (для свободного текста):"))
        self.c_hint.setText(self._tr(
            "Варианты (options) добавляются кнопкой «+ Вариант»,\n"
            "когда этот блок выбран в дереве. Список — ниже, в дереве."
        ))

        # --- форма варианта ---
        self.o_heading.setText(self._tr("Вариант блока"))
        self.o_label_label.setText(self._tr("Метка (то, что видит пользователь):"))
        self.o_tags_label.setText(self._tr("Теги (через запятую):"))
        self.o_lora_label.setText(self._tr("LoRA для этого варианта:"))
        self.o_lora_editor.retranslate_ui()

        # --- пресеты качества/источника ---
        self.preset_editors["quality_prefix"].retranslate_ui(self._tr("Префикс качества (quality_prefix)"))
        self.preset_editors["source"].retranslate_ui(self._tr("Источник (source)"))

        # --- негативные пресеты ---
        self.negative_editor.retranslate_ui(
            self._tr("Негативные пресеты (negative_presets)"),
            self._tr("Пресет по умолчанию (negative_default):"),
        )

    # --------------------------------------------------- categories page
    def _build_categories_page(self):
        layout = QHBoxLayout(self.categories_page)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.tree_heading = self._heading(self._tr("Дерево блоков"))
        left_layout.addWidget(self.tree_heading)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_tree_select)
        left_layout.addWidget(self.tree, 1)

        grid1 = QHBoxLayout()
        self.add_group_btn = QPushButton(self._tr("+ Группа"))
        self.add_group_btn.clicked.connect(self._add_group)
        self.add_cat_btn = QPushButton(self._tr("+ Блок"))
        self.add_cat_btn.clicked.connect(self._add_category)
        grid1.addWidget(self.add_group_btn)
        grid1.addWidget(self.add_cat_btn)
        left_layout.addLayout(grid1)

        grid2 = QHBoxLayout()
        self.add_opt_btn = QPushButton(self._tr("+ Вариант"))
        self.add_opt_btn.clicked.connect(self._add_option)
        self.del_node_btn = QPushButton(self._tr("Удалить"))
        self.del_node_btn.setObjectName("dangerButton")
        self.del_node_btn.clicked.connect(self._delete_node)
        grid2.addWidget(self.add_opt_btn)
        grid2.addWidget(self.del_node_btn)
        left_layout.addLayout(grid2)

        grid3 = QHBoxLayout()
        self.up_btn = QPushButton(self._tr("▲ Выше"))
        self.up_btn.clicked.connect(lambda: self._move_node(-1))
        self.down_btn = QPushButton(self._tr("▼ Ниже"))
        self.down_btn.clicked.connect(lambda: self._move_node(1))
        grid3.addWidget(self.up_btn)
        grid3.addWidget(self.down_btn)
        left_layout.addLayout(grid3)

        self.migrate_btn = QPushButton(self._tr("⇪ Старые LoRA (lora/lora_strength → loras)"))
        self.migrate_btn.clicked.connect(lambda: self.scan_and_offer_legacy_migration(auto=False))
        left_layout.addWidget(self.migrate_btn)

        splitter.addWidget(left)

        # --- правая форма ---
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        self.stack = QStackedWidget()
        right_scroll.setWidget(self.stack)
        splitter.addWidget(right_scroll)
        splitter.setSizes([340, 700])

        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        self.empty_page_label = QLabel(self._tr("Выберите блок в дереве слева, либо создайте новый."))
        self.empty_page_label.setObjectName("mutedLabel")
        empty_layout.addWidget(self.empty_page_label)
        empty_layout.addStretch(1)

        self.group_page = self._build_group_form()
        self.cat_page = self._build_category_form()
        self.opt_page = self._build_option_form()

        for page in (self.empty_page, self.group_page, self.cat_page, self.opt_page):
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self.empty_page)

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("headingLabel")
        return lbl

    def _build_group_form(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.g_heading = self._heading(self._tr("Группа"))
        layout.addWidget(self.g_heading)

        self.g_id_label = QLabel(self._tr("id:"))
        layout.addWidget(self.g_id_label)
        self.g_id_edit = QLineEdit()
        self.g_id_edit.textEdited.connect(self._on_group_field_changed)
        layout.addWidget(self.g_id_edit)

        self.g_label_label = QLabel(self._tr("Название вкладки:"))
        layout.addWidget(self.g_label_label)
        self.g_label_edit = QLineEdit()
        self.g_label_edit.textEdited.connect(self._on_group_field_changed)
        layout.addWidget(self.g_label_edit)

        self.g_hint = QLabel(self._tr(
            "Группа объединяет вложенные блоки в отдельную под-вкладку\n"
            "в интерфейсе билдера. Добавляйте дочерние блоки кнопкой\n"
            "«+ Блок» / «+ Группа», когда группа выбрана в дереве."
        ))
        self.g_hint.setObjectName("mutedLabel")
        layout.addWidget(self.g_hint)
        layout.addStretch(1)
        return page

    def _build_category_form(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.c_heading = self._heading(self._tr("Блок (категория)"))
        layout.addWidget(self.c_heading)

        self.c_id_label = QLabel(self._tr("id:"))
        layout.addWidget(self.c_id_label)
        self.c_id_edit = QLineEdit()
        self.c_id_edit.textEdited.connect(self._on_category_field_changed)
        layout.addWidget(self.c_id_edit)

        self.c_label_label = QLabel(self._tr("Название:"))
        layout.addWidget(self.c_label_label)
        self.c_label_edit = QLineEdit()
        self.c_label_edit.textEdited.connect(self._on_category_field_changed)
        layout.addWidget(self.c_label_edit)

        self.c_type_label = QLabel(self._tr("Тип:"))
        layout.addWidget(self.c_type_label)
        self.c_type_combo = QComboBox()
        self.c_type_combo.addItems([self._tr(TYPE_LABELS[t]) for t in NODE_TYPES])
        self.c_type_combo.currentIndexChanged.connect(self._on_category_type_changed)
        layout.addWidget(self.c_type_combo)

        self.c_random_row = QWidget()
        rr = QHBoxLayout(self.c_random_row)
        rr.setContentsMargins(0, 0, 0, 0)
        self.c_random_label = QLabel(self._tr("Макс. случайных выборов (0 = не участвует в рандоме):"))
        rr.addWidget(self.c_random_label)
        self.c_maxrandom_spin = QSpinBox()
        self.c_maxrandom_spin.setRange(0, 20)
        self.c_maxrandom_spin.valueChanged.connect(self._on_category_field_changed)
        rr.addWidget(self.c_maxrandom_spin)
        layout.addWidget(self.c_random_row)

        self.c_default_row = QWidget()
        dr = QHBoxLayout(self.c_default_row)
        dr.setContentsMargins(0, 0, 0, 0)
        self.c_default_label = QLabel(self._tr("Вариант по умолчанию (метка):"))
        dr.addWidget(self.c_default_label)
        self.c_default_edit = QLineEdit()
        self.c_default_edit.textEdited.connect(self._on_category_field_changed)
        dr.addWidget(self.c_default_edit)
        layout.addWidget(self.c_default_row)

        self.c_required_check = QCheckBox(self._tr("Обязательный блок (required)"))
        self.c_required_check.stateChanged.connect(self._on_category_field_changed)
        layout.addWidget(self.c_required_check)

        self.c_placeholder_row = QWidget()
        pr = QHBoxLayout(self.c_placeholder_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.c_placeholder_label = QLabel(self._tr("Placeholder (для свободного текста):"))
        pr.addWidget(self.c_placeholder_label)
        self.c_placeholder_edit = QLineEdit()
        self.c_placeholder_edit.textEdited.connect(self._on_category_field_changed)
        pr.addWidget(self.c_placeholder_edit)
        layout.addWidget(self.c_placeholder_row)

        self.c_hint = QLabel(self._tr(
            "Варианты (options) добавляются кнопкой «+ Вариант»,\n"
            "когда этот блок выбран в дереве. Список — ниже, в дереве."
        ))
        self.c_hint.setObjectName("mutedLabel")
        layout.addWidget(self.c_hint)
        layout.addStretch(1)
        return page

    def _build_option_form(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.o_heading = self._heading(self._tr("Вариант блока"))
        layout.addWidget(self.o_heading)

        self.o_label_label = QLabel(self._tr("Метка (то, что видит пользователь):"))
        layout.addWidget(self.o_label_label)
        self.o_label_edit = QLineEdit()
        self.o_label_edit.textEdited.connect(self._on_option_label_changed)
        layout.addWidget(self.o_label_edit)

        self.o_tags_label = QLabel(self._tr("Теги (через запятую):"))
        layout.addWidget(self.o_tags_label)
        self.o_tags_edit = QTextEdit()
        self.o_tags_edit.setAcceptRichText(False)
        self.o_tags_edit.setMaximumHeight(90)
        self.o_tags_edit.textChanged.connect(self._on_option_tags_changed)
        layout.addWidget(self.o_tags_edit)

        self.o_lora_label = QLabel(self._tr("LoRA для этого варианта:"))
        layout.addWidget(self.o_lora_label)
        self.o_lora_editor = LoraTableEditor(on_change=self._on_option_lora_changed, loc=self.loc)
        layout.addWidget(self.o_lora_editor)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------- presets page
    def _build_presets_page(self):
        layout = QHBoxLayout(self.presets_page)
        self.preset_editors: dict[str, "_PresetGroupEditor"] = {}
        self.preset_editors["quality_prefix"] = _PresetGroupEditor(
            self._tr("Префикс качества (quality_prefix)"), self._mark_dirty, loc=self.loc)
        self.preset_editors["source"] = _PresetGroupEditor(
            self._tr("Источник (source)"), self._mark_dirty, loc=self.loc)
        layout.addWidget(self.preset_editors["quality_prefix"])
        layout.addWidget(self.preset_editors["source"])

    # ----------------------------------------------------- negatives page
    def _build_negatives_page(self):
        layout = QVBoxLayout(self.negatives_page)
        self.negative_editor = _NamedTextListEditor(
            self._tr("Негативные пресеты (negative_presets)"), self._mark_dirty,
            with_default=True, default_label=self._tr("Пресет по умолчанию (negative_default):"),
            loc=self.loc)
        layout.addWidget(self.negative_editor)

    # ------------------------------------------------------- tree helpers
    def _register(self, info: dict) -> int:
        self._registry_counter += 1
        key = self._registry_counter
        self._node_registry[key] = info
        return key

    def _rebuild_tree(self, select_node: Optional[dict] = None):
        self._suspend = True
        self.tree.clear()
        self._node_registry = {}
        self._registry_counter = 0
        self._build_children(self.tree.invisibleRootItem(), self.categories)
        self.tree.expandAll()
        self._suspend = False

        if select_node is not None:
            item = self._find_item_for_node(select_node)
            if item is not None:
                self.tree.setCurrentItem(item)
                return
        self._on_tree_select(self.tree.currentItem(), None)

    def _build_children(self, parent_item, node_list: list):
        for node in node_list:
            ntype = node.get("type", "")
            if ntype == "group":
                label = f"📁 {node.get('label', node.get('id', '?'))}"
                item = QTreeWidgetItem(parent_item, [label])
                item.setData(0, NODE_ROLE, self._register({"kind": "group", "node": node, "parent_list": node_list}))
                self._build_children(item, node.setdefault("children", []))
            else:
                icon = {"multi_select": "☑", "single_select": "◉", "free_text": "✎"}.get(ntype, "•")
                label = f"{icon} {node.get('label', node.get('id', '?'))}"
                item = QTreeWidgetItem(parent_item, [label])
                item.setData(0, NODE_ROLE, self._register({"kind": "cat", "node": node, "parent_list": node_list}))
                options = node.setdefault("options", []) if ntype != "free_text" else node.get("options", [])
                for opt in options:
                    oitem = QTreeWidgetItem(item, [f"— {opt.get('label', self._tr('(без метки)'))}"])
                    oitem.setData(0, NODE_ROLE, self._register({"kind": "opt", "node": opt, "parent_list": options}))

    def _item_info(self, item) -> Optional[dict]:
        if item is None:
            return None
        key = item.data(0, NODE_ROLE)
        return self._node_registry.get(key)

    def _find_item_for_node(self, node: dict) -> Optional[QTreeWidgetItem]:
        def walk(item) -> Optional[QTreeWidgetItem]:
            for i in range(item.childCount()):
                child = item.child(i)
                info = self._item_info(child)
                if info and info["node"] is node:
                    return child
                found = walk(child)
                if found is not None:
                    return found
            return None
        return walk(self.tree.invisibleRootItem())

    def _selected_info(self) -> Optional[dict]:
        return self._item_info(self.tree.currentItem())

    # ------------------------------------------------------- tree events
    def _on_tree_select(self, current: Optional[QTreeWidgetItem], _previous):
        info = self._item_info(current)

        if info is None:
            self.stack.setCurrentWidget(self.empty_page)
            return

        self._suspend = True
        try:
            if info["kind"] == "group":
                node = info["node"]
                self.g_id_edit.setText(node.get("id", ""))
                self.g_label_edit.setText(node.get("label", ""))
                self.stack.setCurrentWidget(self.group_page)
            elif info["kind"] == "cat":
                node = info["node"]
                self.c_id_edit.setText(node.get("id", ""))
                self.c_label_edit.setText(node.get("label", ""))
                ntype = node.get("type", "multi_select")
                self.c_type_combo.setCurrentIndex(NODE_TYPES.index(ntype) if ntype in NODE_TYPES else 0)
                self.c_maxrandom_spin.setValue(int(node.get("max_random", 0)))
                self.c_default_edit.setText(node.get("default", ""))
                self.c_required_check.setChecked(bool(node.get("required", False)))
                self.c_placeholder_edit.setText(node.get("placeholder", ""))
                self._update_category_form_visibility(ntype)
                self.stack.setCurrentWidget(self.cat_page)
            elif info["kind"] == "opt":
                node = info["node"]
                self.o_label_edit.setText(node.get("label", ""))
                self.o_tags_edit.setPlainText(node.get("tags", ""))
                self.o_lora_editor.set_entries(display_loras_for(node))
                self.stack.setCurrentWidget(self.opt_page)
        finally:
            self._suspend = False

    def _update_category_form_visibility(self, ntype: str):
        self.c_random_row.setVisible(ntype == "multi_select")
        self.c_default_row.setVisible(ntype == "single_select")
        self.c_placeholder_row.setVisible(ntype == "free_text")

    # ------------------------------------------------------- field edits
    def _on_group_field_changed(self, *_args):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "group":
            return
        node = info["node"]
        node["id"] = self.g_id_edit.text().strip()
        node["label"] = self.g_label_edit.text()
        self.tree.currentItem().setText(0, f"📁 {node['label'] or node['id']}")
        self._mark_dirty()

    def _on_category_type_changed(self, _index: int):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "cat":
            return
        ntype = NODE_TYPES[self.c_type_combo.currentIndex()]
        info["node"]["type"] = ntype
        self._update_category_form_visibility(ntype)
        self._on_category_field_changed()

    def _on_category_field_changed(self, *_args):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "cat":
            return
        node = info["node"]
        node["id"] = self.c_id_edit.text().strip()
        node["label"] = self.c_label_edit.text()
        ntype = node.get("type", "multi_select")

        if ntype == "multi_select":
            node["max_random"] = self.c_maxrandom_spin.value()
            node.pop("default", None)
            node.pop("placeholder", None)
        elif ntype == "single_select":
            if self.c_default_edit.text():
                node["default"] = self.c_default_edit.text()
            else:
                node.pop("default", None)
            node.pop("max_random", None)
            node.pop("placeholder", None)
        else:  # free_text
            node["placeholder"] = self.c_placeholder_edit.text()
            node.pop("max_random", None)
            node.pop("default", None)
            node.pop("options", None)

        if self.c_required_check.isChecked():
            node["required"] = True
        else:
            node.pop("required", None)

        icon = {"multi_select": "☑", "single_select": "◉", "free_text": "✎"}.get(ntype, "•")
        self.tree.currentItem().setText(0, f"{icon} {node['label'] or node['id']}")
        self._mark_dirty()

    def _on_option_tags_changed(self):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "opt":
            return
        info["node"]["tags"] = self.o_tags_edit.toPlainText().strip()
        self._mark_dirty()

    def _on_option_label_changed(self, _text: str):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "opt":
            return
        info["node"]["label"] = self.o_label_edit.text()
        self.tree.currentItem().setText(0, f"— {info['node'].get('label', self._tr('(без метки)'))}")
        self._mark_dirty()

    def _on_option_lora_changed(self):
        if self._suspend:
            return
        info = self._selected_info()
        if not info or info["kind"] != "opt":
            return
        entries = self.o_lora_editor.get_entries()
        node = info["node"]
        if entries:
            node["loras"] = entries
        else:
            node.pop("loras", None)
        # Ручное редактирование списка LoRA переносит вариант на новый формат целиком.
        node.pop("lora", None)
        node.pop("lora_strength", None)
        node.pop("strength", None)
        self._mark_dirty()

    # ------------------------------------------------------------ CRUD ops
    def _current_container_for_add(self) -> list:
        info = self._selected_info()
        if info is None:
            return self.categories
        if info["kind"] == "group":
            return info["node"].setdefault("children", [])
        if info["kind"] == "opt":
            owner = self._find_owner_category(info["parent_list"])
            return owner["parent_list"] if owner is not None else self.categories
        return info["parent_list"]

    def _find_owner_category(self, options_list: list) -> Optional[dict]:
        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                info = self._item_info(child)
                if info and info["kind"] == "cat" and info["node"].get("options") is options_list:
                    return info
                found = walk(child)
                if found is not None:
                    return found
            return None
        return walk(self.tree.invisibleRootItem())

    def _add_group(self):
        parent_list = self._current_container_for_add()
        new_node = {"id": new_id("group"), "label": self._tr("Новая группа"), "type": "group", "children": []}
        parent_list.append(new_node)
        self._rebuild_tree(select_node=new_node)
        self._mark_dirty()

    def _add_category(self):
        parent_list = self._current_container_for_add()
        new_node = {"id": new_id("block"), "label": self._tr("Новый блок"), "type": "multi_select",
                    "max_random": 0, "options": []}
        parent_list.append(new_node)
        self._rebuild_tree(select_node=new_node)
        self._mark_dirty()

    def _add_option(self):
        info = self._selected_info()
        if info is None:
            QMessageBox.information(self, self._tr("Добавить вариант"),
                                     self._tr("Сначала выберите блок (категорию), в который нужно добавить вариант."))
            return
        if info["kind"] == "opt":
            options_list = info["parent_list"]
        elif info["kind"] == "cat":
            if info["node"].get("type") == "free_text":
                QMessageBox.information(self, self._tr("Добавить вариант"),
                                         self._tr("У блока типа «Свободный текст» нет вариантов."))
                return
            options_list = info["node"].setdefault("options", [])
        else:
            QMessageBox.information(self, self._tr("Добавить вариант"),
                                     self._tr("Варианты можно добавлять только внутрь блока (не группы)."))
            return

        new_opt = {"label": self._tr("Новый вариант"), "tags": ""}
        options_list.append(new_opt)
        self._rebuild_tree(select_node=new_opt)
        self._mark_dirty()

    def _delete_node(self):
        info = self._selected_info()
        if info is None:
            return
        kind_name = {
            "group": self._tr("группу"),
            "cat": self._tr("блок"),
            "opt": self._tr("вариант"),
        }[info["kind"]]
        if QMessageBox.question(
            self, self._tr("Удалить"), self._tr("Удалить выбранный(ую) {}?").format(kind_name)
        ) != QMessageBox.Yes:
            return
        try:
            info["parent_list"].remove(info["node"])
        except ValueError:
            pass
        self._rebuild_tree()
        self._mark_dirty()

    def _move_node(self, direction: int):
        info = self._selected_info()
        if info is None:
            return
        lst = info["parent_list"]
        node = info["node"]
        idx = lst.index(node)
        new_idx = idx + direction
        if 0 <= new_idx < len(lst):
            lst[idx], lst[new_idx] = lst[new_idx], lst[idx]
            self._rebuild_tree(select_node=node)
            self._mark_dirty()

    # ------------------------------------------------ legacy LoRA migration
    def scan_and_offer_legacy_migration(self, auto: bool = False):
        """Ищет варианты со старым форматом LoRA ("lora"/"lora_strength" вместо
        "loras": [...]). auto=True — вызывается автоматически после загрузки файла
        и молчит, если ничего не найдено; auto=False — вызывается вручную кнопкой
        и всегда показывает результат."""
        legacy_options = find_legacy_lora_options(self.categories)
        count = len(legacy_options)

        if count == 0:
            if not auto:
                QMessageBox.information(
                    self, self._tr("Старая архитектура LoRA"),
                    self._tr(
                        "Вариантов со старым форматом LoRA (\"lora\"/\"lora_strength\") не найдено — "
                        "всё уже в новом формате \"loras\": [...]."
                    ),
                )
            return

        proceed = QMessageBox.question(
            self, self._tr("Найдена старая архитектура LoRA"),
            self._tr(
                "Найдено {} вариант(ов) со старой архитектурой LoRA "
                "(поле \"lora\"/\"lora_strength\" вместо \"loras\": [...]).\n\n"
                "Расширение всё ещё умеет читать старый формат, но в этом редакторе "
                "такая LoRA не отображалась бы в списке варианта.\n\n"
                "Перенести все найденные варианты на новую архитектуру сейчас?"
            ).format(count),
        )
        if proceed != QMessageBox.Yes:
            return

        migrated = sum(1 for opt in legacy_options if migrate_legacy_lora_option(opt))
        self._rebuild_tree()
        self._mark_dirty()
        QMessageBox.information(
            self, self._tr("Миграция завершена"),
            self._tr("Перенесено вариантов: {} из {}.\nНе забудьте сохранить файл (Ctrl+S).").format(migrated, count),
        )

    # --------------------------------------------------------- load/save
    def load(self, path: str, raw: dict):
        self.path = path
        self.raw = raw
        self.categories = self.raw.setdefault("categories", [])
        self._rebuild_tree()

        self.preset_editors["quality_prefix"].load(self.raw.setdefault(
            "quality_prefix", {"presets": {}, "default": ""}))
        self.preset_editors["source"].load(self.raw.setdefault(
            "source", {"presets": {}, "default": ""}))
        self.negative_editor.load(
            self.raw.setdefault("negative_presets", {}),
            self.raw.get("negative_default", ""),
        )

        self.scan_and_offer_legacy_migration(auto=True)

    def to_raw(self) -> dict:
        self.raw["categories"] = self.categories
        self.raw["quality_prefix"] = self.preset_editors["quality_prefix"].to_raw()
        self.raw["source"] = self.preset_editors["source"].to_raw()
        presets, default = self.negative_editor.to_raw()
        self.raw["negative_presets"] = presets
        self.raw["negative_default"] = default
        return self.raw

    def has_data(self) -> bool:
        return self.path is not None


class _PresetGroupEditor(QGroupBox):
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


class _NamedTextListEditor(QGroupBox):
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
