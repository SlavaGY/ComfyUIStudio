"""Метаданные с записью в JSON и историей изменений, пользовательские
отметки, теги и удаление генераций GenerationRepository."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from comfyui_studio.jsonfile import atomic_write_json
from comfyui_studio.promptvault.config import MAX_RATING, MIN_RATING
from comfyui_studio.promptvault.core.parser import parse_generation_data
from comfyui_studio.promptvault.core.repository_base import RepositoryBase

logger = logging.getLogger(__name__)


class EditMixin(RepositoryBase):
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

        # запись через временный файл + атомарное переименование (jsonfile) —
        # если процесс упадёт или будет прерван посередине записи,
        # исходный JSON останется целым файлом (либо старым, либо уже
        # полностью новым), а не окажется наполовину перезаписанным;
        # временный файл при ошибке jsonfile убирает сам
        try:
            atomic_write_json(path, raw)
        except OSError as e:
            logger.error("Не удалось сохранить %s: %s", path, e)
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

        conn = self._conn

        conn.execute(
            """UPDATE generations SET
                model=?, sampler=?, cfg=?, steps=?, positive=?, negative=?,
                extra_data=?, mtime=?
               WHERE id=?""",
            (
                data["model"], data["sampler"], data["cfg"], data["steps"],
                data["positive"], data["negative"], extra_json, mtime,
                generation_id
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
