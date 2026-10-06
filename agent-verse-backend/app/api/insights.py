"""Insights API — AI-powered analytics endpoints.

Endpoints:
    POST /insights/estimate   — pre-run cost & time estimator
    GET  /insights/graph/{goal_id}  — execution graph (nodes + edges)
    GET  /insights/analysis/{goal_id}  — failure analysis with suggestions
    POST /insights/query      — natural language goal/agent query
    GET  /insights/agent-health/{agent_id}  — 6-axis health radar data
    GET  /insights/benchmarks  — anonymized platform benchmarks

Fail-closed contract (estimate / agent-health / benchmarks / query): nothing here
invents numbers.

* Every statement is SQLAlchemy Core over the ORM tables, so a column that does not
  exist fails to *build* (and ``tests/api/test_insights_real_schema.py`` checks every
  referenced column against the ORM metadata). The old raw SQL read goal columns
  for cost, duration and a vector that do not exist; the error was swallowed and
  platform defaults (success probability 0.82, every health axis 0.7) came back
  as if they were measurements.
* Goal duration is ``completed_at - created_at``; cost is the durable per-goal
  ``goal_cost_breakdowns`` ledger. Similar goals are found by trigram similarity
  of ``goal_text`` (pg_trgm) — the goals table stores no vector.
* Tenant reads run under the tenant's RLS context. The cross-tenant benchmark
  aggregate runs on the maintenance (BYPASSRLS) session factory — on the tenant
  session FORCE RLS hid every other tenant, so it was always ``insufficient_data``.
  It returns only aggregates, and only when at least five tenants contribute.
* No database configured -> 501. Database error -> 503. No data -> ``null``.
"""

from __future__ import annotations

import math
import re
import statistics
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, case, extract, func, literal_column, select, true
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from app.db.models.goal import Goal, GoalEvent
from app.db.models.intelligence import Evaluation
from app.db.models.runtime_records import GoalCostBreakdownRow
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

logger = get_logger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

# Platform benchmark window: bound the cross-tenant scan to recent goals.
_BENCHMARK_WINDOW_DAYS = 90
_BENCHMARK_MIN_GOALS = 10
_BENCHMARK_MIN_TENANTS = 5
# Estimator: how far back, how many neighbours, and the minimum pg_trgm similarity
# for a past goal to count as "similar".
_ESTIMATE_WINDOW_DAYS = 180
_ESTIMATE_SAMPLE = 20
_ESTIMATE_MIN_SIMILARITY = 0.2

_goals = Goal.__table__
_events = GoalEvent.__table__
_evals = Evaluation.__table__
_costs = GoalCostBreakdownRow.__table__

_SUCCESS = ("complete", "completed")
_TERMINAL = (*_SUCCESS, "failed")


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


# ── SQL (SQLAlchemy Core over the ORM tables: a missing column fails to build) ─


def _tenant_uuid(tenant_id: str) -> uuid.UUID | None:
    """``goal_cost_breakdowns.tenant_id`` is a UUID; non-UUID tenants own no rows."""
    try:
        return uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        return None


def _duration_s() -> ColumnElement[Any]:
    """Goal wall-clock duration in seconds (NULL until the goal is terminal)."""
    return extract("epoch", _goals.c.completed_at - _goals.c.created_at)


def _goal_cost_subquery(tenant_id: str | None) -> Subquery:
    """Per-goal spend from the durable ledger (``tenant_id=None``: every tenant)."""
    q = select(
        _costs.c.goal_id.label("goal_id"), func.sum(_costs.c.cost_usd).label("goal_cost")
    ).group_by(_costs.c.goal_id)
    if tenant_id is not None:
        q = q.where(_costs.c.tenant_id == _tenant_uuid(tenant_id))
    return q.subquery("goal_cost")


def build_estimate_stmt(
    tenant_id: str,
    goal_text: str,
    agent_id: str | None,
    *,
    since: datetime | None = None,
) -> Select[Any]:
    """The tenant's finished goals most similar to *goal_text* (pg_trgm)."""
    since = since or datetime.now(UTC) - timedelta(days=_ESTIMATE_WINDOW_DAYS)
    sim = func.similarity(_goals.c.goal_text, goal_text)
    gc = _goal_cost_subquery(tenant_id)
    stmt = (
        select(
            _goals.c.status.label("status"),
            _goals.c.iterations.label("iterations"),
            _duration_s().label("duration_s"),
            gc.c.goal_cost.label("cost_usd"),
            sim.label("similarity"),
        )
        .select_from(_goals.outerjoin(gc, gc.c.goal_id == _goals.c.id))
        .where(
            _goals.c.tenant_id == tenant_id,
            _goals.c.status.in_(_TERMINAL),
            _goals.c.created_at >= since,
            sim >= _ESTIMATE_MIN_SIMILARITY,
        )
        .order_by(sim.desc(), _goals.c.created_at.desc())
        .limit(_ESTIMATE_SAMPLE)
    )
    if agent_id:
        stmt = stmt.where(_goals.c.agent_id == agent_id)
    return stmt


