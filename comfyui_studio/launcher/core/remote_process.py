"""
Запуск и остановка Remote — REST API для удалённого/мобильного доступа
к Studio (comfyui_studio/remote/, см. ComfyUIStudio_Remote_Roadmap.md,
§0.1/этап 1).

По архитектуре — 1:1 та же схема диспетчеризации, что уже отработана
для Imagine (imagine_process.py): при frozen=True sys.executable — сам
собранный ComfyUIStudio.exe (отдельного python.exe рядом нет), поэтому
вместо поиска стороннего exe лаунчер запускает САМ СЕБЯ со скрытым
флагом REMOTE_CLI_FLAG — main.py ловит его в самом начале, до создания
QApplication и любых Qt-импортов (см. корневой main.py), и вместо
обычного GUI-запуска вызывает comfyui_studio.remote.__main__.main() с
оставшимися аргументами.

В ОТЛИЧИЕ от ImagineProcess: Remote не привязан к тому, запущен ли
ComfyUI/Imagine прямо сейчас (см. §0 дорожной карты — принцип "Remote
сам решает, что реально доступно"). Включение/выключение Remote в
настройках (см. ui/settings/remote_page.py) полностью независимо от
переключателя "Интерфейс: ComfyUI/Imagine" и от того, запущен ли сам
ComfyUI — Remote можно поднять даже тогда, когда ComfyUI ещё не
запускался вовсе (тогда GET /status просто отдаст comfyui_running=False).
"""

from __future__ import annotations

import os
import sys

from .constants import PROJECT_ROOT
from .logging_setup import log
from .managed_process import ManagedProcess, find_missing_modules
from ...remote.__main__ import REMOTE_CLI_FLAG

REMOTE_MODULE_NAME = "comfyui_studio.remote"
# Те же зависимости, что и у Imagine (см. IMAGINE_REQUIRED_MODULES в
# imagine_process.py) -- отдельной группы extras для Remote не заведено
# (см. pyproject.toml, комментарий у группы `imagine`): Remote --
# FastAPI/uvicorn-процесс ровно так же, как Imagine, и появился позже
# него, когда эти зависимости уже были в проекте.
REMOTE_REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "multipart", "httpx", "zeroconf")


def _missing_remote_dependencies():
    """См. аналогичную _missing_imagine_dependencies() в
    imagine_process.py -- тот же приём (importlib.util.find_spec в ТОМ
    ЖЕ интерпретаторе/exe, что и будет запускать Remote), та же причина
    (не запускать процесс, заранее зная, что ImportError оставит только
    голый код выхода 1 без единой подсказки)."""
    return find_missing_modules(REMOTE_REQUIRED_MODULES)


def resolve_remote_launch(host, port, comfy_host, comfy_port, imagine_port, dev_mode=False):
    """Аналог resolve_imagine_launch() -- возвращает (cmd, cwd, None)
    либо (None, None, error)."""
    extra_args = ["--host", host, "--port", str(port), "--comfy-host", comfy_host]
    if comfy_port:
        extra_args += ["--comfy-port", str(comfy_port)]
    if imagine_port:
        extra_args += ["--imagine-port", str(imagine_port)]
    if dev_mode:
        extra_args.append("--dev")

    missing = _missing_remote_dependencies()
    if missing:
        return None, None, (
            "Не установлены зависимости Remote в текущем окружении: "
            + ", ".join(missing) + ".\n"
            + (
                "Соберите комплект заново — build_exe.bat ставит extras "
                "'imagine' автоматически, Remote использует те же самые "
                "зависимости (см. pyproject.toml)."
                if getattr(sys, "frozen", False) else
                "Выполните `pip install .[imagine]` (fastapi/uvicorn/"
                "pydantic/python-multipart -- отдельной группы extras для "
                "Remote не заведено, см. pyproject.toml) в том же venv, из "
                "которого запущен ComfyUIStudio."
            )
        )

    if getattr(sys, "frozen", False):
        # sys.executable -- сам собранный ComfyUIStudio.exe, см. докстринг
        # модуля и аналогичный участок в imagine_process.py. cwd намеренно
        # не переопределяется -- наследуется от текущего процесса
        # лаунчера, Remote резолвит свои пути (device_store.py) абсолютно,
        # через %APPDATA%, а не относительно cwd.
        return [sys.executable, REMOTE_CLI_FLAG] + extra_args, None, None

    source_entry = os.path.join(PROJECT_ROOT, "comfyui_studio", "remote", "__main__.py")
    if not os.path.isfile(source_entry):
        return None, None, (
            f"Не найден пакет {REMOTE_MODULE_NAME} ({source_entry}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )

    return (
        [sys.executable, "-m", REMOTE_MODULE_NAME] + extra_args,
        PROJECT_ROOT,
        None,
    )


class RemoteProcess(ManagedProcess):
    """Управляет процессом Remote — по образцу ImagineProcess (см. её
    докстринг про перехват stdout/stderr вместо DEVNULL). Общая часть
    (is_running/exit_code/stop/логирование выхода) — в ManagedProcess.

    Сетевые хелперы (is_remote_available, call_local_api,
    extract_error_detail, get_lan_ip) — в core/remote_net.py."""

    label = "Remote"

    def __init__(self, host, port, comfy_host, comfy_port, imagine_port=None, dev_mode=False):
        super().__init__()
        self.host = host
        self.port = port
        self.comfy_host = comfy_host
        self.comfy_port = comfy_port
        self.imagine_port = imagine_port
        self.dev_mode = dev_mode

    def start(self):
        cmd, cwd, error = resolve_remote_launch(
            self.host, self.port, self.comfy_host, self.comfy_port,
            self.imagine_port, self.dev_mode,
        )
        if error:
            raise RuntimeError(error)

        log.info(
            "Запуск Remote: %s (cwd=%s, comfyui=%s:%s, imagine_port=%s)",
            cmd, cwd, self.comfy_host, self.comfy_port, self.imagine_port,
        )
        return self._spawn_captured(cmd, cwd)
