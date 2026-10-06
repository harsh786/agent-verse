"""Guardrails 2.0 API."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.governance.audit_v2 import audit_admin_action
from app.guardrails_v2.models import (
    COMPLIANCE_BUNDLES,
    ComplianceBundle,
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
)
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/guardrails-v2", tags=["guardrails-v2"])


def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class CreateRuleRequest(BaseModel):
    name: str
    rule_type: str
    layers: list[str] = Field(default_factory=lambda: ["step"])
    action: str = "block"
    categories: list[str] = Field(default_factory=list)
    severity: str = "high"
    config: dict[str, Any] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    content: str
    layer: str
    goal_id: str | None = None
    step_description: str | None = None


class SimulateRequest(BaseModel):
    content: str
    layer: str


class CorpusSampleModel(BaseModel):
    content: str
    should_block: bool
    layer: str = "final_output"


class EvaluateCorpusRequest(BaseModel):
    samples: list[CorpusSampleModel] = Field(..., max_length=500)


@router.post("/rules")
@audit_admin_action("guardrail_rule.created", "guardrail_rule", "create")
async def create_rule(
    request: Request,
    body: CreateRuleRequest,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Create a new guardrail rule."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    try:
        layers = [GuardrailLayer(layer_val) for layer_val in body.layers]
    except ValueError as e:
        raise HTTPException(400, f"Invalid layer: {e}") from e

    try:
        action = GuardrailAction(body.action)
    except ValueError as _b904_exc:
        raise HTTPException(400, f"Invalid action: {body.action}") from _b904_exc

    rule = GuardrailRule(
        rule_id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        name=body.name,
        rule_type=body.rule_type,
        layers=layers,
        action=action,
        categories=[],
        severity=body.severity,
        config=body.config,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    # Persisted BEFORE answering "created" (GRD-02): it used to be a best-effort
    # background flush, so a failed write still reported success.
    try:
        durable = await guardrails_engine.add_rule_durable(rule)
    except Exception as exc:
        raise HTTPException(503, "Guardrail rule could not be saved; nothing was created") from exc

    return {"rule_id": rule.rule_id, "status": "created", "name": rule.name, "durable": durable}


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    action: str | None = None
    severity: str | None = None
    layers: list[str] | None = None
    config: dict[str, Any] | None = None


def _rule_view(r: GuardrailRule) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "name": r.name,
        "rule_type": r.rule_type,
        "layers": [layer_val.value for layer_val in r.layers],
        "action": r.action.value,
        "severity": r.severity,
        "enabled": r.enabled,
        "version": r.version,
    }


def _is_seeded(rule_id: str) -> bool:
    # Baseline / compliance-bundle rules are re-seeded on every fresh process;
    # deleting one would bring it back — disable it instead.
    return rule_id.startswith(("gr-default:", "gr-bundle:"))


@router.patch("/rules/{rule_id}")
@audit_admin_action(
    "guardrail_rule.updated",
    "guardrail_rule",
    "update",
    extract_resource_id=lambda kw: kw.get("rule_id"),
)
async def update_rule(
    request: Request,
    rule_id: str,
    body: UpdateRuleRequest,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Update or disable/enable a rule (GRD-01). Persisted before it answers."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine

    changes: dict[str, Any] = {}
    if body.name is not None:
        changes["name"] = body.name
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    if body.severity is not None:
        changes["severity"] = body.severity
    if body.config is not None:
        changes["config"] = body.config
    try:
        if body.action is not None:
            changes["action"] = GuardrailAction(body.action)
        if body.layers is not None:
            changes["layers"] = [GuardrailLayer(v) for v in body.layers]
    except ValueError as exc:
        raise HTTPException(400, f"Invalid value: {exc}") from exc
    if not changes:
        raise HTTPException(400, "No changes supplied")
    try:
        updated = await guardrails_engine.update_rule_durable(tenant.tenant_id, rule_id, **changes)
    except GuardrailRulesUnavailableError as exc:
        raise HTTPException(503, "Guardrail rules are temporarily unavailable") from exc
    except Exception as exc:
        raise HTTPException(503, "Guardrail rule could not be saved; nothing changed") from exc
    if updated is None:
        raise HTTPException(404, "Rule not found")
    return {"status": "updated", "rule": _rule_view(updated)}


@router.delete("/rules/{rule_id}")
@audit_admin_action(
    "guardrail_rule.deleted",
    "guardrail_rule",
    "delete",
    extract_resource_id=lambda kw: kw.get("rule_id"),
)
async def delete_rule(
    request: Request,
    rule_id: str,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Delete a custom rule (GRD-01). Baseline/bundle rules can only be disabled."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine

    if _is_seeded(rule_id):
        raise HTTPException(409, "Baseline and compliance-bundle rules can only be disabled")
    try:
        deleted = await guardrails_engine.delete_rule_durable(tenant.tenant_id, rule_id)
    except GuardrailRulesUnavailableError as exc:
        raise HTTPException(503, "Guardrail rules are temporarily unavailable") from exc
    except Exception as exc:
        raise HTTPException(503, "Guardrail rule could not be deleted; nothing changed") from exc
    if not deleted:
        raise HTTPException(404, "Rule not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.get("/rules")
async def list_rules(request: Request) -> dict[str, Any]:
    """List all guardrail rules for the tenant."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine

    # aget_rules loads this tenant's persisted rules on a fresh process (there is
    # no cross-tenant warm-up at startup any more).
    try:
        await guardrails_engine.ensure_tenant_loaded(tenant.tenant_id)
    except GuardrailRulesUnavailableError as exc:
        raise HTTPException(503, "Guardrail rules are temporarily unavailable") from exc
    # Disabled rules are listed too, so they can be re-enabled.
    return {"rules": [_rule_view(r) for r in guardrails_engine.all_rules(tenant.tenant_id)]}


