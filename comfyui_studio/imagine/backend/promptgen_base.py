"""
promptgen_base.py
Листовой модуль генератора промптов: константы, исключения и мелкие
чистые помощники без зависимостей от остальных модулей promptgen_*.

Вынесено из promptgen.py на этапе R10 плана рефакторинга без изменения
кода. Импортировать отсюда можно из любого promptgen_*-модуля, не боясь
циклов: этот файл не импортирует ничего из пакета.
"""

from __future__ import annotations

import os
from typing import Optional

MAX_TOKENS = 8192
# Загрузка большой модели с медленного диска может занять минуты.
LOAD_TIMEOUT_S = 600
# Таймаут одной операции чтения сокета при стриминге ответа. Между
# токенами пауза может быть долгой только пока обрабатывается длинный
# запрос с картинкой -- запас с избытком.
STREAM_TIMEOUT_S = 1800
# data:-URL картинки: 25 МБ бинарных данных ~ 34 МБ в base64. Фронтенд
# уменьшает картинку заранее, так что это лишь защита от заведомо
# неадекватных запросов.
MAX_IMAGE_DATA_URL_LEN = 34 * 1024 * 1024

_MIB = 1024 * 1024


class PromptGenError(Exception):
    """Ожидаемая ошибка с готовым для пользователя текстом и HTTP-кодом
    (используется эндпоинтами в main.py)."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class _Cancelled(Exception):
    pass


def _clean_image_name(name: Optional[str]) -> str:
    """Имя файла картинки из браузера: только последний компонент пути
    (на всякий случай -- браузеры и так отдают без пути), без
    управляющих символов, не длиннее 255."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:255]


def _file_mb(path: str) -> int:
    try:
        return os.path.getsize(path) // _MIB
    except (OSError, TypeError):
        return 0


def _error_text(err) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)
