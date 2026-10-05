"""Общая база менеджеров тем для всех приложений комплекта (этап R8).

Раньше у лаунчера, Prompt Builder и PromptVault было по своему
``ThemeManager`` с почти одинаковым кодом (выбор темы, QSettings, слежение
за общим файлом темы, применение к QApplication) и по своей копии шести
``.qss``-файлов. Теперь:

* набор базовых ``.qss`` один — рядом с этим модулем (``themes/*.qss``);
* общая логика живёт в ``BaseThemeManager``;
* то, чем приложения различаются, вынесено в точки расширения:
  ``settings_org``/``settings_app`` (куда QSettings сохраняет тему),
  ``log_name``, ``repolish_widgets`` и ``supplemental_qss()`` — добавка к
  базовой теме, которая нужна только этому приложению (лаунчер читает её
  из ``themes/launcher/*.qss``, Prompt Builder строит из своей палитры,
  PromptVault обходится без неё).

Имена QSettings у приложений НЕ менялись — иначе сохранённая пользователем
тема «потерялась» бы при обновлении.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

from .. import shared_theme

# Path(__file__).resolve().parent сам по себе не находит файлы внутри
# сборки PyInstaller — нужен sys._MEIPASS (см. подробный комментарий у
# resource_path в comfyui_studio/launcher/core/logging_setup.py). Темы
# лежат в _MEIPASS/comfyui_studio/themes: так их кладут все три .spec
# и обе отдельные сборки (Prompt Builder, PromptVault).
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    THEMES_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "themes"
else:
    THEMES_DIR = Path(__file__).resolve().parent

# отображаемое имя -> имя файла в THEMES_DIR
AVAILABLE_THEMES: dict[str, str] = {
    "Dark": "dark.qss",
    "Light": "light.qss",
    "Nord": "nord.qss",
    "Catppuccin Mocha": "catppuccin.qss",
    "Dracula": "dracula.qss",
    "GitHub Dark": "github_dark.qss",
}

DEFAULT_THEME = "Dark"

# контрастный цвет (совпадает с основным цветом текста темы) для кружка
# кнопки-переключателя тем: тёмный кружок на светлой теме, светлый — на тёмной
CIRCLE_COLORS: dict[str, str] = {
    "Dark": "#e0e0e0",
    "Light": "#1e1e1e",
    "Nord": "#ECEFF4",
    "Catppuccin Mocha": "#cdd6f4",
    "Dracula": "#f8f8f2",
    "GitHub Dark": "#c9d1d9",
}


class BaseThemeManager(QObject):
    """Загружает QSS-темы и применяет их ко всему приложению.

    Последняя выбранная тема запоминается через QSettings и
    восстанавливается при следующем запуске. Кроме того, следит за общим
    файлом темы комплекта (shared_theme.py) через SharedThemeWatcher —
    если тема была изменена в другом приложении комплекта, пока это уже
    открыто, она применяется сразу, без перезапуска
    (сигнал ``theme_changed_externally``).
    """

    theme_changed_externally = Signal(str)

    # Испускается в конце apply_theme() при ЛЮБОМ применении темы — и
    # локальном (выбор в комбобоксе настроек), и внешнем (через
    # SharedThemeWatcher). В отличие от theme_changed_externally, нужен
    # тем, кому важен сам факт применения темы независимо от источника
    # (например, живая синхронизация палитры ComfyUI в уже открытой
    # странице лаунчера).
    theme_applied = Signal(str)

    # --- точки расширения (переопределяются в наследниках) -------------

    #: организация/приложение для QSettings — менять нельзя (см. docstring модуля)
    settings_org: str = "ComfyUIStudio"
    settings_app: str = "ComfyUIStudio"
    #: имя логгера; у лаунчера оно «comfyui_launcher.*» — чтобы сообщения
    #: попадали в launcher.log (см. setup_logging)
    log_name: str = "comfyui_studio.themes"
    #: принудительно перерисовать все существующие виджеты после
    #: setStyleSheet(). Нужно лаунчеру (известная особенность Qt: на уже
    #: созданных виджетах тема иногда не обновляется до пересоздания
    #: окна); в PromptVault, где виджетов-плиток много, это не включено.
    repolish_widgets: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings(self.settings_org, self.settings_app)
        self._cache: dict[str, str] = {}
        self._applied_theme: str | None = None
        self._log = logging.getLogger(self.log_name)
        self._log.debug("THEMES_DIR = %s", self.themes_dir)

        self._watcher: Any = None
        if hasattr(shared_theme, "SharedThemeWatcher"):
            self._watcher = shared_theme.SharedThemeWatcher(self)
            self._watcher.theme_changed.connect(self._on_shared_theme_changed)

    # --------------------------------------------------

    @property
    def themes_dir(self) -> Path:
        """Папка с базовыми .qss. Читается в момент вызова (а не
        запоминается при создании менеджера), чтобы тест на путь внутри
        сборки PyInstaller мог перезагрузить модуль с подставленным
        sys._MEIPASS."""
        return THEMES_DIR

    def supplemental_qss(self, theme_name: str) -> str:
        """Добавка к базовой теме, нужная только этому приложению.
        Получает уже проверенное имя темы. По умолчанию — пусто."""
        return ""

    # --------------------------------------------------

    def _on_shared_theme_changed(self, theme_name: str) -> None:
        """Вызывается, когда тему поменяли в ДРУГОМ приложении комплекта,
        пока это приложение уже открыто."""
        if theme_name == self._applied_theme or theme_name not in AVAILABLE_THEMES:
            return
        self.apply_theme(theme_name)
        self.theme_changed_externally.emit(theme_name)

    # --------------------------------------------------

    def available_themes(self) -> list[str]:
        """Список отображаемых имён тем в фиксированном порядке."""
        return list(AVAILABLE_THEMES.keys())

    # --------------------------------------------------

    def current_theme(self) -> str:
        """Имя темы: сначала смотрим общую тему комплекта
        (shared_theme.py) — так подхватывается тема, выбранная в другом
        приложении; если её нет, откатываемся на собственные QSettings, а
        затем на тему по умолчанию."""
        shared = shared_theme.read_shared_theme()
        if shared in AVAILABLE_THEMES:
            return str(shared)

        saved = self._settings.value("theme", DEFAULT_THEME)
        if saved not in AVAILABLE_THEMES:
            return DEFAULT_THEME
        return str(saved)

    # --------------------------------------------------

    def circle_color(self, theme_name: str) -> str:
        """Контрастный цвет для круглой кнопки-переключателя тем."""
        return CIRCLE_COLORS.get(theme_name, "#ffffff")

    # --------------------------------------------------

    def load_stylesheet(self, theme_name: str) -> str:
        """Базовый .qss темы + добавка приложения (с кэшированием)."""
        if theme_name not in AVAILABLE_THEMES:
            theme_name = DEFAULT_THEME

        cached = self._cache.get(theme_name)
        if cached is not None:
            return cached

        path = self.themes_dir / AVAILABLE_THEMES[theme_name]
        try:
            base = path.read_text(encoding="utf-8")
        except OSError as e:
            # Раньше это молча превращалось в пустую строку — тема
            # «применялась» (и даже сохранялась), но фактически без
            # единого правила, поэтому на экране ничего не менялось.
            # Теперь хотя бы видно в логе, что файл темы не найден.
            self._log.error("Не удалось прочитать файл темы %s: %s", path, e)
            base = ""

        extra = self.supplemental_qss(theme_name)
        stylesheet = base + "\n" + extra if extra else base

        self._cache[theme_name] = stylesheet
        return stylesheet

    # --------------------------------------------------

    def apply_theme(self, theme_name: str, app: QApplication | None = None) -> None:
        """Применяет тему ко всему приложению и запоминает выбор."""
        if app is None:
            instance = QApplication.instance()
            if not isinstance(instance, QApplication):
                return
            app = instance

        if theme_name not in AVAILABLE_THEMES:
            theme_name = DEFAULT_THEME

        stylesheet = self.load_stylesheet(theme_name)
        app.setStyleSheet(stylesheet)
        self._log.info(
            "Применена тема '%s' (%d байт стилей из %s)",
            theme_name,
            len(stylesheet),
            self.themes_dir / AVAILABLE_THEMES[theme_name],
        )

        if self.repolish_widgets:
            # QApplication.setStyleSheet() не всегда сам перерисовывает
            # уже созданные виджеты — известная особенность Qt/PySide.
            # Форсируем re-polish всех существующих виджетов, иначе тема
            # «применяется» (и сохраняется), но экран не меняется, пока
            # не пересоздать окно.
            for widget in app.allWidgets():
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

        self._settings.setValue("theme", theme_name)
        self._settings.sync()

        self._applied_theme = theme_name
        if self._watcher is not None:
            self._watcher.mark_applied(theme_name)

        # Общая тема комплекта — чтобы остальные приложения, запущенные
        # после этого (или уже открытые — см. SharedThemeWatcher),
        # применили ту же тему.
        shared_theme.write_shared_theme(theme_name)

        self.theme_applied.emit(theme_name)
