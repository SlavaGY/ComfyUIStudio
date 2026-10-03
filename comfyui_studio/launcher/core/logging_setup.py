"""
Логирование лаунчера и разрешение путей ресурсов.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты). app_base_dir()
переехал в .constants (см. пояснение в докстринге того модуля) -- здесь
остаются только resource_path() и setup_logging(), которые опираются на
константы путей.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .constants import APP_DIR, APP_LOG_PATH, app_base_dir


def resource_path(relative):
    """Путь к бандловым ресурсам (иконка и т.п.), работает и из исходников,
    и из собранного PyInstaller-exe."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = app_base_dir()
    return os.path.join(base, relative)


ICON_PATH = resource_path(os.path.join("assets", "icon.ico"))


def setup_logging():
    r"""Настраивает логгер "comfyui_launcher": файл в %APPDATA% + консоль.

    Файловый хендлер -- best-effort. Раньше `RotatingFileHandler(...)`
    вызывался безусловно, и `log = setup_logging()` на уровне модуля
    означал, что ЛЮБОЙ импорт `logging_setup` (а через него --
    `launcher.core.config`, `launcher.core.comfy_process`,
    `remote.app` -- headless-сервер Remote тянет его транзитивно)
    выполняет запись в `%APPDATA%\ComfyUILauncher\`: пакет становился
    неимпортируемым там, где этот путь недоступен (read-only профиль,
    песочница, чужой CI-раннер). Живое следствие: 5 тест-файлов
    `tests/launcher` и `tests/remote` не собирались вообще с
    `PermissionError: [Errno 13] ...\launcher.log`.

    Теперь недоступность каталога/файла логируется предупреждением в
    консоль, а приложение продолжает работать без файлового лога --
    логирование не та функциональность, ради которой стоит падать на
    старте.
    """
    logger = logging.getLogger("comfyui_launcher")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_error = None
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            APP_LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    except OSError as exc:
        file_error = exc
    else:
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(console_handler)

    if file_error is not None:
        logger.warning(
            "Файловый лог недоступен (%s) -- пишу только в консоль: %s",
            APP_LOG_PATH,
            file_error,
        )
    return logger


log = setup_logging()


# --------------------------------------------------------------------------
# Уровень логирования консоли -- этап 4 дорожной карты ("Единое дерево
# настроек" -> Advanced). Файловый хендлер (см. setup_logging() выше)
# ВСЕГДА пишет DEBUG -- это то, что реально читают при разборе проблем
# (см. APP_LOG_PATH), менять его уровень смысла нет. Настраивается
# только консольный хендлер -- то, что видно в stdout при запуске из
# консоли/IDE; в собранном windowed-режиме (--console=False в
# ComfyUIStudio-*.spec) эта консоль всё равно никому не видна, но
# уровень хендлера всё равно применяется единообразно, а не только
# "когда есть консоль".
# --------------------------------------------------------------------------

AVAILABLE_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def set_console_log_level(level_name: str) -> None:
    """Меняет уровень КОНСОЛЬНОГО хендлера логгера "comfyui_launcher" на
    лету -- вызывается один раз при старте (см. MainWindow.__init__,
    применяет cfg["log_level"]) и заново при изменении в
    ui/settings/advanced_page.py. Неизвестное имя уровня тихо
    игнорируется (остаётся прежний уровень) -- пользователь такое имя
    предложить не может (значение приходит из QComboBox с
    AVAILABLE_LOG_LEVELS), но на случай повреждённого config.json падать
    из-за опечатки в файле не хочется.
    """
    level = getattr(logging, level_name.upper(), None)
    if level is None:
        log.warning("Неизвестный уровень логирования в config.json: %s", level_name)
        return
    for handler in log.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            handler.setLevel(level)
