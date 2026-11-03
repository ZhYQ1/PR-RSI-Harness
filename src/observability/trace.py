from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.harness.state import TaskStatus
from src.storage.backend import StorageBackend


@dataclass(frozen=True)
class ReplayStep:
    step: int
    agent: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass
class FailureAttribution:
    task_id: str
    status: str
    failed_agent: str
    failed_step: int
    last_event: str | None
    last_payload: dict[str, Any]
    end_reason: str | None
    dead_letter: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "failed_agent": self.failed_agent,
            "failed_step": self.failed_step,
            "last_event": self.last_event,
            "last_payload": self.last_payload,
            "end_reason": self.end_reason,
            "dead_letter": self.dead_letter,
        }


class TraceReplayer:
    """Replay a task's full trace timeline and attribute failures."""

    def __init__(self, store: StorageBackend) -> None:
        self.store = store

    def replay(self, task_id: str) -> list[ReplayStep]:
        steps: list[ReplayStep] = []
        for event in self.store.list_traces(task_id):
            steps.append(
                ReplayStep(
                    step=event.step,
                    agent=event.agent,
                    event=event.event,
                    payload=event.payload,
                    created_at=event.created_at,
                )
            )
        return steps

    def attribute_failure(self, task_id: str) -> FailureAttribution | None:
        task = self.store.get_task(task_id)
        if task.status is TaskStatus.DONE:
            return None
        steps = self.replay(task_id)
        last = steps[-1] if steps else None
        attribution = FailureAttribution(
            task_id=task_id,
            status=task.status.value,
            failed_agent=last.agent if last else "harness",
            failed_step=last.step if last else 0,
            last_event=last.event if last else None,
            last_payload=last.payload if last else {},
            end_reason=task.end_reason,
        )
        for letter in self.store.list_dead_letters():
            if letter.task_id == task_id:
                attribution.dead_letter = {
                    "reason": letter.reason,
                    "detail": letter.detail,
                    "handling_status": letter.handling_status,
                }
                break
        return attribution
