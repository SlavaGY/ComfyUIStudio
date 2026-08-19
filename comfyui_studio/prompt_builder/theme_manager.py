"""
theme_manager.py
Управление темами — построено по образцу присланного app/themes/theme_manager.py
(PromptVault): базовые .qss файлы лежат в themes/ и применяются через
QApplication.setStyleSheet(), выбор запоминается через QSettings.

Отличие от референса: базовые .qss (взятые как есть из присланного архива)
не покрывают часть виджетов, которых там просто не было (QTreeWidget,
QTabWidget, QGroupBox, QSpinBox/QDoubleSpinBox, QMenuBar/QMenu, QStatusBar,
QTableWidget) — они нужны нашему блочному редактору. Для них дополнительно
генерируется небольшой "довесок" к стилю на основе той же палитры темы,
поэтому цвета всюду остаются едиными.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

import comfyui_studio.shared_theme as shared_theme


def resource_base() -> Path:
    """Базовая папка для файлов данных (темы, иконка). При обычном запуске —
    папка рядом со скриптом; внутри exe, собранного PyInstaller-ом
    (--onefile или --onedir), это временная папка распаковки (sys._MEIPASS)
    либо папка рядом с exe — PyInstaller подставляет её сам."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # Общий монолитный exe (ComfyUIStudio) кладёт данные этого
        # инструмента в comfyui_studio/prompt_builder/... под _MEIPASS --
        # так же, как они лежат рядом с исходниками после переноса под
        # общее пространство имён (этап 2 дорожной карты) -- специально
        # НЕ в корень _MEIPASS, чтобы не перезаписать одноимённую
        # themes/ папку ComfyUI Launcher при сборке (см. корневой
        # ComfyUIStudio.spec; сам .spec ещё предстоит обновить под новую
        # раскладку -- см. этап 5 дорожной карты).
        return Path(sys._MEIPASS) / "comfyui_studio" / "prompt_builder"
    return Path(__file__).resolve().parent


THEMES_DIR = resource_base() / "themes"

# отображаемое имя -> имя файла в themes/
AVAILABLE_THEMES = {
    "Dark": "dark.qss",
    "Light": "light.qss",
    "Nord": "nord.qss",
    "Catppuccin Mocha": "catppuccin.qss",
    "Dracula": "dracula.qss",
    "GitHub Dark": "github_dark.qss",
}

DEFAULT_THEME = "Dark"

# Цвета для виджетов, которых нет в присланных .qss (см. docstring выше).
# Значения сверены построчно с соответствующими .qss (тот же BG/панель/акцент/
# бордер/выделение), плюс WARN/ERROR — их в референсе нет вовсе, подобраны
# в тон каждой темы.
EXTRA_PALETTE: dict[str, dict[str, str]] = {
    "Dark": {
        "bg": "#1e1e1e", "panel_bg": "#252526", "entry_bg": "#2d2d2d", "hover_bg": "#3c3c3c",
        "border": "#3c3c3c", "fg": "#e0e0e0", "muted_fg": "#9da5b4",
        "accent": "#4fc3f7", "accent_fg": "#0d1117", "select_bg": "#094771", "select_fg": "#ffffff",
        "warn": "#dcdcaa", "error": "#f14c4c", "danger_bg": "#5a1d1d", "danger_hover": "#7a2727",
    },
    "Light": {
        "bg": "#f5f5f5", "panel_bg": "#ffffff", "entry_bg": "#ffffff", "hover_bg": "#d0d0d0",
        "border": "#d0d0d0", "fg": "#1e1e1e", "muted_fg": "#6b7280",
        "accent": "#1976d2", "accent_fg": "#ffffff", "select_bg": "#cfe4ff", "select_fg": "#0d1117",
        "warn": "#8a6d1a", "error": "#c62828", "danger_bg": "#c62828", "danger_hover": "#a51f1f",
    },
    "Nord": {
        "bg": "#2E3440", "panel_bg": "#3B4252", "entry_bg": "#3B4252", "hover_bg": "#4C566A",
        "border": "#4C566A", "fg": "#ECEFF4", "muted_fg": "#D8DEE9",
        "accent": "#88C0D0", "accent_fg": "#2E3440", "select_bg": "#5E81AC", "select_fg": "#ECEFF4",
        "warn": "#EBCB8B", "error": "#BF616A", "danger_bg": "#4a2328", "danger_hover": "#6b2f36",
    },
    "Catppuccin Mocha": {
        "bg": "#1e1e2e", "panel_bg": "#181825", "entry_bg": "#313244", "hover_bg": "#45475a",
        "border": "#45475a", "fg": "#cdd6f4", "muted_fg": "#a6adc8",
        "accent": "#89b4fa", "accent_fg": "#1e1e2e", "select_bg": "#45475a", "select_fg": "#cdd6f4",
        "warn": "#f9e2af", "error": "#f38ba8", "danger_bg": "#4a2333", "danger_hover": "#6b2f47",
    },
    "Dracula": {
        "bg": "#282a36", "panel_bg": "#21222c", "entry_bg": "#343746", "hover_bg": "#44475a",
        "border": "#44475a", "fg": "#f8f8f2", "muted_fg": "#6272a4",
        "accent": "#bd93f9", "accent_fg": "#282a36", "select_bg": "#44475a", "select_fg": "#f8f8f2",
        "warn": "#f1fa8c", "error": "#ff5555", "danger_bg": "#4a1f1f", "danger_hover": "#6b2b2b",
    },
    "GitHub Dark": {
        "bg": "#0d1117", "panel_bg": "#161b22", "entry_bg": "#0d1117", "hover_bg": "#30363d",
        "border": "#30363d", "fg": "#c9d1d9", "muted_fg": "#8b949e",
        "accent": "#58a6ff", "accent_fg": "#ffffff", "select_bg": "#1f6feb", "select_fg": "#ffffff",
        "warn": "#d29922", "error": "#f85149", "danger_bg": "#3d1418", "danger_hover": "#5a1e24",
    },
}

