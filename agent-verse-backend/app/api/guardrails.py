"""Guardrails API — CRUD for configs, violations query, stats, and live test endpoint.

Violations and stats read the durable guardrails-v2 store (P8b-3);
``GET /guardrails/violations`` is a deprecated alias of ``GET /guardrails-v2/violations``.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator

from app.intelligence.guardrail_engine import GuardrailEngine

router = APIRouter(prefix="/guardrails", tags=["guardrails"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateGuardrailConfigRequest(BaseModel):
    name: str
    # Gap 4: accept singular `layer` OR plural `layers`; normalize to `layers`
    layer: str | None = None  # backward-compat alias
    layers: list[str] = Field(default_factory=list)
    rule_type: str = "injection"
    config: dict[str, Any] = Field(default_factory=dict)
    severity: str = "high"
    action: str = "block"
    agent_id: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_layers(self) -> CreateGuardrailConfigRequest:
        """Ensure `layers` is always populated; fall back to `layer` when not set."""
        if self.layer and not self.layers:
            self.layers = [self.layer]
        # If neither set, default to ["goal"] for backward compat
        if not self.layers:
            self.layers = ["goal"]
        # Keep `layer` in sync with first entry (backward compat reads)
        if not self.layer and self.layers:
            self.layer = self.layers[0]
        return self


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

# Deprecated: configs are guardrails_v2 rules now (see "Configs are guardrails_v2
# rules" below); kept only so old imports keep working.
_configs_store: dict[str, dict] = {}

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
# Configs are guardrails_v2 rules (P8-1)
# ---------------------------------------------------------------------------
#
# A config created here used to live only in this API process's
# ``_configs_store`` (its ``guardrail_configs`` insert failed and was swallowed
# by ``except: pass``), so no agent, worker or other replica ever enforced it.
# Each config is now a durable guardrails_v2 rule in the tenant's RLS-scoped
# rule store — the rules every goal / workflow / ingestion path evaluates,
# workers included. The legacy request/response shape is kept; the legacy
# fields ride along in the rule's ``config["_legacy"]``.

_LEGACY_KEY = "_legacy"

_LAYER_MAP: dict[str, tuple[str, ...]] = {
    "goal": ("goal",),
    "input": ("goal",),
    "plan": ("plan",),
    "step": ("step",),
    "tool": ("tool_args", "tool_output"),
    "tool_args": ("tool_args",),
    "tool_input": ("tool_args",),
    "tool_output": ("tool_output",),
    "output": ("tool_output",),
    "final": ("final_output",),
    "final_output": ("final_output",),
    "memory": ("memory_write",),
    "memory_write": ("memory_write",),
    "rag_ingest": ("rag_ingest",),
}

_RULE_TYPE_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "pii": ("pii_detection", ("pii",)),
    "pii_detection": ("pii_detection", ("pii",)),
    "phi": ("pii_detection", ("phi",)),
    "pci": ("pii_detection", ("pci",)),
    "secrets": ("pii_detection", ("secrets",)),
    "injection": ("prompt_injection", ("prompt_injection",)),
    "prompt_injection": ("prompt_injection", ("prompt_injection",)),
    "jailbreak": ("prompt_injection", ("jailbreak",)),
    "toxicity": ("toxicity", ("toxicity",)),
    "keyword": ("keyword_block", ()),
    "keyword_block": ("keyword_block", ()),
    "regex": ("regex_match", ()),
    "regex_match": ("regex_match", ()),
}

_ACTION_MAP: dict[str, str] = {
    "block": "block",
    "redact": "redact",
    "warn": "warn",
    "flag": "warn",
    "log": "log",
    "allow": "allow",
    "require_hitl": "require_hitl",
    "require_approval": "require_hitl",
    "quarantine": "quarantine",
}


def _rules_engine() -> Any:
    # Resolved per call so the process singleton (and a test's replacement) wins.
    from app.guardrails_v2 import engine as _engine_mod

    return _engine_mod.guardrails_engine


def _v2_rule_fields(record: dict[str, Any]) -> dict[str, Any]:
    """The guardrails_v2 rule fields of a legacy config record (422 on unknowns)."""
    from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, ViolationCategory

    layers: list[GuardrailLayer] = []
    for raw in record.get("layers") or [record.get("layer") or "goal"]:
        mapped = _LAYER_MAP.get(str(raw).strip().lower())
        if mapped is None:
            raise HTTPException(status_code=422, detail=f"Unsupported guardrail layer {raw!r}")
        layers += [GuardrailLayer(m) for m in mapped if GuardrailLayer(m) not in layers]
    rule_type = _RULE_TYPE_MAP.get(str(record.get("rule_type") or "").strip().lower())
    if rule_type is None:
        raise HTTPException(
            status_code=422, detail=f"Unsupported guardrail rule_type {record.get('rule_type')!r}"
        )
    action = _ACTION_MAP.get(str(record.get("action") or "").strip().lower())
    if action is None:
        raise HTTPException(
            status_code=422, detail=f"Unsupported guardrail action {record.get('action')!r}"
        )
    legacy = {k: record.get(k) for k in ("agent_id", "layer", "layers", "rule_type", "action",
                                          "created_at")}
    return {
        "name": str(record["name"]),
        "rule_type": rule_type[0],
        "layers": layers,
        "action": GuardrailAction(action),
        "categories": [ViolationCategory(c) for c in rule_type[1]],
        "severity": str(record.get("severity") or "high"),
        "enabled": bool(record.get("enabled", True)),
        "config": {**dict(record.get("config") or {}), _LEGACY_KEY: legacy},
    }


def _record_of(rule: Any) -> dict[str, Any]:
    """The legacy config view of a guardrails_v2 rule created through this router."""
    config = dict(rule.config or {})
    legacy = dict(config.pop(_LEGACY_KEY, {}) or {})
    return {
        "id": rule.rule_id,
        "tenant_id": rule.tenant_id,
        "agent_id": legacy.get("agent_id"),
        "name": rule.name,
        "layer": legacy.get("layer"),
        "layers": legacy.get("layers") or [],
        "rule_type": legacy.get("rule_type") or rule.rule_type,
        "config": config,
        "severity": rule.severity,
        "action": legacy.get("action") or rule.action.value,
        "enabled": rule.enabled,
        "created_at": legacy.get("created_at"),
    }


async def _legacy_rules(tenant_id: str) -> list[Any]:
    engine = _rules_engine()
    try:
        await engine.ensure_tenant_loaded(tenant_id)
    except Exception as exc:  # never answer with a partial (in-memory) list
        raise HTTPException(
            status_code=503, detail="Guardrail rules are temporarily unavailable"
        ) from exc
    return [r for r in engine.all_rules(tenant_id) if _LEGACY_KEY in (r.config or {})]


async def _legacy_rule(tenant_id: str, config_id: str) -> Any:
    rule = next((r for r in await _legacy_rules(tenant_id) if r.rule_id == config_id), None)
    if rule is None:
        raise HTTPException(status_code=404, detail="Guardrail config not found")
    return rule


# ---------------------------------------------------------------------------
# GET /guardrails  — list configs for tenant
# ---------------------------------------------------------------------------


@router.get("")
async def list_guardrail_configs(
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    configs = [_record_of(r) for r in await _legacy_rules(ctx.tenant_id)]
    configs.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
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
    from app.guardrails_v2.models import GuardrailRule

    tenant_id = ctx.tenant_id
    record = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "agent_id": body.agent_id,
        "name": body.name,
        "layer": body.layer,  # normalized by model_validator
        "layers": body.layers,  # normalized by model_validator
        "rule_type": body.rule_type,
        "config": body.config,
        "severity": body.severity,
        "action": body.action,
        "enabled": body.enabled,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    rule = GuardrailRule(
        rule_id=record["id"], tenant_id=tenant_id, created_at=record["created_at"],
        **_v2_rule_fields(record),
    )
    try:
        durable = await _rules_engine().add_rule_durable(rule)
    except Exception as exc:
        # Persisted BEFORE answering "created": never a rule nobody enforces.
        raise HTTPException(
            status_code=503, detail="Guardrail config could not be saved; nothing was created"
        ) from exc
    return {**record, "durable": durable}


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
    current = _record_of(await _legacy_rule(tenant_id, config_id))
    current.update(body.model_dump(exclude_none=True))
    fields = _v2_rule_fields(current)
    try:
        updated = await _rules_engine().update_rule_durable(tenant_id, config_id, **fields)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Guardrail config could not be saved; nothing changed"
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Guardrail config not found")
    return _record_of(updated)


# ---------------------------------------------------------------------------
# DELETE /guardrails/{config_id}
# ---------------------------------------------------------------------------


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_guardrail_config(
    config_id: str,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> None:
    await _legacy_rule(ctx.tenant_id, config_id)
    try:
        deleted = await _rules_engine().delete_rule_durable(ctx.tenant_id, config_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Guardrail config could not be deleted"
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Guardrail config not found")


# ---------------------------------------------------------------------------
# POST /guardrails/test  — live-test a rule (rate-limited 20/min per tenant)
# ---------------------------------------------------------------------------


# Legacy (v1) verdict actions, least → most severe.
_V1_ACTION_RANK = ("logged", "warned", "redacted", "hitl_queued", "blocked")
# guardrails_v2 rule action → the legacy verdict action it produces.
_V2_TO_V1_ACTION: dict[str, str] = {
    "log": "logged",
    "warn": "warned",
    "redact": "redacted",
    "require_hitl": "hitl_queued",
    "block": "blocked",
    "quarantine": "blocked",
}
# v2 rules carry a severity, not a score; the legacy shape reports a risk_score.
_SEVERITY_RISK: dict[str, float] = {
    "critical": 1.0, "high": 0.9, "medium": 0.6, "low": 0.3, "info": 0.1,
}


def _matched_pattern(matches: list[Any]) -> str | None:
    """A printable summary of a rule's matches (PII values are never echoed)."""
    parts: list[str] = []
    for m in matches:
        text = str(m.get("type") or "") if isinstance(m, dict) else str(m)
        if text and text not in parts:
            parts.append(text)
    return ", ".join(parts)[:200] or None