@router.post("/evaluate")
async def evaluate_content(request: Request, body: EvaluateRequest) -> dict[str, Any]:
    """Evaluate content against guardrail rules."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine

    try:
        layer = GuardrailLayer(body.layer)
    except ValueError as _b904_exc:
        raise HTTPException(400, f"Invalid layer: {body.layer}") from _b904_exc

    try:
        result = await guardrails_engine.evaluate(
            content=body.content,
            layer=layer,
            tenant_id=tenant.tenant_id,
            goal_id=body.goal_id,
            step_description=body.step_description,
        )
    except GuardrailRulesUnavailableError as exc:
        raise HTTPException(503, "Guardrail rules are temporarily unavailable") from exc
    return result


@router.post("/simulate")
async def simulate_evaluation(request: Request, body: SimulateRequest) -> dict[str, Any]:
    """Simulate guardrail evaluation without recording violations."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine

    try:
        return await guardrails_engine.simulate(body.content, body.layer, tenant.tenant_id)
    except GuardrailRulesUnavailableError as exc:
        raise HTTPException(503, "Guardrail rules are temporarily unavailable") from exc


@router.post("/evaluate-corpus")
async def evaluate_corpus(request: Request, body: EvaluateCorpusRequest) -> dict[str, Any]:
    """Measure the tenant's guardrail rules against a labelled corpus.

    Runs ``simulate`` (records nothing) over each labelled sample and reports
    precision/recall/F1 plus tuning recommendations — which rules are
    over-aggressive (fire on benign content) and which attacks slip through.
    """
    from dataclasses import asdict

    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.tuner import CorpusSample, GuardrailTuner

    tenant = _require_tenant(request)
    tuner = GuardrailTuner(guardrails_engine)
    samples = [
        CorpusSample(content=s.content, should_block=s.should_block, layer=s.layer)
        for s in body.samples
    ]
    report = await tuner.evaluate_corpus(tenant.tenant_id, samples)
    return asdict(report)


