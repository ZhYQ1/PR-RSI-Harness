from src.eval.metrics import Metrics
from src.feedback.rsi import RSIEngine
from src.storage.sqlite_store import SQLiteStore


def test_records_structured_feedback_and_clusters(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    engine.record_feedback(
        task_id="t1",
        case_type="fn",
        predicted=[],
        expected=["sql_injection"],
        confidence=0.2,
        failure_label="missed_sql",
        trajectory=["identify"],
    )
    engine.record_feedback(
        task_id="t2",
        case_type="fn",
        predicted=[],
        expected=["sql_injection"],
        confidence=0.1,
        failure_label="missed_sql",
        trajectory=["identify"],
    )
    engine.record_feedback(
        task_id="t3",
        case_type="fp",
        predicted=["xss"],
        expected=[],
        confidence=0.9,
        failure_label="over_xss",
        trajectory=["identify"],
    )
    clusters = engine.cluster_failures()
    assert clusters["missed_sql"] == 2
    assert clusters["over_xss"] == 1


def test_candidate_dedup_and_rollback(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    v1 = engine.propose("prompt", "scan for SQL concatenation", baseline="v0", cluster="missed_sql")
    v2 = engine.propose("prompt", "scan for SQL concatenation", baseline="v0", cluster="missed_sql")
    assert v1.deduped is False
    assert v2.deduped is True
    engine.activate(v1.version)
    rolled = engine.rollback(v1.version, to_version="v0")
    assert rolled.active is False


def test_gate_requires_improve_and_no_regression():
    engine = RSIEngine(SQLiteStore(":memory:"))
    baseline = Metrics(f1=0.819, high_risk_recall=0.842, clean_pr_accuracy=0.90)
    better = Metrics(f1=0.913, high_risk_recall=0.947, clean_pr_accuracy=0.917)
    worse_recall = Metrics(f1=0.95, high_risk_recall=0.80, clean_pr_accuracy=0.92)
    assert engine.gate(baseline, better).passed is True
    assert engine.gate(baseline, worse_recall).passed is False
    assert engine.gate(baseline, worse_recall).reason == "high_risk_recall_regressed"


class StubGenerator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, label, samples):
        self.calls.append(label)
        return "prompt", f"prompt for {label}"


def _metrics(value: float) -> Metrics:
    return Metrics(f1=value, high_risk_recall=value, clean_pr_accuracy=value)


def _metrics_factory(baseline: float, candidate: float):
    calls = {"n": 0}

    def evaluate() -> Metrics:
        value = baseline if calls["n"] == 0 else candidate
        calls["n"] += 1
        return _metrics(value)

    return evaluate


def _feed_misses(engine: RSIEngine, label: str, count: int = 2) -> None:
    for i in range(count):
        engine.record_feedback(
            task_id=f"t-{label}-{i}",
            case_type="fn",
            predicted=[],
            expected=["sql_injection"],
            confidence=0.2,
            failure_label=label,
            trajectory=["identify"],
        )


def test_evolve_without_evaluator_only_proposes(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    _feed_misses(engine, "missed_sql")
    outcomes = engine.evolve(StubGenerator(), evaluate=None)
    assert len(outcomes) == 1
    assert outcomes[0]["activated"] is False
    assert outcomes[0]["gate"] is None
    assert engine.active_prompt() is None


def test_evolve_activates_candidate_on_gate_pass(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    _feed_misses(engine, "missed_sql")
    outcomes = engine.evolve(StubGenerator(), evaluate=_metrics_factory(0.5, 0.9))
    assert outcomes[0]["activated"] is True
    assert outcomes[0]["gate"] == "improve_and_no_regression"
    assert engine.active_prompt() == "prompt for missed_sql"


def test_evolve_rolls_back_on_gate_fail(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    _feed_misses(engine, "missed_sql")
    outcomes = engine.evolve(StubGenerator(), evaluate=_metrics_factory(0.9, 0.5))
    assert outcomes[0]["activated"] is False
    assert outcomes[0]["gate"] == "f1_regressed"
    assert engine.active_prompt() is None


def test_evolve_skips_deduped_candidates(tmp_path):
    engine = RSIEngine(SQLiteStore(tmp_path / "h.db"))
    _feed_misses(engine, "missed_sql")
    generator = StubGenerator()
    engine.evolve(generator, evaluate=_metrics_factory(0.5, 0.9))
    first = engine.evolve(generator, evaluate=_metrics_factory(0.5, 0.9))
    assert first[0]["deduped"] is True
    assert first[0]["gate"] is None  # deduped candidates skip evaluation and activation
    assert engine.active_prompt() == "prompt for missed_sql"