def build_agent_goal_summary_stmt(tenant_id: str, agent_id: str) -> Select[Any]:
    """Run counts, mean successful-run duration and mean per-goal cost of one agent."""
    gc = _goal_cost_subquery(tenant_id)
    return (
        select(
            func.count().label("total"),
            func.count().filter(_goals.c.status.in_(_SUCCESS)).label("completed"),
            func.count().filter(_goals.c.status.in_(_TERMINAL)).label("finished"),
            func.avg(_duration_s()).filter(_goals.c.status.in_(_SUCCESS)).label("avg_duration_s"),
            func.avg(gc.c.goal_cost).label("avg_cost_usd"),
        )
        .select_from(_goals.outerjoin(gc, gc.c.goal_id == _goals.c.id))
        .where(_goals.c.tenant_id == tenant_id, _goals.c.agent_id == agent_id)
    )


def build_agent_eval_summary_stmt(tenant_id: str, agent_id: str) -> Select[Any]:
    """Mean accuracy / coherence from the persisted scorecards of the agent's goals."""
    return (
        select(
            func.count().label("evaluated"),
            func.avg(_evals.c.scores["accuracy"].as_float()).label("avg_accuracy"),
            func.avg(_evals.c.scores["coherence"].as_float()).label("avg_coherence"),
        )
        .select_from(_evals.join(_goals, _goals.c.id == _evals.c.goal_id))
        .where(
            _evals.c.tenant_id == tenant_id,
            _goals.c.tenant_id == tenant_id,
            _goals.c.agent_id == agent_id,
        )
    )


def build_agent_tool_count_stmt(tenant_id: str, agent_id: str) -> Select[Any]:
    """Distinct tools the agent actually called (``tool_call_complete`` events)."""
    tool = _events.c.payload["tool"].as_string()
    return (
        select(func.count(func.distinct(tool)).label("unique_tools"))
        .select_from(_events.join(_goals, _goals.c.id == _events.c.goal_id))
        .where(
            _events.c.tenant_id == tenant_id,
            _events.c.event_type == "tool_call_complete",
            _goals.c.tenant_id == tenant_id,
            _goals.c.agent_id == agent_id,
        )
    )


def build_benchmark_stmt(since: datetime) -> Select[Any]:
    """Anonymised cross-tenant aggregate: goal-level means + per-tenant bands.

    Percentile bands are over *tenants* (each tenant's success rate / mean goal
    cost), which is what "where does my tenant sit" needs — a percentile over
    per-goal 0/1 outcomes is meaningless. Only aggregates leave the database.
    """
    gc = _goal_cost_subquery(None)
    # Literal constants (not binds): an all-parameter CASE would type as text.
    ok = case((_goals.c.status.in_(_SUCCESS), literal_column("1.0")), else_=literal_column("0.0"))
    per_goal = (
        select(
            _goals.c.tenant_id.label("tenant_id"),
            ok.label("ok"),
            gc.c.goal_cost.label("cost"),
            _duration_s().label("dur"),
            _goals.c.iterations.label("iters"),
        )
        .select_from(_goals.outerjoin(gc, gc.c.goal_id == _goals.c.id))
        .where(_goals.c.status.in_(_TERMINAL), _goals.c.created_at >= since)
        .cte("bench_goals")
    )
    per_tenant = (
        select(
            per_goal.c.tenant_id,
            func.avg(per_goal.c.ok).label("sr"),
            func.avg(per_goal.c.cost).label("cost"),
        )
        .group_by(per_goal.c.tenant_id)
        .cte("bench_tenants")
    )
    totals = select(
        func.count().label("total"),
        func.count(func.distinct(per_goal.c.tenant_id)).label("n_tenants"),
        func.avg(per_goal.c.ok).label("avg_success_rate"),
        func.avg(per_goal.c.cost).label("avg_cost_usd"),
        func.avg(per_goal.c.dur).label("avg_duration_s"),
        func.avg(per_goal.c.iters).label("avg_iterations"),
    ).subquery("bench_totals")

    def _pct(p: float, col: Any, label: str) -> Any:
        return func.percentile_cont(p).within_group(col).label(label)

    bands = select(
        *(_pct(p / 100, per_tenant.c.sr, f"p{p}_sr") for p in (25, 50, 75, 90)),
        *(_pct(p / 100, per_tenant.c.cost, f"p{p}_cost") for p in (10, 25, 50, 75, 90)),
    ).subquery("bench_bands")
    return select(*totals.c, *bands.c).select_from(totals.join(bands, true()))


