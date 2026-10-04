"""
Тесты R6: опросы ComfyUI / Imagine / Remote не блокируют GUI-поток.

Что проверяется:
  * BackgroundTask: выполнение вне вызывающего потока, доставка результата
    обратно, один вызов за раз, cancel() отбрасывает запоздавший ответ,
    ошибка не теряется;
  * LaunchWatcher / ImagineLaunchWatcher: тик возвращается сразу, пока
    HTTP-проверка идёт; повторный тик не плодит проверки и не двигает
    счётчик; stop()/start() отбрасывают запоздавший «готово» (иначе после
    отмены запуска мог бы открыться браузер); таймаут считает завершённые
    неудачные проверки;
  * ResourceMonitor: запрос очереди идёт в рабочем потоке; пока он
    выполняется, железо обновляется, а очередь берётся из последнего
    ответа; ответ, устаревший из-за остановки ComfyUI или смены порта,
    отбрасывается;
  * RemoteController: проверка готовности Remote — то же самое.

Большинство тестов используют «отложенный» spawn: запуск проверки
откладывается, и тест сам решает, когда рабочий поток «дошёл до конца».
Так порядок событий задан явно, без sleep'ов. Несколько тестов с
настоящими потоками (pytest-qt) подтверждают главное: GUI-вызов
возвращается за доли секунды, пока сеть «висит».
"""

import threading
import time
import types
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtCore")

from comfyui_studio.launcher.core import imagine_process, system_monitor as sm  # noqa: E402
from comfyui_studio.launcher.core.background import BackgroundTask  # noqa: E402
from comfyui_studio.launcher.core.comfy_api import QueueState  # noqa: E402
from comfyui_studio.launcher.ui import remote_controller as rc  # noqa: E402
from comfyui_studio.launcher.ui.widgets.launch_watcher import (  # noqa: E402
    ImagineLaunchWatcher,
    LaunchWatcher,
)

_GB = 1024 ** 3


class Deferred:
    """spawn, который откладывает запуск: тест вызывает run_next() сам."""

    def __init__(self):
        self.pending = []

    def __call__(self, fn):
        self.pending.append(fn)

    def run_next(self):
        self.pending.pop(0)()

    def run_all(self):
        while self.pending:
            self.run_next()


# ==========================================================================
# BackgroundTask
# ==========================================================================


def test_task_runs_off_caller_thread_and_delivers_on_owner_thread(qtbot):
    seen = {}
    task = BackgroundTask()

    def work():
        seen["worker"] = threading.current_thread()
        return 42

    def done(result, error):
        seen["delivered_on"] = threading.current_thread()
        seen["result"], seen["error"] = result, error

    assert task.run(work, done) is True
    qtbot.waitUntil(lambda: "result" in seen, timeout=5000)
    assert seen["result"] == 42 and seen["error"] is None
    assert seen["worker"] is not threading.main_thread()
    assert seen["delivered_on"] is threading.main_thread()
    assert task.is_busy() is False


def test_task_only_one_call_at_a_time():
    spawn = Deferred()
    task = BackgroundTask(spawn=spawn)
    got = []
    assert task.run(lambda: 1, lambda r, e: got.append(r)) is True
    assert task.is_busy() is True
    assert task.run(lambda: 2, lambda r, e: got.append(r)) is False  # занят
    assert len(spawn.pending) == 1
    spawn.run_all()
    assert got == [1] and task.is_busy() is False
    assert task.run(lambda: 3, lambda r, e: got.append(r)) is True  # снова можно


def test_task_error_is_delivered_not_lost():
    task = BackgroundTask(spawn=lambda fn: fn())
    got = []
    task.run(lambda: 1 / 0, lambda r, e: got.append((r, type(e))))
    assert got == [(None, ZeroDivisionError)]
    assert task.is_busy() is False


def test_task_cancel_drops_late_result_and_allows_new_run():
    spawn = Deferred()
    task = BackgroundTask(spawn=spawn)
    got = []
    task.run(lambda: "old", lambda r, e: got.append(r))
    task.cancel()
    assert task.is_busy() is False
    task.run(lambda: "new", lambda r, e: got.append(r))
    spawn.run_all()  # сначала «дошёл» старый поток, затем новый
    assert got == ["new"]


def test_task_cancel_without_running_call_is_harmless():
    task = BackgroundTask(spawn=lambda fn: fn())
    task.cancel()
    got = []
    task.run(lambda: 1, lambda r, e: got.append(r))
    assert got == [1]


