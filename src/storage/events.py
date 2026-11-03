from __future__ import annotations

import json
from typing import Any, Protocol


class EventBus(Protocol):
    def publish(self, event: dict[str, Any]) -> str: ...

    def read(self, last_id: str = "0", count: int = 100) -> list[tuple[str, dict[str, Any]]]: ...


def _seq(event_id: str) -> int:
    return int(str(event_id).split("-")[0])


class InMemoryEventBus:
    """Stream-like bus used as the offline fallback and in unit tests."""

    def __init__(self) -> None:
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._seq = 0

    def publish(self, event: dict[str, Any]) -> str:
        self._seq += 1
        event_id = f"{self._seq}-0"
        self._events.append((event_id, dict(event)))
        return event_id

    def read(self, last_id: str = "0", count: int = 100) -> list[tuple[str, dict[str, Any]]]:
        floor = _seq(last_id) if last_id not in {"", "$"} else 0
        selected = [(eid, e) for eid, e in self._events if _seq(eid) > floor]
        return selected[:count]


class RedisStreamBus:
    """Event bus backed by a Redis Stream (XADD/XRANGE)."""

    def __init__(self, url: str, stream: str = "harness:events") -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "redis is required for RedisStreamBus. "
                "Install it with: pip install 'pr-rsi-harness[redis]'"
            ) from exc
        self.stream = stream
        self._client = redis.Redis.from_url(url, decode_responses=True)

    def publish(self, event: dict[str, Any]) -> str:
        fields = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for key, value in event.items()
        }
        return str(self._client.xadd(self.stream, fields))

    def read(self, last_id: str = "0", count: int = 100) -> list[tuple[str, dict[str, Any]]]:
        start = last_id if last_id not in {"", "$"} else "-"
        entries = self._client.xrange(self.stream, min=start, max="+")
        if entries and entries[0][0] == last_id:
            entries = entries[1:]
        return [(eid, {k: _maybe_json(v) for k, v in fields}) for eid, fields in entries[:count]]


def _maybe_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
