"""Launch the installed LM Studio desktop app with its original interface."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import psutil

from ...errors import ProcessStartError
from .logging_setup import log
from .managed_process import terminate_process_tree
from .native_window import find_app_window, window_exists


def find_lm_studio_executable() -> str:
    """Check common Windows install locations without searching the whole disk."""
    locations = (
        ("LOCALAPPDATA", "Programs/LM Studio/LM Studio.exe"),
        ("LOCALAPPDATA", "LM Studio/LM Studio.exe"),
        ("ProgramFiles", "LM Studio/LM Studio.exe"),
    )
    for variable, relative in locations:
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return ""


def lm_studio_executable(cfg: Mapping[str, object]) -> str:
    section = cfg.get("lm_studio")
    value = section.get("executable_path", "") if isinstance(section, dict) else ""
    # An explicitly configured missing path must not silently launch another installation.
    return value.strip() if isinstance(value, str) and value.strip() else find_lm_studio_executable()


def validate_lm_studio_executable(path: str) -> tuple[bool, str]:
    if not path:
        return False, "Укажите путь к LM Studio.exe в настройках LM Studio."
    if not Path(path).is_file():
        return False, "Файл LM Studio не найден. Проверьте путь в настройках LM Studio."
    if sys.platform == "win32" and Path(path).suffix.lower() != ".exe":
        return False, "Выберите исполняемый файл LM Studio.exe."
    return True, ""


def launch_lm_studio(path: str) -> subprocess.Popen:
    """Launch the desktop process; the controller owns its lifetime."""
    valid, message = validate_lm_studio_executable(path)
    if not valid:
        raise ProcessStartError(message)
    executable = str(Path(path).resolve())
    try:
        process = subprocess.Popen(
            [executable],
            cwd=str(Path(executable).parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as error:
        log.exception("Failed to launch LM Studio: %s", executable)
        raise ProcessStartError(str(error)) from error
    log.info("Launched LM Studio (PID %s, executable=%s)", process.pid, executable)
    return process


class LmStudioProcess:
    """Own a launched or adopted desktop instance, never processes by name.

    Electron may forward a second launch to an existing instance. Discover that
    window first and retain a psutil identity (including creation time), so Stop
    affects exactly the instance hosted by Studio, even after a PID is reused.
    """

    def __init__(self, executable: str):
        self.executable = str(Path(executable).resolve()) if executable else ""
        self.proc = None
        self.identity = None
        self.hwnd = None

    def start(self):
        if sys.platform != "win32":
            raise ProcessStartError("Встраивание LM Studio поддерживается только в Windows.")
        valid, message = validate_lm_studio_executable(self.executable)
        if not valid:
            raise ProcessStartError(message)
        existing = find_app_window(self.executable)
        if existing:
            self._adopt(*existing)
        else:
            self.proc = launch_lm_studio(self.executable)

    def _adopt(self, hwnd, pid):
        identity = psutil.Process(pid)
        identity.create_time()
        self.identity = identity
        self.hwnd = hwnd

    def poll_window(self):
        if self.hwnd is not None:
            return self.hwnd if window_exists(self.hwnd) and self.is_running() else None
        if self.proc is None:
            return None
        try:
            root = psutil.Process(self.proc.pid)
            pids = {root.pid, *(child.pid for child in root.children(recursive=True))}
        except psutil.NoSuchProcess:
            # The launcher may have handed off to Electron's single instance.
            pids = None
        found = find_app_window(self.executable, pids)
        if found:
            self._adopt(*found)
        return self.hwnd

    def is_running(self):
        if self.identity is not None:
            return self.identity.is_running()
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        target = self.identity if self.identity is not None else self.proc
        if target is not None and self.is_running():
            if not terminate_process_tree(target, "LM Studio"):
                raise ProcessStartError("Не удалось остановить LM Studio.")
        self.proc = self.identity = self.hwnd = None
