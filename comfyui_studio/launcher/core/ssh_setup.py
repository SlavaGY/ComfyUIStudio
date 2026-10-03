"""
ssh_setup.py -- запуск tools/ssh_setup/Setup-SshAccess.ps1 с повышением
прав из ComfyUI Studio и разбор результата.

Кнопка в ui/settings/remote_page.py эмитит ssh_setup_requested; сама
страница ничего не запускает (тот же принцип, что и у остального Remote
-- см. докстринг RemoteSettingsPage) -- запуском и HTTP/процессным
взаимодействием занимается launcher_window.py, вызывая start_ssh_setup()
отсюда.

Элевация -- через ShellExecuteExW с verb="runas" (единственный способ
получить настоящий UAC-диалог из обычного, неэлевированного процесса
Studio) и SEE_MASK_NOCLOSEPROCESS, чтобы получить хэндл и дождаться
завершения синхронно -- в отличие от голого ShellExecuteW, который хэндл
не отдаёт вовсе. Само ожидание -- в фоновом потоке (см. QObject-мост
ниже, тот же приём, что и ProcessLogBridge в comfy_process.py), чтобы не
подвешивать GUI-поток на время, пока пользователь смотрит на UAC-диалог
и на сам скрипт (30-60 секунд, в основном сетевые вызовы Get-Windows*
и Restart-Service).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import tempfile
import threading
import uuid

from PySide6.QtCore import QObject, Signal

from .constants import TOOLS_DIR

log = logging.getLogger(__name__)

SCRIPT_PATH = os.path.join(TOOLS_DIR, "ssh_setup", "Setup-SshAccess.ps1")

# ERROR_CANCELLED -- пользователь сам отклонил UAC-диалог; отдельно от
# прочих ошибок, чтобы не пугать формулировкой "не удалось запустить"
# в совершенно штатной ситуации "передумал".
_ERROR_CANCELLED = 1223


class SshSetupWorker(QObject):
    """Мост из фонового потока в GUI-поток -- Qt сам маршализует сигнал,
    т.к. экземпляр создаётся и живёт в потоке, откуда его слушают
    (см. докстринг модуля)."""

    succeeded = Signal(dict)
    failed = Signal(str)


def start_ssh_setup(
    username: str,
    ssh_port: int,
    permit_open_host: str,
    permit_open_port: int,
) -> SshSetupWorker:
    """Запускает настройку в фоновом потоке, сразу возвращает worker --
    вызывающий код (launcher_window.py) подключается к его сигналам ДО
    того, как поток реально что-то сделает (GIL это гарантирует: поток
    ещё не успеет дойти до emit к моменту, пока текущая функция не
    вернёт управление и вызывающий код не подключит слушателей)."""
    worker = SshSetupWorker()
    thread = threading.Thread(
        target=_run_in_background,
        args=(worker, username, ssh_port, permit_open_host, permit_open_port),
        daemon=True,
    )
    thread.start()
    return worker


def _run_in_background(
    worker: SshSetupWorker,
    username: str,
    ssh_port: int,
    permit_open_host: str,
    permit_open_port: int,
) -> None:
    if platform.system() != "Windows":
        worker.failed.emit("Автонастройка SSH доступна только на Windows.")
        return
    if not os.path.isfile(SCRIPT_PATH):
        worker.failed.emit(f"Не найден скрипт настройки: {SCRIPT_PATH}")
        return

    output_path = os.path.join(
        tempfile.gettempdir(), f"comfyui_studio_ssh_setup_{uuid.uuid4().hex}.json"
    )
    params = (
        f'-NoProfile -ExecutionPolicy Bypass -File "{SCRIPT_PATH}" '
        f'-Username "{username}" -SshPort {ssh_port} '
        f'-PermitOpenHost "{permit_open_host}" -PermitOpenPort {permit_open_port} '
        f'-OutputJsonPath "{output_path}"'
    )

    try:
        exit_code = _run_elevated_and_wait("powershell.exe", params, TOOLS_DIR)
    except OSError as e:
        if e.errno == _ERROR_CANCELLED:
            worker.failed.emit("Отменено -- запрос прав администратора был отклонён.")
        else:
            log.exception("Не удалось запустить Setup-SshAccess.ps1 с повышением прав")
            worker.failed.emit(f"Не удалось запустить настройку: {e}")
        return

    if not os.path.isfile(output_path):
        worker.failed.emit(
            f"Скрипт завершился (код выхода {exit_code}), но результат не найден -- "
            "смотрите окно PowerShell (оно ждёт Enter перед закрытием) на предмет ошибки."
        )
        return

    try:
        # "utf-8-sig", а не "utf-8": Write-Result в PS1-скрипте пишет
        # через `Out-File -Encoding utf8`, а это в Windows PowerShell 5.1
        # (в отличие от pwsh/Core) ВСЕГДА означает UTF-8 с BOM, не
        # опционально -- живой тест 2026-09-27 упал ровно на этом
        # ("Unexpected UTF-8 BOM"). "utf-8-sig" молча съедает BOM, если
        # он есть, и работает как обычный utf-8, если его нет -- одним
        # кодом закрывает оба варианта PowerShell.
        with open(output_path, "r", encoding="utf-8-sig") as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        worker.failed.emit(f"Не удалось прочитать результат настройки: {e}")
        return
    finally:
        # Файл содержит приватный ключ в открытом виде -- не оставляем
        # его во временной папке дольше, чем нужно на чтение.
        try:
            os.remove(output_path)
        except OSError:
            pass

    if result.get("error"):
        worker.failed.emit(result["error"])
        return
    worker.succeeded.emit(result)


def _run_elevated_and_wait(exe_path: str, params: str, workdir: str) -> int:
    """ShellExecuteExW(verb="runas") + WaitForSingleObject -- см.
    докстринг модуля про то, почему не голый ShellExecuteW/subprocess."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hKeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1
    INFINITE = 0xFFFFFFFF

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = exe_path
    sei.lpParameters = params
    sei.lpDirectory = workdir
    sei.nShow = SW_SHOWNORMAL

    ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        err = ctypes.GetLastError()
        raise OSError(err, "ShellExecuteExW не удался")

    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, INFINITE)
    exit_code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return exit_code.value
