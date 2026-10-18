"""Launch the installed LM Studio desktop app with its original interface."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

from ...errors import ProcessStartError
from .logging_setup import log


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


def launch_lm_studio(path: str) -> None:
    """Open the native UI independently, as with other external desktop tools.

    The desktop app owns its models, chats and single-instance behavior. Studio
    does not infer API readiness from a GUI process or terminate it on exit.
    """
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
    threading.Thread(target=_log_exit, args=(process,), daemon=True).start()


def _log_exit(process: subprocess.Popen) -> None:
    code = process.wait()
    log.info("LM Studio launch process (PID %s) exited with code %s", process.pid, code)
