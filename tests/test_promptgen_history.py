"""Тесты базы истории Генератора промптов (comfyui_studio/promptgen_history.py)."""

import sqlite3
import threading

import pytest

from comfyui_studio import promptgen_history as ph


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "history.db")


def rec(**kw):
    base = {
        "job_id": "j", "started_at": 1000.0, "finished_at": 1010.0, "state": "done",
        "user_text": "cat", "full_prompt": "Describe: cat", "output_text": "A cat",
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "total_s": 10.0, "tok_per_s": 20.0,
    }
    base.update(kw)
    return base


def test_add_and_get_roundtrip(db):
    rid = ph.add_record(rec(has_image=True, image_name="a.png", truncated=False), db)
    got = ph.get(rid, db)
    assert got["user_text"] == "cat" and got["image_name"] == "a.png"
    assert got["has_image"] == 1 and got["truncated"] == 0
    assert got["total_tokens"] == 15
    assert ph.get(999, db) is None


def test_missing_fields_get_defaults_and_unknown_keys_are_ignored(db):
    rid = ph.add_record({"state": "error", "bogus": 1}, db)
    got = ph.get(rid, db)
    assert got["state"] == "error" and got["user_text"] == "" and got["total_tokens"] is None
    assert got["started_at"] > 0


def test_wal_mode_and_schema_version(db):
    ph.add_record(rec(), db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == ph.SCHEMA_VERSION
    finally:
        conn.close()


def test_search_is_case_insensitive_for_cyrillic_and_all_terms_must_match(db):
    ph.add_record(rec(user_text="Кот на крыше", output_text="Рыжий кот сидит"), db)
    ph.add_record(rec(user_text="собака", output_text="Большая собака"), db)
    ph.add_record(rec(user_text="x", image_name="Photo_01.JPG"), db)
    assert ph.count("КОТ", db) == 1
    assert ph.count("кот рыжий", db) == 1
    assert ph.count("кот собака", db) == 0
    assert ph.count("photo_01", db) == 1          # по имени изображения
    assert ph.count("", db) == 3
    # служебные символы LIKE не работают как подстановки
    assert ph.count("%", db) == 0 and ph.count("_", db) == 1  # '_' есть только в Photo_01


def test_sorting_both_directions_and_tiebreak(db):
    ph.add_record(rec(started_at=1, total_tokens=50, user_text="b"), db)
    ph.add_record(rec(started_at=2, total_tokens=10, user_text="a"), db)
    ph.add_record(rec(started_at=3, total_tokens=30, user_text="c"), db)
    def by(key, desc):
        # было `by = lambda ...` -- ruff E731
        return [r["user_text"] for r in ph.query(sort_by=key, descending=desc, path=db)]

    assert by("started_at", True) == ["c", "a", "b"]
    assert by("started_at", False) == ["b", "a", "c"]
    assert by("total_tokens", True) == ["b", "c", "a"]
    assert by("user_text", False) == ["a", "b", "c"]


def test_unknown_sort_key_never_reaches_sql(db):
    ph.add_record(rec(), db)
    rows = ph.query(sort_by="id; DROP TABLE generations; --", path=db)
    assert len(rows) == 1 and ph.count(path=db) == 1


def test_paging(db):
    for i in range(25):
        ph.add_record(rec(started_at=i, user_text=str(i)), db)
    first = ph.query(limit=10, offset=0, path=db)
    last = ph.query(limit=10, offset=20, path=db)
    assert [r["user_text"] for r in first][0] == "24" and len(first) == 10
    assert [r["user_text"] for r in last] == ["4", "3", "2", "1", "0"]


def test_list_rows_omit_heavy_fields_but_get_has_them(db):
    rid = ph.add_record(rec(prefix="P" * 100, raw_output="R"), db)
    row = ph.query(path=db)[0]
    assert "full_prompt" not in row and "prefix" not in row and "raw_output" not in row
    assert ph.get(rid, db)["prefix"] == "P" * 100


def test_stats_average_only_successful_jobs(db):
    ph.add_record(rec(state="done", total_s=10, tok_per_s=20, total_tokens=15), db)
    ph.add_record(rec(state="done", total_s=20, tok_per_s=40, total_tokens=5), db)
    ph.add_record(rec(state="error", total_s=999, tok_per_s=None, total_tokens=None), db)
    s = ph.stats(path=db)
    assert s["n"] == 3 and s["tokens"] == 20
    assert s["avg_total_s"] == 15 and s["avg_tok_per_s"] == 30
    assert ph.stats("нет такого", db)["n"] == 0


def test_delete_and_clear_and_fingerprint(db):
    ids = [ph.add_record(rec(), db) for _ in range(5)]
    assert ph.fingerprint(db) == (5, ids[-1])
    assert ph.delete(ids[:2], db) == 2
    assert ph.delete([], db) == 0
    assert ph.fingerprint(db) == (3, ids[-1])
    assert ph.clear(db) == 3
    assert ph.count(path=db) == 0


def test_concurrent_writers_do_not_lose_records(db):
    ph.add_record(rec(), db)  # схема создаётся до гонки
    errors = []

    def work():
        try:
            for _ in range(20):
                ph.add_record(rec(), db)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert ph.count(path=db) == 81
