"""
Синхронизация цветовой темы приложения со встроенной палитрой ComfyUI.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import json
import os

from comfyui_studio.jsonfile import atomic_write_json

from ..core.logging_setup import log

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
        # файл принадлежит ComfyUI: пишем атомарно, чтобы обрыв записи
        # не оставил ему обрезанные настройки
        atomic_write_json(settings_path, data)
        log.info(
            "Тема ComfyUI синхронизирована: '%s' -> палитра '%s' (%s)",
            app_theme_name, palette, settings_path,
        )
    except Exception:
        log.exception("Не удалось синхронизировать тему ComfyUI (%s)", settings_path)


# --------------------------------------------------------------------------
# Управление процессом ComfyUI + потоковый разбор его вывода
# --------------------------------------------------------------------------


