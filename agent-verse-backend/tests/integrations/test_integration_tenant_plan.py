"""TRG-06: integration and token-webhook goals run on the tenant's real plan.

Slack / Zapier / Alertmanager / Datadog hard-coded PROFESSIONAL and typed
webhooks read the plan from this replica's in-memory tenant dict, so an
enterprise tenant was throttled and a free tenant got professional limits.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier
from tests.tenancy.test_plan_resolver import plan_db


def _app(plans: dict[str, str]) -> tuple[TestClient, Any]:
    app = FastAPI()
    app.include_router(router)
    goal_service = create_autospec(GoalService, instance=True)
    goal_service.submit_goal.return_value = {"goal_id": "g-1"}
    app.state.goal_service = goal_service
    app.state.db_session_factory = plan_db(plans)
    return TestClient(app), goal_service


def _prefix(suffix: str) -> str:
    for r in router.routes:
        path = getattr(r, "path", "")
        if path.endswith(suffix):
            return path
    raise AssertionError(f"{suffix} route not found")


def test_enterprise_tenant_alertmanager_goal_runs_as_enterprise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALERTMANAGER_WEBHOOK_TOKEN", "am-token")
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "t-ent")
    client, goal_service = _app({"t-ent": "enterprise"})

    r = client.post(
        _prefix("/events/alertmanager"),
        json={"alerts": [{"status": "firing", "labels": {"alertname": "HighCPU"}}]},
        headers={"Authorization": "Bearer am-token"},
    )

    assert r.status_code == 200, r.text
    ctx = goal_service.submit_goal.await_args.kwargs["tenant_ctx"]
    assert ctx.plan is PlanTier.ENTERPRISE


def test_free_tenant_alertmanager_goal_runs_as_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTMANAGER_WEBHOOK_TOKEN", "am-token")
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "t-free")
    client, goal_service = _app({"t-free": "free"})

    r = client.post(
        _prefix("/events/alertmanager"),
        json={"alerts": [{"status": "firing", "labels": {"alertname": "HighCPU"}}]},
        headers={"Authorization": "Bearer am-token"},
    )

    assert r.status_code == 200, r.text
    ctx = goal_service.submit_goal.await_args.kwargs["tenant_ctx"]
    assert ctx.plan is PlanTier.FREE


async def test_typed_webhook_ctx_reads_tenant_record() -> None:
    """A tenant created on another replica is absent from this replica's
    in-memory dict; its deliveries used to run as FREE."""
    from app.api.triggers import _webhook_tenant_ctx

    state = SimpleNamespace(
        tenant_service=SimpleNamespace(_tenants={}),
        db_session_factory=plan_db({"t-pro": "professional"}),
    )
    request: Any = SimpleNamespace(app=SimpleNamespace(state=state))

    ctx = await _webhook_tenant_ctx(request, "t-pro")

    assert ctx.plan is PlanTier.PROFESSIONAL
    assert ctx.roles == ()
