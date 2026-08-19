"""Настройки Prompt Builder, управляемые извне -- из единого дерева
настроек ComfyUI Studio (см. comfyui_studio/launcher/ui/settings/
prompt_builder_page.py), а не из самого редактора.

До этого этапа папка расширения выбиралась и запоминалась изнутри
редактора (меню "Файл -> Открыть папку расширения...", хранилась как
"последняя использованная папка" — см. _last_folder()/_set_last_folder()
в старой версии main.py). Верхняя панель меню (Файл/Справка) убрана —
её функции разошлись по двум местам: выбор папок и число бэкапов теперь
настраиваются здесь (и в едином дереве настроек лаунчера), а открытие/
сохранение файлов осталось в самом редакторе как две кнопки на
тулбаре — см. main.py, _build_toolbar()/open_existing_file()/save_all().

Тот же QSettings-паттерн, что и у lora_combo.py (get_lora_folder/
set_lora_folder) — тот же файл настроек ("PromptConfigEditor",
"PromptConfigEditor"), просто другие ключи. get_lora_folder/
set_lora_folder НЕ продублированы здесь — импортируются из lora_combo.py
как есть, чтобы не было двух источников истины для одного и того же
ключа QSettings.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

_EXTENSION_FOLDER_KEY = "last_folder"
_BACKUP_KEEP_KEY = "backup_keep"

# Совпадает с BACKUP_KEEP, который был раньше жёстко зашит в
# json_store.py -- сохраняем то же поведение по умолчанию для уже
# существующих пользователей, которые ни разу не открывали эту
# настройку.
DEFAULT_BACKUP_KEEP = 10


def _settings() -> QSettings:
    # Явно те же org/app, что и у ThemeManager/MainWindow/
    # LocalizationManager/lora_combo.py — один файл настроек на весь
    # инструмент, см. подробный комментарий в lora_combo.py про то,
    # почему это должно быть именно так, а не голый QSettings().
    return QSettings("PromptConfigEditor", "PromptConfigEditor")


def get_extension_folder() -> str:
    """Папка с characters.json/prompt_builder_config.json — Prompt
    Builder подхватывает файлы из неё автоматически при каждом
    открытии (см. main.py, _load_from_extension_folder()). Ключ
    QSettings ("last_folder") оставлен как есть ради обратной
    совместимости — раньше он совмещал в себе смысл "последняя
    использованная папка"; теперь, когда способа сменить папку изнутри
    самого редактора больше нет (см. докстринг модуля), он полностью
    стал этой единственной настройкой."""

    return str(_settings().value(_EXTENSION_FOLDER_KEY, "", type=str) or "")


def set_extension_folder(path: str) -> None:
    _settings().setValue(_EXTENSION_FOLDER_KEY, path)


def get_backup_keep() -> int:
    """Сколько последних резервных копий (*.json.bak-YYYYMMDD-HHMMSS)
    хранить на каждый файл — см. json_store.py, _make_backup(). Читается
    заново при каждом сохранении (а не кэшируется), так что изменение
    этой настройки применяется сразу же, без перезапуска Prompt
    Builder."""

    value = _settings().value(_BACKUP_KEEP_KEY, DEFAULT_BACKUP_KEEP, type=int)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKUP_KEEP
    return value if value >= 0 else DEFAULT_BACKUP_KEEP


def set_backup_keep(value: int) -> None:
    _settings().setValue(_BACKUP_KEEP_KEY, max(0, int(value)))
