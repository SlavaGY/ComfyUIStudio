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

from PySide6.QtCore import QCoreApplication, QTranslator

from comfyui_studio.i18n_base import BaseLocalizationManager
from comfyui_studio.promptvault.config import TRANSLATIONS_DIR

# отображаемое имя в UI -> код языка
AVAILABLE_LANGUAGES: dict[str, str] = {
    "English": "en",
    "Русский": "ru",
}

DEFAULT_LANGUAGE = "en"


class LocalizationManager(BaseLocalizationManager):
    """Переключает язык интерфейса через QTranslator, аналогично
    ThemeManager для тем (см. comfyui_studio/promptvault/themes/).

    Общая часть — текущий выбор в QSettings, слежение за общим языком
    комплекта (shared_language.py), сигнал language_changed_externally —
    живёт в comfyui_studio/i18n_base.py; здесь только сама установка
    перевода из .qm.
    """

    # Прежние значения — менять нельзя, иначе выбранный язык потеряется.
    settings_org = "PromptVault"
    settings_app = "PromptVault"
    languages = AVAILABLE_LANGUAGES
    default_language = DEFAULT_LANGUAGE

    def __init__(self) -> None:

        super().__init__()
        self._translator: QTranslator | None = None

    def _install_language(self, language_code: str) -> None:
        """Ставит QTranslator для языка (и снимает прежний).

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
