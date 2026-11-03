from src.agent.pipeline import ReviewPipeline
from src.harness.runtime import HarnessRuntime
from src.harness.state import TaskStatus
from src.memory.store import MemoryManager
from src.storage.sqlite_store import SQLiteStore

SQL_DIFF = '''
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 def get_user(user_id):
+    query = "SELECT * FROM users WHERE id=" + user_id
     return query
'''

CLEAN_DIFF = '''
diff --git a/util.py b/util.py
+def add(a, b):
+    return a + b
'''


def test_pipeline_finds_and_fixes_sql_injection(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    mem = MemoryManager(store)
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="acme/api",
        pr_number=9,
        branch="feat",
        diff=SQL_DIFF,
        pipeline=ReviewPipeline(memory=mem),
        tenant="acme",
    )
    assert outcome.task.status is TaskStatus.DONE
    assert outcome.result is not None
    assert any(f["rule"] == "sql_injection" for f in outcome.result.details["findings"])
    assert outcome.result.validated is True
    assert "SELECT" not in outcome.result.patch or "parameterized" in outcome.result.patch.lower() or "fix" in outcome.result.patch.lower()


def test_clean_pr_has_empty_findings(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="acme/api",
        pr_number=10,
        branch="feat",
        diff=CLEAN_DIFF,
        pipeline=ReviewPipeline(memory=MemoryManager(store)),
    )
    assert outcome.result is not None
    assert outcome.result.findings == []
    assert outcome.result.validated is True


def test_pipeline_writes_working_memory(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    mem = MemoryManager(store)
    runtime = HarnessRuntime(store)
    outcome = runtime.submit_and_run(
        repo="acme/api",
        pr_number=11,
        branch="feat",
        diff=SQL_DIFF,
        pipeline=ReviewPipeline(memory=mem),
        tenant="acme",
    )
    hits = mem.search(tenant="acme", repo="acme/api", query="sql_injection")
    assert hits
    assert outcome.task.id


class RecordingLLM:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def chat(self, messages):
        self.system_prompts.append(messages[0]["content"])
        return "{}"


def test_pipeline_uses_prompt_provider(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    llm = RecordingLLM()
    pipeline = ReviewPipeline(
        memory=MemoryManager(store),
        llm=llm,
        prompt_provider=lambda: "CUSTOM EVOLVED PROMPT",
    )
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="+pass")
    pipeline.run(task, None)
    assert llm.system_prompts == ["CUSTOM EVOLVED PROMPT"]


def test_critic_feedback_roundtrips_through_memory(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    mem = MemoryManager(store)

    class NoPatchFixer:
        def run(self, verified):
            return ""

    pipeline = ReviewPipeline(memory=mem)
    pipeline.fixer = NoPatchFixer()
    task = store.create_task(repo="r", pr_number=1, branch="m", diff='+query = "SELECT" + x')
    result = pipeline.run(task, None)
    # findings exist but the fixer produced no patch -> critic opinions stored
    assert result.validated is False
    assert result.details["critic"]
    notes = mem.search(tenant="default", repo="r", query="critic")
    assert notes
    # a fresh pipeline run in the same tenant/repo pulls the critic notes into context
    class ContextLLM:
        def __init__(self) -> None:
            self.user_contents: list[str] = []

        def chat(self, messages):
            self.user_contents.append(messages[1]["content"])
            return "{}"

    context_llm = ContextLLM()
    second = ReviewPipeline(memory=mem, llm=context_llm)
    second.run(task, None)
    assert any("patch is empty" in content for content in context_llm.user_contents)