def build_goal_query_stmt(
    tenant_id: str,
    *,
    since: datetime,
    status_filter: str | None,
    cost_min: float | None,
    limit: int,
) -> Select[Any]:
    """A tenant's goals with time/status/cost filters and LIMIT pushed into SQL."""
    gc = _goal_cost_subquery(tenant_id)
    stmt = (
        select(
            _goals.c.id,
            _goals.c.status,
            _goals.c.goal_text,
            _goals.c.priority,
            _goals.c.dry_run,
            _goals.c.agent_id,
            _goals.c.workflow_mode,
            _goals.c.created_at,
            gc.c.goal_cost.label("cost_usd"),
        )
        .select_from(_goals.outerjoin(gc, gc.c.goal_id == _goals.c.id))
        .where(_goals.c.tenant_id == tenant_id, _goals.c.created_at > since)
        .order_by(_goals.c.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(func.lower(_goals.c.status) == status_filter.lower())
    if cost_min is not None:
        stmt = stmt.where(gc.c.goal_cost >= cost_min)
    return stmt


# ── DB access (fail closed) ───────────────────────────────────────────────────


def _tenant_db(request: Request, what: str) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        goal_svc = getattr(request.app.state, "goal_service", None)
        db = getattr(goal_svc, "_db", None) if goal_svc is not None else None
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{what} is computed in Postgres and is not available without a database",
        )
    return db


async def _run_tenant(
    db: Any, tenant_id: str, what: str, *stmts: Select[Any]
) -> list[Sequence[Mapping[str, Any]]]:
    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            return [(await session.execute(s)).mappings().all() for s in stmts]
    except Exception as exc:
        logger.warning("insights_query_failed", what=what, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{what} unavailable: database query failed",
        ) from exc


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _band(values: list[float], *, digits: int | None) -> dict[str, float | int] | None:
    if not values:
        return None
    lo, mean, hi = min(values), statistics.mean(values), max(values)
    if digits is None:
        return {"min": int(lo), "mean": int(mean), "max": int(hi)}
    return {"min": round(lo, digits), "mean": round(mean, digits), "max": round(hi, digits)}


# ── Pre-run Cost & Time Estimator ────────────────────────────────────────────


class EstimateRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    agent_id: str | None = None


@router.post("/estimate")
async def estimate_goal(request: Request, body: EstimateRequest) -> dict[str, Any]:
    """Estimate cost, duration, iterations and success from the tenant's similar goals.

    Every figure is derived from finished goals whose text is similar to this one;
    with none, every estimate is ``null`` and ``confidence`` is ``"none"``.
    """
    tenant = _require_tenant(request)
    db = _tenant_db(request, "Goal estimate")
    stmt = build_estimate_stmt(tenant.tenant_id, body.goal, body.agent_id)
    (rows,) = await _run_tenant(db, tenant.tenant_id, "Goal estimate", stmt)

    n = len(rows)
    if n == 0:
        return {
            "estimated_cost_usd": None,
            "estimated_duration_s": None,
            "estimated_iterations": None,
            "success_probability": None,
            "similar_goals_count": 0,
            "confidence": "none",
            "based_on": "no_similar_history",
        }
    completed = sum(1 for r in rows if r["status"] in _SUCCESS)
    costs = [float(r["cost_usd"]) for r in rows if r["cost_usd"] is not None]
    durations = [float(r["duration_s"]) for r in rows if r["duration_s"] is not None]
    iters = [float(r["iterations"]) for r in rows if r["iterations"] is not None]
    return {
        "estimated_cost_usd": _band(costs, digits=4),
        "estimated_duration_s": _band(durations, digits=None),
        "estimated_iterations": _band(iters, digits=None),
        "success_probability": round(completed / n, 3),
        "similar_goals_count": n,
        "confidence": "high" if n >= 10 else "medium" if n >= 3 else "low",
        "based_on": "similar_tenant_goals",
    }


# ── Execution Graph ───────────────────────────────────────────────────────────


async def _load_goal[T](call: Awaitable[T], what: str) -> T:
    """Await a goal-service read: unknown goal -> 404, any other failure -> 503."""
    from app.core.errors import NotFoundError

    try:
        return await call
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("insights_goal_read_failed", what=what, error=str(exc)[:200])
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"{what} unavailable: goal store failed"
        ) from exc


