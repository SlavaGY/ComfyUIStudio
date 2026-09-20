"""
НАЙДЕННЫЙ БАГ (живой отчёт: "кнопка может выключить только то, что
запущено с телефона, а то что запущено с ПК не выключается") --
`comfy_launcher.py`/`app_launcher.py` умели убивать только процесс,
чей `subprocess.Popen`/`ImagineProcess` они сами держат в своей
переменной `_process` -- т.е. только то, что САМИ и запустили (через
`POST /apps/{id}/start` с телефона).

Причина глубже случайной недоработки: Remote -- ОТДЕЛЬНЫЙ ОС-процесс
от десктопного Studio (см. state.py -- `runtime.comfy_port`/
`imagine_port` приходят через CLI как голые числа порта, см.
__main__.py), а не поток внутри того же процесса, что и
`launcher_window.py`. Если ComfyUI/Imagine запущен КНОПКОЙ В STUDIO НА
ПК, объект `ComfyProcess`/`ImagineProcess` с его PID живёт в памяти
ДРУГОГО процесса (Studio) -- передать питоновский объект между
процессами нельзя в принципе, только через какой-то IPC, которого
здесь нет и не планировалось. У Remote в этом случае есть только номер
порта (`runtime.comfy_port`/`imagine_port`) -- этого модуля не
существовало, а stop_comfyui()/stop_imagine() просто не знали, что ещё
попробовать, когда собственный `_process is None`.

Раз готового PID нет -- ищем его по тому, что точно есть: порт, на
котором ComfyUI/Imagine слушает (это же используется для проверки
"запущен ли" через HTTP, см. ComfyAPIClient.is_available()/
is_imagine_available()). `psutil` уже тянется проектом (см.
launcher/core/system_monitor.py, mem_diagnostics.py) -- отдельной новой
зависимости не добавляет.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

import psutil

from ..launcher.core.logging_setup import log


def find_pid_listening_on_port(port: int) -> Optional[int]:
    """None, если ничего не слушает порт (в т.ч. если сам опрос упал --
    напр. без прав на чтение чужих соединений в некоторых окружениях,
    см. psutil.AccessDenied) -- вызывающая сторона в этом случае просто
    не находит, что останавливать, ровно как если бы порт и правда был
    свободен, а не падает всем Remote-процессом из-за диагностической
    функции."""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and conn.laddr.port == port
                and conn.pid
            ):
                return conn.pid
    except Exception:
        log.exception("Не удалось перечислить сетевые соединения (поиск PID по порту %s)", port)
    return None


def kill_pid_tree(pid: int, label: str) -> None:
    """`taskkill /F /T /PID` -- тот же приём, что уже используют
    `ComfyProcess.stop()`/`ImagineProcess.stop()`/
    `comfy_launcher.stop_comfyui()` -- флаг `/T` убивает всё поддерево
    процессов по PID (важно: PID, найденный по порту, -- это сам
    python.exe ComfyUI/Imagine, а не что-то поверх него, в отличие от
    `_process`-случая из comfy_launcher.py, где PID принадлежал
    промежуточному cmd.exe -- здесь /T всё равно не лишний: тот же
    python.exe вполне мог сам породить дочерние процессы, напр.
    воркеры). `label` -- только для лога ("ComfyUI"/"Imagine"), чтобы
    отличить, что именно останавливалось, при просмотре лога Studio на
    ПК."""
    log.info("Остановка %s (PID %s, найден по порту -- запущен не через Remote)", label, pid)
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            log.exception("Не удалось выполнить taskkill для PID %s (%s)", pid, label)
        return
    try:
        psutil.Process(pid).terminate()
    except Exception:
        log.exception("Не удалось остановить PID %s (%s)", pid, label)
