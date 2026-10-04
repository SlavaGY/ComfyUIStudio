"""
Характеризационные тесты MainWindow (launcher/ui/launcher_window.py) --
этап R0 плана рефакторинга, раунд 2. Фиксируют ТЕКУЩЕЕ поведение цепочки
запуска ComfyUI -> Imagine, остановки/отмены и блока Remote ПЕРЕД тем,
как R3-R5 вынесут их из окна в контроллеры.

Как устроено. Реальное окно (QMainWindow + SettingsPage + BrowserPage +
QtWebEngine) здесь не создаётся: методы MainWindow копируются в обычный
класс-каркас (_make_window), а все зависимости -- settings_page,
browser_page, stack, watchers, процессы -- это MagicMock/фейки. Так
тесты не требуют QApplication и не зависят от внешнего вида окна.
Модуль launcher_window импортируется лениво, внутри фикстуры: он тянет
QtWebEngine, а launcher/__init__.py намеренно ленивый, чтобы тесты
launcher.core.* работали без него.

Что НЕ проверяется: сам вид страниц, трей, тема, WebEngine.

Если после рефакторинга какой-то из этих тестов приходится менять --
это сигнал, что изменилось поведение, а не только структура кода:
сначала убедитесь, что так и задумано.
"""

import inspect
import urllib.error
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def lw():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from comfyui_studio.launcher.ui import launcher_window

    return launcher_window


