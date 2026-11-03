from prometheus_client import REGISTRY

from src.harness.budget import BudgetExceeded, DualBudget
from src.harness.react import ReActLoop, StepResult
from src.harness.runtime import HarnessRuntime
from src.harness.state import TaskStatus
from src.observability.trace import TraceReplayer
from src.storage.sqlite_store import SQLiteStore


class ScriptedPipeline:
    def __init__(self, fatal: bool = False) -> None:
        self.fatal = fatal

    def run(self, task, checkpoint):
        if self.fatal:
            raise RuntimeError("transient worker error")
        from src.harness.runtime import PipelineResult

        return PipelineResult(findings=[], patch="", validated=True, checkpoint={"ok": True})


class EndlessAgent:
    def step(self, observation: str) -> StepResult:
        return StepResult(thought="t", action="scan", observation=observation, finished=False)


def _counter(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return value or 0.0


def test_metrics_record_done_outcome(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    labels = {"tenant": "default", "repo": "r", "outcome": "done"}
    before = _counter("pr_harness_tasks_total", labels)
    runtime.submit_and_run(repo="r", pr_number=1, branch="m", diff="d", pipeline=ScriptedPipeline())
    assert _counter("pr_harness_tasks_total", labels) == before + 1.0


def test_metrics_record_dead_letter_and_retries(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store, max_attempts=2, retry_delay=0)
    dead_labels = {"tenant": "default", "repo": "r", "outcome": "dead_letter"}
    retry_labels = {"tenant": "default", "repo": "r"}
    reason_labels = {"reason": "retry_exhausted"}
    dead_before = _counter("pr_harness_tasks_total", dead_labels)
    retries_before = _counter("pr_harness_retries_total", retry_labels)
    reasons_before = _counter("pr_harness_dead_letters_total", reason_labels)
    runtime.submit_and_run(repo="r", pr_number=1, branch="m", diff="d", pipeline=ScriptedPipeline(fatal=True))
    assert _counter("pr_harness_tasks_total", dead_labels) == dead_before + 1.0
    assert _counter("pr_harness_retries_total", retry_labels) == retries_before + 2.0
    assert _counter("pr_harness_dead_letters_total", reason_labels) == reasons_before + 1.0


def test_replay_returns_ordered_timeline(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="r", pr_number=1, branch="m", diff="d", pipeline=ScriptedPipeline()
    )
    steps = TraceReplayer(store).replay(outcome.task.id)
    assert steps, "expected trace events"
    events = [s.event for s in steps]
    assert events[0] == "status"
    assert events[-1] == "status"
    assert steps[-1].payload["status"] == "DONE"
    steps_used = [s.step for s in steps]
    assert steps_used == sorted(steps_used)


def test_attribute_failure_for_dead_letter(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="r", pr_number=1, branch="m", diff="d", pipeline=ScriptedPipeline(fatal=True)
    )
    attribution = TraceReplayer(store).attribute_failure(outcome.task.id)
    assert attribution is not None
    data = attribution.as_dict()
    assert data["status"] == "DEAD_LETTER"
    assert data["failed_agent"] == "harness"
    assert data["dead_letter"]["reason"] == "retry_exhausted"


def test_attribute_failure_for_budget_exceeded(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    loop = ReActLoop(store=store)
    try:
        loop.run(task.id, EndlessAgent(), DualBudget(max_steps=2, max_seconds=5))
        raise AssertionError("expected budget exceeded")
    except BudgetExceeded:
        pass
    attribution = TraceReplayer(store).attribute_failure(task.id)
    assert attribution is not None
    data = attribution.as_dict()
    assert data["status"] == "BUDGET_EXCEEDED"
    assert "steps budget" in (data["end_reason"] or "")


def test_attribute_failure_none_when_done(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="r", pr_number=1, branch="m", diff="d", pipeline=ScriptedPipeline()
    )
    assert outcome.task.status is TaskStatus.DONE
    assert TraceReplayer(store).attribute_failure(outcome.task.id) is None
