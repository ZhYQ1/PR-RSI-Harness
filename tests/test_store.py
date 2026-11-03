from src.harness.state import TaskStatus
from src.storage.sqlite_store import SQLiteStore


def test_create_and_load_task(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(
        repo="acme/api",
        pr_number=12,
        branch="feat",
        diff="diff --git a/x.py b/x.py",
        max_steps=10,
        max_seconds=30,
    )
    loaded = store.get_task(task.id)
    assert loaded.status is TaskStatus.PENDING
    assert loaded.repo == "acme/api"
    assert loaded.pr_number == 12


def test_checkpoint_roundtrip(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.save_checkpoint(task.id, {"phase": "VERIFYING", "findings": ["sql"]})
    payload = store.load_checkpoint(task.id)
    assert payload["phase"] == "VERIFYING"
    assert payload["findings"] == ["sql"]


def test_dead_letter_records_reason(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.enqueue_dead_letter(task.id, reason="unrecoverable", detail="parse error")
    letters = store.list_dead_letters()
    assert len(letters) == 1
    assert letters[0].reason == "unrecoverable"
    assert store.get_task(task.id).status is TaskStatus.DEAD_LETTER


def test_trace_steps_are_ordered(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.append_trace(task.id, step=1, agent="risk", event="reason", payload={"t": "a"})
    store.append_trace(task.id, step=2, agent="risk", event="act", payload={"tool": "scan"})
    traces = store.list_traces(task.id)
    assert [t.event for t in traces] == ["reason", "act"]
    assert traces[0].step == 1
