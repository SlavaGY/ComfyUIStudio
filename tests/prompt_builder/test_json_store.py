"""
Характеризационные тесты prompt_builder/json_store.py (этап R0 плана
рефакторинга, раунд 2): атомарное сохранение, ротация резервных копий,
сообщения об ошибках чтения.

json_store.py импортирует pb_settings (а он -- QSettings из PySide6),
поэтому get_backup_keep() ниже ВСЕГДА подменяется: иначе тест читал бы
реальные настройки \"PromptConfigEditor\" с машины разработчика (на
Windows -- из реестра) и результат зависел бы от них.
"""

import json

import pytest

from comfyui_studio.prompt_builder import json_store
from comfyui_studio.prompt_builder.json_store import JsonStoreError, load_json, save_json


@pytest.fixture(autouse=True)
def _fixed_backup_keep(monkeypatch):
    monkeypatch.setattr(json_store, "get_backup_keep", lambda: 3)


def _backups(path):
    return sorted(p.name for p in path.parent.glob(path.name + ".bak-*"))


# -- load_json -------------------------------------------------------------


def test_load_json_missing_file_raises_with_path(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(JsonStoreError, match="Файл не найден"):
        load_json(str(missing))


def test_load_json_invalid_json_reports_line_and_column(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{\n  "a": 1,\n  "b": \n}', encoding="utf-8")
    with pytest.raises(JsonStoreError) as exc:
        load_json(str(p))
    text = str(exc.value)
    assert "bad.json" in text
    assert "строка" in text and "столбец" in text


def test_load_json_accepts_utf8_bom(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"k": "v"}).encode("utf-8"))
    assert load_json(str(p)) == {"k": "v"}


# -- save_json -------------------------------------------------------------


def test_save_json_keeps_cyrillic_unescaped_and_indents(tmp_path):
    p = tmp_path / "chars.json"
    save_json(str(p), {"имя": "значение"})
    raw = p.read_text(encoding="utf-8")
    assert "имя" in raw and "\\u" not in raw
    assert raw == '{\n  "имя": "значение"\n}\n'


def test_save_json_creates_missing_directories(tmp_path):
    p = tmp_path / "a" / "b" / "cfg.json"
    save_json(str(p), [1, 2])
    assert json.loads(p.read_text(encoding="utf-8")) == [1, 2]


def test_save_json_roundtrip_through_load_json(tmp_path):
    p = tmp_path / "cfg.json"
    data = {"categories": [{"type": "group", "children": []}], "n": 1.5}
    save_json(str(p), data)
    assert load_json(str(p)) == data


def test_save_json_leaves_no_temp_files(tmp_path):
    p = tmp_path / "cfg.json"
    save_json(str(p), {"a": 1})
    save_json(str(p), {"a": 2})
    assert not [f for f in tmp_path.iterdir() if ".tmp-" in f.name]


def test_save_json_no_backup_for_new_file(tmp_path):
    p = tmp_path / "cfg.json"
    save_json(str(p), {"a": 1})
    assert _backups(p) == []


def test_save_json_backs_up_previous_content(tmp_path):
    p = tmp_path / "cfg.json"
    save_json(str(p), {"v": 1})
    save_json(str(p), {"v": 2})
    backups = _backups(p)
    assert len(backups) == 1
    assert json.loads((tmp_path / backups[0]).read_text(encoding="utf-8")) == {"v": 1}
    assert load_json(str(p)) == {"v": 2}


def test_save_json_make_backup_false_skips_backup(tmp_path):
    p = tmp_path / "cfg.json"
    save_json(str(p), {"v": 1})
    save_json(str(p), {"v": 2}, make_backup=False)
    assert _backups(p) == []


def test_backup_rotation_keeps_only_newest_n(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text("{}", encoding="utf-8")
    old = [f"cfg.json.bak-2020010{i}-000000" for i in range(1, 6)]
    for name in old:
        (tmp_path / name).write_text("{}", encoding="utf-8")

    save_json(str(p), {"v": 1})  # keep=3: новый бэкап + два самых свежих старых

    left = _backups(p)
    assert len(left) == 3
    assert old[-1] in left and old[-2] in left
    assert old[0] not in left and old[1] not in left and old[2] not in left


def test_backup_keep_zero_removes_even_the_new_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(json_store, "get_backup_keep", lambda: 0)
    p = tmp_path / "cfg.json"
    save_json(str(p), {"v": 1})
    save_json(str(p), {"v": 2})
    assert _backups(p) == []


def test_save_json_replace_failure_raises_and_cleans_temp(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"

    def boom(src, dst):
        raise OSError("диск недоступен")

    monkeypatch.setattr(json_store.os, "replace", boom)
    with pytest.raises(JsonStoreError, match="Не удалось сохранить"):
        save_json(str(p), {"a": 1}, make_backup=False)
    assert not [f for f in tmp_path.iterdir() if ".tmp-" in f.name]
    assert not p.exists()
