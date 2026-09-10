"""GET /goals/{id}/pattern-selection surfaces the chosen pattern (+ why) for the
frontend selection UX, and falls back to computing it on demand."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.state import GoalStatus
from app.api.goals import router as goals_router
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext("tenant-1", PlanTier.PROFESSIONAL, "key-1")


def _client(selection: dict[str, Any]) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == "valid" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(goals_router)
    svc = AsyncMock()
    svc.get_pattern_selection.return_value = selection
    app.state.goal_service = svc
    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_returns_service_pattern_selection() -> None:
    selection = {
        "goal_id": "g1",
        "status": "completed",
        "source": "auto",
        "primary_pattern": "react",
        "primary_pattern_name": "ReAct",
        "reasoning_patterns": ["react"],
        "multi_agent_patterns": ["single_agent"],
        "rationale": [{"pattern": "react", "why": "default", "category": "reasoning"}],
        "available_patterns": [],
    }
    resp = _client(selection).get("/goals/g1/pattern-selection", headers={"X-API-Key": "valid"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal_id"] == "g1"
    assert body["primary_pattern"] == "react"
    assert body["source"] == "auto"
    assert body["status"] == "completed"


async def test_service_layer_records_override_from_strategy_runtime() -> None:
    """The service reads the persisted pattern_selection (override wins)."""
    svc = GoalService()
    record = GoalRecord(
        goal_id="g2",
        goal_text="Summarize the onboarding guide",
        status=GoalStatus.PLANNING,
        tenant_id=TENANT.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-01-01T00:00:00Z",
        execution_context={"strategy_runtime": {"primary_strategy": "plan_execute"}},
    )
    svc._goals["g2"] = record

    body = await svc.get_pattern_selection("g2", TENANT)
    assert body["source"] == "override"
    assert body["primary_pattern"] == "plan_execute"


async def test_service_layer_falls_back_to_on_demand_summary() -> None:
    svc = GoalService()
    record = GoalRecord(
        goal_id="g3",
        goal_text="Summarize the quarterly report",
        status=GoalStatus.PLANNING,
        tenant_id=TENANT.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-01-01T00:00:00Z",
        execution_context={},
    )
    svc._goals["g3"] = record

    body = await svc.get_pattern_selection("g3", TENANT)
    assert body["primary_pattern"] == "react"
    ids = {p["id"] for p in body["available_patterns"]}
    assert {"supervisor", "debate", "consensus"} <= ids
