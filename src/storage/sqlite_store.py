from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.storage.backend import StorageBackend

SCHEMA = """
CREATE TABLE IF NOT EXISTS pr_task (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    branch TEXT NOT NULL,
    diff TEXT NOT NULL,
    status TEXT NOT NULL,
    max_steps INTEGER NOT NULL,
    max_seconds REAL NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    tenant TEXT NOT NULL DEFAULT 'default',
    start_time TEXT,
    end_time TEXT,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS checkpoint (
    task_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    agent TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL,
    handling_status TEXT NOT NULL DEFAULT 'open',
    handler TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    case_type TEXT NOT NULL,
    predicted TEXT NOT NULL,
    expected TEXT NOT NULL,
    confidence REAL,
    failure_label TEXT,
    trajectory TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    baseline TEXT,
    cluster TEXT,
    deduped INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evaluation_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    f1 REAL NOT NULL,
    high_risk_recall REAL NOT NULL,
    clean_pr_accuracy REAL NOT NULL,
    gate_passed INTEGER NOT NULL,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,
    tenant TEXT NOT NULL,
    repo TEXT NOT NULL,
    task_id TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);
"""


class SQLiteStore(StorageBackend):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if path != ":memory:" else Path(":memory:")
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(":memory:" if str(path) == ":memory:" else self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur
