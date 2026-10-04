"""
Тесты ConfigStore (launcher/core/config_store.py) и его проводки — этап R7.

Часть 1 — сам ConfigStore: copy-on-write снимки, запись только при
изменении (кроме force_save), сигнал changed, reload/reset.
Часть 2 — то, ради чего он сделан: диалог настроек, страница, Remote и
окно видят ОДИН конфиг, а отложенное автосохранение (400 мс) не
опережает действия, которые читают конфиг сразу:
  * включение Remote сразу после смены порта;
  * «Запустить» сразу после ввода пути;
  * закрытие диалога с несохранённой правкой.
"""

import copy
import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets")

from comfyui_studio.launcher.core import config as cfg_mod  # noqa: E402
from comfyui_studio.launcher.core import constants  # noqa: E402
from comfyui_studio.launcher.core.config_store import ConfigStore  # noqa: E402


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """Реальный config.json во временной папке (с дефолтами из констант)."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(path))
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG", copy.deepcopy(constants.DEFAULT_CONFIG))
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.config_store.DEFAULT_CONFIG",
        copy.deepcopy(constants.DEFAULT_CONFIG),
    )
    return path


def _on_disk(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ==========================================================================
# ConfigStore
# ==========================================================================


def test_store_loads_from_disk_when_no_initial(disk):
    disk.write_text(json.dumps({"port": 9001}), encoding="utf-8")
    store = ConfigStore()
    assert store.cfg["port"] == 9001
    assert store.cfg["interface"] == "comfyui"  # недостающее — из дефолтов, как у load_config
    assert store.get("port") == 9001 and store.get("nope", "x") == "x"


def test_store_initial_does_not_touch_disk(disk):
    store = ConfigStore(initial={"port": 1})
    assert store.cfg == {"port": 1} and not disk.exists()


def test_update_is_copy_on_write_old_snapshot_stays_frozen(disk):
    store = ConfigStore(initial={"port": 1, "script": "a.bat"})
    snapshot = store.cfg
    assert store.update({"port": 2}) is True
    assert store.cfg is not snapshot
    assert snapshot == {"port": 1, "script": "a.bat"}      # у запуска ComfyUI не меняется
    assert store.cfg == {"port": 2, "script": "a.bat"}


def test_update_persists_and_emits_only_on_change(disk):
    store = ConfigStore(initial={"port": 1})
    seen = []
    store.changed.connect(seen.append)

    assert store.update({"port": 1}) is False
    assert seen == [] and not disk.exists()

    assert store.update({"port": 5}) is True
    assert seen == [{"port": 5}] and _on_disk(disk) == {"port": 5}


def test_update_force_save_writes_even_without_changes_but_no_signal(disk):
    store = ConfigStore(initial={"port": 1})
    seen = []
    store.changed.connect(seen.append)
    assert store.update({"port": 1}, force_save=True) is False
    assert _on_disk(disk) == {"port": 1} and seen == []


def test_update_is_shallow_like_the_old_autosave(disk):
    store = ConfigStore(initial={"remote": {"enabled": True, "port": 7861}, "port": 1})
    store.update({"remote": {"enabled": False}})
    assert store.cfg["remote"] == {"enabled": False}  # секция заменяется целиком


def test_save_writes_current_snapshot(disk):
    store = ConfigStore(initial={"port": 3})
    store.save()
    assert _on_disk(disk) == {"port": 3}


def test_reload_picks_up_disk_and_emits(disk):
    store = ConfigStore(initial={"port": 1})
    seen = []
    store.changed.connect(seen.append)
    disk.write_text(json.dumps({"port": 4242}), encoding="utf-8")
    store.reload()
    assert store.cfg["port"] == 4242 and len(seen) == 1


def test_reset_restores_defaults_without_aliasing_them(disk):
    store = ConfigStore(initial={"port": 1, "root_path": "D:/x"})
    store.reset()
    assert store.cfg["port"] == 8188 and store.cfg["root_path"] == ""
    assert _on_disk(disk)["port"] == 8188
    store.cfg["imagine"]["port"] = 1  # дефолты не должны страдать от мутации
    from comfyui_studio.launcher.core import config_store as cs
    assert cs.DEFAULT_CONFIG["imagine"]["port"] == 7860


def test_custom_save_and_load_hooks_are_used():
    saved = []
    store = ConfigStore(initial={"a": 1}, save=saved.append)
    store.update({"a": 2})
    assert saved == [{"a": 2}]
    store2 = ConfigStore(load=lambda: {"z": 9})
    assert store2.cfg == {"z": 9}


# ==========================================================================
# Диалог настроек + Remote + запуск: один конфиг, flush вместо гонки
# ==========================================================================


@pytest.fixture
def dialog_env(qapp, tmp_path, disk, monkeypatch):
    from comfyui_studio import shared_promptgen as sp
    from comfyui_studio.launcher.ui.settings import app_settings_dialog as mod
    from comfyui_studio.themes.theme_manager import ThemeManager

    # общий файл генератора промптов — во временную папку, не в %APPDATA%
    monkeypatch.setattr(sp, "SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", str(tmp_path / "shared" / "pg.json"))

    store = ConfigStore(initial=copy.deepcopy(constants.DEFAULT_CONFIG))
    dlg = mod.AppSettingsDialog(store, ThemeManager(), loc=None)
    yield dlg, store, disk
    dlg.close()


def test_dialog_uses_given_store_and_exposes_cfg(dialog_env):
    dlg, store, _ = dialog_env
    assert dlg.config is store and dlg.cfg is store.cfg


def test_dialog_without_store_wraps_plain_dict(qapp):
    from comfyui_studio.launcher.ui.settings.app_settings_dialog import AppSettingsDialog
    from comfyui_studio.themes.theme_manager import ThemeManager

    dlg = AppSettingsDialog(dict(constants.DEFAULT_CONFIG), ThemeManager(), loc=None)
    assert isinstance(dlg.config, ConfigStore)
    dlg.close()


def test_autosave_updates_the_shared_store_and_disk(dialog_env):
    dlg, store, disk = dialog_env
    dlg.comfyui_page.port_spin.setValue(9100)
    dlg._save_timer.stop()
    dlg._auto_save()
    assert store.cfg["port"] == 9100
    assert _on_disk(disk)["port"] == 9100
    assert dlg.comfyui_page.cfg is store.cfg  # страницы получают тот же снимок


def test_flush_saves_pending_edit_immediately(dialog_env):
    dlg, store, disk = dialog_env
    dlg.comfyui_page.port_spin.setValue(9200)
    assert dlg._save_timer.isActive()
    dlg.flush()
    assert not dlg._save_timer.isActive()
    assert store.cfg["port"] == 9200 and _on_disk(disk)["port"] == 9200


def test_flush_without_pending_edit_does_nothing(dialog_env):
    dlg, store, disk = dialog_env
    dlg.flush()
    assert not disk.exists()


def test_hide_flushes_pending_edit(dialog_env):
    dlg, store, disk = dialog_env
    dlg.show()
    dlg.comfyui_page.port_spin.setValue(9300)
    dlg.hide()
    assert _on_disk(disk)["port"] == 9300


def test_remote_enable_toggle_flushes_before_notifying(dialog_env):
    """Включение Remote сразу после смены порта: к моменту сигнала порт
    уже в хранилище (раньше дебаунс опережал, и Remote стартовал по
    старому порту из config.json)."""
    dlg, store, disk = dialog_env
    seen = []
    dlg.remote_enable_toggled.connect(lambda enabled: seen.append((enabled, store.cfg["remote"].copy())))

    dlg.remote_page.port_spin.setValue(7999)
    dlg.remote_page.enable_check.setChecked(True)

    assert len(seen) == 1
    enabled, remote_cfg_at_signal = seen[0]
    assert enabled is True
    assert remote_cfg_at_signal["port"] == 7999 and remote_cfg_at_signal["enabled"] is True


def test_remote_controller_starts_on_port_just_typed(dialog_env, monkeypatch):
    """Сквозной сценарий гонки: настоящие диалог + RemoteController на
    одном хранилище."""
    from comfyui_studio.launcher.ui import remote_controller as rc

    dlg, store, disk = dialog_env
    started = []

    class FakeProc:
        def __init__(self, **kw):
            self.kw = kw
            self.host, self.port = kw["host"], kw["port"]
            started.append(kw)

        def start(self):
            pass

        def is_running(self):
            return True

    monkeypatch.setattr(rc, "RemoteProcess", FakeProc)
    monkeypatch.setattr(rc, "QTimer", MagicMock())
    ctl = rc.RemoteController(MagicMock(), config=store)
    dlg.remote_enable_toggled.connect(ctl.on_enable_toggled)

    dlg.remote_page.port_spin.setValue(7999)
    dlg.remote_page.lan_access_check.setChecked(True)
    dlg.remote_page.enable_check.setChecked(True)

    assert started[0]["port"] == 7999 and started[0]["host"] == "0.0.0.0"


def test_remote_controller_with_store_does_not_read_disk(monkeypatch):
    from comfyui_studio.launcher.ui import remote_controller as rc

    monkeypatch.setattr(rc, "load_config", lambda: pytest.fail("читать диск не нужно"))
    monkeypatch.setattr(rc, "QTimer", MagicMock())
    created = []

    class FakeProc:
        def __init__(self, **kw):
            created.append(kw)
            self.host, self.port = kw["host"], kw["port"]

        def start(self):
            pass

    monkeypatch.setattr(rc, "RemoteProcess", FakeProc)
    store = ConfigStore(initial={"port": 8200, "imagine": {"port": 7999},
                                 "remote": {"port": 7862, "host": "0.0.0.0"}})
    rc.RemoteController(MagicMock(), config=store).start()
    assert created == [{
        "host": "0.0.0.0", "port": 7862, "comfy_host": "127.0.0.1",
        "comfy_port": 8200, "imagine_port": 7999,
    }]


def test_launch_flushes_pending_edit_and_launches_with_it(qapp, disk, monkeypatch, tmp_path):
    """«Запустить» сразу после ввода порта: cfg для запуска уже содержит
    правку (раньше брался снимок диалога без отложенных изменений)."""
    from comfyui_studio.launcher.ui import settings_page as sp_mod
    from comfyui_studio.themes.theme_manager import ThemeManager

    root = tmp_path / "comfy"
    (root / "python_embeded").mkdir(parents=True)
    (root / "ComfyUI").mkdir()
    (root / "python_embeded" / "python.exe").write_text("")
    (root / "ComfyUI" / "main.py").write_text("")
    (root / "run_cpu.bat").write_text("")
    monkeypatch.setattr(sp_mod, "validate_portable_root", lambda r: (True, "OK"))

    cfg = copy.deepcopy(constants.DEFAULT_CONFIG)
    cfg.update(root_path=str(root), script="run_cpu.bat")
    store = ConfigStore(initial=cfg)
    page = sp_mod.SettingsPage(store, ThemeManager(), loc=None)
    launched = []
    page.launch_requested.connect(launched.append)

    page.settings_dialog.comfyui_page.port_spin.setValue(9400)   # дебаунс ещё не сработал
    page._on_launch()

    assert len(launched) == 1 and launched[0]["port"] == 9400
    assert _on_disk(disk)["port"] == 9400
    # Signal(dict) отдаёт получателю копию (QVariantMap), не сам объект
    assert launched[0]["root_path"] == str(root) and launched[0] is not store.cfg
    page.settings_dialog.close()


def test_launch_snapshot_is_not_changed_by_later_settings_edits(qapp, disk, monkeypatch):
    from comfyui_studio.launcher.ui import settings_page as sp_mod
    from comfyui_studio.themes.theme_manager import ThemeManager

    monkeypatch.setattr(sp_mod, "validate_portable_root", lambda r: (True, "OK"))
    cfg = copy.deepcopy(constants.DEFAULT_CONFIG)
    cfg.update(root_path="D:/c", script="run.bat", port=8188)
    store = ConfigStore(initial=cfg)
    page = sp_mod.SettingsPage(store, ThemeManager(), loc=None)
    launched = []
    page.launch_requested.connect(launched.append)
    page._on_launch()
    store.update({"port": 9999})                    # правка после запуска
    assert launched[0]["port"] == 8188              # запущенная сессия видит свой порт
    assert store.cfg["port"] == 9999
    page.settings_dialog.close()


def test_reset_confirmed_reloads_store_from_disk(dialog_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dlg, store, disk = dialog_env
    store.update({"port": 9500})
    cfg_mod.save_config(copy.deepcopy(constants.DEFAULT_CONFIG))  # «как после сброса»
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg._on_reset_confirmed()
    assert store.cfg["port"] == 8188
