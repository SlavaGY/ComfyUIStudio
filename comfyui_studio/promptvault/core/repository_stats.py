"""Агрегированная статистика галереи: счётчики, топы и SQL-гистограммы."""

from __future__ import annotations

from comfyui_studio.promptvault.config import STATISTICS_HISTOGRAM_BUCKETS, STATISTICS_TOP_N
from comfyui_studio.promptvault.core.repository_base import RepositoryBase
from comfyui_studio.promptvault.core.statistics import HistogramBucket, Statistics


class StatisticsMixin(RepositoryBase):
    # ------------------------------------------------------------
    # статистика

    def get_statistics(self) -> Statistics:
        """Считает агрегированную статистику по всей библиотеке
        (СРАЗУ по всем папкам, когда-либо просканированным в БД) —
        топ-10 моделей/LoRA/сэмплеров, распределения CFG/Steps/рейтинга,
        общее количество генераций/избранных и средний рейтинг.

        Все агрегации выполняются на стороне SQL (COUNT/GROUP BY/AVG),
        а не построчным перебором в Python — остаётся дешёвым даже для
        библиотеки из сотен тысяч записей.
        """

        conn = self._conn

        total = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]

        favorites = conn.execute(
            "SELECT COUNT(*) FROM user_data WHERE favorite = 1"
        ).fetchone()[0]

        avg_rating = conn.execute(
            "SELECT AVG(rating) FROM user_data WHERE rating > 0"
        ).fetchone()[0] or 0.0

        top_models = conn.execute(
            """SELECT model, COUNT(*) c FROM generations
               WHERE model IS NOT NULL AND model != ''
               GROUP BY model ORDER BY c DESC, model LIMIT ?""",
            (STATISTICS_TOP_N,)
        ).fetchall()

        top_samplers = conn.execute(
            """SELECT sampler, COUNT(*) c FROM generations
               WHERE sampler IS NOT NULL AND sampler != ''
               GROUP BY sampler ORDER BY c DESC, sampler LIMIT ?""",
            (STATISTICS_TOP_N,)
        ).fetchall()

        top_loras = conn.execute(
            """SELECT filename, COUNT(*) c FROM loras
               WHERE filename IS NOT NULL AND filename != ''
               GROUP BY filename ORDER BY c DESC, filename LIMIT ?""",
            (STATISTICS_TOP_N,)
        ).fetchall()

        rating_rows = conn.execute(
            """SELECT rating, COUNT(*) FROM user_data
               WHERE rating > 0 GROUP BY rating ORDER BY rating"""
        ).fetchall()

        return Statistics(
            total_generations=total,
            total_favorites=favorites,
            average_rating=round(avg_rating, 2),
            top_models=[(m, c) for m, c in top_models],
            top_samplers=[(s, c) for s, c in top_samplers],
            top_loras=[(lo, c) for lo, c in top_loras],
            cfg_histogram=self._sql_histogram("generations", "cfg"),
            steps_histogram=self._sql_histogram("generations", "steps"),
            rating_distribution=[(r, c) for r, c in rating_rows],
        )

    def _sql_histogram(
        self,
        table: str,
        column: str,
        num_buckets: int = STATISTICS_HISTOGRAM_BUCKETS,
    ) -> list[HistogramBucket]:
        """Строит гистограмму значений числовой колонки полностью на
        стороне SQL: min/max колонки определяются одним запросом,
        затем каждая строка относится к одной из num_buckets
        равноширинных корзин через SQL-арифметику и COUNT(*)/GROUP BY —
        без выгрузки самих значений в Python.
        """

        conn = self._conn

        lo, hi = conn.execute(
            f"SELECT MIN({column}), MAX({column}) FROM {table} "
            f"WHERE {column} IS NOT NULL"
        ).fetchone()

        if lo is None:
            return []

        if lo == hi:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
            ).fetchone()[0]
            return [HistogramBucket(label=self._format_bucket_value(lo), count=count)]

        width = (hi - lo) / num_buckets

        rows = conn.execute(
            f"""SELECT bucket, COUNT(*) FROM (
                    SELECT MIN(
                        CAST(({column} - ?) / ? AS INTEGER), ?
                    ) AS bucket
                    FROM {table}
                    WHERE {column} IS NOT NULL
                )
                GROUP BY bucket ORDER BY bucket""",
            (lo, width, num_buckets - 1)
        ).fetchall()

        counts_by_bucket = dict(rows)

        buckets = []

        for i in range(num_buckets):

            bucket_lo = lo + i * width
            bucket_hi = lo + (i + 1) * width

            label = (
                f"{self._format_bucket_value(bucket_lo)}"
                f"\u2013{self._format_bucket_value(bucket_hi)}"
            )

            buckets.append(
                HistogramBucket(label=label, count=counts_by_bucket.get(i, 0))
            )

        return buckets

    @staticmethod
    def _format_bucket_value(value: float) -> str:

        if float(value).is_integer():
            return str(int(value))

        return f"{value:.2f}"
