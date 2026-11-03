from __future__ import annotations

from typing import Any

from src.storage.backend import StorageBackend

PG_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS pr_task (
        id TEXT PRIMARY KEY,
        repo TEXT NOT NULL,
        pr_number INTEGER NOT NULL,
        branch TEXT NOT NULL,
        diff TEXT NOT NULL,
        status TEXT NOT NULL,
        max_steps INTEGER NOT NULL,
        max_seconds DOUBLE PRECISION NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        tenant TEXT NOT NULL DEFAULT 'default',
        start_time TEXT,
        end_time TEXT,
        end_reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint (
        task_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trace (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        task_id TEXT NOT NULL,
        step INTEGER NOT NULL,
        agent TEXT NOT NULL,
        event TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dead_letter (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        task_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT NOT NULL,
        handling_status TEXT NOT NULL DEFAULT 'open',
        handler TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        task_id TEXT NOT NULL,
        case_type TEXT NOT NULL,
        predicted TEXT NOT NULL,
        expected TEXT NOT NULL,
        confidence DOUBLE PRECISION,
        failure_label TEXT,
        trajectory TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_version (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        version TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        baseline TEXT,
        cluster TEXT,
        deduped INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluation_result (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        dataset TEXT NOT NULL,
        f1 DOUBLE PRECISION NOT NULL,
        high_risk_recall DOUBLE PRECISION NOT NULL,
        clean_pr_accuracy DOUBLE PRECISION NOT NULL,
        gate_passed INTEGER NOT NULL,
        report TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_record (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_type TEXT NOT NULL,
        tenant TEXT NOT NULL,
        repo TEXT NOT NULL,
        task_id TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        archived INTEGER NOT NULL DEFAULT 0
    )
    """,
]


class PostgresStore(StorageBackend):
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "psycopg is required for the PostgreSQL backend. "
                "Install it with: pip install 'pr-rsi-harness[postgres]'"
            ) from exc
        self.dsn = dsn
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        for statement in PG_SCHEMA_STATEMENTS:
            self._conn.execute(statement)
        self._conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        cur = self._conn.execute(sql.replace("?", "%s"), tuple(params))
        self._conn.commit()
        return cur
