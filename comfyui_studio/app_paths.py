"""Пути к папкам данных приложения (этап R2).

Раньше каждый модуль сам собирал ``%APPDATA%\\ComfyUIStudio`` (или
``...\\ComfyUILauncher``) через ``os.environ.get("APPDATA", ...)`` —
в восьми местах. Теперь путь считается здесь, в одном месте.

Модуль намеренно использует ТОЛЬКО стандартную библиотеку: его
импортируют и процессы без Qt (Remote, Imagine), и лаунчер.

Какие каталоги возвращаются (значения по умолчанию — те же, что были
до R2, поэтому данные пользователей и спаренные телефоны не затронуты):

* ``studio_data_dir()`` — ``%APPDATA%\\ComfyUIStudio``: общие файлы
  комплекта (тема, язык, настройки генератора промптов, история,
  устройства Remote, SSH-конфиг, логи генератора) и ``imagine\\``;
* ``launcher_data_dir()`` — ``%APPDATA%\\ComfyUILauncher``: ``config.json``
  лаунчера, профиль WebEngine, ``launcher.log``;
* ``imagine_data_dir()`` — настройки и загрузки Imagine.

Слияние двух папок в одну НЕ делается: для существующих пользователей
это миграция ``config.json``, профиля WebEngine и ``remote_devices.json``
(устройства уже спарены) — отдельное решение, не часть этапа R2.

Переопределение через переменные окружения (для тестов, портативной
установки и отладки; пустое значение считается «не задано»):

* ``COMFYUI_STUDIO_DATA_DIR`` — заменяет ``studio_data_dir()``;
* ``COMFYUI_LAUNCHER_DATA_DIR`` — заменяет ``launcher_data_dir()``;
* ``IMAGINE_DATA_DIR`` — заменяет ``imagine_data_dir()`` (как и раньше).

Подпроцессы Remote и Imagine наследуют окружение лаунчера, поэтому
переопределение действует на весь комплект сразу.

Значения читаются в момент вызова, но модули-потребители запоминают
результат в своих константах при импорте (``SHARED_DIR``,
``DEVICE_STORE_PATH`` и т. д.) — на эти константы опираются тесты, и они
по-прежнему подменяются через monkeypatch. Поэтому переменные нужно
задавать ДО импорта этих модулей (то есть до запуска приложения).

Сюда НЕ относится кэш CUDA-ядер драйвера NVIDIA
(``%APPDATA%\\NVIDIA\\ComputeCache``, imagine/backend/promptgen_diag.py) — это
чужая папка, а не данные приложения.
"""
from __future__ import annotations

import os

STUDIO_DATA_DIR_ENV = "COMFYUI_STUDIO_DATA_DIR"
LAUNCHER_DATA_DIR_ENV = "COMFYUI_LAUNCHER_DATA_DIR"
IMAGINE_DATA_DIR_ENV = "IMAGINE_DATA_DIR"

STUDIO_DIR_NAME = "ComfyUIStudio"
LAUNCHER_DIR_NAME = "ComfyUILauncher"
IMAGINE_SUBDIR_NAME = "imagine"


def _override(env_name: str) -> str | None:
    value = os.environ.get(env_name)
    return value if value else None


def appdata_root() -> str:
    """Корень пользовательских данных: ``%APPDATA%``, а там, где его нет
    (не Windows), домашняя папка."""
    return os.environ.get("APPDATA", os.path.expanduser("~"))


def studio_data_dir() -> str:
    """Общая папка данных комплекта (``%APPDATA%\\ComfyUIStudio``)."""
    return _override(STUDIO_DATA_DIR_ENV) or os.path.join(appdata_root(), STUDIO_DIR_NAME)


def launcher_data_dir() -> str:
    """Папка данных лаунчера (``%APPDATA%\\ComfyUILauncher``)."""
    return _override(LAUNCHER_DATA_DIR_ENV) or os.path.join(appdata_root(), LAUNCHER_DIR_NAME)


def imagine_data_dir() -> str:
    """Папка настроек и загрузок Imagine: ``IMAGINE_DATA_DIR``, иначе
    ``imagine\\`` внутри общей папки комплекта."""
    return _override(IMAGINE_DATA_DIR_ENV) or os.path.join(studio_data_dir(), IMAGINE_SUBDIR_NAME)
