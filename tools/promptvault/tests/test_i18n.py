"""Тесты для app/i18n.py (задача 3.5: локализация ru/en через
QTranslator; задача: полный аудит строк UI под self.tr() — переход с
самодельного DictTranslator на настоящие .qm) и связанной с этим
переключалки в Toolbar.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).

QApplication общий на всю pytest-сессию (см. tests/conftest.py), а
QTranslator устанавливается на него глобально — поэтому каждый тест,
установивший перевод, обязан снять его тем же объектом-менеджером до
своего завершения, иначе он переживёт этот файл и повлияет на тексты
виджетов в других тестах.

Тесты используют РЕАЛЬНЫЙ скомпилированный promptvault_ru.qm (тот же
файл, что грузит приложение) — не фиктивные/временные .qm — так тесты
одновременно проверяют, что .qm в репозитории не битый и реально
содержит перевод для строк, которыми проверяются здесь.
"""

import pytest
from PySide6.QtCore import QCoreApplication

from app.i18n import AVAILABLE_LANGUAGES, LocalizationManager
from app.ui.toolbar import Toolbar


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """QSettings("PromptVault", "PromptVault") иначе читал/писал бы
    язык интерфейса реального пользователя из ~/.config."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))


class TestAvailableLanguages:

    def test_english_and_russian_available(self):

        assert AVAILABLE_LANGUAGES == {"English": "en", "Русский": "ru"}


class TestLocalizationManager:

    def test_install_and_remove_translator_on_language_switch(self, qapp):

        manager = LocalizationManager()

        try:
            manager.apply_language("ru")
            assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Статистика"

            manager.apply_language("en")
            # "en" — DEFAULT_LANGUAGE, переводчик вообще не ставится ->
            # возвращается исходная (английская) строка
            assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Stats"
        finally:
            manager.apply_language("en")

    def test_switching_back_to_ru_reinstalls_translator(self, qapp):
        """Переключение en -> ru -> en -> ru должно каждый раз реально
        снимать/ставить транслятор, а не залипать на первом
        apply_language("ru")."""

        manager = LocalizationManager()

        try:
            manager.apply_language("ru")
            manager.apply_language("en")
            manager.apply_language("ru")

            assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Статистика"
        finally:
            manager.apply_language("en")

    def test_current_language_persists_across_instances(self, qapp):
        """QSettings — это то, что переживает перезапуск приложения:
        новый LocalizationManager должен увидеть язык, установленный
        предыдущим."""

        manager1 = LocalizationManager()

        try:
            manager1.apply_language("ru")

            manager2 = LocalizationManager()
            assert manager2.current_language() == "ru"
        finally:
            manager1.apply_language("en")

    def test_default_language_is_english(self, qapp):

        manager = LocalizationManager()

        assert manager.current_language() == "en"

    def test_restore_saved_language_applies_persisted_choice(self, qapp):

        manager1 = LocalizationManager()

        try:
            manager1.apply_language("ru")

            manager2 = LocalizationManager()
            manager2.restore_saved_language()

            assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Статистика"
        finally:
            manager1.apply_language("en")

    def test_unknown_language_code_does_not_crash(self, qapp):
        """Нет promptvault_xx.qm для несуществующего кода языка —
        QTranslator.load() вернёт False, транслятор просто не
        ставится (тот же эффект, что и для "en"), без исключения."""

        manager = LocalizationManager()

        try:
            manager.apply_language("xx")
            assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Stats"
        finally:
            manager.apply_language("en")


class TestToolbarRetranslateUi:
    """Toolbar больше не переключает язык сам (см. app/ui/settings_window.py)
    — только применяет уже установленный QTranslator к своим текстам
    по вызову retranslate_ui(), как это делает SettingsWindow."""

    def test_retranslate_ui_updates_button_texts(self, qapp):

        toolbar = Toolbar()
        manager = LocalizationManager()

        try:
            manager.apply_language("ru")
            toolbar.retranslate_ui()
            assert toolbar.stats_btn.text() == "📊 Статистика"

            manager.apply_language("en")
            toolbar.retranslate_ui()
            assert toolbar.stats_btn.text() == "📊 Stats"
        finally:
            manager.apply_language("en")
