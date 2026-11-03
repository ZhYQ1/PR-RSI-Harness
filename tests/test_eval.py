import json
from pathlib import Path

from src.eval.benchmark import DATASET_PATH, ensure_benchmark, split_by_repo
from src.eval.runner import EvaluationHarness
from src.feedback.rsi import RSIEngine
from src.storage.sqlite_store import SQLiteStore


def test_benchmark_has_100_cases_split_by_repo():
    cases = ensure_benchmark(DATASET_PATH)
    assert len(cases) == 100
    validation, holdout = split_by_repo(cases)
    assert validation and holdout
    assert len(validation) + len(holdout) == 100
    v_repos = {c["repo"] for c in validation}
    h_repos = {c["repo"] for c in holdout}
    assert v_repos.isdisjoint(h_repos)


def test_evaluation_meets_iteration_targets(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    harness = EvaluationHarness(store)
    report = harness.run()
    assert report.validation.f1 >= 0.913
    assert report.validation.high_risk_recall >= 0.947
    assert report.validation.clean_pr_accuracy >= 0.917
    assert report.holdout.f1 >= 0.913
    assert report.gate.passed is True
    rows = store.execute("SELECT * FROM evaluation_result").fetchall()
    assert rows
    assert Path(report.path).exists()


def test_benchmark_diff_uses_added_lines():
    cases = ensure_benchmark(DATASET_PATH)
    risky = [c for c in cases if c["expected"]]
    assert risky
    for case in risky:
        body = [ln for ln in case["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        assert body, f"case {case['id']} has no added lines to scan"


def test_eval_records_feedback_for_missed_risks(tmp_path):
    dataset = tmp_path / "bench.json"
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        '+sql = f"DELETE FROM users WHERE id={uid}"\n'
    )
    cases = [
        {
            "id": f"miss-{i}",
            "repo": "tenant/alpha" if i == 0 else "tenant/beta",
            "pr_number": i + 1,
            "branch": "eval",
            "rule": "sql_injection",
            "diff": diff if i == 0 else '+query = "SELECT * FROM users WHERE id=" + uid',
            "expected": ["sql_injection"],
            "high_risk": ["sql_injection"],
            "split": None,
        }
        for i in range(2)
    ]
    dataset.write_text(json.dumps(cases), encoding="utf-8")

    store = SQLiteStore(tmp_path / "h.db")
    harness = EvaluationHarness(store, dataset_path=dataset)
    report = harness.run()

    # f-string variant escapes the scanner -> false negative feedback recorded
    rows = store.execute("SELECT * FROM feedback WHERE case_type='false_negative'").fetchall()
    assert rows
    assert rows[0]["failure_label"] == "missed:sql_injection"
    trajectory = json.loads(rows[0]["trajectory"])
    assert trajectory, "trajectory should capture trace events"
    assert report.validation.f1 < 1.0

