"""load_config()/save_config(): повреждённый config.json не теряется молча."""

import json
import os

import pytest

from comfyui_studio.launcher.core import config as cfg_mod
from comfyui_studio.launcher.core.constants import DEFAULT_CONFIG


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(path))
    cfg_mod.take_load_warning()  # сбросить возможный хвост от других тестов
    return path


def test_roundtrip_and_defaults_merge(cfg_path):
    cfg_mod.save_config({"port": 9999})
    cfg = cfg_mod.load_config()
    assert cfg["port"] == 9999
    assert cfg["root_path"] == DEFAULT_CONFIG["root_path"]  # недостающее из defaults
    assert cfg_mod.take_load_warning() is None


def test_missing_config_is_silent(cfg_path):
    assert cfg_mod.load_config() == DEFAULT_CONFIG
    assert cfg_mod.take_load_warning() is None


@pytest.mark.parametrize("content", ['{"root_path": "D:\\\\Comfy', "[1]", ""])
def test_corrupt_config_is_quarantined_and_user_is_warned(cfg_path, content):
    cfg_path.write_text(content, encoding="utf-8")
    cfg = cfg_mod.load_config()
    assert cfg == DEFAULT_CONFIG
    saved = [p for p in os.listdir(cfg_path.parent) if ".corrupt-" in p]
    assert len(saved) == 1
    warning = cfg_mod.take_load_warning()
    assert warning and saved[0] in warning
    assert cfg_mod.take_load_warning() is None  # показывается один раз


def test_corrupt_config_is_not_overwritten_by_next_save(cfg_path):
    cfg_path.write_text("{oops", encoding="utf-8")
    cfg_mod.load_config()
    cfg_mod.save_config({"port": 1234})
    saved = [p for p in os.listdir(cfg_path.parent) if ".corrupt-" in p]
    assert saved and (cfg_path.parent / saved[0]).read_text(encoding="utf-8") == "{oops"
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["port"] == 1234


def test_save_config_failure_is_logged_not_raised(cfg_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("no space")

    monkeypatch.setattr(cfg_mod, "atomic_write_json", boom)
    cfg_mod.save_config({"port": 1})  # не бросает