@router.get("/graph/{goal_id}")
async def get_execution_graph(goal_id: str, request: Request) -> dict[str, Any]:
    """Return the goal execution as a graph of tool calls and data flows."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        raise HTTPException(503, "Goal service not available")

    # Load goal events.  get_events() has a DB fallback so it works even after
    # a server restart or when the goal was run by a Celery worker. An unknown
    # (or another tenant's) goal is a 404 and a store failure a 503: both used to
    # be swallowed into a 200 "start-only" graph that looked like an empty run.
    events: list[dict[str, Any]] = await _load_goal(
        goal_svc.get_events(goal_id=goal_id, tenant_ctx=tenant), "Execution graph"
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Build graph from events
    node_ids: set[str] = set()

    # Start node
    nodes.append({"id": "start", "type": "start", "label": "Start", "data": {}})
    node_ids.add("start")

    prev_id = "start"
    step_counter = 0
    tool_counter: dict[str, int] = {}

    for evt in events:
        evt_type = evt.get("type", "")
        # Support both payload-wrapped events (from DB) and flat events (from SSE)
        payload = evt.get("payload") or {}

        # ── Plan ready: create a step node per planned step ──────────────────
        if evt_type == "plan_ready":
            steps = evt.get("steps") or payload.get("steps") or []
            for step_label in steps[:20]:
                step_counter += 1
                node_id = f"plan_step_{step_counter}"
                nodes.append(
                    {
                        "id": node_id,
                        "type": "step",
                        "label": str(step_label)[:60],
                        "data": {"status": "planned", "description": str(step_label)},
                    }
                )
                edges.append({"id": f"e_{prev_id}_{node_id}", "source": prev_id, "target": node_id})
                prev_id = node_id

        # ── Individual step events ────────────────────────────────────────────
        elif evt_type in ("step_start", "step_started"):
            step_label = (
                evt.get("step")
                or payload.get("step")
                or payload.get("description")
                or f"Step {step_counter + 1}"
            )
            dedup_key = f"step__{str(step_label)[:40]}"
            if dedup_key not in node_ids:
                step_counter += 1
                node_id = f"step_{step_counter}"
                node_ids.add(dedup_key)
                nodes.append(
                    {
                        "id": node_id,
                        "type": "step",
                        "label": str(step_label)[:60],
                        "data": {"status": "running", "description": str(step_label)},
                    }
                )
                edges.append({"id": f"e_{prev_id}_{node_id}", "source": prev_id, "target": node_id})
                prev_id = node_id

        # ── Tool call events (actual event type is tool_call_complete) ────────
        elif evt_type in ("tool_call", "tool_result", "tool_call_complete", "tool_call_failed"):
            tool_name = (
                evt.get("tool_name")
                or evt.get("tool")
                or payload.get("tool_name")
                or payload.get("name")
                or "tool"
            )
            tool_counter[tool_name] = tool_counter.get(tool_name, 0) + 1
            node_id = (
                f"tool_{tool_name.replace('.', '_').replace('/', '_')}_{tool_counter[tool_name]}"
            )
            if node_id not in node_ids:
                success = evt.get("success", evt_type != "tool_call_failed")
                # Extract output preview (first 120 chars)
                raw_output = (
                    evt.get("output")
                    or evt.get("result")
                    or payload.get("output")
                    or payload.get("result")
                    or ""
                )
                output_preview = str(raw_output)[:120] if raw_output else ""
                nodes.append(
                    {
                        "id": node_id,
                        "type": "tool",
                        "label": str(tool_name)[:40],
                        "data": {
                            "tool_name": tool_name,
                            "server_id": evt.get("server_id") or payload.get("server_id"),
                            "status": "success" if success else "failed",
                            "output_preview": output_preview,
                            "duration_ms": evt.get("duration_ms"),
                            "error": evt.get("error") if not success else None,
                        },
                    }
                )
                node_ids.add(node_id)
                edges.append({"id": f"e_{prev_id}_{node_id}", "source": prev_id, "target": node_id})
                prev_id = node_id

        # ── Terminal events ───────────────────────────────────────────────────
        elif evt_type in (
            "goal_complete",
            "goal_failed",
            "goal_cancelled",
            "worker_complete",
            "worker_failed",
        ):
            end_id = "end"
            if end_id not in node_ids:
                label = (
                    "Complete"
                    if evt_type in ("goal_complete", "worker_complete")
                    else "Failed"
                    if evt_type in ("goal_failed", "worker_failed")
                    else "Cancelled"
                )
                nodes.append(
                    {
                        "id": end_id,
                        "type": "end" if label != "Failed" else "failed",
                        "label": label,
                        "data": {"status": evt_type},
                    }
                )
                node_ids.add(end_id)
            edges.append({"id": f"e_{prev_id}_end", "source": prev_id, "target": "end"})

    return {
        "goal_id": goal_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "tool_calls": sum(tool_counter.values()),
            "unique_tools": len(tool_counter),
        },
    }


# ── Failure Analysis ──────────────────────────────────────────────────────────


@router.get("/analysis/{goal_id}")
async def analyze_failure(goal_id: str, request: Request) -> dict[str, Any]:
    """Analyze a failed goal and return actionable suggestions."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        raise HTTPException(503, "Goal service not available")

    goal = await _load_goal(
        goal_svc.get_goal(goal_id=goal_id, tenant_ctx=tenant), "Failure analysis"
    )
    if not goal:
        raise HTTPException(404, "Goal not found")

    goal_text = goal.get("goal", "")
    status_val = goal.get("status", "")
    verification = goal.get("verification_feedback", "") or ""
    steps = goal.get("steps", []) or []

    # Build failure context for LLM analysis
    failure_context = f"Goal: {goal_text}\nStatus: {status_val}\n"
    if verification:
        failure_context += f"Failure reason: {verification}\n"
    if steps:
        last_step = steps[-1] if steps else {}
        failure_context += f"Last step: {last_step.get('description', '')}\n"
        failure_context += f"Last output: {str(last_step.get('output', ''))[:500]}\n"

    # Use the tenant's LLM (BYOK) or the platform one for the analysis
    from app.api.llm_access import tenant_llm_provider
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider = await tenant_llm_provider(request, tenant)
    suggestions: list[dict[str, str]] = []
    failure_reason = "Goal did not complete successfully."

    if provider is not None and verification:
        try:
            from app.providers.base import CompletionRequest, Message

            prompt = (
                f"An AI agent failed to complete a goal. Analyze and suggest fixes.\n\n"
                f"{failure_context}\n\n"
                "Provide:\n"
                "1. A 1-sentence failure_reason\n"
                "2. 3 specific, actionable suggestions in JSON array:\n"
                '[{"action": "short title", "description": "detail"}]\n\n'
                "Reply in this exact JSON format:\n"
                '{"failure_reason": "...", "suggestions": [...]}'
            )
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=500,
                ),
                role="insights_failure_analysis",
                tenant_ctx=tenant,
            )
            import json as _json

            parsed = _json.loads(resp.content.strip())
            failure_reason = parsed.get("failure_reason", failure_reason)
            suggestions = parsed.get("suggestions", [])[:5]
        except DecisionBudgetExceededError:
            raise  # 429 via the app's handler: a budget refusal is not an LLM outage
        except Exception:
            pass  # Fall back to heuristic suggestions

    # Heuristic fallback suggestions based on common failure patterns
    if not suggestions:
        text_lower = (verification + goal_text).lower()
        if "rate limit" in text_lower or "429" in text_lower:
            suggestions.append(
                {
                    "action": "Rate limit",
                    "description": "Add retry delays between API calls. Use exponential backoff.",
                }
            )
        if "timeout" in text_lower or "timed out" in text_lower:
            suggestions.append(
                {
                    "action": "Timeout",
                    "description": "Increase the SLA budget or break the goal into smaller sub-goals.",  # noqa: E501
                }
            )
        if "permission" in text_lower or "unauthorized" in text_lower or "403" in text_lower:
            suggestions.append(
                {
                    "action": "Permissions",
                    "description": "Check that the connector has the required OAuth scopes.",
                }
            )
        if "not found" in text_lower or "404" in text_lower:
            suggestions.append(
                {
                    "action": "Missing resource",
                    "description": "Verify the resource exists and the identifier is correct.",
                }
            )
        if not suggestions:
            suggestions = [
                {
                    "action": "Rephrase goal",
                    "description": "Try rephrasing with more specific instructions.",
                },
                {
                    "action": "Check connectors",
                    "description": "Verify all required MCP connectors are registered and authenticated.",  # noqa: E501
                },
                {
                    "action": "Dry run",
                    "description": "Use dry_run=true to test the plan without executing tools.",
                },
            ]

    return {
        "goal_id": goal_id,
        "goal": goal_text,
        "status": status_val,
        "failure_reason": failure_reason,
        "suggestions": suggestions,
        "iterations_used": goal.get("iterations", 0),
        "cost_usd": goal.get("cost_usd"),
    }


