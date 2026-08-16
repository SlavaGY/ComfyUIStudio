"""Тесты для app/settings.py (AppSettings) — настройки автоочистки
миниатюр/логов, доступные через окно настроек (задача: SettingsWindow).
"""

import pytest

from comfyui_studio.promptvault.config import (
    LOG_DIR_MAX_BYTES,
    LOG_MAX_AGE_DAYS,
    THUMBNAIL_CACHE_MAX_BYTES,
    THUMBNAIL_MAX_AGE_DAYS,
)
from comfyui_studio.promptvault.settings import AppSettings


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """QSettings("PromptVault", "PromptVault") иначе читал/писал бы
    настройки реального пользователя из ~/.config.

    Помимо подмены HOME/XDG_CONFIG_HOME, явно чистим сам стор — Qt
    может закешировать разрешённый путь/состояние QSettings на уровне
    процесса и не всегда переоткрывает файл при смене этих переменных
    окружения между тестами, так что одной подмены HOME недостаточно
    для полной изоляции от других тестовых файлов, использующих тот же
    org/app QSettings (см. регрессию, из-за которой это было добавлено:
    значение, сохранённое в test_app_settings.py, "утекало" сюда)."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


class TestDefaults:
    """Без сохранённых пользователем значений — должны совпадать с
    config.py."""

    def test_thumbnail_max_age_days_default(self):

        assert AppSettings().thumbnail_max_age_days() == THUMBNAIL_MAX_AGE_DAYS

    def test_thumbnail_cache_max_mb_default(self):

        assert AppSettings().thumbnail_cache_max_mb() == THUMBNAIL_CACHE_MAX_BYTES // (1024 * 1024)

    def test_log_max_age_days_default(self):

        assert AppSettings().log_max_age_days() == LOG_MAX_AGE_DAYS

    def test_log_dir_max_mb_default(self):

        assert AppSettings().log_dir_max_mb() == LOG_DIR_MAX_BYTES // (1024 * 1024)


class TestPersistence:

    def test_thumbnail_max_age_days_roundtrip(self):

        settings = AppSettings()
        settings.set_thumbnail_max_age_days(7)

        assert AppSettings().thumbnail_max_age_days() == 7

    def test_thumbnail_cache_max_mb_roundtrip(self):

        settings = AppSettings()
        settings.set_thumbnail_cache_max_mb(1234)

        assert AppSettings().thumbnail_cache_max_mb() == 1234

    def test_log_max_age_days_roundtrip(self):

        settings = AppSettings()
        settings.set_log_max_age_days(3)

        assert AppSettings().log_max_age_days() == 3

    def test_log_dir_max_mb_roundtrip(self):

        settings = AppSettings()
        settings.set_log_dir_max_mb(99)

        assert AppSettings().log_dir_max_mb() == 99

    def test_values_survive_across_separate_instances(self):
        """Симулирует перезапуск приложения — новый AppSettings()
        должен видеть значения, сохранённые предыдущим экземпляром."""

        first = AppSettings()
        first.set_thumbnail_max_age_days(15)
        first.set_log_dir_max_mb(42)

        second = AppSettings()

        assert second.thumbnail_max_age_days() == 15
        assert second.log_dir_max_mb() == 42
