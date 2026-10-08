"""
Запуск/остановка Imagine Pony с телефона -- зеркало Imagine-части
app_launcher.py (status/start/last_error/stop), см. его докстринг про то,
почему запуск переиспользует Qt-независимый механизм лаунчера
(core/imagine_pony_process.py) и поднимает ComfyUI сам, если он ещё не
запущен.

Отдельный модуль, а не обобщение app_launcher.py: у того есть тесты и
живая отладка по реальным отчётам (холодный старт ComfyUI, фантомные
процессы), и менять его ради второго приложения -- лишний риск.
Состояние здесь своё; ComfyUI общий (comfy_launcher), поэтому перед его
запуском проверяется, не стартует ли он уже по запросу Imagine.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..launcher.core.imagine_pony_process import ImaginePonyProcess
from ..launcher.core.imagine_process import is_imagine_available
from . import comfy_launcher
from .process_by_port import find_pid_listening_on_port, kill_pid_tree
from .state import runtime

_lock = threading.Lock()
_process: Optional[ImaginePonyProcess] = None
_awaiting_comfy = False
_last_error: Optional[str] = None

# Те же значения, что в app_launcher.py (холодный старт ComfyUI на HDD).
_COMFY_STARTUP_TIMEOUT_S = 300
_COMFY_POLL_INTERVAL_S = 2


def pony_status() -> str:
    """"running" | "starting" | "stopped"."""
    if runtime.imagine_pony_port is None:
        return "stopped"
    # is_imagine_available -- просто GET http://127.0.0.1:<port>/, не
    # привязан к самому Imagine.
    if is_imagine_available(runtime.imagine_pony_port):
        return "running"
    if _awaiting_comfy or (_process is not None and _process.is_running()):
        return "starting"
    return "stopped"


def pony_last_error() -> Optional[str]:
    if _last_error:
        return _last_error
    if (
        _process is not None
        and not _process.is_running()
        and _process.exit_code() not in (None, 0)
        and pony_status() == "stopped"
    ):
        return (
            f"Imagine Pony завершился с кодом {_process.exit_code()} сразу после "
            "запуска -- проверьте лог на ПК (вкладка настроек в Studio)."
        )
    return None


def start_pony() -> None:
    """Бросает RuntimeError только для синхронно обнаруживаемых ошибок
    (см. app_launcher.start_imagine)."""
    global _awaiting_comfy, _last_error
    if runtime.imagine_pony_port is None:
        raise RuntimeError(
            "Imagine Pony не настроен для этого запуска Remote -- перезапустите "
            "«Удалённый доступ» в настройках Studio."
        )
    with _lock:
        if pony_status() in ("running", "starting"):
            return
        _last_error = None
        if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
            _spawn()
            return
        # ComfyUI мог быть уже запущен Imagine-цепочкой и ещё грузиться.
        if comfy_launcher.comfyui_status(port=runtime.comfy_port) == "stopped":
            comfy_launcher.start_comfyui(port=runtime.comfy_port)
        _awaiting_comfy = True
        threading.Thread(target=_wait_for_comfy_then_start, daemon=True).start()


def _spawn() -> None:
    """Только под _lock."""
    global _process
    proc = ImaginePonyProcess(
        host="127.0.0.1",
        port=runtime.imagine_pony_port,
        comfy_host=runtime.comfy_host,
        comfy_port=runtime.comfy_port,
    )
    proc.start()
    _process = proc


def _wait_for_comfy_then_start() -> None:
    global _awaiting_comfy, _last_error
    deadline = time.monotonic() + _COMFY_STARTUP_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            time.sleep(_COMFY_POLL_INTERVAL_S)
            if comfy_launcher.is_comfyui_running(port=runtime.comfy_port):
                with _lock:
                    _spawn()
                return
            comfy_error = comfy_launcher.last_error()
            if comfy_error:
                with _lock:
                    _last_error = comfy_error
                return
        with _lock:
            _last_error = (
                f"ComfyUI не поднялся за {_COMFY_STARTUP_TIMEOUT_S} секунд -- "
                "проверьте лог запуска на ПК в Studio."
            )
    except (RuntimeError, OSError):
        # _spawn(): ProcessStartError (нет зависимостей, не найден пакет) или
        # ошибка ОС при старте процесса -- иначе телефон завис бы в
        # «запускается…» навсегда.
        with _lock:
            _last_error = "Не удалось автоматически запустить Imagine Pony после ComfyUI (см. лог Studio на ПК)."
    finally:
        with _lock:
            _awaiting_comfy = False


def stop_pony() -> None:
    """Как app_launcher.stop_imagine(): свой процесс -- через .stop(),
    запущенный из Studio -- поиском PID по порту."""
    global _process, _awaiting_comfy, _last_error
    with _lock:
        if _process is not None:
            _process.stop()
            _process = None
        elif runtime.imagine_pony_port:
            pid = find_pid_listening_on_port(runtime.imagine_pony_port)
            if pid is not None:
                kill_pid_tree(pid, "Imagine Pony")
        _awaiting_comfy = False
        _last_error = None


def clear_state() -> None:
    """Только для тестов."""
    global _process, _awaiting_comfy, _last_error
    _process = None
    _awaiting_comfy = False
    _last_error = None
