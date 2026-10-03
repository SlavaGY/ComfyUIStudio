"""Единая точка вычисления папок пользовательских данных комплекта.

До этого модуля одно и то же выражение

    os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio")

было скопировано в семи местах (shared_theme, shared_language,
shared_promptgen, remote/device_store, remote/ssh_config_store,
imagine/backend/config_store, launcher/core/constants). Здесь оно собрано в
одну функцию, и — главное — появляется штатный способ увести данные из
профиля Windows:

    set COMFYUI_STUDIO_DATA_DIR=D:\\ComfyUIStudioData

Переменная переопределяет ОБА каталога комплекта сразу:

    <корень>\\ComfyUILauncher   — config.json, логи, профиль Chromium
    <корень>\\ComfyUIStudio     — общие тема/язык, генератор промптов,
                                  история запросов, Remote (устройства,
                                  SSH-конфиг), данные Imagine
    <корень>\\.promptvault      — база PromptVault, его логи и миниатюры
                                  (у PromptVault свой исторический путь,
                                  поэтому без переменной он остаётся
                                  прежним: ~/.promptvault)

Зачем это нужно на практике (живой случай 2026-10-03): на машине
пользователя не-администраторский `python.exe` не мог писать НИКУДА в
`C:\\Users\\<пользователь>\\...` — ни `AppData\\Roaming`, ни `AppData\\Local`,
ни `Documents`, — при том что `PowerShell`/`cmd` и тот же `python.exe`,
запущенный от администратора, писали туда нормально, а права на папки были
обычные. Из-за этого молча не сохранялся `config.json` (его запись глотает
исключения) и падал запуск из-за файла лога (см. `logging_setup` и
`mem_diagnostics`). Запись при этом работала в рабочем каталоге на другом
диске — то есть данные достаточно было увести из профиля.

Значение читается при вызове (а не один раз при импорте), поэтому тесты
могут подставлять переменную без перезапуска процесса; модульные
константы вида `SHARED_DIR`/`APP_DIR`, как и раньше, вычисляются один раз
при импорте своего модуля.
"""

from __future__ import annotations

import os

#: Имя переменной окружения, которой можно увести данные комплекта.
DATA_DIR_ENV = "COMFYUI_STUDIO_DATA_DIR"


def data_override() -> str:
    """Значение COMFYUI_STUDIO_DATA_DIR как есть ("" -- переменная не задана).

    Отдельно от data_root() нужна тем модулям, у которых исторически свой
    путь по умолчанию (PromptVault использует ~/.promptvault): они должны
    реагировать на переменную, но не менять поведение без неё.
    """
    return os.environ.get(DATA_DIR_ENV, "").strip()


def data_root() -> str:
    """Корень пользовательских данных: переопределение либо %APPDATA%.

    На не-Windows (или там, где APPDATA не задан) — домашний каталог, как
    и было в исходных копиях этого выражения.
    """
    override = data_override()
    if override:
        return override
    return os.environ.get("APPDATA", os.path.expanduser("~"))


def studio_dir() -> str:
    """Общая папка комплекта (тема, язык, генератор промптов, Remote, Imagine)."""
    return os.path.join(data_root(), "ComfyUIStudio")


def launcher_dir() -> str:
    """Папка лаунчера (config.json, логи, профиль Chromium)."""
    return os.path.join(data_root(), "ComfyUILauncher")
