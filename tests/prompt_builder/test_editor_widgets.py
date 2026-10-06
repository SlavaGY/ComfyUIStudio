"""
Qt-тесты редактора Prompt Builder после разбиения promptbuilder_tab.py
(этап R9 плана рефакторинга): вынесенные редакторы и тонкие обёртки
PromptBuilderTab над tree_ops.

Нужны PySide6 и pytest-qt (`pip install ".[dev]"`); без PySide6 файл
пропускается целиком. Проверяют то, что обещает R9: редактор открывает и
сохраняет конфиг без изменений, а кнопки дерева (+ Блок / + Вариант /
Удалить / Выше-Ниже) ведут себя как раньше. Диалоги подменяются, окон на
экран не выводится.
"""
import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from comfyui_studio.prompt_builder.logic import (  # noqa: E402
    format_lora_entry, parse_lora_entry,
)
from comfyui_studio.prompt_builder.lora_table_editor import LoraTableEditor  # noqa: E402
from comfyui_studio.prompt_builder.named_text_list_editor import NamedTextListEditor  # noqa: E402
from comfyui_studio.prompt_builder.preset_group_editor import PresetGroupEditor  # noqa: E402
from comfyui_studio.prompt_builder.promptbuilder_tab import PromptBuilderTab  # noqa: E402

SAMPLE_RAW = {
    "categories": [
        {
            "id": "g1", "label": "Группа", "type": "group",
            "children": [
                {
                    "id": "c1", "label": "Блок", "type": "multi_select",
                    "max_random": 2,
                    "options": [
                        {"label": "o1", "tags": "a, b", "loras": ["x:0.8"]},
                        {"label": "o2", "tags": "c"},
                    ],
                },
            ],
        },
        {"id": "c2", "label": "Текст", "type": "free_text", "placeholder": "..."},
    ],
    "quality_prefix": {"presets": {"hq": "masterpiece"}, "default": "hq"},
    "source": {"presets": {"s1": "source_x"}, "default": "s1"},
    "negative_presets": {"n1": "bad quality"},
    "negative_default": "n1",
}


def _noop():
    pass


def _make_tab(qtbot, dirty=None):
    tab = PromptBuilderTab(on_dirty=(lambda: dirty.append(1)) if dirty is not None else None)
    qtbot.addWidget(tab)
    tab.load("prompt_builder_config.json", copy.deepcopy(SAMPLE_RAW))
    return tab


def _select(tab, item):
    tab.tree.setCurrentItem(item)


# -- вынесенные редакторы --------------------------------------------------


def test_lora_table_editor_roundtrip(qtbot):
    ed = LoraTableEditor()
    qtbot.addWidget(ed)
    entries = ["a:0.8", "b:1"]
    ed.set_entries(entries)
    expected = [format_lora_entry(*parse_lora_entry(e)) for e in entries]
    assert ed.get_entries() == expected


def test_preset_group_editor_roundtrip(qtbot):
    ed = PresetGroupEditor("Пресеты", _noop)
    qtbot.addWidget(ed)
    data = {"presets": {"A": "tag1", "B": "tag2"}, "default": "B"}
    ed.load(data)
    assert ed.to_raw() == data


def test_named_text_list_editor_roundtrip_with_default(qtbot):
    ed = NamedTextListEditor("Негативы", _noop, with_default=True)
    qtbot.addWidget(ed)
    ed.load({"n1": "text one", "n2": "text two"}, "n2")
    presets, default = ed.to_raw()
    assert presets == {"n1": "text one", "n2": "text two"}
    assert default == "n2"


# -- вкладка целиком -------------------------------------------------------


def test_tab_load_to_raw_keeps_config_unchanged(qtbot):
    tab = _make_tab(qtbot)
    assert tab.to_raw() == SAMPLE_RAW


def test_tab_add_category_inside_selected_group(qtbot):
    dirty = []
    tab = _make_tab(qtbot, dirty)
    group_item = tab.tree.topLevelItem(0)
    _select(tab, group_item)
    before = len(dirty)
    tab._add_category()
    children = tab.categories[0]["children"]
    assert len(children) == 2
    assert children[-1]["type"] == "multi_select"
    assert len(dirty) > before


def test_tab_add_option_to_selected_category(qtbot):
    tab = _make_tab(qtbot)
    cat_item = tab.tree.topLevelItem(0).child(0)
    _select(tab, cat_item)
    tab._add_option()
    options = tab.categories[0]["children"][0]["options"]
    assert len(options) == 3
    assert options[-1]["label"] == "Новый вариант"


def test_tab_add_option_to_group_is_refused_with_message(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: shown.append(args))
    tab = _make_tab(qtbot)
    _select(tab, tab.tree.topLevelItem(0))
    tab._add_option()
    assert len(shown) == 1
    assert len(tab.categories[0]["children"][0]["options"]) == 2


def test_tab_add_option_to_free_text_is_refused_with_message(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: shown.append(args))
    tab = _make_tab(qtbot)
    _select(tab, tab.tree.topLevelItem(1))
    tab._add_option()
    assert len(shown) == 1
    assert "options" not in tab.categories[1]


def test_tab_move_node_up(qtbot):
    tab = _make_tab(qtbot)
    group_item = tab.tree.topLevelItem(0)
    _select(tab, group_item)
    tab._add_category()
    children = tab.categories[0]["children"]
    new_node = children[-1]
    tab._move_node(-1)
    assert children[0] is new_node


def test_tab_delete_node_after_confirmation(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    tab = _make_tab(qtbot)
    _select(tab, tab.tree.topLevelItem(0).child(0))
    tab._delete_node()
    assert tab.categories[0]["children"] == []


def test_tab_delete_node_declined_keeps_tree(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    tab = _make_tab(qtbot)
    _select(tab, tab.tree.topLevelItem(0).child(0))
    tab._delete_node()
    assert len(tab.categories[0]["children"]) == 1
