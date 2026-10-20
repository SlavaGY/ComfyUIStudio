"""Local conversation storage, independent of the original LM Studio library."""

import json
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

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM chats ORDER BY updated DESC").fetchall()
        return [
            {key: chat[key] for key in ("id", "title", "model", "updated")}
            for (payload,) in rows for chat in [json.loads(payload)]
        ]

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
            db.execute("INSERT OR REPLACE INTO chats VALUES (?, ?, ?)",
                       (chat["id"], chat["updated"], json.dumps(chat, ensure_ascii=False)))

    def delete(self, chat_id):
        with self.connect() as db:
            db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
