"""Тесты для автоочистки логов/миниатюр (задача 3.5):
app.utils.enforce_dir_size_limit, app.core.logger.cleanup_old_logs,
app.core.thumbnails.cleanup_thumbnail_cache.
"""

import os
import time

from app.core.logger import cleanup_old_logs
from app.core.thumbnails import cleanup_thumbnail_cache
from app.utils import enforce_dir_size_limit


def _touch(path, size_bytes=10, age_seconds=0):

    path.write_bytes(b"x" * size_bytes)

    if age_seconds:
        old_time = time.time() - age_seconds
        os.utime(path, (old_time, old_time))

    return path


class TestEnforceDirSizeLimit:

    def test_noop_when_under_limit(self, tmp_path):

        _touch(tmp_path / "a.log", size_bytes=10)

        removed = enforce_dir_size_limit(tmp_path, "*.log", max_total_bytes=1000)

        assert removed == 0
        assert (tmp_path / "a.log").exists()

    def test_removes_oldest_first_until_under_limit(self, tmp_path):

        _touch(tmp_path / "old.log", size_bytes=100, age_seconds=1000)
        _touch(tmp_path / "mid.log", size_bytes=100, age_seconds=500)
        _touch(tmp_path / "new.log", size_bytes=100, age_seconds=0)

        # лимит пропускает только один файл из трёх (300 байт всего)
        removed = enforce_dir_size_limit(tmp_path, "*.log", max_total_bytes=150)

        assert removed == 2
        assert not (tmp_path / "old.log").exists()
        assert not (tmp_path / "mid.log").exists()
        assert (tmp_path / "new.log").exists()

    def test_missing_directory_returns_zero(self, tmp_path):

        assert enforce_dir_size_limit(tmp_path / "does_not_exist", "*.log", 1000) == 0


class TestCleanupOldLogs:

    def test_removes_logs_older_than_max_age(self, tmp_path, monkeypatch):

        import app.core.logger as logger_module

        monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)

        _touch(tmp_path / "old.log", age_seconds=100 * 86400)
        _touch(tmp_path / "recent.log", age_seconds=0)

        cleanup_old_logs(max_age_days=30, max_total_bytes=10_000_000)

        assert not (tmp_path / "old.log").exists()
        assert (tmp_path / "recent.log").exists()

    def test_missing_log_dir_does_not_raise(self, tmp_path, monkeypatch):

        import app.core.logger as logger_module

        monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path / "nope")

        cleanup_old_logs()  # не должно бросать исключение


class TestCleanupThumbnailCache:

    def test_removes_thumbnails_older_than_max_age(self, tmp_path, monkeypatch):

        import app.core.thumbnails as thumbnails_module

        monkeypatch.setattr(thumbnails_module, "THUMBNAIL_CACHE_DIR", tmp_path)

        _touch(tmp_path / "old.webp", age_seconds=100 * 86400)
        _touch(tmp_path / "recent.webp", age_seconds=0)

        cleanup_thumbnail_cache(max_age_days=30, max_total_bytes=10_000_000)

        assert not (tmp_path / "old.webp").exists()
        assert (tmp_path / "recent.webp").exists()

    def test_enforces_size_limit_after_age_pass(self, tmp_path, monkeypatch):

        import app.core.thumbnails as thumbnails_module

        monkeypatch.setattr(thumbnails_module, "THUMBNAIL_CACHE_DIR", tmp_path)

        _touch(tmp_path / "a.webp", size_bytes=100, age_seconds=10)
        _touch(tmp_path / "b.webp", size_bytes=100, age_seconds=5)

        cleanup_thumbnail_cache(max_age_days=30, max_total_bytes=150)

        remaining = list(tmp_path.glob("*.webp"))
        assert len(remaining) == 1
        assert remaining[0].name == "b.webp"