def test_task_delivery_to_deleted_owner_does_not_raise(qtbot):
    release = threading.Event()
    finished = threading.Event()
    task = BackgroundTask()

    def work():
        assert release.wait(5)
        return 1

    task.run(work, lambda r, e: None)
    task.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    release.set()
    time.sleep(0.2)  # поток дошёл до emit у удалённого объекта — исключения нет
    finished.set()


# ==========================================================================
# LaunchWatcher (ComfyUI)
# ==========================================================================


class FakeProc:
    def __init__(self, running=True, code=None):
        self.running, self.code = running, code

    def is_running(self):
        return self.running

    def exit_code(self):
        return self.code


@pytest.fixture
def comfy_watcher():
    w = LaunchWatcher()
    w.spawn = Deferred()
    w._probe._spawn = w.spawn
    w._api = MagicMock()
    w._api.is_available.return_value = False
    w.events = []
    w.ready.connect(lambda: w.events.append("ready"))
    w.failed.connect(lambda m: w.events.append(("failed", m)))
    w.progress.connect(lambda m: w.events.append(("progress", m)))
    w.start(8188, FakeProc())
    w.events.clear()  # стартовое сообщение прогресса
    yield w
    w.stop()


def test_comfy_tick_returns_immediately_while_probe_is_pending(comfy_watcher):
    w = comfy_watcher
    w._check()
    assert len(w.spawn.pending) == 1
    w._api.is_available.assert_not_called()  # сеть ещё не трогали: проверка ждёт «поток»
    assert w.events == [] and w._elapsed == 0


def test_comfy_ready_when_probe_reports_available(comfy_watcher):
    w = comfy_watcher
    w._api.is_available.return_value = True
    w._check()
    w.spawn.run_all()
    assert w.events == ["ready"]
    assert not w._timer.isActive()
    w._api.is_available.assert_called_once_with(port=8188)


def test_comfy_unavailable_counts_seconds_and_reports_progress(comfy_watcher):
    w = comfy_watcher
    for _ in range(3):
        w._check()
        w.spawn.run_all()
    assert w._elapsed == 3
    assert w.events[-1] == ("progress", "Запуск ComfyUI, ожидание сервера... (3с)")


def test_comfy_tick_while_probe_in_flight_does_not_stack_probes_or_count(comfy_watcher):
    w = comfy_watcher
    w._check()
    w._check()
    w._check()
    assert len(w.spawn.pending) == 1 and w._elapsed == 0
    w.spawn.run_all()
    assert w._elapsed == 1


def test_comfy_timeout_after_completed_failed_probes(comfy_watcher):
    w = comfy_watcher
    w.TIMEOUT_SECONDS = 3
    for _ in range(3):
        w._check()
        w.spawn.run_all()
    assert w.events[-1] == ("failed", "ComfyUI не поднялся за 3 секунд.")
    assert not w._timer.isActive()


def test_comfy_dead_process_fails_without_probing(comfy_watcher):
    w = comfy_watcher
    w._process = FakeProc(running=False, code=3)
    w._check()
    assert w.spawn.pending == []
    kind, message = w.events[-1]
    assert kind == "failed" and "код выхода: 3" in message


def test_comfy_stop_drops_late_ready(comfy_watcher):
    """Отмена запуска, пока проверка ещё идёт: запоздавший «готово» не
    должен открыть браузер."""
    w = comfy_watcher
    w._api.is_available.return_value = True
    w._check()
    w.stop()
    w.spawn.run_all()
    assert w.events == []


def test_comfy_restart_drops_result_of_previous_launch(comfy_watcher):
    w = comfy_watcher
    w._api.is_available.return_value = True
    w._check()  # проверка прошлого запуска ещё в полёте
    w.start(9000, FakeProc())
    w.events.clear()
    w.spawn.run_all()
    assert w.events == [] and w._elapsed == 0


def test_comfy_probe_exception_counts_as_unavailable(comfy_watcher):
    w = comfy_watcher
    w._api.is_available.side_effect = OSError("boom")
    w._check()
    w.spawn.run_all()
    assert w._elapsed == 1 and "ready" not in w.events


# ==========================================================================
# ImagineLaunchWatcher
# ==========================================================================


