"""VerifierCalibrationStore: bounded buffer and goal-keyed outcome (MEM-32)."""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence.verifier_calibration import VerifierCalibrationStore


async def test_record_buffer_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    store = VerifierCalibrationStore(max_records=10)
    for i in range(25):
        await store.record_verdict(goal_id=f"g{i}", tenant_id="t", verifier_verdict=True)
    assert len(store._records) == 10
    assert store._records[0]["goal_id"] == "g15"


async def test_outcome_by_goal_updates_the_latest_verdict_in_memory() -> None:
    store = VerifierCalibrationStore()
    await store.record_verdict(goal_id="g", tenant_id="t", verifier_verdict=False, iteration=1)
    await store.record_verdict(goal_id="g", tenant_id="t", verifier_verdict=True, iteration=2)
    await store.record_verdict(goal_id="g", tenant_id="other", verifier_verdict=True)
    assert await store.record_actual_outcome_by_goal(
        goal_id="g", tenant_id="t", actual_success=False
    ) == 1
    outcomes = [(r["tenant_id"], r["iteration"], r["actual_outcome"]) for r in store._records]
    assert outcomes == [("t", 1, None), ("t", 2, False), ("other", 1, None)]
    assert store.false_confirm_rate("t")["false_positives"] == 1


class _FailingDB:
    def __call__(self) -> Any:
        raise ConnectionError("db down")


async def test_db_failure_is_raised_to_the_caller_not_swallowed() -> None:
    store = VerifierCalibrationStore(_FailingDB())
    with pytest.raises(ConnectionError):
        await store.record_actual_outcome_by_goal(goal_id="g", tenant_id="t", actual_success=True)


def test_feedback_route_updates_calibration_by_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feedback route no longer scans a module singleton's buffer."""
    import inspect

    from app.api import goals

    src = inspect.getsource(goals.submit_goal_feedback)
    assert "record_actual_outcome_by_goal" in src
    assert "._records" not in src
