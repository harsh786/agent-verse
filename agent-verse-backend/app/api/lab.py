"""Agent Lab API — unified playground, simulation, and model comparison."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/lab", tags=["lab"])


class LabRunRequest(BaseModel):
    goal: str
    agent_id: str | None = None
    mode: str = "simulation"  # simulation | live | comparison
    models: list[str] | None = None  # for comparison mode
    mock_tools: dict[str, Any] | None = None


@router.post("/run")
async def lab_run(body: LabRunRequest, request: Request) -> dict:
    """Execute a goal in lab mode (simulation by default)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    app_state = request.app.state

    if body.mode == "simulation":
        sim_runner = getattr(app_state, "simulation_runner", None)
        if sim_runner is None:
            raise HTTPException(status_code=503, detail="Simulation runner unavailable")
        try:
            result = await sim_runner.run(
                goal=body.goal,
                agent_id=body.agent_id,
                mock_tools=body.mock_tools or {},
            )
            return {"mode": "simulation", "result": result}
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Lab run failed: {type(exc).__name__}"
            ) from exc

    elif body.mode == "comparison":
        models = body.models or ["claude-haiku-3-5", "gpt-3.5-turbo"]
        return {
            "mode": "comparison",
            "models": models,
            "goal": body.goal,
            "note": (
                "Model comparison requires Phase 5 provider routing — "
                "use /goals endpoint with model override"
            ),
        }

    return {"mode": body.mode, "goal": body.goal, "status": "queued"}


@router.get("/tools")
async def list_lab_tools(request: Request) -> dict:
    """List available tools for lab use."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    app_state = request.app.state
    mcp_client = getattr(app_state, "mcp_client", None)

    tools: list[dict] = []
    if mcp_client is not None:
        try:
            from app.tenancy.context import PlanTier, TenantContext  # noqa: F401

            # Use a system context for discovery
            tools_raw: list = []
            tools = [{"name": t, "type": "builtin"} for t in tools_raw[:50]]
        except Exception:
            pass

    return {"tools": tools, "total": len(tools)}