@pytest.fixture
def imagine_watcher(monkeypatch):
    w = ImagineLaunchWatcher()
    w.spawn = Deferred()
    w._probe._spawn = w.spawn
    w.available = False
    w.calls = []

    def fake_available(port, timeout=1.0):
        w.calls.append(port)
        return w.available

    monkeypatch.setattr(imagine_process, "is_imagine_available", fake_available)
    w.events = []
    w.ready.connect(lambda: w.events.append("ready"))
    w.failed.connect(lambda m: w.events.append(("failed", m)))
    w.progress.connect(lambda m: w.events.append(("progress", m)))
    w.start(7870, FakeProc())
    w.events.clear()
    yield w
    w.stop()


def test_imagine_tick_defers_probe_then_ready(imagine_watcher):
    w = imagine_watcher
    w.available = True
    w._check()
    assert w.calls == [] and len(w.spawn.pending) == 1
    w.spawn.run_all()
    assert w.calls == [7870] and w.events == ["ready"]


def test_imagine_unavailable_progress_and_timeout(imagine_watcher):
    w = imagine_watcher
    w.TIMEOUT_SECONDS = 2
    w._check()
    w.spawn.run_all()
    assert w.events[-1] == ("progress", "Запуск Imagine, ожидание сервера... (1с)")
    w._check()
    w.spawn.run_all()
    assert w.events[-1] == ("failed", "Imagine не поднялся за 2 секунд.")


def test_imagine_stop_drops_late_ready(imagine_watcher):
    w = imagine_watcher
    w.available = True
    w._check()
    w.stop()
    w.spawn.run_all()
    assert w.events == []


def test_imagine_dead_process_fails_without_probing(imagine_watcher):
    w = imagine_watcher
    w._process = FakeProc(running=False, code=1)
    w._check()
    assert w.spawn.pending == [] and w.events[-1][0] == "failed"


# ==========================================================================
# ResourceMonitor
# ==========================================================================


class FakePsutil:
    @staticmethod
    def cpu_percent(interval=None):
        return 12.5

    @staticmethod
    def virtual_memory():
        return types.SimpleNamespace(percent=50.0, used=8 * _GB, total=16 * _GB)


class FakeAPI:
    def __init__(self):
        self.queue = None
        self.history_ids = None
        self.error = None
        self.threads = []
        self.calls = []

    def get_queue(self, port=None, timeout=None):
        self.threads.append(threading.current_thread())
        self.calls.append(("queue", port))
        if self.error is not None:
            raise self.error
        return self.queue

    def get_history_ids(self, port=None, timeout=None):
        self.threads.append(threading.current_thread())
        self.calls.append(("history", port))
        return self.history_ids


def _queue(running_ids=(), pending=0):
    running_ids = set(running_ids)
    return QueueState(
        running=len(running_ids), pending=pending,
        running_ids=running_ids, step_totals={i: 20 for i in running_ids},
    )


class Mon:
    def __init__(self, monkeypatch, spawn):
        monkeypatch.setattr(sm, "psutil", FakePsutil)
        monkeypatch.setattr(sm, "pynvml", None)
        self.port = 8188
        self.monitor = sm.ResourceMonitor(lambda: self.port, spawn=spawn)
        self.monitor._api = FakeAPI()
        self.monitor._ensure_ws_client = lambda port: None
        self.received = []
        self.monitor.stats_updated.connect(self.received.append)

    @property
    def api(self):
        return self.monitor._api


@pytest.fixture
def mon(monkeypatch):
    spawn = Deferred()
    m = Mon(monkeypatch, spawn)
    m.spawn = spawn
    return m


def test_poll_does_not_touch_network_on_the_calling_thread(mon):
    mon.api.queue = _queue()
    mon.api.history_ids = set()
    mon.monitor._poll()
    assert mon.api.calls == [] and mon.received == []  # ушло в «поток», эмита ещё нет
    mon.spawn.run_all()
    assert mon.api.calls == [("queue", 8188), ("history", 8188)]
    assert len(mon.received) == 1 and mon.received[0]["queue_running"] == 0


