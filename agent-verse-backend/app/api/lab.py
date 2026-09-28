"""Agent Lab API — unified playground, simulation, and model comparison.

Only ``mode="simulation"`` is implemented (it runs the goal through the same
mock-tool :class:`~app.enterprise.simulation.SimulationRunner` as
``POST /enterprise/simulation``). ``comparison`` and ``live`` used to answer 200
with a note / a fake ``queued`` status while doing nothing; they now answer an
honest 501.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/lab", tags=["lab"])
_log = structlog.get_logger(__name__)


class LabRunRequest(BaseModel):
    goal: str
    agent_id: str | None = None
    mode: Literal["simulation", "live", "comparison"] = "simulation"
    models: list[str] | None = None  # for comparison mode
    mock_tools: dict[str, Any] | None = None


@router.post("/run")
async def lab_run(body: LabRunRequest, request: Request) -> dict[str, Any]:
    """Execute a goal in lab mode (simulation by default)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    app_state = request.app.state

    if body.mode == "comparison":
        raise HTTPException(
            status_code=501,
            detail="Lab model comparison is not implemented; submit one goal per model "
            "via POST /goals with a model override",
        )
    if body.mode == "live":
        raise HTTPException(
            status_code=501,
            detail="Lab live mode is not implemented; submit the goal via POST /goals",
        )

    sim_runner = getattr(app_state, "simulation_runner", None)
    if sim_runner is None:
        raise HTTPException(status_code=503, detail="Simulation runner unavailable")
    try:
        # SimulationRunner exposes start(); the old call to a non-existent run()
        # raised AttributeError, so every lab simulation was a 500.
        run = await sim_runner.start(
            goal=body.goal,
            mock_tools=body.mock_tools or {},
            tenant_ctx=tenant_ctx,
            app_state=app_state,
        )
    except Exception as exc:
        _log.error("lab_simulation_failed", error=str(exc)[:200])
        raise HTTPException(status_code=500, detail="Lab simulation failed") from exc
    return {
        "mode": "simulation",
        "run_id": run.run_id,
        "status": run.status,
        "used_real_llm": run.used_real_llm,
        "result": run.result,
    }


@router.get("/tools")
async def list_lab_tools(request: Request) -> dict[str, Any]:
    """List the caller's MCP tools available for lab mocking.

    This used to always return an empty list (discovery was a stub). 503 when
    the MCP client is wired but discovery fails, rather than an empty 200.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        return {"tools": [], "total": 0}
    try:
        raw_tools = await mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)
    except Exception as exc:
        _log.warning("lab_tool_discovery_failed", error=str(exc)[:200])
        raise HTTPException(status_code=503, detail="Tool discovery failed") from exc
    tools: list[dict[str, Any]] = []
    for t in list(raw_tools or [])[:200]:
        if isinstance(t, dict):
            tools.append(
                {
                    "name": str(t.get("name", "")),
                    "description": str(t.get("description", "")),
                    "server_id": str(t.get("server_id", "")),
                }
            )
        else:
            tools.append(
                {
                    "name": str(getattr(t, "name", t)),
                    "description": str(getattr(t, "description", "") or ""),
                    "server_id": str(getattr(t, "server_id", "") or ""),
                }
            )
    return {"tools": tools, "total": len(tools)}
