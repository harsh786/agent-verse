"""Agent Runtime 2.0 API - execution plans, traces, subagents."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# In-memory trace store (production: DB)
_traces: dict[str, Any] = {}
_plans: dict[str, Any] = {}


@router.post("/plans")
async def create_execution_plan(request: Request) -> dict[str, Any]:
    """Create a typed execution plan for a goal."""
    tenant = _require_tenant(request)
    body = await request.json()
    from app.agent_runtime.models import AgentExecutionPlan, AgentRole, PlanStep, RiskLevel

    now = datetime.datetime.now(datetime.UTC).isoformat()
    plan_id = str(uuid.uuid4())

    steps = []
    for i, s in enumerate(body.get("steps", [])):
        try:
            role = AgentRole(s.get("role", "executor"))
        except ValueError:
            role = AgentRole.EXECUTOR
        try:
            risk = RiskLevel(s.get("risk_level", "low"))
        except ValueError:
            risk = RiskLevel.LOW

        steps.append(
            PlanStep(
                step_id=f"step_{i + 1}",
                description=str(s.get("description", "")),
                role=role,
                dependencies=s.get("dependencies", []),
                risk_level=risk,
                expected_evidence=s.get("expected_evidence", []),
                output_contract=s.get("output_contract", {}),
                tools_required=s.get("tools_required", []),
            )
        )

    plan = AgentExecutionPlan(
        plan_id=plan_id,
        goal_id=body.get("goal_id", ""),
        tenant_id=tenant.tenant_id,
        goal_text=body.get("goal_text", ""),
        strategy=body.get("strategy", "single_agent"),
        steps=steps,
        created_at=now,
        agent_id=body.get("agent_id"),
    )
    _plans[plan_id] = plan

    return {
        "plan_id": plan_id,
        "goal_id": plan.goal_id,
        "strategy": plan.strategy,
        "step_count": len(steps),
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "role": s.role.value,
                "risk_level": s.risk_level.value,
                "dependencies": s.dependencies,
            }
            for s in steps
        ],
    }


@router.get("/plans/{plan_id}")
async def get_execution_plan(request: Request, plan_id: str) -> dict[str, Any]:
    """Get a specific execution plan."""
    tenant = _require_tenant(request)
    plan = _plans.get(plan_id)
    if not plan or plan.tenant_id != tenant.tenant_id:
        raise HTTPException(404, "Plan not found")

    return {
        "plan_id": plan.plan_id,
        "goal_id": plan.goal_id,
        "strategy": plan.strategy,
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "role": s.role.value,
                "status": s.status.value,
            }
            for s in plan.steps
        ],
    }


@router.post("/traces")
async def create_run_trace(request: Request) -> dict[str, Any]:
    """Create a run trace for tracking agent execution."""
    tenant = _require_tenant(request)
    body = await request.json()
    from app.agent_runtime.models import AgentRunTrace

    trace_id = str(uuid.uuid4())
    trace = AgentRunTrace(
        trace_id=trace_id,
        goal_id=body.get("goal_id", ""),
        tenant_id=tenant.tenant_id,
    )
    _traces[trace_id] = trace
    return {"trace_id": trace_id, "status": "created"}


@router.get("/traces/{trace_id}")
async def get_run_trace(request: Request, trace_id: str) -> dict[str, Any]:
    """Get a run trace."""
    tenant = _require_tenant(request)
    trace = _traces.get(trace_id)
    if not trace or trace.tenant_id != tenant.tenant_id:
        raise HTTPException(404, "Trace not found")

    return {
        "trace_id": trace.trace_id,
        "goal_id": trace.goal_id,
        "total_cost_usd": trace.total_cost_usd,
        "total_tokens": trace.total_tokens,
        "duration_ms": trace.duration_ms,
        "success": trace.success,
        "role_calls": trace.role_calls[:50],
        "model_selections": trace.model_selections,
    }


@router.get("/strategies")
async def list_strategies(request: Request) -> dict[str, Any]:
    """List available agent execution strategies."""
    _require_tenant(request)
    return {
        "strategies": [
            {
                "id": "single_agent",
                "name": "Single Agent",
                "description": "One agent handles the entire goal",
            },
            {
                "id": "multi_agent_fanout",
                "name": "Multi-Agent Fanout",
                "description": "Multiple agents work on parallel subtasks",
            },
            {
                "id": "debate",
                "name": "Debate",
                "description": "Agents debate to reach consensus",
            },
            {
                "id": "supervisor",
                "name": "Supervisor",
                "description": "Supervisor decomposes and delegates to subagents",
            },
            {
                "id": "goal_tree",
                "name": "Goal Tree",
                "description": "Recursive goal decomposition",
            },
            {
                "id": "persistence",
                "name": "Persistence",
                "description": "Retry with backoff until goal achieved",
            },
        ]
    }


@router.get("/roles")
async def list_agent_roles(request: Request) -> dict[str, Any]:
    """List formal agent roles with descriptions."""
    _require_tenant(request)
    return {
        "roles": [
            {
                "id": "planner",
                "name": "Planner",
                "description": "Decomposes the goal into a typed execution plan",
            },
            {
                "id": "executor",
                "name": "Executor",
                "description": "Executes plan steps by calling tools and producing output",
            },
            {
                "id": "verifier",
                "name": "Verifier",
                "description": "Verifies step output against expected evidence",
            },
            {
                "id": "critic",
                "name": "Critic",
                "description": "Reviews the plan for gaps, risks, and completeness",
            },
            {
                "id": "judge",
                "name": "Judge",
                "description": "Evaluates final output quality on multiple dimensions",
            },
            {
                "id": "reflector",
                "name": "Reflector",
                "description": "Learns from failures and updates memory",
            },
            {
                "id": "synthesizer",
                "name": "Synthesizer",
                "description": "Combines multi-agent outputs into a coherent answer",
            },
            {
                "id": "subagent",
                "name": "Subagent",
                "description": "Handles a specific subtask under a supervisor",
            },
        ]
    }
