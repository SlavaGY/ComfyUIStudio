"""
Запуск и остановка Imagine Pony (comfyui_studio/imagine_pony/) -- второго
веб-генератора в стиле Imagine, под API-граф PonyXL. Зеркало
imagine_process.py: тот же вариант запуска ComfyUI ("Интерфейс" в
ui/settings/comfyui_page.py), тот же приём self-exec со скрытым CLI-флагом
для собранного exe (см. диспетчеризацию IMAGINE_PONY_CLI_FLAG в main.py).

Отличия от Imagine: нет дев-режима и нет связи с Remote (progress_forwarder
у Imagine Pony не реализован), поэтому аргументов меньше.
"""

import os
import sys

from .constants import PROJECT_ROOT
from .logging_setup import log
from .managed_process import ManagedProcess, find_missing_modules
from ...errors import ProcessStartError
from ...imagine_pony.__main__ import IMAGINE_PONY_CLI_FLAG

IMAGINE_PONY_MODULE_NAME = "comfyui_studio.imagine_pony"
# Те же зависимости, что у Imagine (extras 'imagine' в pyproject.toml);
# websockets Imagine Pony не нужен.
IMAGINE_PONY_REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic")


def resolve_imagine_pony_launch(host, port, comfy_host, comfy_port):
    """Возвращает (cmd, cwd, error) -- как resolve_imagine_launch()."""
    extra_args = ["--host", host, "--port", str(port)]
    if comfy_host:
        extra_args += ["--comfy-host", comfy_host]
    if comfy_port:
        extra_args += ["--comfy-port", str(comfy_port)]

    missing = find_missing_modules(IMAGINE_PONY_REQUIRED_MODULES)
    if missing:
        return None, None, (
            "Не установлены зависимости Imagine Pony в текущем окружении: "
            + ", ".join(missing) + ".\n"
            + (
                "Соберите комплект заново — build_exe.bat ставит extras "
                "'imagine' автоматически (см. pyproject.toml)."
                if getattr(sys, "frozen", False) else
                "Выполните `pip install .[imagine]` в том же venv, из "
                "которого запущен ComfyUIStudio."
            )
        )

    if getattr(sys, "frozen", False):
        return [sys.executable, IMAGINE_PONY_CLI_FLAG] + extra_args, None, None

    source_entry = os.path.join(PROJECT_ROOT, "comfyui_studio", "imagine_pony", "__main__.py")
    if not os.path.isfile(source_entry):
        return None, None, (
            f"Не найден пакет {IMAGINE_PONY_MODULE_NAME} ({source_entry}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )
    return (
        [sys.executable, "-m", IMAGINE_PONY_MODULE_NAME] + extra_args,
        PROJECT_ROOT,
        None,
    )


class ImaginePonyProcess(ManagedProcess):
    """Процесс Imagine Pony -- по образцу ImagineProcess (вывод
    перехватывается, при аварийном завершении хвост пишется в лог)."""

    label = "Imagine Pony"

    def __init__(self, host, port, comfy_host, comfy_port):
        super().__init__()
        self.host = host
        self.port = port
        self.comfy_host = comfy_host
        self.comfy_port = comfy_port

    def start(self):
        cmd, cwd, error = resolve_imagine_pony_launch(
            self.host, self.port, self.comfy_host, self.comfy_port,
        )
        if error:
            raise ProcessStartError(error)
        log.info(
            "Запуск Imagine Pony: %s (cwd=%s, comfyui=%s:%s)",
            cmd, cwd, self.comfy_host, self.comfy_port,
        )
        return self._spawn_captured(cmd, cwd)
