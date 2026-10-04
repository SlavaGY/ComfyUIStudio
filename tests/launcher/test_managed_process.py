"""
Тесты процессного слоя (этап R3): ManagedProcess и три наследника
(ComfyProcess, ImagineProcess, RemoteProcess), а также вынесенные модули
external_apps.py и remote_net.py.

Большая часть тестов запускает настоящие короткие подпроцессы
(sys.executable -c ...) — именно их поведение (stop() убивает процесс,
хвост вывода попадает в лог) и дублировалось в трёх классах до R3.
Qt нужен только тестам ComfyProcess (importorskip внутри них).
"""

import io
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from comfyui_studio.launcher.core import (
    external_apps,
    imagine_process,
    managed_process,
    remote_net,
    remote_process,
)
from comfyui_studio.launcher.core.managed_process import (
    ManagedProcess,
    find_missing_modules,
    terminate_process_tree,
)

LOGGER = "comfyui_launcher"
SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(60)"]


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _has_log(caplog, *fragments):
    return any(all(f in r.getMessage() for f in fragments) for r in caplog.records)


@pytest.fixture(autouse=True)
def _info_logging(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)


# --------------------------------------------------------------------------
# Модули процессного слоя не тянут Qt (Remote импортирует ImagineProcess)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "comfyui_studio.launcher.core.managed_process",
        "comfyui_studio.launcher.core.imagine_process",
        "comfyui_studio.launcher.core.remote_process",
        "comfyui_studio.launcher.core.remote_net",
        "comfyui_studio.launcher.core.external_apps",
    ],
)
def test_module_does_not_import_qt(module):
    code = (
        f"import sys, {module}; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6')]; "
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# find_missing_modules
# --------------------------------------------------------------------------


def test_find_missing_modules():
    assert find_missing_modules(["os", "json"]) == []
    assert find_missing_modules(["os", "no_such_module_r3_xyz"]) == ["no_such_module_r3_xyz"]


def test_missing_dependency_wrappers_use_shared_helper(monkeypatch):
    seen = []
    monkeypatch.setattr(
        imagine_process, "find_missing_modules", lambda names: seen.append(tuple(names)) or ["x"]
    )
    monkeypatch.setattr(
        remote_process, "find_missing_modules", lambda names: seen.append(tuple(names)) or ["y"]
    )
    assert imagine_process._missing_imagine_dependencies() == ["x"]
    assert remote_process._missing_remote_dependencies() == ["y"]
    assert seen == [imagine_process.IMAGINE_REQUIRED_MODULES, remote_process.REMOTE_REQUIRED_MODULES]


# --------------------------------------------------------------------------
# terminate_process_tree
# --------------------------------------------------------------------------


class _FakeProc:
    pid = 4242

    def __init__(self, wait_raises=False, terminate_raises=False):
        self.terminated = False
        self._wait_raises = wait_raises
        self._terminate_raises = terminate_raises
        self.wait_timeouts = []

    def terminate(self):
        if self._terminate_raises:
            raise OSError("denied")
        self.terminated = True

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self._wait_raises:
            raise subprocess.TimeoutExpired("x", timeout)
        return 0


def test_terminate_non_windows_uses_terminate(monkeypatch):
    monkeypatch.setattr(managed_process.sys, "platform", "linux")
    run = []
    monkeypatch.setattr(managed_process.subprocess, "run", lambda *a, **k: run.append(a))
    proc = _FakeProc()
    assert terminate_process_tree(proc, "X") is True
    assert proc.terminated and run == []
    assert proc.wait_timeouts == [5]


def test_terminate_windows_uses_taskkill_tree(monkeypatch):
    monkeypatch.setattr(managed_process.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        managed_process.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw))
    )
    proc = _FakeProc()
    assert terminate_process_tree(proc, "X") is True
    assert calls[0][0] == ["taskkill", "/F", "/T", "/PID", "4242"]
    assert calls[0][1]["stdout"] == subprocess.DEVNULL
    assert not proc.terminated  # на Windows terminate() не вызывается


def test_terminate_swallows_errors_and_reports_timeout(monkeypatch, caplog):
    monkeypatch.setattr(managed_process.sys, "platform", "linux")
    proc = _FakeProc(wait_raises=True, terminate_raises=True)
    assert terminate_process_tree(proc, "Imagine") is False  # не бросает
    assert _has_log(caplog, "Не удалось остановить процесс PID 4242", "Imagine")


