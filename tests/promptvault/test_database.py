"""Тесты для app/core/database.py — создания схемы, миграции со старой
схемы (identity = path -> identity = timestamp+generation_time) и
восстановления БД, повреждённых более ранней версией миграции.

Запуск: pytest tests/test_database.py -v
"""

import sqlite3

import pytest

from comfyui_studio.promptvault.core.database import connect

OLD_SCHEMA = """
CREATE TABLE generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    model TEXT, sampler TEXT, cfg REAL, steps INTEGER,
    timestamp TEXT, generation_time REAL, positive TEXT, negative TEXT,
    extra_data TEXT, mtime REAL NOT NULL
);
CREATE TABLE loras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL, strength REAL, source TEXT
);
CREATE TABLE images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL, seed INTEGER
);
CREATE TABLE user_data (
    generation_id INTEGER PRIMARY KEY REFERENCES generations(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def db_path(tmp_path):
    """Путь к БД во временной директории — не трогает реальную БД
    пользователя в ~/.promptvault."""

    return tmp_path / "test.db"


def _make_old_schema_db(path, rows=(), user_data=(), loras=(), images=()):
    """Создаёт файл БД по СТАРОЙ схеме (identity = path) и наполняет
    его переданными строками — эмулирует БД, оставшуюся от более
    ранней версии приложения."""

    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)

    for row in rows:
        conn.execute(
            """INSERT INTO generations
                (id, folder, path, model, timestamp, generation_time, mtime)
               VALUES (?,?,?,?,?,?,?)""",
            row
        )

    for row in user_data:
        conn.execute(
            "INSERT INTO user_data (generation_id, favorite, rating) VALUES (?,?,?)",
            row
        )

    for row in loras:
        conn.execute(
            "INSERT INTO loras (generation_id, filename, strength) VALUES (?,?,?)",
            row
        )

    for row in images:
        conn.execute(
            "INSERT INTO images (generation_id, image_path, seed) VALUES (?,?,?)",
            row
        )

    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# создание новой БД

class TestNewDatabase:

    def test_creates_all_tables(self, db_path):

        conn = connect(db_path)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert {"generations", "loras", "images", "user_data"} <= tables

        conn.close()

    def test_generations_identity_is_timestamp_and_generation_time(self, db_path):
        """Новая БД сразу создаётся с правильным UNIQUE-ограничением,
        без необходимости в миграции."""

        conn = connect(db_path)

        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='generations'"
        ).fetchone()[0]

        assert "UNIQUE(timestamp" in table_sql or "UNIQUE (timestamp" in table_sql

        conn.close()

    def test_foreign_keys_point_to_generations(self, db_path):

        conn = connect(db_path)

        for table in ("loras", "images", "user_data"):
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()[0]

            assert "generations_v1" not in table_sql
            assert "generations" in table_sql

        conn.close()

    def test_duplicate_timestamp_and_generation_time_rejected(self, db_path):
        """UNIQUE(timestamp, generation_time) должен реально работать —
        это основа идентичности генерации."""

        conn = connect(db_path)

        conn.execute(
            """INSERT INTO generations
                (folder, path, timestamp, generation_time, mtime)
               VALUES ('/f', '/f/a.json', 'ts1', 1.0, 100.0)"""
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO generations
                    (folder, path, timestamp, generation_time, mtime)
                   VALUES ('/f', '/f/b.json', 'ts1', 1.0, 200.0)"""
            )

        conn.close()


class TestMetadataHistoryTable:
    """Задача: история изменений метаданных — таблица metadata_history
    создаётся вместе с остальной схемой, для новой БД без отдельной
    миграции (см. TestNewDatabase выше — она уже часть SCHEMA), и
    каскадно чистится при удалении генерации."""

    def test_table_created_for_new_database(self, db_path):

        conn = connect(db_path)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert "metadata_history" in tables

        conn.close()

    def test_existing_database_without_the_table_gets_it_added(self, db_path):
        """БД от версии приложения до этой задачи не имела
        metadata_history — CREATE TABLE IF NOT EXISTS в SCHEMA должен
        добавить её при следующем connect(), как и для custom_tags
        ранее, без отдельной функции миграции (это НОВАЯ таблица, а не
        новая колонка существующей — в отличие от embedding)."""

        _make_old_schema_db(db_path, rows=[(1, "/f", "/f/a.json", "modelA", "ts1", 1.0, 100.0)])

        conn = connect(db_path)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert "metadata_history" in tables

        conn.close()

    def test_cascades_on_generation_delete(self, db_path):

        conn = connect(db_path)

        conn.execute(
            """INSERT INTO generations
                (id, folder, path, timestamp, generation_time, mtime)
               VALUES (1, '/f', '/f/a.json', 'ts1', 1.0, 100.0)"""
        )
        conn.execute(
            """INSERT INTO metadata_history
                (generation_id, changed_at, field, old_value, new_value)
               VALUES (1, 100.0, 'cfg', '7.0', '9.0')"""
        )
        conn.commit()

        conn.execute("DELETE FROM generations WHERE id=1")
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM metadata_history").fetchone()[0]
        assert count == 0

        conn.close()


