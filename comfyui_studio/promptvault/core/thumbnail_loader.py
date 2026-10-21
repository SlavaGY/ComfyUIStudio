"""Bounded, cancellable thumbnail requests; only QImage is used in workers."""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from comfyui_studio.launcher.core.background import BackgroundTask
from comfyui_studio.promptvault.core import thumbnails
from comfyui_studio.promptvault.core.thumbnails import make_thumb

logger = logging.getLogger(__name__)
ThumbnailKey = tuple[str, int]


class ThumbnailLoader(QObject):
    ready = Signal(str, int, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._tasks = [BackgroundTask(self), BackgroundTask(self)]
        self._pending: OrderedDict[ThumbnailKey, None] = OrderedDict()
        self._running: set[ThumbnailKey] = set()
        self._tokens: dict[int, ThumbnailKey] = {}
        self._waiting: dict[ThumbnailKey, set[int]] = {}
        self._next_token = 0
        self._cache_dir = thumbnails.THUMBNAIL_CACHE_DIR

    def request(self, path: str | Path, size: int = 256) -> int:
        key = (str(path), size)
        self._next_token += 1
        token = self._next_token
        self._tokens[token] = key
        self._waiting.setdefault(key, set()).add(token)
        if key not in self._running:
            self._pending[key] = None
        self._pump()
        return token

    def cancel(self, token: int) -> None:
        key = self._tokens.pop(token, None)
        if key is None:
            return
        waiting = self._waiting.get(key)
        if waiting is not None:
            waiting.discard(token)
            if not waiting:
                self._waiting.pop(key, None)
                self._pending.pop(key, None)

    def _pump(self) -> None:
        for task in self._tasks:
            if task.is_busy() or not self._pending:
                continue
            key, _ = self._pending.popitem(last=False)
            self._running.add(key)
            task.run(lambda key=key: make_thumb(*key, cache_dir=self._cache_dir),
                     lambda result, error, key=key: self._finished(key, result, error))

    def _finished(self, key: ThumbnailKey, result: Path | None, error: Exception | None) -> None:
        self._running.discard(key)
        tokens = self._waiting.pop(key, set())
        for token in tokens:
            self._tokens.pop(token, None)
        if error is not None:
            logger.warning("Не удалось создать миниатюру %s: %s", key[0], error)
        if tokens:
            self.ready.emit(key[0], key[1], result)
        self._pump()