def test_busy_fetch_keeps_hardware_live_and_queue_from_last_answer(mon):
    mon.api.queue = _queue(running_ids={"a"}, pending=2)
    mon.api.history_ids = set()
    mon.monitor._poll()
    mon.spawn.run_all()
    assert mon.received[-1]["queue_pending"] == 2

    mon.monitor._poll()          # запрос очереди «завис»
    mon.monitor._poll()          # второй тик, пока первый ещё идёт
    assert len(mon.spawn.pending) == 1  # второй запрос не запущен
    stale = mon.received[-1]
    assert stale["cpu_percent"] == 12.5          # железо обновилось
    assert stale["queue_pending"] == 2            # очередь — последняя известная
    assert "queue_pending" in stale


def test_busy_fetch_after_failed_answer_does_not_invent_queue_keys(mon):
    mon.api.queue = None  # ComfyUI не ответил
    mon.monitor._poll()
    mon.spawn.run_all()
    assert "queue_pending" not in mon.received[-1]
    mon.monitor._poll()
    mon.monitor._poll()
    assert "queue_pending" not in mon.received[-1]  # не мигаем «живой» очередью


def test_answer_after_comfy_stopped_is_dropped_and_session_reset(mon):
    mon.api.queue = _queue(running_ids={"a"})
    mon.api.history_ids = {"old"}
    mon.monitor._poll()                 # запрос в полёте
    mon.port = None                     # ComfyUI остановили
    mon.monitor._poll()                 # тик без порта: сброс, эмит только железа
    assert set(mon.received[-1]) == {
        "cpu_percent", "ram_percent", "ram_used_gb", "ram_total_gb", "gpu_available"
    }
    before = len(mon.received)
    mon.spawn.run_all()                 # запоздавший ответ
    assert len(mon.received) == before
    assert mon.monitor._session_seen_history_ids is None  # состояние не «ожило»


def test_answer_for_old_port_is_dropped_after_port_change(mon):
    mon.api.queue = _queue()
    mon.api.history_ids = set()
    mon.monitor._poll()
    mon.port = 9999
    mon.spawn.run_all()
    assert mon.received == []
    assert mon.monitor._session_seen_history_ids is None


def test_stop_cancels_inflight_fetch(mon):
    mon.api.queue = _queue()
    mon.api.history_ids = set()
    mon.monitor._poll()
    mon.monitor.stop()
    mon.spawn.run_all()
    assert mon.received == []


def test_exception_in_fetch_is_treated_as_failed_poll(mon):
    mon.api.error = OSError("сокет умер")
    mon.monitor._poll()
    mon.spawn.run_all()
    stats = mon.received[-1]
    assert "queue_pending" not in stats and stats["cpu_percent"] == 12.5
    mon.api.error = None
    mon.api.queue = _queue()
    mon.api.history_ids = set()
    mon.monitor._poll()  # монитор жив и опрашивает дальше
    mon.spawn.run_all()
    assert "queue_pending" in mon.received[-1]


def test_session_state_is_updated_only_when_answer_is_applied(mon):
    mon.api.queue = _queue()
    mon.api.history_ids = {"h1"}
    mon.monitor._poll()
    assert mon.monitor._session_seen_history_ids is None  # в «потоке» состояние не трогается
    mon.spawn.run_all()
    assert mon.monitor._session_seen_history_ids == {"h1"}


def test_poll_without_comfy_still_emits_synchronously(mon):
    mon.port = None
    mon.monitor._poll()
    assert len(mon.received) == 1 and mon.spawn.pending == []


# ==========================================================================
# RemoteController: проверка готовности
# ==========================================================================


class FakeRemoteProcess:
    def __init__(self, host="127.0.0.1", port=7861):
        self.host, self.port, self.running = host, port, True

    def is_running(self):
        return self.running

    def exit_code(self):
        return None

    def stop(self):
        self.running = False


@pytest.fixture
def remote(monkeypatch):
    class FakeTimer:
        calls = []

        @staticmethod
        def singleShot(ms, fn):
            FakeTimer.calls.append((ms, fn))

    FakeTimer.calls = []
    monkeypatch.setattr(rc, "QTimer", FakeTimer)
    r = types.SimpleNamespace()
    r.available = False
    r.calls = []

    def fake_available(port, timeout=1.0):
        r.calls.append(port)
        return r.available

    monkeypatch.setattr(rc, "is_remote_available", fake_available)
    r.view = MagicMock()
    r.spawn = Deferred()
    r.ctl = rc.RemoteController(r.view, spawn=r.spawn)
    r.timer = FakeTimer
    r.proc = FakeRemoteProcess()
    r.ctl.process = r.proc
    return r


