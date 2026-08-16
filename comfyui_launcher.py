"""
ComfyUI Portable Launcher (PySide6 / Qt)
=========================================

Однопроцессное Qt-приложение с тремя "страницами" в одном окне:

  1. Настройки   — путь к ComfyUI_windows_portable, выбор run_*.bat, порт,
                   панель лога последнего запуска.
  2. Ожидание    — прогресс-бар, пока поднимается сервер ComfyUI.
  3. Браузер     — QWebEngineView (Chromium) с интерфейсом ComfyUI:
                   без адресной строки, без вкладок, без возможности
                   открыть постороннее окно поверх приложения.

Плюс: иконка приложения и трея, мониторинг CPU/RAM/GPU и очереди
генераций (всплывающая подсказка трея), логирование через модуль
logging, автосохранение настроек и раздельные кнопки "Настройки"
(не останавливает сервер) / "Остановить" (останавливает).

Зависимости: pip install -r requirements.txt
Запуск:      python comfyui_launcher.py
Сборка exe:  см. build_exe.bat
"""

import os
import sys
import gc
import json
import re
import time
import logging
import threading
import subprocess
import urllib.request
import urllib.error
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QObject
from PySide6.QtGui import QDesktopServices, QIcon, QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QLabel,
    QFileDialog,
    QStackedWidget,
    QProgressBar,
    QMessageBox,
    QSizePolicy,
    QPlainTextEdit,
    QGroupBox,
    QSystemTrayIcon,
    QMenu,
    QDialog,
    QScrollArea,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from themes.theme_manager import ThemeManager
from i18n import LocalizationManager

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None


APP_NAME = "ComfyUI Launcher"
APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUILauncher"
)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
WEBENGINE_PROFILE_DIR = os.path.join(APP_DIR, "webengine_profile")
LAUNCH_SCRIPT_TMP = os.path.join(APP_DIR, "_launch_current.bat")
COMFY_LOG_PATH = os.path.join(APP_DIR, "comfyui_last_run.log")
APP_LOG_PATH = os.path.join(APP_DIR, "launcher.log")

DEFAULT_CONFIG = {
    "root_path": "",
    "script": "",
    "port": 8188,
    "disable_auto_launch": True,
    "sync_comfy_theme": False,
}

# --------------------------------------------------------------------------
# Другие приложения комплекта (PromptConfigEditor, PromptVault) — запускаются
# как полностью отдельные процессы, никак не связанные с процессом ComfyUI
# и друг с другом. Общее между ними — только тема оформления (shared_theme.py).
#
# Оба приложения поставляются В ОДНОМ архиве с лаунчером, в фиксированном
# месте (tools/<subdir> рядом с самим лаунчером) — поэтому путь к ним не
# нужно ни указывать, ни сохранять в конфиге: он вычисляется от расположения
# самого лаунчера (см. app_base_dir()).
# --------------------------------------------------------------------------

def app_base_dir():
    """Папка, где лежит сам лаунчер: рядом с ней ожидается tools/ с
    остальными приложениями комплекта. Это НЕ resource_path()/_MEIPASS —
    та временная папка распаковки PyInstaller существует только пока
    процесс жив и не содержит соседних приложений; тут же нужна папка,
    где реально на диске лежит exe (или .py при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


TOOLS_DIR = os.path.join(app_base_dir(), "tools")


class ExternalApp:
    """Описание одного внешнего инструмента комплекта: где его искать
    (фиксированная подпапка в tools/), как найти собранный exe и как
    запустить из исходников, если лаунчер сам не заморожен PyInstaller-ом."""

    def __init__(self, label, subdir, exe_name, source_entry_rel, source_cmd):
        self.label = label
        self.subdir = subdir  # подпапка внутри tools/
        self.exe_name = exe_name
        self.source_entry_rel = source_entry_rel  # относит. путь для проверки "это похоже на нужную папку"
        self.source_cmd = source_cmd  # sys.executable -> list[str] аргументов запуска из исходников

    @property
    def root(self):
        return os.path.join(TOOLS_DIR, self.subdir)


EXTERNAL_APPS = [
    ExternalApp(
        label="Character / Prompt Builder Config Editor",
        subdir="prompt_builder",
        exe_name="PromptConfigEditor",
        source_entry_rel="main.py",
        source_cmd=lambda py: [py, "main.py"],
    ),
    ExternalApp(
        label="PromptVault",
        subdir="promptvault",
        exe_name="PromptVault",
        source_entry_rel=os.path.join("app", "main.py"),
        source_cmd=lambda py: [py, "-m", "app.main"],
    ),
]


# --------------------------------------------------------------------------
# Монолитный режим (ComfyUIStudio): когда лаунчер запущен как часть общего
# однопроцессного приложения (см. корневой main.py), остальные инструменты
# комплекта открываются как окна ЭТОГО ЖЕ процесса, а не отдельные
# подпроцессы -- корневой main.py регистрирует здесь фабрику окна для
# каждого app.subdir через register_in_process_app() ДО того, как
# показывается это окно лаунчера. Если фабрика для данного subdir не
# зарегистрирована (лаунчер запущен сам по себе, как раньше, через
# `python comfyui_launcher.py` или отдельно собранный exe) -- поведение не
# меняется: launch_external_app() ниже по-прежнему пробует отдельный
# процесс/exe.
# --------------------------------------------------------------------------
IN_PROCESS_WINDOW_FACTORIES = {}


def register_in_process_app(subdir, factory):
    """factory: сallable без аргументов, возвращающий готовое (но ещё не
    показанное) QWidget/QMainWindow -- см. create_window() в
    tools/prompt_builder/main.py и tools/promptvault/app/main.py."""
    IN_PROCESS_WINDOW_FACTORIES[subdir] = factory


def resolve_external_launch(app: "ExternalApp"):
    """Определяет, как запустить внешнее приложение комплекта в отдельном
    процессе:

      1. Если рядом лежит собранный PyInstaller-exe
         (tools/<subdir>/dist/<exe_name>/<exe_name>.exe) — запускаем его
         напрямую. Работает независимо от того, запущен ли сам лаунчер из
         исходников или тоже собран в exe.
      2. Иначе, если лаунчер запущен из исходников (не заморожен), пробуем
         запустить исходники приложения тем же интерпретатором Python.
      3. Иначе — понятная ошибка вместо тихого "ничего не произошло".

    Возвращает (cmd: list[str], cwd: str, error: None) либо
    (None, None, error: str).
    """
    app_root = app.root
    if not os.path.isdir(app_root):
        return None, None, (
            f"Не найдена папка {app_root} — похоже, «{app.label}» не "
            "распакован вместе с лаунчером (ожидается в tools/{}) ."
            .format(app.subdir)
        )

    exe_path = os.path.join(app_root, "dist", app.exe_name, app.exe_name + ".exe")
    if os.path.isfile(exe_path):
        return [exe_path], os.path.dirname(exe_path), None

    if getattr(sys, "frozen", False):
        return None, None, (
            f"Не найден собранный {app.exe_name}.exe ({exe_path}).\n"
            "Соберите приложение сборочным скриптом в его папке — запуск "
            "исходников из собранного лаунчера невозможен."
        )

    entry_abs = os.path.join(app_root, app.source_entry_rel)
    if not os.path.isfile(entry_abs):
        return None, None, (
            f"Не найден {app.source_entry_rel} в {app_root} — "
            "похоже, папка приложения повреждена или неполная."
        )

    return app.source_cmd(sys.executable), app_root, None


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

MAX_LOG_PANEL_LINES = 2000
RESOURCE_POLL_INTERVAL_MS = 2000


def resource_path(relative):
    """Путь к бандловым ресурсам (иконка и т.п.), работает и из исходников,
    и из собранного PyInstaller-exe."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


ICON_PATH = resource_path(os.path.join("assets", "icon.ico"))


# --------------------------------------------------------------------------
# Логирование (замена print/самодельных файлов)
# --------------------------------------------------------------------------

def setup_logging():
    os.makedirs(APP_DIR, exist_ok=True)
    logger = logging.getLogger("comfyui_launcher")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        APP_LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


log = setup_logging()


# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

