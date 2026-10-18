"""Хранение и синхронизация генераций в SQLite.

GenerationRepository объединяет запросы (repository_query), редактирование
(repository_edit), перенос данных (repository_transfer) и статистику
(repository_stats). Здесь находятся соединение с БД и синхронизация с диском."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from comfyui_studio.promptvault.core.database import connect
from comfyui_studio.promptvault.core.parser import parse_generation_data
from comfyui_studio.promptvault.core.repository_edit import EditMixin
from comfyui_studio.promptvault.core.repository_query import QueryMixin
from comfyui_studio.promptvault.core.repository_stats import StatisticsMixin
from comfyui_studio.promptvault.core.repository_transfer import TransferMixin

logger = logging.getLogger(__name__)


class GenerationRepository(TransferMixin, QueryMixin, EditMixin, StatisticsMixin):
    """Хранит данные о генерациях и пользовательские отметки в SQLite.

    JSON-файл перечитывается при изменении mtime. Избранное и рейтинг
    обновляются точечными SQL-запросами."""

    def __init__(self, db_path: str | Path | None = None):
        self._conn = connect(db_path)

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

        for path, mtime, data, extra_json in parsed:

            try:
                gen_id, is_new = self._upsert_generation(
                    folder, path, mtime, data, extra_json
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
    ) -> tuple[int, bool]:
        """Вставляет генерацию, либо, если запись с таким
        (timestamp, generation_time) уже есть — обновляет её (в т.ч.
        переносит path на новое место).

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
                    positive=?, negative=?, extra_data=?, mtime=?
                   WHERE id=?""",
                (
                    folder, path, data["model"], data["sampler"], data["cfg"],
                    data["steps"], data["positive"], data["negative"],
                    extra_json, mtime, gen_id
                )
            )

            return gen_id, False

        cur = conn.execute(
            """INSERT INTO generations
                (folder, path, model, sampler, cfg, steps, timestamp,
                 generation_time, positive, negative, extra_data, mtime)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                folder, path, data["model"], data["sampler"], data["cfg"],
                data["steps"], data["timestamp"], data["generation_time"],
                data["positive"], data["negative"], extra_json, mtime,
            )
        )

        assert cur.lastrowid is not None, "lastrowid отсутствует сразу после INSERT"

        return cur.lastrowid, True

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

        conn = self._conn

        gen_id, _is_new = self._upsert_generation(
            str(path.parent), str(path), mtime, data, extra_json
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

    def close(self) -> None:
        """Закрывает соединение с БД. Вызывать при завершении работы
        приложения."""

        self._conn.close()
