"""Доменная модель агрегированной статистики и её расчёт по уже
загруженному в память списку генераций (см. app/ui/statistics_window.py
и GalleryManager.get_statistics).

Есть также GenerationRepository.get_statistics — тот же результат,
но посчитанный SQL-агрегатами по ВСЕЙ библиотеке (всем папкам,
когда-либо просканированным в БД) напрямую в БД, без выгрузки
генераций в Python; используется отдельно от StatisticsWindow.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config import STATISTICS_HISTOGRAM_BUCKETS, STATISTICS_TOP_N

if TYPE_CHECKING:
    from app.core.generation import Generation


@dataclass(slots=True)
class HistogramBucket:
    """Одна корзина гистограммы: подпись диапазона + количество
    попавших в неё генераций."""

    label: str
    count: int


@dataclass(slots=True)
class Statistics:
    """Агрегированная статистика по набору генераций — либо по всей
    библиотеке (см. GenerationRepository.get_statistics), либо, что
    используется в StatisticsWindow, по тому, что сейчас реально
    показано в приложении: генерации текущей открытой папки, уже
    прошедшие текущие фильтры (см. compute_statistics и
    GalleryManager.filtered_generations).
    """

    total_generations: int = 0
    total_favorites: int = 0

    # средний рейтинг среди генераций, у которых он вообще проставлен
    # (rating > 0) — 0.0, если таких нет
    average_rating: float = 0.0

    # (значение, количество), отсортировано по убыванию количества,
    # не более STATISTICS_TOP_N штук
    top_models: list[tuple[str, int]] = field(default_factory=list)
    top_samplers: list[tuple[str, int]] = field(default_factory=list)
    top_loras: list[tuple[str, int]] = field(default_factory=list)

    cfg_histogram: list[HistogramBucket] = field(default_factory=list)
    steps_histogram: list[HistogramBucket] = field(default_factory=list)

    # рейтинг всегда целый 1..MAX_RATING — отдельная (не "гистограммная
    # по диапазонам", а по точным значениям) структура удобнее для
    # столбчатой диаграммы "рейтинг -> количество"
    rating_distribution: list[tuple[int, int]] = field(default_factory=list)


def compute_statistics(
    generations: list["Generation"],
    top_n: int = STATISTICS_TOP_N,
    histogram_buckets: int = STATISTICS_HISTOGRAM_BUCKETS,
) -> Statistics:
    """Считает Statistics по уже загруженному в память списку
    генераций — то есть по тому, что реально видит пользователь
    (текущая папка + применённые фильтры), а не по всей библиотеке.

    В отличие от GenerationRepository.get_statistics (агрегирующие
    SQL-запросы по всей БД), здесь генерации уже есть в памяти (см.
    GalleryManager.filtered_generations), так что агрегацию проще
    сделать построчно в Python — список и так уже отфильтрован и
    ограничен размером текущей папки/выборки.
    """

    total = len(generations)

    favorites = sum(1 for g in generations if g.favorite)

    rated = [g.rating for g in generations if g.rating > 0]
    average_rating = round(sum(rated) / len(rated), 2) if rated else 0.0

    model_counts = Counter(g.model for g in generations if g.model)
    sampler_counts = Counter(g.sampler for g in generations if g.sampler)

    lora_counts: Counter[str] = Counter()
    for g in generations:
        for lora in g.loras:
            if lora.filename:
                lora_counts[lora.filename] += 1

    rating_counts = Counter(g.rating for g in generations if g.rating > 0)

    return Statistics(
        total_generations=total,
        total_favorites=favorites,
        average_rating=average_rating,
        top_models=_top_n(model_counts, top_n),
        top_samplers=_top_n(sampler_counts, top_n),
        top_loras=_top_n(lora_counts, top_n),
        cfg_histogram=_histogram(
            [g.cfg for g in generations if g.cfg is not None], histogram_buckets
        ),
        steps_histogram=_histogram(
            [g.steps for g in generations if g.steps is not None], histogram_buckets
        ),
        rating_distribution=sorted(rating_counts.items()),
    )


def _top_n(counts: "Counter[str]", top_n: int) -> list[tuple[str, int]]:
    """Топ-N (значение, количество) по убыванию количества, при
    равенстве — по алфавиту (тот же порядок, что и в SQL-версии
    ORDER BY c DESC, name)."""

    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:top_n]


def _histogram(values: list[float], num_buckets: int) -> list[HistogramBucket]:
    """Строит гистограмму равноширинных корзин по списку числовых
    значений — тот же алгоритм, что и
    GenerationRepository._sql_histogram, но над списком в памяти
    вместо SQL-запроса."""

    if not values:
        return []

    lo, hi = min(values), max(values)

    if lo == hi:
        return [HistogramBucket(label=_format_bucket_value(lo), count=len(values))]

    width = (hi - lo) / num_buckets

    counts_by_bucket: Counter[int] = Counter()

    for value in values:
        bucket = min(int((value - lo) / width), num_buckets - 1)
        counts_by_bucket[bucket] += 1

    buckets = []

    for i in range(num_buckets):

        bucket_lo = lo + i * width
        bucket_hi = lo + (i + 1) * width

        label = f"{_format_bucket_value(bucket_lo)}\u2013{_format_bucket_value(bucket_hi)}"

        buckets.append(HistogramBucket(label=label, count=counts_by_bucket.get(i, 0)))

    return buckets


def _format_bucket_value(value: float) -> str:

    if float(value).is_integer():
        return str(int(value))

    return f"{value:.2f}"
