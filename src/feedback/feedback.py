from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PRReviewFeedback:
    """Structured feedback collected from a real PR review execution.

    ``predicted`` is the risk rules the review agent flagged on this PR;
    ``expected`` is the ground-truth risk set for this PR (adjudicated by an
    operator or given by a security reference). ``failure_label`` and
    ``case_type`` are derived from the set difference so real-execution
    feedback clusters with evaluation feedback in :meth:`cluster_failures`.
    """

    task_id: str
    predicted: list[str]
    expected: list[str]
    confidence: float = 0.5
    trajectory: list[str] = field(default_factory=list)
