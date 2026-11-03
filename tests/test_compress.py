from src.memory.compress import compress_diff


LARGE = "\n".join(
    [
        "diff --git a/safe.py b/safe.py",
        *[f"+print({i})" for i in range(200)],
        "diff --git a/auth.py b/auth.py",
        '+password = "hunter2"',
        "+token = request.headers['Authorization']",
    ]
)


def test_keeps_high_risk_hunks_when_over_budget():
    compact, meta = compress_diff(LARGE, token_budget=80)
    assert "hunter2" in compact
    assert meta["kept_high_risk"] >= 1
    assert meta["tokens"] <= 80


def test_does_not_drop_small_diffs():
    text = "diff --git a/a.py b/a.py\n+x = 1\n"
    compact, meta = compress_diff(text, token_budget=1000)
    assert compact == text
    assert meta["truncated"] is False
