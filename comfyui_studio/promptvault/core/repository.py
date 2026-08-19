from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from comfyui_studio.promptvault.config import (
    MAX_RATING,
    MIN_RATING,
    STATISTICS_HISTOGRAM_BUCKETS,
    STATISTICS_TOP_N,
)
from comfyui_studio.promptvault.core import embedding
from comfyui_studio.promptvault.core.database import connect
from comfyui_studio.promptvault.core.generation import Generation, ImageData, LoraData
from comfyui_studio.promptvault.core.generation_filter import FilterOptions, GenerationFilterSQL
from comfyui_studio.promptvault.core.generation_sorter import GenerationSorterSQL
from comfyui_studio.promptvault.core.parser import parse_generation_data
from comfyui_studio.promptvault.core.sort_options import SortMode
from comfyui_studio.promptvault.core.statistics import HistogramBucket, Statistics

logger = logging.getLogger(__name__)


class GenerationRepository:
    """Хранит и обновляет данные о генерациях в SQLite.

    Заменяет прежний подход (парсинг всех JSON-файлов при каждом
    открытии папки + отдельный QSettings-блок для избранного/рейтинга):

    - файлы парсятся один раз и только повторно при реальном
      изменении (по mtime) — открытие уже просканированной папки
      почти мгновенное;
    - избранное/рейтинг обновляются одной точечной SQL-командой
      вместо перезаписи целого JSON-блока в QSettings.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._conn = connect(db_path)

    # ------------------------------------------------------------
    # семантические эмбеддинги

    @staticmethod
    def _embedding_text(data: dict[str, Any]) -> str:
        """Текст, из которого считается семантический эмбеддинг
        генерации — ТОЛЬКО позитивный промпт.

        Изначально сюда добавлялся ещё и негативный промпт (в расчёте
        на поиск и по тому, что исключалось из изображения), но на
        практике это оказалось источником шума: негативный промпт почти
        всегда состоит из универсальных технических исключений (bad
        hands, blurry, watermark, censored и т.п.), не описывающих
        содержимое конкретной генерации. При по-теговом max-pooling
        сравнении (см. app/core/embedding.py: cosine_similarity берёт
        ЛУЧШЕЕ совпадение среди тегов, а не среднее) один-единственный
        такой "мусорный" тег из негатива может перевесить весь
        осмысленный позитивный промпт и вызвать ложное совпадение —
        это и наблюдалось на практике."""

        positive = (data.get("positive") or "").strip()

        return positive

    # ------------------------------------------------------------
    # синхронизация с диском

    def sync_folder(self, folder: str | Path) -> bool:
        """Приводит БД в соответствие с содержимым папки на диске.

        Генерация идентифицируется парой (timestamp, generation_time),
        а не путём к файлу — путь может измениться, если пользователь
        перенесёт или переименует папку с генерациями, и тогда запись
        в БД должна "переехать" вместе с файлом, а не создаться заново.

        Файлы, пропавшие с диска, из БД НЕ удаляются: пропажа файла из
        конкретной папки не обязательно означает, что генерация удалена
        навсегда (папку могли просто временно убрать/переместить) —
        удалять избранное и рейтинг из-за этого не хочется. Если файл
        всё же исчез навсегда, соответствующая запись просто перестаёт
        встречаться при сканировании — данные о ней остаются в БД, но
        и не мешают: перекрёстных проверок на "существование" при
        синхронизации по identity не требуется.

        Возвращает True, если были добавлены или обновлены какие-либо
        записи (сигнал для UI, что стоит перечитать список).
        """

        folder = str(Path(folder).resolve())
        conn = self._conn

        disk_files = {
            str(p.resolve()): p.stat().st_mtime
            for p in Path(folder).rglob("*.json")
        }

        if not disk_files:
            logger.debug("Синхронизация %s: JSON-файлы не найдены", folder)
            return False

        # быстрый путь: файлы, которые уже лежат в БД под тем же путём
        # и с тем же mtime — трогать не нужно вообще (даже не паримся
        # с их identity)
        placeholders = ",".join("?" * len(disk_files))

        unchanged_paths = {
            path
            for path, mtime in conn.execute(
                f"SELECT path, mtime FROM generations WHERE path IN ({placeholders})",
                list(disk_files.keys())
            )
            if abs(disk_files.get(path, float("nan")) - mtime) < 1e-6
        }

        changed = False
        added = 0
        updated = 0
        parse_errors = 0

        # первый проход: только парсинг JSON — эмбеддинги считаются
        # ниже одним батчем сразу для всех изменившихся файлов, а не
        # по одному внутри цикла upsert'ов, т.к. у трансформера заметные
        # накладные расходы на каждый отдельный вызов encode() — при
        # синхронизации папки с сотнями новых файлов это на порядок
        # быстрее, чем считать эмбеддинг сразу после парсинга каждого
        parsed: list[tuple[str, float, dict[str, Any], str]] = []

        for path, mtime in disk_files.items():

            if path in unchanged_paths:
                continue

            try:
                data = parse_generation_data(path)
            except (OSError, ValueError) as e:
                parse_errors += 1
                logger.warning("Не удалось разобрать JSON %s: %s", path, e)
                continue

            extra_json = json.dumps(data["extra_data"], ensure_ascii=False)

            parsed.append((path, mtime, data, extra_json))

        embeddings = embedding.compute_embeddings_batch(
            [self._embedding_text(data) for _, _, data, _ in parsed]
        )

        for (path, mtime, data, extra_json), emb in zip(parsed, embeddings):

            try:
                gen_id, is_new = self._upsert_generation(
                    folder, path, mtime, data, extra_json, emb
                )
            except sqlite3.IntegrityError as e:
                # исчезающе маловероятная коллизия identity — не роняем
                # синхронизацию из-за одного файла
                logger.error(
                    "Коллизия identity (timestamp+generation_time) при вставке %s: %s",
                    path, e
                )
                continue

            added += is_new
            updated += not is_new

            conn.execute("DELETE FROM loras WHERE generation_id = ?", (gen_id,))
            conn.execute("DELETE FROM images WHERE generation_id = ?", (gen_id,))

            if data["loras"]:
                conn.executemany(
                    "INSERT INTO loras (generation_id, filename, strength, source) VALUES (?,?,?,?)",
                    [
                        (gen_id, lora_data["filename"], lora_data["strength"], lora_data["source"])
                        for lora_data in data["loras"]
                    ]
                )

            if data["images"]:
                conn.executemany(
                    "INSERT INTO images (generation_id, image_path, seed) VALUES (?,?,?)",
                    [
                        (gen_id, i["file"], i["seed"])
                        for i in data["images"]
                    ]
                )

            changed = True

        conn.commit()

        if changed:
            logger.info(
                "Синхронизация %s: добавлено %d, обновлено %d, ошибок парсинга %d",
                folder, added, updated, parse_errors
            )
        else:
            logger.debug("Синхронизация %s: изменений нет", folder)

        return changed

    # ------------------------------------------------------------

    def _upsert_generation(
        self,
        folder: str,
        path: str,
        mtime: float,
        data: dict[str, Any],
        extra_json: str,
        embedding_bytes: bytes | None = None,
    ) -> tuple[int, bool]:
        """Вставляет генерацию, либо, если запись с таким
        (timestamp, generation_time) уже есть — обновляет её (в т.ч.
        переносит path на новое место).

        embedding_bytes — заранее посчитанный (см. sync_folder, батчем)
        семантический эмбеддинг промпта; None, если для этого текста
        эмбеддинг посчитать не удалось (пустой текст либо библиотека
        эмбеддингов недоступна) — в этом случае колонка просто
        обновляется на NULL, генерация участвует в обычном текстовом
        поиске, но не в семантическом.

        Возвращает (id записи, True если запись новая / False если
        обновлена существующая).
        """

        conn = self._conn

        existing = conn.execute(
            "SELECT id FROM generations WHERE timestamp = ? AND generation_time = ?",
            (data["timestamp"], data["generation_time"])
        ).fetchone()

        if existing is not None:

            gen_id = existing[0]

            conn.execute(
                """UPDATE generations SET
                    folder=?, path=?, model=?, sampler=?, cfg=?, steps=?,
                    positive=?, negative=?, extra_data=?, mtime=?, embedding=?
                   WHERE id=?""",
                (
                    folder, path, data["model"], data["sampler"], data["cfg"],
                    data["steps"], data["positive"], data["negative"],
                    extra_json, mtime, embedding_bytes, gen_id
                )
            )

            return gen_id, False

        cur = conn.execute(
            """INSERT INTO generations
                (folder, path, model, sampler, cfg, steps, timestamp,
                 generation_time, positive, negative, extra_data, mtime, embedding)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                folder, path, data["model"], data["sampler"], data["cfg"],
                data["steps"], data["timestamp"], data["generation_time"],
                data["positive"], data["negative"], extra_json, mtime,
                embedding_bytes
            )
        )

        assert cur.lastrowid is not None, "lastrowid отсутствует сразу после INSERT"

        return cur.lastrowid, True

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
               COALESCE(u.favorite, 0), COALESCE(u.rating, 0),
               g.embedding
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
        разбором в Python — см. TODO.md "известные ограничения" в
        версиях приложения до задачи 3.3), а не по тому, под какой
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
    # только по пути), эти три метода дополнительно применяют
    # FilterOptions и SortMode ЦЕЛИКОМ в самом SQL-запросе (см.
    # GenerationFilterSQL/GenerationSorterSQL) — раньше это делалось
    # построчным разбором в Python над уже загруженным в память полным
    # списком генераций папки (см. GenerationFilter.apply/
    # GenerationSorter.sort), даже если сама загрузка из БД уже была
    # постраничной. Единственное исключение — FilterOptions.semantic_query
    # (векторное сходство не выразить обычным SQL): см.
    # load_filtered_for_semantic ниже и
    # GenerationFilter.rank_by_semantic_query.

    def count_filtered(self, folder: str | Path, options: FilterOptions) -> int:
        """Считает генерации папки, проходящие все условия options
        (КРОМЕ semantic_query — см. класс выше), без их загрузки —
        используется для планирования виртуальной пагинации
        отфильтрованного списка (см. GalleryManager.apply_filters)."""

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
        (по всем условиям options, КРОМЕ semantic_query) и
        отсортированную (sort_mode, избранные первыми) страницу
        генераций папки — SQL WHERE + ORDER BY + LIMIT/OFFSET одним
        запросом, без разбора в Python и без загрузки остальных
        страниц."""

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
        """Все id генераций папки, проходящих условия options (КРОМЕ
        semantic_query), без LIMIT/OFFSET и без загрузки самих
        генераций — для массовых операций над ВСЕМ отфильтрованным
        набором разом (например, "выделить все", не только видимую
        страницу)."""

        pattern = self._folder_like_pattern(folder)
        extra_where, extra_params = GenerationFilterSQL.build_where(options)

        rows = self._conn.execute(
            "SELECT g.id FROM generations g "
            "LEFT JOIN user_data u ON u.generation_id = g.id "
            f"WHERE g.path LIKE ? ESCAPE '\\'{extra_where}",
            [pattern, *extra_params]
        ).fetchall()

        return [row[0] for row in rows]

    def load_filtered_for_semantic(
        self,
        folder: str | Path,
        options: FilterOptions,
        sort_mode: SortMode,
    ) -> list[Generation]:
        """Как load_filtered_page, но БЕЗ LIMIT/OFFSET и без учёта
        options.semantic_query — используется только когда
        semantic_query непустой: возвращает ВСЕ генерации папки,
        прошедшие остальные условия (уже суженные SQL-запросом, а не
        всю папку целиком), которые GalleryManager затем ранжирует по
        векторному сходству в Python (см.
        GenerationFilter.rank_by_semantic_query) — сравнить эмбеддинги
        обычным SQL не получится, так что для этого (и только этого)
        случая полный список кандидатов неизбежно оказывается в
        памяти, как и раньше.

        sort_mode здесь нужен только на случай деградации без модели
        эмбеддингов (см. rank_by_semantic_query) — реальное
        ранжирование по сходству, когда модель доступна, этот порядок
        полностью заменяет собой."""

        pattern = self._folder_like_pattern(folder)
        extra_where, extra_params = GenerationFilterSQL.build_where(options)
        order_sql = GenerationSorterSQL.build_order_by(sort_mode)

        rows = self._conn.execute(
            f"{self._GENERATION_SELECT} WHERE g.path LIKE ? ESCAPE '\\'{extra_where} "
            f"ORDER BY {order_sql}",
            [pattern, *extra_params]
        ).fetchall()

        return self._build_generations(rows)

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
             favorite, rating, embedding_bytes) = row

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
                embedding=embedding_bytes,
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

    # ------------------------------------------------------------
    # редактирование метаданных

    # соответствие полей Generation полям в исходном JSON-файле —
    # некоторые называются иначе на диске, чем в нашей модели
    _JSON_FIELD_MAP = {
        "model": "model_name",
        "sampler": "sampler_name",
        "positive": "positive_text",
        "negative": "negative_text",
    }

    def update_generation(
        self,
        generation_id: int,
        update_dict: dict[str, Any],
    ) -> bool:
        """Обновляет поля генерации: перезаписывает исходный JSON-файл
        на диске и синхронизирует БД с получившимся результатом.

        update_dict — словарь атрибутов Generation (например
        {"positive": "...", "cfg": 7.5}), НЕ ключей исходного JSON —
        соответствие имён см. в _JSON_FIELD_MAP. Идентичность записи
        (timestamp, generation_time) редактированию не подлежит —
        соответствующие поля через этот метод не трогаются.

        Возвращает False, если запись не найдена, файл не удалось
        прочитать/сохранить (ошибка логируется), либо если файл на
        диске изменился снаружи (другим процессом, автосинхронизацией
        и т.п.) в промежутке между чтением и записью — в этом случае
        сохранение отменяется, чтобы не затереть чужие изменения
        значениями, прочитанными ДО них (см. mtime_before ниже).
        """

        row = self._conn.execute(
            """SELECT path, model, sampler, cfg, steps, positive, negative
               FROM generations WHERE id = ?""",
            (generation_id,)
        ).fetchone()

        if row is None:
            logger.warning("update_generation: генерация id=%s не найдена", generation_id)
            return False

        path = Path(row[0])

        # значения ДО редактирования — для записи в metadata_history ниже.
        # Читаем их здесь, а не позже, чтобы использовать то, что реально
        # было в БД до этого вызова (а не пересобирать из raw/JSON).
        old_values = {
            "model": row[1], "sampler": row[2], "cfg": row[3],
            "steps": row[4], "positive": row[5], "negative": row[6],
        }

        try:
            mtime_before = path.stat().st_mtime

            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            logger.error("Не удалось прочитать %s для редактирования: %s", path, e)
            return False

        for key, value in update_dict.items():
            raw[self._JSON_FIELD_MAP.get(key, key)] = value

        # проверка "до записи": если файл успел измениться на диске с
        # момента, когда мы его прочитали (mtime_before), значит raw
        # построен поверх уже устаревшего содержимого — запись поверх
        # него потеряла бы то, что успело записаться снаружи. Не
        # исключает 100% гонку (проверка и сама запись не атомарны как
        # единая транзакция без файловой блокировки), но отсекает
        # подавляющее большинство реальных случаев дешёво и без
        # платформо-зависимого file locking.
        try:
            mtime_check = path.stat().st_mtime
        except OSError as e:
            logger.error("Не удалось проверить %s перед сохранением: %s", path, e)
            return False

        if abs(mtime_check - mtime_before) > 1e-6:
            logger.error(
                "update_generation: %s изменился на диске снаружи, пока "
                "редактировался (id=%s) — сохранение отменено, чтобы не "
                "потерять внешние изменения. Повторите редактирование.",
                path, generation_id
            )
            return False

        # запись через временный файл + атомарное переименование —
        # если процесс упадёт или будет прерван посередине записи,
        # исходный JSON останется целым файлом (либо старым, либо уже
        # полностью новым), а не окажется наполовину перезаписанным
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)

            os.replace(tmp_path, path)
        except OSError as e:
            logger.error("Не удалось сохранить %s: %s", path, e)

            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

            return False

        # перечитываем то, что реально оказалось на диске (а не просто
        # доверяем update_dict) и синхронизируем именно эту запись —
        # переиспользуем ту же логику разбора, что и при обычном
        # sync_folder, чтобы не дублировать код
        try:
            data = parse_generation_data(path)
        except (OSError, ValueError) as e:
            logger.error("Не удалось перечитать %s после сохранения: %s", path, e)
            return False

        mtime = path.stat().st_mtime
        extra_json = json.dumps(data["extra_data"], ensure_ascii=False)
        embedding_bytes = embedding.compute_embedding(self._embedding_text(data))

        conn = self._conn

        conn.execute(
            """UPDATE generations SET
                model=?, sampler=?, cfg=?, steps=?, positive=?, negative=?,
                extra_data=?, mtime=?, embedding=?
               WHERE id=?""",
            (
                data["model"], data["sampler"], data["cfg"], data["steps"],
                data["positive"], data["negative"], extra_json, mtime,
                embedding_bytes, generation_id
            )
        )

        self._record_metadata_history(conn, generation_id, old_values, update_dict)

        conn.execute("DELETE FROM loras WHERE generation_id = ?", (generation_id,))
        conn.execute("DELETE FROM images WHERE generation_id = ?", (generation_id,))

        if data["loras"]:
            conn.executemany(
                "INSERT INTO loras (generation_id, filename, strength, source) VALUES (?,?,?,?)",
                [
                    (generation_id, lora_data["filename"], lora_data["strength"], lora_data["source"])
                    for lora_data in data["loras"]
                ]
            )

        if data["images"]:
            conn.executemany(
                "INSERT INTO images (generation_id, image_path, seed) VALUES (?,?,?)",
                [
                    (generation_id, i["file"], i["seed"])
                    for i in data["images"]
                ]
            )

        conn.commit()

        logger.info("Генерация id=%s обновлена через редактор метаданных", generation_id)

        return True

    def _record_metadata_history(
        self,
        conn: sqlite3.Connection,
        generation_id: int,
        old_values: dict[str, Any],
        update_dict: dict[str, Any],
    ) -> None:
        """Пишет по одной строке в metadata_history на КАЖДОЕ поле из
        update_dict, значение которого реально отличается от
        old_values (сравниваем через str(), чтобы 7.0 vs "7.0" и
        подобные не плодили ложные записи истории при отсутствии
        реального изменения).

        Поля, которых нет в old_values (т.е. не входят в набор
        model/sampler/cfg/steps/positive/negative — единственные,
        которые редактирует MetadataEditor), пропускаются: для них нет
        достоверного "старого" значения в БД, которое можно было бы
        записать.

        Не коммитит — вызывающий код (update_generation) уже делает
        общий commit() в конце своей транзакции.
        """

        now = time.time()
        rows = []

        for field, new_value in update_dict.items():

            if field not in old_values:
                continue

            old_value = old_values[field]

            if str(old_value) == str(new_value):
                continue

            rows.append((
                generation_id, now, field,
                None if old_value is None else str(old_value),
                None if new_value is None else str(new_value),
            ))

        if rows:
            conn.executemany(
                """INSERT INTO metadata_history
                       (generation_id, changed_at, field, old_value, new_value)
                   VALUES (?, ?, ?, ?, ?)""",
                rows
            )

    def get_metadata_history(self, generation_id: int) -> list[dict[str, Any]]:
        """Возвращает историю изменений метаданных генерации — самые
        новые записи первыми. Каждая запись: {changed_at, field,
        old_value, new_value}."""

        rows = self._conn.execute(
            """SELECT changed_at, field, old_value, new_value
               FROM metadata_history
               WHERE generation_id = ?
               ORDER BY changed_at DESC, id DESC""",
            (generation_id,)
        ).fetchall()

        return [
            {
                "changed_at": r[0], "field": r[1],
                "old_value": r[2], "new_value": r[3],
            }
            for r in rows
        ]

    def update_generations(
        self,
        generation_ids: list[int],
        update_dict: dict[str, Any],
    ) -> list[int]:
        """Массовое редактирование метаданных (задача: массовое
        редактирование метаданных) — применяет один и тот же
        update_dict к каждой из перечисленных генераций.

        Каждая генерация хранится в своём собственном JSON-файле, так
        что настоящей "массовости" на уровне БД/файлов не нужно —
        достаточно последовательно вызвать уже существующий
        update_generation для каждого id (получая заодно её же
        гарантии: атомарную запись, detection внешних изменений,
        запись в metadata_history). Одна неудача (файл пропал,
        гонка с внешним изменением и т.п.) не прерывает обработку
        остальных id.

        Возвращает список id, для которых сохранение НЕ удалось —
        пустой список значит "всё сохранено успешно". Подробности
        каждой неудачи уже залогированы внутри update_generation.
        """

        failed = []

        for generation_id in generation_ids:
            if not self.update_generation(generation_id, update_dict):
                failed.append(generation_id)

        return failed

    # ------------------------------------------------------------
    # удаление

    def delete_generation(self, generation_id: int, delete_files: bool = False) -> bool:
        """Удаляет запись о генерации из БД (каскадно — loras/images/
        user_data). Если delete_files=True, дополнительно физически
        удаляет JSON-файл и все связанные изображения с диска.

        Возвращает False, если запись не найдена.
        """

        row = self._conn.execute(
            "SELECT path FROM generations WHERE id = ?", (generation_id,)
        ).fetchone()

        if row is None:
            logger.warning("delete_generation: генерация id=%s не найдена", generation_id)
            return False

        path = Path(row[0])

        if delete_files:

            image_rows = self._conn.execute(
                "SELECT image_path FROM images WHERE generation_id = ?",
                (generation_id,)
            ).fetchall()

            for (image_rel_path,) in image_rows:

                image_path = path.parent / image_rel_path

                try:
                    image_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Не удалось удалить файл изображения %s: %s", image_path, e)

            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Не удалось удалить файл %s: %s", path, e)

        self._conn.execute("DELETE FROM generations WHERE id = ?", (generation_id,))
        self._conn.commit()

        logger.info(
            "Генерация id=%s удалена (файлы %s)",
            generation_id, "удалены" if delete_files else "оставлены на диске"
        )

        return True

    # ------------------------------------------------------------
    # расширенный экспорт / импорт (задача 3.4)

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

    def add_generation_file(self, path: str | Path) -> int | None:
        """Добавляет/обновляет в БД ОДИН JSON-файл генерации напрямую
        (в отличие от sync_folder, не сканирует всю папку целиком) —
        используется для drag & drop одиночных JSON-файлов в главное окно.

        folder записи выставляется в родительскую папку файла (как и
        при обычном sync_folder этой же папки). Возвращает id записи,
        либо None, если файл не удалось разобрать.
        """

        path = Path(path).resolve()

        try:
            data = parse_generation_data(path)
        except (OSError, ValueError) as e:
            logger.warning("add_generation_file: не удалось разобрать %s: %s", path, e)
            return None

        mtime = path.stat().st_mtime
        extra_json = json.dumps(data["extra_data"], ensure_ascii=False)
        embedding_bytes = embedding.compute_embedding(self._embedding_text(data))

        conn = self._conn

        gen_id, _is_new = self._upsert_generation(
            str(path.parent), str(path), mtime, data, extra_json, embedding_bytes
        )

        conn.execute("DELETE FROM loras WHERE generation_id = ?", (gen_id,))
        conn.execute("DELETE FROM images WHERE generation_id = ?", (gen_id,))

        if data["loras"]:
            conn.executemany(
                "INSERT INTO loras (generation_id, filename, strength, source) VALUES (?,?,?,?)",
                [
                    (gen_id, lora_data["filename"], lora_data["strength"], lora_data["source"])
                    for lora_data in data["loras"]
                ]
            )

        if data["images"]:
            conn.executemany(
                "INSERT INTO images (generation_id, image_path, seed) VALUES (?,?,?)",
                [
                    (gen_id, i["file"], i["seed"])
                    for i in data["images"]
                ]
            )

        conn.commit()

        logger.info("Генерация id=%s добавлена перетаскиванием файла %s", gen_id, path)

        return gen_id

    # ------------------------------------------------------------
    # пользовательские данные (избранное / рейтинг)

    def set_favorite(self, generation_id: int, value: bool) -> None:
        """Проставляет/снимает избранное одной точечной SQL-командой."""

        self._conn.execute(
            """INSERT INTO user_data (generation_id, favorite)
               VALUES (?, ?)
               ON CONFLICT(generation_id) DO UPDATE SET favorite = excluded.favorite""",
            (generation_id, int(bool(value)))
        )
        self._conn.commit()

    def set_rating(self, generation_id: int, value: int) -> None:
        """Выставляет рейтинг (значение зажимается в [MIN_RATING, MAX_RATING])
        одной точечной SQL-командой."""

        value = max(MIN_RATING, min(MAX_RATING, int(value)))

        self._conn.execute(
            """INSERT INTO user_data (generation_id, rating)
               VALUES (?, ?)
               ON CONFLICT(generation_id) DO UPDATE SET rating = excluded.rating""",
            (generation_id, value)
        )
        self._conn.commit()

    # ------------------------------------------------------------
    # пользовательские теги (задача: пользовательские теги)

    def get_custom_tags(self, generation_id: int) -> list[str]:
        """Возвращает пользовательские теги генерации (отсортированные
        без учёта регистра) — отдельно от _build_generations, для
        точечного доступа (например, при открытии MetadataEditor)."""

        rows = self._conn.execute(
            """SELECT tag FROM custom_tags
               WHERE generation_id = ? ORDER BY tag COLLATE NOCASE""",
            (generation_id,)
        ).fetchall()

        return [row[0] for row in rows]

    def set_custom_tags(self, generation_id: int, tags: list[str]) -> None:
        """Полная замена тегов для генерации (удобнее, чем add/remove) —
        существующие теги этой генерации удаляются и заменяются
        переданным списком.

        Пустые/состоящие только из пробелов теги отбрасываются;
        дубликаты (без учёта регистра) схлопываются, сохраняя написание
        первого встреченного варианта — UNIQUE(generation_id, tag)
        иначе привёл бы к ошибке при дубле с точно таким же написанием,
        а без схлопывания по регистру в списке могли бы соседствовать
        "Cat" и "cat" как формально разные теги.
        """

        conn = self._conn

        seen: dict[str, str] = {}

        for raw in tags:

            tag = raw.strip()

            if not tag:
                continue

            key = tag.lower()

            if key not in seen:
                seen[key] = tag

        cleaned = list(seen.values())

        conn.execute("DELETE FROM custom_tags WHERE generation_id = ?", (generation_id,))

        if cleaned:
            conn.executemany(
                "INSERT INTO custom_tags (generation_id, tag) VALUES (?, ?)",
                [(generation_id, tag) for tag in cleaned]
            )

        conn.commit()

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

    # ------------------------------------------------------------
    # семантический поиск

    def backfill_missing_embeddings(self, batch_size: int = 200) -> int:
        """Досчитывает эмбеддинги для генераций, у которых их ещё нет —
        закрывает два случая, для которых sync_folder сам их не считает:

        1) записи, унаследованные от версии приложения ДО задачи 3.1
           (миграция БД добавляет колонку embedding как NULL, а не
           пересчитывает её — файл на диске при этом не менялся, так
           что обычный sync_folder такую запись как "изменившуюся" не
           увидит и никогда не досчитает);
        2) записи, для которых модель эмбеддингов была недоступна в
           момент их первой синхронизации (например, несовместимая
           версия torch), а впоследствии окружение починили.

        Обрабатывает не более batch_size записей за один вызов — чтобы
        не блокировать UI надолго на большой библиотеке при первом
        запуске после обновления; при регулярных вызовах (например, при
        каждом открытии папки) библиотека дозаполняется постепенно.

        Возвращает количество реально обновлённых записей. Если модель
        эмбеддингов недоступна, ничего не делает и возвращает 0 сразу
        (не тратит время на бесполезные попытки на каждую строку).
        """

        if not embedding.is_available():
            return 0

        conn = self._conn

        rows = conn.execute(
            """SELECT id, positive FROM generations
               WHERE embedding IS NULL AND COALESCE(positive, '') != ''
               LIMIT ?""",
            (batch_size,)
        ).fetchall()

        if not rows:
            return 0

        texts = [
            self._embedding_text({"positive": positive})
            for _id, positive in rows
        ]

        embeddings = embedding.compute_embeddings_batch(texts)

        updated = 0

        for (gen_id, _positive), emb in zip(rows, embeddings):

            if emb is None:
                continue

            conn.execute(
                "UPDATE generations SET embedding = ? WHERE id = ?",
                (emb, gen_id)
            )
            updated += 1

        conn.commit()

        logger.info(
            "Досчитано эмбеддингов: %d из %d просмотренных (batch_size=%d)",
            updated, len(rows), batch_size
        )

        return updated

    def recompute_all_embeddings(self, batch_size: int = 200) -> int:
        """Принудительно пересчитывает эмбеддинги ВСЕХ генераций в БД
        (сначала обнуляя существующие), а не только тех, для кого он
        ещё не посчитан — в отличие от backfill_missing_embeddings.

        Нужен разово после смены логики вычисления эмбеддинга (в этой
        версии — переход на по-теговое кодирование промпта вместо
        одного вектора на весь текст целиком, см. app/core/embedding.py).
        Старые эмбеддинги, посчитанные до этого перехода, остаются
        формально валидными (не ломают поиск — при чтении BLOB просто
        интерпретируется как один "тег"), но дают заметно менее точные
        результаты и сами по себе не заменятся, пока не изменится JSON
        на диске — этот метод нужен, чтобы пересчитать их явно.

        Возвращает общее количество пересчитанных записей.
        """

        self._conn.execute("UPDATE generations SET embedding = NULL")
        self._conn.commit()

        total = 0

        while True:

            updated = self.backfill_missing_embeddings(batch_size=batch_size)

            if updated == 0:
                break

            total += updated

        logger.info("Принудительный пересчёт эмбеддингов завершён: %d записей", total)

        return total

    def clear_all_embeddings(self) -> int:
        """Удаляет посчитанные векторы эмбеддингов у ВСЕХ генераций (просто
        `embedding = NULL`, без пересчёта — в отличие от
        recompute_all_embeddings выше). Кнопка "Delete vectors" в
        настройках (см. SettingsWindow._on_delete_vectors_clicked) —
        освободить место в БД, если семантический поиск больше не
        используется, не дожидаясь следующего pip-install/переключения
        зависимостей. Работает независимо от того, доступна ли сейчас
        сама библиотека sentence-transformers/torch (это чистая
        операция над БД, вычислений не требует) — так что доступна,
        даже если пользователь уже удалил тяжёлые зависимости и просто
        хочет подчистить то, что от них осталось в БД.

        Возвращает число записей, у которых был вектор (и он был
        удалён) — 0, если удалять было нечего."""

        cursor = self._conn.execute(
            "UPDATE generations SET embedding = NULL WHERE embedding IS NOT NULL"
        )
        self._conn.commit()

        deleted = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0

        logger.info("Векторы эмбеддингов удалены: %d записей", deleted)

        return deleted

    def search_semantic(self, query: str, limit: int = 100) -> list[int]:
        """Возвращает id генераций из ВСЕЙ БД (а не только текущей
        открытой папки), чей промпт семантически ближе всего к query,
        отсортированные по убыванию сходства — до limit штук.

        В отличие от GenerationFilter (который ранжирует уже
        загруженный в память список генераций текущей папки — см. его
        FilterOptions.semantic_query), этот метод обращается прямо к
        БД и годится, например, для поиска по всей когда-либо
        просканированной библиотеке независимо от того, какая папка
        открыта в галерее сейчас.

        Возвращает пустой список, если query пустой, библиотека
        эмбеддингов недоступна, либо в БД ещё нет ни одной генерации
        с посчитанным эмбеддингом.
        """

        query = query.strip()

        if not query:
            return []

        query_bytes = embedding.compute_query_embedding(query)

        if query_bytes is None:
            logger.info(
                "search_semantic: не удалось вычислить эмбеддинг запроса "
                "(пустой текст либо модель недоступна)"
            )
            return []

        query_vec = embedding.bytes_to_array(query_bytes)

        rows = self._conn.execute(
            "SELECT id, embedding FROM generations WHERE embedding IS NOT NULL"
        ).fetchall()

        scored = [
            (gen_id, embedding.cosine_similarity(query_vec, emb_bytes))
            for gen_id, emb_bytes in rows
        ]

        scored.sort(key=lambda pair: pair[1], reverse=True)

        return [gen_id for gen_id, _score in scored[:limit]]

    # ------------------------------------------------------------
    # статистика (задача 3.2)

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

    # ------------------------------------------------------------

    def close(self) -> None:
        """Закрывает соединение с БД. Вызывать при завершении работы
        приложения."""

        self._conn.close()
