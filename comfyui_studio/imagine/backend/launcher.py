"""
Минимальный запуск/остановка ComfyUI из этого инструмента.

Специально НЕ пытается повторить ComfyUIStudio (мониторинг ресурсов,
трей, темы, логи в UI и т.д.) -- там это уже сделано полноценно, и при
интеграции этого инструмента в Studio данный модуль, скорее всего,
просто выпилится в пользу существующего comfy_process.py. Здесь --
только то, что нужно, чтобы отдельный инструмент мог "поднять" ComfyUI
сам, если пользователь не запустил его заранее: subprocess.Popen на
указанный .bat, без парсинга логов на предмет готовности (готовность
определяется отдельно -- поллингом ComfyClient.is_alive() со стороны
API-эндпоинта /api/comfyui/start, см. main.py).
"""

import subprocess
import sys
from pathlib import Path

_process: subprocess.Popen | None = None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def start(bat_path: str) -> None:
    global _process
    if is_running():
        return
    path = Path(bat_path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл запуска не найден: {bat_path}")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    _process = subprocess.Popen(
        [str(path)],
        cwd=str(path.parent),
        creationflags=creationflags,
    )


def stop() -> None:
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
    _process = None
