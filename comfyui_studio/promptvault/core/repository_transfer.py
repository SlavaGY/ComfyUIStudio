"""Экспорт генераций в ZIP и импорт пользовательских отметок
(избранного, рейтинга и тегов) из другой БД."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from comfyui_studio.promptvault.core.repository_query import QueryMixin

logger = logging.getLogger(__name__)


class TransferMixin(QueryMixin):
    # ------------------------------------------------------------
    # экспорт и импорт

    def export_generations_zip(
        self,
        generation_ids: list[int],
        zip_path: str | Path,
        include_previews: bool = True,
    ) -> int:
        """Экспортирует выбранные генерации (JSON + все связанные
        изображения +, опционально, PNG/WEBP-превью первого изображения
        каждой генерации) в один ZIP-архив.

        Каждая генерация кладётся в архиве в собственную подпапку
        ``<id генерации>/`` (JSON + изображения рядом, как на диске),
        плюс общая папка ``previews/`` с одним небольшим превью на
        генерацию — удобно для быстрого просмотра содержимого архива
        без распаковки всех полноразмерных изображений.

        Возвращает количество успешно экспортированных генераций (запись
        считается экспортированной, если удалось записать хотя бы её
        JSON-файл — отсутствие отдельных изображений на диске не
        прерывает экспорт остальных, только логируется).
        """

        import zipfile

        from comfyui_studio.promptvault.core.thumbnails import make_thumb

        zip_path = Path(zip_path)
        exported = 0

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:

            for gen_id in generation_ids:

                generation = self.get_generation(gen_id)

                if generation is None or not generation.path.exists():
                    logger.warning(
                        "export_generations_zip: генерация id=%s пропущена "
                        "(не найдена в БД или файл отсутствует на диске)",
                        gen_id
                    )
                    continue

                entry_dir = f"{gen_id}"

                try:
                    archive.write(generation.path, f"{entry_dir}/{generation.path.name}")
                except OSError as e:
                    logger.error(
                        "export_generations_zip: не удалось добавить %s: %s",
                        generation.path, e
                    )
                    continue

                for image in generation.images:

                    image_path = generation.directory / image.file

                    if not image_path.exists():
                        logger.warning(
                            "export_generations_zip: изображение %s "
                            "(id=%s) отсутствует на диске — пропущено",
                            image_path, gen_id
                        )
                        continue

                    try:
                        archive.write(image_path, f"{entry_dir}/{image.file}")
                    except OSError as e:
                        logger.error(
                            "export_generations_zip: не удалось добавить "
                            "изображение %s: %s", image_path, e
                        )

                if include_previews and generation.images:

                    preview_source = generation.directory / generation.images[0].file
                    thumb_path = make_thumb(preview_source) if preview_source.exists() else None

                    if thumb_path is not None:
                        archive.write(
                            thumb_path, f"previews/{gen_id}{thumb_path.suffix}"
                        )

                exported += 1

        logger.info(
            "Экспортировано в ZIP %d из %d генераций: %s",
            exported, len(generation_ids), zip_path
        )

        return exported

    def import_user_data(self, other_db_path: str | Path) -> tuple[int, int]:
        """Импортирует избранное/рейтинг из ДРУГОЙ базы PromptVault
        (например, с другой машины) в текущую.

        Сопоставление идёт по identity (timestamp, generation_time) —
        как и вся остальная работа с идентичностью генераций в этом
        приложении (см. app/core/database.py) — а НЕ по id (id —
        просто автоинкремент внутри своей БД, между разными базами
        совпадений быть не может).

        Для генераций, присутствующих в обеих базах: избранное — ИЛИ
        (True, если было True хотя бы в одной), рейтинг — берётся
        БОЛЬШИЙ из двух (то же правило слияния, что и при миграции
        схемы БД — см. _migrate_path_identity_to_timestamp_identity),
        чтобы импорт с другой машины никогда не понижал уже
        проставленную локально оценку.

        Возвращает (количество затронутых записей, количество записей
        из другой БД, для которых не нашлось соответствия по identity
        в текущей БД — они молча пропускаются, т.к. создавать
        generations-записи "с нуля" без исходного JSON-файла нельзя).
        """

        other_db_path = Path(other_db_path)

        if not other_db_path.exists():
            logger.error("import_user_data: файл БД не найден: %s", other_db_path)
            return 0, 0

        other_conn = sqlite3.connect(str(other_db_path))

        try:
            other_rows = other_conn.execute(
                """SELECT g.timestamp, g.generation_time, u.favorite, u.rating
                   FROM user_data u
                   JOIN generations g ON g.id = u.generation_id
                   WHERE u.favorite = 1 OR u.rating > 0"""
            ).fetchall()
        finally:
            other_conn.close()

        conn = self._conn
        updated = 0
        unmatched = 0

        for timestamp, generation_time, favorite, rating in other_rows:

            local_row = conn.execute(
                "SELECT id FROM generations WHERE timestamp = ? AND generation_time = ?",
                (timestamp, generation_time)
            ).fetchone()

            if local_row is None:
                unmatched += 1
                continue

            gen_id = local_row[0]

            conn.execute(
                """INSERT INTO user_data (generation_id, favorite, rating)
                   VALUES (?, ?, ?)
                   ON CONFLICT(generation_id) DO UPDATE SET
                       favorite = MAX(favorite, excluded.favorite),
                       rating = MAX(rating, excluded.rating)""",
                (gen_id, int(bool(favorite)), int(rating or 0))
            )

            updated += 1

        conn.commit()

        logger.info(
            "import_user_data: обновлено %d записей из %s (без соответствия: %d)",
            updated, other_db_path, unmatched
        )

        return updated, unmatched