def test_terminate_taskkill_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(managed_process.sys, "platform", "win32")

    def boom(*a, **k):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(managed_process.subprocess, "run", boom)
    assert terminate_process_tree(_FakeProc(), "Remote") is True
    assert _has_log(caplog, "Не удалось выполнить taskkill для PID 4242")


# --------------------------------------------------------------------------
# ManagedProcess: жизненный цикл на настоящем подпроцессе
# --------------------------------------------------------------------------


class _Sleeper(ManagedProcess):
    label = "Sleeper"


def test_initial_state():
    p = _Sleeper()
    assert p.proc is None
    assert p.is_running() is False
    assert p.exit_code() is None
    p.stop()  # без процесса — no-op


def test_spawn_stop_lifecycle(caplog):
    p = _Sleeper()
    proc = p._spawn(SLEEP_CMD)
    try:
        assert p.proc is proc
        assert p.is_running() is True
        assert p.exit_code() is None
        pid = proc.pid
        p.stop()
        assert p.proc is None
        assert p.is_running() is False
        assert p.exit_code() is None  # после stop() proc обнулён — как и раньше
        assert proc.poll() is not None  # процесс реально завершён
        assert _has_log(caplog, "Остановка Sleeper", f"PID {pid}")
    finally:
        if proc.poll() is None:
            proc.kill()


def test_exit_code_after_natural_exit():
    p = _Sleeper()
    proc = p._spawn([sys.executable, "-c", "import sys; sys.exit(7)"])
    proc.wait(10)
    assert p.is_running() is False
    assert p.exit_code() == 7


def test_spawn_passes_popen_kwargs(monkeypatch):
    captured = {}

    class P:
        def __init__(self, cmd, **kw):
            captured["cmd"], captured["kw"] = cmd, kw

    monkeypatch.setattr(managed_process.subprocess, "Popen", P)
    monkeypatch.setattr(managed_process.sys, "platform", "linux")
    p = _Sleeper()
    p._spawn(["a", "b"], cwd="/x", env={"K": "v"})
    assert captured["cmd"] == ["a", "b"]
    assert captured["kw"]["cwd"] == "/x" and captured["kw"]["env"] == {"K": "v"}
    assert captured["kw"]["creationflags"] == 0


