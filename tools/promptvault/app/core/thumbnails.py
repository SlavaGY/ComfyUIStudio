from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from app.config import THUMBNAIL_CACHE_DIR, THUMBNAIL_CACHE_MAX_BYTES, THUMBNAIL_MAX_AGE_DAYS
from app.utils import enforce_dir_size_limit


def make_thumb(image_path: str | Path, size: int = 256) -> Path | None:
    """Создаёт (или переиспользует уже созданную) миниатюру изображения.

    Кэш хранится в THUMBNAIL_CACHE_DIR (~/.promptvault/thumbnails) по
    хэшу полного пути к исходному файлу — не зависит от текущей
    рабочей директории, в отличие от прежнего относительного пути
    "cache/thumbnails".

    Возвращает путь к файлу миниатюры, либо None, если исходный файл
    не найден или не удалось прочитать как изображение.
    """

    image_path = Path(image_path)

    if not image_path.exists():
        return None

    THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # уникальный ключ из полного пути
    file_hash = hashlib.md5(
        str(image_path.resolve()).encode("utf-8")
    ).hexdigest()

    cache_path = THUMBNAIL_CACHE_DIR / f"{file_hash}.webp"

    if cache_path.exists():
        return cache_path

    pixmap = QPixmap(str(image_path))

    if pixmap.isNull():
        return None

    pixmap = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )

    pixmap.save(
        str(cache_path),
        "WEBP",
        90
    )

    return cache_path


def cleanup_thumbnail_cache(
    max_age_days: int = THUMBNAIL_MAX_AGE_DAYS,
    max_total_bytes: int = THUMBNAIL_CACHE_MAX_BYTES,
) -> None:
    """Автоочистка кэша миниатюр (задача 3.5).

    Сначала удаляются миниатюры старше max_age_days (по mtime — mtime
    миниатюры обновляется только при её создании, т.к. make_thumb
    переиспользует уже существующий файл без записи в него повторно;
    "старая" миниатюра — значит соответствующее изображение давно не
    открывали). Если после этого кэш всё ещё превышает
    max_total_bytes, дополнительно удаляются самые старые из
    оставшихся файлов, пока размер не опустится до лимита.

    Miниатюры пересоздаются по требованию (см. make_thumb), так что
    удаление отсюда безопасно — просто следующее открытие изображения
    пересоздаст его миниатюру заново.

    Вызывать один раз при старте приложения (см. app/main.py).
    """

    logger = logging.getLogger(__name__)

    if not THUMBNAIL_CACHE_DIR.exists():
        return

    now = time.time()
    max_age_seconds = max_age_days * 86400
    removed_by_age = 0

    for path in list(THUMBNAIL_CACHE_DIR.glob("*.webp")):

        try:
            age_seconds = now - path.stat().st_mtime
        except OSError:
            continue

        if age_seconds > max_age_seconds:
            try:
                path.unlink()
                removed_by_age += 1
            except OSError as e:
                logger.warning("Не удалось удалить старую миниатюру %s: %s", path, e)

    removed_by_size = enforce_dir_size_limit(
        THUMBNAIL_CACHE_DIR, "*.webp", max_total_bytes
    )

    if removed_by_age or removed_by_size:
        logger.info(
            "Автоочистка кэша миниатюр: удалено %d по возрасту (>%d дней), %d по размеру",
            removed_by_age, max_age_days, removed_by_size
        )
