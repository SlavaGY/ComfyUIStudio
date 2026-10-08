"""
Тесты RemoteController (launcher/ui/remote_controller.py) — этап R4.

Большая часть — перенос Remote-тестов из test_main_window_flow.py (R0)
без изменения ожиданий: запуск со свежим конфигом, опрос готовности
(20 попыток по 300 мс), остановка, pairing / список / отзыв устройств.
Добавлено то, ради чего делался R4: вызовы локального API идут в
рабочем потоке, а результат приходит в GUI-поток; устаревшие ответы
отбрасываются; повторный клик не плодит запросы.

Для детерминизма почти все тесты подменяют spawn на синхронный запуск;
отдельная группа в конце использует настоящие потоки и pytest-qt.
"""

import threading
import urllib.error
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtCore")

from comfyui_studio.launcher.ui import remote_controller as rc  # noqa: E402


class FakeQTimer:
    calls = []

    @staticmethod
    def singleShot(ms, fn):
        FakeQTimer.calls.append((ms, fn))


class FakeRemoteProcess:
    instances = []
    start_error = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.host = kwargs.get("host")
        self.port = kwargs.get("port")
        self.started = False
        self.stopped = False
        self.running = True
        self.code = None
        type(self).instances.append(self)

    def start(self):
        if type(self).start_error is not None:
            raise type(self).start_error
        self.started = True

    def stop(self):
        self.stopped = True
        self.running = False

    def is_running(self):
        return self.running

    def exit_code(self):
        return self.code


def _http_error(code=500):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    FakeQTimer.calls = []
    FakeRemoteProcess.instances = []
    FakeRemoteProcess.start_error = None
    monkeypatch.setattr(rc, "QTimer", FakeQTimer)
    monkeypatch.setattr(rc, "RemoteProcess", FakeRemoteProcess)
    monkeypatch.setattr(rc, "load_config", lambda: {})
    monkeypatch.setattr(rc, "extract_error_detail", lambda e: "detail-from-http")

    e = Env()
    e.view = MagicMock()
    e.api_calls = []
    e.api_result = None
    e.api_error = None

    def call_local_api(port, method, path, *a, **k):
        e.api_calls.append((port, method, path))
        if e.api_error is not None:
            raise e.api_error
        return e.api_result

    monkeypatch.setattr(rc, "call_local_api", call_local_api)
    e.spawned = []
    e.ctl = rc.RemoteController(e.view, spawn=lambda fn: (e.spawned.append(fn), fn()))
    e.proc = lambda **kw: _attach(e, FakeRemoteProcess(**{"host": "127.0.0.1", "port": 7861, **kw}))
    return e


def _attach(e, proc):
    e.ctl.process = proc
    return proc


# -- запуск ------------------------------------------------------------------


def test_start_reads_fresh_config_from_disk(env, monkeypatch):
    """Регрессия 2026-09-06: настройки автосохраняются из диалога, окно
    держит устаревший снимок — порт/хост берутся из свежего load_config()."""
    monkeypatch.setattr(
        rc, "load_config",
        lambda: {"port": 8200, "imagine": {"port": 7999}, "imagine_pony": {"port": 7998},
                 "remote": {"port": 7862, "host": "0.0.0.0"}},
    )
    env.ctl.start()
    proc = FakeRemoteProcess.instances[0]
    assert env.ctl.process is proc and proc.started is True
    assert proc.kwargs == {
        "host": "0.0.0.0", "port": 7862, "comfy_host": "127.0.0.1",
        "comfy_port": 8200, "imagine_port": 7999, "imagine_pony_port": 7998,
    }
    assert env.ctl._ready_attempts == 0
    assert [ms for ms, _ in FakeQTimer.calls] == [300]
    assert FakeQTimer.calls[0][1] == env.ctl._poll_ready


