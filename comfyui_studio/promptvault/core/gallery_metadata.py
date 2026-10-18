"""Редактирование метаданных, история изменений и статистика GalleryManager."""

from __future__ import annotations

import logging

from comfyui_studio.promptvault.core.gallery_base import GalleryBase
from comfyui_studio.promptvault.core.statistics import Statistics, compute_statistics

logger = logging.getLogger(__name__)


class MetadataMixin(GalleryBase):
    # ------------------------------------------------------------
    # редактирование метаданных

    def update_generation_metadata(self, generation_id: int, update_dict: dict) -> bool:
        """Сохраняет отредактированные метаданные генерации (JSON на диске
        + БД) и обновляет её in-memory представление.

        При неудаче эмитит error_occurred с человекочитаемым сообщением и
        возвращает False.
        """

        success = self._repository.update_generation(generation_id, update_dict)

        if not success:
            self.error_occurred.emit(
                "Не удалось сохранить изменения — подробности в логе."
            )
            return False

        refreshed = self._repository.get_generation(generation_id)

        if refreshed is None:
            self.error_occurred.emit(
                "Изменения сохранены, но не удалось перечитать генерацию."
            )
            return False

        self._invalidate_available_cache()

        if (
            self.current_generation is not None
            and self.current_generation.id == generation_id
        ):
            self.current_generation = refreshed

        # модель/сэмплер могли измениться — список доступных значений для
        # фильтров и сам отфильтрованный список могли устареть.
        # apply_filters() перезапрашивает страницу из БД заново, вызываем
        # ДО эмита metadata_updated, чтобы к моменту, когда UI на него
        # отреагирует, filtered_generations уже отражал результат.
        self.apply_filters()

        still_visible = any(
            g.id == generation_id for g in self.filtered_generations
        )

        self.metadata_updated.emit(refreshed)

        if not still_visible:
            logger.info(
                "Генерация id=%s отредактирована и скрыта текущими фильтрами",
                generation_id
            )
            self.metadata_updated_hidden_by_filters.emit(refreshed)

        return True

    def update_generations_metadata(
        self, generation_ids: list[int], update_dict: dict
    ) -> bool:
        """Массовое редактирование метаданных — применяет один и тот же
        update_dict сразу к нескольким генерациям.

        Эмитит bulk_metadata_updated(ids) один раз со списком id, для
        которых сохранение прошло успешно. Возвращает True, если сохранены
        ВСЕ переданные генерации, и False, если хотя бы одна не
        сохранилась (частичный успех не откатывается).
        """

        failed_ids = self._repository.update_generations(generation_ids, update_dict)
        succeeded_ids = [gid for gid in generation_ids if gid not in failed_ids]

        if failed_ids:
            self.error_occurred.emit(
                f"Не удалось сохранить изменения для {len(failed_ids)} из "
                f"{len(generation_ids)} генераций — подробности в логе."
            )

        if succeeded_ids:
            self._invalidate_available_cache()

            if (
                self.current_generation is not None
                and self.current_generation.id in succeeded_ids
            ):
                self.current_generation = self._repository.get_generation(
                    self.current_generation.id
                )

            # как и в update_generation_metadata — перезапрашиваем страницу
            # из БД ДО эмита сигнала
            self.apply_filters()

            self.bulk_metadata_updated.emit(succeeded_ids)

        return not failed_ids

    def get_metadata_history(self, generation_id: int) -> list[dict]:
        """История изменений метаданных одной генерации — самые новые
        записи первыми. См. GenerationRepository.get_metadata_history."""

        return self._repository.get_metadata_history(generation_id)

    # ------------------------------------------------------------
    # статистика

    def get_statistics(self) -> Statistics:
        """Статистика по тому, что сейчас реально показано в галерее:
        текущая открытая папка с уже применёнными фильтрами. Считается в
        Python над уже загруженным списком (compute_statistics)."""

        return compute_statistics(self.filtered_generations)

    def get_library_statistics(self) -> Statistics:
        """Статистика по ВСЕЙ библиотеке (всем папкам, когда-либо
        просканированным в БД), без учёта текущей папки/фильтров —
        считается SQL-агрегатами в БД (GenerationRepository.get_statistics)."""

        return self._repository.get_statistics()
