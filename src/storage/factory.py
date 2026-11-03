from __future__ import annotations

from src.storage.backend import StorageBackend
from src.storage.pg_store import PostgresStore
from src.storage.sqlite_store import SQLiteStore

_PG_PREFIXES = ("postgresql://", "postgres://")


def open_store(target: str) -> StorageBackend:
    """Open a store from a path (SQLite) or a ``postgresql://`` URL."""
    if target.startswith(_PG_PREFIXES):
        return PostgresStore(target)
    return SQLiteStore(target)