def test_spawn_hides_console_window_on_windows(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        managed_process.subprocess, "Popen", lambda cmd, **kw: captured.update(kw)
    )
    monkeypatch.setattr(managed_process.sys, "platform", "win32")
    monkeypatch.setattr(
        managed_process.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    _Sleeper()._spawn(["a"])
    assert captured["creationflags"] == 0x08000000


# --------------------------------------------------------------------------
# _spawn_captured: хвост вывода в лог
# --------------------------------------------------------------------------


def test_captured_nonzero_exit_logs_output_tail(caplog):
    p = _Sleeper()
    code = "import sys; print('boom-traceback-line'); sys.exit(3)"
    p._spawn_captured([sys.executable, "-c", code])
    assert _wait_for(lambda: _has_log(caplog, "Sleeper", "завершился с кодом 3"))
    assert _has_log(caplog, "boom-traceback-line")


def test_captured_zero_exit_logs_info_only(caplog):
    p = _Sleeper()
    p._spawn_captured([sys.executable, "-c", "print('fine')"])
    assert _wait_for(lambda: _has_log(caplog, "Sleeper", "код выхода 0"))
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_captured_silent_failure_has_placeholder(caplog):
    p = _Sleeper()
    p._spawn_captured([sys.executable, "-c", "import sys; sys.exit(1)"])
    assert _wait_for(lambda: _has_log(caplog, "процесс не вывел ничего"))


def test_captured_output_tail_is_truncated(caplog):
    p = _Sleeper()
    code = "import sys; print('A' * 9000 + 'END'); sys.exit(2)"
    p._spawn_captured([sys.executable, "-c", code])
    assert _wait_for(lambda: _has_log(caplog, "завершился с кодом 2"))
    msg = next(r.getMessage() for r in caplog.records if "завершился с кодом 2" in r.getMessage())
    assert msg.count("A") <= managed_process.EXIT_TAIL_CHARS
    assert "END" in msg


def test_captured_exit_still_logged_when_stopped_immediately(caplog):
    """Регресс: раньше поток читал self.proc сам и, если stop() успевал
    обнулить его до старта потока, лог завершения терялся."""
    p = _Sleeper()
    p._spawn_captured(SLEEP_CMD)
    p.stop()
    assert _wait_for(lambda: _has_log(caplog, "Sleeper", "завершился"))


def test_captured_uses_text_pipe_and_devnull_stdin(monkeypatch):
    captured = {}

    class P:
        pid = 1
        stdout = None

        def __init__(self, cmd, **kw):
            captured.update(kw)

        def wait(self):
            return 0

    monkeypatch.setattr(managed_process.subprocess, "Popen", P)
    _Sleeper()._spawn_captured(["x"], cwd="/w")
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.STDOUT
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["text"] is True and captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace" and captured["cwd"] == "/w"


# --------------------------------------------------------------------------
# ImagineProcess / RemoteProcess
# --------------------------------------------------------------------------


def test_imagine_process_contract():
    p = imagine_process.ImagineProcess("127.0.0.1", 7870, "127.0.0.1", 8188, dev_mode=True, remote_port=7861)
    assert isinstance(p, ManagedProcess) and p.label == "Imagine"
    assert (p.host, p.port, p.comfy_host, p.comfy_port) == ("127.0.0.1", 7870, "127.0.0.1", 8188)
    assert p.dev_mode is True and p.remote_port == 7861
    assert p.proc is None and p.is_running() is False


def test_remote_process_contract():
    p = remote_process.RemoteProcess("0.0.0.0", 7861, "127.0.0.1", 8188, imagine_port=7870)
    assert isinstance(p, ManagedProcess) and p.label == "Remote"
    assert (p.host, p.port, p.comfy_host, p.comfy_port) == ("0.0.0.0", 7861, "127.0.0.1", 8188)
    assert p.imagine_port == 7870 and p.dev_mode is False
    assert p.proc is None


def test_imagine_start_failure_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(
        imagine_process, "resolve_imagine_launch", lambda *a, **k: (None, None, "нет fastapi")
    )
    p = imagine_process.ImagineProcess("h", 1, "c", 2)
    with pytest.raises(RuntimeError, match="нет fastapi"):
        p.start()
    assert p.proc is None


def test_remote_start_failure_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(
        remote_process, "resolve_remote_launch", lambda *a, **k: (None, None, "нет zeroconf")
    )
    p = remote_process.RemoteProcess("h", 1, "c", 2)
    with pytest.raises(RuntimeError, match="нет zeroconf"):
        p.start()
    assert p.proc is None


def test_imagine_start_stop_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        imagine_process, "resolve_imagine_launch", lambda *a, **k: (SLEEP_CMD, str(tmp_path), None)
    )
    p = imagine_process.ImagineProcess("127.0.0.1", 7870, "127.0.0.1", 8188)
    returned = p.start()
    try:
        assert returned is p.proc and p.is_running()
        p.stop()
        assert p.proc is None and not p.is_running()
    finally:
        if returned.poll() is None:
            returned.kill()


def test_remote_start_stop_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        remote_process, "resolve_remote_launch", lambda *a, **k: (SLEEP_CMD, str(tmp_path), None)
    )
    p = remote_process.RemoteProcess("127.0.0.1", 7861, "127.0.0.1", 8188)
    returned = p.start()
    try:
        assert returned is p.proc and p.is_running()
        p.stop()
        assert p.proc is None
    finally:
        if returned.poll() is None:
            returned.kill()


def test_imagine_crash_output_reaches_log(monkeypatch, caplog):
    code = "import sys; print('ImportError: fastapi'); sys.exit(1)"
    monkeypatch.setattr(
        imagine_process, "resolve_imagine_launch",
        lambda *a, **k: ([sys.executable, "-c", code], None, None),
    )
    imagine_process.ImagineProcess("h", 1, "c", 2).start()
    assert _wait_for(lambda: _has_log(caplog, "Imagine", "завершился с кодом 1", "ImportError: fastapi"))


