"""
promptgen_history.py
====================
История запросов «Генератора промптов» в SQLite: один файл
%APPDATA%\\ComfyUIStudio\\promptgen_history.db.

ПИШЕТ Imagine (imagine/backend/promptgen.py -- по одной записи на каждую
задачу, в том числе неудачную и отменённую), ЧИТАЕТ вкладка «История
промптов» в настройках Studio (launcher/ui/settings/prompt_history_page.py).
Это два разных процесса, поэтому:

  * соединение открывается на одну операцию и сразу закрывается --
    ничего долгоживущего, что мог бы заблокировать другой процесс;
  * включён режим WAL: читатель (Studio) не мешает писателю (Imagine) и
    наоборот; busy_timeout даёт короткую паузу вместо мгновенной ошибки
    «database is locked».

Модуль, как и shared_promptgen.py, не зависит ни от Qt, ни от FastAPI --
им пользуются и бэкенд Imagine, и Qt-страница, и тесты.

Поиск без учёта регистра работает и для кириллицы: встроенные lower()/LIKE
в SQLite понимают только ASCII, поэтому подключается своя функция
ulower() на Python (str.lower()). Названия колонок для сортировки берутся
ТОЛЬКО из белого списка SORT_COLUMNS -- пользовательская строка никогда
не попадает в SQL как идентификатор.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from typing import Iterable, Optional

from comfyui_studio import shared_promptgen

DB_FILE_NAME = "promptgen_history.db"
SCHEMA_VERSION = 1

# ключ сортировки (его выбирает интерфейс) -> колонка таблицы
SORT_COLUMNS = {
    "started_at": "started_at",
    "state": "state",
    "user_text": "user_text",
    "image_name": "image_name",
    "total_tokens": "total_tokens",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "total_s": "total_s",
    "gen_s": "gen_s",
    "tok_per_s": "tok_per_s",
    "output_text": "output_text",
}

# По каким колонкам идёт поиск (слова запроса -- через пробел, все должны
# встретиться; каждое -- в любой из этих колонок).
SEARCH_COLUMNS = ("user_text", "full_prompt", "output_text", "image_name", "job_id", "model_name")

_TEXT_COLUMNS = (
    "job_id", "state", "error", "prefix", "user_text", "full_prompt", "image_name",
    "output_text", "raw_output", "tokens_source", "model_name", "mmproj_name",
)
_NUM_COLUMNS = (
    "started_at", "finished_at", "has_image", "truncated", "prompt_tokens",
    "completion_tokens", "total_tokens", "total_s", "load_s", "ttft_s", "gen_s",
    "tok_per_s", "ctx_size",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id            TEXT    NOT NULL DEFAULT '',
    started_at        REAL    NOT NULL,            -- unix-время начала задачи (UTC)
    finished_at       REAL,                        -- unix-время конца задачи
    state             TEXT    NOT NULL DEFAULT '', -- done | error | cancelled
    error             TEXT    NOT NULL DEFAULT '',
    prefix            TEXT    NOT NULL DEFAULT '', -- префикс из настроек (как был на момент запроса)
    user_text         TEXT    NOT NULL DEFAULT '', -- что написал пользователь
    full_prompt       TEXT    NOT NULL DEFAULT '', -- итоговый текст, ушедший модели
    has_image         INTEGER NOT NULL DEFAULT 0,
    image_name        TEXT    NOT NULL DEFAULT '',
    output_text       TEXT    NOT NULL DEFAULT '', -- ответ без «рассуждений»
    raw_output        TEXT    NOT NULL DEFAULT '', -- сырой ответ, только если отличается от output_text
    truncated         INTEGER NOT NULL DEFAULT 0,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    tokens_source     TEXT    NOT NULL DEFAULT '', -- usage | timings | chunks
    total_s           REAL,                        -- вся задача, включая загрузку модели
    load_s            REAL,                        -- загрузка модели
    ttft_s            REAL,                        -- до первого токена
    gen_s             REAL,                        -- от первого токена до конца
    tok_per_s         REAL,
    model_name        TEXT    NOT NULL DEFAULT '',
    mmproj_name       TEXT    NOT NULL DEFAULT '',
    ctx_size          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_generations_started ON generations(started_at);
"""


def db_path() -> str:
    return os.path.join(shared_promptgen.SHARED_DIR, DB_FILE_NAME)


def _ulower(value):
    return value.lower() if isinstance(value, str) else ""


