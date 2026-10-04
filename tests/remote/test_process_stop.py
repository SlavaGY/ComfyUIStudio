"""
Остановка процессов в Remote (этап R3b): comfy_launcher.stop_comfyui и
process_by_port.kill_pid_tree теперь используют общие хелперы из
launcher/core/managed_process.py вместо собственных копий taskkill.
"""

import pytest

from comfyui_studio.launcher.core import managed_process
from comfyui_studio.remote import comfy_launcher, process_by_port, state


class _Proc:
    pid = 321


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
    monkeypatch.setattr(
        comfy_launcher, "terminate_process_tree", lambda proc, label: calls.append((proc, label))
    )
    proc = _Proc()
    comfy_launcher._process = proc
    comfy_launcher._last_error = "старая ошибка"
    comfy_launcher.stop_comfyui()
    assert calls == [(proc, "ComfyUI")]
    assert comfy_launcher._process is None and comfy_launcher._last_error is None


def test_stop_comfyui_windows_runs_taskkill_on_whole_tree(monkeypatch):
    monkeypatch.setattr(managed_process.sys, "platform", "win32")
    run = []
    monkeypatch.setattr(managed_process.subprocess, "run", lambda cmd, **kw: run.append(cmd))

    class P(_Proc):
        def wait(self, timeout=None):
            return 0

    comfy_launcher._process = P()
    comfy_launcher.stop_comfyui()
    assert run == [["taskkill", "/F", "/T", "/PID", "321"]]


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
