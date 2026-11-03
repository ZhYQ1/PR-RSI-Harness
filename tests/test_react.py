from src.harness.budget import BudgetExceeded, DualBudget
from src.harness.react import ReActLoop, StepResult
from src.harness.state import TaskStatus
from src.storage.sqlite_store import SQLiteStore


class CountingAgent:
    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def step(self, observation: str) -> StepResult:
        self.calls += 1
        if self.calls >= self.n:
            return StepResult(thought="done", action="finish", observation="ok", finished=True)
        return StepResult(
            thought=f"think-{self.calls}",
            action="scan",
            observation=observation,
            finished=False,
        )


def test_react_stops_when_agent_finishes(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    loop = ReActLoop(store=store)
    result = loop.run(task.id, CountingAgent(2), DualBudget(max_steps=10, max_seconds=5))
    assert result.finished is True
    assert result.steps == 2
    events = [t.event for t in store.list_traces(task.id)]
    assert events.count("reason") == 2
    assert events.count("act") == 2
    assert events.count("observe") == 2


def test_react_stops_on_step_budget(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    loop = ReActLoop(store=store)
    try:
        loop.run(task.id, CountingAgent(99), DualBudget(max_steps=3, max_seconds=5))
        raise AssertionError("expected budget exceeded")
    except BudgetExceeded:
        pass
    assert store.get_task(task.id).status is TaskStatus.BUDGET_EXCEEDED
    assert store.get_task(task.id).end_reason == "steps budget exceeded"
