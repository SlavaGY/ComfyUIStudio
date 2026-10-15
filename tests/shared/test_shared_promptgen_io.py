"""shared_promptgen.read_settings / write_settings: чтение терпимо, запись атомарна."""

import json

import pytest

from comfyui_studio import shared_promptgen as sp


@pytest.fixture
def path(tmp_path, monkeypatch):
    p = tmp_path / "shared" / "prompt_generator.json"
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", str(p))
    return p


def test_missing_file_gives_defaults(path):
    assert sp.read_settings() == dict(sp.DEFAULTS)


def test_corrupted_file_gives_defaults(path):
    path.parent.mkdir(parents=True)
    path.write_text("{не json", encoding="utf-8")
    assert sp.read_settings() == dict(sp.DEFAULTS)


def test_non_object_root_gives_defaults(path):
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2]", encoding="utf-8")
    assert sp.read_settings() == dict(sp.DEFAULTS)


def test_wrong_type_of_settings_version_does_not_crash(path):
    """Руками отредактированный файл: сравнение "2" < int раньше ловилось
    широким except, теперь -- явно (TypeError)."""
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"settings_version": "2"}), encoding="utf-8")
    assert sp.read_settings() == dict(sp.DEFAULTS)


def test_write_then_read_roundtrip_leaves_no_temp_files(path):
    assert sp.write_settings({**sp.DEFAULTS}) is True
    assert sp.read_settings()["settings_version"] == sp.SETTINGS_VERSION
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_write_failure_returns_false_and_keeps_old_file(path, monkeypatch):
    assert sp.write_settings({**sp.DEFAULTS}) is True
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("диск недоступен")

    with monkeypatch.context() as patch:
        patch.setattr(sp.os, "replace", boom)
        assert sp.write_settings({**sp.DEFAULTS}) is False

    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]
