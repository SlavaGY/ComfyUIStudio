"""Локализация интерфейса: переключение ru/en через QTranslator
(задача 3.5; задача: полный аудит строк UI под self.tr()).

Переводы хранятся в скомпилированных .qm-файлах (app/resources/
translations/promptvault_{lang}.qm) — стандартный формат Qt,
загружаемый обычным QTranslator.load(). Исходник для перевода — .ts
рядом (той же папке); собирается из app/ui/*.py инструментом
pyside6-lupdate (идёт в комплекте с самим PySide6 — pip install
PySide6 кладёt pyside6-lupdate/pyside6-lrelease в тот же venv/Scripts,
отдельно ставить не нужно), .qm компилируется из .ts инструментом
pyside6-lrelease. Полный цикл обновления перевода — см.
tools/update_translations.py и раздел "Локализация" в CONTRIBUTING.md.

Раньше (первая версия задачи 3.5) переводы жили в обычном Python
dict, а не в .qm — на момент внедрения инфраструктуры существовал
риск, что pyside6-lupdate/pyside6-lrelease будут недоступны в
окружении разработки/CI. Проверено: они ставятся вместе с PySide6
через pip на любой платформе (Windows включительно) — блокера не
было, только не хватало самого аудита строк (~90% self.tr() в
app/ui/ на тот момент отсутствовали). Аудит сделан, переход на
настоящие .qm — тоже.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QSettings, QTranslator, Signal

from comfyui_studio.promptvault.config import TRANSLATIONS_DIR
from .. import shared_language

# отображаемое имя в UI -> код языка
AVAILABLE_LANGUAGES: dict[str, str] = {
    "English": "en",
    "Русский": "ru",
}

DEFAULT_LANGUAGE = "en"


class LocalizationManager(QObject):
    """Переключает язык интерфейса через QTranslator, аналогично
    ThemeManager для тем (см. app/themes/theme_manager.py).

    Текущий выбор запоминается в QSettings и восстанавливается при
    следующем запуске. Также следит за общим языком комплекта
    (shared_language.py) — если язык поменяли в ComfyUI Launcher или
    PromptConfigEditor, пока это приложение уже открыто, он применяется
    сразу (сигнал language_changed_externally), а не только при
    следующем запуске.
    """

    language_changed_externally = Signal(str)

    def __init__(self) -> None:

        super().__init__()
        self._settings = QSettings("PromptVault", "PromptVault")
        self._translator: QTranslator | None = None
        self._applied_language: str | None = None

        self._watcher = None
        if hasattr(shared_language, "SharedLanguageWatcher"):
            self._watcher = shared_language.SharedLanguageWatcher(self)
            self._watcher.language_changed.connect(self._on_shared_language_changed)

    def _on_shared_language_changed(self, language_code: str) -> None:
        valid_codes = set(AVAILABLE_LANGUAGES.values())
        if language_code == self._applied_language or language_code not in valid_codes:
            return
        self.apply_language(language_code)
        self.language_changed_externally.emit(language_code)

    def current_language(self) -> str:
        """Код языка: сначала общий язык комплекта (shared_language.py)
        — так подхватывается язык, выбранный в ComfyUI Launcher или
        PromptConfigEditor; если его нет, откатываемся на собственные
        QSettings, а затем на язык по умолчанию."""

        shared = shared_language.read_shared_language()
        if shared in AVAILABLE_LANGUAGES.values():
            return shared

        return str(self._settings.value("language", DEFAULT_LANGUAGE))

    def apply_language(self, language_code: str) -> None:
        """Устанавливает язык интерфейса и запоминает выбор.

        Для DEFAULT_LANGUAGE ("en", исходный язык строк в коде)
        переводчик не устанавливается вообще — self.tr() возвращает
        сам source_text, никакой .qm для английского не существует и
        не нужен.

        Виджеты, уже построенные к моменту вызова, НЕ обновляют текст
        автоматически (Python-виджеты в этом проекте не переопределяют
        changeEvent(QEvent.LanguageChange), в отличие от кода,
        сгенерированного Qt Designer) — вызывающая сторона должна сама
        перестроить/перевести видимые тексты (см. Toolbar.retranslate_ui,
        SettingsWindow.retranslate_ui, StatisticsWindow.refresh).
        """

        app = QCoreApplication.instance()

        if self._translator is not None and app is not None:
            app.removeTranslator(self._translator)
            self._translator = None

        if language_code != DEFAULT_LANGUAGE and app is not None:

            qm_path = TRANSLATIONS_DIR / f"promptvault_{language_code}.qm"

            translator = QTranslator()

            # load() возвращает False, если файла нет/он битый — в
            # этом случае намеренно НЕ ставим транслятор вообще
            # (тот же эффект, что при отсутствующем переводе в старом
            # DictTranslator: self.tr() просто возвращает исходный
            # английский текст, а не падает и не показывает пустоту)
            if translator.load(str(qm_path)):
                self._translator = translator
                app.installTranslator(self._translator)

        self._settings.setValue("language", language_code)

        self._applied_language = language_code
        if self._watcher is not None:
            self._watcher.mark_applied(language_code)

        # Общий язык комплекта — чтобы ComfyUI Launcher и
        # PromptConfigEditor, запущенные после этого (или уже открытые),
        # применили тот же язык.
        shared_language.write_shared_language(language_code)

    def restore_saved_language(self) -> None:

        self.apply_language(self.current_language())
