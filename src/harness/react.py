from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.harness.budget import BudgetExceeded, DualBudget
from src.harness.state import TaskStatus
from src.storage.backend import StorageBackend


@dataclass
class StepResult:
    thought: str
    action: str
    observation: str
    finished: bool = False
    payload: dict | None = None


class Agent(Protocol):
    def step(self, observation: str) -> StepResult: ...


@dataclass
class LoopResult:
    finished: bool
    steps: int
    last: StepResult | None


class ReActLoop:
    def __init__(self, store: StorageBackend, agent_name: str = "runtime") -> None:
        self.store = store
        self.agent_name = agent_name

    def run(self, task_id: str, agent: Agent, budget: DualBudget, observation: str = "") -> LoopResult:
        task = self.store.get_task(task_id)
        if task.status is TaskStatus.PENDING:
            self.store.set_status(task_id, TaskStatus.RUNNING)
        last: StepResult | None = None
        try:
            while True:
                budget.consume_step()
                last = agent.step(observation)
                self.store.append_trace(
                    task_id, budget.steps_used, self.agent_name, "reason", {"thought": last.thought}
                )
                self.store.append_trace(
                    task_id, budget.steps_used, self.agent_name, "act", {"action": last.action}
                )
                self.store.append_trace(
                    task_id,
                    budget.steps_used,
                    self.agent_name,
                    "observe",
                    {"observation": last.observation},
                )
                observation = last.observation
                if last.finished:
                    return LoopResult(finished=True, steps=budget.steps_used, last=last)
        except BudgetExceeded as exc:
            self.store.set_status(
                task_id,
                TaskStatus.BUDGET_EXCEEDED,
                reason=f"{exc.kind} budget exceeded",
            )
            raise
        return LoopResult(finished=False, steps=budget.steps_used, last=last)
