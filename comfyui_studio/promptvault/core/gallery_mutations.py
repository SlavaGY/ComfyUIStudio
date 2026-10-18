"""Изменения галереи: избранное, рейтинг, теги, удаление,
экспорт, импорт и добавление файлов перетаскиванием."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from comfyui_studio.promptvault.core.gallery_base import GalleryBase

logger = logging.getLogger(__name__)


class MutationsMixin(GalleryBase):
    # ------------------------------------------------------------
    # избранное / рейтинг (одна генерация)

    def toggle_favorite(self, generation_id: int) -> None:
        """Переключает избранное у генерации с данным id.

        Персистентность — одна быстрая точечная SQL-команда (не блокирует
        UI). Пересортировка (избранные поднимаются наверх) не выполняется
        немедленно, а планируется через debounce — см. _refresh_timer.
        """

        generation = self._find_by_id(generation_id)

        if generation is None:
            logger.warning("toggle_favorite: генерация id=%s не найдена", generation_id)
            return

        generation.favorite = not generation.favorite

        logger.debug(
            "Избранное для id=%s -> %s", generation_id, generation.favorite
        )

        self._repository.set_favorite(generation_id, generation.favorite)
        self._refresh_timer.start()

    def set_rating(self, generation_id: int, value: int) -> None:
        """Выставляет рейтинг (0-5) генерации с данным id."""

        generation = self._find_by_id(generation_id)

        if generation is None:
            logger.warning("set_rating: генерация id=%s не найдена", generation_id)
            return

        generation.rating = value

        logger.debug("Рейтинг для id=%s -> %s", generation_id, value)

        self._repository.set_rating(generation_id, value)
        self._refresh_timer.start()

    # ------------------------------------------------------------
    # пользовательские теги

    def set_custom_tags(self, generation_id: int, tags: list[str]) -> None:
        """Полностью заменяет пользовательские теги одной генерации и
        обновляет её in-memory представление."""

        generation = self._find_by_id(generation_id)

        self._repository.set_custom_tags(generation_id, tags)
        refreshed_tags = self._repository.get_custom_tags(generation_id)

        if generation is not None:
            generation.custom_tags = refreshed_tags

        logger.debug("Теги для id=%s -> %s", generation_id, refreshed_tags)

        self._invalidate_available_cache()
        self._refresh_timer.start()

    def add_tags_to_generations(self, generation_ids: list[int], tags: list[str]) -> None:
        """Массовое добавление тега(ов) сразу нескольким генерациям — в
        отличие от set_custom_tags, ОБЪЕДИНЯЕТ новые теги с уже
        существующими, а не заменяет их."""

        count = 0

        for gid in generation_ids:
            generation = self._find_by_id(gid)

            existing = (
                generation.custom_tags if generation is not None
                else self._repository.get_custom_tags(gid)
            )

            self._repository.set_custom_tags(gid, [*existing, *tags])
            refreshed_tags = self._repository.get_custom_tags(gid)

            if generation is not None:
                generation.custom_tags = refreshed_tags

            count += 1

        logger.info(
            "Массовое добавление тегов %s: %d генераций", tags, count
        )

        self._invalidate_available_cache()
        self._refresh_timer.start()

    # ------------------------------------------------------------
    # массовые операции

    def set_multiple_favorite(self, generation_ids: list[int], value: bool) -> None:
        """Проставляет/снимает избранное сразу у нескольких генераций."""

        count = 0

        for gid in generation_ids:
            generation = self._find_by_id(gid)

            if generation is None:
                continue

            generation.favorite = value
            self._repository.set_favorite(gid, value)
            count += 1

        logger.info(
            "Массовое изменение избранного: %d генераций -> %s", count, value
        )

        self._refresh_timer.start()

    def set_multiple_rating(self, generation_ids: list[int], value: int) -> None:
        """Выставляет одинаковый рейтинг сразу нескольким генерациям."""

        count = 0

        for gid in generation_ids:
            generation = self._find_by_id(gid)

            if generation is None:
                continue

            generation.rating = value
            self._repository.set_rating(gid, value)
            count += 1

        logger.info(
            "Массовое изменение рейтинга: %d генераций -> %s", count, value
        )

        self._refresh_timer.start()

    def delete_generations(self, generation_ids: list[int], delete_files: bool = False) -> int:
        """Удаляет несколько генераций из библиотеки (и, если
        delete_files=True, физически с диска). Возвращает количество
        успешно удалённых записей."""

        deleted_ids = set()

        for gid in generation_ids:
            try:
                if self._repository.delete_generation(gid, delete_files=delete_files):
                    deleted_ids.add(gid)
            except OSError as e:
                logger.error("Ошибка при удалении генерации id=%s: %s", gid, e)
                self.error_occurred.emit(f"Не удалось удалить генерацию id={gid}: {e}")

        if deleted_ids:
            self._invalidate_available_cache()

            if (
                self.current_generation is not None
                and self.current_generation.id in deleted_ids
            ):
                self.current_generation = None

            self.apply_filters()

        logger.info(
            "Удалено %d из %d выбранных генераций (файлы %s)",
            len(deleted_ids), len(generation_ids),
            "удалены" if delete_files else "оставлены"
        )

        return len(deleted_ids)

    def export_generations(self, generation_ids: list[int], target_dir: str) -> int:
        """Копирует JSON-файлы выбранных генераций в target_dir.
        Возвращает количество успешно скопированных файлов."""

        target = Path(target_dir)
        count = 0

        for gid in generation_ids:
            generation = self._find_by_id(gid)

            if generation is None or not generation.path.exists():
                continue

            try:
                shutil.copy2(generation.path, target / generation.path.name)
                count += 1
            except OSError as e:
                logger.error("Не удалось экспортировать %s: %s", generation.path, e)
                self.error_occurred.emit(f"Не удалось экспортировать {generation.path.name}: {e}")

        logger.info("Экспортировано %d из %d генераций в %s", count, len(generation_ids), target_dir)

        return count

    def export_generations_zip(self, generation_ids: list[int], zip_path: str) -> int:
        """Экспортирует выбранные генерации (JSON + изображения + превью)
        в один ZIP-архив. См. GenerationRepository.export_generations_zip."""

        count = self._repository.export_generations_zip(generation_ids, zip_path)

        logger.info(
            "Экспортировано в ZIP %d из %d генераций: %s",
            count, len(generation_ids), zip_path
        )

        return count

    def import_user_data(self, other_db_path: str) -> tuple[int, int]:
        """Импортирует избранное/рейтинг из БД другой машины и обновляет
        in-memory представление уже загруженных генераций."""

        updated, unmatched = self._repository.import_user_data(other_db_path)

        if updated and self.current_folder is not None:
            self.apply_filters()

        return updated, unmatched

    def add_dropped_files(self, paths: list[str]) -> int:
        """Добавляет в библиотеку JSON-файлы генераций, перетащенные в
        главное окно — каждый файл добавляется/обновляется в БД напрямую,
        затем список в UI перечитывается, если сейчас открыта папка.

        Возвращает количество успешно добавленных файлов."""

        added = 0

        for path in paths:
            try:
                gen_id = self._repository.add_generation_file(path)
            except OSError as e:
                logger.error("add_dropped_files: ошибка при добавлении %s: %s", path, e)
                self.error_occurred.emit(f"Не удалось добавить {path}: {e}")
                continue

            if gen_id is not None:
                added += 1
            else:
                self.error_occurred.emit(f"Не удалось разобрать JSON: {path}")

        logger.info("Перетаскиванием добавлено %d из %d файлов", added, len(paths))

        if added and self.current_folder is not None:
            self._invalidate_available_cache()
            self.apply_filters()

        return added
