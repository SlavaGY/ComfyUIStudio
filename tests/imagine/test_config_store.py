"""imagine/backend/config_store: запись config.json атомарна, битый файл не затирается.

Раньше save_config писал ``CONFIG_PATH.write_text(...)`` поверх файла: сбой
посреди записи оставлял обрезанный config.json, load_config молча отдавал
умолчания, а следующее сохранение окончательно затирало стили и категории
пользователя (та же болезнь, что была у config.json лаунчера).
"""

import json
import os

import pytest

from comfyui_studio.imagine.backend import config_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    data_dir = tmp_path / "imagine"
    monkeypatch.setattr(config_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_store, "CONFIG_PATH", data_dir / "config.json")
    monkeypatch.setattr(config_store, "UPLOADS_DIR", data_dir / "uploads")
    return config_store


def _names(directory):
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def test_first_load_creates_default_file(store):
    config = store.load_config()
    assert store.CONFIG_PATH.is_file()
    assert "styles" in config and "boxes" in config
    assert _names(store.DATA_DIR) == ["config.json"]


def test_save_then_load_roundtrip(store):
    config = store.load_config()
    config["always_loras"] = ["x.safetensors"]
    store.save_config(config)
    assert store.load_config()["always_loras"] == ["x.safetensors"]
    assert _names(store.DATA_DIR) == ["config.json"]  # без *.tmp


def test_failed_save_keeps_previous_config(store, monkeypatch):
    config = store.load_config()
    config["always_loras"] = ["good"]
    store.save_config(config)
    before = store.CONFIG_PATH.read_bytes()

    def boom(src, dst):
        raise OSError("диск недоступен")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            store.save_config({**config, "always_loras": ["bad"]})

    assert store.CONFIG_PATH.read_bytes() == before
    assert _names(store.DATA_DIR) == ["config.json"]


def test_corrupted_config_is_quarantined_and_defaults_returned(store):
    store.load_config()  # создаст файл
    store.CONFIG_PATH.write_text('{"styles": [', encoding="utf-8")  # обрезанный JSON

    config = store.load_config()

    assert "styles" in config  # умолчания
    kept = [n for n in _names(store.DATA_DIR) if ".corrupt-" in n]
    assert len(kept) == 1
    assert (store.DATA_DIR / kept[0]).read_text(encoding="utf-8") == '{"styles": ['
    # следующая загрузка заново создаёт нормальный файл, а не зацикливается
    store.load_config()
    assert json.loads(store.CONFIG_PATH.read_text(encoding="utf-8"))


def test_non_object_root_is_treated_as_corrupt(store):
    store.load_config()
    store.CONFIG_PATH.write_text("[1, 2, 3]", encoding="utf-8")
    config = store.load_config()
    assert isinstance(config, dict) and "styles" in config
    assert [n for n in _names(store.DATA_DIR) if ".corrupt-" in n]
