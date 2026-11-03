from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.harness.budget import DualBudget
from src.harness.retry import RetryPolicy, UnrecoverableError, retry_with_backoff
from src.harness.state import TaskStatus
from src.observability.metrics import observe_retry, observe_task
from src.storage.backend import StorageBackend
from src.storage.models import PRTask


@dataclass
class PipelineResult:
    findings: list[str]
    patch: str
    validated: bool
    checkpoint: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


class Pipeline(Protocol):
    def run(self, task: PRTask, checkpoint: dict[str, Any] | None) -> PipelineResult: ...


@dataclass
class RunOutcome:
    task: PRTask
    result: PipelineResult | None
    error: str | None = None


class HarnessRuntime:
    def __init__(
        self,
        store: StorageBackend,
        max_attempts: int = 3,
        retry_delay: float = 0.05,
    ) -> None:
        self.store = store
        self.policy = RetryPolicy(max_attempts=max_attempts, base_delay=retry_delay, jitter=0)

    def submit_and_run(
        self,
        repo: str,
        pr_number: int,
        branch: str,
        diff: str,
        pipeline: Pipeline,
        max_steps: int = 40,
        max_seconds: float = 120,
        tenant: str = "default",
    ) -> RunOutcome:
        task = self.store.create_task(
            repo=repo,
            pr_number=pr_number,
            branch=branch,
            diff=diff,
            max_steps=max_steps,
            max_seconds=max_seconds,
            tenant=tenant,
        )
        return self._execute(task.id, pipeline)

    def resume(self, task_id: str, pipeline: Pipeline) -> RunOutcome:
        return self._execute(task_id, pipeline)

    def _execute(self, task_id: str, pipeline: Pipeline) -> RunOutcome:
        task = self.store.get_task(task_id)
        if task.status is TaskStatus.PENDING:
            self.store.set_status(task_id, TaskStatus.RUNNING)
        DualBudget(task.max_steps, task.max_seconds)  # dual budget is enforced by inner loop
        self.store.append_trace(task_id, 0, "harness", "status", {"status": "RUNNING"})

        def attempt() -> PipelineResult:
            checkpoint = self.store.load_checkpoint(task_id)
            current = self.store.get_task(task_id)
            if current.status is TaskStatus.CHECKPOINT:
                self.store.set_status(task_id, TaskStatus.RUNNING)
            try:
                result = pipeline.run(self.store.get_task(task_id), checkpoint)
            except Exception:
                self.store.bump_retry(task_id)
                observe_retry(self.store.get_task(task_id))
                raise
            self.store.save_checkpoint(task_id, result.checkpoint or {"ok": True})
            self._advance_phases(task_id)
            return result

        try:
            result = retry_with_backoff(attempt, self.policy)
            outcome = RunOutcome(task=self.store.get_task(task_id), result=result)
        except UnrecoverableError as exc:
            self.store.enqueue_dead_letter(task_id, reason="unrecoverable", detail=str(exc))
            outcome = RunOutcome(task=self.store.get_task(task_id), result=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self.store.enqueue_dead_letter(task_id, reason="retry_exhausted", detail=str(exc))
            outcome = RunOutcome(task=self.store.get_task(task_id), result=None, error=str(exc))
        observe_task(outcome.task, outcome.error)
        return outcome

    def _advance_phases(self, task_id: str) -> None:
        mapping = [
            TaskStatus.RUNNING,
            TaskStatus.CHECKPOINT,
            TaskStatus.VERIFYING,
            TaskStatus.FIXING,
            TaskStatus.VALIDATING,
            TaskStatus.DONE,
        ]
        current = self.store.get_task(task_id).status
        try:
            idx = mapping.index(current)
        except ValueError:
            return
        for nxt in mapping[idx + 1 :]:
            self.store.set_status(task_id, nxt)
            self.store.append_trace(task_id, 0, "harness", "status", {"status": nxt.value})
