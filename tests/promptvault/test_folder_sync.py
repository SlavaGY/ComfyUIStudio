"""Real workers: responsiveness, independent SQLite connections and stale results."""

import json
import threading

import pytest
from PySide6.QtCore import QTimer

from comfyui_studio.promptvault.core.folder_sync import FolderSync
from comfyui_studio.promptvault.core.repository import GenerationRepository


@pytest.fixture
def sync_environment(qtbot, tmp_path):
    repository = GenerationRepository(tmp_path / "library.sqlite3")
    sync = FolderSync(repository)
    try:
        yield sync, repository
    finally:
        sync.stop()
        qtbot.waitUntil(lambda: not sync._task.is_busy(), timeout=10000)
        repository.close()


def write_generation(folder, timestamp):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "generation.json").write_text(json.dumps({
        "timestamp": timestamp, "generation_time": 1.0, "model_name": "modelA",
        "images": [], "loras": [],
    }), encoding="utf-8")


def test_initial_scan_runs_off_gui_and_updates_shared_database(sync_environment, tmp_path, qtbot, monkeypatch):
    sync, repository = sync_environment
    folder = tmp_path / "generations"
    nested = folder / "nested"
    write_generation(nested, "first")
    entered, release = threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    original = GenerationRepository.sync_files
    workers = []

    def slow_scan(worker, path, files, **kwargs):
        workers.append((threading.get_ident(), worker._conn is repository._conn))
        entered.set()
        assert release.wait(5)
        return original(worker, path, files, **kwargs)

    monkeypatch.setattr(GenerationRepository, "sync_files", slow_scan)
    changed, heartbeat = [], []
    sync.changed.connect(lambda: changed.append(threading.get_ident()))
    try:
        sync.watch(folder)
        qtbot.waitUntil(entered.is_set)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qtbot.waitUntil(lambda: bool(heartbeat))
        assert not changed
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(changed))
    assert changed == [gui_thread]
    assert workers[0][0] != gui_thread and not workers[0][1]
    assert repository.count_generations(folder) == 1
    assert str(nested) in sync._watcher.directories()


def test_switch_coalesces_work_and_discards_old_result(sync_environment, tmp_path, qtbot, monkeypatch):
    sync, repository = sync_environment
    first, second = tmp_path / "first", tmp_path / "second"
    write_generation(first, "first")
    write_generation(second, "second")
    entered, release = threading.Event(), threading.Event()
    original = GenerationRepository.sync_files
    calls, notifications = [], []

    def slow_scan(worker, path, files, **kwargs):
        calls.append(path)
        if path == str(first):
            entered.set()
            assert release.wait(5)
        return original(worker, path, files, **kwargs)

    monkeypatch.setattr(GenerationRepository, "sync_files", slow_scan)
    sync.changed.connect(lambda: notifications.append(sync._folder))
    try:
        sync.watch(first)
        qtbot.waitUntil(entered.is_set)
        sync.watch(second)
        for _ in range(10):
            sync._sync()
        assert calls == [str(first)]
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(notifications))
    assert notifications == [str(second)]
    assert calls == [str(first), str(second)]
    assert repository.count_generations(second) == 1
    assert str(first) not in sync._watcher.directories()


def test_stop_ignores_inflight_result(sync_environment, tmp_path, qtbot, monkeypatch):
    sync, _ = sync_environment
    folder = tmp_path / "generations"
    write_generation(folder, "first")
    entered, release = threading.Event(), threading.Event()
    original = GenerationRepository.sync_files

    def slow_scan(worker, path, files, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(worker, path, files, **kwargs)

    monkeypatch.setattr(GenerationRepository, "sync_files", slow_scan)
    notifications = []
    sync.changed.connect(lambda: notifications.append(True))
    try:
        sync.watch(folder)
        qtbot.waitUntil(entered.is_set)
        sync.stop()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not sync._task.is_busy())
    assert not notifications
    assert not sync._watcher.directories()


def test_error_releases_worker_and_next_scan_recovers(sync_environment, tmp_path, qtbot, monkeypatch):
    sync, repository = sync_environment
    folder = tmp_path / "generations"
    write_generation(folder, "first")
    original = GenerationRepository.sync_files
    errors, notifications = [], []
    sync.failed.connect(errors.append)
    sync.changed.connect(lambda: notifications.append(True))

    def failing_scan(worker, path, files, **kwargs):
        raise OSError("unavailable disk")

    monkeypatch.setattr(GenerationRepository, "sync_files", failing_scan)
    sync.watch(folder)
    qtbot.waitUntil(lambda: bool(errors))
    assert errors == ["unavailable disk"] and not notifications
    monkeypatch.setattr(GenerationRepository, "sync_files", original)
    sync._sync()
    qtbot.waitUntil(lambda: bool(notifications))
    assert repository.count_generations(folder) == 1


def test_filesystem_event_burst_starts_one_scan(sync_environment, tmp_path, qtbot, monkeypatch):
    sync, _ = sync_environment
    folder = tmp_path / "generations"
    write_generation(folder, "first")
    calls = []
    original = GenerationRepository.sync_files

    def record_scan(worker, path, files, **kwargs):
        calls.append(path)
        return original(worker, path, files, **kwargs)

    monkeypatch.setattr(GenerationRepository, "sync_files", record_scan)
    sync.watch(folder)
    qtbot.waitUntil(lambda: not sync._task.is_busy())
    assert calls == [str(folder)]
    write_generation(folder, "second")
    for _ in range(20):
        sync._on_fs_event(str(folder))
    assert calls == [str(folder)]
    qtbot.waitUntil(lambda: len(calls) == 2)
    qtbot.waitUntil(lambda: not sync._task.is_busy())
    assert calls == [str(folder), str(folder)]
