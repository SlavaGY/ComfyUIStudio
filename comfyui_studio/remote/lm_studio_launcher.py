"""Launch the local Studio chat from Remote without importing Qt."""

import threading
import time
import urllib.request

from ..launcher.core.config import load_config
from ..launcher.core.lm_studio_app_process import LmStudioAppProcess, is_app_ready
from .state import runtime

_lock = threading.Lock()
_process = None
_last_error = None


def status():
    if runtime.lm_studio_port is None:
        return "stopped"
    if is_app_ready(runtime.lm_studio_port):
        return "running"
    if _process is not None and _process.is_running():
        return "starting"
    return "stopped"


def last_error():
    if _last_error:
        return _last_error
    if _process is not None and not _process.is_running() and _process.exit_code() not in (None, 0):
        return "LM Studio завершился с ошибкой. Проверьте журнал Studio на ПК."
    return None


def start():
    global _process, _last_error
    if runtime.lm_studio_port is None:
        raise RuntimeError("LM Studio не настроен для Remote. Перезапустите удалённый доступ в Studio.")
    with _lock:
        if status() in ("running", "starting"):
            return
        _last_error = None
        cfg = load_config()
        cfg["lm_studio"] = {**(cfg.get("lm_studio") or {}), "ui_port": runtime.lm_studio_port}
        process = LmStudioAppProcess(cfg)
        try:
            process.start()
        except (RuntimeError, OSError) as error:
            _last_error = str(error)
            raise RuntimeError(_last_error) from error
        _process = process


def stop():
    global _process, _last_error
    with _lock:
        if _process is not None and not _process.is_running():
            _process = None
        if _process is not None:
            _process.stop()
            if _process.is_running():
                raise RuntimeError("Не удалось остановить LM Studio. Повторите остановку.")
            _process = None
        elif runtime.lm_studio_port is not None and is_app_ready(runtime.lm_studio_port):
            # Studio may own this process. Its shutdown endpoint releases the
            # engine and llama workers as well; killing only the UI would not.
            req = urllib.request.Request(f"http://127.0.0.1:{runtime.lm_studio_port}/api/shutdown",
                                         data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=3):
                    pass
            except OSError as error:
                raise RuntimeError("Не удалось отправить команду остановки LM Studio.") from error
            deadline = time.monotonic() + 20
            while is_app_ready(runtime.lm_studio_port):
                if time.monotonic() >= deadline:
                    raise RuntimeError("LM Studio ещё завершает работу. Повторите остановку.")
                time.sleep(0.2)
        _last_error = None