# ------------------------------------------------------------------
# миграция со старой схемы

class TestMigrationFromOldSchema:

    def test_simple_row_migrates_with_favorite_and_rating(self, db_path):

        _make_old_schema_db(
            db_path,
            rows=[(1, "/f", "/f/gen1.json", "modelA", "ts1", 1.5, 100.0)],
            user_data=[(1, 1, 4)],
            loras=[(1, "loraX.safetensors", 0.8)],
        )

        conn = connect(db_path)

        gens = conn.execute("SELECT id, path, timestamp FROM generations").fetchall()
        assert len(gens) == 1

        gen_id = gens[0][0]

        favorite, rating = conn.execute(
            "SELECT favorite, rating FROM user_data WHERE generation_id = ?",
            (gen_id,)
        ).fetchone()

        assert (favorite, rating) == (1, 4)

        lora = conn.execute(
            "SELECT filename FROM loras WHERE generation_id = ?", (gen_id,)
        ).fetchone()

        assert lora == ("loraX.safetensors",)

        conn.close()

    def test_migration_result_passes_new_schema_checks(self, db_path):
        """После миграции схема должна быть неотличима от только что
        созданной новой БД (тот же UNIQUE, корректные FK)."""

        _make_old_schema_db(
            db_path,
            rows=[(1, "/f", "/f/gen1.json", "modelA", "ts1", 1.5, 100.0)],
        )

        conn = connect(db_path)

        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='generations'"
        ).fetchone()[0]
        assert "UNIQUE(timestamp" in table_sql or "UNIQUE (timestamp" in table_sql

        for table in ("loras", "images", "user_data"):
            child_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()[0]
            assert "generations_v1" not in child_sql

        conn.close()

    def test_duplicate_generation_under_two_paths_is_merged(self, db_path):
        """Баг, приведший к этой миграции: одна и та же генерация могла
        попасть в БД дважды под разными path (например, родительская
        папка и её же вложенная подпапка просканированы отдельно).
        Миграция должна схлопнуть дубли в одну запись."""

        _make_old_schema_db(
            db_path,
            rows=[
                (1, "/old", "/old/gen2.json", "modelB", "ts2", 2.0, 200.0),
                (2, "/new", "/new/gen2_copy.json", "modelB", "ts2", 2.0, 300.0),
            ],
            user_data=[
                (1, 0, 3),  # рейтинг проставлен под первым путём
                (2, 1, 0),  # избранное проставлено под вторым (более свежим) путём
            ],
            images=[(2, "img2.png", 42)],
        )

        conn = connect(db_path)

        rows = conn.execute("SELECT id, path FROM generations WHERE timestamp='ts2'").fetchall()
        assert len(rows) == 1, "дубли должны были схлопнуться в одну запись"

        gen_id, path = rows[0]

        # путь остаётся от записи с самым свежим mtime
        assert path == "/new/gen2_copy.json"

        # избранное/рейтинг объединены (OR / MAX) со всех дублей
        favorite, rating = conn.execute(
            "SELECT favorite, rating FROM user_data WHERE generation_id = ?",
            (gen_id,)
        ).fetchone()
        assert (favorite, rating) == (1, 3)

        images = conn.execute(
            "SELECT image_path, seed FROM images WHERE generation_id = ?",
            (gen_id,)
        ).fetchall()
        assert images == [("img2.png", 42)]

        conn.close()

    def test_no_data_loss_across_multiple_independent_rows(self, db_path):
        """Миграция не должна трогать записи, у которых нет дублей."""

        _make_old_schema_db(
            db_path,
            rows=[
                (1, "/f", "/f/a.json", "modelA", "ts1", 1.0, 100.0),
                (2, "/f", "/f/b.json", "modelB", "ts2", 2.0, 200.0),
                (3, "/f", "/f/c.json", "modelC", "ts3", 3.0, 300.0),
            ],
            user_data=[(1, 1, 5), (2, 0, 2), (3, 1, 1)],
        )

        conn = connect(db_path)

        count = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        assert count == 3

        favorites = dict(
            conn.execute(
                """SELECT g.timestamp, u.favorite FROM generations g
                   JOIN user_data u ON u.generation_id = g.id"""
            ).fetchall()
        )
        assert favorites == {"ts1": 1, "ts2": 0, "ts3": 1}

        conn.close()


# ------------------------------------------------------------------
# восстановление битых внешних ключей