def test_resolve_launch_commands_unchanged(monkeypatch):
    monkeypatch.setattr(imagine_process, "_missing_imagine_dependencies", lambda: [])
    monkeypatch.setattr(remote_process, "_missing_remote_dependencies", lambda: [])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd, cwd, err = imagine_process.resolve_imagine_launch(
        "127.0.0.1", 7870, "127.0.0.1", 8188, True, remote_port=7861
    )
    assert err is None
    assert cmd[:3] == [sys.executable, "-m", "comfyui_studio.imagine"]
    assert cmd[3:] == [
        "--host", "127.0.0.1", "--port", "7870", "--comfy-host", "127.0.0.1",
        "--comfy-port", "8188", "--remote-port", "7861", "--dev",
    ]
    cmd, cwd, err = remote_process.resolve_remote_launch(
        "0.0.0.0", 7861, "127.0.0.1", 8188, 7870
    )
    assert err is None
    assert cmd[:3] == [sys.executable, "-m", "comfyui_studio.remote"]
    assert cmd[3:] == [
        "--host", "0.0.0.0", "--port", "7861", "--comfy-host", "127.0.0.1",
        "--comfy-port", "8188", "--imagine-port", "7870",
    ]


def test_resolve_launch_reports_missing_dependencies(monkeypatch):
    monkeypatch.setattr(imagine_process, "_missing_imagine_dependencies", lambda: ["fastapi"])
    cmd, cwd, err = imagine_process.resolve_imagine_launch("h", 1, "c", 2, False)
    assert cmd is None and "fastapi" in err


# --------------------------------------------------------------------------
# ComfyProcess (нужен Qt)
# --------------------------------------------------------------------------


class _FakePopen:
    pid = 777

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.stdout = io.BytesIO(b"line one\r\nline two\n")
        self.returncode = None

    def poll(self):
        return self.returncode


