"""CORE-16: queued (worker) goals record strategy certification evidence.

Only in-process goals called StrategyEvidenceRecorder; the worker persisted
``strategy_execution`` but never recorded evidence, so production certification
stayed empty and the catalogue under-reported real runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.scaling.test_worker_runtime_profile import _profile, _run, worker  # noqa: F401


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from app.orchestration.strategy_evidence import StrategyEvidenceRecorder

    calls: list[dict[str, Any]] = []

    async def _record_run(self: Any, **kwargs: Any) -> list[Any]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(StrategyEvidenceRecorder, "record_run", _record_run)
    return calls


def test_worker_v2_goal_records_evidence_for_what_ran(
    worker: dict[str, Any],  # noqa: F811
    recorded: list[dict[str, Any]],
) -> None:
    worker["with_context"](
        {"runtime_profile": _profile("self_refine").to_dict(), "strategy_runtime_path": "v2"}
    )

    result = _run()

    assert result["status"] == "complete", result
    assert len(recorded) == 1
    call = recorded[0]
    assert call["tenant_id"] == "t-prof"
    assert call["goal_id"] == "g-prof"
    assert "self_refine" in list(call["strategy_ids"])
    assert call["succeeded"] is True
    assert call["runtime_path"] == "v2"


def test_worker_dry_run_records_no_evidence(
    worker: dict[str, Any],  # noqa: F811
    recorded: list[dict[str, Any]],
) -> None:
    from app.scaling import tasks

    worker["with_context"](
        {"runtime_profile": _profile("self_refine").to_dict(), "strategy_runtime_path": "v2"}
    )
    tasks.run_goal.run("g-prof", "t-prof", "write a report", "normal", True)

    assert recorded == []
