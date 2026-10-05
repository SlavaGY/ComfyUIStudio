"""ThemeManager лаунчера.

Вся общая логика — в ``themes/base.py`` (``BaseThemeManager``). Здесь
только то, что отличает лаунчер: имя QSettings, логгер, принудительный
re-polish виджетов и добавка к базовой теме — ``themes/launcher/*.qss``
(правила для QGroupBox/QProgressBar/QSpinBox/QMenu, которых нет в
базовых темах).

Модуль остаётся точкой импорта ``comfyui_studio.themes.theme_manager``:
на него опираются окна лаунчера и тесты.
"""
from __future__ import annotations

from .base import (
    AVAILABLE_THEMES,
    CIRCLE_COLORS,
    DEFAULT_THEME,
    BaseThemeManager,
)

__all__ = ["AVAILABLE_THEMES", "CIRCLE_COLORS", "DEFAULT_THEME", "ThemeManager"]


class ThemeManager(BaseThemeManager):
    """Менеджер тем лаунчера. Живёт в одном процессе с остальными
    окнами Studio и применяет тему к QApplication; при смене темы в
    PromptConfigEditor или PromptVault (см. SharedThemeWatcher)
    применяет её сразу и сообщает об этом сигналом
    theme_changed_externally."""

    # Прежние значения — менять нельзя, иначе сохранённая тема потеряется.
    settings_org = "ComfyUILauncher"
    settings_app = "ComfyUILauncher"
    # Дочерний логгер общего логгера приложения (см. setup_logging() в
    # comfyui_studio/launcher/core/logging_setup.py) — пишет в тот же
    # launcher.log благодаря наследованию обработчиков по имени
    # "comfyui_launcher.*".
    log_name = "comfyui_launcher.themes"
    repolish_widgets = True

    def supplemental_qss(self, theme_name: str) -> str:
        path = self.themes_dir / "launcher" / AVAILABLE_THEMES[theme_name]
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            self._log.error("Не удалось прочитать добавку темы лаунчера %s: %s", path, e)
            return ""
