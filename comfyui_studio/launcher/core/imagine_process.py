"""
Запуск и остановка Imagine — карточного веб-генератора поверх ComfyUI
(comfyui_studio/imagine/), встроенного в Studio как АЛЬТЕРНАТИВНЫЙ
способ открыть ComfyUI (см. "Интерфейс" в ui/settings/comfyui_page.py):
вместо загрузки сырого веб-интерфейса ComfyUI во встроенный браузер
(BrowserPage.load(cfg["port"]), см. ui/launcher_window.py), при
включённом режиме Imagine лаунчер ДОПОЛНИТЕЛЬНО поднимает Imagine как
свой процесс, направляет его на уже запущенный этим же лаунчером
ComfyUI (host/port из cfg — единственный источник истины, см.
ImagineProcess.start() ниже) и показывает уже ЕГО интерфейс.

ИЗМЕНЕНО: раньше resolve_imagine_launch() искал ОТДЕЛЬНО собранный
tools/imagine/dist/Imagine/Imagine.exe — которого build_exe.bat никогда
не собирал (весь комплект собирается ОДНИМ exe, см. его шапку и
main.py), поэтому после сборки Imagine не находил себя и падал. Вместо
этого используется приём "self-exec со скрытым CLI-флагом" (см.
IMAGINE_CLI_FLAG-диспетчеризацию в main.py): при frozen=True
sys.executable — это сам же собранный ComfyUIStudio.exe (отдельного
python.exe рядом нет), поэтому вместо поиска стороннего exe лаунчер
просто запускает САМ СЕБЯ со скрытым флагом IMAGINE_CLI_FLAG — main.py
ловит его в самом начале, до создания QApplication и любых Qt-импортов,
и вместо обычного GUI-запуска вызывает
comfyui_studio.imagine.__main__.main() с оставшимися аргументами
(--host/--port/--comfy-host/--comfy-port/--dev). Один и тот же exe в
итоге умеет быть и GUI, и Imagine-подпроцессом самого себя — как и
воркер эмбеддингов, отдельной сборки/спека для этого не нужно (см.
build_exe.bat: единственное, что ему нужно, — чтобы fastapi/uvicorn/
pydantic/python-multipart были в venv сборки и, значит, попали в exe;
см. IMAGINE_REQUIRED_MODULES ниже и pyproject.toml).
"""

import os
import sys
import urllib.request
import urllib.error

from .constants import PROJECT_ROOT
from .logging_setup import log
from .managed_process import ManagedProcess, find_missing_modules
from ...imagine.__main__ import IMAGINE_CLI_FLAG

IMAGINE_MODULE_NAME = "comfyui_studio.imagine"
IMAGINE_REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "multipart", "websockets")


def _missing_imagine_dependencies():
    """importlib.util.find_spec() здесь запускается в ТОМ ЖЕ
    интерпретаторе/exe, что и будет запускать Imagine (см.
    resolve_imagine_launch() ниже — что при запуске из исходников
    (sys.executable = системный python того же venv), что из
    собранного exe (sys.executable = сам себя же и запустит через
    IMAGINE_CLI_FLAG) — поэтому в обоих случаях надёжно предсказывает,
    что будет доступно дочернему процессу. Добавлено после того, как
    отсутствие extras 'imagine' (см. pyproject.toml) приводило к тому,
    что Imagine падал с ImportError сразу на импорте fastapi/uvicorn —
    process.poll() возвращал код выхода 1 без единой подсказки, ПОКА
    stderr уходил в DEVNULL (см. историю ImagineProcess.start() ниже —
    теперь он туда не уходит, но лучше вообще не запускать процесс,
    заранее зная, что он не поднимется)."""
    return find_missing_modules(IMAGINE_REQUIRED_MODULES)