def test_start_defaults_when_config_sections_missing(env):
    env.ctl.start()
    kwargs = FakeRemoteProcess.instances[0].kwargs
    assert kwargs["port"] == 7861 and kwargs["host"] == "127.0.0.1"
    assert kwargs["comfy_port"] is None and kwargs["imagine_port"] is None
    assert kwargs["imagine_pony_port"] is None


def test_start_error_resets_process_and_reports(env):
    FakeRemoteProcess.start_error = RuntimeError("не найден uvicorn")
    env.ctl.start()
    assert env.ctl.process is None
    env.view.set_remote_running_state.assert_called_once_with(False, error="не найден uvicorn")
    assert FakeQTimer.calls == []


# -- опрос готовности ----------------------------------------------------------


def test_poll_noop_without_process(env):
    env.ctl._poll_ready()
    env.view.set_remote_running_state.assert_not_called()
    assert FakeQTimer.calls == []


def test_poll_reports_premature_exit(env):
    proc = env.proc()
    proc.running, proc.code = False, 3
    env.ctl._poll_ready()
    assert env.ctl.process is None
    assert env.view.set_remote_running_state.call_args.args == (False,)
    assert "код выхода 3" in env.view.set_remote_running_state.call_args.kwargs["error"]


def test_poll_success_on_localhost_hides_lan_url(env, monkeypatch):
    monkeypatch.setattr(rc, "is_remote_available", lambda port: True)
    env.proc(host="127.0.0.1")
    env.ctl._poll_ready()
    env.view.set_remote_running_state.assert_called_once_with(True)
    env.view.show_lan_url.assert_called_once_with(None)
    assert FakeQTimer.calls == []


def test_poll_success_on_lan_shows_lan_url(env, monkeypatch):
    monkeypatch.setattr(rc, "is_remote_available", lambda port: True)
    monkeypatch.setattr(rc, "get_lan_ip", lambda: "192.168.1.5")
    env.proc(host="0.0.0.0")
    env.ctl._poll_ready()
    env.view.show_lan_url.assert_called_once_with("http://192.168.1.5:7861/")


def test_poll_lan_ip_unknown_shows_none(env, monkeypatch):
    monkeypatch.setattr(rc, "is_remote_available", lambda port: True)
    monkeypatch.setattr(rc, "get_lan_ip", lambda: None)
    env.proc(host="0.0.0.0")
    env.ctl._poll_ready()
    env.view.show_lan_url.assert_called_once_with(None)


def test_poll_retries_then_gives_up_after_20_attempts(env, monkeypatch):
    monkeypatch.setattr(rc, "is_remote_available", lambda port: False)
    env.proc()
    for _ in range(19):
        env.ctl._poll_ready()
    assert len(FakeQTimer.calls) == 19 and all(ms == 300 for ms, _ in FakeQTimer.calls)
    env.view.set_remote_running_state.assert_not_called()

    env.ctl._poll_ready()  # 20-я попытка — сдаётся
    assert len(FakeQTimer.calls) == 19
    assert env.view.set_remote_running_state.call_args.args == (False,)
    assert "не поднялся вовремя" in env.view.set_remote_running_state.call_args.kwargs["error"]


# -- остановка / переключатель ---------------------------------------------------


def test_stop_stops_process_and_updates_state(env):
    proc = env.proc()
    env.ctl.stop()
    assert proc.stopped is True and env.ctl.process is None
    env.view.set_remote_running_state.assert_called_with(False)


def test_stop_without_process_still_resets_view(env):
    env.ctl.stop()
    env.view.set_remote_running_state.assert_called_once_with(False)


def test_enable_toggle_starts_only_when_not_already_running(env):
    env.ctl.start = MagicMock()
    env.ctl.stop = MagicMock()
    env.ctl.on_enable_toggled(True)
    env.ctl.start.assert_called_once()
    env.proc()
    env.ctl.on_enable_toggled(True)
    env.ctl.start.assert_called_once()  # повторно не стартует
    env.ctl.on_enable_toggled(False)
    env.ctl.stop.assert_called_once()