def test_comfy_process_start_builds_command_env_and_reads_log(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtCore")
    from comfyui_studio.launcher.core import comfy_process

    log_path = tmp_path / "comfy.log"
    monkeypatch.setattr(comfy_process, "COMFY_LOG_PATH", str(log_path))
    created = []
    monkeypatch.setattr(
        managed_process.subprocess, "Popen",
        lambda cmd, **kw: created.append(_FakePopen(cmd, **kw)) or created[-1],
    )
    bridge = comfy_process.ProcessLogBridge()
    p = comfy_process.ComfyProcess("C:/comfy", "run.bat", bridge, env_overrides={"A": "1"})
    assert isinstance(p, ManagedProcess) and p.label == "ComfyUI"
    assert p.start() is created[0] is p.proc

    fake = created[0]
    assert fake.cmd == ["cmd.exe", "/c", "run.bat"]
    assert fake.kw["cwd"] == "C:/comfy"
    assert fake.kw["env"]["A"] == "1" and "PATH" in fake.kw["env"]
    assert fake.kw["stdout"] == subprocess.PIPE and fake.kw["stderr"] == subprocess.STDOUT
    assert "creationflags" in fake.kw

    p._reader.join(5)
    assert log_path.read_text(encoding="utf-8") == "line one\nline two\n"
    assert p.is_running() is True
    fake.returncode = 5
    assert p.is_running() is False and p.exit_code() == 5


def test_comfy_process_env_overrides_do_not_leak_to_os_environ(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtCore")
    import os
    from comfyui_studio.launcher.core import comfy_process

    monkeypatch.setattr(comfy_process, "COMFY_LOG_PATH", str(tmp_path / "c.log"))
    monkeypatch.setattr(
        managed_process.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw)
    )
    p = comfy_process.ComfyProcess("r", "s.bat", comfy_process.ProcessLogBridge(), {"R3_ONLY": "x"})
    p.start()
    p._reader.join(5)
    assert "R3_ONLY" not in os.environ


def test_comfy_process_stop_kills_whole_tree_and_resets(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtCore")
    from comfyui_studio.launcher.core import comfy_process

    monkeypatch.setattr(comfy_process, "COMFY_LOG_PATH", str(tmp_path / "c.log"))
    stopped = []
    monkeypatch.setattr(
        managed_process, "terminate_process_tree", lambda proc, label, *a: stopped.append(label)
    )
    monkeypatch.setattr(
        managed_process.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw)
    )
    p = comfy_process.ComfyProcess("r", "s.bat", comfy_process.ProcessLogBridge())
    p.start()
    p.stop()
    assert stopped == ["ComfyUI"] and p.proc is None


# --------------------------------------------------------------------------
# external_apps (вынесено из comfy_process.py)
# --------------------------------------------------------------------------


def test_external_apps_registry():
    assert [a.subdir for a in external_apps.EXTERNAL_APPS] == ["prompt_builder", "promptvault"]
    assert [a.module_name for a in external_apps.EXTERNAL_APPS] == [
        "comfyui_studio.prompt_builder", "comfyui_studio.promptvault",
    ]


def test_old_location_no_longer_exports_external_apps():
    pytest.importorskip("PySide6.QtCore")
    from comfyui_studio.launcher.core import comfy_process

    assert not hasattr(comfy_process, "EXTERNAL_APPS")
    assert not hasattr(comfy_process, "launch_external_app")


def test_resolve_external_launch_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    app = external_apps.EXTERNAL_APPS[1]
    cmd, cwd, err = external_apps.resolve_external_launch(app)
    assert err is None
    assert cmd == [sys.executable, "-m", "comfyui_studio.promptvault"]
    assert cwd == external_apps.PROJECT_ROOT


def test_resolve_external_launch_frozen_without_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd, cwd, err = external_apps.resolve_external_launch(external_apps.EXTERNAL_APPS[0])
    assert cmd is None and cwd is None and "PromptConfigEditor.exe" in err


def test_resolve_external_launch_prefers_built_exe(monkeypatch, tmp_path):
    app = external_apps.ExternalApp("L", "sub", "Tool", "pkg.mod")
    monkeypatch.setattr(external_apps, "TOOLS_DIR", str(tmp_path))
    exe = tmp_path / "sub" / "dist" / "Tool" / "Tool.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    cmd, cwd, err = external_apps.resolve_external_launch(app)
    assert (cmd, cwd, err) == ([str(exe)], str(exe.parent), None)


def test_launch_external_app_reports_os_error(monkeypatch):
    app = external_apps.EXTERNAL_APPS[0]
    monkeypatch.setattr(
        external_apps, "resolve_external_launch", lambda a: (["x"], None, None)
    )

    def boom(*a, **k):
        raise OSError("нет доступа")

    monkeypatch.setattr(external_apps.subprocess, "Popen", boom)
    ok, message = external_apps.launch_external_app(app)
    assert ok is False and "нет доступа" in message


def test_launch_external_app_fire_and_forget(monkeypatch, caplog):
    app = external_apps.EXTERNAL_APPS[0]
    monkeypatch.setattr(
        external_apps, "resolve_external_launch",
        lambda a: ([sys.executable, "-c", "pass"], None, None),
    )
    ok, message = external_apps.launch_external_app(app)
    assert (ok, message) == (True, "")
    assert _wait_for(lambda: _has_log(caplog, app.label, "завершился, код выхода 0"))


# --------------------------------------------------------------------------
# remote_net (вынесено из remote_process.py)
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/remote/status":
            self._send(401, {"detail": "no token"})
        elif self.path == "/boom":
            self._send(503, {"detail": "down"})
        elif self.path == "/bad":
            self._send(400, {"detail": "Код истёк"})
        else:
            self._send(200, {"path": self.path})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        self._send(200, {"echo": data, "ctype": self.headers.get("Content-Type")})


@pytest.fixture
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_call_local_api_get_and_post(http_server):
    assert remote_net.call_local_api(http_server, "GET", "/devices") == {"path": "/devices"}
    out = remote_net.call_local_api(http_server, "POST", "/pair", json_body={"a": 1})
    assert out == {"echo": {"a": 1}, "ctype": "application/json"}


def test_call_local_api_http_error_and_detail_extraction(http_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        remote_net.call_local_api(http_server, "GET", "/bad")
    assert remote_net.extract_error_detail(exc.value) == "Код истёк"


def test_extract_error_detail_falls_back_to_str():
    err = urllib.error.HTTPError("http://x", 500, "Server Error", {}, io.BytesIO(b"not json"))
    assert "500" in remote_net.extract_error_detail(err)


def test_is_remote_available_any_http_answer_below_500(http_server):
    assert remote_net.is_remote_available(http_server) is True  # 401 — тоже «поднялся»


def test_is_remote_available_false_when_port_closed():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert remote_net.is_remote_available(port, timeout=0.3) is False


def test_get_lan_ip_returns_none_on_oserror(monkeypatch):
    class S:
        def connect(self, *a):
            raise OSError("no network")

        def close(self):
            pass

    monkeypatch.setattr("socket.socket", lambda *a, **k: S())
    assert remote_net.get_lan_ip() is None


def test_remote_process_module_does_not_reexport_net_helpers():
    """Хелперы живут в remote_net — потребители (launcher_window,
    remote/mdns.py) импортируют их оттуда."""
    for name in ("call_local_api", "extract_error_detail", "get_lan_ip", "is_remote_available"):
        assert not hasattr(remote_process, name)
