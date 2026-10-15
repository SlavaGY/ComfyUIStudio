"""Чтение из БД: выборки, подсчёты, фильтрация, списки значений для фильтров.

Миксин GenerationRepository (этап 6.1 -- разрез repository.py). Вся работа
идёт через `self._conn`; ничего не пишет ни в БД, ни на диск.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from comfyui_studio.promptvault.core.generation import Generation, ImageData, LoraData
from comfyui_studio.promptvault.core.generation_filter import FilterOptions, GenerationFilterSQL
from comfyui_studio.promptvault.core.generation_sorter import GenerationSorterSQL
from comfyui_studio.promptvault.core.repository_base import RepositoryBase
from comfyui_studio.promptvault.core.sort_options import SortMode


class QueryMixin(RepositoryBase):
    # ------------------------------------------------------------
    # загрузка

    # соответствует SELECT-списку, разделяемому load_generations,
    # load_generations_page и get_generation — вынесен один раз, чтобы
    # три места не могли рассинхронизироваться по набору колонок
    #
    # ВАЖНО: g.extra_data сюда НЕ включён (задача: память). Это все
    # "незнакомые" верхнеуровневые ключи исходного JSON (см.
    # KNOWN_KEYS в app/core/parser.py) — для генераторов вроде ComfyUI
    # там обычно лежит целиком workflow/граф нод, который может весить
    # сотни КБ — единицы МБ НА ФАЙЛ. При этом extra_data нигде в
    # приложении не читается (см. get_generation_extra_data ниже —
    # единственная точка доступа, вызывается по требованию). Раньше
    # это поле парсилось и висело в памяти для КАЖДОЙ генерации в
    # каждом объекте Generation, включая держащиеся месяцами
    # self.generations/self.filtered_generations в GalleryManager —
    # на библиотеке из тысяч генераций именно это было основным
    # источником "жрёт гигабайты ОЗУ", а не сам список Generation
    # (без extra_data один объект — единицы КБ).
    _GENERATION_SELECT = """
        SELECT g.id, g.path, g.model, g.sampler, g.cfg, g.steps,
               g.timestamp, g.generation_time, g.positive, g.negative,
               COALESCE(u.favorite, 0), COALESCE(u.rating, 0)
        FROM generations g
        LEFT JOIN user_data u ON u.generation_id = g.id
    """

    @staticmethod
    def _like_escape(value: str) -> str:
        """Экранирует спецсимволы LIKE (`%`, `_`, сам escape-символ
        `\\`) в произвольной строке перед подстановкой в SQL LIKE."""

        return (
            value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_")
        )

    @classmethod
    def _folder_like_pattern(cls, folder: str | Path) -> str:
        """Строит LIKE-паттерн ``<папка><разделитель>%``, которому
        соответствуют пути ко всем файлам ВНУТРИ указанной папки
        (включая вложенные подпапки), но не сама папка.

        Разделитель — нативный os.sep, т.к. path в БД всегда сохраняется
        через str(Path(...).resolve()) (см. sync_folder), то есть уже
        в нативном для текущей ОС виде.
        """

        prefix = str(Path(folder).resolve()) + os.sep

        return cls._like_escape(prefix) + "%"

    def load_generations(self, folder: str | Path) -> list[Generation]:
        """Загружает все генерации, реально лежащие внутри указанной
        папки (включая вложенные), одним пакетом запросов (без N+1 —
        отдельные bulk-запросы для loras/images).

        Фильтрация идёт по фактическому текущему path каждой записи
        (SQL LIKE по индексированной колонке path, а не построчным
        разбором в Python — см. «Известные ограничения» в удалённом
        tools/promptvault/TODO.md, git-история, в версиях приложения до задачи 3.3), а не по тому, под какой
        папкой её когда-то просканировали — так открытие вложенной
        подпапки корректно показывает только её содержимое, даже если
        родительская папка уже была просканирована раньше (а path мог
        с тех пор переехать).
        """

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            f"{self._GENERATION_SELECT} WHERE g.path LIKE ? ESCAPE '\\' "
            f"ORDER BY g.timestamp DESC",
            (pattern,)
        ).fetchall()

        return self._build_generations(rows)

    def count_generations(self, folder: str | Path) -> int:
        """Считает генерации внутри папки без их загрузки — используется
        для планирования постраничной загрузки (см. load_generations_page)."""

        pattern = self._folder_like_pattern(folder)

        row = self._conn.execute(
            "SELECT COUNT(*) FROM generations WHERE path LIKE ? ESCAPE '\\'",
            (pattern,)
        ).fetchone()

        return row[0] if row else 0

    def load_generations_page(
        self,
        folder: str | Path,
        offset: int,
        limit: int,
    ) -> list[Generation]:
        """Загружает одну "страницу" генераций внутри папки (SQL
        LIMIT/OFFSET), отсортированную так же, как и load_generations
        (по timestamp DESC) — используется для ленивой/постраничной
        загрузки больших библиотек (см. GalleryManager._load_next_page),
        чтобы открытие папки не блокировало UI одним огромным запросом.
        """

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            f"{self._GENERATION_SELECT} WHERE g.path LIKE ? ESCAPE '\\' "
            f"ORDER BY g.timestamp DESC LIMIT ? OFFSET ?",
            (pattern, limit, offset)
        ).fetchall()

        return self._build_generations(rows)

    # ------------------------------------------------------------
    # фильтрация/сортировка на стороне SQL (задача: перенос
    # GenerationFilter/GenerationSorter на SQL — Этап 1)
    #
    # В отличие от load_generations/load_generations_page (фильтруют
    # только по пути), эти методы дополнительно применяют FilterOptions
    # и SortMode ЦЕЛИКОМ в самом SQL-запросе (см. GenerationFilterSQL/
    # GenerationSorterSQL) — раньше это делалось построчным разбором в
    # Python над уже загруженным в память полным списком генераций
    # папки (см. GenerationFilter.apply/GenerationSorter.sort), даже
    # если сама загрузка из БД уже была постраничной.

    def count_filtered(self, folder: str | Path, options: FilterOptions) -> int:
        """Считает генерации папки, проходящие все условия options, без
        их загрузки — используется для планирования виртуальной
        пагинации отфильтрованного списка (см.
        GalleryManager.apply_filters)."""

        pattern = self._folder_like_pattern(folder)
        extra_where, extra_params = GenerationFilterSQL.build_where(options)

        row = self._conn.execute(
            "SELECT COUNT(*) FROM generations g "
            "LEFT JOIN user_data u ON u.generation_id = g.id "
            f"WHERE g.path LIKE ? ESCAPE '\\'{extra_where}",
            [pattern, *extra_params]
        ).fetchone()

        return row[0] if row else 0

    def load_filtered_page(
        self,
        folder: str | Path,
        options: FilterOptions,
        sort_mode: SortMode,
        offset: int = 0,
        limit: int = 500,
    ) -> list[Generation]:
        """Основной метод виртуальной пагинации (задача: настоящая
        виртуальная пагинация — Этап 2): возвращает уже отфильтрованную
        (по всем условиям options) и отсортированную (sort_mode,
        избранные первыми) страницу генераций папки — SQL WHERE +
        ORDER BY + LIMIT/OFFSET одним запросом, без разбора в Python и
        без загрузки остальных страниц."""

        pattern = self._folder_like_pattern(folder)
        extra_where, extra_params = GenerationFilterSQL.build_where(options)
        order_sql = GenerationSorterSQL.build_order_by(sort_mode)

        rows = self._conn.execute(
            f"{self._GENERATION_SELECT} WHERE g.path LIKE ? ESCAPE '\\'{extra_where} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [pattern, *extra_params, limit, offset]
        ).fetchall()

        return self._build_generations(rows)

    def get_filtered_ids(self, folder: str | Path, options: FilterOptions) -> list[int]:
        """Все id генераций папки, проходящих условия options, без
        LIMIT/OFFSET и без загрузки самих генераций — для массовых
        операций над ВСЕМ отфильтрованным набором разом (например,
        "выделить все", не только видимую страницу)."""

        pattern = self._folder_like_pattern(folder)
        extra_where, extra_params = GenerationFilterSQL.build_where(options)

        rows = self._conn.execute(
            "SELECT g.id FROM generations g "
            "LEFT JOIN user_data u ON u.generation_id = g.id "
            f"WHERE g.path LIKE ? ESCAPE '\\'{extra_where}",
            [pattern, *extra_params]
        ).fetchall()

        return [row[0] for row in rows]

    def available_models(self, folder: str | Path) -> set[str]:
        """Множество различных непустых значений model среди генераций
        папки — через SQL DISTINCT (индекс idx_generations_model), а не
        построением set() над уже загруженным в память списком Generation,
        чтобы оставаться дешёвым даже для очень больших библиотек.
        Результат стоит кэшировать на стороне вызывающего кода
        (см. GalleryManager) — сам метод ничего не кэширует."""

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            """SELECT DISTINCT model FROM generations
               WHERE path LIKE ? ESCAPE '\\' AND model IS NOT NULL AND model != ''""",
            (pattern,)
        ).fetchall()

        return {row[0] for row in rows}

    def available_samplers(self, folder: str | Path) -> set[str]:
        """См. available_models — то же самое для sampler."""

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            """SELECT DISTINCT sampler FROM generations
               WHERE path LIKE ? ESCAPE '\\' AND sampler IS NOT NULL AND sampler != ''""",
            (pattern,)
        ).fetchall()

        return {row[0] for row in rows}

    def available_loras(self, folder: str | Path) -> set[str]:
        """См. available_models — то же самое для имён файлов LoRA
        (JOIN на loras, отфильтрованный по generation_id внутри папки)."""

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            """SELECT DISTINCT l.filename
               FROM loras l
               JOIN generations g ON g.id = l.generation_id
               WHERE g.path LIKE ? ESCAPE '\\'
                 AND l.filename IS NOT NULL AND l.filename != ''""",
            (pattern,)
        ).fetchall()

        return {row[0] for row in rows}

    def get_generation(self, generation_id: int) -> Generation | None:
        """Загружает одну генерацию по id заново из БД (со свежими
        loras/images/избранным/рейтингом). Используется после точечного
        редактирования метаданных, чтобы не перечитывать всю папку целиком."""

        row = self._conn.execute(
            f"{self._GENERATION_SELECT} WHERE g.id = ?",
            (generation_id,)
        ).fetchone()

        if row is None:
            return None

        return self._build_generations([row])[0]

    def _build_generations(self, gen_rows: list[tuple]) -> list[Generation]:
        """Собирает объекты Generation из строк generations (+JOIN
        user_data), догружая loras/images одним bulk-запросом на все
        переданные id разом (без N+1)."""

        gen_ids = [row[0] for row in gen_rows]

        loras_by_gen: dict[int, list[LoraData]] = {}
        images_by_gen: dict[int, list[ImageData]] = {}
        tags_by_gen: dict[int, list[str]] = {}

        if gen_ids:

            conn = self._conn
            placeholders = ",".join("?" * len(gen_ids))

            for gid, filename, strength, source in conn.execute(
                f"""SELECT generation_id, filename, strength, source
                    FROM loras WHERE generation_id IN ({placeholders})""",
                gen_ids
            ):
                loras_by_gen.setdefault(gid, []).append(
                    LoraData(filename=filename, strength=strength, source=source)
                )

            for gid, image_path, seed in conn.execute(
                f"""SELECT generation_id, image_path, seed
                    FROM images WHERE generation_id IN ({placeholders})""",
                gen_ids
            ):
                images_by_gen.setdefault(gid, []).append(
                    ImageData(file=image_path, seed=seed)
                )

            for gid, tag in conn.execute(
                f"""SELECT generation_id, tag
                    FROM custom_tags WHERE generation_id IN ({placeholders})
                    ORDER BY tag COLLATE NOCASE""",
                gen_ids
            ):
                tags_by_gen.setdefault(gid, []).append(tag)

        generations = []

        for row in gen_rows:

            (gid, path, model, sampler, cfg, steps, timestamp,
             generation_time, positive, negative,
             favorite, rating) = row

            generations.append(Generation(
                id=gid,
                path=Path(path),
                timestamp=timestamp or "",
                generation_time=generation_time or 0,
                model=model or "",
                cfg=cfg or 0,
                steps=steps or 0,
                sampler=sampler or "",
                positive=positive or "",
                negative=negative or "",
                images=images_by_gen.get(gid, []),
                loras=loras_by_gen.get(gid, []),
                # см. комментарий у _GENERATION_SELECT — extra_data
                # намеренно не загружается в резидентную в памяти
                # Generation; используйте get_generation_extra_data
                # для точечного доступа по требованию
                extra_data={},
                favorite=bool(favorite),
                rating=rating or 0,
                custom_tags=tags_by_gen.get(gid, []),
            ))

        return generations

    def get_generation_extra_data(self, generation_id: int) -> dict:
        """Точечно подгружает extra_data ОДНОЙ генерации по требованию
        (см. комментарий у _GENERATION_SELECT — почему это не часть
        обычной загрузки списка/одной генерации).

        Сейчас нигде в UI не вызывается — задел на будущее (например,
        просмотр "сырых" полей исходного JSON в MetadataEditor), не
        стоит приложению ничего, пока не используется.
        """

        row = self._conn.execute(
            "SELECT extra_data FROM generations WHERE id = ?", (generation_id,)
        ).fetchone()

        if row is None or not row[0]:
            return {}

        try:
            parsed = json.loads(row[0])
        except ValueError:
            return {}

        return parsed if isinstance(parsed, dict) else {}

    def available_custom_tags(self, folder: str | Path) -> set[str]:
        """См. available_models — то же самое для пользовательских
        тегов (JOIN на custom_tags, отфильтрованный по generation_id
        внутри папки)."""

        pattern = self._folder_like_pattern(folder)

        rows = self._conn.execute(
            """SELECT DISTINCT t.tag
               FROM custom_tags t
               JOIN generations g ON g.id = t.generation_id
               WHERE g.path LIKE ? ESCAPE '\\'""",
            (pattern,)
        ).fetchall()

        return {row[0] for row in rows}
