from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.agent.pipeline import ReviewPipeline
from src.eval.benchmark import DATASET_PATH, ensure_benchmark, split_by_repo
from src.eval.metrics import Metrics, compute_metrics
from src.feedback.rsi import GateResult, RSIEngine
from src.harness.runtime import HarnessRuntime
from src.memory.store import MemoryManager
from src.storage.backend import StorageBackend

BASELINE = Metrics(f1=0.819, high_risk_recall=0.842, clean_pr_accuracy=0.90)


@dataclass
class EvalReport:
    validation: Metrics
    holdout: Metrics
    gate: GateResult
    path: str
    cases: int


class EvaluationHarness:
    def __init__(self, store: StorageBackend, dataset_path: Path = DATASET_PATH) -> None:
        self.store = store
        self.dataset_path = dataset_path
        self.runtime = HarnessRuntime(store, retry_delay=0)
        self.memory = MemoryManager(store)
        self.rsi = RSIEngine(store)

    def run(self) -> EvalReport:
        cases = ensure_benchmark(self.dataset_path)
        validation, holdout = split_by_repo(cases)
        v_metrics = self._eval_split(validation, "validation")
        h_metrics = self._eval_split(holdout, "holdout")
        gate = self.rsi.gate(BASELINE, v_metrics)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "validation": asdict(v_metrics),
            "holdout": asdict(h_metrics),
            "gate": {"passed": gate.passed, "reason": gate.reason},
            "baseline": asdict(BASELINE),
            "cases": len(cases),
        }
        report_path = self.dataset_path.parent / "eval_report.json"
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.execute(
            """INSERT INTO evaluation_result(dataset, f1, high_risk_recall, clean_pr_accuracy, gate_passed, report, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                "validation",
                v_metrics.f1,
                v_metrics.high_risk_recall,
                v_metrics.clean_pr_accuracy,
                int(gate.passed),
                json.dumps(payload),
                payload["created_at"],
            ),
        )
        return EvalReport(
            validation=v_metrics,
            holdout=h_metrics,
            gate=gate,
            path=str(report_path),
            cases=len(cases),
        )

    def _eval_split(self, cases: list[dict], name: str) -> Metrics:
        scored = []
        pipeline = ReviewPipeline(memory=self.memory, prompt_provider=self.rsi.active_prompt)
        for case in cases:
            outcome = self.runtime.submit_and_run(
                repo=case["repo"],
                pr_number=case["pr_number"],
                branch=case["branch"],
                diff=case["diff"],
                pipeline=pipeline,
            )
            predicted = outcome.result.findings if outcome.result else []
            scored.append(
                {
                    "predicted": predicted,
                    "expected": case["expected"],
                    "high_risk": case["high_risk"],
                }
            )
            self._record_feedback(case, predicted, outcome)
        return compute_metrics(scored)

    def _record_feedback(self, case: dict, predicted: list[str], outcome) -> None:
        predicted_set = set(predicted)
        expected_set = set(case["expected"])
        false_alarms = sorted(predicted_set - expected_set)
        missed = sorted(expected_set - predicted_set)
        if not false_alarms and not missed:
            return
        trajectory = [event.event for event in self.store.list_traces(outcome.task.id)]
        for rule in false_alarms:
            self.rsi.record_feedback(
                task_id=outcome.task.id,
                case_type="false_positive",
                predicted=predicted,
                expected=case["expected"],
                confidence=0.5,
                failure_label=f"false_alarm:{rule}",
                trajectory=trajectory,
            )
        for rule in missed:
            self.rsi.record_feedback(
                task_id=outcome.task.id,
                case_type="false_negative",
                predicted=predicted,
                expected=case["expected"],
                confidence=0.5,
                failure_label=f"missed:{rule}",
                trajectory=trajectory,
            )
