"""Real Qt workers, bounded queues, cache invalidation and stale preview requests."""

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage

from comfyui_studio.promptvault.core import thumbnail_loader
from comfyui_studio.promptvault.core.generation import Generation, ImageData
from comfyui_studio.promptvault.core.thumbnail_loader import ThumbnailLoader
from comfyui_studio.promptvault.core.thumbnails import make_thumb
from comfyui_studio.promptvault.ui.generation_list import GenerationCard


def write_image(path, width=1024, height=512, color=Qt.GlobalColor.red):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))


def generation(path):
    return Generation(path=path / "generation.json", timestamp="now", generation_time=1,
                      model="model", sampler="sampler", cfg=7, steps=20, positive="", negative="",
                      images=[ImageData(file="image.png")])


def test_thumbnail_dimensions_reuse_and_invalidate_on_source_or_size(qapp, tmp_path):
    source = tmp_path / "image.png"
    write_image(source)
    cache = tmp_path / "cache"
    first = make_thumb(source, 90, cache_dir=cache)
    assert first is not None
    assert QImage(str(first)).size().width() == 90
    assert QImage(str(first)).size().height() == 45
    stamp = first.stat().st_mtime_ns
    assert make_thumb(source, 90, cache_dir=cache) == first
    assert first.stat().st_mtime_ns == stamp
    assert make_thumb(source, 40, cache_dir=cache) != first
    write_image(source, 800, 400, Qt.GlobalColor.blue)
    changed = make_thumb(source, 90, cache_dir=cache)
    assert changed != first and changed is not None
    assert not list(cache.glob("*.part*"))


def test_loader_deduplicates_bounds_workers_and_cancels_queued_jobs(qapp, qtbot, tmp_path, monkeypatch):
    loader = ThumbnailLoader()
    release = threading.Event()
    lock = threading.Lock()
    calls, results = [], []
    active, peak = [0], [0]
    main_thread = threading.get_ident()

    def slow_thumb(path, size, **kwargs):
        with lock:
            calls.append((path, threading.get_ident()))
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        assert release.wait(5)
        with lock:
            active[0] -= 1
        return Path(path + ".webp")

    monkeypatch.setattr(thumbnail_loader, "make_thumb", slow_thumb)
    loader.ready.connect(lambda path, size, result: results.append((path, threading.get_ident())))
    try:
        first = loader.request("first")
        loader.request("first")
        loader.request("second")
        cancelled = loader.request("cancelled")
        loader.request("last")
        loader.cancel(cancelled)
        loader.cancel(first)
        qtbot.waitUntil(lambda: len(calls) == 2)
        assert not results
    finally:
        release.set()
    qtbot.waitUntil(lambda: len(results) == 3)
    qtbot.waitUntil(lambda: all(not task.is_busy() for task in loader._tasks))
    assert sorted(path for path, _ in calls) == ["first", "last", "second"]
    assert peak[0] <= 2 and all(thread != main_thread for _, thread in calls)
    assert all(thread == main_thread for _, thread in results)


def test_cancelled_card_stays_blank_and_gui_remains_responsive(qapp, qtbot, tmp_path, monkeypatch):
    ready_image = tmp_path / "ready.png"
    write_image(ready_image, 90, 45)
    entered, release = threading.Event(), threading.Event()

    def slow_thumb(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return ready_image

    monkeypatch.setattr(thumbnail_loader, "make_thumb", slow_thumb)
    loader = ThumbnailLoader()
    card = GenerationCard(generation(tmp_path), loader)
    qtbot.addWidget(card)
    heartbeat = []
    try:
        card.load_preview()
        qtbot.waitUntil(entered.is_set)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qtbot.waitUntil(lambda: bool(heartbeat))
        card.cancel_preview()
    finally:
        release.set()
    qtbot.waitUntil(lambda: all(not task.is_busy() for task in loader._tasks))
    assert card.preview.pixmap() is None or card.preview.pixmap().isNull()


def test_card_displays_completed_thumbnail(qapp, qtbot, tmp_path):
    write_image(tmp_path / "image.png")
    loader = ThumbnailLoader()
    card = GenerationCard(generation(tmp_path), loader)
    qtbot.addWidget(card)
    card.load_preview()
    qtbot.waitUntil(lambda: card.preview.pixmap() is not None and not card.preview.pixmap().isNull())
    assert card.preview.pixmap().width() == 90
    assert card.preview.pixmap().height() == 45
