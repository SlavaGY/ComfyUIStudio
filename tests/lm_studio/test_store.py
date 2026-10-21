"""Keep existing conversations intact while listing only indexed summaries."""

import json
import sqlite3
from unittest.mock import patch

from comfyui_studio.lm_studio.store import ChatStore


def test_migrates_old_library_once_without_changing_conversations(tmp_path):
    path = tmp_path / "chats.sqlite3"
    chat = {"id": "old", "title": "Старый диалог", "model": "model", "updated": 123,
            "messages": [{"role": "assistant", "content": "history" * 10000}]}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE chats (id TEXT PRIMARY KEY, updated REAL, payload TEXT)")
        db.execute("INSERT INTO chats VALUES (?, ?, ?)", (chat["id"], chat["updated"], json.dumps(chat)))

    store = ChatStore(path)
    expected = [{key: chat[key] for key in ("id", "title", "model", "updated")}]
    assert store.get("old") == chat
    with patch("comfyui_studio.lm_studio.store.json.loads", side_effect=AssertionError("read body")):
        assert store.list() == expected
        assert ChatStore(path).list() == expected


def test_summaries_follow_save_order_rename_and_delete(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    first, second = store.create(), store.create()
    first.update(title="Renamed", model="new-model")
    store.save(first)
    assert store.list() == [
        {key: chat[key] for key in ("id", "title", "model", "updated")}
        for chat in (first, second)
    ]
    assert store.get(first["id"])["title"] == "Renamed"
    store.delete(first["id"])
    assert [chat["id"] for chat in store.list()] == [second["id"]]


def test_listing_does_not_select_payload(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    chat = store.create()
    # Even an unreadable body must not affect the independent summary query.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE chats SET payload='not JSON'")
    assert store.list() == [{key: chat[key] for key in ("id", "title", "model", "updated")}]
