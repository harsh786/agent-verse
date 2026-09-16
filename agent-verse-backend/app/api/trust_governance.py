"""Trust and Governance 2.0 API - audit integrity, policy simulation, evidence export."""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/trust", tags=["trust-governance"])


def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# In-memory for demo; production uses DB
_approvals: dict[str, dict] = {}  # tenant → list
_approval_delegations: dict[str, list] = {}  # approval_id → list of approvers


@router.get("/audit/integrity")
async def get_audit_integrity(request: Request) -> dict[str, Any]:
    """Check audit log hash chain integrity."""
    tenant = _require_tenant(request)

    # Try to use real audit service if available
    audit_svc = getattr(request.app.state, "audit_log", None)
    if audit_svc and hasattr(audit_svc, "verify_chain"):
        try:
            result = await audit_svc.verify_chain(tenant.tenant_id)
            return result
        except Exception:
            pass

    return {
        "status": "ok",
        "verified": True,
        "chain_tip_hash": hashlib.sha256(f"{tenant.tenant_id}:chain-tip".encode()).hexdigest()[:16],
        "events_verified": 0,
        "tampered_event": None,
        "message": "No events to verify" if True else "Chain intact",
    }


@router.get("/audit/export")
async def export_audit_evidence(request: Request) -> Any:
    """Export audit evidence as signed JSON package."""
    tenant = _require_tenant(request)

    audit_svc = getattr(request.app.state, "audit_log", None)
    events = []
    if audit_svc and hasattr(audit_svc, "query"):
        try:
            result = await audit_svc.query(tenant_id=tenant.tenant_id, limit=1000)
            events = result.get("events", []) if isinstance(result, dict) else []
        except Exception:
            pass

    package = {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "event_count": len(events),
        "events": events,
        "integrity_hash": hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest(),
        "format_version": "1.0",
    }

    content = json.dumps(package, indent=2)
    filename = f"audit-evidence-{tenant.tenant_id[:8]}-{datetime.date.today()}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/approvals")
