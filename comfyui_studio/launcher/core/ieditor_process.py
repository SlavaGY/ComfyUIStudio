"""IEditor child process, using the same lifecycle as Imagine."""

import errno
import socket
import sys

from ...errors import ProcessStartError
from ...ieditor.__main__ import IEDITOR_CLI_FLAG
from .constants import PROJECT_ROOT
from .logging_setup import log
from .managed_process import ManagedProcess, find_missing_modules

IEDITOR_REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "multipart", "httpx")


def check_ieditor_port(host, port):
    """Fail before starting ComfyUI when another process owns the editor port.

    Checking by binding also catches an IEditor opened separately for preview.
    Never terminate or reuse the process occupying the port.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, int(port)))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            raise ProcessStartError(
                f"Порт IEditor {port} уже занят другим процессом. Закройте отдельно "
                "запущенный IEditor или измените «Порт IEditor» в настройках ComfyUI."
            ) from exc
        raise ProcessStartError(f"Не удалось открыть порт IEditor {port}: {exc}") from exc


def resolve_ieditor_launch(host, port, comfy_host, comfy_port):
    missing = find_missing_modules(IEDITOR_REQUIRED_MODULES)
    if missing:
        return None, None, (
            "Не установлены зависимости IEditor: " + ", ".join(missing) + ".\n"
            + ("Соберите комплект заново через build_exe.bat." if getattr(sys, "frozen", False)
               else "Выполните `pip install .[imagine]` в окружении ComfyUI Studio.")
        )
    args = ["--host", host, "--port", str(port), "--comfy-host", comfy_host, "--comfy-port", str(comfy_port)]
    if getattr(sys, "frozen", False):
        return [sys.executable, IEDITOR_CLI_FLAG, *args], None, None
    return [sys.executable, "-m", "comfyui_studio.ieditor", *args], PROJECT_ROOT, None


class IEditorProcess(ManagedProcess):
    label = "IEditor"

    def __init__(self, host, port, comfy_host, comfy_port):
        super().__init__()
        self.host, self.port = host, port
        self.comfy_host, self.comfy_port = comfy_host, comfy_port

    def start(self):
        cmd, cwd, error = resolve_ieditor_launch(self.host, self.port, self.comfy_host, self.comfy_port)
        if error:
            raise ProcessStartError(error)
        check_ieditor_port(self.host, self.port)
        log.info("Запуск IEditor: %s (cwd=%s)", cmd, cwd)
        return self._spawn_streamed(cmd, cwd)
