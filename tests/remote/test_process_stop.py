"""
Остановка процессов в Remote (этап R3b): comfy_launcher.stop_comfyui и
process_by_port.kill_pid_tree используют общие хелперы из
launcher/core/managed_process.py вместо собственных копий taskkill.
Этап 6.2: сам процесс ComfyUI в comfy_launcher -- потомок ManagedProcess.
"""

import sys
import time

import pytest

from comfyui_studio.launcher.core import managed_process
from comfyui_studio.remote import comfy_launcher, process_by_port, state


class _Proc:
    pid = 321

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _launched(proc):
    """_RemoteComfyProcess с уже «запущенным» (подставным) Popen."""
    launched = comfy_launcher._RemoteComfyProcess()
    launched.proc = proc
    return launched


@pytest.fixture(autouse=True)
def _clean_launcher_state():
    comfy_launcher._process = None
    comfy_launcher._last_error = None
    saved = state.runtime.comfy_port
    yield
    comfy_launcher._process = None
    state.runtime.comfy_port = saved


def test_stop_comfyui_uses_shared_tree_termination(monkeypatch):
    calls = []

    def fake_terminate(proc, label, wait_timeout=5):
        calls.append((proc, label))
        return True

    monkeypatch.setattr(managed_process, "terminate_process_tree", fake_terminate)
    proc = _Proc()
    comfy_launcher._process = _launched(proc)
    comfy_launcher._last_error = "старая ошибка"
    comfy_launcher.stop_comfyui()
    assert calls == [(proc, "ComfyUI")]
    assert comfy_launcher._process is None and comfy_launcher._last_error is None


def test_stop_comfyui_windows_runs_taskkill_on_whole_tree(monkeypatch):
    monkeypatch.setattr(managed_process.sys, "platform", "win32")
    run = []
    monkeypatch.setattr(managed_process.subprocess, "run", lambda cmd, **kw: run.append(cmd))

    comfy_launcher._process = _launched(_Proc())
    comfy_launcher.stop_comfyui()
    assert run == [["taskkill", "/F", "/T", "/PID", "321"]]


def test_stop_comfyui_survivor_keeps_reference_and_falls_back_to_port(monkeypatch):
    """Процесс пережил и штатную остановку, и kill(): ссылка на него не
    теряется («фантомный процесс»), а ComfyUI добивается по порту."""

    class Stubborn(_Proc):
        def poll(self):
            return None  # не умирает

        def wait(self, timeout=None):
            raise managed_process.subprocess.TimeoutExpired("x", timeout)

    state.runtime.comfy_port = 8188
    killed = []
    monkeypatch.setattr(managed_process, "terminate_process_tree", lambda proc, label, wait_timeout=5: False)
    monkeypatch.setattr(comfy_launcher, "find_pid_listening_on_port", lambda port: 555)
    monkeypatch.setattr(comfy_launcher, "kill_pid_tree", lambda pid, label: killed.append((pid, label)))
    survivor = _launched(Stubborn())
    comfy_launcher._process = survivor

    comfy_launcher.stop_comfyui()

    assert comfy_launcher._process is survivor
    assert killed == [(555, "ComfyUI")]


def _wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_unexpected_exit_sets_last_error_for_the_phone():
    code = "import sys; print('traceback...'); sys.exit(3)"
    launched = comfy_launcher._RemoteComfyProcess()
    launched._spawn_captured([sys.executable, "-c", code])
    assert _wait_for(lambda: comfy_launcher.last_error() is not None)
    assert "3" in comfy_launcher.last_error()


def test_stop_does_not_leave_a_crash_message():
    """Раньше поток-наблюдатель после остановки с телефона выставлял
    «ComfyUI завершился с кодом 1 сразу после запуска»."""
    launched = comfy_launcher._RemoteComfyProcess()
    launched._spawn_captured([sys.executable, "-c", "import time; time.sleep(60)"])
    proc = launched.proc
    comfy_launcher._process = launched

    comfy_launcher.stop_comfyui()

    assert proc.wait(15) is not None
    time.sleep(0.3)  # хук вызывается сразу после лога завершения
    assert comfy_launcher.last_error() is None
    assert comfy_launcher.comfyui_status(port=1) == "stopped"


def test_comfyui_status_starting_while_process_alive(monkeypatch):
    monkeypatch.setattr(comfy_launcher, "is_comfyui_running", lambda port=None: False)
    launched = comfy_launcher._RemoteComfyProcess()
    launched._spawn_captured([sys.executable, "-c", "import time; time.sleep(60)"])
    comfy_launcher._process = launched
    try:
        assert comfy_launcher.comfyui_status() == "starting"
    finally:
        launched.stop()


def test_stop_comfyui_falls_back_to_port_lookup(monkeypatch):
    state.runtime.comfy_port = 8188
    killed = []
    monkeypatch.setattr(comfy_launcher, "find_pid_listening_on_port", lambda port: 555)
    monkeypatch.setattr(comfy_launcher, "kill_pid_tree", lambda pid, label: killed.append((pid, label)))
    comfy_launcher.stop_comfyui()
    assert killed == [(555, "ComfyUI")]


def test_stop_comfyui_nothing_to_stop(monkeypatch):
    state.runtime.comfy_port = 8188
    monkeypatch.setattr(comfy_launcher, "find_pid_listening_on_port", lambda port: None)
    monkeypatch.setattr(
        comfy_launcher, "kill_pid_tree", lambda *a: pytest.fail("не должен вызываться")
    )
    comfy_launcher.stop_comfyui()


def test_kill_pid_tree_windows_uses_taskkill(monkeypatch):
    monkeypatch.setattr(managed_process.sys, "platform", "win32")
    run = []
    monkeypatch.setattr(managed_process.subprocess, "run", lambda cmd, **kw: run.append(cmd))
    process_by_port.kill_pid_tree(999, "Imagine")
    assert run == [["taskkill", "/F", "/T", "/PID", "999"]]


def test_kill_pid_tree_non_windows_terminates_via_psutil(monkeypatch):
    import psutil

    monkeypatch.setattr(managed_process.sys, "platform", "linux")
    terminated = []

    class FakePsProc:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            terminated.append(self.pid)

    monkeypatch.setattr(psutil, "Process", FakePsProc)
    process_by_port.kill_pid_tree(777, "ComfyUI")
    assert terminated == [777]


def test_kill_pid_tree_swallows_errors(monkeypatch, caplog):
    import logging

    import psutil

    caplog.set_level(logging.ERROR, logger="comfyui_launcher")
    monkeypatch.setattr(managed_process.sys, "platform", "linux")

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    process_by_port.kill_pid_tree(31337, "Imagine")  # не бросает
    assert any("31337" in r.getMessage() for r in caplog.records)