# контрастный цвет (совпадает с основным цветом текста темы) для кружка
# кнопки-переключателя тем: тёмный кружок на светлой теме, светлый — на тёмной
CIRCLE_COLORS = {
    "Dark": "#e0e0e0",
    "Light": "#1e1e1e",
    "Nord": "#ECEFF4",
    "Catppuccin Mocha": "#cdd6f4",
    "Dracula": "#f8f8f2",
    "GitHub Dark": "#c9d1d9",
}


def _supplemental_qss(p: dict[str, str]) -> str:
    """QSS для виджетов, отсутствующих в присланных темах (см. docstring),
    построенный из EXTRA_PALETTE — той же палитры, что и у остального UI."""
    return f"""
/* ==== Дополнительно: виджеты блочного редактора (нет в исходных темах) ==== */
QTabWidget::pane {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background-color: {p['panel_bg']};
    color: {p['muted_fg']};
    padding: 7px 16px;
    border: 1px solid {p['border']};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {p['bg']};
    color: {p['fg']};
}}
QTabBar::tab:hover {{
    background-color: {p['hover_bg']};
}}

QTreeWidget {{
    background-color: {p['entry_bg']};
    color: {p['fg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeWidget::item {{
    padding: 4px 2px;
}}
QTreeWidget::item:hover {{
    background-color: {p['hover_bg']};
}}
QTreeWidget::item:selected {{
    background-color: {p['select_bg']};
    color: {p['select_fg']};
}}

QHeaderView::section {{
    background-color: {p['panel_bg']};
    color: {p['fg']};
    padding: 4px;
    border: none;
    border-bottom: 1px solid {p['border']};
}}

QTableWidget {{
    background-color: {p['entry_bg']};
    color: {p['fg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    gridline-color: {p['border']};
    outline: none;
}}
QTableWidget::item:selected {{
    background-color: {p['select_bg']};
    color: {p['select_fg']};
}}

QGroupBox {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 12px;
    color: {p['muted_fg']};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {p['entry_bg']};
    color: {p['fg']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 3px 6px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {p['accent']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {p['panel_bg']};
    border: none;
    width: 14px;
}}

QMenuBar {{
    background-color: {p['panel_bg']};
    color: {p['fg']};
    border-bottom: 1px solid {p['border']};
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 10px;
}}
QMenuBar::item:selected {{
    background-color: {p['hover_bg']};
}}
QMenu {{
    background-color: {p['panel_bg']};
    color: {p['fg']};
    border: 1px solid {p['border']};
}}
QMenu::item {{
    padding: 5px 20px;
}}
QMenu::item:selected {{
    background-color: {p['select_bg']};
    color: {p['select_fg']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {p['border']};
    margin: 4px 6px;
}}

QStatusBar {{
    background-color: {p['panel_bg']};
    color: {p['muted_fg']};
    border-top: 1px solid {p['border']};
}}

/* ==== Наши смысловые роли, задаются через objectName ==== */
QPushButton#dangerButton {{
    background-color: {p['danger_bg']};
    color: #ffffff;
    border: 1px solid {p['danger_bg']};
}}
QPushButton#dangerButton:hover {{
    background-color: {p['danger_hover']};
}}
QLabel#warnLabel {{
    color: {p['warn']};
}}
QLabel#errorLabel {{
    color: {p['error']};
}}
QLabel#mutedLabel {{
    color: {p['muted_fg']};
}}
QLabel#headingLabel {{
    color: {p['fg']};
    font-size: 12pt;
    font-weight: 600;
}}
"""


