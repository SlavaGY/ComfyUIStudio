"""In-memory directory inventory; only changed directories are enumerated again."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DirectoryChanges:
    files: dict[str, float]
    directories: list[str]
    reconcile: bool


class DirectoryIndex:
    """Worker-owned snapshot with a periodic full reconciliation as a fallback.

    Directory mtimes detect additions/removals, not writes to existing files.
    Known JSON files have their metadata checked on every poll. On Windows,
    large directories use scandir's cached FindFirstFile metadata in batches.
    Contents/database records are touched only when their signature changes.
    """

    def __init__(self, folder: str, reconcile_seconds: float = 60.0):
        self.root = Path(folder).resolve()
        self._directories: dict[Path, tuple[int, int]] = {}
        self._children: dict[Path, tuple[set[Path], set[Path]]] = {}
        self._files: dict[str, tuple[int, int]] = {}
        self._last_reconcile = float("-inf")
        self._reconcile_seconds = reconcile_seconds

    def invalidate(self) -> None:
        self._last_reconcile = float("-inf")

    def scan(self, dirty: set[str] | None = None) -> DirectoryChanges:
        now = time.monotonic()
        reconcile = now - self._last_reconcile >= self._reconcile_seconds
        forced = {Path(path).resolve() for path in (dirty or set())}
        pending = [self.root]
        visited: set[Path] = set()
        changed: dict[str, float] = {}
        current_files: dict[str, tuple[int, int]] = {}

        while pending:
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            try:
                metadata = directory.stat()
            except FileNotFoundError:
                self._directories.pop(directory, None)
                self._children.pop(directory, None)
                continue
            signature = (metadata.st_mtime_ns, metadata.st_ctime_ns)
            cached_files = self._children.get(directory, (set(), set()))[0]
            batch_metadata = os.name == "nt" and len(cached_files) >= 128
            file_metadata: dict[Path, os.stat_result] = {}
            if reconcile or batch_metadata or directory in forced or self._directories.get(directory) != signature:
                files: set[Path] = set()
                directories: set[Path] = set()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            # Resolve directories once, including Windows junctions;
                            # ordinary files below them already have canonical paths.
                            directories.add(Path(entry.path).resolve())
                        elif entry.name.lower().endswith(".json") and entry.is_file():
                            path = Path(entry.path)
                            if entry.is_symlink():
                                path = path.resolve()
                            try:
                                file_metadata[path] = entry.stat()
                            except FileNotFoundError:
                                continue
                            files.add(path)
                # Files and subdirectories share one inventory, avoiding a
                # second recursive walk just to update QFileSystemWatcher.
                self._children[directory] = (files, directories)
                self._directories[directory] = signature
                pending.extend(directories)
            else:
                files, directories = self._children[directory]
                pending.extend(directories)

            for path in files:
                try:
                    metadata = file_metadata[path] if path in file_metadata else path.stat()
                except FileNotFoundError:
                    continue
                key = str(path)
                file_signature = (metadata.st_mtime_ns, metadata.st_size)
                current_files[key] = file_signature
                if reconcile or self._files.get(key) != file_signature:
                    changed[key] = metadata.st_mtime

        self._directories = {path: stamp for path, stamp in self._directories.items() if path in visited}
        self._children = {path: children for path, children in self._children.items() if path in self._directories}
        self._files = current_files
        if reconcile:
            self._last_reconcile = now
        return DirectoryChanges(changed, [str(path) for path in self._directories], reconcile)