# ── Natural Language Query ────────────────────────────────────────────────────


# The LLM's ``days`` is model output: bound it before it reaches timedelta (a huge
# value raised OverflowError -> 500; a negative one put the cutoff in the future).
_QUERY_MAX_DAYS = 3650


def _clamp_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError):
        return 30
    return max(1, min(days, _QUERY_MAX_DAYS))


def _finite_or_none(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


class NLQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    entity: str = Field(default="goals", pattern="^(goals|agents|connectors)$")
    limit: int = Field(default=20, ge=1, le=100)


@router.post("/query")
async def natural_language_query(request: Request, body: NLQueryRequest) -> dict[str, Any]:
    """Parse a natural language query into filters and return matching results."""
    tenant = _require_tenant(request)
    query_lower = body.query.lower()

    # Defaults
    days: int = 30
    status_filter: str | None = None
    cost_min: float | None = None
    llm_parsed = False

    # ── Try LLM-powered parsing first (the tenant's LLM, BYOK first) ─────────
    from app.api.llm_access import tenant_llm_provider
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider = await tenant_llm_provider(request, tenant)
    if provider is not None:
        try:
            from app.providers.base import CompletionRequest, Message

            parse_prompt = (
                "Parse this natural language query about AI agent goals "
                "into structured filters.\n\n"
                f'Query: "{body.query}"\n\n'
                "Return ONLY valid JSON (no markdown) with these optional fields:\n"
                '{"days": 30, '
                '"status": "complete|failed|executing|planning|cancelled|null", '
                '"cost_min": null, "cost_max": null, "search": null}\n\n'
                "Examples:\n"
                '- "failed goals today" → {"days": 1, "status": "failed"}\n'
                '- "expensive goals over $1" → {"cost_min": 1.0}\n'
                '- "goals about deployment this week"'
                ' → {"days": 7, "search": "deploy"}\n'
            )
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[Message(role="user", content=parse_prompt)],
                    model="",
                    max_tokens=150,
                ),
                role="insights_nl_query",
                tenant_ctx=tenant,
            )
            import json as _json

            parsed = _json.loads(resp.content.strip())
            days = _clamp_days(parsed.get("days", 30))
            status_filter = parsed.get("status") or None
            cost_min = _finite_or_none(parsed["cost_min"]) if parsed.get("cost_min") else None
            llm_parsed = True
        except DecisionBudgetExceededError:
            raise  # 429 via the app's handler: a budget refusal is not an LLM outage
        except Exception:
            pass  # Fall back to regex parsing (the filters, not the results)

    # ── Regex/string fallback ─────────────────────────────────────────────────
    if not llm_parsed:
        if "today" in query_lower:
            days = 1
        elif "week" in query_lower or "7 day" in query_lower:
            days = 7
        elif "month" in query_lower or "30 day" in query_lower:
            days = 30
        elif "year" in query_lower:
            days = 365

        for s in ("failed", "complete", "executing", "planning", "cancelled"):
            if s in query_lower:
                status_filter = s
                break

        cost_match = re.search(
            r"cost(?:s?)?\s+(?:more|over|greater)\s+than\s+\$?([\d.]+)", query_lower
        )
        if cost_match:
            cost_min = _finite_or_none(cost_match.group(1))

    query_parsed = {
        "days": days,
        "status_filter": status_filter,
        "cost_min": cost_min,
        "entity": body.entity,
    }

    goal_svc = getattr(request.app.state, "goal_service", None)
    db = getattr(request.app.state, "db_session_factory", None) or (
        getattr(goal_svc, "_db", None) if goal_svc is not None else None
    )

    # DB path: filters + LIMIT in SQL, cost from the durable breakdown ledger.
    # A DB error is a 503 — never a silent switch to a partial in-memory view.
    if db is not None:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = build_goal_query_stmt(
            tenant.tenant_id,
            since=since,
            status_filter=status_filter,
            cost_min=cost_min,
            limit=body.limit,
        )
        (rows,) = await _run_tenant(db, tenant.tenant_id, "Goal query", stmt)
        results = [
            {
                "id": r["id"],
                "goal_id": r["id"],
                "status": r["status"],
                "goal": r["goal_text"],
                "priority": r["priority"],
                "dry_run": r["dry_run"],
                "agent_id": r["agent_id"],
                "workflow_mode": r["workflow_mode"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else "",
                "cost_usd": None if r["cost_usd"] is None else round(float(r["cost_usd"]), 6),
            }
            for r in rows
        ]
        return {"results": results, "total": len(results), "query_parsed": query_parsed}

    if goal_svc is None:
        return {"results": [], "total": 0, "query_parsed": query_parsed}

    # In-memory mode (no database configured): filter the service's own goals.
    try:
        resp = await goal_svc.list_goals(tenant_ctx=tenant)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Goal query unavailable: goal service failed",
        ) from exc
    all_goals = resp.get("goals", []) if isinstance(resp, dict) else (resp or [])

    cutoff = datetime.now(UTC) - timedelta(days=days)
    results = []
    for g in all_goals:
        created = g.get("created_at", "")
        if created:
            try:
                from dateutil.parser import parse as _parse

                if _parse(created).replace(tzinfo=UTC) < cutoff:
                    continue
            except Exception:
                pass
        if status_filter and g.get("status", "").lower() != status_filter:
            continue
        if cost_min is not None:
            g_cost = g.get("cost_usd")
            if g_cost is None or float(g_cost) < cost_min:
                continue
        results.append(g)

    results = results[: body.limit]
    return {
        "results": results,
        "total": len(results),
        "query_parsed": query_parsed,
    }


