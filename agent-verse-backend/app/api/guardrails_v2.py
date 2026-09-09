"""Guardrails 2.0 API."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.guardrails_v2.models import (
    COMPLIANCE_BUNDLES,
    ComplianceBundle,
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
)

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
async def create_rule(request: Request, body: CreateRuleRequest) -> dict[str, Any]:
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
    guardrails_engine.add_rule(rule)

    return {"rule_id": rule.rule_id, "status": "created", "name": rule.name}


@router.get("/rules")
async def list_rules(request: Request) -> dict[str, Any]:
    """List all guardrail rules for the tenant."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    rules = guardrails_engine.get_rules(tenant.tenant_id)
    return {
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "rule_type": r.rule_type,
                "layers": [layer_val.value for layer_val in r.layers],
                "action": r.action.value,
                "severity": r.severity,
                "enabled": r.enabled,
                "version": r.version,
            }
            for r in rules
        ]
    }


@router.post("/evaluate")
async def evaluate_content(request: Request, body: EvaluateRequest) -> dict[str, Any]:
    """Evaluate content against guardrail rules."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    try:
        layer = GuardrailLayer(body.layer)
    except ValueError as _b904_exc:
        raise HTTPException(400, f"Invalid layer: {body.layer}") from _b904_exc

    result = await guardrails_engine.evaluate(
        content=body.content,
        layer=layer,
        tenant_id=tenant.tenant_id,
        goal_id=body.goal_id,
        step_description=body.step_description,
    )
    return result


@router.post("/simulate")
async def simulate_evaluation(request: Request, body: SimulateRequest) -> dict[str, Any]:
    """Simulate guardrail evaluation without recording violations."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    return await guardrails_engine.simulate(body.content, body.layer, tenant.tenant_id)


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
    limit: int = Query(default=50, le=500),
    severity: str | None = Query(default=None),
) -> dict[str, Any]:
    """List guardrail violations for the tenant."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    violations = guardrails_engine.get_violations(tenant.tenant_id, limit)
    if severity:
        violations = [v for v in violations if v.severity == severity]

    return {
        "violations": [
            {
                "violation_id": v.violation_id,
                "rule_name": v.rule_name,
                "layer": v.layer,
                "action_taken": v.action_taken,
                "category": v.category,
                "severity": v.severity,
                "goal_id": v.goal_id,
                "content_preview": v.content_preview,
                "created_at": v.created_at,
            }
            for v in violations
        ],
        "total": len(violations),
    }


@router.post("/bundles/{bundle_name}")
async def enable_compliance_bundle(request: Request, bundle_name: str) -> dict[str, Any]:
    """Enable a compliance bundle (creates preset rules)."""
    tenant = _require_tenant(request)
    from app.guardrails_v2.engine import guardrails_engine

    try:
        bundle = ComplianceBundle(bundle_name)
    except ValueError as _b904_exc:
        valid = [b.value for b in ComplianceBundle]
        raise HTTPException(400, f"Unknown bundle: {bundle_name}. Valid: {valid}") from _b904_exc

    bundle_rules = COMPLIANCE_BUNDLES.get(bundle, [])
    created = []

    for rule_def in bundle_rules:
        try:
            layers = [GuardrailLayer(lv) for lv in rule_def.get("layers", ["step"])]
            action = GuardrailAction(rule_def.get("action", "block"))
        except ValueError:
            continue

        rule = GuardrailRule(
            rule_id=str(uuid.uuid4()),
            tenant_id=tenant.tenant_id,
            name=f"[{bundle.value.upper()}] {rule_def['name']}",
            rule_type=rule_def["rule_type"],
            layers=layers,
            action=action,
            severity=rule_def.get("severity", "high"),
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        guardrails_engine.add_rule(rule)
        created.append(rule.rule_id)

    return {
        "bundle": bundle_name,
        "rules_created": len(created),
        "rule_ids": created,
        "status": "enabled",
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
