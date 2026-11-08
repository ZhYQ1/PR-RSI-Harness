from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from src.eval.metrics import Metrics
from src.feedback.feedback import PRReviewFeedback
from src.storage.backend import StorageBackend


@dataclass
class CandidateVersion:
    version: str
    kind: str
    content: str
    baseline: str | None
    cluster: str | None
    deduped: bool
    active: bool


@dataclass
class GateResult:
    passed: bool
    reason: str


class RSIEngine:
    def __init__(self, store: StorageBackend) -> None:
        self.store = store

    def _insert_feedback(
        self,
        task_id: str,
        case_type: str,
        predicted: list[str],
        expected: list[str],
        confidence: float,
        failure_label: str,
        trajectory: list[str],
    ) -> None:
        self.store.execute(
            """INSERT INTO feedback(task_id, case_type, predicted, expected, confidence, failure_label, trajectory)
               VALUES(?,?,?,?,?,?,?)""",
            (
                task_id,
                case_type,
                json.dumps(predicted),
                json.dumps(expected),
                confidence,
                failure_label,
                json.dumps(trajectory),
            ),
        )

    def record_feedback(
        self,
        task_id: str,
        case_type: str,
        predicted: list[str],
        expected: list[str],
        confidence: float,
        failure_label: str,
        trajectory: list[str],
    ) -> None:
        self._insert_feedback(
            task_id,
            case_type,
            predicted,
            expected,
            confidence,
            failure_label,
            trajectory,
        )

    def record_execution_feedback(self, feedback: PRReviewFeedback) -> None:
        """Record real-PR-review feedback into the same ``feedback`` table.

        Derives ``case_type`` / ``failure_label`` per rule from the set
        difference between ``predicted`` and ``expected``, so real-execution
        failures cluster with evaluation failures in :meth:`cluster_failures`.
        """
        predicted_set = set(feedback.predicted)
        expected_set = set(feedback.expected)
        false_alarms = sorted(predicted_set - expected_set)
        missed = sorted(expected_set - predicted_set)
        if not false_alarms and not missed:
            return
        for rule in false_alarms:
            self.record_feedback(
                task_id=feedback.task_id,
                case_type="false_positive",
                predicted=feedback.predicted,
                expected=feedback.expected,
                confidence=feedback.confidence,
                failure_label=f"false_alarm:{rule}",
                trajectory=feedback.trajectory,
            )
        for rule in missed:
            self.record_feedback(
                task_id=feedback.task_id,
                case_type="false_negative",
                predicted=feedback.predicted,
                expected=feedback.expected,
                confidence=feedback.confidence,
                failure_label=f"missed:{rule}",
                trajectory=feedback.trajectory,
            )

    def cluster_failures(self) -> dict[str, int]:
        rows = self.store.execute(
            "SELECT failure_label, COUNT(*) AS n FROM feedback GROUP BY failure_label"
        ).fetchall()
        return {r["failure_label"]: r["n"] for r in rows}

    def propose(self, kind: str, content: str, baseline: str | None, cluster: str | None) -> CandidateVersion:
        existing = self.store.execute(
            "SELECT * FROM candidate_version WHERE kind=? AND content=?",
            (kind, content),
        ).fetchone()
        if existing:
            self.store.execute(
                "UPDATE candidate_version SET deduped=1 WHERE id=?", (existing["id"],)
            )
            row = self.store.execute(
                "SELECT * FROM candidate_version WHERE id=?", (existing["id"],)
            ).fetchone()
            item = self._row(row)
            item.deduped = True
            return item
        version = f"v-{uuid.uuid4().hex[:8]}"
        self.store.execute(
            """INSERT INTO candidate_version(version, kind, content, baseline, cluster, deduped, active)
               VALUES(?,?,?,?,?,0,0)""",
            (version, kind, content, baseline, cluster),
        )
        row = self.store.execute(
            "SELECT * FROM candidate_version WHERE version=?", (version,)
        ).fetchone()
        return self._row(row)

    def activate(self, version: str) -> None:
        self.store.execute("UPDATE candidate_version SET active=0")
        self.store.execute("UPDATE candidate_version SET active=1 WHERE version=?", (version,))

    def rollback(self, version: str, to_version: str) -> CandidateVersion:
        self.store.execute(
            "UPDATE candidate_version SET active=0, baseline=? WHERE version=?",
            (to_version, version),
        )
        row = self.store.execute(
            "SELECT * FROM candidate_version WHERE version=?", (version,)
        ).fetchone()
        return self._row(row)

    def gate(self, baseline: Metrics, candidate: Metrics) -> GateResult:
        checks = [
            ("f1", candidate.f1, baseline.f1),
            ("high_risk_recall", candidate.high_risk_recall, baseline.high_risk_recall),
            ("clean_pr_accuracy", candidate.clean_pr_accuracy, baseline.clean_pr_accuracy),
        ]
        for name, cur, base in checks:
            if cur + 1e-12 < base:
                return GateResult(False, f"{name}_regressed")
        improved = any(cur > base + 1e-12 for _, cur, base in checks)
        if not improved:
            return GateResult(False, "no_improvement")
        return GateResult(True, "improve_and_no_regression")

    # ------------------------------------------------------------- evolution

    def failure_samples(self, cluster_label: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.store.execute(
            "SELECT case_type, predicted, expected, failure_label FROM feedback WHERE failure_label=?",
            (cluster_label,),
        ).fetchall()
        return [
            {
                "case_type": row["case_type"],
                "predicted": json.loads(row["predicted"]),
                "expected": json.loads(row["expected"]),
                "failure_label": row["failure_label"],
            }
            for row in rows[:limit]
        ]

    def active_prompt(self) -> str | None:
        row = self.store.execute(
            "SELECT content FROM candidate_version WHERE kind='prompt' AND active=1 LIMIT 1"
        ).fetchone()
        return row["content"] if row else None

    def active_version(self) -> str | None:
        row = self.store.execute(
            "SELECT version FROM candidate_version WHERE active=1 LIMIT 1"
        ).fetchone()
        return row["version"] if row else None

    def evolve(
        self,
        generator: Any,
        evaluate: Callable[[], Metrics] | None = None,
    ) -> list[dict[str, Any]]:
        """Cluster failures, generate candidates, dedupe, and gate activations.

        Without ``evaluate`` candidates are only proposed (never activated).
        With ``evaluate``, each new candidate is tentatively activated, the
        evaluation runs again, and the candidate keeps the active flag only if
        the improve + no-regression gate passes; otherwise the previous active
        version is restored.
        """
        previous_active = self.active_version()
        outcomes: list[dict[str, Any]] = []
        for label, count in sorted(self.cluster_failures().items()):
            kind, content = generator.generate(label, self.failure_samples(label))
            candidate = self.propose(kind, content, baseline=previous_active, cluster=label)
            entry: dict[str, Any] = {
                "cluster": label,
                "count": count,
                "version": candidate.version,
                "deduped": candidate.deduped,
                "activated": False,
                "gate": None,
            }
            if candidate.deduped or evaluate is None:
                outcomes.append(entry)
                continue
            baseline_metrics = evaluate()
            self.activate(candidate.version)
            candidate_metrics = evaluate()
            gate = self.gate(baseline_metrics, candidate_metrics)
            entry["gate"] = gate.reason
            if gate.passed:
                entry["activated"] = True
                previous_active = candidate.version
            else:
                self.store.execute(
                    "UPDATE candidate_version SET active=0 WHERE version=?", (candidate.version,)
                )
                if previous_active:
                    self.activate(previous_active)
            outcomes.append(entry)
        return outcomes

    @staticmethod
    def _row(row: Any) -> CandidateVersion:
        return CandidateVersion(
            version=row["version"],
            kind=row["kind"],
            content=row["content"],
            baseline=row["baseline"],
            cluster=row["cluster"],
            deduped=bool(row["deduped"]),
            active=bool(row["active"]),
        )