# ── Agent Health Radar ────────────────────────────────────────────────────────


@router.get("/agent-health/{agent_id}")
async def get_agent_health(agent_id: str, request: Request) -> dict[str, Any]:
    """Return 6-axis health radar data for a specific agent.

    Each axis is derived from the agent's real runs; an axis with no underlying
    data is ``null`` (never a neutral-looking default):

    * ``success_rate`` — completed / finished goals.
    * ``speed`` — ``1 / (1 + mean successful-run seconds / 60)`` (60 s -> 0.5).
    * ``cost_efficiency`` — ``1 / (1 + mean per-goal USD / 0.05)`` ($0.05 -> 0.5).
    * ``accuracy`` / ``coherence`` — mean of the persisted eval scorecards.
    * ``tool_coverage`` — distinct tools called / 10, capped at 1.0.
    """
    tenant = _require_tenant(request)
    tid = tenant.tenant_id
    db = _tenant_db(request, "Agent health")
    goals_rows, eval_rows, tool_rows = await _run_tenant(
        db,
        tid,
        "Agent health",
        build_agent_goal_summary_stmt(tid, agent_id),
        build_agent_eval_summary_stmt(tid, agent_id),
        build_agent_tool_count_stmt(tid, agent_id),
    )
    g = goals_rows[0] if goals_rows else {}
    e = eval_rows[0] if eval_rows else {}
    t = tool_rows[0] if tool_rows else {}

    total = int(g.get("total") or 0)
    finished = int(g.get("finished") or 0)
    evaluated = int(e.get("evaluated") or 0)
    unique_tools = int(t.get("unique_tools") or 0)
    avg_duration = _f(g.get("avg_duration_s"))
    avg_cost = _f(g.get("avg_cost_usd"))
    accuracy = _f(e.get("avg_accuracy")) if evaluated else None
    coherence = _f(e.get("avg_coherence")) if evaluated else None

    def _r(v: float | None) -> float | None:
        return None if v is None else round(max(0.0, min(1.0, v)), 3)

    health: dict[str, float | None] = {
        "speed": _r(None if avg_duration is None else 1.0 / (1.0 + avg_duration / 60.0)),
        "accuracy": _r(accuracy),
        "cost_efficiency": _r(None if avg_cost is None else 1.0 / (1.0 + avg_cost / 0.05)),
        "tool_coverage": _r(unique_tools / 10.0) if unique_tools else None,
        "success_rate": _r(int(g.get("completed") or 0) / finished) if finished else None,
        "coherence": _r(coherence),
    }
    return {
        "agent_id": agent_id,
        "health": health,
        "sample_size": total,
        "finished_count": finished,
        "eval_sample_size": evaluated,
    }


