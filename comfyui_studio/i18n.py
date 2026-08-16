"""
i18n.py
=======
Переключение языка интерфейса лаунчера (RU/EN) — по образцу
app/i18n.py из PromptVault, но без QTranslator/.ts/.qm: исходные
строки в этом приложении и так на русском (а не на английском, как в
PromptVault), поэтому переводом здесь считается словарь ru -> en,
применяемый вручную через tr().

Как и тема (см. shared_theme.py), язык — общий на весь комплект
ComfyUI Studio: хранится в %APPDATA%\\ComfyUIStudio\\language.json
(shared_language.py) и синхронизируется живьём между уже запущенными
приложениями через QFileSystemWatcher.

Языковой переключатель есть только в лаунчере (см. README —
"общее окно настроек для всего комплекта"): PromptConfigEditor и
PromptVault сами язык не выбирают, только применяют то, что выбрано
здесь.

Область покрытия перевода (сознательно ограничена, см. README):
основной "хром" интерфейса — экран настроек, окно браузера ComfyUI,
меню трея. Живая телеметрия (CPU/RAM/GPU-чипы, подсказка трея) и
сообщения в лог-панели пока остаются на русском — их перевод стоит
меньше, чем перевод основных элементов управления, и отложен на потом.
"""
from PySide6.QtCore import QObject, QSettings, Signal

from . import shared_language

AVAILABLE_LANGUAGES = {
    "Русский": "ru",
    "English": "en",
}

DEFAULT_LANGUAGE = "ru"

# ru -> en, только для строк, реально обёрнутых в tr() ниже по коду
TRANSLATIONS = {
    "en": {
        "Лог последнего запуска ComfyUI": "Last ComfyUI launch log",
        "Тема оформления:": "Theme:",
        "Язык интерфейса:": "Language:",
        "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)": (
            "Sync ComfyUI's own theme with the app theme (closest built-in match)"
        ),
        (
            "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
            "темой приложения — вживую, пока ComfyUI уже открыт, и при "
            "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
            "цветовая система узлов."
        ): (
            "Syncs ComfyUI's built-in palette (Comfy.ColorPalette) with the app "
            "theme — live, while ComfyUI is already open, and on the next "
            "launch. Not identical to the Qt theme — ComfyUI has its own node "
            "color system."
        ),
        "Папка ComfyUI_windows_portable:": "ComfyUI_windows_portable folder:",
        "Аргументы запуска ComfyUI": "ComfyUI launch arguments",
        "Аргументы запуска ComfyUI...": "ComfyUI launch arguments...",
        "Дополнительные аргументы командной строки": "Additional command-line arguments",
        "Закрыть": "Close",
        (
            "Слушать на всех сетевых интерфейсах — ComfyUI станет доступен "
            "с других устройств в локальной сети. Поле необязательно: IP "
            "для прослушивания (по умолчанию — все интерфейсы)."
        ): (
            "Listen on all network interfaces — ComfyUI becomes reachable "
            "from other devices on your local network. The field is optional: "
            "the IP to listen on (defaults to all interfaces)."
        ),
        "Считать только на CPU, без GPU (медленно, но работает без видеокарты).": (
            "Compute on CPU only, no GPU (slow, but works without a graphics card)."
        ),
        "Меньше VRAM ценой части скорости — для видеокарт с небольшим объёмом VRAM.": (
            "Use less VRAM at some cost to speed — for GPUs with a small amount of VRAM."
        ),
        "Минимум VRAM — для видеокарт с очень малым объёмом VRAM (если --lowvram не помогает).": (
            "Minimal VRAM usage — for GPUs with very little VRAM (if --lowvram isn't enough)."
        ),
        "Держать модели в VRAM постоянно — для видеокарт с большим запасом VRAM.": (
            "Keep models loaded in VRAM at all times — for GPUs with plenty of VRAM."
        ),
        (
            "Держать вообще всё, включая текстовые энкодеры, в VRAM — для "
            "видеокарт с очень большим запасом VRAM."
        ): (
            "Keep absolutely everything, including text encoders, in VRAM — "
            "for GPUs with a very large amount of VRAM."
        ),
        "Зарезервировать под другие программы указанный объём VRAM, в гигабайтах.": (
            "Reserve this much VRAM (in GB) for other applications."
        ),
        "Отключить оптимизацию xFormers (если из-за неё возникают ошибки или чёрные изображения).": (
            "Disable the xFormers optimization (if it's causing errors or black images)."
        ),
        "Способ показа превью во время генерации: none, auto, latent2rgb или taesd.": (
            "Preview method used during generation: none, auto, latent2rgb, or taesd."
        ),
        (
            "Загрузить дополнительный файл extra_model_paths.yaml с путями "
            "к моделям/LoRA и т.п., лежащим вне папки ComfyUI."
        ): (
            "Load an additional extra_model_paths.yaml file with paths to "
            "models/LoRAs/etc. that live outside the ComfyUI folder."
        ),
        "Обзор...": "Browse...",
        "Скрипт запуска:": "Launch script:",
        "Порт:": "Port:",
        "Не давать ComfyUI открывать системный браузер при старте": (
            "Don't let ComfyUI open the system browser on startup"
        ),
        "Другие инструменты": "Other tools",
        "Запустить": "Launch",
        "Отмена": "Cancel",
        "Открыть папку с логами": "Open logs folder",
        "Очистить": "Clear",
        "ComfyUI уже запущен": "ComfyUI is already running",
        "Открыть ComfyUI": "Open ComfyUI",
        "Остановить": "Stop",
        "\u2190 Настройки": "\u2190 Settings",
        "Вернуться к настройкам, не останавливая ComfyUI": (
            "Back to settings without stopping ComfyUI"
        ),
        "\u23F9 Остановить": "\u23F9 Stop",
        "Остановить процесс ComfyUI": "Stop the ComfyUI process",
        "Показать окно": "Show window",
        "Остановить ComfyUI": "Stop ComfyUI",
        "Выход": "Quit",
        "Выберите папку ComfyUI_windows_portable": "Select the ComfyUI_windows_portable folder",
        "Найдено: {}": "Found: {}",
        "Готово — откроется в этом же приложении.": "Ready — opens in this same app.",
        "{} открыт.": "{} opened.",
        "{} запущен в отдельном процессе.": "{} launched in a separate process.",
        "Запуск ComfyUI, ожидание сервера...": "Launching ComfyUI, waiting for the server...",
        "Запуск ComfyUI, ожидание сервера... ({}с)": "Launching ComfyUI, waiting for the server... ({}s)",
        "Процесс ComfyUI неожиданно завершился (код выхода: {}). Подробности — в логе ниже.": (
            "The ComfyUI process exited unexpectedly (exit code: {}). See the log below for details."
        ),
        "ComfyUI не поднялся за {} секунд.": "ComfyUI didn't start within {} seconds.",
        "Запуск отменён.": "Launch cancelled.",
        "н/д": "n/a",
        "ГБ": "GB",
        "Указанная папка не существует": "The specified folder doesn't exist",
        "Не найден python_embeded\\python.exe — это не похоже на ComfyUI portable": (
            "python_embeded\\python.exe not found — this doesn't look like ComfyUI portable"
        ),
        "Не найден ComfyUI\\main.py": "ComfyUI\\main.py not found",
        "В папке не найдено ни одного run_*.bat": "No run_*.bat found in the folder",
        "Выберите скрипт запуска": "Choose a launch script",
        "Не удалось подготовить скрипт запуска: {}": "Failed to prepare the launch script: {}",
        "Очередь": "Queue",
        "Готово": "Done",
        "ComfyUI не запущен": "ComfyUI is not running",
        "оценка...": "estimating...",
        "< 1 с": "< 1 s",
        "мин": "min",
        "с": "s",
    }
}


