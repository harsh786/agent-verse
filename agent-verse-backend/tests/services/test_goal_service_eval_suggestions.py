"""GoalService.get_eval_suggestions — the read side of the self-improvement UX:
real low-scoring dimensions → actionable suggestions, config-driven threshold,
honest empty when unevaluated or all pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-sugg", plan=PlanTier.ENTERPRISE, api_key_id="kid")


def _record(goal_id: str = "g1") -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="Ship the release",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX.tenant_id,
        priority="high",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        execution_context={},
    )


@pytest.mark.asyncio
async def test_suggestions_flag_low_dimensions_worst_first() -> None:
    svc = GoalService()
    svc._goals["g1"] = _record("g1")
    svc._eval_scores["g1"] = MagicMock(
        scores={"accuracy": 0.30, "safety": 0.95, "sla": 0.45, "coherence": 0.90}
    )

    result = await svc.get_eval_suggestions("g1", _CTX)

    assert result["status"] == "evaluated"
    assert isinstance(result["pass_threshold"], float)
    dims = [s["dimension"] for s in result["suggestions"]]
    # Only sub-threshold dimensions, worst-first; passing dims excluded.
    assert dims[0] == "accuracy"  # 0.30 is worst
    assert "sla" in dims
    assert "safety" not in dims and "coherence" not in dims
    for s in result["suggestions"]:
        assert s["suggestion"] and s["score"] < result["pass_threshold"]
    assert result["count"] == len(result["suggestions"])


@pytest.mark.asyncio
async def test_all_passing_yields_no_suggestions() -> None:
    svc = GoalService()
    svc._goals["g2"] = _record("g2")
    svc._eval_scores["g2"] = MagicMock(scores={"accuracy": 0.95, "safety": 0.99})
    result = await svc.get_eval_suggestions("g2", _CTX)
    assert result["status"] == "evaluated"
    assert result["suggestions"] == [] and result["count"] == 0


@pytest.mark.asyncio
async def test_unevaluated_goal_is_honest_empty() -> None:
    svc = GoalService()
    svc._goals["g3"] = _record("g3")  # no scorecard cached
    result = await svc.get_eval_suggestions("g3", _CTX)
    assert result["status"] == "not_evaluated"
    assert result["suggestions"] == [] and result["pass_threshold"] is None
