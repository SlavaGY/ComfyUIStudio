"""
i18n.py
=======
Применение языка интерфейса (RU/EN) в Character/Prompt Builder Config
Editor — по образцу i18n.py в лаунчере (ComfyUI Studio), но без
собственного переключателя: язык здесь общий на весь комплект и
выбирается только в лаунчере (см. README всего комплекта — "общее
окно настроек"), это приложение только применяет то, что выбрано там,
и живьём подхватывает смену, пока уже открыто (shared_language.py).

Охват перевода: меню, заголовки вкладок, диалог "О программе",
строка состояния, и содержимое обеих вкладок ("Персонажи",
"Конструктор промпта" — дерево блоков, формы группы/блока/варианта,
пресеты качества/источника, негативные пресеты, диалоги подтверждения).
"""
from PySide6.QtCore import QObject, QSettings, Signal

import comfyui_studio.prompt_builder.shared_language as shared_language

AVAILABLE_LANGUAGES = {
    "Русский": "ru",
    "English": "en",
}

DEFAULT_LANGUAGE = "ru"

TRANSLATIONS = {
    "en": {
        # -- Тулбар (main.py, _build_toolbar) -- заменил меню "Файл"/
        # "Справка" целиком (см. докстринг pb_settings.py и
        # main.py._build_toolbar): "Открыть папку расширения...",
        # "Открыть characters.json...", "Открыть prompt_builder_config.
        # json...", "Указать папку с файлами LoRA...", "Сохранить текущую
        # вкладку", "Сохранить как...", "Выход", "Справка", "О программе"
        # больше не существуют как действия — соответствующие ключи ниже
        # убраны как неиспользуемые (папки/бэкапы теперь в едином дереве
        # настроек ComfyUI Studio, "Открыть файл..." заменяет три диалога
        # открытия одним).
        "💾 Сохранить всё": "💾 Save all",
        "📂 Открыть файл...": "📂 Open file...",
        "Открыть файл": "Open file",
        "В какую вкладку загрузить?": "Which tab should this load into?",
        (
            'Файл "{name}" не похож по имени ни на {characters}, ни на '
            "{prompt_builder}.\n\nЗагрузить его как вкладку "
            '"Персонажи"? ("Нет" — загрузить как "Конструктор промпта")'
        ): (
            'The file "{name}" doesn\'t match either {characters} or '
            "{prompt_builder} by name.\n\nLoad it as the \"Characters\" "
            'tab? ("No" — load it as "Prompt builder")'
        ),
        "  Персонажи (characters.json)  ": "  Characters (characters.json)  ",
        "  Конструктор промпта (prompt_builder_config.json)  ": (
            "  Prompt builder (prompt_builder_config.json)  "
        ),
        (
            "Файлы не загружены — укажите папку расширения в настройках "
            "ComfyUI Studio, либо откройте файл (Ctrl+O)"
        ): (
            "No files loaded — set the extension folder in ComfyUI "
            "Studio's settings, or open a file (Ctrl+O)"
        ),

        # --- CharactersTab ---
        "Персонажи": "Characters",
        "Поиск по ключу или тегам...": "Search by key or tags...",
        "Только с LoRA": "LoRA only",
        "Только с ошибками": "Errors only",
        "Папка с файлами LoRA": "LoRA files folder",
        "+ Новый": "+ New",
        "Дублировать": "Duplicate",
        "Удалить": "Delete",
        "Ключ (имя персонажа)": "Key (character name)",
        "Теги (через запятую, как в промпте)": "Tags (comma-separated, as in the prompt)",
        "Привязанная LoRA (опционально)": "Attached LoRA (optional)",
        "Файл LoRA:": "LoRA file:",
        "Сила:": "Strength:",
        "Выберите персонажа слева или создайте нового": "Select a character on the left or create a new one",
        "{} персонажей": "{} characters",
        "Редактируется: {}": "Editing: {}",
        "Ключ '{}' уже занят — выберите другой": "Key '{}' is already taken — choose another",
        "⚠ отсутствуют теги": "⚠ no tags",
        "✓ теги выглядят корректно": "✓ tags look correct",
        "Удалить персонажа": "Delete character",
        "Удалить '{}' из базы?": "Delete '{}' from the database?",

        # --- PromptBuilderTab: LoraTableEditor ---
        "Сила": "Strength",
        "имя LoRA": "LoRA name",

        # --- PromptBuilderTab: outer tabs ---
        "Блоки промпта": "Prompt blocks",
        "Пресеты качества/источника": "Quality/source presets",
        "Негативные пресеты": "Negative presets",

        # --- PromptBuilderTab: block tree panel ---
        "Дерево блоков": "Block tree",
        "+ Группа": "+ Group",
        "+ Блок": "+ Block",
        "+ Вариант": "+ Option",
        "▲ Выше": "▲ Up",
        "▼ Ниже": "▼ Down",
        "⇪ Старые LoRA (lora/lora_strength → loras)": "⇪ Legacy LoRA (lora/lora_strength → loras)",
        "Выберите блок в дереве слева, либо создайте новый.": (
            "Select a block in the tree on the left, or create a new one."
        ),

        # --- PromptBuilderTab: group form ---
        "Группа": "Group",
        "Название вкладки:": "Tab name:",
        (
            "Группа объединяет вложенные блоки в отдельную под-вкладку\n"
            "в интерфейсе билдера. Добавляйте дочерние блоки кнопкой\n"
            "«+ Блок» / «+ Группа», когда группа выбрана в дереве."
        ): (
            "A group combines nested blocks into their own sub-tab\n"
            "in the builder UI. Add child blocks with the\n"
            "«+ Block» / «+ Group» button while the group is selected in the tree."
        ),

        # --- PromptBuilderTab: category (block) form ---
        "Блок (категория)": "Block (category)",
        "Название:": "Name:",
        "Тип:": "Type:",
        "☑ Множественный выбор": "☑ Multiple choice",
        "◉ Одиночный выбор": "◉ Single choice",
        "✎ Свободный текст": "✎ Free text",
        "Макс. случайных выборов (0 = не участвует в рандоме):": (
            "Max random picks (0 = excluded from randomizing):"
        ),
        "Вариант по умолчанию (метка):": "Default option (label):",
        "Обязательный блок (required)": "Required block (required)",
        "Placeholder (для свободного текста):": "Placeholder (for free text):",
        (
            "Варианты (options) добавляются кнопкой «+ Вариант»,\n"
            "когда этот блок выбран в дереве. Список — ниже, в дереве."
        ): (
            "Options are added with the «+ Option» button\n"
            "while this block is selected in the tree. The list is below, in the tree."
        ),

        # --- PromptBuilderTab: option form ---
        "Вариант блока": "Block option",
        "Метка (то, что видит пользователь):": "Label (what the user sees):",
        "Теги (через запятую):": "Tags (comma-separated):",
        "LoRA для этого варианта:": "LoRA for this option:",
        "(без метки)": "(no label)",

        # --- PromptBuilderTab: CRUD defaults/dialogs ---
        "Новая группа": "New group",
        "Новый блок": "New block",
        "Добавить вариант": "Add option",
        "Сначала выберите блок (категорию), в который нужно добавить вариант.": (
            "First select the block (category) to add the option to."
        ),
        "У блока типа «Свободный текст» нет вариантов.": "A «Free text» block has no options.",
        "Варианты можно добавлять только внутрь блока (не группы).": (
            "Options can only be added inside a block (not a group)."
        ),
        "Новый вариант": "New option",
        "группу": "the group",
        "блок": "the block",
        "вариант": "the option",
        "Удалить выбранный(ую) {}?": "Delete the selected {}?",
        "Старая архитектура LoRA": "Legacy LoRA format",
        (
            "Вариантов со старым форматом LoRA (\"lora\"/\"lora_strength\") не найдено — "
            "всё уже в новом формате \"loras\": [...]."
        ): (
            "No options with the legacy LoRA format (\"lora\"/\"lora_strength\") were found — "
            "everything is already in the new \"loras\": [...] format."
        ),
        "Найдена старая архитектура LoRA": "Legacy LoRA format found",
        (
            "Найдено {} вариант(ов) со старой архитектурой LoRA "
            "(поле \"lora\"/\"lora_strength\" вместо \"loras\": [...]).\n\n"
            "Расширение всё ещё умеет читать старый формат, но в этом редакторе "
            "такая LoRA не отображалась бы в списке варианта.\n\n"
            "Перенести все найденные варианты на новую архитектуру сейчас?"
        ): (
            "Found {} option(s) with the legacy LoRA format "
            "(\"lora\"/\"lora_strength\" field instead of \"loras\": [...]).\n\n"
            "The extension can still read the old format, but this editor "
            "wouldn't show that LoRA in the option's list.\n\n"
            "Migrate all found options to the new format now?"
        ),
        "Миграция завершена": "Migration complete",
        "Перенесено вариантов: {} из {}.\nНе забудьте сохранить файл (Ctrl+S).": (
            "Migrated options: {} of {}.\nDon't forget to save the file (Ctrl+S)."
        ),

        # --- PromptBuilderTab: quality/source preset editor ---
        "Префикс качества (quality_prefix)": "Quality prefix (quality_prefix)",
        "Источник (source)": "Source (source)",
        "Название": "Name",
        "Теги": "Tags",
        "Теги:": "Tags:",
        "+ Добавить": "+ Add",
        "Обновить": "Update",
        "По умолчанию:": "Default:",
        "Пресет": "Preset",
        "Пресет '{}' уже существует, используйте «Обновить».": (
            "Preset '{}' already exists — use «Update»."
        ),

        # --- PromptBuilderTab: negative presets editor ---
        "Негативные пресеты (negative_presets)": "Negative presets (negative_presets)",
        "Пресет по умолчанию (negative_default):": "Default preset (negative_default):",
        "Название пресета:": "Preset name:",
    }
}


class LocalizationManager(QObject):
    """Применяет язык интерфейса и держит его в синхроне с остальными
    приложениями комплекта (shared_language.py). Своего переключателя
    нет — язык выбирается в лаунчере, см. docstring модуля."""

    language_changed_externally = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("PromptConfigEditor", "PromptConfigEditor")
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

    def current_language(self):
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
        lang = self.current_language()
        return TRANSLATIONS.get(lang, {}).get(text, text)
