from __future__ import annotations

from comfyui_studio.promptvault.core.generation import Generation
from comfyui_studio.promptvault.core.sort_options import SortMode


class GenerationSorter:
    """Сортирует список генераций по выбранному режиму."""

    @staticmethod
    def sort(
        generations: list[Generation],
        mode: SortMode,
    ) -> list[Generation]:
        """Возвращает НОВЫЙ отсортированный список (исходный не
        изменяется). Избранные генерации всегда оказываются в самом
        верху, независимо от выбранного режима сортировки."""

        generations = GenerationSorter._sort_by_mode(list(generations), mode)

        # избранные всегда должны быть в самом верху списка,
        # независимо от выбранного режима сортировки; порядок
        # внутри каждой из групп при этом сохраняется (сортировка
        # в Python стабильна)
        favorites = [g for g in generations if g.favorite]
        others = [g for g in generations if not g.favorite]

        return favorites + others

    @staticmethod
    def _sort_by_mode(
        generations: list[Generation],
        mode: SortMode,
    ) -> list[Generation]:

        if mode == SortMode.NEWEST:

            generations.sort(
                key=lambda g: g.timestamp,
                reverse=True
            )

        elif mode == SortMode.OLDEST:

            generations.sort(
                key=lambda g: g.timestamp
            )

        elif mode == SortMode.MODEL:

            generations.sort(
                key=lambda g: g.model.lower()
            )

        elif mode == SortMode.CFG:

            generations.sort(
                key=lambda g: g.cfg,
                reverse=True
            )

        elif mode == SortMode.STEPS:

            generations.sort(
                key=lambda g: g.steps,
                reverse=True
            )

        elif mode == SortMode.GENERATION_TIME:

            generations.sort(
                key=lambda g: g.generation_time,
                reverse=True
            )

        elif mode == SortMode.RATING:

            generations.sort(
                key=lambda g: g.rating,
                reverse=True
            )

        return generations


class GenerationSorterSQL:
    """SQL-эквивалент GenerationSorter.sort (задача: перенос
    GenerationFilter/GenerationSorter на SQL — Этап 1).

    Строит фрагмент ORDER BY (без самого ключевого слова) для
    заданного SortMode — ожидает те же алиасы, что и
    GenerationFilterSQL: ``g`` (generations), ``u`` (user_data,
    LEFT JOIN по g.id = u.generation_id).

    Избранные генерации всегда идут первыми, независимо от режима —
    как и в GenerationSorter.sort."""

    @staticmethod
    def build_order_by(mode: SortMode) -> str:

        favorites_first = "COALESCE(u.favorite, 0) DESC"

        if mode == SortMode.NEWEST:
            key = "g.timestamp DESC"
        elif mode == SortMode.OLDEST:
            key = "g.timestamp ASC"
        elif mode == SortMode.MODEL:
            key = "LOWER(g.model) ASC"
        elif mode == SortMode.CFG:
            key = "g.cfg DESC"
        elif mode == SortMode.STEPS:
            key = "g.steps DESC"
        elif mode == SortMode.GENERATION_TIME:
            key = "g.generation_time DESC"
        elif mode == SortMode.RATING:
            key = "COALESCE(u.rating, 0) DESC"
        else:
            key = "g.timestamp DESC"

        return f"{favorites_first}, {key}"
