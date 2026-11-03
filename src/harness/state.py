from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CHECKPOINT = "CHECKPOINT"
    VERIFYING = "VERIFYING"
    FIXING = "FIXING"
    VALIDATING = "VALIDATING"
    DONE = "DONE"
    DEAD_LETTER = "DEAD_LETTER"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.DEAD_LETTER}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.CHECKPOINT,
            TaskStatus.VERIFYING,
            TaskStatus.DEAD_LETTER,
            TaskStatus.BUDGET_EXCEEDED,
        }
    ),
    TaskStatus.CHECKPOINT: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
            TaskStatus.DEAD_LETTER,
            TaskStatus.BUDGET_EXCEEDED,
        }
    ),
    TaskStatus.VERIFYING: frozenset(
        {
            TaskStatus.FIXING,
            TaskStatus.VALIDATING,
            TaskStatus.CHECKPOINT,
            TaskStatus.DEAD_LETTER,
            TaskStatus.BUDGET_EXCEEDED,
        }
    ),
    TaskStatus.FIXING: frozenset(
        {
            TaskStatus.VALIDATING,
            TaskStatus.CHECKPOINT,
            TaskStatus.DEAD_LETTER,
            TaskStatus.BUDGET_EXCEEDED,
        }
    ),
    TaskStatus.VALIDATING: frozenset(
        {
            TaskStatus.DONE,
            TaskStatus.FIXING,
            TaskStatus.CHECKPOINT,
            TaskStatus.DEAD_LETTER,
            TaskStatus.BUDGET_EXCEEDED,
        }
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.DEAD_LETTER: frozenset(),
    TaskStatus.BUDGET_EXCEEDED: frozenset(),
}


def transition(current: TaskStatus, nxt: TaskStatus) -> TaskStatus:
    allowed = ALLOWED_TRANSITIONS[current]
    if nxt not in allowed:
        raise ValueError(f"illegal transition: {current.value} -> {nxt.value}")
    return nxt