@contextlib.contextmanager
def _connect(path: Optional[str] = None):
    """Соединение на одну операцию: commit при успехе, rollback при
    исключении, всегда close."""
    path = path or db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.create_function("ulower", 1, _ulower, deterministic=True)
        conn.execute("PRAGMA busy_timeout = 10000")
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    # WAL запоминается в самом файле; ставим при создании/миграции
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ---------------------------------------------------------------------------
# запись
# ---------------------------------------------------------------------------

def add_record(record: dict, path: Optional[str] = None) -> int:
    """Добавляет запись и возвращает её id. Неизвестные ключи игнорируются,
    отсутствующие -- заполняются значениями по умолчанию."""
    started = record.get("started_at")
    values = {"started_at": time.time() if started is None else float(started)}
    for key in _TEXT_COLUMNS:
        value = record.get(key)
        values[key] = "" if value is None else str(value)
    for key in _NUM_COLUMNS:
        if key == "started_at":
            continue
        value = record.get(key)
        values[key] = None if value is None else value
    for key in ("has_image", "truncated"):
        values[key] = 1 if values[key] else 0
    columns = list(values)
    sql = (
        f"INSERT INTO generations ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    with _connect(path) as conn:
        return conn.execute(sql, [values[c] for c in columns]).lastrowid


# ---------------------------------------------------------------------------
# чтение
# ---------------------------------------------------------------------------

def _where(search: str) -> tuple[str, list]:
    terms = [t.lower() for t in (search or "").split() if t]
    if not terms:
        return "", []
    haystack = " || ' ' || ".join(f"COALESCE({c}, '')" for c in SEARCH_COLUMNS)
    clause = " AND ".join(f"instr(ulower({haystack}), ?) > 0" for _ in terms)
    return " WHERE " + clause, terms


def query(
    search: str = "",
    sort_by: str = "started_at",
    descending: bool = True,
    limit: int = 100,
    offset: int = 0,
    path: Optional[str] = None,
) -> list[dict]:
    """Страница записей. Большие текстовые поля (префикс, полный запрос,
    сырой ответ) в список НЕ входят -- их отдаёт get() для выбранной
    записи; в списке -- только то, что показывает таблица."""
    column = SORT_COLUMNS.get(sort_by, "started_at")
    direction = "DESC" if descending else "ASC"
    where, params = _where(search)
    sql = (
        "SELECT id, job_id, started_at, finished_at, state, error, user_text, has_image, "
        "image_name, output_text, truncated, prompt_tokens, completion_tokens, total_tokens, "
        "total_s, load_s, ttft_s, gen_s, tok_per_s, model_name "
        f"FROM generations{where} "
        f"ORDER BY {column} {direction}, id {direction} LIMIT ? OFFSET ?"
    )
    with _connect(path) as conn:
        rows = conn.execute(sql, [*params, int(limit), int(offset)]).fetchall()
    return [dict(r) for r in rows]


def get(record_id: int, path: Optional[str] = None) -> Optional[dict]:
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM generations WHERE id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def count(search: str = "", path: Optional[str] = None) -> int:
    where, params = _where(search)
    with _connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM generations{where}", params).fetchone()[0]


def stats(search: str = "", path: Optional[str] = None) -> dict:
    """Сводка по выборке: число запросов, сумма токенов, среднее время и
    скорость (только по успешным задачам)."""
    where, params = _where(search)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_tokens), 0) AS tokens, "
            "AVG(CASE WHEN state = 'done' THEN total_s END) AS avg_total_s, "
            "AVG(CASE WHEN state = 'done' THEN tok_per_s END) AS avg_tok_per_s "
            f"FROM generations{where}",
            params,
        ).fetchone()
    return dict(row)


def fingerprint(path: Optional[str] = None) -> tuple[int, int]:
    """(число записей, максимальный id) -- дешёвый способ понять, что
    Imagine дописал или кто-то удалил записи, пока страница открыта."""
    with _connect(path) as conn:
        row = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM generations").fetchone()
    return int(row[0]), int(row[1])


# ---------------------------------------------------------------------------
# удаление
# ---------------------------------------------------------------------------

def delete(ids: Iterable[int], path: Optional[str] = None) -> int:
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    deleted = 0
    with _connect(path) as conn:
        for start in range(0, len(ids), 500):  # лимит SQLite на число параметров
            chunk = ids[start:start + 500]
            cur = conn.execute(
                f"DELETE FROM generations WHERE id IN ({', '.join('?' for _ in chunk)})", chunk
            )
            deleted += cur.rowcount
    return deleted


def clear(path: Optional[str] = None) -> int:
    with _connect(path) as conn:
        return conn.execute("DELETE FROM generations").rowcount
