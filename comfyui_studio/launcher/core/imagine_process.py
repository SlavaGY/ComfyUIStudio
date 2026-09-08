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
этого используется тот же приём, что уже решает ровно эту же задачу для
воркера эмбеддингов PromptVault (см.
comfyui_studio/promptvault/core/embedding_ipc.py, _worker_command() и
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
import subprocess
import threading
import urllib.request
import urllib.error

from .constants import PROJECT_ROOT
from .logging_setup import log
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
    import importlib.util
    missing = [m for m in IMAGINE_REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    return missing


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
        # отдельного python.exe рядом) -- см. докстринг модуля и
        # аналогичный _worker_command() в embedding_ipc.py. cwd
        # намеренно не переопределяется (та же логика, что и у
        # WorkerHandle._spawn() там же) -- наследуется от текущего
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


class ImagineProcess:
    """Управляет процессом Imagine — по образцу ComfyProcess
    (core/comfy_process.py), но без парсинга/трансляции stdout в лог-
    панель лаунчера (Imagine — не ComfyUI, отдельного лог-протокола под
    него пока нет; вывод просто уходит в DEVNULL, как у prompt_builder/
    promptvault через launch_external_app)."""

    def __init__(self, host, port, comfy_host, comfy_port, dev_mode=False, remote_port=None):
        self.host = host
        self.port = port
        self.comfy_host = comfy_host
        self.comfy_port = comfy_port
        self.dev_mode = dev_mode
        self.remote_port = remote_port
        self.proc = None

    def start(self):
        cmd, cwd, error = resolve_imagine_launch(
            self.host, self.port, self.comfy_host, self.comfy_port, self.dev_mode,
            remote_port=self.remote_port,
        )
        if error:
            raise RuntimeError(error)

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.info(
            "Запуск Imagine: %s (cwd=%s, comfyui=%s:%s, dev=%s)",
            cmd, cwd, self.comfy_host, self.comfy_port, self.dev_mode,
        )
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=creationflags,
            # ИЗМЕНЕНО: раньше stdout/stderr уходили в DEVNULL -- любая
            # ошибка запуска (например ImportError из-за отсутствующих
            # зависимостей, до того как появилась предварительная
            # проверка выше) превращалась в голый "код выхода: 1" без
            # единой подсказки почему. Перехватываем и логируем в
            # _log_exit() ниже -- как минимум последний экран вывода,
            # этого обычно достаточно, чтобы увидеть traceback.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        watcher = threading.Thread(target=self._log_exit, daemon=True)
        watcher.start()
        return self.proc

    def _log_exit(self):
        proc = self.proc
        if proc is None:
            return
        output = ""
        try:
            output = proc.stdout.read() if proc.stdout else ""
        except Exception:
            log.exception("Не удалось прочитать вывод процесса Imagine")
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
        code = proc.wait()
        if code == 0:
            log.info("Imagine (PID %s) завершился, код выхода 0", proc.pid)
        else:
            # Обрезаем до последних ~4000 символов -- достаточно для
            # traceback'а любой обычной длины, не раздувает лог-файл
            # при зацикленных ошибках запуска.
            tail = output[-4000:] if output else "(процесс не вывел ничего в stdout/stderr)"
            log.error(
                "Imagine (PID %s) завершился с кодом %s. Вывод процесса:\n%s",
                proc.pid, code, tail,
            )

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.returncode if self.proc is not None else None

    def stop(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        log.info("Остановка Imagine (PID %s)", pid)
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                log.exception("Не удалось выполнить taskkill для PID %s", pid)
        else:
            try:
                self.proc.terminate()
            except Exception:
                log.exception("Не удалось остановить процесс PID %s", pid)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        self.proc = None


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
