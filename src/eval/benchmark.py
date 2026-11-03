from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parents[2] / "data" / "benchmark.json"

HIGH_RISK_RULES = {
    "sql_injection",
    "hardcoded_secret",
    "command_injection",
    "ssrf",
    "insecure_deserialization",
}

SNIPPETS = {
    "sql_injection": '+query = "SELECT * FROM users WHERE id=" + user_id',
    "hardcoded_secret": '+API_KEY = "sk-live-1234567890abcdef"',
    "command_injection": "+os.system('ls ' + user_input)",
    "path_traversal": "+open(base + user_path)",
    "xss": "+el.innerHTML = comment",
    "weak_crypto": "+digest = hashlib.md5(password).hexdigest()",
    "ssrf": "+requests.get(request.args['url'])",
    "insecure_deserialization": "+obj = pickle.loads(body)",
    "clean": "def add(a, b):\n    return a + b",
}


def _case(idx: int, rule: str) -> dict[str, Any]:
    repo = f"tenant/repo-{idx % 10}"
    snippet = SNIPPETS[rule]
    added = "\n".join("+" + line for line in snippet.splitlines())
    filename = "app.py" if rule != "clean" else "util.py"
    diff = f"diff --git a/{filename} b/{filename}\n--- a/{filename}\n+++ b/{filename}\n@@ -1,2 +1,3 @@\n{added}\n"
    expected = [] if rule == "clean" else [rule]
    high = [rule] if rule in HIGH_RISK_RULES else []
    return {
        "id": f"case-{idx:03d}",
        "repo": repo,
        "pr_number": idx + 1,
        "branch": "eval",
        "rule": rule,
        "diff": diff,
        "expected": expected,
        "high_risk": high,
        "split": None,
    }


def generate_benchmark() -> list[dict[str, Any]]:
    rules = [
        "sql_injection",
        "hardcoded_secret",
        "command_injection",
        "path_traversal",
        "xss",
        "weak_crypto",
        "ssrf",
        "insecure_deserialization",
        "clean",
        "clean",
    ]
    cases = [_case(i, rules[i % len(rules)]) for i in range(100)]
    return cases


def split_by_repo(
    cases: list[dict[str, Any]], holdout_repos: set[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repos = sorted({c["repo"] for c in cases})
    if holdout_repos is None:
        cut = max(1, int(len(repos) * 0.3))
        holdout_repos = set(repos[-cut:])
    validation = [dict(c, split="validation") for c in cases if c["repo"] not in holdout_repos]
    holdout = [dict(c, split="holdout") for c in cases if c["repo"] in holdout_repos]
    return validation, holdout


def ensure_benchmark(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    cases = generate_benchmark()
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return cases
