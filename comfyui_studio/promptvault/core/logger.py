"""Настройка логирования приложения.

Вызывается один раз при старте (см. app/main.py). Все модули далее
просто делают `logging.getLogger(__name__)` и пишут через него —
отдельно настраивать логирование в каждом модуле не нужно.
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from comfyui_studio.promptvault.config import LOG_DIR, LOG_DIR_MAX_BYTES, LOG_MAX_AGE_DAYS
from comfyui_studio.promptvault.utils import enforce_dir_size_limit


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает корневой логгер: запись в файл + вывод в stdout.

    Файл лога создаётся заново на каждый запуск приложения (имя
    включает дату и время старта), старые логи не перезаписываются и
    не ротируются автоматически — это осознанно просто, чтобы не
    тащить лишнюю зависимость ради ротации; при необходимости логи в
    ~/.promptvault/logs можно чистить вручную.

    Файловый хендлер -- best-effort: если каталог логов недоступен
    (нет прав, занят, профиль только для чтения), логирование остаётся
    консольным, а не роняет открытие окна PromptVault. Тот же принцип,
    что и в launcher/core/logging_setup.py и mem_diagnostics.py --
    из-за них запуск комплекта уже один раз падал трейсбеком вместо
    окна (см. историю правок от 2026-10-03).
    """

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    handlers: list[logging.Handler] = [console_handler]
    log_file: Path | None = None
    file_error = None

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"promptvault_{datetime.now():%Y%m%d_%H%M%S}.log"
        handlers.insert(0, logging.FileHandler(log_file, encoding="utf-8"))
    except OSError as exc:
        file_error = exc

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
    )

    log = logging.getLogger(__name__)
    if file_error is not None:
        log.warning(
            "Каталог логов недоступен (%s) -- пишу только в консоль: %s",
            LOG_DIR,
            file_error,
        )
    else:
        log.info("Логирование запущено, файл: %s", log_file)


def cleanup_old_logs(
    max_age_days: int = LOG_MAX_AGE_DAYS,
    max_total_bytes: int = LOG_DIR_MAX_BYTES,
) -> None:
    """Автоочистка старых логов (задача 3.5).

    Сначала удаляются файлы старше max_age_days. Если после этого
    папка логов всё ещё превышает max_total_bytes, дополнительно
    удаляются самые старые (по mtime) из оставшихся файлов, пока
    размер не опустится до лимита — защищает от неограниченного роста
    даже при очень частых запусках приложения в пределах max_age_days.

    Вызывать один раз при старте, ПОСЛЕ setup_logging() — файл лога
    текущего запуска только что создан (age ~0), так что сам себя не
    удалит.
    """

    log = logging.getLogger(__name__)

    if not LOG_DIR.exists():
        return

    now = time.time()
    max_age_seconds = max_age_days * 86400
    removed_by_age = 0

    for path in list(LOG_DIR.glob("*.log")):

        try:
            age_seconds = now - path.stat().st_mtime
        except OSError:
            continue

        if age_seconds > max_age_seconds:
            try:
                path.unlink()
                removed_by_age += 1
            except OSError as e:
                log.warning("Не удалось удалить старый лог %s: %s", path, e)

    removed_by_size = enforce_dir_size_limit(LOG_DIR, "*.log", max_total_bytes)

    if removed_by_age or removed_by_size:
        log.info(
            "Автоочистка логов: удалено %d по возрасту (>%d дней), %d по размеру",
            removed_by_age, max_age_days, removed_by_size
        )
