from __future__ import annotations

import time


class BudgetExceeded(RuntimeError):
    def __init__(self, kind: str, used: float, limit: float) -> None:
        super().__init__(f"{kind} budget exceeded: used={used} limit={limit}")
        self.kind = kind
        self.used = used
        self.limit = limit


class DualBudget:
    def __init__(self, max_steps: int, max_seconds: float) -> None:
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.steps_used = 0
        self.started_at = time.monotonic()

    @property
    def steps_remaining(self) -> int:
        return self.max_steps - self.steps_used

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def consume_step(self) -> None:
        self.check_time()
        if self.steps_used >= self.max_steps:
            raise BudgetExceeded("steps", self.steps_used, self.max_steps)
        self.steps_used += 1

    def check_time(self) -> None:
        elapsed = self.elapsed_seconds
        if elapsed > self.max_seconds:
            raise BudgetExceeded("time", elapsed, self.max_seconds)

    def snapshot(self) -> dict[str, float | int]:
        return {
            "steps_used": self.steps_used,
            "max_steps": self.max_steps,
            "elapsed_seconds": self.elapsed_seconds,
            "max_seconds": self.max_seconds,
        }
