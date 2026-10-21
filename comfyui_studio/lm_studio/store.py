"""Local conversation storage, independent of the original LM Studio library."""

import base64
import binascii
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class ChatStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, updated REAL, payload TEXT)")
            # Migrate existing libraries once; listing must never read message bodies.
            columns = {row[1] for row in db.execute("PRAGMA table_info(chats)")}
            for column in ("title", "model"):
                if column not in columns:
                    db.execute(f"ALTER TABLE chats ADD COLUMN {column} TEXT")
            for chat_id, payload in db.execute(
                "SELECT id, payload FROM chats WHERE title IS NULL OR model IS NULL"
            ):
                chat = json.loads(payload)
                db.execute("UPDATE chats SET title=?, model=? WHERE id=?",
                           (chat.get("title", ""), chat.get("model", ""), chat_id))
            db.execute("CREATE INDEX IF NOT EXISTS idx_chats_page ON chats(updated DESC, id DESC, title, model)")
            db.execute("DROP INDEX IF EXISTS idx_chats_summary")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT id, title, model, updated FROM chats ORDER BY updated DESC, id DESC").fetchall()
        return [dict(zip(("id", "title", "model", "updated"), row)) for row in rows]

    def list_page(self, limit=50, cursor=None, search=""):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid page size")
        conditions, params = [], []
        if cursor:
            try:
                updated, chat_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if (not isinstance(updated, (int, float)) or not math.isfinite(updated)
                        or not isinstance(chat_id, str) or not chat_id):
                    raise ValueError("Invalid cursor")
            except (ValueError, TypeError, OverflowError, binascii.Error) as error:
                raise ValueError("Invalid cursor") from error
            conditions.append("(updated, id) < (?, ?)")
            params.extend((updated, chat_id))
        if search:
            conditions.append("instr(casefold(CASE WHEN title='' THEN 'Новый диалог New conversation' ELSE title END), ?) > 0")
            params.append(search.casefold())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, title, model, updated FROM chats" + where
                + " ORDER BY updated DESC, id DESC LIMIT ?", [*params, limit + 1]
            ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more:
            next_cursor = base64.urlsafe_b64encode(json.dumps([rows[-1][3], rows[-1][0]]).encode()).decode().rstrip("=")
        return {"chats": [dict(zip(("id", "title", "model", "updated"), row)) for row in rows],
                "next_cursor": next_cursor}

    def get(self, chat_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM chats WHERE id=?", (chat_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def create(self):
        chat = {
            "id": uuid.uuid4().hex, "title": "", "model": "", "system": "",
            "temperature": 0.7, "top_p": 0.95, "max_tokens": 2048,
            "messages": [], "updated": time.time(),
        }
        self.save(chat)
        return chat

    def save(self, chat):
        chat["updated"] = time.time()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO chats (id, updated, payload, title, model) VALUES (?, ?, ?, ?, ?)",
                       (chat["id"], chat["updated"], json.dumps(chat, ensure_ascii=False),
                        chat["title"], chat["model"]))

    def delete(self, chat_id):
        with self.connect() as db:
            db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