class LocalizationManager(QObject):
    """Переключает язык интерфейса лаунчера и держит его в синхроне с
    остальными приложениями комплекта (shared_language.py), аналогично
    ThemeManager для тем (themes/theme_manager.py)."""

    language_changed_externally = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ComfyUILauncher", "ComfyUILauncher")
        self._applied_language = None

        self._watcher = None
        if hasattr(shared_language, "SharedLanguageWatcher"):
            self._watcher = shared_language.SharedLanguageWatcher(self)
            self._watcher.language_changed.connect(self._on_shared_language_changed)

    def _on_shared_language_changed(self, language_code):
        valid_codes = set(AVAILABLE_LANGUAGES.values())
        if language_code == self._applied_language or language_code not in valid_codes:
            return
        self.apply_language(language_code)
        self.language_changed_externally.emit(language_code)

    def available_languages(self):
        return list(AVAILABLE_LANGUAGES.keys())

    def current_language(self):
        """Код языка: сначала общий язык комплекта (shared_language.py),
        иначе собственные QSettings, иначе язык по умолчанию."""
        shared = shared_language.read_shared_language()
        if shared in AVAILABLE_LANGUAGES.values():
            return shared
        saved = self._settings.value("language", DEFAULT_LANGUAGE)
        if saved not in AVAILABLE_LANGUAGES.values():
            return DEFAULT_LANGUAGE
        return saved

    def apply_language(self, language_code):
        if language_code not in AVAILABLE_LANGUAGES.values():
            language_code = DEFAULT_LANGUAGE

        self._settings.setValue("language", language_code)
        self._applied_language = language_code
        if self._watcher is not None:
            self._watcher.mark_applied(language_code)

        shared_language.write_shared_language(language_code)

    def tr(self, text):
        """Возвращает перевод text для текущего языка, либо сам text,
        если языка нет в словаре или перевода для этой строки ещё нет
        (сознательно неполный охват — см. docstring модуля)."""
        lang = self.current_language()
        return TRANSLATIONS.get(lang, {}).get(text, text)