def load_config():
    os.makedirs(APP_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
        except Exception:
            log.exception("Не удалось прочитать config.json, используем значения по умолчанию")
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Не удалось сохранить config.json")


# --------------------------------------------------------------------------
# Работа с папкой ComfyUI portable
# --------------------------------------------------------------------------

def find_run_scripts(root_path):
    """Ищет run_*.bat в корне и в подпапке advanced/."""
    scripts = []
    if not root_path or not os.path.isdir(root_path):
        return scripts
    for entry in sorted(os.listdir(root_path)):
        low = entry.lower()
        if low.startswith("run_") and low.endswith(".bat"):
            scripts.append(entry)
    adv = os.path.join(root_path, "advanced")
    if os.path.isdir(adv):
        for entry in sorted(os.listdir(adv)):
            low = entry.lower()
            if low.startswith("run_") and low.endswith(".bat"):
                scripts.append(os.path.join("advanced", entry))
    return scripts


def validate_portable_root(root_path):
    if not root_path or not os.path.isdir(root_path):
        return False, "Указанная папка не существует"
    py = os.path.join(root_path, "python_embeded", "python.exe")
    main_py = os.path.join(root_path, "ComfyUI", "main.py")
    if not os.path.isfile(py):
        return False, "Не найден python_embeded\\python.exe — это не похоже на ComfyUI portable"
    if not os.path.isfile(main_py):
        return False, "Не найден ComfyUI\\main.py"
    if not find_run_scripts(root_path):
        return False, "В папке не найдено ни одного run_*.bat"
    return True, "OK"


def guess_default_script(scripts):
    for name in scripts:
        if os.path.basename(name).lower() == "run_nvidia_gpu.bat":
            return name
    for name in scripts:
        if os.path.basename(name).lower() == "run_cpu.bat":
            return name
    return scripts[0] if scripts else ""


def is_port_open(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout):
            return True
    except urllib.error.URLError:
        return False
    except Exception:
        return False


STEP_INPUT_KEYS = ("steps", "sampling_steps", "num_steps")  # имена входов, которые ищем в узлах графа

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Строка прогресс-бара сэмплера ComfyUI (tqdm), например:
# "74%|███████▍         | 26/35 [00:24<00:07, 1.21it/s]" -- см.
# ResourceMonitor.feed_log_line. Единица скорости бывает "it/s" (шагов в
# секунду) или, на медленных шагах, "s/it" (секунд на шаг) -- у tqdm это
# переключается автоматически в зависимости от того, что читабельнее.
_TQDM_PROGRESS_RE = re.compile(
    r"(?P<cur>\d+)/(?P<total>\d+)\s*"
    r"\[[^<\]]*<[^,\]]*,\s*(?P<rate>[\d.]+)\s*(?P<unit>it/s|s/it)\]"
)


def count_steps_in_prompt(prompt_dict):
    """Сумма числового поля "steps" по всем узлам графа задания (формат
    API-графа: {node_id: {class_type, inputs}}) — грубая, но рабочая
    эвристика объёма работы для KSampler/KSamplerAdvanced и большинства
    кастомных сэмплеров с тем же именем входа. Если steps приходит не
    числом (ссылка на другой узел), просто пропускаем этот узел -- тянуть
    оттуда рекурсивно не будем, оценка и так приблизительная."""
    total = 0
    if not prompt_dict:
        return total
    for node in prompt_dict.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not inputs:
            continue
        for key in STEP_INPUT_KEYS:
            v = inputs.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


def fetch_queue_status(port, timeout=1.5):
    """Возвращает {"running", "pending", "running_ids", "step_totals"} из
    /queue ComfyUI, или None, если недоступно.

    running_ids -- set() prompt_id заданий, которые ПРЯМО СЕЙЧАС
    выполняются (не просто числятся первыми в очереди) — нужно, чтобы
    ResourceMonitor мог отличить их от только что закончившихся в
    /history (см. fetch_history_ids ниже и комментарий в _poll).

    step_totals -- {prompt_id: total_steps} для ВСЕХ заданий в очереди
    (бегущих и ожидающих), см. count_steps_in_prompt() -- нужно для
    оценки оставшегося времени всей очереди."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/queue", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        running_items = data.get("queue_running", [])
        pending_items = data.get("queue_pending", [])
        running_ids = {item[1] for item in running_items if len(item) > 1}
        step_totals = {}
        for item in running_items + pending_items:
            if len(item) > 2:
                step_totals[item[1]] = count_steps_in_prompt(item[2])
        return {
            "running": len(running_items),
            "pending": len(pending_items),
            "running_ids": running_ids,
            "step_totals": step_totals,
        }
    except Exception:
        return None


def fetch_history_ids(port, timeout=1.5):
    """Возвращает set() prompt_id всех записей /history ComfyUI, или None,
    если недоступно. /history — собственный журнал ComfyUI обо всех
    запросах, которые ДОЕХАЛИ до конца (успешно или с ошибкой; висящие в
    очереди туда не попадают, добавляются туда только после завершения
    исполнения — см. execution.py/PromptQueue.task_done в самом
    ComfyUI)."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/history", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return set(data.keys())
    except Exception:
        return None


# Каталог доп. аргументов командной строки ComfyUI (main.py), которые
# можно включить чекбоксом в LaunchArgsDialog (см. ниже) — вместо того,
# чтобы заставлять пользователя редактировать сам .bat вручную. Список не
# исчерпывающий: несколько наиболее часто нужных флагов из документации
# ComfyUI (VRAM-режимы, сетевой доступ, превью, доп. пути к моделям).
# takes_value=True + value_required=False (только --listen) означает, что
# флаг можно включить и без значения (ComfyUI сам возьмёт 0.0.0.0).
LAUNCH_ARG_DEFS = [
    dict(
        id="listen", flag="--listen", takes_value=True, value_required=False,
        placeholder="0.0.0.0",
        desc_ru=(
            "Слушать на всех сетевых интерфейсах — ComfyUI станет доступен "
            "с других устройств в локальной сети. Поле необязательно: IP "
            "для прослушивания (по умолчанию — все интерфейсы)."
        ),
    ),
    dict(
        id="cpu", flag="--cpu", takes_value=False,
        desc_ru="Считать только на CPU, без GPU (медленно, но работает без видеокарты).",
    ),
    dict(
        id="lowvram", flag="--lowvram", takes_value=False,
        desc_ru="Меньше VRAM ценой части скорости — для видеокарт с небольшим объёмом VRAM.",
    ),
    dict(
        id="novram", flag="--novram", takes_value=False,
        desc_ru="Минимум VRAM — для видеокарт с очень малым объёмом VRAM (если --lowvram не помогает).",
    ),
    dict(
        id="highvram", flag="--highvram", takes_value=False,
        desc_ru="Держать модели в VRAM постоянно — для видеокарт с большим запасом VRAM.",
    ),
    dict(
        id="gpu_only", flag="--gpu-only", takes_value=False,
        desc_ru=(
            "Держать вообще всё, включая текстовые энкодеры, в VRAM — для "
            "видеокарт с очень большим запасом VRAM."
        ),
    ),
    dict(
        id="reserve_vram", flag="--reserve-vram", takes_value=True, value_required=True,
        placeholder="2",
        desc_ru="Зарезервировать под другие программы указанный объём VRAM, в гигабайтах.",
    ),
    dict(
        id="disable_xformers", flag="--disable-xformers", takes_value=False,
        desc_ru="Отключить оптимизацию xFormers (если из-за неё возникают ошибки или чёрные изображения).",
    ),
    dict(
        id="preview_method", flag="--preview-method", takes_value=True, value_required=True,
        placeholder="taesd",
        desc_ru="Способ показа превью во время генерации: none, auto, latent2rgb или taesd.",
    ),
    dict(
        id="extra_model_paths_config", flag="--extra-model-paths-config",
        takes_value=True, value_required=True,
        placeholder=r"C:\path\to\extra_model_paths.yaml",
        desc_ru=(
            "Загрузить дополнительный файл extra_model_paths.yaml с путями "
            "к моделям/LoRA и т.п., лежащим вне папки ComfyUI."
        ),
    ),
]


def build_extra_launch_args(cfg):
    """Собирает итоговый список CLI-флагов из cfg["launch_args"]
    (заполняется LaunchArgsDialog) плюс отдельно хранящийся
    disable_auto_launch — единый список строк для prepare_launch_script."""
    args = []
    if cfg.get("disable_auto_launch"):
        args.append("--disable-auto-launch")
    launch_args = cfg.get("launch_args", {})
    for d in LAUNCH_ARG_DEFS:
        entry = launch_args.get(d["id"], {})
        if not entry.get("enabled"):
            continue
        if d.get("takes_value"):
            value = (entry.get("value") or "").strip()
            if value:
                args.append(f"{d['flag']} {value}")
            elif not d.get("value_required"):
                args.append(d["flag"])
            # value_required и пусто — пропускаем молча, чекбокс без
            # значения для такого флага смысла не имеет.
        else:
            args.append(d["flag"])
    return args


def prepare_launch_script(root_path, script_rel_path, extra_args):
    """
    Копирует выбранный .bat во временный файл, добавляя к строке запуска
    python.exe main.py переданные доп. аргументы (см. LAUNCH_ARG_DEFS и
    build_extra_launch_args выше) — чтобы не редактировать сам .bat
    вручную. Исходный .bat в папке ComfyUI не изменяется.
    """
    src_abs = os.path.join(root_path, script_rel_path)
    with open(src_abs, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    if extra_args:
        suffix = " " + " ".join(extra_args)
        new_lines = []
        for line in lines:
            low = line.lower()
            if "python.exe" in low and "main.py" in low:
                line = line.rstrip("\r\n") + suffix + os.linesep
            new_lines.append(line)
        lines = new_lines

    os.makedirs(APP_DIR, exist_ok=True)
    with open(LAUNCH_SCRIPT_TMP, "w", encoding="utf-8", errors="ignore") as f:
        f.writelines(lines)
    return LAUNCH_SCRIPT_TMP


# Наша тема оформления -> ближайшая ВСТРОЕННАЯ палитра ComfyUI
# (Comfy.ColorPalette: dark/light/nord/github/solarized/arc). Для тем без
# точного аналога берём ближайшую по духу тёмную/светлую базу — полного
# соответствия цветов ждать не стоит, у ComfyUI своя система цветов узлов,
# отдельная от Qt-темы приложения.
COMFY_PALETTE_MAP = {
    "Dark": "dark",
    "Light": "light",
    "Nord": "nord",
    "GitHub Dark": "github",
    "Catppuccin Mocha": "dark",
    "Dracula": "dark",
}


def sync_comfyui_color_palette(root_path, app_theme_name):
    """Переключает встроенную тему ComfyUI на ближайший аналог темы
    приложения, правя ComfyUI/user/default/comfy.settings.json (остальные
    ключи не трогаем). Нужно вызывать ДО запуска сервера — после старта
    ComfyUI сам монопольно владеет этим файлом и наши правки не увидит.
    """
    palette = COMFY_PALETTE_MAP.get(app_theme_name, "dark")
    settings_dir = os.path.join(root_path, "ComfyUI", "user", "default")
    settings_path = os.path.join(settings_dir, "comfy.settings.json")
    try:
        os.makedirs(settings_dir, exist_ok=True)
        data = {}
        if os.path.isfile(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["Comfy.ColorPalette"] = palette
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(
            "Тема ComfyUI синхронизирована: '%s' -> палитра '%s' (%s)",
            app_theme_name, palette, settings_path,
        )
    except Exception:
        log.exception("Не удалось синхронизировать тему ComfyUI (%s)", settings_path)


# --------------------------------------------------------------------------
# Управление процессом ComfyUI + потоковый разбор его вывода
# --------------------------------------------------------------------------

class ProcessLogBridge(QObject):
    """Мост из потока чтения stdout процесса в GUI-поток (Qt сам
    маршализует сигнал в основной поток, т.к. bridge создаётся там)."""

    line_received = Signal(str)
    # Отдельно от line_received: ComfyUI/tqdm перерисовывают прогресс-бар
    # через "\r" БЕЗ "\n" на каждый шаг, а readline() блокируется до
    # первого настоящего "\n" -- то есть весь бар может прийти одним
    # куском с кучей "\r" внутри. Здесь -- каждый такой кусок отдельно
    # (см. _LogReaderThread.run и ResourceMonitor.feed_log_line), чтобы
    # разобрать реальную скорость шага из строк вида
    # "74%|███████▍ | 26/35 [00:24<00:07, 1.21it/s]".
    progress_chunk_received = Signal(str)


class _LogReaderThread(threading.Thread):
    def __init__(self, stream, bridge: ProcessLogBridge, log_file_path):
        super().__init__(daemon=True)
        self.stream = stream
        self.bridge = bridge
        self.log_file_path = log_file_path

    def run(self):
        # ВАЖНО: читаем сырыми кусками (stream.read(N)), а НЕ построчно
        # (iter(readline, b"")) -- readline() блокируется, пока не
        # встретит настоящий "\n". Пока рядом печаталось что-то ещё со
        # своими "\n" (например периодические строки ComfyUI-Manager
        # "FETCH ComfyRegistry Data: N/164"), это давало нам частые
        # "проблески" и весь буфер с "\r"-тиками tqdm вовремя
        # вытеснялся наружу -- эффект был похож на то, что прогресс
        # обновляется вживую. Но как только рядом печатать перестаёт
        # что-либо ещё (например после того как Manager закончил свою
        # фоновую синхронизацию при старте), единственный настоящий "\n"
        # -- это конец самого прогресс-бара, и readline() просто ждёт
        # его, копя ВСЕ промежуточные "\r"-перерисовки во внутреннем
        # буфере -- они долетают до нас все разом только в момент,
        # когда бар уже закрылся (или рядом наконец что-то ещё
        # напечаталось). Со стороны выглядит как "прогресс не
        # обновляется до самого конца генерации".
        # read(N) на пайпе возвращает данные, как только они появились
        # (не ждёт заполнения N байт) -- а сам tqdm делает flush() после
        # каждой перерисовки, так что байты в пайпе действительно
        # появляются вживую, нам просто нужно их вовремя забирать.
        buf = b""
        try:
            with open(self.log_file_path, "w", encoding="utf-8", errors="ignore") as f:
                while True:
                    # read1(), а НЕ read() -- read() на BufferedReader
                    # может сделать НЕСКОЛЬКО системных чтений, пытаясь
                    # набрать полные 4096 байт, и в худшем случае снова
                    # подвиснет так же, как readline() ждал "\n".
                    # read1() гарантированно возвращает то, что уже
                    # пришло в пайп, максимум за одно системное чтение --
                    # именно то, что нужно для реального времени.
                    chunk = self.stream.read1(4096)
                    if not chunk:
                        break  # EOF -- процесс закрыл stdout
                    buf += chunk

                    while True:
                        idx_r = buf.find(b"\r")
                        idx_n = buf.find(b"\n")
                        if idx_r == -1 and idx_n == -1:
                            break
                        if idx_n != -1 and (idx_r == -1 or idx_n < idx_r):
                            idx, is_newline = idx_n, True
                            consumed = idx + 1
                        else:
                            idx = idx_r
                            consumed = idx + 1
                            # "\r\n" -- ОДНА граница обычной строки (не
                            # перерисовка бара) -- обязательно помечаем
                            # is_newline=True и здесь тоже, иначе такая
                            # строка уйдёт только в progress_chunk_received,
                            # а в файл лога/панель -- нет (именно так
                            # ломался лог: "\r\n" распознавался и склеивался
                            # правильно, но не как настоящий перевод строки).
                            if buf[idx + 1:idx + 2] == b"\n":
                                consumed += 1
                                is_newline = True
                            else:
                                is_newline = False

                        piece = buf[:idx]
                        buf = buf[consumed:]

                        text = piece.decode("utf-8", errors="ignore")
                        if text:
                            # КАЖДЫЙ кусок (включая промежуточные "\r"-
                            # перерисовки) -- для разбора ETA в реальном
                            # времени.
                            self.bridge.progress_chunk_received.emit(text)

                        if is_newline:
                            # В файл лога и в line_received (панель лога
                            # в UI), как и раньше, уходят только
                            # настоящие, полные строки -- не каждая
                            # промежуточная перерисовка бара.
                            f.write(text + "\n")
                            f.flush()
                            self.bridge.line_received.emit(text)

                # Если процесс закрыл stdout, а в буфере остался хвост
                # без завершающего "\n"/"\r" -- всё равно публикуем его
                # (иначе последняя строка перед закрытием терялась бы).
                if buf:
                    text = buf.decode("utf-8", errors="ignore")
                    if text:
                        self.bridge.progress_chunk_received.emit(text)
                    f.write(text + "\n")
                    f.flush()
                    self.bridge.line_received.emit(text)
        except Exception:
            log.exception("Ошибка чтения вывода процесса ComfyUI")


class ComfyProcess:
    """Запускает ComfyUI, читает его stdout/stderr в фоне и умеет
    корректно убить всё дерево процессов."""

    def __init__(self, root_path, launch_script_abs, bridge: ProcessLogBridge):
        self.root_path = root_path
        self.launch_script_abs = launch_script_abs
        self.bridge = bridge
        self.proc = None
        self._reader = None

    def start(self):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.info("Запуск ComfyUI: %s (cwd=%s)", self.launch_script_abs, self.root_path)
        self.proc = subprocess.Popen(
            ["cmd.exe", "/c", self.launch_script_abs],
            cwd=self.root_path,
            creationflags=creationflags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._reader = _LogReaderThread(self.proc.stdout, self.bridge, COMFY_LOG_PATH)
        self._reader.start()
        return self.proc

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.returncode if self.proc is not None else None

    def stop(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        log.info("Остановка ComfyUI (PID %s)", pid)
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


# --------------------------------------------------------------------------
# Ограниченная веб-страница: без внешних доменов и без новых окон
# --------------------------------------------------------------------------

class RestrictedWebPage(QWebEnginePage):
    """
    - Переход по ссылке на другой хост/порт (документация, GitHub и т.п.)
      отменяется и открывается в системном браузере.
    - Любая попытка открыть "новое окно" (window.open, target=_blank,
      Ctrl+клик) не создаёт нового окна: если итоговый адрес — тот же
      ComfyUI, страница просто переходит на него в этом же окне; если
      сторонний домен — уходит в системный браузер.
    """

    def __init__(self, profile, allowed_host, allowed_port, parent=None):
        super().__init__(profile, parent)
        self._allowed_host = allowed_host
        self._allowed_port = allowed_port

    def _is_external(self, url: QUrl) -> bool:
        return not (
            url.host() == self._allowed_host and url.port(80) == self._allowed_port
        )

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame and self._is_external(url):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _window_type):
        temp_page = QWebEnginePage(self.profile(), self)

        def handle(url):
            if self._is_external(url):
                QDesktopServices.openUrl(url)
            else:
                self.setUrl(url)
            temp_page.deleteLater()

        temp_page.urlChanged.connect(handle)
        return temp_page


# --------------------------------------------------------------------------
# Мониторинг ресурсов (CPU/RAM/GPU/температура/очередь ComfyUI)
# --------------------------------------------------------------------------

class ResourceMonitor(QObject):
    stats_updated = Signal(dict)

    def __init__(self, get_running_port_fn, parent=None):
        super().__init__(parent)
        self._get_running_port = get_running_port_fn
        self._nvml_ok = False
        self._gpu_handle = None
        self._warned_psutil = False
        self._warned_nvml = False

        # Счётчик "готово за сессию" (см. fetch_history_ids) — по set()
        # прочитанных id, а не по разнице длин: так надёжнее в двух
        # смыслах сразу -- (1) не зависит от того, в каком порядке
        # опрашиваются /queue и /history (это два отдельных, не атомарных
        # HTTP-запроса — см. _poll), (2) естественно защищает от
        # повторного счёта одного и того же id.
        # None -- сессия ещё не началась (порт ни разу не был обнаружен
        # запущенным); при первом успешном опросе туда попадёт то, что
        # уже было в /history ДО старта сессии, чтобы не засчитать это
        # как "сделано сейчас".
        self._session_seen_history_ids = None
        self._session_done_ids = set()

        # -- ETA всей очереди --
        # Вместо /ws (не удалось надёжно поймать формат сообщений --
        # см. историю правок) используем то, что и так уже печатает сам
        # ComfyUI в свой stdout при сэмплинге -- строку прогресс-бара
        # tqdm вида "74%|███████▍ | 26/35 [00:24<00:07, 1.21it/s]".
        # ResourceMonitor.feed_log_line читает её из ProcessLogBridge
        # (подключается в MainWindow.__init__ к
        # log_bridge.progress_chunk_received) -- это та же труба, из
        # которой лаунчер и так читает вывод процесса ComfyUI для лога,
        # отдельное сетевое соединение не нужно.
        # Даёт актуальный прогресс только для ОДНОГО, сейчас считающего
        # шаги сэмплера (это же ограничение и у самого tqdm в консоли) --
        # для остальных заданий в очереди (pending) объём по-прежнему
        # берётся из графовой эвристики count_steps_in_prompt().
        self._current_progress = None  # {"done": int, "total": int}
        # prompt_id, к которому ОТНОСИТСЯ self._current_progress (лучшее
        # предположение -- см. _poll: единственный running_id на момент
        # последнего обновления). Строки tqdm не содержат prompt_id, так
        # что это единственный способ понять, что задание сменилось и
        # старые "done"/"total" от предыдущего задания больше не
        # актуальны -- без этого, пока новое задание грузит модель и
        # ещё не напечатало ни одной своей строки, использовались бы
        # цифры от УЖЕ ЗАКОНЧИВШЕГОСЯ предыдущего задания (например
        # done=39 при total=39 -- "уже готово"), что и давало ложные
        # "< 1 с" на самом деле только начавшихся заданиях.
        self._progress_for_id = None
        self._avg_sec_per_step = None
        # Диагностика по КАЖДОМУ заданию отдельно (не только по первому
        # за сессию) -- id заданий, для которых уже залогировали и смену,
        # и первую пойманную строку прогресса. Нужно, чтобы видеть в
        # логе, что происходит именно со 2-м/3-м заданием в очереди, а
        # не только с самым первым.
        self._logged_switch_for_ids = set()
        self._logged_progress_for_ids = set()
        # Диагностика: если очередь активна (что-то running), а от
        # feed_log_line за это время не пришло ни одной строки прогресса
        # -- один раз предупреждаем в лог (не на каждый опрос, чтобы не
        # спамить). Помогает отличить "строки вообще не доходят из
        # _LogReaderThread" от "просто ещё не было ни одного тика".
        self._stall_polls = 0
        self._logged_stall_warning_for_ids = set()

        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._nvml_ok = True
                log.info("NVML инициализирован, GPU-метрики доступны")
            except Exception as e:
                log.warning("NVML недоступен (нет NVIDIA GPU или драйвера?): %s", e)
        else:
            log.warning("Модуль pynvml не установлен — метрики GPU будут недоступны")

        if psutil is not None:
            psutil.cpu_percent(interval=None)  # первый вызов всегда возвращает 0.0
        else:
            log.warning("Модуль psutil не установлен — метрики CPU/RAM будут недоступны")

        self._timer = QTimer(self)
        self._timer.setInterval(RESOURCE_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self):
        self._timer.start()
        self._poll()

    def stop(self):
        self._timer.stop()

    # -- прогресс сэмплера из stdout ComfyUI (см. комментарий в __init__) --

    def feed_log_line(self, line: str):
        """Разбирает строку прогресс-бара tqdm из вывода ComfyUI (см.
        _TQDM_PROGRESS_RE) -- обновляет self._current_progress (для
        оставшихся шагов ТЕКУЩЕГО задания) и self._avg_sec_per_step (по
        rate, который tqdm и так сам считает и сглаживает -- поэтому
        здесь без дополнительного EMA, значение просто заменяется на
        последнее известное)."""
        clean = _ANSI_ESCAPE_RE.sub("", line)
        m = _TQDM_PROGRESS_RE.search(clean)
        if not m:
            return
        try:
            cur = int(m.group("cur"))
            total = int(m.group("total"))
            rate = float(m.group("rate"))
        except (TypeError, ValueError):
            return
        if total <= 0 or rate <= 0:
            return

        self._current_progress = {"done": cur, "total": total}
        sec_per_step = rate if m.group("unit") == "s/it" else (1.0 / rate)
        self._avg_sec_per_step = sec_per_step

        job_key = self._progress_for_id or "?"
        if job_key not in self._logged_progress_for_ids:
            # Подтверждение в лог для КАЖДОГО задания отдельно -- чтобы
            # было видно, доходит ли реальный прогресс до каждого из них
            # по очереди, а не только до первого/последнего.
            log.info(
                "ETA очереди: поймана первая строка прогресса для %s (%s/%s, %.2f%s)",
                job_key, cur, total, rate, m.group("unit"),
            )
            self._logged_progress_for_ids.add(job_key)

    def _compute_eta_seconds(self, step_totals, running_ids):
        """0.0 -- в очереди реально ничего нет. None -- в очереди что-то
        есть, но посчитать нельзя: либо объём хотя бы одного задания
        неизвестен (эвристика по графу не нашла "steps", а строка
        прогресса ещё не пришла), либо скорость шага ещё не замерена
        (ни одной строки прогресса не было с момента запуска ComfyUI)."""
        if not step_totals:
            return 0.0

        # tqdm в консоли ComfyUI не сообщает prompt_id -- только текущий
        # прогресс ОДНОГО считающего сэмплера. Если сейчас выполняется
        # ровно одно задание, однозначно относим self._current_progress
        # к нему; если их несколько (мультиGPU) или ни одного -- не
        # рискуем угадать какое, используем только графовую эвристику.
        progress = self._current_progress if len(running_ids) == 1 else None
        progress_pid = next(iter(running_ids)) if progress else None

        # Объём (total) для каждого задания -- либо реальный (граф или
        # tqdm), либо, если оба не дали ничего, None ("не знаем").
        totals = {}
        for prompt_id, graph_total in step_totals.items():
            if prompt_id == progress_pid:
                total = max(graph_total, progress["total"])
            else:
                total = graph_total
            totals[prompt_id] = total if total > 0 else None

        # Раньше ХОТЯ БЫ ОДНО задание с неизвестным объёмом обнуляло
        # оценку ЦЕЛИКОМ -- даже если у остальных (например у ТЕКУЩЕГО,
        # уже идущего) объём отлично известен из tqdm. На практике это
        # почти всегда било по ожидающим заданиям (эвристика по графу не
        # находит "steps" для части воркфлоу), из-за чего ETA
        # переставал считаться, как только в очереди появлялось хоть
        # одно ожидающее задание -- независимо от того, насколько точно
        # известен прогресс уже выполняющегося. Вместо этого: заданиям с
        # неизвестным объёмом подставляем СРЕДНИЙ объём остальных
        # заданий этой же очереди, у которых объём известен (в рамках
        # одной "серии" генераций объёмы обычно похожи) -- это оценка, а
        # не точное число, но оно куда полезнее, чем внезапное "не
        # знаю" из-за одного-единственного неопределённого задания.
        known = [t for t in totals.values() if t is not None]
        fallback_total = round(sum(known) / len(known)) if known else None

        remaining = 0
        for prompt_id, total in totals.items():
            if total is None:
                if fallback_total is None:
                    # Совсем без ориентиров (обычно только самое первое
                    # задание сессии, ещё до первой строки прогресса) --
                    # тут уже честно "не знаем" для всей очереди.
                    return None
                total = fallback_total
            done = (
                progress["done"] if prompt_id == progress_pid else 0
            )
            remaining += max(total - done, 0)

        if remaining <= 0:
            return 0.0
        if self._avg_sec_per_step is None:
            return None
        return remaining * self._avg_sec_per_step

    def _poll(self):
        stats = {}

        if psutil is not None:
            try:
                stats["cpu_percent"] = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                stats["ram_percent"] = vm.percent
                stats["ram_used_gb"] = vm.used / (1024 ** 3)
                stats["ram_total_gb"] = vm.total / (1024 ** 3)
            except Exception:
                if not self._warned_psutil:
                    log.exception("Ошибка чтения метрик CPU/RAM")
                    self._warned_psutil = True

        if self._nvml_ok:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                temp = pynvml.nvmlDeviceGetTemperature(
                    self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )
                name = pynvml.nvmlDeviceGetName(self._gpu_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "ignore")
                stats["gpu_available"] = True
                stats["gpu_name"] = name
                stats["gpu_util"] = util.gpu
                stats["gpu_mem_used_gb"] = mem.used / (1024 ** 3)
                stats["gpu_mem_total_gb"] = mem.total / (1024 ** 3)
                stats["gpu_temp"] = temp
            except Exception:
                if not self._warned_nvml:
                    log.exception("Ошибка чтения метрик GPU")
                    self._warned_nvml = True
                stats["gpu_available"] = False
        else:
            stats["gpu_available"] = False

        port = self._get_running_port()
        if port:
            queue_info = fetch_queue_status(port)
            if queue_info is not None:
                running = queue_info["running"]
                pending = queue_info["pending"]
                running_ids = queue_info["running_ids"]
                step_totals = queue_info["step_totals"]
                stats["queue_running"] = running
                stats["queue_pending"] = pending

                history_ids = fetch_history_ids(port)
                if history_ids is not None:
                    if self._session_seen_history_ids is None:
                        # Первый успешный опрос за это включение ComfyUI --
                        # запоминаем то, что уже есть в /history, как "не
                        # наше", иначе в счётчик попало бы то, что было
                        # сделано ДО запуска лаунчера/в прошлые сессии.
                        self._session_seen_history_ids = set(history_ids)
                    # "Готово за сессию" = то, чего не было в /history на
                    # старте сессии, И чего СЕЙЧАС нет в running_ids по
                    # данным /queue из ЭТОГО ЖЕ опроса. Второе условие --
                    # защита от гонки: /queue и /history это два отдельных
                    # HTTP-запроса, не единый снимок состояния, и
                    # генерация может успеть попасть в /history в
                    # промежутке между ними, оставаясь в это же мгновение
                    # ещё "running" по /queue -- без этой проверки она на
                    # секунду засчитывалась готовой, хотя прогресс-бар
                    # всё ещё показывал её выполняющейся. Просто
                    # отложится до следующего опроса (2 сек), когда она
                    # уже точно пропадёт из running_ids.
                    newly_done = (
                        history_ids - self._session_seen_history_ids
                    ) - running_ids
                    self._session_done_ids |= newly_done
                stats["queue_completed_session"] = len(self._session_done_ids)

                # Сменилось ли ЗАДАНИЕ, которое сейчас единственное
                # выполняется? Если да -- self._current_progress (если
                # там что-то есть) относится к УЖЕ не тому заданию,
                # обнуляем -- иначе первые секунды нового задания (пока
                # оно ещё грузит модель и не напечатало ни одной своей
                # строки прогресса) считались бы по остаточным цифрам от
                # предыдущего, уже готового задания (см. комментарий у
                # self._progress_for_id в __init__).
                current_single_id = (
                    next(iter(running_ids)) if len(running_ids) == 1 else None
                )
                if current_single_id != self._progress_for_id:
                    if current_single_id not in self._logged_switch_for_ids:
                        log.info(
                            "ETA очереди: активное задание сменилось %s -> %s "
                            "(running_ids=%s), сбрасываю накопленный прогресс",
                            self._progress_for_id, current_single_id, running_ids,
                        )
                        if current_single_id is not None:
                            self._logged_switch_for_ids.add(current_single_id)
                    self._current_progress = None
                    self._progress_for_id = current_single_id

                # ETA -- см. _compute_eta_seconds/feed_log_line.
                stats["queue_eta_seconds"] = self._compute_eta_seconds(
                    step_totals, running_ids
                )

                if running_ids and self._current_progress is None:
                    # ВАЖНО: проверяем именно self._current_progress (для
                    # ТЕКУЩЕГО задания), а не self._avg_sec_per_step --
                    # последнее, однажды установившись на первом задании,
                    # больше не сбрасывается между заданиями, и проверка
                    # по нему замаскировала бы точно такое же зависание
                    # на 2-м/3-м задании.
                    self._stall_polls += 1
                    if (
                        self._stall_polls >= 5
                        and current_single_id is not None
                        and current_single_id not in self._logged_stall_warning_for_ids
                    ):
                        # ~10 секунд (5 опросов по 2 сек) активной очереди,
                        # а от feed_log_line не пришло ни одной строки
                        # прогресса ДЛЯ ЭТОГО задания -- значит строки из
                        # _LogReaderThread либо не доходят до
                        # progress_chunk_received, либо не совпадают с
                        # _TQDM_PROGRESS_RE. Дальше искать нужно уже по
                        # этому логу, а не гадать.
                        log.warning(
                            "ETA очереди: задание %s идёт уже %d опросов "
                            "подряд, но feed_log_line ни разу не распознал "
                            "для него строку прогресса -- ETA останется "
                            "'оценка...' (см. COMFY_LOG_PATH -- доходят ли "
                            "туда вообще строки вида 'N/M [...it/s]')",
                            current_single_id, self._stall_polls,
                        )
                        self._logged_stall_warning_for_ids.add(current_single_id)
                else:
                    self._stall_polls = 0
        else:
            # ComfyUI не запущен -- сбрасываем сессию и ETA-состояние,
            # чтобы при следующем запуске счёт начался заново с нуля, а не
            # продолжал считать от старого /history (это уже мог быть
            # другой процесс ComfyUI с чистым журналом) и от скорости шага,
            # замеренной в прошлый раз (могла быть другая модель/разрешение).
            self._session_seen_history_ids = None
            self._session_done_ids = set()
            self._current_progress = None
            self._progress_for_id = None
            self._avg_sec_per_step = None
            self._logged_switch_for_ids = set()
            self._logged_progress_for_ids = set()
            self._stall_polls = 0
            self._logged_stall_warning_for_ids = set()

        self.stats_updated.emit(stats)


def format_eta_seconds(seconds, tr=None) -> str:
    """"~2 мин 30 с" / "~45 с" / "оценка..." (скорость шага ещё не
    замерена) -- используется и в чипе очереди, и в подсказке трея.

    tr -- необязательная функция перевода (обычно self._tr / loc.tr);
    если не передана, строки остаются на русском (поведение по
    умолчанию, как раньше)."""
    if tr is None:
        tr = lambda text: text
    if seconds is None:
        return tr("оценка...")
    if seconds < 1:
        return tr("< 1 с")
    m, s = divmod(int(round(seconds)), 60)
    return f"~{m} {tr('мин')} {s} {tr('с')}" if m > 0 else f"~{s} {tr('с')}"


def format_stats_tooltip(stats: dict, tr=None) -> str:
    """Компактная подсказка для трея.

    Важно: Windows обрезает текст всплывающей подсказки трея примерно
    на 128 символах без предупреждения (именно так "срезалась" строка
    с очередью). Поэтому здесь без заголовка, без лишних слов и с
    жёсткой подстраховкой по длине.

    tr -- необязательная функция перевода, см. format_eta_seconds().
    """
    if tr is None:
        tr = lambda text: text
    lines = []
    if "cpu_percent" in stats:
        lines.append(f"CPU {stats['cpu_percent']:.0f}%")
    if "ram_percent" in stats:
        gb = tr("ГБ")
        lines.append(
            f"RAM {stats['ram_percent']:.0f}% "
            f"({stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} {gb})"
        )
    if stats.get("gpu_available"):
        lines.append(f"GPU {stats['gpu_util']}% {stats['gpu_temp']}°C")
        gb = tr("ГБ")
        lines.append(
            f"VRAM {stats['gpu_mem_used_gb']:.1f}/{stats['gpu_mem_total_gb']:.1f} {gb}"
        )
    if "queue_pending" in stats:
        running, pending = stats["queue_running"], stats["queue_pending"]
        line = f"{tr('Очередь')} {running}/{pending}"
        if "queue_completed_session" in stats:
            line += f" · {tr('Готово')} {stats['queue_completed_session']}"
        if (running + pending) > 0 and "queue_eta_seconds" in stats:
            line += f" · ETA {format_eta_seconds(stats['queue_eta_seconds'], tr=tr)}"
        lines.append(line)
    else:
        lines.append(tr("ComfyUI не запущен"))

    text = "\n".join(lines) if lines else APP_NAME
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def level_color(value, warn=60, crit=85):
    """Цвет по уровню нагрузки/температуры: зелёный -> жёлтый -> красный."""
    if value is None:
        return "#5b6472"
    if value >= crit:
        return "#d9534f"
    if value >= warn:
        return "#d98c2b"
    return "#3fae4f"


NEUTRAL_CHIP_COLOR = "#5b6472"
QUEUE_ACTIVE_COLOR = "#3a7ecf"
QUEUE_IDLE_COLOR = "#3fae4f"


class ResourceBar(QWidget):
    """Цветные "чипы" CPU/RAM/GPU/VRAM/очередь — цвет отражает уровень
    нагрузки (зелёный/жёлтый/красный), а не тему оформления, чтобы это
    было заметно на любой теме."""

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._last_stats = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.cpu_chip = self._make_chip()
        self.ram_chip = self._make_chip()
        self.gpu_chip = self._make_chip()
        self.vram_chip = self._make_chip()
        self.queue_chip = self._make_chip()

        for chip in (
            self.cpu_chip,
            self.ram_chip,
            self.gpu_chip,
            self.vram_chip,
            self.queue_chip,
        ):
            layout.addWidget(chip)
        layout.addStretch(1)

        self.update_stats({})

    def _make_chip(self):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        self._style_chip(lbl, NEUTRAL_CHIP_COLOR)
        return lbl

    @staticmethod
    def _style_chip(label, color):
        label.setStyleSheet(
            f"QLabel {{ background-color: {color}; color: #ffffff; "
            "border-radius: 9px; padding: 3px 10px; font-weight: 600; }"
        )

    def update_stats(self, stats: dict):
        self._last_stats = stats
        if "cpu_percent" in stats:
            v = stats["cpu_percent"]
            self.cpu_chip.setText(f"CPU {v:.0f}%")
            self._style_chip(self.cpu_chip, level_color(v))
        else:
            self.cpu_chip.setText(f"CPU: {self._tr('н/д')}")
            self._style_chip(self.cpu_chip, NEUTRAL_CHIP_COLOR)

        if "ram_percent" in stats:
            v = stats["ram_percent"]
            gb = self._tr("ГБ")
            self.ram_chip.setText(
                f"RAM {v:.0f}% ({stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} {gb})"
            )
            self._style_chip(self.ram_chip, level_color(v))
        else:
            self.ram_chip.setText(f"RAM: {self._tr('н/д')}")
            self._style_chip(self.ram_chip, NEUTRAL_CHIP_COLOR)

        if stats.get("gpu_available"):
            self.gpu_chip.setText(f"GPU {stats['gpu_util']}%")
            self._style_chip(self.gpu_chip, level_color(stats["gpu_util"]))
            gb = self._tr("ГБ")
            self.vram_chip.setText(
                f"{stats['gpu_temp']}°C · VRAM "
                f"{stats['gpu_mem_used_gb']:.1f}/{stats['gpu_mem_total_gb']:.1f} {gb}"
            )
            self._style_chip(
                self.vram_chip, level_color(stats["gpu_temp"], warn=70, crit=84)
            )
        else:
            self.gpu_chip.setText(f"GPU: {self._tr('н/д')}")
            self._style_chip(self.gpu_chip, NEUTRAL_CHIP_COLOR)
            self.vram_chip.setText(f"VRAM: {self._tr('н/д')}")
            self._style_chip(self.vram_chip, NEUTRAL_CHIP_COLOR)

        if "queue_pending" in stats:
            running, pending = stats["queue_running"], stats["queue_pending"]
            text = f"{self._tr('Очередь')} {running}/{pending}"
            if "queue_completed_session" in stats:
                text += f" · {self._tr('Готово')} {stats['queue_completed_session']}"
            active = (running + pending) > 0
            if active and "queue_eta_seconds" in stats:
                eta = format_eta_seconds(stats['queue_eta_seconds'], tr=self._tr)
                text += f" · ETA {eta}"
            self.queue_chip.setText(text)
            self._style_chip(
                self.queue_chip, QUEUE_ACTIVE_COLOR if active else QUEUE_IDLE_COLOR
            )
        else:
            self.queue_chip.setText(self._tr("ComfyUI не запущен"))
            self._style_chip(self.queue_chip, NEUTRAL_CHIP_COLOR)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.update_stats(self._last_stats)


# --------------------------------------------------------------------------
# Небольшая панель лога, переиспользуемая на странице настроек
# --------------------------------------------------------------------------

class LogPanel(QGroupBox):
    def __init__(self, loc=None, title="Лог последнего запуска ComfyUI", parent=None):
        self.loc = loc
        self._title_ru = title
        super().__init__(self._tr(title), parent)
        layout = QVBoxLayout(self)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LOG_PANEL_LINES)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.text.setFont(mono)
        # Цвета берём из применённой темы (QWidget-правило в *.qss), а не
        # захардкоженный тёмный терминал — иначе на светлой теме лог
        # оставался тёмным пятном посреди светлого интерфейса.
        layout.addWidget(self.text)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.open_folder_btn = QPushButton(self._tr("Открыть папку с логами"))
        self.open_folder_btn.clicked.connect(self._open_log_folder)
        btn_row.addWidget(self.open_folder_btn)
        self.clear_btn = QPushButton(self._tr("Очистить"))
        self.clear_btn.clicked.connect(self.text.clear)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.setTitle(self._tr(self._title_ru))
        self.open_folder_btn.setText(self._tr("Открыть папку с логами"))
        self.clear_btn.setText(self._tr("Очистить"))

    def append_line(self, line):
        self.text.appendPlainText(line)

    def _open_log_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(APP_DIR))


# --------------------------------------------------------------------------
# Диалог "Аргументы запуска ComfyUI" — раньше скрипт запуска/порт/чекбокс
# браузера жили прямо на экране настроек; теперь это отдельное окно,
# чтобы не загромождать главный экран, и в нём же живут доп. флаги
# командной строки ComfyUI (см. LAUNCH_ARG_DEFS выше) — каждый как
# чекбокс с описанием, что он делает, и полем для значения там, где оно
# нужно.
# --------------------------------------------------------------------------

class LaunchArgsDialog(QDialog):
    def __init__(self, cfg, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self.setWindowTitle(self._tr("Аргументы запуска ComfyUI"))
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.script_combo = QComboBox()
        self.script_row_label = QLabel(self._tr("Скрипт запуска:"))
        form.addRow(self.script_row_label, self.script_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(cfg.get("port", 8188)))
        self.port_row_label = QLabel(self._tr("Порт:"))
        form.addRow(self.port_row_label, self.port_spin)

        self.disable_auto_launch_check = QCheckBox(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.disable_auto_launch_check.setChecked(cfg.get("disable_auto_launch", True))
        form.addRow(self.disable_auto_launch_check)

        outer.addLayout(form)

        self.args_heading = self._heading(self._tr("Дополнительные аргументы командной строки"))
        outer.addWidget(self.args_heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        args_container = QWidget()
        args_layout = QVBoxLayout(args_container)
        scroll.setWidget(args_container)
        outer.addWidget(scroll, 1)

        saved_args = cfg.get("launch_args", {})
        # id -> {"check": QCheckBox, "desc": QLabel, "value": QLineEdit|None}
        self.arg_widgets = {}
        for d in LAUNCH_ARG_DEFS:
            row_box = QGroupBox()
            row_layout = QVBoxLayout(row_box)
            check = QCheckBox(d["flag"])
            saved_entry = saved_args.get(d["id"], {})
            check.setChecked(bool(saved_entry.get("enabled")))
            row_layout.addWidget(check)

            desc = QLabel(self._tr(d["desc_ru"]))
            desc.setObjectName("mutedLabel")
            desc.setWordWrap(True)
            row_layout.addWidget(desc)

            value_edit = None
            if d.get("takes_value"):
                value_edit = QLineEdit(str(saved_entry.get("value", "")))
                value_edit.setPlaceholderText(d.get("placeholder", ""))
                row_layout.addWidget(value_edit)

            args_layout.addWidget(row_box)
            self.arg_widgets[d["id"]] = {"check": check, "desc": desc, "value": value_edit,
                                          "box": row_box}

        args_layout.addStretch(1)

        self.close_btn = QPushButton(self._tr("Закрыть"))
        self.close_btn.clicked.connect(self.close)
        outer.addWidget(self.close_btn)

    def _heading(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("headingLabel")
        return lbl

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def collect_launch_args(self):
        """Возвращает cfg["launch_args"] — {id: {"enabled": bool, "value": str}}."""
        result = {}
        for arg_id, widgets in self.arg_widgets.items():
            value_edit = widgets["value"]
            result[arg_id] = {
                "enabled": widgets["check"].isChecked(),
                "value": value_edit.text().strip() if value_edit is not None else "",
            }
        return result

    def set_extra_widgets_enabled(self, enabled):
        """Блокирует именно доп.-аргументные чекбоксы/поля (скрипт/порт/
        браузер блокируются отдельно, там же, где раньше — см.
        SettingsPage.set_server_running/_set_launch_controls_enabled)."""
        for widgets in self.arg_widgets.values():
            widgets["check"].setEnabled(enabled)
            if widgets["value"] is not None:
                widgets["value"].setEnabled(enabled)

    def retranslate_ui(self):
        self.setWindowTitle(self._tr("Аргументы запуска ComfyUI"))
        self.script_row_label.setText(self._tr("Скрипт запуска:"))
        self.port_row_label.setText(self._tr("Порт:"))
        self.disable_auto_launch_check.setText(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.args_heading.setText(self._tr("Дополнительные аргументы командной строки"))
        self.close_btn.setText(self._tr("Закрыть"))
        for d in LAUNCH_ARG_DEFS:
            self.arg_widgets[d["id"]]["desc"].setText(self._tr(d["desc_ru"]))
            value_edit = self.arg_widgets[d["id"]]["value"]
            if value_edit is not None:
                value_edit.setPlaceholderText(d.get("placeholder", ""))


# --------------------------------------------------------------------------
# Страница настроек
# --------------------------------------------------------------------------

class SettingsPage(QWidget):
    launch_requested = Signal(dict)
    open_running_requested = Signal()
    stop_requested = Signal()
    cancel_requested = Signal()
    language_changed = Signal(str)

    AUTOSAVE_DEBOUNCE_MS = 400

    def __init__(self, cfg, theme_manager: ThemeManager, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme_manager = theme_manager
        self.loc = loc
        self._loading_fields = False
        # Держит живые ссылки на окна остальных инструментов комплекта,
        # открытые В ЭТОМ ЖЕ процессе (см. IN_PROCESS_WINDOW_FACTORIES) --
        # без этого объект окна был бы собран сборщиком мусора Python сразу
        # после выхода из _launch_external() и окно бы тут же закрылось.
        #
        # ВАЖНО: раньше запись из этого словаря никогда не удалялась --
        # окно (а с ним и всё, что оно загрузило в память, для PromptVault
        # это модели torch/transformers) жило до самого закрытия ВСЕГО
        # приложения, даже если пользователь закрывал только окно
        # инструмента крестиком. См. _open_in_process_window() ниже --
        # там теперь окно реально уничтожается по закрытию и запись
        # удаляется из кэша, а не просто "скрывается" навсегда.
        self._child_windows = {}

        root = QVBoxLayout(self)

        self.resource_bar = ResourceBar(loc=self.loc)
        root.addWidget(self.resource_bar)

        # Полоса "ComfyUI уже запущен" — видна только пока процесс жив,
        # и именно тут разведены по смыслу кнопки "Настройки" и "Стоп":
        # эта страница сама по себе больше не останавливает сервер.
        self.running_bar = QWidget()
        running_row = QHBoxLayout(self.running_bar)
        running_row.setContentsMargins(0, 0, 0, 0)
        self.running_label = QLabel(self._tr("ComfyUI уже запущен"))
        self.running_label.setStyleSheet("color: #6fbf73; font-weight: bold;")
        running_row.addWidget(self.running_label)
        running_row.addStretch(1)
        self.open_running_btn = QPushButton(self._tr("Открыть ComfyUI"))
        self.open_running_btn.clicked.connect(self.open_running_requested.emit)
        running_row.addWidget(self.open_running_btn)
        self.stop_running_btn = QPushButton(self._tr("Остановить"))
        self.stop_running_btn.clicked.connect(self.stop_requested.emit)
        running_row.addWidget(self.stop_running_btn)
        root.addWidget(self.running_bar)
        self.running_bar.setVisible(False)

        form = QFormLayout()
        form.setSpacing(10)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(theme_manager.available_themes())
        self.theme_combo.setCurrentText(theme_manager.current_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_manager.theme_changed_externally.connect(self._on_theme_changed_externally)
        self.theme_row_label = QLabel(self._tr("Тема оформления:"))
        form.addRow(self.theme_row_label, self.theme_combo)

        # Язык — общий на весь комплект (см. i18n.py / shared_language.py):
        # переключатель есть только здесь, PromptConfigEditor и PromptVault
        # только применяют выбор, сделанный тут.
        self.language_combo = QComboBox()
        if self.loc is not None:
            self.language_combo.addItems(self.loc.available_languages())
            self._sync_language_combo_display()
            self.language_combo.currentTextChanged.connect(self._on_language_changed)
            self.loc.language_changed_externally.connect(self._on_language_changed_externally)
        self.language_row_label = QLabel(self._tr("Язык интерфейса:"))
        form.addRow(self.language_row_label, self.language_combo)

        self.sync_comfy_theme_check = QCheckBox(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
                "темой приложения — вживую, пока ComfyUI уже открыт, и при "
                "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
                "цветовая система узлов."
            )
        )
        self.sync_comfy_theme_check.setChecked(cfg.get("sync_comfy_theme", False))
        self.sync_comfy_theme_check.stateChanged.connect(self._schedule_autosave)
        form.addRow(self.sync_comfy_theme_check)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(cfg.get("root_path", ""))
        self.path_edit.editingFinished.connect(self._refresh_scripts)
        self.path_edit.textChanged.connect(self._schedule_autosave)
        self.browse_btn = QPushButton(self._tr("Обзор..."))
        self.browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
        self.path_row_label = QLabel(self._tr("Папка ComfyUI_windows_portable:"))
        form.addRow(self.path_row_label, path_row)

        self.launch_args_dialog = LaunchArgsDialog(cfg, loc=self.loc, parent=self)
        # Скрипт запуска/порт/чекбокс браузера физически живут в диалоге
        # (LaunchArgsDialog), но остальной код этой страницы (автосохранение,
        # запуск, включение/выключение полей во время работы сервера)
        # по-прежнему обращается к ним как self.script_combo/self.port_spin/
        # self.disable_auto_launch_check — так с этим кодом ничего больше
        # менять не пришлось.
        self.script_combo = self.launch_args_dialog.script_combo
        self.port_spin = self.launch_args_dialog.port_spin
        self.disable_auto_launch_check = self.launch_args_dialog.disable_auto_launch_check
        self.script_combo.currentIndexChanged.connect(self._schedule_autosave)
        self.port_spin.valueChanged.connect(self._schedule_autosave)
        self.disable_auto_launch_check.stateChanged.connect(self._schedule_autosave)
        for widgets in self.launch_args_dialog.arg_widgets.values():
            widgets["check"].stateChanged.connect(self._schedule_autosave)
            if widgets["value"] is not None:
                widgets["value"].textChanged.connect(self._schedule_autosave)

        self.launch_args_btn = QPushButton(self._tr("Аргументы запуска ComfyUI..."))
        self.launch_args_btn.clicked.connect(self.launch_args_dialog.exec)
        form.addRow(self.launch_args_btn)

        root.addLayout(form)

        # -- Другие инструменты комплекта: запускаются как отдельные,
        # независимые процессы (см. ExternalApp/launch_external_app выше).
        # Путь к ним не настраивается — оба поставляются в одном архиве
        # с лаунчером, в фиксированной подпапке tools/.
        self.tools_box = QGroupBox(self._tr("Другие инструменты"))
        tools_layout = QVBoxLayout(self.tools_box)
        self.external_status_labels = {}
        self.external_launch_btns = {}
        for app in EXTERNAL_APPS:
            row = QHBoxLayout()
            row.addWidget(QLabel(app.label))
            row.addStretch(1)

            launch_btn = QPushButton(self._tr("Запустить"))
            launch_btn.clicked.connect(
                lambda _checked=False, a=app: self._launch_external(a)
            )
            row.addWidget(launch_btn)
            self.external_launch_btns[app.subdir] = launch_btn

            tools_layout.addLayout(row)

            status_label = QLabel("")
            status_label.setWordWrap(True)
            self.external_status_labels[app.subdir] = status_label
            tools_layout.addWidget(status_label)

        root.addWidget(self.tools_box)
        self._refresh_external_status()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #d9534f;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.log_panel = LogPanel(loc)
        self.log_panel.setMinimumHeight(160)
        root.addWidget(self.log_panel, 1)

        # Полоса прогресса запуска — вместо отдельного экрана "Ожидание".
        # Остаёмся на экране настроек, чтобы был виден живой лог сверху.
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_status_label = QLabel("")
        progress_layout.addWidget(self.progress_status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(200)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addStretch(1)
        self.cancel_launch_btn = QPushButton(self._tr("Отмена"))
        self.cancel_launch_btn.clicked.connect(self.cancel_requested.emit)
        progress_layout.addWidget(self.cancel_launch_btn)
        root.addWidget(self.progress_row)
        self.progress_row.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.launch_btn = QPushButton(self._tr("Запустить"))
        self.launch_btn.setDefault(True)
        self.launch_btn.clicked.connect(self._on_launch)
        btn_row.addWidget(self.launch_btn)
        root.addLayout(btn_row)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.AUTOSAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._auto_save)

        self._refresh_scripts()

    # -- состояние "сервер уже запущен" --------------------------------

    def set_server_running(self, running: bool, port=None):
        self.running_bar.setVisible(running)
        if running and port:
            self.running_label.setText(f"ComfyUI уже запущен на порту {port}")
        for w in (self.path_edit, self.script_combo, self.port_spin,
                  self.disable_auto_launch_check, self.sync_comfy_theme_check,
                  self.launch_btn, self.launch_args_btn):
            w.setEnabled(not running)
        self.launch_args_dialog.set_extra_widgets_enabled(not running)

    # -- прогресс запуска (вместо отдельной страницы) --------------------

    def show_launch_progress(self, text):
        self.progress_status_label.setText(text)
        self.progress_row.setVisible(True)
        self._set_launch_controls_enabled(False)

    def update_launch_progress(self, text):
        self.progress_status_label.setText(text)

    def hide_launch_progress(self):
        self.progress_row.setVisible(False)
        self._set_launch_controls_enabled(True)

    def _set_launch_controls_enabled(self, enabled):
        for w in (self.path_edit, self.script_combo, self.port_spin,
                  self.disable_auto_launch_check, self.sync_comfy_theme_check,
                  self.launch_btn, self.launch_args_btn):
            w.setEnabled(enabled)
        self.launch_args_dialog.set_extra_widgets_enabled(enabled)

    # -- автосохранение --------------------------------------------------

    def _schedule_autosave(self, *_args):
        if self._loading_fields:
            return
        self._save_timer.start()

    def _auto_save(self):
        cfg = dict(self.cfg)
        cfg.update(
            {
                "root_path": self.path_edit.text().strip(),
                "script": self.script_combo.currentText(),
                "port": self.port_spin.value(),
                "disable_auto_launch": self.disable_auto_launch_check.isChecked(),
                "sync_comfy_theme": self.sync_comfy_theme_check.isChecked(),
                "launch_args": self.launch_args_dialog.collect_launch_args(),
            }
        )
        self.cfg = cfg
        save_config(cfg)
        log.debug("Настройки автосохранены")

    # -- прочее ------------------------------------------------------

    def set_status(self, text):
        self.status_label.setText(text)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _sync_language_combo_display(self):
        from i18n import AVAILABLE_LANGUAGES

        code = self.loc.current_language()
        display = next((n for n, c in AVAILABLE_LANGUAGES.items() if c == code), None)
        if display is not None:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentText(display)
            self.language_combo.blockSignals(False)

    def _on_language_changed(self, display_name):
        from i18n import AVAILABLE_LANGUAGES

        code = AVAILABLE_LANGUAGES.get(display_name)
        if code is None:
            return
        self.loc.apply_language(code)
        self.retranslate_ui()
        self.language_changed.emit(code)

    def _on_language_changed_externally(self, _code):
        """Язык поменялся в PromptConfigEditor или PromptVault, пока
        лаунчер уже открыт (тут это теоретическая возможность — обычно
        переключатель есть только тут — но на всякий случай тоже
        подхватываем и обновляем видимые тексты)."""
        self._sync_language_combo_display()
        self.retranslate_ui()

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты этой страницы (и её
        LogPanel) после смены языка — сам по себе выбор языка не
        обновляет текст уже созданных виджетов."""
        self.running_label.setText(self._tr("ComfyUI уже запущен"))
        self.open_running_btn.setText(self._tr("Открыть ComfyUI"))
        self.stop_running_btn.setText(self._tr("Остановить"))
        self.theme_row_label.setText(self._tr("Тема оформления:"))
        self.language_row_label.setText(self._tr("Язык интерфейса:"))
        self.sync_comfy_theme_check.setText(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
                "темой приложения — вживую, пока ComfyUI уже открыт, и при "
                "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
                "цветовая система узлов."
            )
        )
        self.browse_btn.setText(self._tr("Обзор..."))
        self.path_row_label.setText(self._tr("Папка ComfyUI_windows_portable:"))
        self.launch_args_btn.setText(self._tr("Аргументы запуска ComfyUI..."))
        self.launch_args_dialog.retranslate_ui()
        self.tools_box.setTitle(self._tr("Другие инструменты"))
        for btn in self.external_launch_btns.values():
            btn.setText(self._tr("Запустить"))
        self.launch_btn.setText(self._tr("Запустить"))
        self.cancel_launch_btn.setText(self._tr("Отмена"))
        self._refresh_external_status()
        self.log_panel.retranslate_ui()
        self.resource_bar.retranslate_ui()

    def _on_theme_changed(self, name):
        self.theme_manager.apply_theme(name)
        log.info("Тема оформления изменена на: %s", name)

    def _on_theme_changed_externally(self, name):
        """Тема была изменена в PromptConfigEditor или PromptVault, пока
        лаунчер уже открыт — applying уже сделан в ThemeManager, здесь
        только подтягиваем видимое состояние комбобокса."""
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(name)
        self.theme_combo.blockSignals(False)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку ComfyUI_windows_portable")
        )
        if chosen:
            self.path_edit.setText(chosen)
            self._refresh_scripts()

    def _refresh_external_status(self):
        """Показывает, готово ли каждое приложение к запуску (и как именно
        оно будет запущено) — чисто информационно, путь не редактируется."""
        for app in EXTERNAL_APPS:
            status_label = self.external_status_labels[app.subdir]
            if app.subdir in IN_PROCESS_WINDOW_FACTORIES:
                # Монолитная сборка ComfyUIStudio: инструмент открывается
                # окном этого же процесса, отдельный exe/подпроцесс не
                # ищется и не нужен.
                status_label.setText(self._tr("Готово — откроется в этом же приложении."))
                status_label.setStyleSheet("color: #6fbf73;")
                continue
            cmd, cwd, error = resolve_external_launch(app)
            if error:
                status_label.setText(error)
                status_label.setStyleSheet("color: #d9534f;")
            else:
                status_label.setText(self._tr("Найдено: {}").format(cwd))
                status_label.setStyleSheet("color: #6fbf73;")

    def _launch_external(self, app: "ExternalApp"):
        status_label = self.external_status_labels[app.subdir]

        factory = IN_PROCESS_WINDOW_FACTORIES.get(app.subdir)
        if factory is not None:
            self._open_in_process_window(app, factory, status_label)
            return

        ok, message = launch_external_app(app)
        if ok:
            status_label.setText(
                self._tr("{} запущен в отдельном процессе.").format(app.label)
            )
            status_label.setStyleSheet("color: #6fbf73;")
        else:
            status_label.setText(message)
            status_label.setStyleSheet("color: #d9534f;")

    def _open_in_process_window(self, app: "ExternalApp", factory, status_label):
        """Открывает окно инструмента комплекта в текущем процессе (см.
        IN_PROCESS_WINDOW_FACTORIES/register_in_process_app выше). Если
        окно уже было открыто и просто свёрнуто/скрыто за другими окнами —
        поднимает существующее вместо создания второго.

        Когда пользователь ЗАКРЫВАЕТ окно (крестиком), оно не просто
        прячется: WA_DeleteOnClose заставляет Qt реально уничтожить его
        C++-объект после closeEvent, сигнал destroyed чистит запись в
        self._child_windows, а gc.collect() сразу забирает то, что окно
        держало в памяти (для PromptVault — загруженные модели
        torch/transformers). Раньше запись из кэша не удалялась никогда,
        и вся эта память оставалась занятой до закрытия всего приложения,
        даже если было закрыто только окно инструмента.
        """
        window = self._child_windows.get(app.subdir)
        if window is None:
            try:
                window = factory()
            except Exception as e:
                log.exception("Не удалось открыть окно %s", app.label)
                status_label.setText(f"Не удалось открыть {app.label}: {e}")
                status_label.setStyleSheet("color: #d9534f;")
                return
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(
                lambda _obj=None, subdir=app.subdir: self._on_child_window_destroyed(subdir)
            )
            self._child_windows[app.subdir] = window

        window.show()
        window.raise_()
        window.activateWindow()

        status_label.setText(self._tr("{} открыт.").format(app.label))
        status_label.setStyleSheet("color: #6fbf73;")

    def _on_child_window_destroyed(self, subdir):
        self._child_windows.pop(subdir, None)
        # Явный gc.collect() — на объекте окна почти наверняка были
        # цикличные ссылки (сигналы/слоты, родитель/потомок в Qt), которые
        # обычный refcounting сам по себе не всегда убирает сразу же.
        gc.collect()
        log.info(
            "Окно инструмента '%s' закрыто и удалено из памяти процесса",
            subdir,
        )

    def _refresh_scripts(self):
        scripts = find_run_scripts(self.path_edit.text().strip())
        self._loading_fields = True
        current = self.script_combo.currentText()
        self.script_combo.clear()
        self.script_combo.addItems(scripts)
        if current in scripts:
            self.script_combo.setCurrentText(current)
        elif self.cfg.get("script") in scripts:
            self.script_combo.setCurrentText(self.cfg["script"])
        else:
            self.script_combo.setCurrentText(guess_default_script(scripts))
        self._loading_fields = False

    def _on_launch(self):
        root_path = self.path_edit.text().strip()
        ok, msg = validate_portable_root(root_path)
        if not ok:
            self.set_status(self._tr(msg))
            log.warning("Проверка папки не пройдена: %s", msg)
            return
        script = self.script_combo.currentText()
        if not script:
            self.set_status(self._tr("Выберите скрипт запуска"))
            return

        self.set_status("")
        self.log_panel.text.clear()
        cfg = {
            "root_path": root_path,
            "script": script,
            "port": self.port_spin.value(),
            "disable_auto_launch": self.disable_auto_launch_check.isChecked(),
            "sync_comfy_theme": self.sync_comfy_theme_check.isChecked(),
            "launch_args": self.launch_args_dialog.collect_launch_args(),
        }
        self.cfg = cfg
        save_config(cfg)
        self.launch_requested.emit(cfg)


# --------------------------------------------------------------------------
# Наблюдатель за запуском сервера (без своей страницы — прогресс теперь
# показывается прямо на экране настроек, чтобы был виден живой лог)
# --------------------------------------------------------------------------

class LaunchWatcher(QObject):
    ready = Signal()
    failed = Signal(str)
    progress = Signal(str)

    TIMEOUT_SECONDS = 180

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)
        self._port = None
        self._elapsed = 0
        self._process = None

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def start(self, port, process: ComfyProcess):
        self._port = port
        self._process = process
        self._elapsed = 0
        self.progress.emit(self._tr("Запуск ComfyUI, ожидание сервера..."))
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _check(self):
        if self._process is not None and not self._process.is_running():
            self.stop()
            code = self._process.exit_code()
            log.error("Процесс ComfyUI завершился раньше времени, код выхода: %s", code)
            self.failed.emit(
                self._tr(
                    "Процесс ComfyUI неожиданно завершился (код выхода: {}). "
                    "Подробности — в логе ниже."
                ).format(code)
            )
            return

        if is_port_open(self._port):
            self.stop()
            self.ready.emit()
            return

        self._elapsed += 1
        if self._elapsed >= self.TIMEOUT_SECONDS:
            self.stop()
            log.error("Таймаут ожидания сервера ComfyUI (%s сек)", self.TIMEOUT_SECONDS)
            self.failed.emit(
                self._tr("ComfyUI не поднялся за {} секунд.").format(self.TIMEOUT_SECONDS)
            )
            return

        self.progress.emit(
            self._tr("Запуск ComfyUI, ожидание сервера... ({}с)").format(self._elapsed)
        )


# --------------------------------------------------------------------------
# Страница со встроенным браузером
# --------------------------------------------------------------------------

class BrowserPage(QWidget):
    # Раздельные сигналы: "Настройки" НЕ останавливает сервер,
    # "Остановить" — останавливает. Раньше обе кнопки делали одно и то же.
    settings_requested = Signal()
    stop_requested = Signal()

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(40)
        # Раньше цвет был захардкожен (тёмная "шторка браузера" поверх
        # любой темы) — теперь панель красится тем же QSS, что и весь
        # остальной интерфейс, и меняется вместе с темой оформления.
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(10, 0, 10, 0)

        self.address_label = QLabel("")
        top_row.addWidget(self.address_label)

        self.resource_bar = ResourceBar(loc=self.loc)
        top_row.addWidget(self.resource_bar)

        top_row.addStretch(1)

        self.settings_btn = QPushButton(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr("Вернуться к настройкам, не останавливая ComfyUI"))
        self.settings_btn.setFlat(True)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        top_row.addWidget(self.settings_btn)

        self.stop_btn = QPushButton(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr("Остановить процесс ComfyUI"))
        self.stop_btn.setFlat(True)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        top_row.addWidget(self.stop_btn)

        layout.addWidget(top_bar)

        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.view)

        self._profile = None
        self._page = None
        # True только когда встроенная страница ComfyUI реально
        # догрузилась — до этого runJavaScript() либо ничего не найдёт
        # (window.app ещё не создан фронтендом), либо выполнится в
        # контексте предыдущей/пустой страницы.
        self._page_ready = False

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.settings_btn.setText(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr("Вернуться к настройкам, не останавливая ComfyUI"))
        self.stop_btn.setText(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr("Остановить процесс ComfyUI"))
        self.resource_bar.retranslate_ui()

    def load(self, port):
        if self._profile is None:
            os.makedirs(WEBENGINE_PROFILE_DIR, exist_ok=True)
            self._profile = QWebEngineProfile("comfyui_launcher", self.view)
            self._profile.setPersistentStoragePath(WEBENGINE_PROFILE_DIR)

        self._page = RestrictedWebPage(self._profile, "127.0.0.1", port, self.view)
        self._page_ready = False
        self._page.loadFinished.connect(self._on_load_finished)
        self.view.setPage(self._page)
        url = f"http://127.0.0.1:{port}/"
        self.address_label.setText(url)
        self.view.load(QUrl(url))

    def _on_load_finished(self, ok):
        self._page_ready = bool(ok)

    def apply_color_palette(self, palette_id):
        """Переключает встроенную палитру ComfyUI в УЖЕ открытой странице —
        через тот же JS-вызов, который выполняется, когда пользователь сам
        меняет тему в диалоге настроек ComfyUI. Это применяет палитру
        мгновенно и параллельно сохраняет её на бэкенде — перезапуск
        сервера не нужен, в отличие от правки comfy.settings.json на диске.

        Пробуем новый API фронтенда (app.extensionManager.setting.set),
        и, если его нет в этой сборке фронтенда, откатываемся на legacy
        (app.ui.settings.setSettingValue) — оба существуют для обратной
        совместимости в разных версиях ComfyUI_frontend.
        """
        if self._page is None or not self._page_ready:
            return

        js = f"""
        (function() {{
            try {{
                var value = {json.dumps(palette_id)};
                if (window.app && window.app.extensionManager
                        && window.app.extensionManager.setting) {{
                    window.app.extensionManager.setting.set('Comfy.ColorPalette', value);
                }} else if (window.app && window.app.ui && window.app.ui.settings) {{
                    window.app.ui.settings.setSettingValue('Comfy.ColorPalette', value);
                }}
            }} catch (e) {{
                console.error('ComfyUIStudio: не удалось применить палитру', e);
            }}
        }})();
        """
        self._page.runJavaScript(js)

    def update_stats(self, stats: dict):
        self.resource_bar.update_stats(stats)

    def unload(self):
        # Отвязываем страницу от вида перед уничтожением процесса,
        # чтобы не тянуть загрузку "мёртвого" сервера.
        self._page_ready = False
        if self._profile is not None:
            self.view.setPage(QWebEnginePage(self._profile, self.view))
        if self._page is not None:
            self._page.deleteLater()
            self._page = None


# --------------------------------------------------------------------------
# Трей
# --------------------------------------------------------------------------

class TrayIcon(QSystemTrayIcon):
    show_window_requested = Signal()
    stop_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, loc=None, parent=None):
        super().__init__(icon, parent)
        self.loc = loc
        self.setToolTip(APP_NAME)

        self.menu = QMenu()
        self.show_action = QAction(self._tr("Показать окно"), self.menu)
        self.show_action.triggered.connect(self.show_window_requested.emit)
        self.menu.addAction(self.show_action)

        self.stop_action = QAction(self._tr("Остановить ComfyUI"), self.menu)
        self.stop_action.triggered.connect(self.stop_requested.emit)
        self.menu.addAction(self.stop_action)

        self.menu.addSeparator()

        self.quit_action = QAction(self._tr("Выход"), self.menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.show_action.setText(self._tr("Показать окно"))
        self.stop_action.setText(self._tr("Остановить ComfyUI"))
        self.quit_action.setText(self._tr("Выход"))

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_window_requested.emit()

    def update_stats(self, stats: dict):
        self.setToolTip(format_stats_tooltip(stats, tr=self._tr))


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, theme_manager: ThemeManager, loc=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.theme_manager = theme_manager
        self.loc = loc
        self.cfg = load_config()
        self.comfy_process = None
        self._quitting = False

        self.log_bridge = ProcessLogBridge()
        self.log_bridge.line_received.connect(self._on_process_log_line)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.settings_page = SettingsPage(self.cfg, theme_manager, loc)
        self.browser_page = BrowserPage(loc)

        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.browser_page)

        self.settings_page.launch_requested.connect(self._on_launch)
        self.settings_page.open_running_requested.connect(self._show_browser_page)
        self.settings_page.stop_requested.connect(self._stop_and_show_settings)
        self.settings_page.cancel_requested.connect(self._on_launch_cancelled)

        self.launch_watcher = LaunchWatcher(loc=self.loc, parent=self)
        self.launch_watcher.ready.connect(self._on_server_ready)
        self.launch_watcher.failed.connect(self._on_server_failed)
        self.launch_watcher.progress.connect(self.settings_page.update_launch_progress)

        self.browser_page.settings_requested.connect(self._show_settings_keep_running)
        self.browser_page.stop_requested.connect(self._stop_and_show_settings)

        self.theme_manager.theme_applied.connect(self._on_app_theme_applied)

        self.stack.setCurrentWidget(self.settings_page)

        tray_icon = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else self.windowIcon()
        self.tray = TrayIcon(tray_icon, loc)
        self.tray.show_window_requested.connect(self._restore_from_tray)
        self.tray.stop_requested.connect(self._stop_and_show_settings)
        self.tray.quit_requested.connect(self._quit_from_tray)
        self.tray.show()

        if self.loc is not None:
            self.loc.language_changed_externally.connect(self._retranslate_secondary_ui)
        self.settings_page.language_changed.connect(self._retranslate_secondary_ui)

        # Трей должен существовать до первого срабатывания монитора —
        # start() сразу делает один опрос, а не ждёт первый тик таймера.
        self.resource_monitor = ResourceMonitor(self._get_running_port)
        self.resource_monitor.stats_updated.connect(self._on_stats_updated)
        self.log_bridge.progress_chunk_received.connect(
            self.resource_monitor.feed_log_line
        )
        self.resource_monitor.start()

    # -- лог процесса ComfyUI -----------------------------------------

    def _on_process_log_line(self, line):
        self.settings_page.log_panel.append_line(line)

    # -- мониторинг ------------------------------------------------------

    def _get_running_port(self):
        if self.comfy_process is not None and self.comfy_process.is_running():
            return self.cfg.get("port")
        return None

    def _on_stats_updated(self, stats):
        self.tray.update_stats(stats)
        self.browser_page.update_stats(stats)
        self.settings_page.resource_bar.update_stats(stats)

    # -- запуск/остановка ------------------------------------------------

    def _on_launch(self, cfg):
        self.cfg = cfg
        try:
            launch_script = prepare_launch_script(
                cfg["root_path"], cfg["script"], build_extra_launch_args(cfg)
            )
        except OSError as e:
            log.exception("Не удалось подготовить скрипт запуска")
            self.settings_page.set_status(
                self.settings_page._tr("Не удалось подготовить скрипт запуска: {}").format(e)
            )
            return

        if cfg.get("sync_comfy_theme"):
            sync_comfyui_color_palette(cfg["root_path"], self.theme_manager.current_theme())

        self.comfy_process = ComfyProcess(cfg["root_path"], launch_script, self.log_bridge)
        self.comfy_process.start()

        # Остаёмся на экране настроек — виден живой лог запуска, только
        # снизу появляется индикатор прогресса вместо отдельной страницы.
        self.settings_page.show_launch_progress(
            self.settings_page._tr("Запуск ComfyUI, ожидание сервера...")
        )
        self.launch_watcher.start(cfg["port"], self.comfy_process)

    def _on_server_ready(self):
        log.info("Сервер ComfyUI поднялся, открываю встроенный браузер")
        self.settings_page.hide_launch_progress()
        self.browser_page.load(self.cfg["port"])
        self.stack.setCurrentWidget(self.browser_page)

        # Подстраховка: применяем текущую тему сразу после того, как
        # страница реально догрузится (а не только то, что уже успели
        # записать в comfy.settings.json до старта сервера) — на случай,
        # если тема приложения поменялась между сохранением конфига и
        # фактическим стартом сервера.
        if self.cfg.get("sync_comfy_theme"):
            self.browser_page._page.loadFinished.connect(self._sync_comfy_theme_once)

    def _sync_comfy_theme_once(self, ok):
        if ok:
            self._on_app_theme_applied(self.theme_manager.current_theme())

    def _on_app_theme_applied(self, theme_name):
        """Живая, без перезапуска, синхронизация палитры ComfyUI при смене
        темы приложения — см. apply_color_palette() в BrowserPage."""
        if not self.cfg.get("sync_comfy_theme"):
            return
        if self.comfy_process is None or not self.comfy_process.is_running():
            return
        palette = COMFY_PALETTE_MAP.get(theme_name, "dark")
        self.browser_page.apply_color_palette(palette)

    def _on_server_failed(self, message):
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.hide_launch_progress()
        self.settings_page.set_status(message)
        self.settings_page.set_server_running(False)

    def _on_launch_cancelled(self):
        self.launch_watcher.stop()
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.hide_launch_progress()
        self.settings_page.set_status(self.settings_page._tr("Запуск отменён."))
        self.settings_page.set_server_running(False)

    def _show_browser_page(self):
        # Возврат к уже работающему ComfyUI без перезапуска.
        self.stack.setCurrentWidget(self.browser_page)

    def _show_settings_keep_running(self):
        # "Настройки" из окна браузера — сервер продолжает работать.
        running = self.comfy_process is not None and self.comfy_process.is_running()
        self.settings_page.set_server_running(running, self.cfg.get("port"))
        self.stack.setCurrentWidget(self.settings_page)

    def _stop_and_show_settings(self):
        self.browser_page.unload()
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.set_status("")
        self.settings_page.set_server_running(False)
        self.stack.setCurrentWidget(self.settings_page)

    # -- трей --------------------------------------------------------

    def _retranslate_secondary_ui(self, _code):
        """Перевыставляет тексты того, что вне SettingsPage (у неё
        своя обработка смены языка): страницу браузера и меню трея.
        Вызывается и при локальной смене языка (комбобокс в
        SettingsPage), и при внешней (см. shared_language.py)."""
        self.browser_page.retranslate_ui()
        self.tray.retranslate_ui()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.close()

    def closeEvent(self, event):
        if not self._quitting:
            # По умолчанию сворачиваем в трей, а не завершаем работу —
            # ComfyUI (если запущен) продолжает работать в фоне.
            event.ignore()
            self.hide()
            if self.tray.supportsMessages():
                self.tray.showMessage(
                    APP_NAME,
                    "Приложение свёрнуто в трей. ComfyUI продолжает работать, "
                    "если был запущен. Чтобы выйти полностью — пункт «Выход» в трее.",
                    QSystemTrayIcon.Information,
                    4000,
                )
            return

        if self.comfy_process and self.comfy_process.is_running():
            reply = QMessageBox.question(
                self,
                APP_NAME,
                "ComfyUI ещё запущен. Остановить процесс и выйти?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                self._quitting = False
                return
            self.comfy_process.stop()

        self.resource_monitor.stop()
        self.tray.hide()

        # Явно отвязываем страницу ComfyUI от вида перед выходом (как и
        # при возврате в настройки без остановки, см. unload() выше) --
        # иначе при резком завершении процесса QtWebEngine может не
        # успеть сбросить persistent-хранилище (IndexedDB/localStorage)
        # фронтенда ComfyUI на диск. Именно в этом сторадже фронтенд
        # держит список открытых вкладок/воркфлоу -- без сброса на диск
        # он "теряется", и после перезапуска лаунчера ComfyUI поднимается
        # с чистого листа, а сами воркфлоу приходится открывать заново.
        self.browser_page.unload()
        event.accept()

        # setQuitOnLastWindowClosed(False) держит цикл событий живым,
        # пока мы явно не попросим его завершиться — иначе процесс
        # остаётся висеть в диспетчере задач после закрытия окна.
        # Небольшая задержка перед фактическим quit() даёт QtWebEngine
        # время дообработать deleteLater() старой страницы и сбросить
        # её хранилище на диск, прежде чем процесс будет завершён.
        QTimer.singleShot(400, QApplication.instance().quit)


def create_window(app: QApplication) -> "MainWindow":
    """Готовит тему/язык/иконку и возвращает главное окно лаунчера, не
    запуская цикл событий -- используется как при самостоятельном запуске
    (main() ниже), так и из монолитного ComfyUIStudio (см. корневой
    main.py), где QApplication уже создан заранее и общий на все три
    инструмента комплекта."""
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("Системный трей недоступен — иконка трея не будет показана")

    theme_manager = ThemeManager()
    theme_manager.apply_theme(theme_manager.current_theme(), app)

    loc = LocalizationManager()
    loc.apply_language(loc.current_language())

    log.info("=== Запуск %s ===", APP_NAME)
    return MainWindow(theme_manager, loc)


def main():
    if hasattr(Qt, "AA_ShareOpenGLContexts"):
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    window = create_window(app)
    window.show()

    exit_code = app.exec()
    log.info("=== Выход, код %s ===", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
