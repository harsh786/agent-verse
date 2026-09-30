"""CORE-20: worker bookkeeping is explicit instead of silently blank."""

from __future__ import annotations

from typing import Any

import pytest

from tests.scaling.test_worker_runtime_profile import _run, worker  # noqa: F401


def test_unreadable_execution_context_records_a_downgrade(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    async def _boom(goal_id: str, tenant_id: str) -> dict[str, Any]:
        raise RuntimeError("db read failed")

    monkeypatch.setattr(tasks, "_goal_execution_context", _boom)

    result = _run()

    assert result["status"] == "complete", result
    downgrades = worker["merged"]["strategy_execution"].get("downgrades") or []
    assert any(d.get("reason") == "profile_unreadable" for d in downgrades), downgrades


def test_recreated_goal_row_is_marked_not_blank(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.goal_service import GoalService

    captured: dict[str, Any] = {}

    async def _ensure(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(GoalService, "_db_ensure_goal_row", _ensure)
    worker["with_context"]({})

    _run()

    ctx = captured["execution_context"]
    assert ctx, "a recreated row must not carry a blank execution_context"
    assert ctx.get("execution_context_source") == "worker_recreated_row"
