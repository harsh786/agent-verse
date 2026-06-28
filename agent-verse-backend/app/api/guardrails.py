"""Guardrails API — CRUD for configs, violations query, stats, and live test endpoint."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.intelligence.guardrail_engine import GuardrailEngine

router = APIRouter(prefix="/guardrails", tags=["guardrails"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateGuardrailConfigRequest(BaseModel):
    name: str
    layer: str = "goal"
    rule_type: str = "injection"
    config: dict[str, Any] = Field(default_factory=dict)
    severity: str = "high"
    action: str = "block"
    agent_id: str | None = None
    enabled: bool = True


class UpdateGuardrailConfigRequest(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    severity: str | None = None
    action: str | None = None
    enabled: bool | None = None


class TestGuardrailRequest(BaseModel):
    text: str
    layer: str = "goal"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


class ViolationFilters(BaseModel):
    severity: str | None = None
    layer: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    goal_id: str | None = None
    limit: int = Field(default=50, le=200)
    offset: int = 0


# ---------------------------------------------------------------------------
# In-memory store (upgraded to DB in lifespan when DB available)
# ---------------------------------------------------------------------------

_configs_store: dict[str, dict] = {}   # tenant_id → {id → config}
_violations_store: dict[str, list] = defaultdict(list)  # tenant_id → [violation]

# Per-tenant rate limiting for /test endpoint: {tenant_id → (count, window_start)}
_test_rate: dict[str, tuple[int, float]] = {}
_TEST_LIMIT = 20
_TEST_WINDOW = 60.0  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _get_engine(request: Request) -> GuardrailEngine:
    engine = getattr(request.app.state, "guardrail_engine", None)
    if engine is None:
        engine = GuardrailEngine()
    return engine


def _check_test_rate(tenant_id: str) -> None:
    now = time.monotonic()
    count, window_start = _test_rate.get(tenant_id, (0, now))
    if now - window_start > _TEST_WINDOW:
        count, window_start = 0, now
    if count >= _TEST_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Test endpoint limited to {_TEST_LIMIT} requests/minute per tenant.",
        )
    _test_rate[tenant_id] = (count + 1, window_start)


# ---------------------------------------------------------------------------
# GET /guardrails  — list configs for tenant
# ---------------------------------------------------------------------------

@router.get("")
async def list_guardrail_configs(
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    tenant_id = ctx.tenant_id
    # Try DB first
    db_factory = getattr(request.app.state, "_db_session_factory", None)
    if db_factory is not None:
        try:
            from sqlalchemy import text as _sql
            async with db_factory() as session:
                from app.db.rls import rls_context
                async with rls_context(session, tenant_id):
                    result = await session.execute(_sql(
                        "SELECT id, tenant_id, agent_id, name, layer, rule_type, "
                        "config, severity, action, enabled, created_at "
                        "FROM guardrail_configs WHERE tenant_id = :tid ORDER BY created_at DESC",
                    ), {"tid": tenant_id})
                    rows = result.fetchall()
                    configs = [dict(r._mapping) for r in rows]
                    return {"configs": configs, "total": len(configs)}
        except Exception:
            pass  # fall through to in-memory

    configs = list(_configs_store.get(tenant_id, {}).values())
    return {"configs": configs, "total": len(configs)}


# ---------------------------------------------------------------------------
# POST /guardrails  — create a new config rule
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_guardrail_config(
    body: CreateGuardrailConfigRequest,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    tenant_id = ctx.tenant_id
    config_id = str(uuid.uuid4())
    record = {
        "id": config_id,
        "tenant_id": tenant_id,
        "agent_id": body.agent_id,
        "name": body.name,
        "layer": body.layer,
        "rule_type": body.rule_type,
        "config": body.config,
        "severity": body.severity,
        "action": body.action,
        "enabled": body.enabled,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    db_factory = getattr(request.app.state, "_db_session_factory", None)
    if db_factory is not None:
        try:
            import json as _json

            from sqlalchemy import text as _sql
            async with db_factory() as session:
                from app.db.rls import rls_context
                async with rls_context(session, tenant_id):
                    await session.execute(_sql("""
                        INSERT INTO guardrail_configs
                            (id, tenant_id, agent_id, name, layer, rule_type,
                             config, severity, action, enabled)
                        VALUES
                            (:id, :tenant_id, :agent_id, :name, :layer, :rule_type,
                             :config, :severity, :action, :enabled)
                    """), {**record, "config": _json.dumps(body.config)})
                    await session.commit()
            return record
        except Exception:
            pass

    _configs_store.setdefault(tenant_id, {})[config_id] = record
    return record


# ---------------------------------------------------------------------------
# PUT /guardrails/{config_id}  — update existing config
# ---------------------------------------------------------------------------

@router.put("/{config_id}")
async def update_guardrail_config(
    config_id: str,
    body: UpdateGuardrailConfigRequest,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    tenant_id = ctx.tenant_id
    existing = _configs_store.get(tenant_id, {}).get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Guardrail config not found")

    updates = body.model_dump(exclude_none=True)
    existing.update(updates)
    return existing


# ---------------------------------------------------------------------------
# DELETE /guardrails/{config_id}
# ---------------------------------------------------------------------------

@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_guardrail_config(
    config_id: str,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> None:
    tenant_id = ctx.tenant_id
    tenant_configs = _configs_store.get(tenant_id, {})
    if config_id not in tenant_configs:
        raise HTTPException(status_code=404, detail="Guardrail config not found")
    del tenant_configs[config_id]


# ---------------------------------------------------------------------------
# POST /guardrails/test  — live-test a rule (rate-limited 20/min per tenant)
# ---------------------------------------------------------------------------

@router.post("/test")
async def test_guardrail(
    body: TestGuardrailRequest,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    _check_test_rate(ctx.tenant_id)
    engine: GuardrailEngine = _get_engine(request)

    if body.layer in ("tool_args",) and body.tool_name:
        result = await engine.evaluate_tool_args(
            body.tool_name, body.tool_args or {}, context={"tenant_id": ctx.tenant_id}
        )
    elif body.layer == "tool_output" and body.tool_name:
        result = await engine.evaluate_tool_output(
            body.tool_name, body.text, context={"tenant_id": ctx.tenant_id}
        )
    elif body.layer == "final":
        result = await engine.evaluate_output(body.text, context={"tenant_id": ctx.tenant_id})
    else:
        result = await engine.evaluate_goal(
            body.text, context={"tenant_id": ctx.tenant_id}
        )

    violations_out = [
        {
            "layer": v.layer,
            "category": v.category,
            "severity": v.severity.value,
            "risk_score": v.risk_score,
            "matched_pattern": v.matched_pattern,
            "recommendation": (
                "block" if v.risk_score >= 0.9 else
                "warn" if v.risk_score >= 0.6 else "log"
            ),
        }
        for v in result.violations
    ]
    return {
        "allowed": result.allowed,
        "risk_score": result.risk_score,
        "action": result.action.value,
        "violations": violations_out,
        "input_hash": result.input_hash,
    }


# ---------------------------------------------------------------------------
# GET /guardrails/violations  — query violations with filters
# ---------------------------------------------------------------------------

@router.get("/violations")
async def list_violations(
    request: Request,
    severity: str | None = None,
    layer: str | None = None,
    goal_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    tenant_id = ctx.tenant_id
    violations = _violations_store.get(tenant_id, [])

    if severity:
        violations = [v for v in violations if v.get("severity") == severity]
    if layer:
        violations = [v for v in violations if v.get("layer") == layer]
    if goal_id:
        violations = [v for v in violations if v.get("goal_id") == goal_id]

    total = len(violations)
    page = violations[offset: offset + limit]
    return {"violations": page, "total": total, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# GET /guardrails/stats  — aggregated violation statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
async def guardrail_stats(
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    tenant_id = ctx.tenant_id
    violations = _violations_store.get(tenant_id, [])

    now = time.time()
    day_ago = now - 86_400
    recent = [v for v in violations if v.get("_ts", 0) >= day_ago]

    by_severity: dict[str, int] = defaultdict(int)
    by_layer: dict[str, int] = defaultdict(int)
    category_counts: dict[str, int] = defaultdict(int)

    for v in violations:
        by_severity[v.get("severity", "unknown")] += 1
        by_layer[v.get("layer", "unknown")] += 1
        category_counts[v.get("violation_type", "unknown")] += 1

    top_categories = sorted(
        [{"category": k, "count": c} for k, c in category_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    all_scores = [v.get("risk_score", 0.0) for v in violations]
    p95 = 0.0
    if all_scores:
        sorted_scores = sorted(all_scores)
        p95_idx = int(len(sorted_scores) * 0.95)
        p95 = sorted_scores[min(p95_idx, len(sorted_scores) - 1)]

    return {
        "total_24h": len(recent),
        "total_all": len(violations),
        "by_severity": dict(by_severity),
        "by_layer": dict(by_layer),
        "top_categories": top_categories,
        "risk_score_p95": p95,
    }
