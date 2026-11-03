from src.memory.store import MemoryManager


def test_layers_and_tenant_isolation(tmp_path):
    mem = MemoryManager.from_sqlite(tmp_path / "h.db")
    mem.put("working", tenant="t1", repo="r1", content="w1", task_id="a")
    mem.put("scenario", tenant="t1", repo="r1", content="s1", task_id="a")
    mem.put("semantic", tenant="t1", repo="r1", content="rule: no secrets")
    mem.put("working", tenant="t2", repo="r1", content="other", task_id="b")

    hits = mem.search(tenant="t1", repo="r1", query="secrets")
    texts = [h.content for h in hits]
    assert "rule: no secrets" in texts
    assert "other" not in texts


def test_expired_records_are_cleaned(tmp_path):
    mem = MemoryManager.from_sqlite(tmp_path / "h.db")
    mem.put("working", tenant="t", repo="r", content="old", expires_in_seconds=-1)
    removed = mem.cleanup_expired()
    assert removed == 1
    assert mem.search(tenant="t", repo="r", query="old") == []


def test_archive_hides_task_memory(tmp_path):
    mem = MemoryManager.from_sqlite(tmp_path / "h.db")
    mem.put("working", tenant="t", repo="r", content="task-note", task_id="tid")
    mem.archive_task("tid")
    assert mem.search(tenant="t", repo="r", query="task-note") == []
