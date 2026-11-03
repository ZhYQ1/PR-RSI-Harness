from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


class UnrecoverableError(RuntimeError):
    """Errors that must not be retried."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.1
    jitter: float = 0.0
    multiplier: float = 2.0

    def delay_for(self, attempt_index: int) -> float:
        delay = self.base_delay * (self.multiplier**attempt_index)
        if self.jitter:
            delay += random.uniform(0, self.jitter)
        return delay


def retry_with_backoff(operation: Callable[[], T], policy: RetryPolicy) -> T:
    last_error: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except UnrecoverableError:
            raise
        except Exception as exc:  # noqa: BLE001 — retryable by policy
            last_error = exc
            if attempt >= policy.max_attempts - 1:
                break
            time.sleep(policy.delay_for(attempt))
    assert last_error is not None
    raise last_error
