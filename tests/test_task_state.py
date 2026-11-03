"""Task state machine for Runtime Harness."""

import pytest

from src.harness.state import ALLOWED_TRANSITIONS, TaskStatus, transition


def test_happy_path_reaches_done():
    status = TaskStatus.PENDING
    for nxt in (
        TaskStatus.RUNNING,
        TaskStatus.CHECKPOINT,
        TaskStatus.VERIFYING,
        TaskStatus.FIXING,
        TaskStatus.VALIDATING,
        TaskStatus.DONE,
    ):
        status = transition(status, nxt)
    assert status is TaskStatus.DONE


def test_running_may_enter_dead_letter():
    status = transition(TaskStatus.RUNNING, TaskStatus.DEAD_LETTER)
    assert status is TaskStatus.DEAD_LETTER


def test_budget_exceeded_from_running():
    status = transition(TaskStatus.RUNNING, TaskStatus.BUDGET_EXCEEDED)
    assert status is TaskStatus.BUDGET_EXCEEDED


def test_illegal_transition_is_rejected():
    with pytest.raises(ValueError, match="illegal transition"):
        transition(TaskStatus.PENDING, TaskStatus.DONE)


def test_terminal_states_have_no_successors():
    for terminal in (TaskStatus.DONE, TaskStatus.DEAD_LETTER, TaskStatus.BUDGET_EXCEEDED):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
