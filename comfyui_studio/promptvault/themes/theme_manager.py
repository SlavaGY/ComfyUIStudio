"""ThemeManager PromptVault.

Вся логика — в общем ``comfyui_studio/themes/base.py``; PromptVault
использует базовые темы без добавок. Модуль остаётся точкой импорта
``comfyui_studio.promptvault.themes.theme_manager``.
"""
from comfyui_studio.themes.base import (
    AVAILABLE_THEMES,
    CIRCLE_COLORS,
    DEFAULT_THEME,
    BaseThemeManager,
)

__all__ = ["AVAILABLE_THEMES", "CIRCLE_COLORS", "DEFAULT_THEME", "ThemeManager"]


class ThemeManager(BaseThemeManager):
    """Менеджер тем PromptVault: следит за общим файлом темы комплекта и
    применяет тему сразу, если её поменяли в ComfyUI Launcher или
    PromptConfigEditor (сигнал theme_changed_externally)."""

    # Прежние значения — менять нельзя, иначе сохранённая тема потеряется.
    settings_org = "PromptVault"
    settings_app = "PromptVault"
    log_name = "comfyui_studio.promptvault.themes"
