"""Insights API — AI-powered analytics endpoints.

Endpoints:
    POST /insights/estimate   — pre-run cost & time estimator
    GET  /insights/graph/{goal_id}  — execution graph (nodes + edges)
    GET  /insights/analysis/{goal_id}  — failure analysis with suggestions
    POST /insights/query      — natural language goal/agent query
    GET  /insights/agent-health/{agent_id}  — 6-axis health radar data
    GET  /insights/benchmarks  — anonymized platform benchmarks
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/insights", tags=["insights"])


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


# ── Pre-run Cost & Time Estimator ────────────────────────────────────────────


class EstimateRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    agent_id: str | None = None


@router.post("/estimate")
async def estimate_goal(request: Request, body: EstimateRequest) -> dict[str, Any]:
    """Estimate cost, duration, and success probability based on similar historical goals."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    embedder = getattr(request.app.state, "embedder", None)

    # Default estimate (no history available)
    result: dict[str, Any] = {
        "estimated_cost_usd": {"min": 0.01, "mean": 0.05, "max": 0.20},
        "estimated_duration_s": {"min": 15, "mean": 45, "max": 120},
        "estimated_iterations": {"min": 1, "mean": 3, "max": 8},
        "success_probability": 0.82,
        "similar_goals_count": 0,
        "confidence": "low",
        "based_on": "platform_defaults",
    }

    # Try to find similar historical goals via embedding similarity
    db_factory = getattr(goal_svc, "_db", None) if goal_svc else None
    if db_factory is not None and embedder is not None:
        try:
            # Embed the goal text
            embedding = await embedder.embed(body.goal)
            if embedding:
                from sqlalchemy import text as _t

                async with db_factory() as session:
                    await session.execute(
                        _t("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant.tenant_id},
                    )
                    # Use pgvector cosine similarity (<=> operator) when available;
                    # fall back to recency-ordered query if pgvector is absent.
                    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
                    try:
                        rows = (
                            await session.execute(
                                _t("""
                                SELECT cost_usd, duration_s, iterations, status,
                                       1 - (embedding <=> :vec::vector) AS similarity
                                FROM goals
                                WHERE tenant_id = :tid
                                  AND status IN ('complete', 'failed')
                                  AND cost_usd IS NOT NULL
                                  AND embedding IS NOT NULL
                                ORDER BY embedding <=> :vec::vector
                                LIMIT 20
                            """),
                                {"tid": tenant.tenant_id, "vec": vector_str},
                            )
                        ).fetchall()
                    except Exception:
                        # pgvector extension unavailable or no embedding column
                        rows = (
                            await session.execute(
                                _t("""
                                SELECT cost_usd, duration_s, iterations, status,
                                       1.0 AS similarity
                                FROM goals
                                WHERE tenant_id = :tid
                                  AND status IN ('complete', 'failed')
                                  AND cost_usd IS NOT NULL
                                ORDER BY created_at DESC
                                LIMIT 50
                            """),
                                {"tid": tenant.tenant_id},
                            )
                        ).fetchall()

                    if rows:
                        completed = [r for r in rows if r[3] == "complete"]
                        all_costs = [float(r[0]) for r in rows if r[0] is not None]
                        all_durations = [float(r[1]) for r in rows if r[1] is not None]
                        all_iters = [int(r[2]) for r in rows if r[2] is not None]
                        success_rate = len(completed) / len(rows) if rows else 0.82

                        if all_costs:
                            import statistics

                            result = {
                                "estimated_cost_usd": {
                                    "min": round(min(all_costs), 4),
                                    "mean": round(statistics.mean(all_costs), 4),
                                    "max": round(max(all_costs), 4),
                                },
                                "estimated_duration_s": {
                                    "min": int(min(all_durations)) if all_durations else 15,
                                    "mean": (
                                        int(statistics.mean(all_durations)) if all_durations else 45
                                    ),
                                    "max": int(max(all_durations)) if all_durations else 120,
                                },
                                "estimated_iterations": {
                                    "min": int(min(all_iters)) if all_iters else 1,
                                    "mean": int(statistics.mean(all_iters)) if all_iters else 3,
                                    "max": int(max(all_iters)) if all_iters else 8,
                                },
                                "success_probability": round(success_rate, 3),
                                "similar_goals_count": len(rows),
                                "confidence": (
                                    "high"
                                    if len(rows) >= 10
                                    else "medium"
                                    if len(rows) >= 3
                                    else "low"
                                ),
                                "based_on": "historical_data",
                            }
        except Exception:
            pass  # Return defaults on any error

    return result


# ── Execution Graph ───────────────────────────────────────────────────────────


@router.get("/graph/{goal_id}")
async def get_execution_graph(goal_id: str, request: Request) -> dict[str, Any]:
    """Return the goal execution as a graph of tool calls and data flows."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        raise HTTPException(503, "Goal service not available")

    # Load goal events.  get_events() has a DB fallback so it works even after
    # a server restart or when the goal was run by a Celery worker.
    try:
        events: list[dict[str, Any]] = await goal_svc.get_events(goal_id=goal_id, tenant_ctx=tenant)
    except Exception:
        events = []

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

    try:
        goal = await goal_svc.get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except Exception as _b904_exc:
        raise HTTPException(404, "Goal not found") from _b904_exc

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

    # Use LLM for intelligent analysis if available
    provider = getattr(request.app.state, "_app_provider", None)
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
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=500,
                )
            )
            import json as _json

            parsed = _json.loads(resp.content.strip())
            failure_reason = parsed.get("failure_reason", failure_reason)
            suggestions = parsed.get("suggestions", [])[:5]
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
        "cost_usd": goal.get("cost_usd", 0),
    }


# ── Natural Language Query ────────────────────────────────────────────────────


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

    # ── Try LLM-powered parsing first ────────────────────────────────────────
    provider = getattr(request.app.state, "_app_provider", None)
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
                '- "failed goals today" \u2192 {"days": 1, "status": "failed"}\n'
                '- "expensive goals over $1" \u2192 {"cost_min": 1.0}\n'
                '- "goals about deployment this week"'
                ' \u2192 {"days": 7, "search": "deploy"}\n'
            )
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=parse_prompt)],
                    model="",
                    max_tokens=150,
                )
            )
            import json as _json

            parsed = _json.loads(resp.content.strip())
            days = int(parsed.get("days", 30))
            status_filter = parsed.get("status") or None
            cost_min = float(parsed["cost_min"]) if parsed.get("cost_min") else None
            llm_parsed = True
        except Exception:
            pass  # Fall back to regex on any failure

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
            cost_min = float(cost_match.group(1))

    # Execute query
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is None:
        return {"results": [], "total": 0, "query_parsed": {}}

    try:
        resp = await goal_svc.list_goals(tenant_ctx=tenant)
        all_goals = resp.get("goals", []) if isinstance(resp, dict) else (resp or [])
    except Exception:
        all_goals = []

    # Apply filters
    import datetime as _dt

    cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)
    results = []
    for g in all_goals:
        # Time filter
        created = g.get("created_at", "")
        if created:
            try:
                from dateutil.parser import parse as _parse

                if _parse(created).replace(tzinfo=_dt.UTC) < cutoff:
                    continue
            except Exception:
                pass
        # Status filter
        if status_filter and g.get("status", "").lower() != status_filter:
            continue
        # Cost filter
        if cost_min is not None:
            g_cost = float(g.get("cost_usd", 0) or 0)
            if g_cost < cost_min:
                continue
        results.append(g)

    results = results[: body.limit]
    return {
        "results": results,
        "total": len(results),
        "query_parsed": {
            "days": days,
            "status_filter": status_filter,
            "cost_min": cost_min,
            "entity": body.entity,
        },
    }


# ── Agent Health Radar ────────────────────────────────────────────────────────


@router.get("/agent-health/{agent_id}")
async def get_agent_health(agent_id: str, request: Request) -> dict[str, Any]:
    """Return 6-axis health radar data for a specific agent."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    # Default health values
    health: dict[str, float] = {
        "speed": 0.7,
        "accuracy": 0.7,
        "cost_efficiency": 0.7,
        "tool_coverage": 0.7,
        "success_rate": 0.7,
        "coherence": 0.7,
    }
    sample_size = 0

    if goal_svc is None:
        return {"agent_id": agent_id, "health": health, "sample_size": sample_size}

    db_factory = getattr(goal_svc, "_db", None)
    if db_factory is not None:
        try:
            from sqlalchemy import text as _t

            async with db_factory() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant.tenant_id}
                )

                # Query goals for this specific agent
                goals_row = (
                    await session.execute(
                        _t("""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as completed,
                            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                            AVG(COALESCE(cost_usd, 0)) as avg_cost,
                            AVG(COALESCE(duration_s, 0)) as avg_duration,
                            AVG(COALESCE(iterations, 0)) as avg_iterations
                        FROM goals
                        WHERE tenant_id = :tid
                          AND agent_id = :aid
                    """),
                        {"tid": tenant.tenant_id, "aid": agent_id},
                    )
                ).fetchone()

                if goals_row and goals_row[0] and int(goals_row[0]) > 0:
                    total = int(goals_row[0])
                    completed = int(goals_row[1] or 0)
                    avg_cost = float(goals_row[3] or 0)
                    avg_duration = float(goals_row[4] or 0)

                    success_rate = completed / total if total > 0 else 0.0

                    # Speed: inverse of avg_duration (faster = higher score)
                    # Normalize: 0s = 1.0, 60s = 0.5, 300s = 0.1
                    speed = (
                        max(0.0, min(1.0, 1.0 / (1.0 + avg_duration / 60.0)))
                        if avg_duration > 0
                        else 0.7
                    )

                    # Cost efficiency: inverse of avg_cost (cheaper = more efficient)
                    # Normalize: $0 = 1.0, $0.10 = 0.5, $1.00 = 0.1
                    cost_efficiency = (
                        max(0.0, min(1.0, 1.0 / (1.0 + avg_cost / 0.05))) if avg_cost > 0 else 0.7
                    )

                    # Try to get eval scores for this agent
                    eval_row = (
                        await session.execute(
                            _t("""
                            SELECT
                                COUNT(*) as total,
                                AVG(score_task_completion) as accuracy,
                                AVG(score_coherence) as coherence
                            FROM evaluations e
                            JOIN goals g ON e.goal_id = g.id
                            WHERE e.tenant_id = :tid
                              AND g.agent_id = :aid
                        """),
                            {"tid": tenant.tenant_id, "aid": agent_id},
                        )
                    ).fetchone()

                    accuracy = float(eval_row[1] or 0.7) if eval_row and eval_row[0] else 0.7
                    coherence = float(eval_row[2] or 0.7) if eval_row and eval_row[0] else 0.7

                    # Tool coverage: default until refined by distinct-tool query below
                    tool_coverage = 0.7

                    health = {
                        "speed": round(speed, 3),
                        "accuracy": round(accuracy, 3),
                        "cost_efficiency": round(cost_efficiency, 3),
                        "tool_coverage": round(tool_coverage, 3),
                        "success_rate": round(success_rate, 3),
                        "coherence": round(coherence, 3),
                    }
                    sample_size = total

                    # Refine tool_coverage using actual distinct tools from execution_context
                    try:
                        tool_rows = (
                            await session.execute(
                                _t("""
                                SELECT COUNT(DISTINCT (execution_context->>'last_tool_used'))
                                    AS unique_tools
                                FROM goals
                                WHERE tenant_id = :tid
                                  AND agent_id = :aid
                                  AND execution_context->>'last_tool_used' IS NOT NULL
                            """),
                                {"tid": tenant.tenant_id, "aid": agent_id},
                            )
                        ).fetchone()
                        if tool_rows and tool_rows[0]:
                            unique_tools = int(tool_rows[0])
                            # 10+ unique tools = 1.0, 0 = 0.0
                            tool_coverage = min(1.0, unique_tools / 10.0)
                    except Exception:
                        pass
                    health["tool_coverage"] = round(tool_coverage, 3)
        except Exception:
            pass  # Return defaults on any DB error

    return {"agent_id": agent_id, "health": health, "sample_size": sample_size}


