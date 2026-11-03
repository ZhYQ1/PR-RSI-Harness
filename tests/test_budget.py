import time

import pytest

from src.harness.budget import BudgetExceeded, DualBudget


def test_step_budget_exhausted_raises():
    budget = DualBudget(max_steps=2, max_seconds=60)
    budget.consume_step()
    budget.consume_step()
    with pytest.raises(BudgetExceeded, match="steps"):
        budget.consume_step()


def test_time_budget_exhausted_raises():
    budget = DualBudget(max_steps=100, max_seconds=0.01)
    time.sleep(0.02)
    with pytest.raises(BudgetExceeded, match="time"):
        budget.check_time()


def test_remaining_counters():
    budget = DualBudget(max_steps=5, max_seconds=30)
    budget.consume_step()
    assert budget.steps_used == 1
    assert budget.steps_remaining == 4
