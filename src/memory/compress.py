from __future__ import annotations

import re
from typing import Any

HIGH_RISK = re.compile(
    r"password|secret|token|api[_-]?key|SELECT|subprocess|pickle|os\.system|innerHTML",
    re.I,
)


def _tokens(text: str) -> int:
    return max(1, len(text.split()))


def _split_hunks(diff: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            parts.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        parts.append("".join(current))
    return parts or [diff]


def compress_diff(diff: str, token_budget: int) -> tuple[str, dict[str, Any]]:
    if _tokens(diff) <= token_budget:
        return diff, {"tokens": _tokens(diff), "truncated": False, "kept_high_risk": 0}

    hunks = _split_hunks(diff)
    ranked = sorted(hunks, key=lambda h: (0 if HIGH_RISK.search(h) else 1, -_tokens(h)))
    kept: list[str] = []
    used = 0
    kept_high = 0
    for hunk in ranked:
        cost = _tokens(hunk)
        if used + cost > token_budget and kept:
            continue
        if used + cost > token_budget:
            words = hunk.split()
            hunk = " ".join(words[:token_budget])
            cost = _tokens(hunk)
        kept.append(hunk)
        used += cost
        if HIGH_RISK.search(hunk):
            kept_high += 1
        if used >= token_budget:
            break
    compact = "\n".join(kept)
    return compact, {
        "tokens": _tokens(compact),
        "truncated": True,
        "kept_high_risk": kept_high,
    }
