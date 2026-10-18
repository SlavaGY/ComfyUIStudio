"""Общее соединение с SQLite для миксинов GenerationRepository.

Соединение создаётся в GenerationRepository.__init__; все группы операций
используют один self._conn."""

from __future__ import annotations

import sqlite3


class RepositoryBase:
    """Соединение с SQLite, которым пользуются все части репозитория."""

    _conn: sqlite3.Connection
