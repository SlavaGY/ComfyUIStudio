"""Автозапуск ComfyUI Studio при входе в Windows -- этап 4 дорожной
карты ("Единое дерево настроек" -> General -> Startup).

Реализовано через HKEY_CURRENT_USER\\...\\Run (не через Планировщик
задач/службу) -- это тот же механизм, что используют большинство
трей-приложений уровня "лаунчер": не требует прав администратора и
переживает переустановку без отдельной миграции.

Работает только на Windows -- is_supported() возвращает False на
других ОС, и ui/settings/general_page.py показывает вместо чекбокса
поясняющую надпись, а не пытается писать в несуществующий реестр.
"""

from __future__ import annotations

import os
import sys

from .constants import APP_NAME, PROJECT_ROOT
from .logging_setup import log

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "ComfyUIStudio"


def is_supported() -> bool:

    return sys.platform == "win32"


def _command_line() -> str:
    """Команда, которую Windows выполнит при входе в систему.

    В собранном виде (PyInstaller, sys.frozen) -- сам .exe напрямую.
    При запуске из исходников -- pythonw.exe (без консольного окна,
    в отличие от обычного python.exe) + абсолютный путь к main.py в
    корне репозитория (см. PROJECT_ROOT в constants.py)."""

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        # venv без pythonw.exe (случается на некоторых сборках Python) --
        # лучше автозапуск с мелькающей консолью, чем автозапуск, который
        # вообще не находит интерпретатор.
        pythonw = sys.executable
    main_py = os.path.join(PROJECT_ROOT, "main.py")
    return f'"{pythonw}" "{main_py}"'


def is_enabled() -> bool:
    """Читает состояние НАПРЯМУЮ из реестра, а не из своего кэша/cfg --
    реестр тут единственный источник истины (пользователь мог убрать
    автозапуск средствами самой Windows, в обход этого приложения, и
    следующий показ настроек должен это отразить)."""

    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Возвращает (успех, сообщение_об_ошибке). Сообщение пустое при
    успехе -- вызывающий код (см. general_page.py) показывает его
    пользователю только при неудаче."""

    if not is_supported():
        return False, "Автозапуск поддерживается только в Windows."

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command_line())
                log.info("Автозапуск %s включён: %s", APP_NAME, _command_line())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
                log.info("Автозапуск %s выключен", APP_NAME)
        return True, ""
    except OSError as e:
        log.exception("Не удалось изменить автозапуск")
        return False, str(e)