def test_remote_poll_ready_returns_before_network_call(remote):
    remote.ctl._poll_ready()
    assert remote.calls == [] and remote.view.mock_calls == []
    remote.available = True
    remote.spawn.run_all()
    remote.view.set_remote_running_state.assert_called_once_with(True)


def test_remote_poll_ready_next_poll_scheduled_after_answer(remote):
    remote.ctl._poll_ready()
    assert remote.timer.calls == []
    remote.spawn.run_all()
    assert [ms for ms, _ in remote.timer.calls] == [300]
    assert remote.ctl._ready_attempts == 1


def test_remote_stop_during_probe_drops_late_available(remote):
    remote.available = True
    remote.ctl._poll_ready()
    remote.ctl.stop()
    remote.view.reset_mock()
    remote.spawn.run_all()
    remote.view.set_remote_running_state.assert_not_called()
    remote.view.show_lan_url.assert_not_called()


def test_remote_restart_during_probe_drops_answer_for_old_process(remote):
    remote.available = True
    remote.ctl._poll_ready()
    remote.ctl.process = FakeRemoteProcess(port=7862)  # «новый» Remote
    remote.spawn.run_all()
    remote.view.set_remote_running_state.assert_not_called()


def test_remote_probe_exception_counts_as_not_ready(remote, monkeypatch):
    def boom(port, timeout=1.0):
        raise OSError("x")

    monkeypatch.setattr(rc, "is_remote_available", boom)
    remote.ctl._poll_ready()
    remote.spawn.run_all()
    assert remote.ctl._ready_attempts == 1
    remote.view.set_remote_running_state.assert_not_called()


# ==========================================================================
# Настоящие потоки: GUI-вызов возвращается быстро, пока «сеть» висит
# ==========================================================================


def test_real_threads_comfy_watcher_tick_does_not_block(qtbot):
    w = LaunchWatcher()
    release = threading.Event()
    events = []

    def slow_available(port=None, timeout=None):
        assert release.wait(10)
        return True

    w._api = types.SimpleNamespace(is_available=slow_available)
    w.ready.connect(lambda: events.append("ready"))
    w.start(8188, FakeProc())

    t0 = time.monotonic()
    w._check()
    w._check()
    assert time.monotonic() - t0 < 0.25  # раньше блокировалось на время проверки
    assert events == []

    release.set()
    qtbot.waitUntil(lambda: events == ["ready"], timeout=5000)
    w.stop()


def test_real_threads_resource_monitor_poll_does_not_block(qtbot, monkeypatch):
    monkeypatch.setattr(sm, "psutil", FakePsutil)
    monkeypatch.setattr(sm, "pynvml", None)
    release = threading.Event()
    got = []

    class SlowAPI(FakeAPI):
        def get_queue(self, port=None, timeout=None):
            self.threads.append(threading.current_thread())
            assert release.wait(10)
            return _queue(pending=1)

        def get_history_ids(self, port=None, timeout=None):
            return set()

    monitor = sm.ResourceMonitor(lambda: 8188)
    monitor._api = SlowAPI()
    monitor._ensure_ws_client = lambda port: None
    monitor.stats_updated.connect(got.append)

    t0 = time.monotonic()
    monitor._poll()
    monitor._poll()  # второй тик, пока «сервер» молчит — железо обновляется сразу
    assert time.monotonic() - t0 < 0.25
    assert len(got) == 1 and "queue_pending" not in got[0]  # очереди ещё нет, но CPU/RAM живы

    release.set()
    qtbot.waitUntil(lambda: len(got) == 2, timeout=5000)
    assert got[-1]["queue_pending"] == 1
    assert all(t is not threading.main_thread() for t in monitor._api.threads)
    monitor.stop()


def test_real_threads_remote_ready_probe_does_not_block(qtbot, monkeypatch):
    release = threading.Event()

    def slow(port, timeout=1.0):
        assert release.wait(10)
        return True

    monkeypatch.setattr(rc, "is_remote_available", slow)
    view = MagicMock()
    ctl = rc.RemoteController(view)
    ctl.process = FakeRemoteProcess()

    t0 = time.monotonic()
    ctl._poll_ready()
    assert time.monotonic() - t0 < 0.25
    view.set_remote_running_state.assert_not_called()

    release.set()
    qtbot.waitUntil(lambda: view.set_remote_running_state.called, timeout=5000)
    view.set_remote_running_state.assert_called_once_with(True)
