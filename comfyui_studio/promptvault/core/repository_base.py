"""Общая основа миксинов GenerationRepository (этап 6.1 -- разрез repository.py).

Репозиторий разрезан на модули по смыслу (запросы, редактирование,
экспорт/импорт, статистика, синхронизация с диском), но остаётся ОДНИМ
классом `repository.GenerationRepository` -- именно его импортируют
GalleryManager, folder_sync и главное окно. Миксины делят одно
соединение с БД; объявлено оно здесь, чтобы mypy видел `self._conn` во
всех частях.
"""

from __future__ import annotations

import sqlite3


class RepositoryBase:
    """Соединение с SQLite, которым пользуются все части репозитория."""

    _conn: sqlite3.Connection