class ThemeManager(QObject):
    """Загружает QSS-темы (файлы из themes/, как есть, плюс наш довесок для
    виджетов, которых там не было) и применяет их ко всему приложению.

    Последняя выбранная тема запоминается через QSettings и
    восстанавливается при следующем запуске. Также следит за общим
    файлом темы комплекта (shared_theme.py) — если тему поменяли в
    ComfyUI Launcher или PromptVault, пока это приложение уже открыто,
    она применяется сразу (сигнал theme_changed_externally).
    """

    theme_changed_externally = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("PromptConfigEditor", "PromptConfigEditor")
        self._cache: dict[str, str] = {}
        self._applied_theme = None

        self._watcher = None
        if hasattr(shared_theme, "SharedThemeWatcher"):
            self._watcher = shared_theme.SharedThemeWatcher(self)
            self._watcher.theme_changed.connect(self._on_shared_theme_changed)

    # --------------------------------------------------

    def _on_shared_theme_changed(self, theme_name):
        if theme_name == self._applied_theme or theme_name not in AVAILABLE_THEMES:
            return
        self.apply_theme(theme_name)
        self.theme_changed_externally.emit(theme_name)

    # --------------------------------------------------

    def available_themes(self):
        """Список отображаемых имён тем в фиксированном порядке."""
        return list(AVAILABLE_THEMES.keys())

    # --------------------------------------------------

    def current_theme(self):
        """Имя темы: сначала общая тема комплекта (shared_theme.py) — так
        подхватывается тема, выбранная в ComfyUI Launcher или PromptVault;
        если её нет, откатываемся на собственные QSettings, а затем на
        тему по умолчанию."""
        shared = shared_theme.read_shared_theme()
        if shared in AVAILABLE_THEMES:
            return shared

        saved = self._settings.value("theme", DEFAULT_THEME)
        if saved not in AVAILABLE_THEMES:
            return DEFAULT_THEME
        return saved

    # --------------------------------------------------

    def circle_color(self, theme_name):
        return CIRCLE_COLORS.get(theme_name, "#ffffff")

    def extra_palette(self, theme_name: str) -> dict[str, str]:
        """Даёт доступ к сырым цветам темы — нужно виджетам, которые красят
        себя программно (не через QSS), например индивидуальные ярлыки."""
        return EXTRA_PALETTE.get(theme_name, EXTRA_PALETTE[DEFAULT_THEME])

    # --------------------------------------------------

    def load_stylesheet(self, theme_name):
        """Возвращает содержимое .qss файла темы + наш довесок (с кэшированием)."""
        if theme_name not in AVAILABLE_THEMES:
            theme_name = DEFAULT_THEME

        if theme_name in self._cache:
            return self._cache[theme_name]

        path = THEMES_DIR / AVAILABLE_THEMES[theme_name]
        try:
            base_stylesheet = path.read_text(encoding="utf-8")
        except OSError:
            base_stylesheet = ""

        stylesheet = base_stylesheet + "\n" + _supplemental_qss(self.extra_palette(theme_name))
        self._cache[theme_name] = stylesheet
        return stylesheet

    # --------------------------------------------------

    def apply_theme(self, theme_name, app=None):
        """Применяет тему ко всему приложению и запоминает выбор."""
        if app is None:
            app = QApplication.instance()
        if app is None:
            return

        app.setStyleSheet(self.load_stylesheet(theme_name))
        self._settings.setValue("theme", theme_name)

        self._applied_theme = theme_name
        if self._watcher is not None:
            self._watcher.mark_applied(theme_name)

        # Общая тема комплекта — чтобы ComfyUI Launcher и PromptVault,
        # запущенные после этого (или уже открытые), применили ту же тему.
        shared_theme.write_shared_theme(theme_name)
