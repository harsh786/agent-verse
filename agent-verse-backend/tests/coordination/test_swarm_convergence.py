"""Behavioral tests for bounded deterministic swarm convergence evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.coordination.swarm.convergence import ConvergenceItem, evaluate_convergence


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=1)


def _within_budget() -> dict[str, object]:
    return {"spent": Decimal("1"), "budget": Decimal("10"), "repeated_results": 0}


def test_convergence_true_only_when_criteria_met_and_mandatory_complete() -> None:
    decision = evaluate_convergence(
        (
            ConvergenceItem(work_item_id="m", mandatory=True, state="completed", result_digest="x"),
            ConvergenceItem(work_item_id="opt", mandatory=False, state="pending"),
        ),
        criteria_met=True,
        deadline=_future(),
        **_within_budget(),
    )
    # Optional work still pending does not block convergence.
    assert decision.converged and decision.terminal
    assert decision.reason == "objective_satisfied"


def test_convergence_reports_work_remaining_when_criteria_unmet() -> None:
    decision = evaluate_convergence(
        (ConvergenceItem(work_item_id="m", mandatory=True, state="completed"),),
        criteria_met=False,
        deadline=_future(),
        **_within_budget(),
    )
    assert not decision.converged and not decision.terminal
    assert decision.reason == "work_remaining"


def test_convergence_pending_mandatory_is_not_converged() -> None:
    decision = evaluate_convergence(
        (ConvergenceItem(work_item_id="m", mandatory=True, state="claimed"),),
        criteria_met=True,
        deadline=_future(),
        **_within_budget(),
    )
    # criteria_met but a mandatory item is not yet completed -> keep going.
    assert not decision.converged and not decision.terminal
    assert decision.reason == "work_remaining"


def test_convergence_terminates_on_failed_mandatory_item() -> None:
    decision = evaluate_convergence(
        (
            ConvergenceItem(work_item_id="m", mandatory=True, state="failed"),
            ConvergenceItem(work_item_id="opt", mandatory=False, state="failed"),
        ),
        criteria_met=True,
        deadline=_future(),
        **_within_budget(),
    )
    assert decision.terminal and not decision.converged
    assert decision.reason == "mandatory_failed"


def test_convergence_terminates_on_deadline_and_no_progress() -> None:
    items = (ConvergenceItem(work_item_id="m", mandatory=True, state="completed"),)
    past_deadline = evaluate_convergence(
        items,
        criteria_met=True,
        spent=Decimal("1"),
        budget=Decimal("10"),
        deadline=datetime.now(UTC) - timedelta(minutes=1),
        repeated_results=0,
    )
    assert past_deadline.terminal and past_deadline.reason == "deadline_exceeded"
    stalled = evaluate_convergence(
        items,
        criteria_met=False,
        spent=Decimal("1"),
        budget=Decimal("10"),
        deadline=_future(),
        repeated_results=3,
    )
    assert stalled.terminal and stalled.reason == "no_progress"


def test_convergence_budget_check_precedes_all_other_reasons() -> None:
    # Over budget AND past deadline: budget is evaluated first.
    decision = evaluate_convergence(
        (ConvergenceItem(work_item_id="m", mandatory=True, state="failed"),),
        criteria_met=False,
        spent=Decimal("11"),
        budget=Decimal("10"),
        deadline=datetime.now(UTC) - timedelta(minutes=1),
        repeated_results=5,
    )
    assert decision.reason == "budget_exceeded"
