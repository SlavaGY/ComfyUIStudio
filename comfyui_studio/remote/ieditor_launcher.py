"""Start/stop IEditor from a paired phone, sharing the existing ComfyUI."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

from ..launcher.core.ieditor_process import IEditorProcess, check_ieditor_port
from . import comfy_launcher
from .process_by_port import find_pid_listening_on_port, kill_pid_tree
from .state import runtime

_lock = threading.Lock()
_process = None
_awaiting_comfy = False
_last_error = None
_generation = 0
_COMFY_STARTUP_TIMEOUT_S = 300
_COMFY_POLL_INTERVAL_S = 2


def is_ready(port):
    if port is None:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
            # Do not identify an unrelated HTTP service as a running editor.
            return response.status == 200 and b"<title>IEditor" in response.read(2048)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def status():
    if runtime.ieditor_port is None:
        return "stopped"
    if is_ready(runtime.ieditor_port):
        return "running"
    if _awaiting_comfy or (_process is not None and _process.is_running()):
        return "starting"
    return "stopped"


def last_error():
    if _last_error:
        return _last_error
    if _process is not None and not _process.is_running() and _process.exit_code() not in (None, 0):
        return "IEditor завершился с ошибкой. Проверьте журнал Studio на ПК."
    return None


def start():
    global _generation, _awaiting_comfy, _last_error
    if runtime.ieditor_port is None or runtime.comfy_port is None:
        raise RuntimeError("IEditor не настроен для Remote. Перезапустите удалённый доступ в Studio.")
    with _lock:
        if status() in ("running", "starting"):
            return
        _last_error = None
        # Report an occupied port without launching another ComfyUI first.
        check_ieditor_port("127.0.0.1", runtime.ieditor_port)
        _generation += 1
        if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
            _spawn()
            return
        if comfy_launcher.comfyui_status(port=runtime.comfy_port) == "stopped":
            comfy_launcher.start_comfyui(port=runtime.comfy_port)
        _awaiting_comfy = True
        threading.Thread(target=_wait_for_comfy_then_start, args=(_generation,), daemon=True).start()


def _spawn():
    global _process
    process = IEditorProcess(
        host="127.0.0.1", port=runtime.ieditor_port,
        comfy_host=runtime.comfy_host, comfy_port=runtime.comfy_port,
    )
    process.start()
    _process = process


def _wait_for_comfy_then_start(generation):
    global _awaiting_comfy, _last_error
    deadline = time.monotonic() + _COMFY_STARTUP_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            time.sleep(_COMFY_POLL_INTERVAL_S)
            with _lock:
                if generation != _generation:
                    return
            if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
                with _lock:
                    if generation == _generation:
                        # Desktop Studio may have finished starting IEditor
                        # while Remote was waiting for the shared ComfyUI.
                        if not is_ready(runtime.ieditor_port):
                            _spawn()
                return
            error = comfy_launcher.last_error()
            if error:
                with _lock:
                    if generation == _generation:
                        _last_error = error
                return
        with _lock:
            if generation == _generation:
                _last_error = f"ComfyUI не поднялся за {_COMFY_STARTUP_TIMEOUT_S} секунд. Проверьте журнал Studio."
    except (RuntimeError, OSError) as error:
        with _lock:
            if generation == _generation:
                _last_error = str(error)
    finally:
        with _lock:
            if generation == _generation:
                _awaiting_comfy = False


def stop():
    """Stop only IEditor; cancelling a cold start must not resurrect it."""
    global _generation, _awaiting_comfy, _process, _last_error
    with _lock:
        _generation += 1
        _awaiting_comfy = False
        if _process is not None and not _process.is_running():
            _process = None
        if _process is not None:
            _process.stop()
            _process = None
        elif is_ready(runtime.ieditor_port):
            # IEditor may have been started by the desktop Studio process.
            pid = find_pid_listening_on_port(runtime.ieditor_port)
            if pid is not None:
                kill_pid_tree(pid, "IEditor")
        _last_error = None
