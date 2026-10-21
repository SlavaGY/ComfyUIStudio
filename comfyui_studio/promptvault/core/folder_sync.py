from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from comfyui_studio.launcher.core.background import BackgroundTask
from comfyui_studio.promptvault.config import POLL_INTERVAL_MS
from comfyui_studio.promptvault.core.directory_index import DirectoryIndex
from comfyui_studio.promptvault.core.repository import GenerationRepository

logger = logging.getLogger(__name__)


class FolderSync(QObject):
    """Следит за открытой папкой и синхронизирует БД с содержимым диска.

    QFileSystemWatcher реагирует почти мгновенно на изменения внутри
    уже известных папок, но не отслеживает автоматически новые
    вложенные подпапки и по-разному ведёт себя в разных ОС — поэтому
    как надёжный резерв используется периодический QTimer, который
    гарантированно подхватит любые изменения (в т.ч. новые подпапки)
    в течение нескольких секунд.
    """

    changed = Signal()
    failed = Signal(str)

    def __init__(
        self,
        repository: GenerationRepository,
        parent: QObject | None = None,
    ):
        super().__init__(parent)

        self._db_path = repository.db_path
        self._folder: str | None = None
        self._generation = 0
        self._pending = False
        self._initial = False
        self._initial_error_reported = False
        self._task = BackgroundTask(self)
        self._index: DirectoryIndex | None = None
        self._dirty: set[str] = set()

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_event)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._sync)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._sync)

    # --------------------------------------------------

    def watch(self, folder: str | Path) -> None:
        """Начинает следить за указанной папкой (и всеми текущими
        вложенными подпапками). Останавливает слежение за предыдущей
        папкой, если оно было активно."""

        self.stop()

        self._folder = str(folder)
        self._index = DirectoryIndex(self._folder)

        # Рекурсивное обнаружение подпапок выполняет тот же рабочий поток,
        # что и индексацию. В GUI остаётся только регистрация готовых путей.
        self._watcher.addPath(self._folder)
        self._initial = True
        self._initial_error_reported = False

        self._timer.start()
        self._sync()

        logger.info("Начато отслеживание папки: %s", self._folder)

    def stop(self) -> None:
        """Останавливает слежение за текущей папкой, если оно было
        активно. Безопасно вызывать повторно."""

        self._timer.stop()
        self._debounce.stop()
        self._generation += 1
        self._pending = False
        self._initial = False
        # Не cancel(): предыдущая операция должна освободить своё соединение
        # до запуска следующей. Результат отбрасывается по _generation.

        watched = self._watcher.directories()

        if watched:
            self._watcher.removePaths(watched)

        if self._folder is not None:
            logger.info("Остановлено отслеживание папки: %s", self._folder)

        self._folder = None
        self._index = None
        self._dirty.clear()

    # --------------------------------------------------

    def _on_fs_event(self, _changed_path: str) -> None:

        if self._folder is not None:
            self._dirty.add(_changed_path)
            self._debounce.start()

    def _sync(self) -> None:

        if self._folder is None or self._index is None:
            return

        if self._task.is_busy():
            self._pending = True
            return

        self._pending = False
        folder, generation, db_path = self._folder, self._generation, self._db_path
        index, dirty = self._index, self._dirty
        self._dirty = set()

        def scan() -> tuple[bool, list[str]]:
            changes = index.scan(dirty)
            changed = False
            if changes.files:
                repository = GenerationRepository(db_path)
                try:
                    changed = repository.sync_files(folder, changes.files, force=not changes.reconcile)
                finally:
                    repository.close()
            return changed, changes.directories

        self._task.run(scan, lambda result, error: self._on_scanned(generation, result, error))

    def _on_scanned(
        self,
        generation: int,
        result: tuple[bool, list[str]] | None,
        error: Exception | None,
    ) -> None:
        if generation == self._generation and self._folder is not None:
            if error is not None:
                if self._index is not None:
                    self._index.invalidate()
                logger.error("Ошибка синхронизации %s: %s", self._folder, error)
                # Первичную ошибку показываем один раз; повторные фоновые
                # ошибки остаются в журнале и не мешают работе с галереей.
                if self._initial and not self._initial_error_reported:
                    self._initial_error_reported = True
                    self.failed.emit(str(error))
            elif result is not None:
                changed, directories = result
                watched = set(self._watcher.directories())
                obsolete = list(watched - set(directories))
                if obsolete:
                    self._watcher.removePaths(obsolete)
                additions = [path for path in directories if path not in watched]
                if additions:
                    self._watcher.addPaths(additions)
                initial, self._initial = self._initial, False
                if changed or initial:
                    self.changed.emit()

        if self._pending and self._folder is not None:
            self._sync()
