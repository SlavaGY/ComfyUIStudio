import logging
import sqlite3
from pathlib import Path

from app.config import DB_PATH

logger = logging.getLogger(__name__)


CREATE_GENERATIONS = """
CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT,
    path TEXT NOT NULL,
    model TEXT,
    sampler TEXT,
    cfg REAL,
    steps INTEGER,
    timestamp TEXT,
    generation_time REAL,
    positive TEXT,
    negative TEXT,
    extra_data TEXT,
    mtime REAL NOT NULL,
    embedding BLOB,
    UNIQUE(timestamp, generation_time)
);

CREATE INDEX IF NOT EXISTS idx_generations_folder ON generations(folder);
CREATE INDEX IF NOT EXISTS idx_generations_model ON generations(model);
CREATE INDEX IF NOT EXISTS idx_generations_sampler ON generations(sampler);
CREATE INDEX IF NOT EXISTS idx_generations_timestamp ON generations(timestamp);
CREATE INDEX IF NOT EXISTS idx_generations_path ON generations(path);
"""

CREATE_LORAS = """
CREATE TABLE IF NOT EXISTS loras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    strength REAL,
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_loras_generation ON loras(generation_id);
CREATE INDEX IF NOT EXISTS idx_loras_filename ON loras(filename);
"""

CREATE_IMAGES = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    seed INTEGER
);

