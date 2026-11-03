import os

import pytest

from src.storage.factory import open_store
from src.storage.pg_store import PostgresStore
from src.storage.sqlite_store import SQLiteStore

DSN = os.environ.get("TEST_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="TEST_PG_DSN not set; PostgreSQL integration tests disabled"
)


@pytest.fixture()
def store():
    pg = PostgresStore(DSN)
    pg.execute("DELETE FROM trace")
    pg.execute("DELETE FROM checkpoint")
    pg.execute("DELETE FROM dead_letter")
    pg.execute("DELETE FROM pr_task")
    return pg


def test_factory_selects_postgres_backend():
    assert isinstance(open_store(DSN), PostgresStore)
    assert isinstance(open_store("data/x.db"), SQLiteStore)


def test_create_and_get_task_roundtrip(store):
    task = store.create_task(repo="acme/api", pr_number=7, branch="feat", diff="d")
    loaded = store.get_task(task.id)
    assert loaded.id == task.id
    assert loaded.repo == "acme/api"
    assert loaded.status.value == "PENDING"


def test_checkpoint_upsert_is_idempotent(store):
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.save_checkpoint(task.id, {"phase": "FIXING"})
    store.save_checkpoint(task.id, {"phase": "VALIDATING"})
    assert store.load_checkpoint(task.id) == {"phase": "VALIDATING"}


def test_autoincrement_and_row_access(store):
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.append_trace(task.id, 1, "agent", "reason", {"a": 1})
    store.append_trace(task.id, 2, "agent", "act", {"b": 2})
    events = store.list_traces(task.id)
    assert [e.id for e in events] == sorted(e.id for e in events)
    assert len(events) == 2
    assert events[1].payload == {"b": 2}


def test_set_status_transition_and_dead_letter(store):
    from src.harness.state import TaskStatus

    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.set_status(task.id, TaskStatus.RUNNING)
    letter = store.enqueue_dead_letter(task.id, reason="unrecoverable", detail="boom")
    assert letter.reason == "unrecoverable"
    assert store.get_task(task.id).status is TaskStatus.DEAD_LETTER
    assert store.get_task(task.id).end_reason == "unrecoverable"


def test_placeholder_translation_with_external_sql(store):
    cur = store.execute(
        "INSERT INTO memory_record(memory_type, tenant, repo, content, created_at, archived) "
        "VALUES(?,?,?,?,?,0) RETURNING id",
        ("working", "t1", "r1", "hello", "2026-01-01"),
    )
    row = cur.fetchone()
    assert row["id"] > 0
    count = store.execute(
        "SELECT COUNT(*) AS n FROM memory_record WHERE tenant=?", ("t1",)
    ).fetchone()
    assert count["n"] == 1
