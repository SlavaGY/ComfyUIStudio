"""
Запуск ComfyUI по запросу с телефона -- продолжение этапа "Запуск
приложений через Remote" (§Этап 6.4 дорожной карты). Раньше
`app_launcher.py` только ПРОВЕРЯЛ, поднят ли ComfyUI, и отказывался
запускать Imagine с понятной ошибкой, если нет -- без автозапуска (см.
"Живой баг и фикс" там же: Imagine открывался, но был бесполезен, пока
ComfyUI не подняли вручную через Studio).

Оказалось, что автозапуск ComfyUI ВОЗМОЖЕН без нарушения инварианта
"Remote никогда не импортирует Qt": вся подготовка запуска
(`launcher.core.config.load_config/build_extra_launch_args/
prepare_launch_script`) на поверку Qt-независима -- Qt-зависим только
сам `launcher.core.comfy_process.ComfyProcess`, и то лишь из-за моста
живого лога в Qt-панель настроек (`ProcessLogBridge`), который Remote
всё равно не может использовать (у него нет Qt-окна, куда приземлять
сигналы). Здесь -- Qt-независимый аналог: та же подготовка конфига и
временного .bat, что и в `_on_launch()`
(`launcher/ui/launcher_window.py`), тот же запуск, что и в
`ComfyProcess.start()`, просто без `ProcessLogBridge`/`_LogReaderThread`
-- вывод процесса логируется в файл напрямую.

Сам процесс -- потомок `managed_process.ManagedProcess` (как ComfyProcess,
ImagineProcess, RemoteProcess): общий запуск без консольного окна, лог
завершения с хвостом вывода и остановка `taskkill /F /T` с запасным
`kill()`. Раньше здесь был голый Popen с копией `_log_exit()` и остановкой
без проверки результата: процесс, переживший остановку, терял единственную
ссылку («фантомный процесс»).
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from ..launcher.core.comfy_api import ComfyAPIClient
from ..launcher.core.config import build_extra_launch_args, load_config, prepare_launch_script
from ..launcher.core.logging_setup import log
from ..launcher.core.managed_process import ManagedProcess
from .process_by_port import find_pid_listening_on_port, kill_pid_tree
from .state import runtime

_lock = threading.Lock()
# Сообщение о ПОСЛЕДНЕЙ неудаче автозапуска (напр. ComfyUI сразу упал
# после старта) -- POST /start к этому моменту уже давно ответил 202,
# так что единственный способ показать это на телефоне -- отдавать
# через GET /apps (см. app_launcher.imagine_last_error() и AppEntry.error).
_last_error: Optional[str] = None

# Один инстанс на процесс -- по тому же принципу, что и в app_launcher.py.
_comfy_client = ComfyAPIClient()


class _RemoteComfyProcess(ManagedProcess):
    """`cmd.exe /c <launch_script>`, запущенный из Remote (см. докстринг
    модуля). Ненулевой код выхода, не вызванный stop(), превращается в
    короткое сообщение для телефона (`_last_error`); хвост вывода при
    аварии пишет в лог Studio сам ManagedProcess."""

    label = "ComfyUI"

    def start(self, launch_script, cwd, env):
        self._spawn_streamed(["cmd.exe", "/c", launch_script], cwd=cwd, env=env)

    def _on_failed_exit(self, proc, code):
        global _last_error
        # Короткая версия для телефона -- полный вывод только в лог-файл
        # Studio на ПК, как и у обычного запуска через UI.
        _last_error = (
            f"ComfyUI завершился с кодом {code} сразу после запуска -- "
            "проверьте лог ComfyUI на ПК (вкладка настроек в Studio)."
        )


_process: Optional[_RemoteComfyProcess] = None


def is_comfyui_running(port: Optional[int] = None) -> bool:
    cfg = load_config()
    p = port or cfg.get("port")
    return bool(p) and _comfy_client.is_available(port=p)


def comfyui_status(port: Optional[int] = None) -> str:
    """"running" | "starting" | "stopped" -- тот же принцип, что
    app_launcher.imagine_status(). `port` -- см. докстринг start_comfyui()
    про то, почему вызывающая сторона (app_launcher.py) всегда передаёт
    `runtime.comfy_port`, а не полагается на cfg["port"] по умолчанию."""
    if is_comfyui_running(port):
        return "running"
    if _process is not None and _process.is_running():
        return "starting"
    return "stopped"


def last_error() -> Optional[str]:
    return _last_error


def start_comfyui(port: Optional[int] = None) -> None:
    """Бросает RuntimeError с понятным сообщением, если ComfyUI ещё не
    настроен вовсе (пустая/несуществующая папка portable, не выбран
    скрипт запуска) -- та же диагностика, которую пользователь увидел
    бы, попытавшись нажать "Старт" в Studio с пустыми настройками.

    `port`, если передан (см. app_launcher.py -- всегда передаёт
    `runtime.comfy_port`), ПЕРЕОПРЕДЕЛЯЕТ cfg["port"] из config.json --
    единственный источник истины о том, на каком порту ComfyUI ждёт
    Imagine, должен быть тот же `runtime.comfy_port`, которым уже
    пользуется весь остальной Remote (см. state.py), а не то, что
    сейчас лежит в config.json -- он мог измениться в настройках Studio
    уже ПОСЛЕ того, как Remote запустился с прежним значением (Remote
    требует перезапуска "Удалённого доступа", чтобы подхватить новый
    порт, см. §0 дорожной карты) -- без этого override можно было бы
    поднять ComfyUI на одном порту, а Imagine продолжил бы стучаться на
    другой (тот же класс бага, что уже случился один раз, только
    сдвинутый на слой ниже)."""
    global _process, _last_error
    with _lock:
        if comfyui_status(port) in ("running", "starting"):
            return

        cfg = dict(load_config())
        if port:
            cfg["port"] = port
        root_path = cfg.get("root_path")
        script = cfg.get("script")
        if not root_path or not os.path.isdir(root_path):
            raise RuntimeError(
                "Папка ComfyUI portable не настроена в Studio -- откройте "
                "Studio на ПК и укажите её в настройках, прежде чем "
                "запускать ComfyUI с телефона."
            )
        if not script:
            raise RuntimeError(
                "Скрипт запуска ComfyUI не выбран в настройках Studio -- "
                "откройте Studio на ПК и выберите run_*.bat."
            )

        try:
            launch_script = prepare_launch_script(root_path, script, build_extra_launch_args(cfg))
        except OSError as e:
            raise RuntimeError(f"Не удалось подготовить скрипт запуска ComfyUI: {e}")

        env = os.environ.copy()
        env_overrides = cfg.get("env_vars")
        if env_overrides:
            env.update(env_overrides)

        log.info("Запуск ComfyUI через Remote: %s (cwd=%s)", launch_script, root_path)
        _last_error = None
        proc = _RemoteComfyProcess()
        try:
            proc.start(launch_script, root_path, env)
        except OSError as e:
            # НОВОЕ по сравнению с ComfyProcess/ImagineProcess (те не
            # оборачивают Popen) -- здесь ошибка иначе улетела бы наружу
            # как голый необработанный OSError мимо HTTPException в
            # routes/apps.py (тот ловит только RuntimeError) и дошла бы
            # до телефона как безликий 500 без текста.
            raise RuntimeError(f"Не удалось запустить ComfyUI: {e}")
        _process = proc


def stop_comfyui() -> None:
    """НОВОЕ (живая просьба: "запустить с телефона можем, а выключить
    нет" + "чтобы на ПК не появлялись фантомные процессы от предыдущих
    сессий") -- см. routes/system.py::stop_server, дергается кнопкой
    "Выключить сервер" на домашней странице терминала (routes/home.py).

    `_process` здесь -- это САМ `cmd.exe /c <launch_script>` из
    start_comfyui() выше, а не питоновский процесс ComfyUI напрямую
    (тот запускается уже ВНУТРИ .bat-скрипта, как его дочерний
    процесс) -- обычный `proc.terminate()` убил бы только этот
    промежуточный cmd.exe, оставив реальный ComfyUI (и всё, что он сам
    мог успеть породить) сиротой в дереве процессов -- буквально тот
    самый "фантомный процесс" из отчёта. `taskkill /F /T /PID` -- тот
    же приём, что уже используют `ComfyProcess.stop()`/
    `ImagineProcess.stop()` в launcher/core/ (см. их докстринги): флаг
    `/T` убивает всё поддерево процессов по PID, а не только сам PID.

    НОВОЕ: если `_process is None` (ComfyUI запущен НЕ через Remote, а
    кнопкой в самой Studio на ПК -- см. живой отчёт "кнопка выключает
    только то, что запущено с телефона"), ниже есть fallback через
    process_by_port.py -- см. его докстринг про то, почему Remote
    физически не может иметь PID процесса, запущенного другим ОС-
    процессом."""
    global _process, _last_error
    with _lock:
        if _process is not None:
            # ManagedProcess.stop(): taskkill по дереву, затем kill(), если
            # процесс пережил штатную остановку. Ссылка сбрасывается только
            # когда процесса действительно больше нет.
            if _process.stop():
                _process = None
                _last_error = None
                return
            log.warning(
                "ComfyUI, запущенный через Remote, не остановился -- пробуем найти процесс по порту"
            )

        # НОВОЕ (живой отчёт: "кнопка может выключить только то, что
        # запущено с телефона") -- см. докстринг process_by_port.py про
        # причину: если ComfyUI подняли кнопкой в самой Studio на ПК, а
        # не с телефона, у ЭТОГО процесса (Remote) попросту нет ни
        # Popen-объекта, ни PID для него напрямую -- ищем по порту,
        # который Remote и так уже знает (runtime.comfy_port, тот же,
        # которым пользуется is_comfyui_running() выше).
        if runtime.comfy_port:
            port_pid = find_pid_listening_on_port(runtime.comfy_port)
            if port_pid is not None:
                kill_pid_tree(port_pid, "ComfyUI")
        _last_error = None


def clear_state() -> None:
    """Только для тестов -- см. app_launcher.clear_state()."""
    global _process, _last_error
    _process = None
    _last_error = None