@router.get("/violations")
async def list_violations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    severity: str | None = Query(default=None),
    layer: str | None = Query(default=None),
    goal_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None, description="next_cursor of the previous page"),
) -> dict[str, Any]:
    """The tenant's guardrail violations, newest first, keyset-paginated."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.violation_pages import InvalidCursorError, violation_page

    # Every replica's violations from Postgres (GRD-04), never this process's alone.
    try:
        page = await violation_page(
            guardrails_engine,
            tenant.tenant_id,
            limit=limit,
            severity=severity,
            layer=layer,
            goal_id=goal_id,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    except Exception as exc:
        raise HTTPException(503, "Guardrail violations are temporarily unavailable") from exc

    return {
        "violations": [
            {
                "violation_id": v.violation_id,
                "rule_id": v.rule_id,
                "rule_name": v.rule_name,
                "layer": v.layer,
                "action_taken": v.action_taken,
                "category": v.category,
                "severity": v.severity,
                "goal_id": v.goal_id,
                "content_preview": v.content_preview,
                "created_at": v.created_at,
            }
            for v in page.violations
        ],
        # The size of THIS page (kept for older clients); page with next_cursor.
        "total": len(page.violations),
        "next_cursor": page.next_cursor,
    }


@router.post("/bundles/{bundle_name}")
@audit_admin_action(
    "guardrail_bundle.enabled",
    "guardrail_bundle",
    "enable",
    extract_resource_id=lambda kw: kw.get("bundle_name"),
)
async def enable_compliance_bundle(
    request: Request,
    bundle_name: str,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Enable a compliance bundle (creates preset rules)."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    try:
        bundle = ComplianceBundle(bundle_name)
    except ValueError as _b904_exc:
        valid = [b.value for b in ComplianceBundle]
        raise HTTPException(400, f"Unknown bundle: {bundle_name}. Valid: {valid}") from _b904_exc

    bundle_rules = COMPLIANCE_BUNDLES.get(bundle, [])
    # a03-F063-03: rule ids are derived from (tenant, bundle, rule name), so
    # re-enabling a bundle finds its rules instead of minting duplicates, and
    # every write goes through the durable path (it was the in-memory add_rule
    # with a best-effort background flush behind an "enabled" answer).
    try:
        await guardrails_engine.ensure_tenant_loaded(tenant.tenant_id)
    except Exception as exc:
        raise HTTPException(503, "Guardrail rules could not be read; retry shortly") from exc
    created: list[str] = []
    existing: list[str] = []
    durable = True
    try:
        for rule_def in bundle_rules:
            try:
                layers = [GuardrailLayer(lv) for lv in rule_def.get("layers", ["step"])]
                action = GuardrailAction(rule_def.get("action", "block"))
            except ValueError:
                continue
            rule_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"agentverse:guardrail-bundle:{tenant.tenant_id}:{bundle.value}:"
                    f"{rule_def['name']}",
                )
            )
            current = guardrails_engine.find_rule(tenant.tenant_id, rule_id)
            if current is not None:
                if not current.enabled:
                    await guardrails_engine.update_rule_durable(
                        tenant.tenant_id, rule_id, enabled=True
                    )
                existing.append(rule_id)
                continue
            rule = GuardrailRule(
                rule_id=rule_id,
                tenant_id=tenant.tenant_id,
                name=f"[{bundle.value.upper()}] {rule_def['name']}",
                rule_type=rule_def["rule_type"],
                layers=layers,
                action=action,
                severity=rule_def.get("severity", "high"),
                created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            )
            durable = await guardrails_engine.add_rule_durable(rule) and durable
            created.append(rule_id)
    except Exception as exc:
        raise HTTPException(
            503,
            "Compliance bundle rules could not be saved; retry (re-enabling is safe)",
        ) from exc

    return {
        "bundle": bundle_name,
        "rules_created": len(created),
        "rules_existing": len(existing),
        "rule_ids": created + existing,
        "status": "enabled",
        "durable": durable,
    }


@router.get("/layers")
async def list_layers(request: Request) -> dict[str, Any]:
    """List all available guardrail layers."""
    _require_tenant(request)
    _layer_descriptions = {
        "goal": "Applied to the initial goal text before planning",
        "plan": "Applied to the execution plan before running",
        "step": "Applied to each step description before execution",
        "tool_args": "Applied to tool call arguments before executing",
        "tool_output": "Applied to tool output after execution",
        "final_output": "Applied to the final goal result",
        "memory_write": "Applied before writing to long-term memory",
        "rag_ingest": "Applied before indexing documents into RAG",
        "graph_extract": "Applied before adding to knowledge graph",
    }
    return {
        "layers": [
            {
                "id": lyr.value,
                "name": lyr.value.replace("_", " ").title(),
                "description": _layer_descriptions.get(lyr.value, ""),
            }
            for lyr in GuardrailLayer
        ]
    }