class FakeProcess:
    """Общий фейк ComfyProcess / ImagineProcess / RemoteProcess."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.running = True
        self.code = None
        self.start_error = None
        self.host = kwargs.get("host")
        self.port = kwargs.get("port")
        type(self).instances.append(self)

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stopped = True
        self.running = False

    def is_running(self):
        return self.running

    def exit_code(self):
        return self.code


def _fake_process_class(name):
    return type(name, (FakeProcess,), {"instances": []})


class FakeQTimer:
    """Подмена QTimer в launcher_window: запоминает singleShot вместо
    реального отложенного вызова."""

    calls = []

    @staticmethod
    def singleShot(ms, fn):
        FakeQTimer.calls.append((ms, fn))


def _make_window(lw, cfg=None):
    # __init__ не копируем: он создаёт настоящие страницы и процессы.
    methods = {
        k: v
        for k, v in vars(lw.MainWindow).items()
        if inspect.isfunction(v) and not k.startswith("__")
    }
    win = type("MainWindowHarness", (), methods)()
    win.cfg = cfg if cfg is not None else {
        "root_path": "C:/comfy",
        "script": "run.bat",
        "port": 8188,
        "interface": "comfyui",
        "sync_comfy_theme": False,
        "env_vars": {"A": "1"},
        "imagine": {"port": 7870, "dev_mode": True},
        "remote": {"enabled": False, "port": 7861, "host": "127.0.0.1"},
    }
    win.settings_page = MagicMock()
    win.settings_page._tr.side_effect = lambda text: text
    win.browser_page = MagicMock()
    win.stack = MagicMock()
    win.launch_watcher = MagicMock()
    win.imagine_launch_watcher = MagicMock()
    win.log_bridge = MagicMock()
    win.theme_manager = MagicMock()
    win.comfy_process = None
    win.imagine_process = None
    win.remote_process = None
    win._remote_ready_attempts = 0
    return win


@pytest.fixture
def env(lw, monkeypatch):
    """Окно-каркас + подменённые во всём модуле процессы/хелперы."""
    FakeQTimer.calls = []
    comfy = _fake_process_class("FakeComfy")
    imagine = _fake_process_class("FakeImagine")
    remote = _fake_process_class("FakeRemote")
    monkeypatch.setattr(lw, "ComfyProcess", comfy)
    monkeypatch.setattr(lw, "ImagineProcess", imagine)
    monkeypatch.setattr(lw, "RemoteProcess", remote)
    monkeypatch.setattr(lw, "QTimer", FakeQTimer)
    monkeypatch.setattr(lw, "prepare_launch_script", lambda root, script, extra: "launch.bat")
    monkeypatch.setattr(lw, "build_extra_launch_args", lambda cfg: [])
    monkeypatch.setattr(lw, "sync_comfyui_color_palette", MagicMock())

    class Env:
        pass

    e = Env()
    e.lw, e.win = lw, _make_window(lw)
    e.comfy, e.imagine, e.remote = comfy, imagine, remote
    return e


# -- запуск ComfyUI --------------------------------------------------------


def test_launch_creates_starts_comfy_and_arms_watcher(env):
    win = env.win
    win._on_launch(win.cfg)

    proc = env.comfy.instances[0]
    assert win.comfy_process is proc
    assert proc.started is True
    assert proc.args == ("C:/comfy", "launch.bat", win.log_bridge)
    assert proc.kwargs == {"env_overrides": {"A": "1"}}
    win.settings_page.show_launch_progress.assert_called_once()
    win.launch_watcher.start.assert_called_once_with(8188, proc)


def test_launch_remembers_passed_cfg(env):
    win = env.win
    new_cfg = dict(win.cfg, port=9000)
    win._on_launch(new_cfg)
    assert win.cfg is new_cfg
    win.launch_watcher.start.assert_called_once()
    assert win.launch_watcher.start.call_args.args[0] == 9000


def test_launch_script_error_sets_status_and_does_not_start(env, monkeypatch):
    def boom(root, script, extra):
        raise OSError("нет доступа")

    monkeypatch.setattr(env.lw, "prepare_launch_script", boom)
    win = env.win
    win._on_launch(win.cfg)

    assert win.comfy_process is None
    assert env.comfy.instances == []
    win.launch_watcher.start.assert_not_called()
    status = win.settings_page.set_status.call_args.args[0]
    assert "Не удалось подготовить скрипт запуска" in status and "нет доступа" in status


def test_launch_syncs_comfy_palette_only_when_enabled(env):
    win = env.win
    win._on_launch(win.cfg)
    env.lw.sync_comfyui_color_palette.assert_not_called()

    cfg = dict(win.cfg, sync_comfy_theme=True)
    win._on_launch(cfg)
    env.lw.sync_comfyui_color_palette.assert_called_once_with(
        "C:/comfy", win.theme_manager.current_theme()
    )


# -- ComfyUI поднялся ------------------------------------------------------


def test_server_ready_comfyui_interface_opens_embedded_browser(env):
    win = env.win
    win._on_server_ready()

    win.browser_page.load.assert_called_once_with(8188)
    win.stack.setCurrentWidget.assert_called_once_with(win.browser_page)
    win.settings_page.hide_launch_progress.assert_called_once()
    assert env.imagine.instances == []


def test_server_ready_imagine_interface_starts_imagine_instead(env):
    win = env.win
    win.cfg["interface"] = "imagine"
    win._on_server_ready()

    proc = env.imagine.instances[0]
    assert win.imagine_process is proc and proc.started is True
    assert proc.kwargs["host"] == "127.0.0.1"
    assert proc.kwargs["port"] == 7870
    assert proc.kwargs["comfy_host"] == "127.0.0.1"
    assert proc.kwargs["comfy_port"] == 8188
    assert proc.kwargs["dev_mode"] is True
    assert proc.kwargs["remote_port"] == 7861
    win.imagine_launch_watcher.start.assert_called_once_with(7870, proc)
    win.browser_page.load.assert_not_called()  # браузер откроется после Imagine


def test_imagine_defaults_port_7860_and_no_dev_mode(env):
    win = env.win
    win.cfg["interface"] = "imagine"
    win.cfg["imagine"] = {}
    win.cfg.pop("remote")
    win._on_server_ready()

    proc = env.imagine.instances[0]
    assert proc.kwargs["port"] == 7860
    assert proc.kwargs["dev_mode"] is False
    assert proc.kwargs["remote_port"] is None


def test_imagine_start_error_rolls_back_both_processes(env):
    win = env.win
    win.cfg["interface"] = "imagine"
    win.comfy_process = env.comfy()
    # Следующий созданный ImagineProcess не сможет стартовать.
    original_init = env.imagine.__init__

    def init_failing(self, *a, **k):
        original_init(self, *a, **k)
        self.start_error = RuntimeError("порт занят")

    env.imagine.__init__ = init_failing

    win._on_server_ready()

    assert env.imagine.instances[0].stopped is True
    assert win.comfy_process.stopped is True  # откат ПОЛНЫЙ: Comfy тоже гасится
    status = win.settings_page.set_status.call_args.args[0]
    assert "Не удалось запустить Imagine" in status and "порт занят" in status
    win.settings_page.set_server_running.assert_called_with(False)
    win.imagine_launch_watcher.start.assert_not_called()


def test_imagine_ready_loads_browser_on_imagine_port(env):
    win = env.win
    win._on_imagine_ready()
    win.browser_page.load.assert_called_once_with(7870)
    win.stack.setCurrentWidget.assert_called_once_with(win.browser_page)
    win.settings_page.hide_launch_progress.assert_called_once()


def test_imagine_failed_stops_both_and_shows_message(env):
    win = env.win
    win.comfy_process = env.comfy()
    win.imagine_process = env.imagine()
    win._on_imagine_failed("Imagine не отвечает")

    assert win.imagine_process.stopped and win.comfy_process.stopped
    win.settings_page.set_status.assert_called_with("Imagine не отвечает")
    win.settings_page.set_server_running.assert_called_with(False)
    win.settings_page.hide_launch_progress.assert_called_once()


# -- сбои, отмена, остановка -----------------------------------------------


def test_server_failed_stops_comfy_and_reports(env):
    win = env.win
    win.comfy_process = env.comfy()
    win._on_server_failed("ComfyUI не поднялся")

    assert win.comfy_process.stopped is True
    win.settings_page.hide_launch_progress.assert_called_once()
    win.settings_page.set_status.assert_called_with("ComfyUI не поднялся")
    win.settings_page.set_server_running.assert_called_with(False)


def test_server_failed_without_process_does_not_crash(env):
    env.win._on_server_failed("x")
    env.win.settings_page.set_status.assert_called_with("x")


def test_launch_cancelled_stops_watchers_and_processes(env):
    win = env.win
    comfy, imagine = env.comfy(), env.imagine()
    win.comfy_process, win.imagine_process = comfy, imagine
    win._on_launch_cancelled()

    win.launch_watcher.stop.assert_called_once()
    win.imagine_launch_watcher.stop.assert_called_once()
    assert comfy.stopped and imagine.stopped
    assert win.imagine_process is None  # Imagine обнуляется, Comfy-объект остаётся
    win.settings_page.set_status.assert_called_with("Запуск отменён.")
    win.settings_page.set_server_running.assert_called_with(False)


def test_stop_and_show_settings_returns_to_settings_page(env):
    win = env.win
    comfy, imagine = env.comfy(), env.imagine()
    win.comfy_process, win.imagine_process = comfy, imagine
    win._stop_and_show_settings()

    win.browser_page.unload.assert_called_once()
    assert comfy.stopped and imagine.stopped
    assert win.imagine_process is None
    win.settings_page.set_status.assert_called_with("")
    win.settings_page.set_server_running.assert_called_with(False)
    win.stack.setCurrentWidget.assert_called_with(win.settings_page)


def test_show_settings_keep_running_reports_actual_state(env):
    win = env.win
    win.comfy_process = env.comfy()
    win._show_settings_keep_running()
    win.settings_page.set_server_running.assert_called_with(True, 8188)
    win.stack.setCurrentWidget.assert_called_with(win.settings_page)

    win.comfy_process.running = False
    win._show_settings_keep_running()
    win.settings_page.set_server_running.assert_called_with(False, 8188)


def test_get_running_port_only_while_comfy_is_alive(env):
    win = env.win
    assert win._get_running_port() is None
    win.comfy_process = env.comfy()
    assert win._get_running_port() == 8188
    win.comfy_process.running = False
    assert win._get_running_port() is None


# -- Remote: запуск и опрос готовности -------------------------------------


def test_start_remote_reads_fresh_config_from_disk_not_stale_self_cfg(env, monkeypatch):
    """Регрессия 2026-09-06 (см. комментарий в _start_remote): настройки
    автосохраняются из диалога, а self.cfg окна устаревает -- поэтому
    порт/хост берутся из свежего load_config(), а не из self.cfg."""
    win = env.win
    win.cfg["remote"] = {"port": 1111, "host": "127.0.0.1"}  # устаревший снимок
    monkeypatch.setattr(
        env.lw,
        "load_config",
        lambda: {
            "port": 8200,
            "imagine": {"port": 7999},
            "remote": {"port": 7862, "host": "0.0.0.0"},
        },
    )
    win._start_remote()

    proc = env.remote.instances[0]
    assert win.remote_process is proc and proc.started is True
    assert proc.kwargs == {
        "host": "0.0.0.0",
        "port": 7862,
        "comfy_host": "127.0.0.1",
        "comfy_port": 8200,
        "imagine_port": 7999,
    }
    assert win._remote_ready_attempts == 0
    assert [ms for ms, _ in FakeQTimer.calls] == [300]
    assert FakeQTimer.calls[0][1] == win._poll_remote_ready


def test_start_remote_defaults_when_config_sections_missing(env, monkeypatch):
    monkeypatch.setattr(env.lw, "load_config", lambda: {})
    env.win._start_remote()
    kwargs = env.remote.instances[0].kwargs
    assert kwargs["port"] == 7861 and kwargs["host"] == "127.0.0.1"
    assert kwargs["comfy_port"] is None and kwargs["imagine_port"] is None


def test_start_remote_error_resets_process_and_reports(env, monkeypatch):
    monkeypatch.setattr(env.lw, "load_config", lambda: {})
    original_init = env.remote.__init__

    def init_failing(self, *a, **k):
        original_init(self, *a, **k)
        self.start_error = RuntimeError("не найден uvicorn")

    env.remote.__init__ = init_failing

    win = env.win
    win._start_remote()
    assert win.remote_process is None
    win.settings_page.set_remote_running_state.assert_called_once_with(
        False, error="не найден uvicorn"
    )
    assert FakeQTimer.calls == []  # опрос готовности не запускается


def test_poll_remote_ready_noop_without_process(env):
    env.win._poll_remote_ready()
    env.win.settings_page.set_remote_running_state.assert_not_called()
    assert FakeQTimer.calls == []


def test_poll_remote_ready_reports_premature_exit(env):
    win = env.win
    win.remote_process = env.remote(host="127.0.0.1", port=7861)
    win.remote_process.running = False
    win.remote_process.code = 3
    win._poll_remote_ready()

    assert win.remote_process is None
    state, = win.settings_page.set_remote_running_state.call_args.args
    assert state is False
    error = win.settings_page.set_remote_running_state.call_args.kwargs["error"]
    assert "код выхода 3" in error


def test_poll_remote_ready_success_on_localhost_hides_lan_url(env, monkeypatch):
    monkeypatch.setattr(env.lw, "is_remote_available", lambda port: True)
    win = env.win
    win.remote_process = env.remote(host="127.0.0.1", port=7861)
    win._poll_remote_ready()

    win.settings_page.set_remote_running_state.assert_called_once_with(True)
    win.settings_page.show_lan_url.assert_called_once_with(None)
    assert FakeQTimer.calls == []


def test_poll_remote_ready_success_on_lan_shows_lan_url(env, monkeypatch):
    monkeypatch.setattr(env.lw, "is_remote_available", lambda port: True)
    monkeypatch.setattr(env.lw, "get_lan_ip", lambda: "192.168.1.5")
    win = env.win
    win.remote_process = env.remote(host="0.0.0.0", port=7861)
    win._poll_remote_ready()
    win.settings_page.show_lan_url.assert_called_once_with("http://192.168.1.5:7861/")


def test_poll_remote_ready_lan_ip_unknown_shows_none(env, monkeypatch):
    monkeypatch.setattr(env.lw, "is_remote_available", lambda port: True)
    monkeypatch.setattr(env.lw, "get_lan_ip", lambda: None)
    win = env.win
    win.remote_process = env.remote(host="0.0.0.0", port=7861)
    win._poll_remote_ready()
    win.settings_page.show_lan_url.assert_called_once_with(None)


def test_poll_remote_ready_retries_then_gives_up_after_20_attempts(env, monkeypatch):
    monkeypatch.setattr(env.lw, "is_remote_available", lambda port: False)
    win = env.win
    win.remote_process = env.remote(host="127.0.0.1", port=7861)

    for _ in range(19):
        win._poll_remote_ready()
    assert len(FakeQTimer.calls) == 19  # 19 раз перепланирует себя через 300 мс
    assert all(ms == 300 for ms, _ in FakeQTimer.calls)
    win.settings_page.set_remote_running_state.assert_not_called()

    win._poll_remote_ready()  # 20-я попытка -- сдаётся
    assert len(FakeQTimer.calls) == 19
    assert win.settings_page.set_remote_running_state.call_args.args == (False,)
    assert "не поднялся вовремя" in win.settings_page.set_remote_running_state.call_args.kwargs["error"]


def test_stop_remote_stops_process_and_updates_state(env):
    win = env.win
    proc = env.remote(host="127.0.0.1", port=7861)
    win.remote_process = proc
    win._stop_remote()
    assert proc.stopped is True and win.remote_process is None
    win.settings_page.set_remote_running_state.assert_called_with(False)


def test_enable_toggle_starts_only_when_not_already_running(env):
    win = env.win
    win._start_remote = MagicMock()
    win._stop_remote = MagicMock()

    win._on_remote_enable_toggled(True)
    win._start_remote.assert_called_once()

    win.remote_process = env.remote()
    win._on_remote_enable_toggled(True)
    win._start_remote.assert_called_once()  # повторно не стартует

    win._on_remote_enable_toggled(False)
    win._stop_remote.assert_called_once()


# -- Remote: pairing / устройства (синхронные вызовы локального API) -------


def _http_error(code=500):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


@pytest.fixture
def remote_env(env, monkeypatch):
    env.api_calls = []
    env.api_result = None
    env.api_error = None

    def call_local_api(port, method, path, *a, **k):
        env.api_calls.append((port, method, path))
        if env.api_error is not None:
            raise env.api_error
        return env.api_result

    monkeypatch.setattr(env.lw, "call_local_api", call_local_api)
    monkeypatch.setattr(env.lw, "extract_error_detail", lambda e: "detail-from-http")
    env.win.remote_process = env.remote(host="127.0.0.1", port=7861)
    return env


def test_pairing_requires_running_remote(env, monkeypatch):
    monkeypatch.setattr(env.lw, "call_local_api", MagicMock())
    env.win._on_remote_pairing_requested()
    env.win.settings_page.show_remote_pairing_error.assert_called_once()
    assert "Remote не запущен" in env.win.settings_page.show_remote_pairing_error.call_args.args[0]
    env.lw.call_local_api.assert_not_called()


def test_pairing_success_shows_code(remote_env):
    remote_env.api_result = {"code": "123456", "expires_at": 1700000000, "attempts_left": 5}
    remote_env.win._on_remote_pairing_requested()
    assert remote_env.api_calls == [(7861, "POST", "/api/v1/remote/pair/start")]
    remote_env.win.settings_page.show_remote_pairing_code.assert_called_once_with(
        "123456", 1700000000, 5
    )


def test_pairing_http_error_shows_extracted_detail(remote_env):
    remote_env.api_error = _http_error()
    remote_env.win._on_remote_pairing_requested()
    remote_env.win.settings_page.show_remote_pairing_error.assert_called_once_with(
        "detail-from-http"
    )


def test_pairing_url_error_shows_error_text(remote_env):
    remote_env.api_error = urllib.error.URLError("connection refused")
    remote_env.win._on_remote_pairing_requested()
    text = remote_env.win.settings_page.show_remote_pairing_error.call_args.args[0]
    assert "connection refused" in text


def test_refresh_devices_requires_running_remote(env):
    env.win._on_remote_refresh_devices_requested()
    assert "Remote не запущен" in env.win.settings_page.show_remote_devices_error.call_args.args[0]


def test_refresh_devices_success_passes_list_through(remote_env):
    remote_env.api_result = [{"id": "d1"}]
    remote_env.win._on_remote_refresh_devices_requested()
    assert remote_env.api_calls == [(7861, "GET", "/api/v1/remote/devices")]
    remote_env.win.settings_page.set_remote_devices.assert_called_once_with([{"id": "d1"}])


def test_refresh_devices_empty_response_becomes_empty_list(remote_env):
    remote_env.api_result = None
    remote_env.win._on_remote_refresh_devices_requested()
    remote_env.win.settings_page.set_remote_devices.assert_called_once_with([])


def test_refresh_devices_errors_are_reported(remote_env):
    remote_env.api_error = _http_error()
    remote_env.win._on_remote_refresh_devices_requested()
    remote_env.win.settings_page.show_remote_devices_error.assert_called_with("detail-from-http")

    remote_env.api_error = urllib.error.URLError("нет сети")
    remote_env.win._on_remote_refresh_devices_requested()
    assert "нет сети" in remote_env.win.settings_page.show_remote_devices_error.call_args.args[0]


def test_revoke_calls_api_per_device_then_refreshes(remote_env):
    remote_env.api_result = []
    remote_env.win._on_remote_revoke_requested(["a", "b"])
    assert remote_env.api_calls[:2] == [
        (7861, "POST", "/api/v1/remote/devices/a/revoke"),
        (7861, "POST", "/api/v1/remote/devices/b/revoke"),
    ]
    assert remote_env.api_calls[2] == (7861, "GET", "/api/v1/remote/devices")
    remote_env.win.settings_page.set_remote_devices.assert_called_once_with([])


def test_revoke_stops_at_first_error_and_does_not_refresh(remote_env):
    remote_env.api_error = _http_error()
    remote_env.win._on_remote_revoke_requested(["a", "b"])
    assert len(remote_env.api_calls) == 1  # на втором устройстве уже не пытается
    remote_env.win.settings_page.show_remote_devices_error.assert_called_once_with(
        "detail-from-http"
    )
    remote_env.win.settings_page.set_remote_devices.assert_not_called()


def test_revoke_requires_running_remote(env, monkeypatch):
    monkeypatch.setattr(env.lw, "call_local_api", MagicMock())
    env.win._on_remote_revoke_requested(["a"])
    env.lw.call_local_api.assert_not_called()
    env.win.settings_page.show_remote_devices_error.assert_called_once()
