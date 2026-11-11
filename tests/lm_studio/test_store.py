"""Keep existing conversations intact while listing only indexed summaries."""

import json
import sqlite3
from unittest.mock import patch

import pytest

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


def test_cursor_pages_cover_tied_timestamps_without_duplicates(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    with store.connect() as db:
        db.executemany("INSERT INTO chats (id, updated, title, model, payload) VALUES (?, ?, ?, '', 'not JSON')",
                       [(f"{i:04d}", i // 3, f"Chat {i}") for i in range(123)])
    found, cursor = [], None
    while True:
        page = store.list_page(7, cursor)
        assert 0 < len(page["chats"]) <= 7
        found.extend(item["id"] for item in page["chats"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert found == [item["id"] for item in store.list()]
    assert len(set(found)) == 123


def test_search_is_unicode_literal_and_covers_later_pages(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    with store.connect() as db:
        db.executemany("INSERT INTO chats (id, updated, title, model, payload) VALUES (?, ?, ?, '', '{}')",
                       [(str(i), i, "Обычный") for i in range(100)])
        db.execute("UPDATE chats SET title='ЁЖИК 100%_test' WHERE id='0'")
    assert store.list_page(search="ёжик 100%_")["chats"][0]["id"] == "0"
    assert not store.list_page(search="missing")["chats"]
    unnamed = store.create()
    assert store.list_page(search="new conversation")["chats"][0]["id"] == unnamed["id"]
    assert store.list_page(search="новый диалог")["chats"][0]["id"] == unnamed["id"]


def test_inserts_and_deletions_do_not_shift_remaining_pages(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    with store.connect() as db:
        db.executemany("INSERT INTO chats (id, updated, title, model, payload) VALUES (?, ?, '', '', '{}')",
                       [(str(i), i) for i in range(10)])
    first = store.list_page(3)
    store.delete("9")
    store.create()
    second = store.list_page(3, first["next_cursor"])
    assert [item["id"] for item in second["chats"]] == ["6", "5", "4"]


@pytest.mark.parametrize("cursor", ["invalid", "e30", "W10", "WzEsbnVsbF0", "W05hTiwiaWQiXQ"])
def test_invalid_cursors_are_rejected(tmp_path, cursor):
    store = ChatStore(tmp_path / "chats.sqlite3")
    with pytest.raises(ValueError, match="Invalid cursor"):
        store.list_page(cursor=cursor)
