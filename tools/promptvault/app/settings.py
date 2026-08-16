"""Пользовательские настройки хранения, доступные через окно настроек
(см. app/ui/settings_window.py).

В отличие от статических констант в app/config.py (значения по
умолчанию, зашитые в код) — здесь то же самое, но с возможностью
переопределения пользователем через QSettings.

Тема, язык, семантический поиск и размер страницы ленивой загрузки
(GENERATIONS_PAGE_SIZE) НЕ хранятся здесь — у них уже есть собственные
менеджеры с собственной персистентностью (ThemeManager,
LocalizationManager, GalleryManager.set_semantic_search_enabled /
generations_page_size) — дублировать эту логику незачем, SettingsWindow
обращается к ним напрямую. Здесь — только автоочистка (задача 3.5),
у которой естественного "владельца"-менеджера нет: она применяется в
app/main.py, до создания GalleryManager/MainWindow.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

from app.config import (
    LOG_DIR_MAX_BYTES,
    LOG_MAX_AGE_DAYS,
    THUMBNAIL_CACHE_MAX_BYTES,
    THUMBNAIL_MAX_AGE_DAYS,
)


class AppSettings:
    """Тонкая обёртка над QSettings — не синглтон, создание дёшево
    (сам QSettings ничего не держит в памяти между экземплярами, читает
    один и тот же файл/реестр ОС)."""

    def __init__(self) -> None:

        self._settings = QSettings("PromptVault", "PromptVault")

    # ------------------------------------------------------------
    # автоочистка миниатюр (задача 3.5)

    def thumbnail_max_age_days(self) -> int:

        value: Any = self._settings.value("storage/thumbnail_max_age_days", THUMBNAIL_MAX_AGE_DAYS)
        return int(value)

    def set_thumbnail_max_age_days(self, value: int) -> None:

        self._settings.setValue("storage/thumbnail_max_age_days", int(value))

    def thumbnail_cache_max_mb(self) -> int:

        default_mb = THUMBNAIL_CACHE_MAX_BYTES // (1024 * 1024)
        value: Any = self._settings.value("storage/thumbnail_cache_max_mb", default_mb)
        return int(value)

    def set_thumbnail_cache_max_mb(self, value: int) -> None:

        self._settings.setValue("storage/thumbnail_cache_max_mb", int(value))

    # ------------------------------------------------------------
    # автоочистка логов (задача 3.5)

    def log_max_age_days(self) -> int:

        value: Any = self._settings.value("storage/log_max_age_days", LOG_MAX_AGE_DAYS)
        return int(value)

    def set_log_max_age_days(self, value: int) -> None:

        self._settings.setValue("storage/log_max_age_days", int(value))

    def log_dir_max_mb(self) -> int:

        default_mb = LOG_DIR_MAX_BYTES // (1024 * 1024)
        value: Any = self._settings.value("storage/log_dir_max_mb", default_mb)
        return int(value)

    def set_log_dir_max_mb(self, value: int) -> None:

        self._settings.setValue("storage/log_dir_max_mb", int(value))
