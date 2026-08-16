"""Мелкие утилиты общего назначения, не привязанные к конкретному модулю."""

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

logger = logging.getLogger(__name__)


def open_file_externally(path: Path | str) -> bool:
    """Открывает файл в ассоциированном приложении ОС.

    Заменяет os.startfile, который есть только на Windows — QDesktopServices
    работает одинаково на Windows/macOS/Linux. Возвращает True, если ОС
    приняла запрос на открытие (не гарантирует, что приложение реально
    успешно открыло файл — это уже вне контроля Qt).
    """

    path = Path(path)

    if not path.exists():
        logger.warning("Попытка открыть несуществующий файл: %s", path)
        return False

    opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    if not opened:
        logger.warning("Не удалось открыть файл во внешнем приложении: %s", path)

    return opened


def group_paths_by_folder(paths: list[Path]) -> dict[Path, list[Path]]:
    """Группирует пути по родительской папке, сохраняя порядок
    появления папок и порядок путей внутри каждой группы.

    Используется reveal_in_file_manager, чтобы решить, сколько окон
    файлового менеджера открывать (по одному на уникальную папку)."""

    groups: dict[Path, list[Path]] = {}

    for path in paths:
        groups.setdefault(path.parent, []).append(path)

    return groups


def reveal_in_file_manager(paths: list[Path | str]) -> None:
    """Открывает файловый менеджер ОС с выделенными файлами.

    paths может ссылаться на файлы в РАЗНЫХ папках — для каждой
    отдельной папки открывается своё окно файлового менеджера со всеми
    принадлежащими ей файлами, выделенными одновременно (см.
    group_paths_by_folder), а не одно окно на все файлы сразу.
    """

    existing_paths = [p for p in (Path(p) for p in paths) if p.exists()]

    for folder, files_in_folder in group_paths_by_folder(existing_paths).items():
        _reveal_single_folder(folder, files_in_folder)


def _reveal_single_folder(folder: Path, files: list[Path]) -> None:
    """Открывает ОДНО окно файлового менеджера на folder, выделяя в
    нём все files (все обязаны лежать именно в этой папке)."""

    revealed = False

    if sys.platform == "win32":
        revealed = _reveal_windows(files)
    elif sys.platform == "darwin":
        revealed = _reveal_macos(files)

    if revealed:
        return

    # Linux (нет универсального кроссплатформенного способа выделить
    # конкретный файл — конкретная реализация зависит от файлового
    # менеджера DE) и запасной вариант на случай сбоя shell-вызова на
    # Windows/macOS: просто открыть саму папку целиком, без выделения.
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _reveal_windows(files: list[Path]) -> bool:
    """Открывает Проводник с одновременным выделением ВСЕХ files (все
    должны лежать в одной папке) через shell32.SHOpenFolderAndSelectItems.

    Простой `explorer.exe /select,"path"` из subprocess умеет выделить
    только ОДИН файл за раз — для настоящего множественного выделения
    нужен именно этот более низкоуровневый Shell API (стандартный
    ctypes-приём, без зависимости от pywin32).
    """

    import ctypes

    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]

        ole32.CoInitialize(None)

        folder = files[0].parent

        pidl_folder = ctypes.c_void_p()

        if shell32.SHParseDisplayName(str(folder), None, ctypes.byref(pidl_folder), 0, None) != 0:
            return False

        item_pidls = []

        for file_path in files:

            pidl_item = ctypes.c_void_p()

            if shell32.SHParseDisplayName(
                str(file_path), None, ctypes.byref(pidl_item), 0, None
            ) == 0:
                item_pidls.append(pidl_item)

        if not item_pidls:
            ole32.CoTaskMemFree(pidl_folder)
            return False

        pidl_array = (ctypes.c_void_p * len(item_pidls))(*item_pidls)

        shell32.SHOpenFolderAndSelectItems(pidl_folder, len(item_pidls), pidl_array, 0)

        ole32.CoTaskMemFree(pidl_folder)

        for pidl_item in item_pidls:
            ole32.CoTaskMemFree(pidl_item)

        return True

    except Exception as e:
        logger.warning("Не удалось открыть Проводник с выделением файлов: %s", e)
        return False


def _reveal_macos(files: list[Path]) -> bool:
    """`open -R` выделяет файл(ы) в Finder. Принимает несколько путей
    за один вызов — при условии, что все они лежат в одной папке
    (гарантируется вызывающей стороной, см. _reveal_single_folder)."""

    try:
        subprocess.run(["open", "-R", *(str(f) for f in files)], check=False)
        return True
    except OSError as e:
        logger.warning("Не удалось открыть Finder с выделением файлов: %s", e)
        return False


def enforce_dir_size_limit(directory: Path, glob_pattern: str, max_total_bytes: int) -> int:
    """Удаляет самые старые (по mtime) файлы, соответствующие
    glob_pattern в directory, пока суммарный размер оставшихся файлов
    не станет <= max_total_bytes.

    Общий помощник для автоочистки (задача 3.5) — используется и
    app.core.logger.cleanup_old_logs, и
    app.core.thumbnails.cleanup_thumbnail_cache. Возвращает количество
    удалённых файлов.
    """

    if not directory.exists():
        return 0

    entries = []

    for path in directory.glob(glob_pattern):
        try:
            entries.append((path.stat().st_mtime, path.stat().st_size, path))
        except OSError:
            continue

    total_size = sum(size for _mtime, size, _path in entries)

    if total_size <= max_total_bytes:
        return 0

    entries.sort(key=lambda e: e[0])  # старые первыми

    removed = 0

    for _mtime, size, path in entries:

        if total_size <= max_total_bytes:
            break

        try:
            path.unlink()
            total_size -= size
            removed += 1
        except OSError:
            continue

    return removed