def resolve_imagine_launch(host, port, comfy_host, comfy_port, dev_mode, remote_port=None):
    """Аналог resolve_external_launch() из comfy_process.py, но для
    Imagine и с прокидыванием аргументов запуска (host/port/comfy-*/dev)
    вместо простого списка без параметров -- у prompt_builder/
    promptvault параметров командной строки нет, у Imagine они есть, и
    нужны И собранному exe, И запуску из исходников одинаково.

    remote_port -- НОВОЕ (этап 3 дорожной карты Remote): порт Remote,
    куда фоновая задача progress_forwarder.py внутри Imagine пересылает
    generation.progress (см. её докстринг). Необязателен -- если Remote
    выключен в настройках, просто не передаётся, Imagine работает как
    раньше.

    Возвращает (cmd: list[str], cwd: str, error: None) либо
    (None, None, error: str).
    """
    extra_args = ["--host", host, "--port", str(port)]
    if comfy_host:
        extra_args += ["--comfy-host", comfy_host]
    if comfy_port:
        extra_args += ["--comfy-port", str(comfy_port)]
    if remote_port:
        extra_args += ["--remote-port", str(remote_port)]
    if dev_mode:
        extra_args.append("--dev")

    missing = _missing_imagine_dependencies()
    if missing:
        return None, None, (
            "Не установлены зависимости Imagine в текущем окружении: "
            + ", ".join(missing) + ".\n"
            + (
                "Соберите комплект заново — build_exe.bat ставит extras "
                "'imagine' автоматически (см. pyproject.toml)."
                if getattr(sys, "frozen", False) else
                "Выполните `pip install .[imagine]` (или "
                "`pip install fastapi uvicorn[standard] pydantic python-multipart`) "
                "в том же venv, из которого запущен ComfyUIStudio."
            )
        )

    if getattr(sys, "frozen", False):
        # sys.executable -- это сам собранный ComfyUIStudio.exe (нет
        # отдельного python.exe рядом) -- см. докстринг модуля. cwd
        # намеренно не переопределяется -- наследуется от текущего
        # процесса лаунчера, Imagine сам резолвит все свои пути
        # абсолютно от расположения пакета (см. backend/main.py
        # _STATIC_DIR, backend/workflow.py _ASSETS_DIR).
        return [sys.executable, IMAGINE_CLI_FLAG] + extra_args, None, None

    source_entry = os.path.join(PROJECT_ROOT, "comfyui_studio", "imagine", "__main__.py")
    if not os.path.isfile(source_entry):
        return None, None, (
            f"Не найден пакет {IMAGINE_MODULE_NAME} ({source_entry}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )

    return (
        [sys.executable, "-m", IMAGINE_MODULE_NAME] + extra_args,
        PROJECT_ROOT,
        None,
    )


class ImagineProcess(ManagedProcess):
    """Управляет процессом Imagine — по образцу ComfyProcess
    (core/comfy_process.py), но без парсинга/трансляции stdout в лог-
    панель лаунчера (Imagine — не ComfyUI, отдельного лог-протокола под
    него пока нет). Вывод перехватывается и при аварийном завершении
    его хвост пишется в лог (ManagedProcess._watch_exit) — раньше он
    уходил в DEVNULL, и ошибка запуска сводилась к «код выхода: 1».
    Общая часть (is_running/exit_code/stop) — в ManagedProcess."""

    label = "Imagine"

    def __init__(self, host, port, comfy_host, comfy_port, dev_mode=False, remote_port=None):
        super().__init__()
        self.host = host
        self.port = port
        self.comfy_host = comfy_host
        self.comfy_port = comfy_port
        self.dev_mode = dev_mode
        self.remote_port = remote_port

    def start(self):
        cmd, cwd, error = resolve_imagine_launch(
            self.host, self.port, self.comfy_host, self.comfy_port, self.dev_mode,
            remote_port=self.remote_port,
        )
        if error:
            raise RuntimeError(error)

        log.info(
            "Запуск Imagine: %s (cwd=%s, comfyui=%s:%s, dev=%s)",
            cmd, cwd, self.comfy_host, self.comfy_port, self.dev_mode,
        )
        return self._spawn_captured(cmd, cwd)


def is_imagine_available(port, timeout=1.0):
    """Простой HTTP-опрос готовности (аналог ComfyAPIClient.is_available()
    у LaunchWatcher, но без завязки на API-семантику ComfyUI — Imagine
    отдаёт свою статику по '/', 200 OK достаточно как признак
    готовности)."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False
