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
    goal = str(body.get("goal", "") or "")
    mock_tools = body.get("mock_tools")
    mock_tools = mock_tools if isinstance(mock_tools, dict) else {}

    # Preferred path: actually RUN the goal against mock tools via the enterprise
    # SimulationRunner (full AgentGraph with a MockMCPClient). This endpoint used to
    # answer "Goal ran in sandbox mode" while submitting dry_run=True — nothing ran,
    # and the sandbox_mode/mock_tools flags it set were read by nothing.
    runner = getattr(request.app.state, "simulation_runner", None)
    if runner is not None and goal:
        try:
            run = await runner.start(
                goal=goal,
                mock_tools=mock_tools,
                tenant_ctx=tenant,
                app_state=request.app.state,
            )
        except Exception as exc:
            raise HTTPException(502, f"Sandbox run failed: {exc}") from exc
        steps = list(getattr(run, "steps_executed", []) or [])
        return {
            "run_id": run.run_id,
            "status": run.status,
            "sandbox": True,
            "mode": "mock_tool_run",
            "executed": True,
            "steps_executed": steps,
            "tools_called": list(getattr(run, "tools_called", []) or []),
            "mock_tools_used": list(getattr(run, "mock_tools_used", []) or []),
            "used_real_llm": bool(getattr(run, "used_real_llm", False)),
            "result": getattr(run, "result", None),
            "message": (
                f"Goal ran in the sandbox against mock tools ({len(steps)} step(s)); "
                "no real tools were called."
            ),
        }

    # Fallback: no simulation runner wired — be honest that this is only a dry-run
    # validation (plan preview), not an execution.
    svc = getattr(request.app.state, "goal_service", None)
    if svc is None:
        raise HTTPException(503, "Goal service unavailable")
    result = await svc.submit_goal(
        goal=goal,
        priority="low",
        dry_run=True,
        tenant_ctx=tenant,
        execution_context={
            "sandbox_mode": True,
            "mock_tools": True,
            "agent_id": body.get("agent_id"),
        },
    )
    return {
        **result,
        "sandbox": True,
        "mode": "dry_run_validation",
        "executed": False,
        "message": (
            "Sandbox runner unavailable: the goal was validated as a dry run only "
            "(plan preview) — it did not run and no real tools were called."
        ),
    }


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
