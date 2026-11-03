from src.agent.agents import CriticFeedback, ResultValidator, SecureFixer
from src.memory.context import ContextAssembler
from src.memory.store import MemoryManager
from src.storage.sqlite_store import SQLiteStore

DIFF = """
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
+query = "SELECT * FROM users WHERE id=" + user_id
+def add(a, b):
+    return a + b
"""


def test_high_risk_diff_survives_tiny_budget():
    assembler = ContextAssembler()
    context, meta = assembler.assemble(DIFF, token_budget=40)
    assert "[diff]" in context
    assert "SELECT" in context
    assert meta["tokens"] <= 40


def test_low_priority_sections_truncated_first():
    big_history = "\n".join(f"- past pr {i} with clean result" for i in range(200))
    store = SQLiteStore(":memory:")
    memory = MemoryManager(store)
    memory.put("scenario", "t", "r", big_history)
    assembler = ContextAssembler(memory)
    context, meta = assembler.assemble(
        DIFF, token_budget=120, tenant="t", repo="r"
    )
    assert "SELECT" in context  # diff kept
    assert meta["truncated"], "expected some section to be truncated"
    assert meta["truncated"][-1] in {"tools", "history"}


def test_critic_feedback_included_before_tools():
    assembler = ContextAssembler()
    context, meta = assembler.assemble(
        DIFF,
        critic_feedback=["patch missing fixes for: sql_injection"],
        token_budget=1000,
    )
    assert "patch missing fixes" in context
    assert context.index("[critic]") < context.index("[tools]")
    assert "diff" in meta["sections"]


def test_no_memory_still_assembles():
    assembler = ContextAssembler(memory=None)
    context, meta = assembler.assemble(DIFF, token_budget=2000)
    assert "[diff]" in context
    assert "[tools]" in context
    assert context.count("[history]") == 0


def test_validator_returns_structured_critic_feedback():
    from src.agent.agents import VerifiedFinding

    validator = ResultValidator()
    finding = VerifiedFinding(
        rule="sql_injection", severity="high", evidence="e", verified=True, reason="r"
    )
    ok = validator.run([finding], patch="# fix sql_injection: use parameterized query")
    assert ok.valid is True
    assert ok.opinions == []

    bad = validator.run([finding], patch="")
    assert bad.valid is False
    assert bad.opinions


def test_tool_guide_lists_all_rules():
    guide = "\n".join(f"- {rule}: {advice}" for rule, advice in SecureFixer.PATCHES.items())
    assert "sql_injection" in guide and "ssrf" in guide


def test_assembler_budget_respected_with_all_sections():
    store = SQLiteStore(":memory:")
    memory = MemoryManager(store)
    memory.put("scenario", "t", "r", "- historical observation one")
    assembler = ContextAssembler(memory)
    context, meta = assembler.assemble(
        DIFF,
        critic_feedback=["keep parameterized queries"],
        token_budget=150,
        tenant="t",
        repo="r",
    )
    assert meta["tokens"] <= 150
    assert "keep parameterized queries" in context
    assert "historical observation" in context
