from __future__ import annotations

from src.feedback.feedback import PRReviewFeedback
from src.feedback.rsi import RSIEngine


class ReviewFeedbackCollector:
    """Record structured feedback from real PR review executions into RSI.

    Real PRs have no ground-truth labels, so the expected risk set must come
    from an adjudication (operator / security reference). Feedback written
    here goes to the same ``feedback`` table as evaluation feedback, so the
    existing clustering and evolution loop drives off real-execution failures
    as well.
    """

    def __init__(self, rsi: RSIEngine) -> None:
        self.rsi = rsi

    def record(self, task_id: str, review_feedback: PRReviewFeedback | None) -> None:
        if review_feedback is None:
            return
        self.rsi.record_execution_feedback(review_feedback)
