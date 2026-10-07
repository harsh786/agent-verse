"""Agent Runtime 2.0 API - execution plans, traces, subagents."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.agent_runtime.store import agent_runtime_store

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# Plans/traces live in agent_runtime_store: a bounded in-process LRU in front of
# Redis (tenant-namespaced keys), so other replicas can read them. They used to
# be unbounded module-level dicts (one plan + one trace per goal, never evicted).
#
# A goal-linked trace is a VIEW of the goal (a10-F237-01/03/06): its outcome
# (status / success / error / duration) comes from the goal record and its cost,
# tokens, per-role calls and model selections from goal_cost_breakdowns — both
# written by whichever API replica or Celery worker ran the goal. The stored
# shadow was only updated by the API process that ran the goal (never for a
# worker-run goal) and nothing ever set its cost fields. A trace id that the
# store no longer has (Redis TTL, outage, other replica) is resolved from the
# goal's persisted execution_context.


def _goal_service(request: Request) -> Any:
    svc = getattr(request.app.state, "goal_service", None)
    if svc is None:
        raise HTTPException(503, "Goal service unavailable")
    return svc


async def _require_goal(request: Request, tenant: Any, goal_id: str) -> None:
    """404 unless *goal_id* is one of the caller tenant's goals (empty = unlinked)."""
    if not goal_id:
        return
    from app.core.errors import NotFoundError

    try:
        await _goal_service(request).get_goal_outcome(goal_id, tenant)
    except NotFoundError as exc:
        raise HTTPException(404, "Goal not found") from exc


@router.post("/plans")
async def create_execution_plan(request: Request) -> dict[str, Any]:
    """Create a typed execution plan for a goal."""
    tenant = _require_tenant(request)
    body = await request.json()
    # A plan may only reference the caller's own goal (any goal_id was accepted).
    await _require_goal(request, tenant, str(body.get("goal_id") or ""))
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
    await agent_runtime_store.put_plan(plan)

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
    plan = await agent_runtime_store.get_plan(tenant.tenant_id, plan_id)
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
    await _require_goal(request, tenant, str(body.get("goal_id") or ""))
    from app.agent_runtime.models import AgentRunTrace

    trace_id = str(uuid.uuid4())
    trace = AgentRunTrace(
        trace_id=trace_id,
        goal_id=body.get("goal_id", ""),
        tenant_id=tenant.tenant_id,
    )
    await agent_runtime_store.put_trace(trace)
    return {"trace_id": trace_id, "status": "created"}


@router.get("/traces/{trace_id}")
async def get_run_trace(request: Request, trace_id: str) -> dict[str, Any]:
    """A run trace; a goal-linked one is derived from the goal's durable records."""
    tenant = _require_tenant(request)
    trace = await agent_runtime_store.get_trace(tenant.tenant_id, trace_id)
    if trace is not None and trace.tenant_id != tenant.tenant_id:
        trace = None
    goal_id = trace.goal_id if trace is not None else ""
    if trace is None:
        try:
            found = await _goal_service(request).find_goals_by_context(
                tenant, "agent_runtime_trace_id", trace_id, limit=1
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Trace store temporarily unavailable; retry") from exc
        if not found:
            raise HTTPException(404, "Trace not found")
        goal_id = str(found[0]["goal_id"])

    if not goal_id:
        # A trace created through POST /traces without a goal: what was stored.
        assert trace is not None
        return {
            "trace_id": trace.trace_id,
            "goal_id": "",
            "status": None,
            "total_cost_usd": trace.total_cost_usd,
            "total_tokens": trace.total_tokens,
            "duration_ms": trace.duration_ms,
            "success": trace.success,
            "error": trace.error,
            "role_calls": trace.role_calls[:50],
            "model_selections": trace.model_selections,
        }
    return {"trace_id": trace_id, **await _goal_trace_view(request, tenant, goal_id)}


def _parse_ts(value: Any) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        ts = value
    elif isinstance(value, str) and value:
        try:
            ts = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=datetime.UTC)


async def _goal_trace_view(request: Request, tenant: Any, goal_id: str) -> dict[str, Any]:
    """Outcome + cost of *goal_id* from its canonical records (404 / 503 honest)."""
    from app.core.errors import NotFoundError
    from app.observability.cost_breakdown import aget_breakdown

    try:
        outcome = await _goal_service(request).get_goal_outcome(goal_id, tenant)
    except NotFoundError as exc:
        raise HTTPException(404, "Trace not found") from exc
    try:
        breakdown = await aget_breakdown(goal_id, tenant_id=tenant.tenant_id)
    except Exception as exc:
        raise HTTPException(503, "Trace cost data temporarily unavailable; retry") from exc

    created = _parse_ts(outcome.get("created_at"))
    ended = _parse_ts(outcome.get("completed_at")) or datetime.datetime.now(datetime.UTC)
    duration_ms = max(0.0, (ended - created).total_seconds() * 1000) if created else 0.0
    entries = breakdown.entries
    role_calls = [
        {
            "role": e.role,
            "model": e.model,
            "calls": e.calls,
            "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens,
            "cost_usd": round(e.cost_usd, 6),
        }
        for e in entries
    ]
    selections: list[dict[str, Any]] = []
    for e in entries:
        pick = {"role": e.role, "model": e.model}
        if e.model and pick not in selections:
            selections.append(pick)
    status = outcome.get("status")
    return {
        "goal_id": goal_id,
        "status": status,
        "total_cost_usd": round(sum(e.cost_usd for e in entries), 6),
        "total_tokens": sum(e.input_tokens + e.output_tokens for e in entries),
        "duration_ms": duration_ms,
        "success": status == "complete",
        "error": outcome.get("failure_reason"),
        "role_calls": role_calls[:50],
        "model_selections": selections,
    }


# The execution modes POST /goals actually runs (``workflow_mode``; the value a
# goal's AgentExecutionPlan.strategy records). This list used to be static and
# advertised modes nothing runs (multi_agent_fanout, goal_tree) and named a
# flag (persistence) as a mode. GET /strategies is the strategy-runtime
# catalogue (``strategy_override``), with readiness and certification.
WORKFLOW_MODES: tuple[dict[str, str], ...] = (
    {
        "id": "single_agent",
        "name": "Single Agent",
        "description": "One agent plans, executes and verifies the goal",
    },
    {
        "id": "multi_agent",
        "name": "Multi-Agent",
        "description": "The same goal dispatched to several agents (agent_ids) in parallel",
    },
    {
        "id": "debate",
        "name": "Debate",
        "description": "Agents propose, critique and vote inside the goal (debate_rounds)",
    },
    {
        "id": "supervisor",
        "name": "Supervisor",
        "description": "A parent goal decomposes and delegates to sub-goals, then synthesizes",
    },
)


@router.get("/strategies")
async def list_strategies(request: Request) -> dict[str, Any]:
    """The goal execution modes (``workflow_mode``) POST /goals accepts."""
    _require_tenant(request)
    return {
        "strategies": [dict(m) for m in WORKFLOW_MODES],
        "flags": [
            {
                "id": "persistence_mode",
                "description": "Retry with backoff until the goal is achieved (any mode)",
            }
        ],
        "strategy_catalogue": "/strategies",
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
