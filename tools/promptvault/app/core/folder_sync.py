from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from app.config import POLL_INTERVAL_MS
from app.core.repository import GenerationRepository

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

    def __init__(
        self,
        repository: GenerationRepository,
        parent: QObject | None = None,
    ):
        super().__init__(parent)

        self._repository = repository
        self._folder: str | None = None

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_fs_event)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._sync)

    # --------------------------------------------------

    def watch(self, folder: str | Path) -> None:
        """Начинает следить за указанной папкой (и всеми текущими
        вложенными подпапками). Останавливает слежение за предыдущей
        папкой, если оно было активно."""

        self.stop()

        self._folder = str(folder)

        dirs = [self._folder] + [
            str(p) for p in Path(folder).rglob("*") if p.is_dir()
        ]

        if dirs:
            self._watcher.addPaths(dirs)

        self._timer.start()

        logger.info("Начато отслеживание папки: %s", self._folder)

    def stop(self) -> None:
        """Останавливает слежение за текущей папкой, если оно было
        активно. Безопасно вызывать повторно."""

        self._timer.stop()

        watched = self._watcher.directories()

        if watched:
            self._watcher.removePaths(watched)

        if self._folder is not None:
            logger.info("Остановлено отслеживание папки: %s", self._folder)

        self._folder = None

    # --------------------------------------------------

    def _on_fs_event(self, _changed_path: str) -> None:

        # новые подпапки, появившиеся после watch(), сами не отслеживаются —
        # добавляем их, раз уж событие всё равно пришло
        if self._folder is not None:

            watched = set(self._watcher.directories())

            for p in Path(self._folder).rglob("*"):
                if p.is_dir() and str(p) not in watched:
                    self._watcher.addPath(str(p))

        self._sync()

    def _sync(self) -> None:

        if self._folder is None:
            return

        if self._repository.sync_folder(self._folder):
            logger.info("Автосинхронизация обнаружила изменения в %s", self._folder)
            self.changed.emit()
