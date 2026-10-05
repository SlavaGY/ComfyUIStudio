"""Тесты менеджеров языка (этап R8): общая база и три приложения.

Пути к общему файлу языка и QSettings перенаправлены во временную папку
корневым conftest.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from comfyui_studio import shared_language
from comfyui_studio.i18n import AVAILABLE_LANGUAGES as LAUNCHER_LANGUAGES
from comfyui_studio.i18n import DEFAULT_LANGUAGE as LAUNCHER_DEFAULT
from comfyui_studio.i18n import TRANSLATIONS as LAUNCHER_TRANSLATIONS
from comfyui_studio.i18n import LocalizationManager as LauncherLocalization
from comfyui_studio.i18n_base import BaseLocalizationManager, DictLocalizationManager
from comfyui_studio.prompt_builder.pb_i18n import AVAILABLE_LANGUAGES as PB_LANGUAGES
from comfyui_studio.prompt_builder.pb_i18n import DEFAULT_LANGUAGE as PB_DEFAULT
from comfyui_studio.prompt_builder.pb_i18n import TRANSLATIONS as PB_TRANSLATIONS
from comfyui_studio.prompt_builder.pb_i18n import LocalizationManager as PromptBuilderLocalization
from comfyui_studio.promptvault.i18n import AVAILABLE_LANGUAGES as PV_LANGUAGES
from comfyui_studio.promptvault.i18n import DEFAULT_LANGUAGE as PV_DEFAULT
from comfyui_studio.promptvault.i18n import LocalizationManager as PromptVaultLocalization

# (класс, настройки QSettings, язык по умолчанию, словарь языков)
APPS = [
    pytest.param(
        LauncherLocalization, ("ComfyUILauncher", "ComfyUILauncher"), "ru", LAUNCHER_LANGUAGES,
        id="launcher",
    ),
    pytest.param(
        PromptBuilderLocalization, ("PromptConfigEditor", "PromptConfigEditor"), "ru", PB_LANGUAGES,
        id="prompt_builder",
    ),
    pytest.param(
        PromptVaultLocalization, ("PromptVault", "PromptVault"), "en", PV_LANGUAGES,
        id="promptvault",
    ),
]


@pytest.fixture(autouse=True)
def _no_translator_leak(qapp):
    """PromptVault ставит QTranslator на общий QApplication — снимаем
    после каждого теста, иначе он повлияет на тексты в чужих тестах."""
    yield
    PromptVaultLocalization().apply_language("en")


def _other_code(languages: dict[str, str], default: str) -> str:
    return next(code for code in languages.values() if code != default)


@pytest.mark.parametrize(("manager_cls", "org_app", "default", "languages"), APPS)
class TestSharedBehaviour:
    def test_languages_and_defaults_unchanged(
        self, qapp, manager_cls, org_app, default, languages
    ):
        assert issubclass(manager_cls, BaseLocalizationManager)
        assert manager_cls.default_language == default
        assert manager_cls.languages == languages
        assert manager_cls().available_languages() == list(languages.keys())

    def test_default_language_when_nothing_saved(
        self, qapp, manager_cls, org_app, default, languages
    ):
        assert manager_cls().current_language() == default

    def test_qsettings_names_unchanged(
        self, qapp, qsettings_factory, manager_cls, org_app, default, languages
    ):
        assert (manager_cls.settings_org, manager_cls.settings_app) == org_app
        other = _other_code(languages, default)
        manager_cls().apply_language(other)
        # выбор лежит именно там, где его искало приложение до R8
        assert qsettings_factory(*org_app).value("language") == other

    def test_apply_saves_shares_and_persists(
        self, qapp, manager_cls, org_app, default, languages
    ):
        other = _other_code(languages, default)
        manager = manager_cls()
        manager.apply_language(other)

        assert shared_language.read_shared_language() == other
        assert manager_cls().current_language() == other  # новый экземпляр

        # без общего файла остаётся то, что в QSettings
        Path(shared_language.SHARED_LANGUAGE_PATH).unlink()
        assert manager_cls().current_language() == other

    def test_invalid_code_applies_default(
        self, qapp, manager_cls, org_app, default, languages
    ):
        """PromptVault раньше принимал и записывал любой код (например,
        "xx") — теперь, как лаунчер и Prompt Builder, применяется язык по
        умолчанию."""
        manager = manager_cls()
        manager.apply_language("xx")
        assert manager.current_language() == default
        assert shared_language.read_shared_language() == default

    def test_invalid_shared_language_falls_back_to_saved(
        self, qapp, manager_cls, org_app, default, languages
    ):
        other = _other_code(languages, default)
        manager = manager_cls()
        manager.apply_language(other)
        shared_language.write_shared_language("xx")
        assert manager.current_language() == other

    def test_invalid_saved_language_falls_back_to_default(
        self, qapp, qsettings_factory, manager_cls, org_app, default, languages
    ):
        qsettings_factory(*org_app).setValue("language", "мусор")
        assert manager_cls().current_language() == default

    def test_shared_language_wins_over_saved(
        self, qapp, manager_cls, org_app, default, languages
    ):
        other = _other_code(languages, default)
        manager = manager_cls()
        manager.apply_language(default)
        shared_language.write_shared_language(other)
        assert manager.current_language() == other

    def test_restore_saved_language_reapplies_persisted_choice(
        self, qapp, qsettings_factory, manager_cls, org_app, default, languages
    ):
        other = _other_code(languages, default)
        qsettings_factory(*org_app).setValue("language", other)
        manager = manager_cls()
        manager.restore_saved_language()
        assert shared_language.read_shared_language() == other

    def test_external_change_is_applied_and_reported(
        self, qapp, manager_cls, org_app, default, languages
    ):
        other = _other_code(languages, default)
        manager = manager_cls()
        manager.apply_language(default)
        external: list[str] = []
        manager.language_changed_externally.connect(external.append)

        # проверяем именно проводку watcher -> менеджер, без файловой системы
        manager._watcher.language_changed.emit(other)

        assert external == [other]
        assert manager._applied_language == other

    def test_same_language_is_not_reapplied(
        self, qapp, manager_cls, org_app, default, languages
    ):
        manager = manager_cls()
        manager.apply_language(default)
        external: list[str] = []
        manager.language_changed_externally.connect(external.append)
        manager._watcher.language_changed.emit(default)
        assert external == []

    def test_unknown_external_language_is_ignored(
        self, qapp, manager_cls, org_app, default, languages
    ):
        manager = manager_cls()
        manager.apply_language(default)
        external: list[str] = []
        manager.language_changed_externally.connect(external.append)
        manager._watcher.language_changed.emit("xx")
        assert external == []
        assert manager.current_language() == default

    def test_own_write_is_not_taken_for_external_change(
        self, qapp, manager_cls, org_app, default, languages
    ):
        manager = manager_cls()
        external: list[str] = []
        manager.language_changed_externally.connect(external.append)
        manager.apply_language(_other_code(languages, default))
        manager._watcher._on_changed("ignored")
        assert external == []


class TestDictionaryManagers:
    """Лаунчер и Prompt Builder переводят по словарю ru -> en
    (русская строка — ключ)."""

    def test_both_are_dict_managers_with_their_own_tables(self):
        assert issubclass(LauncherLocalization, DictLocalizationManager)
        assert issubclass(PromptBuilderLocalization, DictLocalizationManager)
        assert LauncherLocalization.translations is LAUNCHER_TRANSLATIONS
        assert PromptBuilderLocalization.translations is PB_TRANSLATIONS
        assert LAUNCHER_DEFAULT == PB_DEFAULT == "ru"

    def test_russian_returns_source_text(self, qapp):
        for cls in (LauncherLocalization, PromptBuilderLocalization):
            manager = cls()
            manager.apply_language("ru")
            assert manager.tr("Обновить") == "Обновить"

    def test_unknown_string_returns_itself(self, qapp):
        for cls in (LauncherLocalization, PromptBuilderLocalization):
            manager = cls()
            manager.apply_language("en")
            assert manager.tr("строки нет ни в одном словаре") == "строки нет ни в одном словаре"

    def test_every_launcher_and_pb_entry_translates(self, qapp):
        for cls, table in (
            (LauncherLocalization, LAUNCHER_TRANSLATIONS),
            (PromptBuilderLocalization, PB_TRANSLATIONS),
        ):
            manager = cls()
            manager.apply_language("en")
            for source, translated in table["en"].items():
                assert manager.tr(source) == translated

    def test_dictionaries_are_deliberately_not_merged(self, qapp):
        """Единственный общий ключ словарей — «Обновить» — переводится
        по-разному: в лаунчере это «Refresh» (обновить список), в Prompt
        Builder — «Update» (кнопка обновления пресета). Если когда-нибудь
        словари решат слить, этот тест покажет, какую кнопку изменит
        слияние."""
        launcher = LauncherLocalization()
        builder = PromptBuilderLocalization()
        launcher.apply_language("en")
        builder.apply_language("en")

        assert launcher.tr("Обновить") == "Refresh"
        assert builder.tr("Обновить") == "Update"

        overlap = set(LAUNCHER_TRANSLATIONS["en"]) & set(PB_TRANSLATIONS["en"])
        assert overlap == {"Обновить"}


class TestPromptVaultTranslator:
    """PromptVault ставит перевод через QTranslator и .qm. Основные
    сценарии покрывает tools/promptvault/tests/test_i18n.py; здесь —
    только то, что относится к общей базе."""

    def test_translator_follows_applied_language(self, qapp):
        manager = PromptVaultLocalization()
        assert PV_DEFAULT == "en"

        manager.apply_language("ru")
        assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Статистика"

        manager.apply_language("en")
        assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Stats"

    def test_external_language_installs_translator(self, qapp):
        manager = PromptVaultLocalization()
        manager.apply_language("en")
        manager._watcher.language_changed.emit("ru")
        assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Статистика"

    def test_invalid_code_does_not_install_translator(self, qapp):
        manager = PromptVaultLocalization()
        manager.apply_language("xx")
        assert QCoreApplication.translate("Toolbar", "📊 Stats") == "📊 Stats"
