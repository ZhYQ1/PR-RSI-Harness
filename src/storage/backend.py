from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from src.harness.state import TaskStatus, transition
from src.storage.models import DeadLetter, PRTask, TraceEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageBackend(ABC):
    """Persistence logic shared by every backend.

    High-level methods use SQLite-style ``?`` placeholders; concrete backends
    translate them inside :meth:`execute` when needed (PostgreSQL ``%s``).
    """

    _bus: Any = None

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a statement, commit, and return a cursor-like object."""

    def attach_bus(self, bus: Any) -> None:
        """Optionally mirror writes onto an event bus (Redis Streams)."""
        self._bus = bus

    def _publish(self, event: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(event)
        except Exception:  # noqa: BLE001 — event delivery must not break writes
            pass

    # ------------------------------------------------------------------ tasks

    def create_task(
        self,
        repo: str,
        pr_number: int,
        branch: str,
        diff: str,
        max_steps: int = 20,
        max_seconds: float = 120,
        tenant: str = "default",
        task_id: str | None = None,
    ) -> PRTask:
        task = PRTask(
            id=task_id or str(uuid.uuid4()),
            repo=repo,
            pr_number=pr_number,
            branch=branch,
            diff=diff,
            status=TaskStatus.PENDING,
            max_steps=max_steps,
            max_seconds=max_seconds,
            tenant=tenant,
        )
        self.execute(
            """INSERT INTO pr_task
               (id, repo, pr_number, branch, diff, status, max_steps, max_seconds, retry_count, tenant)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                task.id,
                task.repo,
                task.pr_number,
                task.branch,
                task.diff,
                task.status.value,
                task.max_steps,
                task.max_seconds,
                task.tenant,
            ),
        )
        return task

    def get_task(self, task_id: str) -> PRTask:
        row = self.execute("SELECT * FROM pr_task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._row_to_task(row)

    def set_status(self, task_id: str, nxt: TaskStatus, *, reason: str | None = None) -> PRTask:
        task = self.get_task(task_id)
        new_status = transition(task.status, nxt)
        end_time = (
            _now()
            if new_status in {TaskStatus.DONE, TaskStatus.DEAD_LETTER, TaskStatus.BUDGET_EXCEEDED}
            else None
        )
        start_time = task.start_time or (_now() if new_status is TaskStatus.RUNNING else None)
        self.execute(
            """UPDATE pr_task SET status=?, start_time=COALESCE(start_time, ?),
               end_time=?, end_reason=? WHERE id=?""",
            (new_status.value, start_time, end_time, reason, task_id),
        )
        self._publish(
            {"type": "status", "task_id": task_id, "status": new_status.value, "reason": reason}
        )
        return self.get_task(task_id)

    def bump_retry(self, task_id: str) -> int:
        self.execute("UPDATE pr_task SET retry_count = retry_count + 1 WHERE id=?", (task_id,))
        return self.get_task(task_id).retry_count

    @staticmethod
    def _row_to_task(row: Any) -> PRTask:
        return PRTask(
            id=row["id"],
            repo=row["repo"],
            pr_number=row["pr_number"],
            branch=row["branch"],
            diff=row["diff"],
            status=TaskStatus(row["status"]),
            max_steps=row["max_steps"],
            max_seconds=row["max_seconds"],
            retry_count=row["retry_count"],
            tenant=row["tenant"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            end_reason=row["end_reason"],
        )

    # ------------------------------------------------------------- checkpoint

    def save_checkpoint(self, task_id: str, payload: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO checkpoint(task_id, payload, updated_at) VALUES(?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (task_id, json.dumps(payload, ensure_ascii=False), _now()),
        )

    def load_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        row = self.execute("SELECT payload FROM checkpoint WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])

    # ------------------------------------------------------------ dead letter

    def enqueue_dead_letter(self, task_id: str, reason: str, detail: str) -> DeadLetter:
        task = self.get_task(task_id)
        if task.status is not TaskStatus.DEAD_LETTER:
            self.set_status(task_id, TaskStatus.DEAD_LETTER, reason=reason)
        self.execute(
            "INSERT INTO dead_letter(task_id, reason, detail) VALUES(?,?,?)",
            (task_id, reason, detail),
        )
        return self.list_dead_letters()[-1]

    def list_dead_letters(self) -> list[DeadLetter]:
        rows = self.execute("SELECT * FROM dead_letter ORDER BY id").fetchall()
        return [
            DeadLetter(
                id=r["id"],
                task_id=r["task_id"],
                reason=r["reason"],
                detail=r["detail"],
                handling_status=r["handling_status"],
                handler=r["handler"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ trace

    def append_trace(
        self,
        task_id: str,
        step: int,
        agent: str,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.execute(
            "INSERT INTO trace(task_id, step, agent, event, payload, created_at) VALUES(?,?,?,?,?,?)",
            (task_id, step, agent, event, json.dumps(payload or {}, ensure_ascii=False), _now()),
        )
        self._publish(
            {
                "type": "trace",
                "task_id": task_id,
                "step": step,
                "agent": agent,
                "event": event,
                "payload": payload or {},
            }
        )

    def list_traces(self, task_id: str) -> list[TraceEvent]:
        rows = self.execute("SELECT * FROM trace WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return [
            TraceEvent(
                id=r["id"],
                task_id=r["task_id"],
                step=r["step"],
                agent=r["agent"],
                event=r["event"],
                payload=json.loads(r["payload"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
