"""
Логирование лаунчера и разрешение путей ресурсов.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты). app_base_dir()
переехал в .constants (см. пояснение в докстринге того модуля) -- здесь
остаются только resource_path() и setup_logging(), которые опираются на
константы путей.
"""

import os
import sys
import logging
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