CREATE INDEX IF NOT EXISTS idx_images_generation ON images(generation_id);
"""

CREATE_USER_DATA = """
CREATE TABLE IF NOT EXISTS user_data (
    generation_id INTEGER PRIMARY KEY REFERENCES generations(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_user_data_favorite ON user_data(favorite);
CREATE INDEX IF NOT EXISTS idx_user_data_rating ON user_data(rating);
"""

# custom_tags — пользовательские теги (задача: пользовательские теги),
# в отличие от loras/images НЕ перезаписываются при update_generation
# (не часть исходного JSON, чисто пользовательские данные, как
# favorite/rating в user_data) — переживают редактирование метаданных
# и ре-синхронизацию с диском.
CREATE_CUSTOM_TAGS = """
CREATE TABLE IF NOT EXISTS custom_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    UNIQUE(generation_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_custom_tags_tag ON custom_tags(tag);
CREATE INDEX IF NOT EXISTS idx_custom_tags_generation ON custom_tags(generation_id);
"""

# metadata_history — история изменений метаданных (задача: история
# изменений метаданных): одна строка на КАЖДОЕ реально изменившееся
# поле при каждом вызове GenerationRepository.update_generation (см.
# _record_metadata_history), а не одна строка на весь вызов — так
# проще и показывать (по одной строке "поле: было -> стало"), и
# фильтровать по конкретному полю. field хранит имя атрибута
# Generation (не сырой ключ JSON-файла — см. _JSON_FIELD_MAP).
# old_value/new_value — TEXT (str() от исходного значения); история
# предназначена для просмотра человеком, не для программного
# восстановления типов.
#
# ON DELETE CASCADE — при удалении генерации (delete_generation) её
# история удаляется вместе с ней, как loras/images/custom_tags: без
# генерации, к которой она относится, история бессмысленна и хранить
# её осиротевшей незачем.
CREATE_METADATA_HISTORY = """
CREATE TABLE IF NOT EXISTS metadata_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    changed_at REAL NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);

CREATE INDEX IF NOT EXISTS idx_metadata_history_generation ON metadata_history(generation_id);
"""

# embedding — кэш семантического (векторного) представления промпта
# (см. app/core/embedding.py), NULL для генераций, для которых он ещё
# не посчитан (либо библиотека эмбеддингов не установлена) — такие
# генерации просто не участвуют в семантическом поиске, это не ошибка.
#
# ВАЖНО: генерация идентифицируется парой (timestamp, generation_time),
# а не path. Путь к файлу может меняться (пользователь переносит/переименовывает
# папку с генерациями), а вероятность совпадения timestamp+generation_time у
# двух разных генераций практически нулевая. Это же значит, что при переносе
# файла в другое место старая запись просто "переезжает" (обновляется path),
# а не создаётся заново — избранное и рейтинг не теряются.
SCHEMA = (
    CREATE_GENERATIONS + CREATE_LORAS + CREATE_IMAGES + CREATE_USER_DATA
    + CREATE_CUSTOM_TAGS + CREATE_METADATA_HISTORY
)


CHILD_TABLES = {
    "loras": CREATE_LORAS,
    "images": CREATE_IMAGES,
    "user_data": CREATE_USER_DATA,
    "custom_tags": CREATE_CUSTOM_TAGS,
    "metadata_history": CREATE_METADATA_HISTORY,
}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Открывает соединение с БД, создаёт схему при первом запуске
    (и мигрирует/чинит БД, оставшуюся от предыдущих версий приложения —
    без потери избранного/рейтинга).

    WAL + synchronous=NORMAL — сильно ускоряет частые мелкие записи
    (важно для рейтинга/избранного, которые могут меняться часто),
    почти без риска для целостности данных.
    """

    db_path = Path(db_path) if db_path else DB_PATH

    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Подключение к БД: %s", db_path)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    # LIKE в SQLite по умолчанию регистронезависим для ASCII — старая
    # реализация фильтрации по папке (Path.is_relative_to в Python)
    # была регистрозависимой, так что для эквивалентного поведения при
    # переходе на SQL LIKE (см. GenerationRepository.load_generations)
    # регистрозависимость нужно включить явно.
    conn.execute("PRAGMA case_sensitive_like = ON")

    if _needs_migration(conn):
        logger.warning(
            "Обнаружена БД со старой схемой (identity = path) — запуск миграции"
        )
        _migrate_path_identity_to_timestamp_identity(conn)
        logger.info("Миграция на identity (timestamp, generation_time) завершена")

    # у части баз, успевших пройти более раннюю версию миграции, SQLite
    # молча переписал FOREIGN KEY в дочерних таблицах на несуществующую
    # generations_v1 (см. _repair_dangling_foreign_keys) — чиним это
    # независимо от того, нужна ли полная миграция выше
    if _needs_fk_repair(conn):
        logger.warning(
            "Обнаружены битые FOREIGN KEY на generations_v1 — запуск восстановления"
        )
        _repair_dangling_foreign_keys(conn)
        logger.info("Восстановление внешних ключей завершено")

    if _needs_embedding_column(conn):
        logger.info("Добавление колонки embedding в generations (миграция для 3.1)")
        _migrate_add_embedding_column(conn)

    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(SCHEMA)
    conn.commit()

    return conn


def _needs_migration(conn: sqlite3.Connection) -> bool:
    """Старая схема (до этой версии) держала UNIQUE на path и NOT NULL
    на folder. Определяем её по тексту CREATE TABLE в sqlite_master —
    отдельного номера версии схемы раньше не было."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='generations'"
    ).fetchone()

    if row is None or row[0] is None:
        return False

    table_sql = row[0]

    return "UNIQUE(timestamp" not in table_sql and "UNIQUE (timestamp" not in table_sql


def _needs_fk_repair(conn: sqlite3.Connection) -> bool:
    """Обнаруживает БД, повреждённую более ранней версией миграции этого
    же приложения: при переименовании generations -> generations_v1
    SQLite (начиная с 3.25, если явно не отключить legacy_alter_table)
    заодно молча переписывает FOREIGN KEY в других таблицах, которые
    ссылались на generations, так что они начинают указывать на уже
    удалённую generations_v1 — сами данные при этом целы, битой
    оказывается только объявленная схема."""

    for table in CHILD_TABLES:

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()

        if row is not None and row[0] and "generations_v1" in row[0]:
            return True

    return False


def _repair_dangling_foreign_keys(conn: sqlite3.Connection) -> None:
    """Пересоздаёт таблицы с испорченным FOREIGN KEY (см.
    _needs_fk_repair), перенося все данные как есть — сами строки не
    повреждены, битым было только объявление внешнего ключа."""

    conn.execute("PRAGMA foreign_keys = OFF")

    for table, create_sql in CHILD_TABLES.items():

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()

        if row is None or row[0] is None or "generations_v1" not in row[0]:
            continue

        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_broken")
        conn.executescript(create_sql)
        conn.execute(f"INSERT INTO {table} SELECT * FROM {table}_broken")
        conn.execute(f"DROP TABLE {table}_broken")

    # леftover-таблица от прерванной на середине старой миграции —
    # если данные уже перенесены в generations, она больше не нужна
    conn.execute("DROP TABLE IF EXISTS generations_v1")

    conn.execute("PRAGMA foreign_keys = ON")

    conn.commit()


def _migrate_path_identity_to_timestamp_identity(conn: sqlite3.Connection) -> None:
    """Переводит БД со старой схемы (identity = path) на новую
    (identity = timestamp + generation_time), сохраняя избранное и
    рейтинг. Если из-за старой схемы в БД оказались задвоенные записи
    об одной и той же генерации (разные path, тот же timestamp +
    generation_time), они схлопываются в одну — с сохранением самого
    свежего mtime и объединением избранного/рейтинга (OR / MAX) со
    всех дублей."""

    conn.execute("PRAGMA foreign_keys = OFF")

    # ВАЖНО: без этой прагмы ALTER TABLE ... RENAME заодно молча
    # переписывает FOREIGN KEY во всех таблицах, ссылающихся на
    # переименовываемую (loras/images/user_data), нацеливая их на
    # generations_v1 — а после DROP TABLE generations_v1 в конце эти
    # ссылки становятся битыми. legacy_alter_table отключает этот
    # авто-рерайт, оставляя их textually указывать на "generations",
    # что корректно резолвится в НОВУЮ таблицу, создаваемую ниже.
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("ALTER TABLE generations RENAME TO generations_v1")
    conn.execute("PRAGMA legacy_alter_table = OFF")

    conn.executescript(SCHEMA)

    # для каждой группы (timestamp, generation_time) определяем id
    # "выжившей" записи — с самым свежим mtime
    conn.execute("""
        CREATE TEMP TABLE id_map AS
        SELECT g.id AS old_id, (
            SELECT id FROM generations_v1 g2
            WHERE g2.timestamp = g.timestamp
              AND g2.generation_time = g.generation_time
            ORDER BY g2.mtime DESC, g2.id DESC
            LIMIT 1
        ) AS new_id
        FROM generations_v1 g
    """)

    conn.execute("""
        INSERT INTO generations
            (id, folder, path, model, sampler, cfg, steps, timestamp,
             generation_time, positive, negative, extra_data, mtime)
        SELECT id, folder, path, model, sampler, cfg, steps, timestamp,
               generation_time, positive, negative, extra_data, mtime
        FROM generations_v1
        WHERE id IN (SELECT DISTINCT new_id FROM id_map)
    """)

    # loras/images дублей избыточны (это та же генерация) — переносить
    # нечего, просто убираем
    conn.execute("""
        DELETE FROM loras WHERE generation_id IN (
            SELECT old_id FROM id_map WHERE old_id != new_id
        )
    """)
    conn.execute("""
        DELETE FROM images WHERE generation_id IN (
            SELECT old_id FROM id_map WHERE old_id != new_id
        )
    """)

    # избранное/рейтинг объединяем со всех дублей группы (чтобы не
    # потерять то, что могло быть проставлено под любым из путей);
    # UPSERT — потому что у "выжившей" записи user_data уже может
    # существовать (это её собственные старые данные)
    conn.execute("""
        INSERT INTO user_data (generation_id, favorite, rating)
        SELECT im.new_id,
               MAX(COALESCE(u.favorite, 0)),
               MAX(COALESCE(u.rating, 0))
        FROM id_map im
        LEFT JOIN user_data u ON u.generation_id = im.old_id
        GROUP BY im.new_id
        HAVING MAX(COALESCE(u.favorite, 0)) > 0 OR MAX(COALESCE(u.rating, 0)) > 0
        ON CONFLICT(generation_id) DO UPDATE SET
            favorite = excluded.favorite,
            rating = excluded.rating
    """)

    conn.execute("""
        DELETE FROM user_data WHERE generation_id IN (
            SELECT old_id FROM id_map WHERE old_id != new_id
        )
    """)

    conn.execute("DROP TABLE generations_v1")
    conn.execute("DROP TABLE id_map")

    conn.execute("PRAGMA foreign_keys = ON")

    conn.commit()


def _needs_embedding_column(conn: sqlite3.Connection) -> bool:
    """True, если таблица generations уже существует (БД от версии
    приложения до задачи 3.1), но в ней ещё нет колонки embedding.

    Для полностью новой БД таблицы ещё нет — она будет создана уже с
    колонкой embedding самим SCHEMA/CREATE_GENERATIONS, отдельная
    миграция ей не нужна.
    """

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='generations'"
    ).fetchone()

    if row is None or row[0] is None:
        return False

    columns = {info[1] for info in conn.execute("PRAGMA table_info(generations)")}

    return "embedding" not in columns


def _migrate_add_embedding_column(conn: sqlite3.Connection) -> None:
    """Добавляет колонку embedding к уже существующей таблице
    generations, оставшейся от версии приложения до задачи 3.1.

    Простой ALTER TABLE ... ADD COLUMN — SQLite добавляет новую колонку
    со значением NULL для всех существующих строк без переписывания
    таблицы, данные (включая избранное/рейтинг в других таблицах) не
    затрагиваются. Сами эмбеддинги досчитываются лениво: при следующем
    sync_folder/update_generation для каждой генерации, у которой их
    ещё нет.
    """

    conn.execute("ALTER TABLE generations ADD COLUMN embedding BLOB")
    conn.commit()