# ── Platform Benchmarks ───────────────────────────────────────────────────────


def _insufficient_benchmarks() -> dict[str, Any]:
    return {
        "platform_avg_success_rate": None,
        "platform_avg_cost_usd": None,
        "platform_avg_duration_s": None,
        "platform_avg_iterations": None,
        "top_10_pct_success_rate": None,
        "top_10_pct_cost_usd": None,
        "percentile_bands": {},
        "sample_count": 0,
        "data_source": "insufficient_data",
        "message": (
            f"Benchmarks need at least {_BENCHMARK_MIN_GOALS} finished goals from "
            f"{_BENCHMARK_MIN_TENANTS} or more tenants in the last "
            f"{_BENCHMARK_WINDOW_DAYS} days."
        ),
    }


def _round(v: Any, digits: int) -> float | None:
    return None if v is None else round(float(v), digits)


@router.get("/benchmarks")
async def get_benchmarks(request: Request) -> dict[str, Any]:
    """Anonymised platform-wide benchmarks, or all-null ``insufficient_data``."""
    _require_tenant(request)
    system_db = getattr(request.app.state, "system_db_session_factory", None)
    if system_db is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Platform benchmarks are computed in Postgres and need a database",
        )
    return await compute_platform_benchmarks(system_db)


async def compute_platform_benchmarks(system_db: Any) -> dict[str, Any]:
    """The platform benchmark figures (system session, k-anonymity); 503 on a DB error.

    Shared by ``/insights/benchmarks`` and the legacy ``/intelligence/benchmarks``
    so the two pages that show platform averages report the same numbers.
    """
    from app.db.rls import system_session

    since = datetime.now(UTC) - timedelta(days=_BENCHMARK_WINDOW_DAYS)
    try:
        async with system_db() as session, session.begin(), system_session(session):
            row = (await session.execute(build_benchmark_stmt(since))).mappings().one()
    except Exception as exc:
        logger.warning("insights_benchmarks_query_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform benchmarks unavailable: database query failed",
        ) from exc

    total = int(row.get("total") or 0)
    if total < _BENCHMARK_MIN_GOALS or int(row.get("n_tenants") or 0) < _BENCHMARK_MIN_TENANTS:
        return _insufficient_benchmarks()

    avg_duration = row.get("avg_duration_s")
    return {
        "platform_avg_success_rate": _round(row.get("avg_success_rate"), 3),
        "platform_avg_cost_usd": _round(row.get("avg_cost_usd"), 4),
        "platform_avg_duration_s": None if avg_duration is None else int(float(avg_duration)),
        "platform_avg_iterations": _round(row.get("avg_iterations"), 1),
        # Best decile of tenants: highest success rate, lowest mean goal cost.
        "top_10_pct_success_rate": _round(row.get("p90_sr"), 3),
        "top_10_pct_cost_usd": _round(row.get("p10_cost"), 4),
        "percentile_bands": {
            f"p{p}": {
                "success_rate": _round(row.get(f"p{p}_sr"), 3),
                "cost_usd": _round(row.get(f"p{p}_cost"), 4),
            }
            for p in (25, 50, 75, 90)
        },
        "sample_count": total,
        "data_source": "live_platform_data",
    }


