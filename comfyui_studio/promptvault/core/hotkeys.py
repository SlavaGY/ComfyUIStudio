"""Настраиваемые горячие клавиши (задача "Настраиваемые горячие
клавиши" в TODO.md).

Раньше несколько горячих клавиш (F11, R, стрелки) были жёстко зашиты
в MainWindow.keyPressEvent — ни посмотреть, ни поменять их без правки
кода было нельзя. Здесь они (и ряд новых, ранее доступных только
кликом по кнопке/пункту меню) переезжают в единый реестр действий:
каждое действие получает id, комбинацию по умолчанию и — через
QShortcut, создаваемый в MainWindow._register_hotkeys — реальную
привязку к обработчику. Сама привязка "какому действию какая сейчас
клавиша назначена" хранится здесь, персистентно, тем же способом, что
тема (см. app/themes/theme_manager.py) — через QSettings("PromptVault",
"PromptVault"), просто в отдельном разделе ключей ("hotkeys/...").

Этот модуль ничего не знает про конкретные обработчики (open_folder,
toggle_favorite и т.п.) — только про id действий и назначенные им
комбинации. Сама привязка id -> обработчик — в MainWindow
(_register_hotkeys), сама привязка id -> переводимая подпись — в
SettingsWindow (_hotkey_label), как и EMBEDDING_MODELS в app/config.py
хранит только нейтральные ключи ("quality": "excellent"), а готовый
текст на нужном языке собирает UI-слой.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

# action_id -> комбинация по умолчанию, в формате, который понимает
# QKeySequence (человекочитаемые строки вида "Ctrl+Shift+E" — тот же
# формат, что возвращает QKeySequence.toString(), так что его можно
# сохранять и читать обратно как есть, без отдельного парсера).
#
# Порядок ключей здесь же задаёт порядок отображения строк в окне
# настроек (см. SettingsWindow._build_hotkeys_group).
DEFAULT_HOTKEYS: dict[str, str] = {
    "open_folder": "Ctrl+O",
    "focus_search": "Ctrl+F",
    "toggle_filters": "Ctrl+Shift+F",
    "toggle_sort": "Ctrl+Shift+S",
    "show_statistics": "Ctrl+I",
    "show_settings": "Ctrl+,",
    "toggle_favorite": "F",
    "edit_metadata": "Ctrl+E",
    "add_tags": "Ctrl+T",
    "export_json": "Ctrl+Shift+E",
    "export_zip": "Ctrl+Shift+Z",
    "open_json_externally": "Ctrl+J",
    "open_in_file_manager": "Ctrl+Shift+J",
    "delete_from_library": "Delete",
    "delete_files": "Shift+Delete",
    "toggle_fullscreen": "F11",
    "reset_image_view": "R",
    "next_image": "Right",
    "previous_image": "Left",
}

# фиксированный порядок id действий (Python-словари сохраняют порядок
# вставки, но список отдельно — чтобы порядок отображения не зависел
# от того, что кто-то потом будет итерировать DEFAULT_HOTKEYS.items()
# в другом месте и case-by-case решать, важен ли там порядок)
HOTKEY_ACTIONS: list[str] = list(DEFAULT_HOTKEYS.keys())


class HotkeyManager:
    """Тонкая обёртка над QSettings для горячих клавиш — аналог
    ThemeManager.current_theme()/apply_theme(), просто на действие, а
    не на одно значение. Создание дёшево (см. docstring AppSettings —
    тот же QSettings-паттерн)."""

    def __init__(self) -> None:

        self._settings = QSettings("PromptVault", "PromptVault")

    # --------------------------------------------------

    def sequence_text(self, action_id: str) -> str:
        """Текущая комбинация действия как строка (то, что реально
        хранится в QSettings/по умолчанию) — используется и для
        сравнения (is_default/find_conflict), и как то, что показывать
        в QKeySequenceEdit."""

        default = DEFAULT_HOTKEYS.get(action_id, "")

        return str(self._settings.value(f"hotkeys/{action_id}", default))

    def sequence(self, action_id: str) -> QKeySequence:

        return QKeySequence(self.sequence_text(action_id))

    def set_sequence(self, action_id: str, sequence: QKeySequence) -> None:

        self._settings.setValue(f"hotkeys/{action_id}", sequence.toString())

    def reset(self, action_id: str) -> None:
        """Возвращает действие к комбинации по умолчанию, удаляя
        пользовательское переопределение из QSettings (а не просто
        перезаписывая его значением по умолчанию) — так find_conflict/
        is_default остаются корректными даже если DEFAULT_HOTKEYS
        когда-нибудь поменяется в новой версии приложения."""

        self._settings.remove(f"hotkeys/{action_id}")

    def reset_all(self) -> None:

        for action_id in HOTKEY_ACTIONS:
            self.reset(action_id)

    def is_default(self, action_id: str) -> bool:

        return self.sequence_text(action_id) == DEFAULT_HOTKEYS.get(action_id, "")

    # --------------------------------------------------

    def find_conflict(self, action_id: str, sequence: QKeySequence) -> str | None:
        """Если sequence уже назначена другому действию — возвращает
        id этого действия (для предупреждения в UI), иначе None.
        Пустая комбинация (снятие хоткея — задача: настраиваемые
        горячие клавиши, "снять" тоже должно быть возможно) конфликтов
        не имеет: несколько действий одновременно могут быть без
        хоткея."""

        if sequence.isEmpty():
            return None

        text = sequence.toString()

        for other_id in HOTKEY_ACTIONS:

            if other_id == action_id:
                continue

            if self.sequence_text(other_id) == text:
                return other_id

        return None