class TestForeignKeyRepair:

    def _make_db_with_dangling_fk(self, path):
        """Эмулирует БД, повреждённую более ранней версией миграции:
        новая схема generations, но loras/images/user_data всё ещё
        ссылаются на несуществующую generations_v1 (реальный баг,
        вызванный поведением SQLite ALTER TABLE RENAME по умолчанию)."""

        conn = sqlite3.connect(str(path))

        conn.execute("""
            CREATE TABLE generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder TEXT, path TEXT NOT NULL, model TEXT, sampler TEXT,
                cfg REAL, steps INTEGER, timestamp TEXT, generation_time REAL,
                positive TEXT, negative TEXT, extra_data TEXT, mtime REAL NOT NULL,
                UNIQUE(timestamp, generation_time)
            )
        """)
        conn.execute("""
            CREATE TABLE loras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generation_id INTEGER NOT NULL REFERENCES generations_v1(id) ON DELETE CASCADE,
                filename TEXT NOT NULL, strength REAL, source TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generation_id INTEGER NOT NULL REFERENCES generations_v1(id) ON DELETE CASCADE,
                image_path TEXT NOT NULL, seed INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE user_data (
                generation_id INTEGER PRIMARY KEY REFERENCES generations_v1(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute(
            "INSERT INTO generations (id, folder, path, model, timestamp, generation_time, mtime) "
            "VALUES (1, '/f', '/f/gen1.json', 'modelA', 'ts1', 1.5, 100.0)"
        )
        conn.execute("INSERT INTO loras (generation_id, filename, strength) VALUES (1, 'loraX.safetensors', 0.8)")
        conn.execute("INSERT INTO images (generation_id, image_path, seed) VALUES (1, 'img1.png', 42)")
        conn.execute("INSERT INTO user_data (generation_id, favorite, rating) VALUES (1, 1, 5)")

        conn.commit()
        conn.close()

    def test_repairs_dangling_foreign_keys(self, db_path):

        self._make_db_with_dangling_fk(db_path)

        conn = connect(db_path)

        for table in ("loras", "images", "user_data"):
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()[0]
            assert "generations_v1" not in table_sql

        conn.close()

    def test_repair_preserves_data(self, db_path):

        self._make_db_with_dangling_fk(db_path)

        conn = connect(db_path)

        lora = conn.execute("SELECT filename, strength FROM loras WHERE generation_id=1").fetchone()
        assert lora == ("loraX.safetensors", 0.8)

        image = conn.execute("SELECT image_path, seed FROM images WHERE generation_id=1").fetchone()
        assert image == ("img1.png", 42)

        user_data = conn.execute("SELECT favorite, rating FROM user_data WHERE generation_id=1").fetchone()
        assert user_data == (1, 5)

        conn.close()

    def test_repaired_db_accepts_writes_with_foreign_keys_enforced(self, db_path):
        """Регрессионный тест на конкретный баг: DELETE FROM loras падал
        с sqlite3.OperationalError: no such table: main.generations_v1."""

        self._make_db_with_dangling_fk(db_path)

        conn = connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")

        # раньше именно эта операция роняла приложение
        conn.execute("DELETE FROM loras WHERE generation_id = ?", (1,))

        conn.close()

    def test_no_repair_needed_for_healthy_db(self, db_path):
        """На здоровой БД восстановление не должно ничего ломать —
        повторный connect() идемпотентен."""

        conn1 = connect(db_path)
        conn1.execute(
            "INSERT INTO generations (folder, path, timestamp, generation_time, mtime) "
            "VALUES ('/f', '/f/a.json', 'ts1', 1.0, 100.0)"
        )
        conn1.commit()
        conn1.close()

        conn2 = connect(db_path)

        count = conn2.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        assert count == 1

        conn2.close()


class TestEmbeddingColumnMigration:
    """Тесты для задачи 3.1 — добавления колонки embedding к БД,
    оставшейся от версии приложения до этой задачи."""

    def test_new_database_has_embedding_column(self, db_path):

        conn = connect(db_path)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
        assert "embedding" in columns

        conn.close()

    def test_old_database_without_embedding_gets_column_added(self, db_path):

        # эмулируем БД версии до задачи 3.1: полная актуальная схема
        # (identity уже на timestamp+generation_time), но без embedding
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder TEXT, path TEXT NOT NULL,
                model TEXT, sampler TEXT, cfg REAL, steps INTEGER,
                timestamp TEXT, generation_time REAL,
                positive TEXT, negative TEXT, extra_data TEXT,
                mtime REAL NOT NULL,
                UNIQUE(timestamp, generation_time)
            );
        """)
        conn.execute(
            """INSERT INTO generations
                (folder, path, timestamp, generation_time, mtime)
               VALUES ('f', 'p.json', 'ts1', 1.0, 100.0)"""
        )
        conn.commit()
        conn.close()

        conn = connect(db_path)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
        assert "embedding" in columns

        # существующая строка должна пережить миграцию без потерь,
        # embedding для неё — NULL (ещё не посчитан)
        row = conn.execute(
            "SELECT path, embedding FROM generations WHERE timestamp = 'ts1'"
        ).fetchone()
        assert row[0] == "p.json"
        assert row[1] is None

        conn.close()

    def test_reopening_already_migrated_db_is_a_noop(self, db_path):
        """Повторное открытие уже смигрированной БД не должно падать
        (ALTER TABLE ADD COLUMN на уже существующую колонку упал бы)."""

        connect(db_path).close()
        conn = connect(db_path)  # второе открытие — не должно бросить исключение

        columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
        assert "embedding" in columns

        conn.close()
