from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    f1: float
    high_risk_recall: float
    clean_pr_accuracy: float


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def compute_metrics(cases: list[dict]) -> Metrics:
    tp = fp = fn = 0
    high_tp = high_fn = 0
    clean_correct = clean_total = 0
    for case in cases:
        predicted = set(case.get("predicted") or [])
        expected = set(case.get("expected") or [])
        high = set(case.get("high_risk") or expected)
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
        high_tp += len(predicted & high)
        high_fn += len(high - predicted)
        if not expected:
            clean_total += 1
            if not predicted:
                clean_correct += 1
    _, _, f1 = _prf(tp, fp, fn)
    high_recall = high_tp / (high_tp + high_fn) if high_tp + high_fn else 1.0
    clean_acc = clean_correct / clean_total if clean_total else 1.0
    return Metrics(f1=f1, high_risk_recall=high_recall, clean_pr_accuracy=clean_acc)
