"""
Характеризационные тесты load_config / save_config (launcher/core/
config.py) -- этап R0 плана рефакторинга, раунд 2. Контракт для R7
(ConfigStore и типизированные секции): после рефакторинга ЧТЕНИЕ уже
существующих config.json должно давать то же самое.

Пути (APP_DIR/CONFIG_PATH) и DEFAULT_CONFIG подменяются на локальные,
чтобы тест не трогал реальный %APPDATA% и не мутировал глобальные
значения по умолчанию.
"""

import copy
import json

import pytest

from comfyui_studio.launcher.core import config as cfg_mod
from comfyui_studio.launcher.core import constants


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    defaults = copy.deepcopy(constants.DEFAULT_CONFIG)
    monkeypatch.setattr(cfg_mod, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(path))
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG", defaults)
    return path, defaults


def test_default_config_schema_is_stable():
    """Набор ключей config.json -- контракт для R7 (см. docs/baseline_R0.md)."""
    d = constants.DEFAULT_CONFIG
    assert set(d) == {
        "root_path", "script", "port", "disable_auto_launch", "sync_comfy_theme",
        "interface", "imagine", "imagine_pony", "env_vars", "log_level", "remote",
    }
    assert d["port"] == 8188
    assert d["interface"] == "comfyui"
    assert d["log_level"] == "INFO"
    assert d["imagine"] == {"port": 7860, "dev_mode": False}
    assert d["imagine_pony"] == {"port": 7862}  # 7860 -- Imagine, 7861 -- Remote
    assert d["remote"] == {
        "enabled": False, "port": 7861, "host": "127.0.0.1", "fcm_service_account_path": None,
    }


def test_load_config_without_file_returns_defaults(cfg_env):
    _path, defaults = cfg_env
    assert cfg_mod.load_config() == defaults


def test_load_config_overrides_top_level_keys_and_keeps_unknown_ones(cfg_env):
    path, defaults = cfg_env
    path.write_text(json.dumps({"port": 9000, "future_key": [1]}), encoding="utf-8")
    cfg = cfg_mod.load_config()
    assert cfg["port"] == 9000
    assert cfg["future_key"] == [1]  # неизвестные ключи не теряются при чтении
    assert cfg["interface"] == defaults["interface"]


def test_load_config_merge_is_shallow_nested_section_replaced_wholesale(cfg_env):
    """Известная особенность: слияние ПОВЕРХНОСТНОЕ. Если в файле есть
    секция remote, то её недостающие ключи (port/host/...) НЕ
    подставляются из значений по умолчанию -- поэтому весь код читает их
    через .get(key, default). R7 должен сохранить результат чтения
    (или осознанно сменить его вместе с потребителями)."""
    path, _defaults = cfg_env
    path.write_text(json.dumps({"remote": {"enabled": True}}), encoding="utf-8")
    cfg = cfg_mod.load_config()
    assert cfg["remote"] == {"enabled": True}
    assert cfg["imagine"] == {"port": 7860, "dev_mode": False}  # секция не тронута


def test_load_config_shares_nested_dicts_with_defaults_known_quirk(cfg_env):
    """Известная особенность (латентная -- нынешний код вложенные секции
    на месте не правит, их собирают заново remote_page.collect() и т. п.):
    DEFAULT_CONFIG.copy() поверхностная, поэтому вложенные словари --
    те же объекты, что в DEFAULT_CONFIG. Правка cfg[\"remote\"] на месте
    испортила бы значения по умолчанию. R7b (датаклассы-секции) это
    устраняет -- тогда этот тест нужно ПЕРЕВЕРНУТЬ осознанно."""
    _path, defaults = cfg_env
    cfg = cfg_mod.load_config()
    assert cfg["remote"] is defaults["remote"]


def test_load_config_corrupted_file_falls_back_to_defaults(cfg_env):
    path, defaults = cfg_env
    path.write_text("{ не json", encoding="utf-8")
    assert cfg_mod.load_config() == defaults


def test_save_then_load_roundtrip_keeps_cyrillic(cfg_env):
    path, _defaults = cfg_env
    cfg = cfg_mod.load_config()
    cfg["root_path"] = "D:/ИИ/ComfyUI"
    cfg_mod.save_config(cfg)

    raw = path.read_text(encoding="utf-8")
    assert "ИИ" in raw and "\\u" not in raw
    assert cfg_mod.load_config()["root_path"] == "D:/ИИ/ComfyUI"


def test_save_config_swallows_write_errors(cfg_env):
    path, _defaults = cfg_env
    path.mkdir()  # CONFIG_PATH -- каталог: open(..., "w") упадёт
    cfg_mod.save_config({"port": 1})  # не должно бросать исключение
