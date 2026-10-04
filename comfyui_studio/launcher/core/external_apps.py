"""
Запуск внешних инструментов комплекта (Prompt Builder и PromptVault) как
отдельных процессов, когда лаунчер не в монолитном режиме.

Вынесено из comfy_process.py (этап R3): к жизненному циклу ComfyUI этот
код отношения не имеет. Логика перенесена без изменений. Модуль не
импортирует Qt.

Монолитный режим (ComfyUIStudio): когда лаунчер запущен как часть общего
однопроцессного приложения (корневой main.py), остальные инструменты
открываются как окна ЭТОГО ЖЕ процесса — main.py регистрирует фабрики
через register_in_process_app() (integration/tool_registry.py) до показа
окна лаунчера. Если фабрика для данного subdir не зарегистрирована,
launch_external_app() ниже по-прежнему пробует отдельный процесс/exe.
"""

import os
import sys
import threading
import subprocess

from .constants import PROJECT_ROOT, TOOLS_DIR
from .logging_setup import log


class ExternalApp:
    """Описание одного внешнего инструмента комплекта: где искать
    собранный exe (фиксированная подпапка в tools/<subdir>/dist/...,
    туда его кладут build_windows.bat/build.bat самих инструментов —
    это НЕ затронуто переносом исходников под comfyui_studio/, см.
    ниже) и как запустить его из исходников как пакет, если лаунчер
    сам не заморожен PyInstaller-ом."""

    def __init__(self, label, subdir, exe_name, module_name):
        self.label = label
        self.subdir = subdir  # подпапка внутри tools/ — ТОЛЬКО для поиска dist/<exe_name>.exe
        self.exe_name = exe_name
        # Пакет для запуска из исходников: `python -m <module_name>` —
        # начиная с этапа 2 дорожной карты (перенос prompt_builder/
        # promptvault под общее пространство имён comfyui_studio/) это
        # comfyui_studio.prompt_builder / comfyui_studio.promptvault, а
        # НЕ tools/<subdir> — там (см. свойство root ниже) с этапа 2
        # исходников инструмента больше нет, только служебные файлы
        # сборки (build.spec/README/requirements.txt) и, если собран,
        # dist/ с exe.
        self.module_name = module_name

    @property
    def root(self):
        """Папка со СБОРОЧНЫМИ артефактами инструмента (tools/<subdir>/,
        там же dist/<exe_name>/<exe_name>.exe после сборки) — НЕ папка
        с исходниками; те теперь под comfyui_studio/<subdir>/, см.
        source_entry_abs."""
        return os.path.join(TOOLS_DIR, self.subdir)

    @property
    def source_entry_abs(self):
        """Путь к __main__.py пакета в исходниках (comfyui_studio/<subdir>/
        __main__.py), которым можно проверить, что "python -m
        <module_name>" вообще имеет смысл пробовать — путь считается
        от PROJECT_ROOT (корень проекта), а не от app.root (tools/<subdir>/),
        т.к. это разные папки после переноса под comfyui_studio/ (этап 2
        дорожной карты)."""
        return os.path.join(PROJECT_ROOT, "comfyui_studio", self.subdir, "__main__.py")


EXTERNAL_APPS = [
    ExternalApp(
        label="Character / Prompt Builder Config Editor",
        subdir="prompt_builder",
        exe_name="PromptConfigEditor",
        module_name="comfyui_studio.prompt_builder",
    ),
    ExternalApp(
        label="PromptVault",
        subdir="promptvault",
        exe_name="PromptVault",
        module_name="comfyui_studio.promptvault",
    ),
]


def resolve_external_launch(app: "ExternalApp"):
    """Определяет, как запустить внешнее приложение комплекта в отдельном
    процессе:

      1. Если рядом лежит собранный PyInstaller-exe
         (tools/<subdir>/dist/<exe_name>/<exe_name>.exe) — запускаем его
         напрямую. Работает независимо от того, запущен ли сам лаунчер из
         исходников или тоже собран в exe.
      2. Иначе, если лаунчер запущен из исходников (не заморожен), пробуем
         запустить пакет тем же интерпретатором Python: `python -m
         <module_name>` (comfyui_studio.prompt_builder /
         comfyui_studio.promptvault) с рабочей папкой PROJECT_ROOT — так
         же, как их запускает монолитный main.py, только отдельным
         процессом вместо окна в этом же. До этапа 2 дорожной карты
         (перенос исходников под comfyui_studio/) здесь был путь вида
         `tools/<subdir>/main.py` — устарел вместе с самой структурой.
      3. Иначе — понятная ошибка вместо тихого "ничего не произошло".

    Возвращает (cmd: list[str], cwd: str, error: None) либо
    (None, None, error: str).
    """
    app_root = app.root
    exe_path = os.path.join(app_root, "dist", app.exe_name, app.exe_name + ".exe")
    if os.path.isfile(exe_path):
        return [exe_path], os.path.dirname(exe_path), None

    if getattr(sys, "frozen", False):
        return None, None, (
            f"Не найден собранный {app.exe_name}.exe ({exe_path}).\n"
            "Соберите приложение сборочным скриптом в его папке — запуск "
            "исходников из собранного лаунчера невозможен."
        )

    if not os.path.isfile(app.source_entry_abs):
        return None, None, (
            f"Не найден пакет {app.module_name} ({app.source_entry_abs}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )

    return [sys.executable, "-m", app.module_name], PROJECT_ROOT, None


def launch_external_app(app: "ExternalApp"):
    """Запускает внешнее приложение комплекта как независимый,
    самостоятельный процесс (не дочерний в смысле логики приложения —
    лаунчер за ним не следит и не останавливает при своём закрытии).
    Возвращает (ok: bool, message: str)."""
    cmd, cwd, error = resolve_external_launch(app)
    if error:
        return False, error

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        log.exception("Не удалось запустить %s", app.label)
        return False, f"Не удалось запустить {app.label}: {e}"

    log.info("Запущен %s (PID %s, cmd=%s, cwd=%s)", app.label, proc.pid, cmd, cwd)

    # Раньше лаунчер вообще не отслеживал, что стало с этим процессом
    # дальше — при жалобах "закрыл инструмент, а память не освободилась"
    # не было даже лога, чтобы проверить, действительно ли процесс
    # завершился. Здесь только логируем сам факт и время завершения —
    # ничего не останавливаем и не мониторим активно (см. докстринг выше).
    watcher = threading.Thread(
        target=_log_external_app_exit,
        args=(app.label, proc),
        daemon=True,
    )
    watcher.start()

    return True, ""


def _log_external_app_exit(label, proc):
    exit_code = proc.wait()
    log.info("%s (PID %s) завершился, код выхода %s", label, proc.pid, exit_code)
