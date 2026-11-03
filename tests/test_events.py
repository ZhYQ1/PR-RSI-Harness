import os

import pytest

from src.harness.state import TaskStatus
from src.storage.events import InMemoryEventBus, RedisStreamBus
from src.storage.sqlite_store import SQLiteStore


class BrokenBus:
    def publish(self, event):
        raise RuntimeError("broker down")

    def read(self, last_id="0", count=100):
        return []


def test_inmemory_bus_roundtrip_and_filtering():
    bus = InMemoryEventBus()
    first = bus.publish({"type": "status", "task_id": "t1"})
    bus.publish({"type": "trace", "task_id": "t1"})
    bus.publish({"type": "status", "task_id": "t2"})

    assert first == "1-0"
    all_events = bus.read()
    assert len(all_events) == 3

    after_first = bus.read(last_id=first)
    assert len(after_first) == 2

    limited = bus.read(last_id="0", count=1)
    assert len(limited) == 1
    assert limited[0][1]["task_id"] == "t1"


def test_store_publishes_status_and_trace_events(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    bus = InMemoryEventBus()
    store.attach_bus(bus)

    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.set_status(task.id, TaskStatus.RUNNING)
    store.append_trace(task.id, 1, "agent", "reason", {"thought": "x"})

    events = [event for _, event in bus.read()]
    types = [event["type"] for event in events]
    assert "status" in types
    assert "trace" in types
    status_event = next(event for event in events if event["type"] == "status")
    assert status_event["status"] == "RUNNING"
    trace_event = next(event for event in events if event["type"] == "trace")
    assert trace_event["payload"] == {"thought": "x"}


def test_publish_failure_does_not_break_writes(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    store.attach_bus(BrokenBus())

    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.set_status(task.id, TaskStatus.RUNNING, reason="still works")
    assert store.get_task(task.id).status is TaskStatus.RUNNING


def test_no_bus_attached_is_silent(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    task = store.create_task(repo="r", pr_number=1, branch="m", diff="d")
    store.set_status(task.id, TaskStatus.RUNNING)
    assert store.get_task(task.id).status is TaskStatus.RUNNING


@pytest.mark.skipif(
    not os.environ.get("REDIS_URL"), reason="REDIS_URL not set; Redis integration disabled"
)
def test_redis_stream_roundtrip():
    bus = RedisStreamBus(os.environ["REDIS_URL"], stream="harness:events:test")
    bus._client.delete(bus.stream)
    bus.publish({"type": "status", "task_id": "t9", "status": "RUNNING", "reason": None})
    bus.publish({"type": "trace", "task_id": "t9", "payload": {"k": 1}})

    events = bus.read()
    assert len(events) == 2
    assert events[0][1]["type"] == "status"
    assert events[1][1]["payload"] == {"k": 1}

    tail = bus.read(last_id=events[0][0])
    assert len(tail) == 1
    bus._client.delete(bus.stream)
