from enum import Enum


class SortMode(Enum):
    """Доступные режимы сортировки списка генераций.

    Избранные генерации при этом всегда поднимаются наверх списка
    независимо от выбранного режима — см. GenerationSorter.sort().
    """

    NEWEST = 0
    OLDEST = 1

    MODEL = 2

    CFG = 3

    STEPS = 4

    GENERATION_TIME = 5

    RATING = 6