async def compute_platform_eval_benchmarks(
    system_db: Any, dimensions: Sequence[str]
) -> dict[str, Any]:
    """Platform mean eval score and per-dimension means over the benchmark window.

    Same system session, window and k-anonymity guard as
    :func:`compute_platform_benchmarks`; ``None`` / ``{}`` when too few tenants
    contributed. A DB error is a 503.
    """
    from sqlalchemy import Float, cast

    from app.db.rls import system_session

    ev = Evaluation.__table__
    since = datetime.now(UTC) - timedelta(days=_BENCHMARK_WINDOW_DAYS)
    stmt = select(
        func.count(),
        func.count(func.distinct(ev.c.tenant_id)),
        func.avg(ev.c.average_score),
        *(func.avg(cast(ev.c.scores[d].as_string(), Float)) for d in dimensions),
    ).where(ev.c.created_at >= since)
    try:
        async with system_db() as session, session.begin(), system_session(session):
            row = (await session.execute(stmt)).fetchone()
    except Exception as exc:
        logger.warning("insights_eval_benchmarks_query_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform benchmarks unavailable: database query failed",
        ) from exc
    if (
        row is None
        or int(row[0] or 0) < _BENCHMARK_MIN_GOALS
        or int(row[1] or 0) < _BENCHMARK_MIN_TENANTS
        or row[2] is None
    ):
        return {"eval_score": None, "dims": {}}
    return {
        "eval_score": round(float(row[2]), 4),
        "dims": {
            d: round(float(v), 4)
            for d, v in zip(dimensions, row[3:], strict=False)
            if v is not None
        },
    }
