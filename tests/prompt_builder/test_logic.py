"""
Характеризационные тесты prompt_builder/logic.py (этап R0 плана
рефакторинга, раунд 2) -- фиксируют ТЕКУЩЕЕ поведение чистой (без Qt)
логики редактора перед разбиением promptbuilder_tab.py (этап R9).

Тесты ничего не импортируют из PySide6: logic.py намеренно не зависит
от GUI-фреймворка (см. его докстринг), поэтому этот файл запускается
даже без Qt-окружения.
"""

import re

import pytest

from comfyui_studio.prompt_builder.logic import (
    CharacterEntry,
    display_loras_for,
    find_legacy_lora_options,
    format_lora_entry,
    legacy_lora_entry,
    migrate_legacy_lora_option,
    new_id,
    parse_lora_entry,
    validate_tags_text,
)


# -- validate_tags_text ----------------------------------------------------


def test_validate_tags_empty_string_has_no_issues():
    assert validate_tags_text("") == []


def test_validate_tags_clean_text_has_no_issues():
    assert validate_tags_text("1girl, solo, blue eyes") == []


def test_validate_tags_reports_empty_elements():
    issues = validate_tags_text("a,,b,")
    assert len(issues) == 1
    assert issues[0].startswith("2 пустых элементов")


def test_validate_tags_reports_duplicates_once_each():
    issues = validate_tags_text("a, b, a, a, b")
    assert issues == ["дублирующиеся теги: a, b"]


def test_validate_tags_reports_suspicious_characters():
    issues = validate_tags_text("ok, bad<tag>")
    assert len(issues) == 1
    assert issues[0].startswith("подозрительные символы:")
    assert "bad<tag>" in issues[0]


def test_validate_tags_reports_too_long_tag():
    issues = validate_tags_text("x" * 201)
    assert issues == ["есть теги длиннее 200 символов"]


def test_validate_tags_tag_of_exactly_max_length_is_ok():
    assert validate_tags_text("x" * 200) == []


# -- parse_lora_entry / format_lora_entry ----------------------------------


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("name:0.8", ("name", 0.8)),
        ("name", ("name", 1.0)),
        ("name:abc", ("name", 1.0)),  # нечисловая сила -> 1.0
        ("a:b:0.5", ("a:b", 0.5)),  # двоеточие в самом имени: делит по последнему
        ("  name : 0.5 ", ("name", 0.5)),
        ("", ("", 1.0)),
    ],
)
def test_parse_lora_entry(entry, expected):
    assert parse_lora_entry(entry) == expected


@pytest.mark.parametrize(
    "name, strength, expected",
    [
        ("x", 1.0, "x:1"),
        ("x", 0.75, "x:0.75"),
        ("x", 0.0, "x:0"),
    ],
)
def test_format_lora_entry(name, strength, expected):
    assert format_lora_entry(name, strength) == expected


def test_format_then_parse_roundtrip():
    assert parse_lora_entry(format_lora_entry("style_lora", 0.65)) == ("style_lora", 0.65)


def test_new_id_has_prefix_and_numeric_suffix():
    assert re.fullmatch(r"opt_\d+", new_id("opt"))


# -- CharacterEntry --------------------------------------------------------


def test_character_entry_from_plain_string_keeps_only_tags():
    e = CharacterEntry.from_raw("1girl, red hair")
    assert (e.tags, e.lora, e.strength) == ("1girl, red hair", "", 1.0)


def test_character_entry_from_dict_reads_all_fields():
    e = CharacterEntry.from_raw({"tags": "a", "lora": "  my_lora  ", "strength": 0.5})
    assert (e.tags, e.lora, e.strength) == ("a", "my_lora", 0.5)


def test_character_entry_from_dict_accepts_legacy_lora_strength_key():
    e = CharacterEntry.from_raw({"tags": "a", "lora": "l", "lora_strength": 0.7})
    assert e.strength == 0.7


def test_character_entry_from_unknown_type_is_empty():
    e = CharacterEntry.from_raw(None)
    assert (e.tags, e.lora, e.strength) == ("", "", 1.0)


def test_character_entry_to_raw_is_plain_string_without_lora():
    assert CharacterEntry(tags="a, b").to_raw() == "a, b"


def test_character_entry_to_raw_is_dict_with_lora_or_nondefault_strength():
    assert CharacterEntry(tags="a", lora="l", strength=0.5).to_raw() == {
        "tags": "a",
        "lora": "l",
        "strength": 0.5,
    }
    # сила != 1.0 без lora тоже требует словарной формы
    assert isinstance(CharacterEntry(tags="a", strength=0.5).to_raw(), dict)


def test_character_entry_raw_roundtrip():
    raw = {"tags": "t", "lora": "l", "strength": 0.9}
    assert CharacterEntry.from_raw(raw).to_raw() == raw


# -- миграция старого формата LoRA -----------------------------------------


def test_legacy_lora_entry_formats_name_and_strength():
    assert legacy_lora_entry({"lora": "x", "lora_strength": 0.7}) == "x:0.7"


def test_legacy_lora_entry_falls_back_to_plain_strength_key():
    assert legacy_lora_entry({"lora": "x", "strength": 0.4}) == "x:0.4"


def test_legacy_lora_entry_bad_strength_becomes_one():
    assert legacy_lora_entry({"lora": "x", "lora_strength": "много"}) == "x:1"


def test_legacy_lora_entry_none_without_name():
    assert legacy_lora_entry({}) is None
    assert legacy_lora_entry({"lora": "   "}) is None


def _tree():
    return [
        {
            "type": "group",
            "children": [
                {"options": [{"label": "a", "lora": "old_a", "lora_strength": 0.5}]},
                {"options": [{"label": "b", "loras": ["new_b:1"]}]},
            ],
        },
        {"options": [{"label": "c", "lora": "old_c"}]},
    ]


def test_find_legacy_lora_options_walks_groups_recursively():
    found = find_legacy_lora_options(_tree())
    assert [o["label"] for o in found] == ["a", "c"]


def test_migrate_legacy_lora_option_moves_fields_into_loras_list():
    opt = {"label": "a", "lora": "old_a", "lora_strength": 0.5}
    assert migrate_legacy_lora_option(opt) is True
    assert opt == {"label": "a", "loras": ["old_a:0.5"]}


def test_migrate_legacy_lora_option_does_not_duplicate_existing_entry():
    opt = {"lora": "x", "loras": ["x:1"]}
    assert migrate_legacy_lora_option(opt) is True
    assert opt["loras"] == ["x:1"]


def test_migrate_legacy_lora_option_noop_without_legacy_fields():
    opt = {"label": "b", "loras": ["new_b:1"]}
    assert migrate_legacy_lora_option(opt) is False
    assert opt == {"label": "b", "loras": ["new_b:1"]}


def test_display_loras_for_includes_new_and_legacy_without_duplicates():
    node = {"loras": ["a:1"], "lora": "b", "lora_strength": 0.5}
    assert display_loras_for(node) == ["a:1", "b:0.5"]
    assert display_loras_for({"loras": ["b:0.5"], "lora": "b", "lora_strength": 0.5}) == ["b:0.5"]
