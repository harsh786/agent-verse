"""Goal eval scoring must pass the real LLM provider to the eval runner.

``GoalService`` holds the Starlette *app* as ``_app_state``; the provider lives
on ``app.state._app_provider``. The scoring path read
``getattr(self._app_state, "_app_provider", None)`` — the app object, not its
state — so the provider was always ``None`` and the LLM judge (coherence /
accuracy) never ran: every goal was scored by heuristics only.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.agent.state import GoalStatus
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.intelligence.eval_runner import EvalRunner
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_llm_judge_used_when_app_has_provider() -> None:
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    record = GoalRecord(
        goal_id="g1",
        goal_text="Summarise the report",
        status=GoalStatus.EXECUTING,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    record.events.append({"type": "plan_ready", "steps": ["read report", "write summary"]})
    record.events.append({"type": "verification_done", "success": True})
    svc._goals["g1"] = record

    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(content="0.9"))

    app = FastAPI()  # the real object GoalService is given in create_app
    app.state.eval_runner = EvalRunner()
    app.state._app_provider = provider
    svc._app_state = app

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()):
        await svc._dispatch_event("g1", {"type": "goal_complete"}, tenant_ctx=ctx)

    assert "g1" in svc._eval_scores
    provider.complete.assert_awaited()  # LLM judge path actually ran
