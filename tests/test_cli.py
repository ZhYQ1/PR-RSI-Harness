import json

from src.cli import main
from src.feedback.rsi import RSIEngine
from src.harness.state import TaskStatus
from src.storage.sqlite_store import SQLiteStore


def _last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_cli_submit_reviews_diff(tmp_path, capsys):
    db = tmp_path / "h.db"
    code = main(
        [
            "--db",
            str(db),
            "submit",
            "--repo",
            "acme/api",
            "--pr",
            "7",
            "--branch",
            "feat",
            "--diff",
            '+API_KEY = "sk-live-1234567890abcdef"',
        ]
    )
    assert code == 0
    payload = _last_json(capsys)
    assert payload["status"] == "DONE"
    assert payload["findings"] == ["hardcoded_secret"]
    assert payload["validated"] is True


def test_cli_eval_runs_benchmark(tmp_path, capsys):
    dataset = tmp_path / "bench.json"
    code = main(["--db", str(tmp_path / "h.db"), "eval", "--dataset", str(dataset)])
    assert code == 0
    payload = _last_json(capsys)
    assert payload["cases"] == 100
    assert payload["gate"]["passed"] is True
    assert dataset.exists()


def test_cli_replay_shows_steps(tmp_path, capsys):
    db = tmp_path / "h.db"
    submit = main(
        [
            "--db",
            str(db),
            "submit",
            "--repo",
            "r",
            "--pr",
            "1",
            "--branch",
            "m",
            "--diff",
            "+pass",
        ]
    )
    assert submit == 0
    task_id = _last_json(capsys)["task_id"]
    capsys.readouterr()
    code = main(["--db", str(db), "replay", "--task-id", task_id])
    assert code == 0
    payload = _last_json(capsys)
    assert payload["steps"]
    assert payload["failure_attribution"] is None


def test_cli_dead_letters_lists_entries(tmp_path, capsys):
    db = tmp_path / "h.db"
    store = SQLiteStore(db)
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.set_status(task.id, TaskStatus.RUNNING)
    store.enqueue_dead_letter(task.id, reason="unrecoverable", detail="boom")
    capsys.readouterr()

    code = main(["--db", str(db), "dead-letters"])
    assert code == 0
    entries = _last_json(capsys)
    assert len(entries) == 1
    assert entries[0]["task_id"] == task.id
    assert entries[0]["reason"] == "unrecoverable"


def test_cli_resume_completes_task(tmp_path, capsys):
    db = tmp_path / "h.db"
    store = SQLiteStore(db)
    task = store.create_task(repo="r", pr_number=2, branch="m", diff="+pass")
    store.set_status(task.id, TaskStatus.RUNNING)
    store.set_status(task.id, TaskStatus.CHECKPOINT)
    store.save_checkpoint(task.id, {"phase": "VERIFYING"})

    code = main(["--db", str(db), "resume", "--task-id", task.id])
    assert code == 0
    payload = _last_json(capsys)
    assert payload["status"] == "DONE"


def test_cli_evolve_proposes_and_gates(tmp_path, capsys):
    db = tmp_path / "h.db"
    store = SQLiteStore(db)
    engine = RSIEngine(store)
    engine.record_feedback(
        task_id="t1",
        case_type="false_negative",
        predicted=[],
        expected=["sql_injection"],
        confidence=0.2,
        failure_label="missed:sql_injection",
        trajectory=["status", "reason"],
    )

    dataset = tmp_path / "bench.json"
    cases = [
        {
            "id": f"clean-{i}",
            "repo": f"tenant/repo-clean-{i}",
            "pr_number": i + 1,
            "branch": "eval",
            "rule": "clean",
            "diff": "+def add(a, b):\n+    return a + b",
            "expected": [],
            "high_risk": [],
            "split": None,
        }
        for i in range(2)
    ]
    dataset.write_text(json.dumps(cases), encoding="utf-8")

    code = main(["--db", str(db), "evolve", "--dataset", str(dataset)])
    assert code == 0
    proposals = _last_json(capsys)
    assert len(proposals) == 1
    entry = proposals[0]
    assert entry["cluster"] == "missed:sql_injection"
    assert entry["deduped"] == 0
    assert entry["activated"] is False  # no metric improvement offline -> rollback
    assert entry["gate"] == "no_improvement"


def test_cli_review_feedback_records(tmp_path, capsys):
    db = tmp_path / "h.db"
    code = main(
        [
            "--db",
            str(db),
            "review-feedback",
            "--task-id",
            "r1",
            "--predicted",
            '["xss"]',
            "--expected",
            "[]",
            "--confidence",
            "0.5",
            "--trajectory",
            '["review"]',
        ]
    )
    assert code == 0
    assert _last_json(capsys) == {"task_id": "r1", "recorded": True}
    rows = SQLiteStore(db).execute(
        "SELECT case_type, failure_label FROM feedback WHERE task_id='r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["case_type"] == "false_positive"
    assert rows[0]["failure_label"] == "false_alarm:xss"


def test_cli_review_feedback_skips_when_no_difference(tmp_path, capsys):
    db = tmp_path / "h.db"
    code = main(
        [
            "--db",
            str(db),
            "review-feedback",
            "--task-id",
            "r2",
            "--predicted",
            '["xss"]',
            "--expected",
            '["xss"]',
        ]
    )
    assert code == 0
    assert _last_json(capsys) == {"task_id": "r2", "recorded": True}
    assert (
        SQLiteStore(db).execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"] == 0
    )