async def _tenant_rule_hits(body: TestGuardrailRequest, tenant_id: str) -> list[dict[str, Any]]:
    """The tenant's stored guardrails_v2 rules that *body* would trigger.

    Uses the engine's non-recording ``simulate`` — testing content must never
    write a violation (``/guardrails-v2/evaluate`` records them).
    """
    layers = _LAYER_MAP.get(str(body.layer).strip().lower(), ("goal",))
    hits: list[dict[str, Any]] = []
    for layer in layers:
        content = (
            json.dumps(body.tool_args, default=str)
            if layer == "tool_args" and body.tool_args
            else body.text
        )
        try:
            sim = await _rules_engine().simulate(content, layer, tenant_id)
        except Exception as exc:  # never answer "allowed" without the tenant's rules
            raise HTTPException(
                status_code=503, detail="Guardrail rules are temporarily unavailable"
            ) from exc
        for hit in sim.get("triggered_rules") or []:
            hits.append({**hit, "layer": layer})
    return hits


@router.post("/test")
async def test_guardrail(
    body: TestGuardrailRequest,
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    """Dry-run *body* against the built-in checks AND the tenant's stored rules.

    QA-12: this used to run only the built-in v1 checks, so a tenant's own rules
    (what goals, workflows and ingestion actually enforce) never showed up here.
    Nothing is recorded.
    """
    _check_test_rate(ctx.tenant_id)
    tenant_hits = await _tenant_rule_hits(body, ctx.tenant_id)
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
        result = await engine.evaluate_goal(body.text, context={"tenant_id": ctx.tenant_id})

    violations_out = [
        {
            "layer": v.layer,
            "category": v.category,
            "severity": v.severity.value,
            "risk_score": v.risk_score,
            "matched_pattern": v.matched_pattern,
            "recommendation": (
                "block" if v.risk_score >= 0.9 else "warn" if v.risk_score >= 0.6 else "log"
            ),
            "source": "builtin",
        }
        for v in result.violations
    ]
    action = result.action.value
    risk_score = result.risk_score
    for hit in tenant_hits:
        severity = str(hit.get("severity") or "high")
        hit_risk = _SEVERITY_RISK.get(severity.lower(), 0.9)
        violations_out.append(
            {
                "layer": hit["layer"],
                "category": hit.get("category") or "unknown",
                "severity": severity,
                "risk_score": hit_risk,
                "matched_pattern": _matched_pattern(list(hit.get("matches") or [])),
                "recommendation": hit["action"],
                "source": "tenant_rule",
                "rule_id": hit.get("rule_id"),
                "rule_name": hit.get("rule_name"),
            }
        )
        risk_score = max(risk_score, hit_risk)
        hit_action = _V2_TO_V1_ACTION.get(str(hit["action"]), "logged")
        if _V1_ACTION_RANK.index(hit_action) > _V1_ACTION_RANK.index(action):
            action = hit_action
    return {
        "allowed": action not in ("blocked", "hitl_queued"),
        "risk_score": risk_score,
        "action": action,
        "quarantined": any(h["action"] == "quarantine" for h in tenant_hits),
        "violations": violations_out,
        "input_hash": result.input_hash,
    }


# ---------------------------------------------------------------------------
# GET /guardrails/violations  — deprecated alias of GET /guardrails-v2/violations
# ---------------------------------------------------------------------------

_V2_VIOLATIONS = "/guardrails-v2/violations"


@router.get("/violations")
async def list_violations(
    request: Request,
    response: Response,
    severity: str | None = None,
    layer: str | None = None,
    goal_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = None,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    """Deprecated: use ``GET /guardrails-v2/violations``.

    P8b-3: this read a per-process ``_violations_store`` that nothing wrote to,
    so it always answered "no violations". It now serves the same durable,
    tenant-scoped, keyset-paginated rows as the v2 endpoint, in the legacy shape.
    OFFSET paging is gone (``offset`` > 0 is refused): page with ``cursor``.
    """
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.violation_pages import InvalidCursorError, violation_page

    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{_V2_VIOLATIONS}>; rel="successor-version"'
    if offset:
        raise HTTPException(
            status_code=400,
            detail=f"offset paging is not supported; use cursor (see {_V2_VIOLATIONS})",
        )
    try:
        page = await violation_page(
            guardrails_engine,
            ctx.tenant_id,
            limit=limit,
            severity=severity,
            layer=layer,
            goal_id=goal_id,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Guardrail violations are temporarily unavailable"
        ) from exc
    return {
        "violations": [
            {
                "id": v.violation_id,
                "guardrail_id": v.rule_id,
                "guardrail_name": v.rule_name,
                "type": v.category,
                "severity": v.severity,
                "layer": v.layer,
                "action_taken": v.action_taken,
                "message": v.content_preview,
                "goal_id": v.goal_id,
                "created_at": v.created_at,
            }
            for v in page.violations
        ],
        "total": len(page.violations),
        "limit": limit,
        "next_cursor": page.next_cursor,
    }


# ---------------------------------------------------------------------------
# GET /guardrails/stats  — aggregated violation statistics (durable store)
# ---------------------------------------------------------------------------

_STATS_WINDOW_DAYS = 30


@router.get("/stats")
async def guardrail_stats(
    request: Request,
    ctx: Any = Depends(_require_tenant),
) -> dict[str, Any]:
    """Counts of the tenant's durable violations over the last 30 days.

    P8b-3: these were computed from the same never-written per-process dict, so
    the dashboard always showed zero. ``total_all`` is kept for older clients
    and now means the window total; there is no risk score on durable
    violations, so ``risk_score_p95`` is ``null`` rather than a made-up 0.
    """
    from app.guardrails_v2.engine import guardrails_engine

    try:
        stats = await guardrails_engine.aget_violation_stats(
            ctx.tenant_id, window_days=_STATS_WINDOW_DAYS
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Guardrail statistics are temporarily unavailable"
        ) from exc
    return {**stats, "total_all": stats["total_window"], "risk_score_p95": None}