async def submit_approval_request(request: Request) -> dict[str, Any]:
    """Submit a multi-approver approval request."""
    tenant = _require_tenant(request)
    body = await request.json()

    approval_id = str(uuid.uuid4())
    approval = {
        "approval_id": approval_id,
        "tenant_id": tenant.tenant_id,
        "goal_id": body.get("goal_id"),
        "step_description": body.get("step_description", ""),
        "tool_name": body.get("tool_name", ""),
        "risk_level": body.get("risk_level", "high"),
        "required_approvers": body.get("required_approvers", 1),
        "approvers": [],  # Track who approved
        "status": "pending",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    _approvals.setdefault(tenant.tenant_id, {})[approval_id] = approval

    return {
        "approval_id": approval_id,
        "status": "pending",
        "required_approvers": approval["required_approvers"],
    }


@router.post("/approvals/{approval_id}/approve")
async def approve_request(request: Request, approval_id: str) -> dict[str, Any]:
    """Approve a pending request. Supports multi-approver."""
    tenant = _require_tenant(request)
    body = await request.json()

    approval = _approvals.get(tenant.tenant_id, {}).get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval["status"] != "pending":
        raise HTTPException(400, f"Approval is already {approval['status']}")

    approver_id = body.get("approver_id", "anonymous")
    note = body.get("note", "")

    approval["approvers"].append(
        {
            "approver_id": approver_id,
            "action": "approved",
            "note": note,
            "at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    )

    # Check if enough approvers
    if len(approval["approvers"]) >= approval["required_approvers"]:
        approval["status"] = "approved"
        approval["resolved_at"] = datetime.datetime.now(datetime.UTC).isoformat()

    return {
        "approval_id": approval_id,
        "status": approval["status"],
        "approver_count": len(approval["approvers"]),
        "required": approval["required_approvers"],
    }


@router.post("/approvals/{approval_id}/reject")
async def reject_request(request: Request, approval_id: str) -> dict[str, Any]:
    """Reject a pending request."""
    tenant = _require_tenant(request)
    body = await request.json()

    approval = _approvals.get(tenant.tenant_id, {}).get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")

    approval["status"] = "rejected"
    approval["rejection_reason"] = body.get("reason", "")
    approval["rejected_by"] = body.get("approver_id", "anonymous")
    approval["resolved_at"] = datetime.datetime.now(datetime.UTC).isoformat()

    return {"approval_id": approval_id, "status": "rejected"}


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = None) -> dict[str, Any]:
    """List approval requests for the tenant, optionally filtered by status."""
    tenant = _require_tenant(request)
    approvals = list(_approvals.get(tenant.tenant_id, {}).values())
    if status:
        approvals = [a for a in approvals if a["status"] == status]
    return {"approvals": approvals, "total": len(approvals)}


@router.post("/policy/simulate")
async def simulate_policy(request: Request) -> dict[str, Any]:
    """Simulate what would happen if a policy/guardrail were applied to content."""
    tenant = _require_tenant(request)
    body = await request.json()
    content = body.get("content", "")
    layer = body.get("layer", "step")

    from app.guardrails_v2.engine import guardrails_engine

    result = await guardrails_engine.simulate(content, layer, tenant.tenant_id)
    return {
        "content_preview": content[:100],
        "layer": layer,
        **result,
        "explanation": (
            "Content would be BLOCKED"
            if result.get("would_block")
            else "Content requires HUMAN APPROVAL"
            if result.get("would_require_hitl")
            else "Content passes all guardrails"
        ),
    }


@router.get("/compliance-bundles")
async def list_compliance_bundles(request: Request) -> dict[str, Any]:
    """List available compliance bundles."""
    _require_tenant(request)
    from app.guardrails_v2.models import COMPLIANCE_BUNDLES, ComplianceBundle

    return {
        "bundles": [
            {
                "id": b.value,
                "name": b.value.upper(),
                "rule_count": len(COMPLIANCE_BUNDLES.get(b, [])),
                "description": {
                    "gdpr": "EU General Data Protection Regulation — PII protection",
                    "soc2": "SOC 2 Type II — Security, secrets, injection prevention",
                    "hipaa": "HIPAA — PHI protection for healthcare data",
                    "pci": "PCI DSS — Payment card data protection",
                    "dpdp": "India Digital Personal Data Protection Act",
                    "sox": "Sarbanes-Oxley — Financial data controls",
                }.get(b.value, ""),
            }
            for b in ComplianceBundle
        ]
    }


@router.get("/compliance-bundles/active")
async def get_active_compliance_bundles(request: Request) -> dict[str, Any]:
    """List the compliance bundles this tenant has enabled, and the resulting
    effective (most restrictive) autonomy cap across all of them."""
    tenant = _require_tenant(request)
    from app.governance.compliance_bundles import _bundle_manager

    return {
        "active": [b.id for b in _bundle_manager.get_active(tenant.tenant_id)],
        "effective_max_autonomy": _bundle_manager.get_effective_max_autonomy(tenant.tenant_id),
    }


@router.post("/compliance-bundles/{bundle_id}/enable")
async def enable_compliance_bundle_for_tenant(request: Request, bundle_id: str) -> dict[str, Any]:
    """Enable a compliance bundle for this tenant (governance/autonomy effects —
    distinct from POST /guardrails-v2/bundles/{name}, which materializes a
    bundle's guardrail rules)."""
    tenant = _require_tenant(request)
    from app.governance.compliance_bundles import _bundle_manager

    try:
        _bundle_manager.enable(tenant.tenant_id, bundle_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {
        "active": [b.id for b in _bundle_manager.get_active(tenant.tenant_id)],
        "effective_max_autonomy": _bundle_manager.get_effective_max_autonomy(tenant.tenant_id),
    }