# -- pairing -----------------------------------------------------------------------


def test_pairing_requires_running_remote(env):
    env.ctl.request_pairing()
    env.view.show_remote_pairing_error.assert_called_once()
    assert "Remote не запущен" in env.view.show_remote_pairing_error.call_args.args[0]
    assert env.api_calls == [] and env.spawned == []


def test_pairing_success_shows_code(env):
    env.proc()
    env.api_result = {"code": "123456", "expires_at": 1700000000, "attempts_left": 5}
    env.ctl.request_pairing()
    assert env.api_calls == [(7861, "POST", "/api/v1/remote/pair/start")]
    env.view.show_remote_pairing_code.assert_called_once_with("123456", 1700000000, 5)


def test_pairing_http_error_shows_extracted_detail(env):
    env.proc()
    env.api_error = _http_error()
    env.ctl.request_pairing()
    env.view.show_remote_pairing_error.assert_called_once_with("detail-from-http")


def test_pairing_url_error_shows_error_text(env):
    env.proc()
    env.api_error = urllib.error.URLError("connection refused")
    env.ctl.request_pairing()
    assert "connection refused" in env.view.show_remote_pairing_error.call_args.args[0]


def test_pairing_unexpected_exception_is_shown_not_raised(env):
    env.proc()
    env.api_error = TimeoutError("timed out")  # раньше улетало из слота необработанным
    env.ctl.request_pairing()
    assert "timed out" in env.view.show_remote_pairing_error.call_args.args[0]


def test_pairing_malformed_response_is_reported(env):
    env.proc()
    env.api_result = {"unexpected": True}
    env.ctl.request_pairing()
    env.view.show_remote_pairing_code.assert_not_called()
    assert "неожиданный ответ" in env.view.show_remote_pairing_error.call_args.args[0]


# -- устройства ----------------------------------------------------------------------


def test_refresh_requires_running_remote(env):
    env.ctl.refresh_devices()
    assert "Remote не запущен" in env.view.show_remote_devices_error.call_args.args[0]


def test_refresh_success_passes_list_through(env):
    env.proc()
    env.api_result = [{"id": "d1"}]
    env.ctl.refresh_devices()
    assert env.api_calls == [(7861, "GET", "/api/v1/remote/devices")]
    env.view.set_remote_devices.assert_called_once_with([{"id": "d1"}])


def test_refresh_empty_response_becomes_empty_list(env):
    env.proc()
    env.ctl.refresh_devices()
    env.view.set_remote_devices.assert_called_once_with([])


def test_refresh_errors_are_reported(env):
    env.proc()
    env.api_error = _http_error()
    env.ctl.refresh_devices()
    env.view.show_remote_devices_error.assert_called_with("detail-from-http")
    env.api_error = urllib.error.URLError("нет сети")
    env.ctl.refresh_devices()
    assert "нет сети" in env.view.show_remote_devices_error.call_args.args[0]


def test_revoke_calls_api_per_device_then_refreshes(env):
    env.proc()
    env.api_result = []
    env.ctl.revoke(["a", "b"])
    assert env.api_calls == [
        (7861, "POST", "/api/v1/remote/devices/a/revoke"),
        (7861, "POST", "/api/v1/remote/devices/b/revoke"),
        (7861, "GET", "/api/v1/remote/devices"),
    ]
    env.view.set_remote_devices.assert_called_once_with([])


def test_revoke_stops_at_first_error_and_does_not_refresh(env):
    env.proc()
    env.api_error = _http_error()
    env.ctl.revoke(["a", "b"])
    assert len(env.api_calls) == 1  # на втором устройстве уже не пытается
    env.view.show_remote_devices_error.assert_called_once_with("detail-from-http")
    env.view.set_remote_devices.assert_not_called()


def test_revoke_requires_running_remote(env):
    env.ctl.revoke(["a"])
    assert env.api_calls == []
    env.view.show_remote_devices_error.assert_called_once()


