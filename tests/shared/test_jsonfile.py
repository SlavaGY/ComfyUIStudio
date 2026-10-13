"""comfyui_studio.jsonfile: атомарная запись и терпимое чтение."""

import json
import os

import pytest

from comfyui_studio import jsonfile
from comfyui_studio.jsonfile import (
    CORRUPT,
    MISSING,
    OK,
    UNREADABLE,
    atomic_write_json,
    load_json,
    load_json_or_default,
)


def _files(directory):
    # в tmp_path корневой conftest держит свои служебные папки -- смотрим файлы
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def test_write_then_read_roundtrip_unicode(tmp_path):
    path = tmp_path / "sub" / "a.json"  # каталога ещё нет
    atomic_write_json(path, {"имя": "значение", "n": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"имя": "значение", "n": 1}
    assert "имя" in path.read_text(encoding="utf-8")  # ensure_ascii=False


def test_no_temp_files_left_behind(tmp_path):
    path = tmp_path / "a.json"
    atomic_write_json(path, {"a": 1})
    atomic_write_json(path, {"a": 2})
    assert _files(tmp_path) == ["a.json"]


def test_failed_serialization_keeps_old_file_and_cleans_tmp(tmp_path):
    path = tmp_path / "a.json"
    atomic_write_json(path, {"ok": True})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert _files(tmp_path) == ["a.json"]


def test_failed_replace_keeps_old_file_and_cleans_tmp(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    atomic_write_json(path, {"v": 1})

    def boom(src, dst):
        raise OSError("disk says no")

    monkeypatch.setattr(jsonfile.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 2})
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert _files(tmp_path) == ["a.json"]


def test_replace_retries_on_permission_error_on_windows(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        real(src, dst)

    monkeypatch.setattr(jsonfile.sys, "platform", "win32")
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    monkeypatch.setattr(jsonfile.os, "replace", flaky)
    atomic_write_json(tmp_path / "a.json", {"x": 1})
    assert calls["n"] == 3


def test_missing_file(tmp_path):
    result = load_json(tmp_path / "nope.json")
    assert result.status == MISSING and result.data is None


@pytest.mark.parametrize("content", ["{broken", "", "[1, 2]", "5", '{"a": 1}xx'])
def test_corrupt_files_are_reported_not_raised(tmp_path, content):
    path = tmp_path / "a.json"
    path.write_text(content, encoding="utf-8")
    result = load_json(path)
    assert result.status == CORRUPT
    assert path.exists()  # без quarantine файл не трогаем


def test_non_utf8_is_corrupt(tmp_path):
    path = tmp_path / "a.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    assert load_json(path).status == CORRUPT


def test_quarantine_moves_corrupt_file_aside(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"root_path": "D:\\\\Comfy', encoding="utf-8")  # обрезан
    result = load_json(path, quarantine=True)
    assert result.status == CORRUPT
    assert not path.exists()
    assert result.quarantined_to and os.path.exists(result.quarantined_to)
    assert ".corrupt-" in result.quarantined_to
    assert "root_path" in open(result.quarantined_to, encoding="utf-8").read()


def test_quarantine_does_not_overwrite_previous_quarantine(tmp_path):
    path = tmp_path / "config.json"
    moved = []
    for _ in range(2):
        path.write_text("{x", encoding="utf-8")
        moved.append(load_json(path, quarantine=True).quarantined_to)
    assert moved[0] != moved[1]
    assert all(os.path.exists(m) for m in moved)


def test_expected_type_list(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_json(path, expected_type=list).data == [1, 2]


def test_unreadable_is_not_quarantined(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")

    def locked(*a, **k):
        raise PermissionError("locked by another process")

    monkeypatch.setattr("builtins.open", locked)
    result = load_json(path, quarantine=True)
    monkeypatch.undo()
    assert result.status == UNREADABLE
    assert path.exists()


def test_load_json_or_default(tmp_path):
    path = tmp_path / "a.json"
    assert load_json_or_default(path, {"d": 1}) == {"d": 1}
    atomic_write_json(path, {"a": 1})
    assert load_json_or_default(path, {"d": 1}) == {"a": 1}
    assert load_json(path).status == OK


def test_trailing_newline_and_forced_lf(tmp_path):
    path = tmp_path / "a.json"
    atomic_write_json(path, {"a": 1}, newline="\n", trailing_newline=True)
    assert path.read_bytes() == b'{\n  "a": 1\n}\n'  # LF на любой платформе


def test_defaults_do_not_add_trailing_newline(tmp_path):
    path = tmp_path / "a.json"
    atomic_write_json(path, {"a": 1})
    assert not path.read_text(encoding="utf-8").endswith("\n")