# ── Platform Benchmarks ───────────────────────────────────────────────────────


@router.get("/benchmarks")
async def get_benchmarks(request: Request) -> dict[str, Any]:
    """Return platform-wide benchmarks. Uses real data when available, clearly-labeled estimates otherwise."""  # noqa: E501
    _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    # Try to compute real benchmarks from the platform's goal data
    real_data = False
    benchmarks: dict[str, Any] = {}

    if goal_svc is not None:
        db = getattr(goal_svc, "_db", None)
        if db is not None:
            try:
                from sqlalchemy import text as _t

                async with db() as session:
                    # Cross-tenant aggregate (anonymized)
                    row = (
                        await session.execute(
                            _t("""
                            SELECT
                                COUNT(*) as total,
                                AVG(CASE WHEN status = 'complete' THEN 1.0 ELSE 0.0 END)
                                    as success_rate,
                                AVG(COALESCE(cost_usd, 0)) as avg_cost,
                                AVG(COALESCE(duration_s, 0)) as avg_duration,
                                AVG(COALESCE(iterations, 0)) as avg_iterations,
                                PERCENTILE_CONT(0.25)
                                    WITHIN GROUP (ORDER BY COALESCE(cost_usd, 0)) as p25_cost,
                                PERCENTILE_CONT(0.50)
                                    WITHIN GROUP (ORDER BY COALESCE(cost_usd, 0)) as p50_cost,
                                PERCENTILE_CONT(0.75)
                                    WITHIN GROUP (ORDER BY COALESCE(cost_usd, 0)) as p75_cost,
                                PERCENTILE_CONT(0.90)
                                    WITHIN GROUP (ORDER BY COALESCE(cost_usd, 0)) as p90_cost,
                                PERCENTILE_CONT(0.25) WITHIN GROUP (
                                    ORDER BY CASE WHEN status = 'complete' THEN 1.0 ELSE 0.0 END
                                ) as p25_sr,
                                PERCENTILE_CONT(0.50) WITHIN GROUP (
                                    ORDER BY CASE WHEN status = 'complete' THEN 1.0 ELSE 0.0 END
                                ) as p50_sr,
                                PERCENTILE_CONT(0.75) WITHIN GROUP (
                                    ORDER BY CASE WHEN status = 'complete' THEN 1.0 ELSE 0.0 END
                                ) as p75_sr,
                                PERCENTILE_CONT(0.90) WITHIN GROUP (
                                    ORDER BY CASE WHEN status = 'complete' THEN 1.0 ELSE 0.0 END
                                ) as p90_sr
                            FROM goals
                            WHERE status IN ('complete', 'failed')
                              AND cost_usd IS NOT NULL
                        """),
                            {},
                        )
                    ).fetchone()

                    if row and row[0] and int(row[0]) >= 10:
                        real_data = True
                        benchmarks = {
                            "platform_avg_success_rate": round(float(row[1] or 0), 3),
                            "platform_avg_cost_usd": round(float(row[2] or 0), 4),
                            "platform_avg_duration_s": int(float(row[3] or 0)),
                            "platform_avg_iterations": round(float(row[4] or 0), 1),
                            "top_10_pct_success_rate": round(float(row[12] or 0), 3),
                            "top_10_pct_cost_usd": round(float(row[8] or 0), 4),
                            "percentile_bands": {
                                "p25": {
                                    "success_rate": round(float(row[9] or 0), 3),
                                    "cost_usd": round(float(row[5] or 0), 4),
                                },
                                "p50": {
                                    "success_rate": round(float(row[10] or 0), 3),
                                    "cost_usd": round(float(row[6] or 0), 4),
                                },
                                "p75": {
                                    "success_rate": round(float(row[11] or 0), 3),
                                    "cost_usd": round(float(row[7] or 0), 4),
                                },
                                "p90": {
                                    "success_rate": round(float(row[12] or 0), 3),
                                    "cost_usd": round(
                                        float(row[8] or 0), 4
                                    ),  # p90 cost > p75 > p50 (correct order)
                                },
                            },
                            "sample_count": int(row[0]),
                            "data_source": "live_platform_data",
                        }
            except Exception:
                pass

    if not real_data:
        # Return clearly-labeled placeholder values (no fabricated "real" data)
        benchmarks = {
            "platform_avg_success_rate": None,
            "platform_avg_cost_usd": None,
            "platform_avg_duration_s": None,
            "platform_avg_iterations": None,
            "top_10_pct_success_rate": None,
            "top_10_pct_cost_usd": None,
            "percentile_bands": {},
            "sample_count": 0,
            "data_source": "insufficient_data",
            "message": "Benchmarks require at least 10 completed goals with cost data to compute. Run more goals to see real benchmarks.",  # noqa: E501
        }

    return benchmarks
