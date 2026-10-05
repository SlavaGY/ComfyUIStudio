"""Общая база менеджеров языка для всех приложений комплекта (этап R8).

У лаунчера, Prompt Builder и PromptVault было по своему
``LocalizationManager`` — у первых двух код совпадал почти дословно
(выбор языка, QSettings, слежение за общим файлом языка, словарь
ru -> en), у PromptVault отличалась только сама установка перевода
(QTranslator и .qm вместо словаря). Теперь общая часть — здесь:

* ``BaseLocalizationManager`` — язык: текущий/применить/восстановить,
  QSettings, общий файл ``language.json`` и живая синхронизация между
  уже запущенными приложениями. Как именно «включается» язык, решает
  метод ``_install_language`` (по умолчанию ничего не делает);
* ``DictLocalizationManager`` — то же плюс ``tr()`` по словарю
  ``translations`` вида ``{"en": {"русская строка": "English"}}``
  (конвенция лаунчера и Prompt Builder: русская строка = ключ).

Сами словари НЕ объединены, и это сознательно: общий ключ у них ровно
один — «Обновить» — и переводится по-разному (лаунчер: «Refresh»,
Prompt Builder: «Update», кнопка обновления пресета). Слить словари —
значит молча изменить одну из двух кнопок, а выигрыш — одна строка.
Отдельный PromptVault вообще не использует словари (его .ts/.qm не
тронуты).

Имена QSettings у приложений НЕ менялись — иначе выбранный
пользователем язык «потерялся» бы при обновлении.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from . import shared_language


class BaseLocalizationManager(QObject):
    """Держит язык интерфейса и синхронизирует его с остальными
    приложениями комплекта (shared_language.py).

    Если язык поменяли в другом приложении, пока это уже открыто, он
    применяется сразу, а не только при следующем запуске (сигнал
    ``language_changed_externally``).
    """

    language_changed_externally = Signal(str)

    # --- точки расширения (переопределяются в наследниках) -------------

    #: организация/приложение для QSettings — менять нельзя (см. docstring модуля)
    settings_org: str = "ComfyUIStudio"
    settings_app: str = "ComfyUIStudio"
    #: отображаемое имя в UI -> код языка
    languages: dict[str, str] = {}
    #: язык по умолчанию — один из кодов ``languages``
    default_language: str = "ru"

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings(self.settings_org, self.settings_app)
        self._applied_language: str | None = None

        self._watcher: Any = None
        if hasattr(shared_language, "SharedLanguageWatcher"):
            self._watcher = shared_language.SharedLanguageWatcher(self)
            self._watcher.language_changed.connect(self._on_shared_language_changed)

    # --------------------------------------------------

    def _valid_codes(self) -> set[str]:
        return set(self.languages.values())

    def _install_language(self, language_code: str) -> None:
        """Включает язык в самом приложении. Получает уже проверенный
        код. У словарных менеджеров ничего делать не нужно — перевод
        берётся в момент tr(); PromptVault ставит здесь QTranslator."""

    # --------------------------------------------------

    def _on_shared_language_changed(self, language_code: str) -> None:
        if language_code == self._applied_language or language_code not in self._valid_codes():
            return
        self.apply_language(language_code)
        self.language_changed_externally.emit(language_code)

    def available_languages(self) -> list[str]:
        """Отображаемые имена языков в фиксированном порядке."""
        return list(self.languages.keys())

    def current_language(self) -> str:
        """Код языка: сначала общий язык комплекта (shared_language.py) —
        так подхватывается язык, выбранный в другом приложении; если его
        нет, откатываемся на собственные QSettings, а затем на язык по
        умолчанию."""
        shared = shared_language.read_shared_language()
        if shared in self._valid_codes():
            return str(shared)

        saved = self._settings.value("language", self.default_language)
        if saved not in self._valid_codes():
            return self.default_language
        return str(saved)

    def apply_language(self, language_code: str) -> None:
        """Устанавливает язык интерфейса и запоминает выбор."""
        if language_code not in self._valid_codes():
            language_code = self.default_language

        self._install_language(language_code)

        self._settings.setValue("language", language_code)
        self._applied_language = language_code
        if self._watcher is not None:
            self._watcher.mark_applied(language_code)

        # Общий язык комплекта — чтобы остальные приложения, запущенные
        # после этого (или уже открытые), применили тот же язык.
        shared_language.write_shared_language(language_code)

    def restore_saved_language(self) -> None:
        """Применяет язык, выбранный в прошлый раз (или общий язык комплекта)."""
        self.apply_language(self.current_language())


class DictLocalizationManager(BaseLocalizationManager):
    """База с ``tr()`` по словарю ``translations`` (русская строка — ключ)."""

    #: {код языка: {исходная строка: перевод}}
    translations: dict[str, dict[str, str]] = {}

    def tr(self, text: str) -> str:  # type: ignore[override]
        """Возвращает перевод text для текущего языка, либо сам text,
        если языка нет в словаре или перевода для этой строки ещё нет
        (охват перевода сознательно неполный)."""
        lang = self.current_language()
        return self.translations.get(lang, {}).get(text, text)