def test_revoke_does_not_alias_caller_list(env):
    env.proc()
    ids = ["a"]
    env.ctl.revoke(ids)
    ids.append("late")
    assert all("late" not in c[2] for c in env.api_calls)


# -- устаревшие ответы и повторные клики --------------------------------------------------


def _deferred(env):
    """spawn, который откладывает запуск: тест сам решает, когда 'поток' дойдёт."""
    env.pending = []
    env.ctl._spawn = lambda fn: env.pending.append(fn)


def test_response_after_stop_is_dropped(env):
    env.proc()
    _deferred(env)
    env.api_result = {"code": "1", "expires_at": 2, "attempts_left": 3}
    env.ctl.request_pairing()
    env.ctl.stop()
    env.pending.pop()()
    env.view.show_remote_pairing_code.assert_not_called()


def test_response_after_restart_is_dropped(env):
    env.proc()
    _deferred(env)
    env.api_result = [{"id": "old"}]
    env.ctl.refresh_devices()
    env.ctl.stop()
    env.proc(port=7862)  # «новый» Remote
    env.pending.pop()()
    env.view.set_remote_devices.assert_not_called()


def test_double_click_does_not_spawn_second_request(env):
    env.proc()
    _deferred(env)
    env.ctl.request_pairing()
    env.ctl.request_pairing()
    assert len(env.pending) == 1
    env.api_result = {"code": "1", "expires_at": 2, "attempts_left": 3}
    env.pending.pop()()
    env.ctl.request_pairing()  # после ответа — можно снова
    assert len(env.pending) == 1


def test_different_kinds_of_requests_do_not_block_each_other(env):
    env.proc()
    _deferred(env)
    env.ctl.request_pairing()
    env.ctl.refresh_devices()
    assert len(env.pending) == 2


def test_inflight_is_cleared_after_error(env):
    env.proc()
    env.api_error = _http_error()
    env.ctl.request_pairing()
    env.api_error = None
    env.api_result = {"code": "1", "expires_at": 2, "attempts_left": 3}
    env.ctl.request_pairing()
    env.view.show_remote_pairing_code.assert_called_once()


# -- настоящие потоки (pytest-qt) ----------------------------------------------------------


def test_api_call_runs_off_gui_thread_and_does_not_block(qtbot, monkeypatch):
    """Суть R4: пока сервер «висит», GUI-поток свободен, а результат
    приходит обратно в GUI-поток."""
    release = threading.Event()
    seen = {}

    def slow_call(port, method, path, *a, **k):
        seen["call_thread"] = threading.current_thread()
        assert release.wait(10)
        return {"code": "777", "expires_at": 1, "attempts_left": 2}

    monkeypatch.setattr(rc, "call_local_api", slow_call)

    class View:
        def show_remote_pairing_code(self, code, expires_at, attempts_left):
            seen["ui_thread"] = threading.current_thread()
            seen["code"] = code

        def show_remote_pairing_error(self, message):
            seen["error"] = message

    ctl = rc.RemoteController(View())
    ctl.process = FakeRemoteProcess(host="127.0.0.1", port=7861)

    ctl.request_pairing()  # вернулся сразу, хотя «сервер» ещё не ответил
    assert "code" not in seen and "error" not in seen

    release.set()
    qtbot.waitUntil(lambda: "code" in seen or "error" in seen, timeout=5000)
    assert seen.get("code") == "777"
    assert seen["call_thread"] is not threading.main_thread()
    assert seen["ui_thread"] is threading.main_thread()
    assert not ctl._inflight


def test_error_from_real_thread_is_delivered(qtbot, monkeypatch):
    def failing(port, method, path, *a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(rc, "call_local_api", failing)
    got = []
    view = MagicMock()
    view.show_remote_devices_error.side_effect = got.append
    ctl = rc.RemoteController(view)
    ctl.process = FakeRemoteProcess(host="127.0.0.1", port=7861)
    ctl.refresh_devices()
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert "refused" in got[0]
