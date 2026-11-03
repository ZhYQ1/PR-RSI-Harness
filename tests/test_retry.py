import pytest

from src.harness.retry import RetryPolicy, UnrecoverableError, retry_with_backoff


def test_retries_recoverable_failures_then_succeeds():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = retry_with_backoff(op, RetryPolicy(max_attempts=3, base_delay=0, jitter=0))
    assert result == "ok"
    assert attempts["n"] == 3


def test_unrecoverable_error_is_not_retried():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise UnrecoverableError("fatal")

    with pytest.raises(UnrecoverableError):
        retry_with_backoff(op, RetryPolicy(max_attempts=5, base_delay=0, jitter=0))
    assert attempts["n"] == 1


def test_exhausts_attempts_then_raises():
    def op():
        raise RuntimeError("still failing")

    with pytest.raises(RuntimeError):
        retry_with_backoff(op, RetryPolicy(max_attempts=2, base_delay=0, jitter=0))


def test_backoff_delays_are_exponential():
    policy = RetryPolicy(max_attempts=5, base_delay=0.1, jitter=0)
    assert policy.delay_for(0) == pytest.approx(0.1)
    assert policy.delay_for(1) == pytest.approx(0.2)
    assert policy.delay_for(2) == pytest.approx(0.4)
