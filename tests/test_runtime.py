from src.feedback.feedback import PRReviewFeedback
from src.harness.retry import UnrecoverableError
from src.harness.runtime import HarnessRuntime, PipelineResult
from src.harness.state import TaskStatus
from src.storage.sqlite_store import SQLiteStore


class ScriptedPipeline:
    def __init__(self, fail_times: int = 0, fatal: bool = False) -> None:
        self.fail_times = fail_times
        self.fatal = fatal
        self.calls = 0

    def run(self, task, checkpoint):
        self.calls += 1
        if self.fatal:
            raise UnrecoverableError("cannot parse diff")
        if self.calls <= self.fail_times:
            raise RuntimeError("transient worker error")
        start_phase = (checkpoint or {}).get("phase", "VERIFYING")
        return PipelineResult(
            findings=["hardcoded-secret"] if "SECRET" in task.diff else [],
            patch="" if "SECRET" not in task.diff else "remove secret",
            validated=True,
            checkpoint={"phase": start_phase},
        )


def test_runtime_runs_to_done_and_records_trace(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    result = runtime.submit_and_run(
        repo="acme/api",
        pr_number=7,
        branch="feat",
        diff="no issue",
        pipeline=ScriptedPipeline(),
    )
    assert result.task.status is TaskStatus.DONE
    events = [t.event for t in store.list_traces(result.task.id)]
    assert "status" in events


def test_runtime_retries_then_succeeds(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store, max_attempts=3, retry_delay=0)
    pipeline = ScriptedPipeline(fail_times=2)
    result = runtime.submit_and_run(
        repo="r", pr_number=1, branch="m", diff="ok", pipeline=pipeline
    )
    assert result.task.status is TaskStatus.DONE
    assert pipeline.calls == 3
    assert result.task.retry_count == 2


def test_runtime_sends_unrecoverable_to_dlq(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    result = runtime.submit_and_run(
        repo="r",
        pr_number=1,
        branch="m",
        diff="bad",
        pipeline=ScriptedPipeline(fatal=True),
    )
    assert result.task.status is TaskStatus.DEAD_LETTER
    assert store.list_dead_letters()[0].reason == "unrecoverable"


def test_runtime_resumes_from_checkpoint(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="SECRET=1")
    store.set_status(task.id, TaskStatus.RUNNING)
    store.set_status(task.id, TaskStatus.CHECKPOINT)
    store.save_checkpoint(task.id, {"phase": "FIXING"})
    result = runtime.resume(task.id, ScriptedPipeline())
    assert result.task.status is TaskStatus.DONE
    loaded = store.load_checkpoint(task.id)
    assert loaded is not None


class FeedbackPipeline:
    def run(self, task, checkpoint, review_feedback=None):
        return PipelineResult(
            findings=["xss"],
            patch="",
            validated=True,
            checkpoint={},
            review_feedback=review_feedback,
        )


def test_runtime_records_review_feedback_on_success(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    fb = PRReviewFeedback(
        task_id="r1",
        predicted=["xss"],
        expected=[],
        confidence=0.5,
        trajectory=["review"],
    )
    result = runtime.submit_and_run(
        repo="acme/api",
        pr_number=7,
        branch="feat",
        diff="no issue",
        pipeline=FeedbackPipeline(),
        review_feedback=fb,
    )
    assert result.task.status is TaskStatus.DONE
    rows = store.execute(
        "SELECT case_type, failure_label FROM feedback WHERE task_id='r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["case_type"] == "false_positive"
    assert rows[0]["failure_label"] == "false_alarm:xss"


def test_runtime_ignores_none_review_feedback(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    result = runtime.submit_and_run(
        repo="r",
        pr_number=1,
        branch="m",
        diff="no issue",
        pipeline=FeedbackPipeline(),
    )
    assert result.task.status is TaskStatus.DONE
    assert store.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"] == 0
