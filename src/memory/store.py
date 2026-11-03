from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.storage.backend import StorageBackend
from src.storage.sqlite_store import SQLiteStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MemoryRecord:
    id: int
    memory_type: str
    tenant: str
    repo: str
    task_id: str | None
    content: str
    created_at: str
    expires_at: str | None
    archived: int


class MemoryManager:
    def __init__(self, store: StorageBackend) -> None:
        self.store = store

    @classmethod
    def from_sqlite(cls, path: str | Path) -> MemoryManager:
        return cls(SQLiteStore(path))

    def put(
        self,
        memory_type: str,
        tenant: str,
        repo: str,
        content: str,
        task_id: str | None = None,
        expires_in_seconds: int | None = None,
    ) -> None:
        expires_at = None
        if expires_in_seconds is not None:
            expires_at = (_now() + timedelta(seconds=expires_in_seconds)).isoformat()
        self.store.execute(
            """INSERT INTO memory_record(memory_type, tenant, repo, task_id, content, created_at, expires_at, archived)
               VALUES(?,?,?,?,?,?,?,0)""",
            (memory_type, tenant, repo, task_id, content, _now().isoformat(), expires_at),
        )

    def search(self, tenant: str, repo: str, query: str) -> list[MemoryRecord]:
        now = _now().isoformat()
        rows = self.store.execute(
            """SELECT * FROM memory_record
               WHERE tenant=? AND repo=? AND archived=0
                 AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY id""",
            (tenant, repo, now),
        ).fetchall()
        q = query.lower()
        records = [self._row(r) for r in rows]
        return [r for r in records if q in r.content.lower() or q in r.memory_type]

    def archive_task(self, task_id: str) -> int:
        cur = self.store.execute(
            "UPDATE memory_record SET archived=1 WHERE task_id=?", (task_id,)
        )
        return cur.rowcount

    def cleanup_expired(self) -> int:
        now = _now().isoformat()
        cur = self.store.execute(
            "DELETE FROM memory_record WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        return cur.rowcount

    @staticmethod
    def _row(r) -> MemoryRecord:
        return MemoryRecord(
            id=r["id"],
            memory_type=r["memory_type"],
            tenant=r["tenant"],
            repo=r["repo"],
            task_id=r["task_id"],
            content=r["content"],
            created_at=r["created_at"],
            expires_at=r["expires_at"],
            archived=r["archived"],
        )
