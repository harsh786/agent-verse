"""Per-tenant staging/sandbox environment.

Provides a sandboxed copy of the tenant's agent configuration where goals
run against mock tools (SimulationRunner) not production connectors.
"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/goals")
async def submit_sandbox_goal(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Submit a goal in sandbox mode — uses SimulationRunner, no real tools called."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(401, "Unauthorized")
    svc = getattr(request.app.state, "goal_service", None)
    if svc is None:
        raise HTTPException(503, "Goal service unavailable")
    result = await svc.submit_goal(
        goal=body.get("goal", ""),
        priority="low",
        dry_run=True,           # SimulationRunner path
        tenant_ctx=tenant,
        execution_context={
            "sandbox_mode": True,
            "mock_tools": True,
            "agent_id": body.get("agent_id"),
        },
    )
    return {**result, "sandbox": True,
            "message": "Goal ran in sandbox mode — no real tools were called"}


@router.get("/config")
async def get_sandbox_config(request: Request) -> dict[str, Any]:
    """Return sandbox configuration for the tenant."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(401, "Unauthorized")
    return {
        "sandbox_enabled": True,
        "mock_tools": True,
        "note": "Sandbox uses SimulationRunner — all tool calls return mock responses",
        "limitations": [
            "No real API calls made",
            "Cost is simulated",
            "Results are deterministic mock data",
        ],
    }
