from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.harness.state import TaskStatus


@dataclass
class PRTask:
    id: str
    repo: str
    pr_number: int
    branch: str
    diff: str
    status: TaskStatus
    max_steps: int
    max_seconds: float
    retry_count: int = 0
    tenant: str = "default"
    start_time: str | None = None
    end_time: str | None = None
    end_reason: str | None = None


@dataclass
class TraceEvent:
    id: int
    task_id: str
    step: int
    agent: str
    event: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class DeadLetter:
    id: int
    task_id: str
    reason: str
    detail: str
    handling_status: str = "open"
    handler: str | None = None
