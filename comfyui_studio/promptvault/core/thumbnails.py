from __future__ import annotations

import hashlib
import logging
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader

from comfyui_studio.promptvault.config import (
    THUMBNAIL_CACHE_DIR,
    THUMBNAIL_CACHE_MAX_BYTES,
    THUMBNAIL_MAX_AGE_DAYS,
)
from comfyui_studio.promptvault.utils import enforce_dir_size_limit


def make_thumb(image_path: str | Path, size: int = 256, *, cache_dir: Path | None = None) -> Path | None:
    """Создаёт (или переиспользует уже созданную) миниатюру изображения.

    Кэш хранится в THUMBNAIL_CACHE_DIR (~/.promptvault/thumbnails) по
    хэшу пути, версии исходного файла и размера миниатюры — не зависит от текущей
    рабочей директории, в отличие от прежнего относительного пути
    "cache/thumbnails".

    Возвращает путь к файлу миниатюры, либо None, если исходный файл
    не найден или не удалось прочитать как изображение.
    """

    image_path = Path(image_path)

    if not image_path.exists():
        return None

    cache_dir = cache_dir if cache_dir is not None else THUMBNAIL_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    metadata = image_path.stat()
    # Размер и версия исходника входят в ключ: обновление картинки или
    # запрос другого размера не должны возвращать устаревшую миниатюру.
    file_hash = hashlib.md5(
        f"{image_path.resolve()}:{metadata.st_mtime_ns}:{metadata.st_size}:{size}".encode("utf-8")
    ).hexdigest()

    cache_path = cache_dir / f"{file_hash}.webp"

    if cache_path.exists():
        return cache_path

    reader = QImageReader(str(image_path))
    reader.setAutoTransform(True)
    original_size = reader.size()
    if original_size.isValid():
        reader.setScaledSize(original_size.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()

    if image.isNull():
        return None

    image = image.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )

    partial = cache_path.with_suffix(f".{uuid.uuid4().hex}.part.webp")
    try:
        if not image.save(str(partial), quality=90):
            return None
        partial.replace(cache_path)
    finally:
        partial.unlink(missing_ok=True)

    return cache_path


def cleanup_thumbnail_cache(
    max_age_days: int = THUMBNAIL_MAX_AGE_DAYS,
    max_total_bytes: int = THUMBNAIL_CACHE_MAX_BYTES,
) -> None:
    """Автоочистка кэша миниатюр (задача 3.5).

    Сначала удаляются миниатюры старше max_age_days (по mtime — mtime
    миниатюры обновляется только при её создании, т.к. make_thumb
    переиспользует уже существующий файл без записи в него повторно;
    возраст считается от создания, а не последнего просмотра).
    Если после этого кэш всё ещё превышает
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
