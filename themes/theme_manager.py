import sys
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

import shared_theme

# Дочерний логгер общего логгера приложения (см. setup_logging() в
# comfyui_launcher.py) — пишет в тот же launcher.log благодаря
# стандартному наследованию обработчиков по имени "comfyui_launcher.*".
log = logging.getLogger("comfyui_launcher.themes")

# см. подробный комментарий у аналогичного кода в comfyui_launcher.py
# (resource_path) — Path(__file__).resolve().parent сам по себе не
# находит файлы внутри сборки PyInstaller, нужен sys._MEIPASS.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    THEMES_DIR = Path(sys._MEIPASS) / "themes"
else:
    THEMES_DIR = Path(__file__).resolve().parent

# отображаемое имя -> имя файла в этой же папке
AVAILABLE_THEMES = {
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
CIRCLE_COLORS = {
    "Dark": "#e0e0e0",
    "Light": "#1e1e1e",
    "Nord": "#ECEFF4",
    "Catppuccin Mocha": "#cdd6f4",
    "Dracula": "#f8f8f2",
    "GitHub Dark": "#c9d1d9",
}


class ThemeManager(QObject):
    """Загружает QSS-темы и применяет их ко всему приложению.

    Последняя выбранная тема запоминается через QSettings и
    восстанавливается при следующем запуске. Кроме того, следит за
    общим файлом темы комплекта (shared_theme.py) через
    SharedThemeWatcher — если тема была изменена в PromptConfigEditor
    или PromptVault, пока это приложение уже открыто, она применяется
    сразу же, без перезапуска (сигнал theme_changed_externally).
    """

    theme_changed_externally = Signal(str)

    # Испускается в конце apply_theme() при ЛЮБОМ применении темы —
    # и локальном (выбор в комбобоксе настроек), и внешнем (через
    # SharedThemeWatcher). В отличие от theme_changed_externally, нужен
    # тем, кому важен сам факт применения темы независимо от источника
    # (например, живая синхронизация палитры ComfyUI в уже открытой
    # странице — без этого сигнала пришлось бы дублировать подписку и
    # на комбобокс, и на theme_changed_externally по отдельности).
    theme_applied = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ComfyUILauncher", "ComfyUILauncher")
        self._cache = {}
        self._applied_theme = None
        log.debug("THEMES_DIR = %s", THEMES_DIR)

        self._watcher = None
        if hasattr(shared_theme, "SharedThemeWatcher"):
            self._watcher = shared_theme.SharedThemeWatcher(self)
            self._watcher.theme_changed.connect(self._on_shared_theme_changed)

    # --------------------------------------------------

    def _on_shared_theme_changed(self, theme_name):
        """Вызывается, когда тему поменяли в ДРУГОМ приложении комплекта,
        пока это приложение уже открыто."""
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
        """Имя темы: сначала смотрим общую тему комплекта приложений
        (shared_theme.py) — так подхватывается тема, выбранная в
        PromptConfigEditor или PromptVault; если её нет, откатываемся на
        собственные QSettings, а затем на тему по умолчанию."""

        shared = shared_theme.read_shared_theme()
        if shared in AVAILABLE_THEMES:
            return shared

        saved = self._settings.value("theme", DEFAULT_THEME)

        if saved not in AVAILABLE_THEMES:
            return DEFAULT_THEME

        return saved

    # --------------------------------------------------

    def circle_color(self, theme_name):
        """Контрастный цвет для круглой кнопки-переключателя тем."""

        return CIRCLE_COLORS.get(theme_name, "#ffffff")

    # --------------------------------------------------

    def load_stylesheet(self, theme_name):
        """Возвращает содержимое .qss файла темы (с кэшированием)."""

        if theme_name not in AVAILABLE_THEMES:
            theme_name = DEFAULT_THEME

        if theme_name in self._cache:
            return self._cache[theme_name]

        path = THEMES_DIR / AVAILABLE_THEMES[theme_name]

        try:
            stylesheet = path.read_text(encoding="utf-8")
        except OSError as e:
            # Раньше это молча превращалось в пустую строку — тема
            # "применялась" (и даже сохранялась), но фактически без
            # единого правила CSS, поэтому ничего не менялось на экране.
            # Теперь хотя бы видно в логе, что файл темы не найден.
            log.error("Не удалось прочитать файл темы %s: %s", path, e)
            stylesheet = ""

        self._cache[theme_name] = stylesheet

        return stylesheet

    # --------------------------------------------------

    def apply_theme(self, theme_name, app=None):
        """Применяет тему ко всему приложению и запоминает выбор."""

        if app is None:
            app = QApplication.instance()

        if app is None:
            return

        if theme_name not in AVAILABLE_THEMES:
            theme_name = DEFAULT_THEME

        stylesheet = self.load_stylesheet(theme_name)
        app.setStyleSheet(stylesheet)
        log.info(
            "Применена тема '%s' (%d байт стилей из %s)",
            theme_name, len(stylesheet), THEMES_DIR / AVAILABLE_THEMES[theme_name],
        )

        # QApplication.setStyleSheet() не всегда сам перерисовывает уже
        # созданные виджеты — известная особенность Qt/PySide. Форсируем
        # re-polish всех существующих виджетов, иначе тема "применяется"
        # (и сохраняется), но экран не меняется, пока не пересоздать окно.
        for widget in app.allWidgets():
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

        self._settings.setValue("theme", theme_name)
        self._settings.sync()

        self._applied_theme = theme_name
        if self._watcher is not None:
            self._watcher.mark_applied(theme_name)

        # Общая тема комплекта — чтобы PromptConfigEditor и PromptVault,
        # запущенные после этого (или уже открытые — см. SharedThemeWatcher),
        # применили ту же тему.
        shared_theme.write_shared_theme(theme_name)

        self.theme_applied.emit(theme_name)
