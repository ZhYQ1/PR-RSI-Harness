from __future__ import annotations

from datetime import datetime

from prometheus_client import Counter, Histogram, start_http_server

from src.harness.state import TaskStatus
from src.storage.models import PRTask

TASKS_TOTAL = Counter(
    "pr_harness_tasks_total",
    "PR tasks processed by outcome",
    ["tenant", "repo", "outcome"],
)
TASK_DURATION = Histogram(
    "pr_harness_task_duration_seconds",
    "Wall-clock duration of task execution",
    ["tenant", "repo", "outcome"],
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300),
)
RETRIES_TOTAL = Counter(
    "pr_harness_retries_total",
    "Retry attempts across tasks",
    ["tenant", "repo"],
)
DEAD_LETTERS_TOTAL = Counter(
    "pr_harness_dead_letters_total",
    "Tasks routed to the dead letter queue",
    ["reason"],
)

_OUTCOME_BY_STATUS = {
    TaskStatus.DONE: "done",
    TaskStatus.DEAD_LETTER: "dead_letter",
    TaskStatus.BUDGET_EXCEEDED: "budget_exceeded",
}


def _outcome(task: PRTask) -> str:
    return _OUTCOME_BY_STATUS.get(task.status, "unknown")


def _duration_seconds(task: PRTask) -> float | None:
    if not task.start_time or not task.end_time:
        return None
    start = datetime.fromisoformat(task.start_time)
    end = datetime.fromisoformat(task.end_time)
    return max(0.0, (end - start).total_seconds())


def observe_task(task: PRTask, error: str | None = None) -> None:
    outcome = _outcome(task)
    labels = {"tenant": task.tenant, "repo": task.repo, "outcome": outcome}
    TASKS_TOTAL.labels(**labels).inc()
    if outcome == "dead_letter":
        DEAD_LETTERS_TOTAL.labels(reason=_dead_letter_reason(task, error)).inc()
    duration = _duration_seconds(task)
    if duration is not None:
        TASK_DURATION.labels(**labels).observe(duration)


def observe_retry(task: PRTask) -> None:
    RETRIES_TOTAL.labels(tenant=task.tenant, repo=task.repo).inc()


def _dead_letter_reason(task: PRTask, error: str | None) -> str:
    if task.status is TaskStatus.BUDGET_EXCEEDED or "budget" in (task.end_reason or ""):
        return "budget_exceeded"
    return "retry_exhausted" if error else "unrecoverable"


def start_metrics_server(port: int = 8000, addr: str = "0.0.0.0") -> None:
    """Expose Prometheus metrics on an HTTP endpoint."""
    start_http_server(port, addr=addr)
