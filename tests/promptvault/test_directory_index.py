"""Unchanged trees avoid enumeration; content edits and new subtrees stay visible."""

import json
import os
import sqlite3

import pytest

from comfyui_studio.promptvault.core import directory_index
from comfyui_studio.promptvault.core.directory_index import DirectoryIndex
from comfyui_studio.promptvault.core.repository import GenerationRepository


def test_unchanged_tree_needs_no_directory_enumeration(tmp_path, monkeypatch):
    tmp_path = tmp_path / "library"
    tmp_path.mkdir()
    for index in range(8):
        folder = tmp_path / str(index)
        folder.mkdir()
        (folder / "generation.json").write_text("{}")
        (folder / "image.png").write_bytes(b"image")
    calls = []
    original = os.scandir

    def record(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(directory_index.os, "scandir", record)
    inventory = DirectoryIndex(str(tmp_path))
    first = inventory.scan()
    assert first.reconcile and len(first.files) == 8 and len(calls) == 9
    calls.clear()
    second = inventory.scan()
    assert not second.files and not second.reconcile and calls == []
    assert set(first.directories) == set(second.directories)


def test_edit_to_known_file_is_detected_without_directory_event(tmp_path):
    tmp_path = tmp_path / "library"
    tmp_path.mkdir()
    path = tmp_path / "generation.json"
    path.write_text("{}")
    inventory = DirectoryIndex(str(tmp_path))
    inventory.scan()
    old_mtime = path.stat().st_mtime_ns
    path.write_text('{"positive_text":"changed"}')
    os.utime(path, ns=(old_mtime, old_mtime))
    result = inventory.scan()
    assert list(result.files) == [str(path)] and not result.reconcile


def test_new_renamed_removed_and_recreated_subtrees(tmp_path):
    tmp_path = tmp_path / "library"
    tmp_path.mkdir()
    inventory = DirectoryIndex(str(tmp_path))
    inventory.scan()
    nested = tmp_path / "new" / "deep"
    nested.mkdir(parents=True)
    path = nested / "generation.json"
    path.write_text("{}")
    result = inventory.scan({str(tmp_path)})
    assert str(path) in result.files and str(nested) in result.directories
    renamed = tmp_path / "renamed"
    (tmp_path / "new").rename(renamed)
    result = inventory.scan({str(tmp_path)})
    assert str(renamed / "deep" / path.name) in result.files
    assert str(nested) not in result.directories
    (renamed / "deep" / path.name).unlink()
    (renamed / "deep").rmdir()
    renamed.rmdir()
    assert inventory.scan({str(tmp_path)}).directories == [str(tmp_path)]
    nested.mkdir(parents=True)
    path.write_text("{}")
    assert str(path) in inventory.scan({str(tmp_path)}).files


def test_full_reconciliation_and_error_retry_can_revisit_unchanged_files(tmp_path, monkeypatch):
    tmp_path = tmp_path / "library"
    tmp_path.mkdir()
    path = tmp_path / "generation.json"
    path.write_text("{}")
    clock = [10.0]
    monkeypatch.setattr(directory_index.time, "monotonic", lambda: clock[0])
    inventory = DirectoryIndex(str(tmp_path), reconcile_seconds=60)
    inventory.scan()
    assert not inventory.scan().files
    clock[0] += 61
    assert inventory.scan().reconcile
    inventory.invalidate()
    assert str(path) in inventory.scan().files


def test_indexed_edits_and_moves_preserve_favorite_and_identity(tmp_path):
    folder = tmp_path / "library"
    subfolder = folder / "original"
    subfolder.mkdir(parents=True)
    path = subfolder / "generation.json"
    data = {"timestamp": "first", "generation_time": 1, "positive_text": "original", "images": [], "loras": []}
    path.write_text(json.dumps(data))
    inventory = DirectoryIndex(str(folder))
    repository = GenerationRepository(tmp_path / "library.sqlite3")
    try:
        changes = inventory.scan()
        repository.sync_files(folder, changes.files)
        generation = repository.load_generations(folder)[0]
        repository.set_favorite(generation.id, True)
        stamp = path.stat().st_mtime_ns
        data["positive_text"] = "updated and longer"
        path.write_text(json.dumps(data))
        os.utime(path, ns=(stamp, stamp))
        changes = inventory.scan()
        assert repository.sync_files(folder, changes.files, force=not changes.reconcile)
        updated = repository.load_generations(folder)[0]
        assert updated.positive == "updated and longer" and updated.favorite
        subfolder.rename(folder / "moved")
        changes = inventory.scan({str(folder)})
        repository.sync_files(folder, changes.files, force=not changes.reconcile)
        moved = repository.load_generations(folder)[0]
        assert moved.id == generation.id and moved.favorite
        assert moved.path.parent.name == "moved"
    finally:
        repository.close()


def test_reconciliation_batches_sql_variables(tmp_path):
    repository = GenerationRepository(tmp_path / "library.sqlite3")
    files = {str(tmp_path / f"{i}.json"): 100.0 for i in range(1200)}
    try:
        repository._conn.executemany("INSERT INTO generations(path, mtime) VALUES (?, ?)", files.items())
        repository._conn.commit()
        repository._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 600)
        assert not repository.sync_files(tmp_path, files)
    finally:
        repository.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows caches metadata in scandir entries")
def test_large_windows_directory_batches_metadata_and_detects_silent_edits(tmp_path, monkeypatch):
    tmp_path = tmp_path / "library"
    tmp_path.mkdir()
    for index in range(140):
        (tmp_path / f"{index}.json").write_text("{}")
    inventory = DirectoryIndex(str(tmp_path))
    inventory.scan()
    target = tmp_path / "0.json"
    stamp = target.stat().st_mtime_ns
    target.write_text('{"changed":true}')
    os.utime(target, ns=(stamp, stamp))
    original = directory_index.Path.stat
    stat_paths = []

    def record(path, *args, **kwargs):
        stat_paths.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(directory_index.Path, "stat", record)
    changes = inventory.scan()
    assert list(changes.files) == [str(target)]
    assert not any(path.suffix == ".json" for path in stat_paths)
    assert len(stat_paths) == 1  # directory itself, instead of 140 file stat calls
    assert not inventory.scan().files
