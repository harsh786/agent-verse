"""Trust and Governance 2.0 API - audit integrity, policy simulation, evidence export."""

from __future__ import annotations

import datetime
import hashlib
import inspect
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


# Legacy in-process fallback, used only when no durable store is wired (tests and
# single-process dev). Production swaps in `app.state.trust_approval_store`
# during lifespan — see app/governance/trust_approval_store.py. Before that store
# existed these dicts WERE production: approvals vanished on every restart and an
# approval granted on one replica did not exist for the next request served
# elsewhere.
_approvals: dict[str, dict] = {}  # tenant → {approval_id: approval}
_approval_delegations: dict[str, list] = {}  # approval_id → list of approvers


def _approval_store(request: Request) -> Any:
    """Durable approval store (DB-backed in prod). None → in-memory fallback."""
    return getattr(request.app.state, "trust_approval_store", None)


@router.get("/audit/integrity")
async def get_audit_integrity(request: Request) -> dict[str, Any]:
    """Check audit log hash chain integrity.

    This endpoint is a tamper-detection attestation, so it must never claim a
    chain was verified when no verification happened. It previously fell through
    to a hardcoded ``{"status": "ok", "verified": True, "chain_tip_hash":
    sha256(tenant_id + ":chain-tip")}`` — a "chain tip" derived from nothing but
    the tenant id — in two situations that were both permanent:

      * ``app.state.audit_log`` is an ``AuditLog`` (app/governance/audit.py),
        which has no ``verify_chain`` at all, so the ``hasattr`` guard never
        passed; and
      * even where a verifier existed, the call was ``await
        audit_svc.verify_chain(...)`` while ``AuditV3.verify_chain`` is a plain
        ``def`` returning a dict — awaiting a dict raises TypeError, which the
        bare ``except Exception: pass`` swallowed straight into the same fake
        "verified" response.

    So a broken chain and an unwired service were both reported as intact.
    """
    tenant = _require_tenant(request)

    # Prefer an explicitly wired chain verifier; only fall back to the audit
    # service when it genuinely exposes verify_chain.
    candidates = (
        getattr(request.app.state, "audit_v3", None),
        getattr(request.app.state, "audit_log", None),
    )
    target = next((c for c in candidates if c is not None and hasattr(c, "verify_chain")), None)

    if target is None:
        return {
            "status": "unavailable",
            "verified": False,
            "chain_tip_hash": None,
            "events_verified": 0,
            "tampered_event": None,
            "message": "Audit chain verification is not available on this deployment.",
        }

    try:
        result: Any = target.verify_chain(tenant.tenant_id)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        # An integrity check that errored is not an integrity guarantee.
        return {
            "status": "error",
            "verified": False,
            "chain_tip_hash": None,
            "events_verified": 0,
            "tampered_event": None,
            "message": f"Audit chain verification failed: {exc}",
        }

    if isinstance(result, bool):
        result = {"valid": result}
    if not isinstance(result, dict):
        result = {"valid": False, "reason": f"unexpected verifier result: {type(result).__name__}"}

    valid = bool(result.get("valid", result.get("verified", False)))
    return {
        "status": "ok" if valid else "tampered",
        "verified": valid,
        "chain_tip_hash": result.get("chain_tip_hash"),
        "events_verified": int(
            result.get("records_checked", result.get("verified_events", 0)) or 0
        ),
        "tampered_event": result.get("broken_at", result.get("broken_chain_at")),
        "message": (
            "Chain intact" if valid else (result.get("reason") or "Chain integrity check failed")
        ),
    }


@router.get("/audit/export")
async def export_audit_evidence(request: Request) -> Any:
    """Export audit evidence as an integrity-hashed JSON package.

    Note on `integrity_hash`: it is an UNKEYED SHA-256 over the events. It
    detects accidental corruption in transit, but it is not a signature —
    anyone who alters the events can recompute it. This endpoint previously
    described the result as a "signed" package, which overstated that.

    An unavailable or failing audit source is now an error rather than an empty
    package. Returning `{"event_count": 0, "events": []}` when the audit log
    simply could not be read is indistinguishable from "this tenant genuinely
    had no audit events", and the difference matters when the file is filed as
    compliance evidence.
    """
    tenant = _require_tenant(request)

    audit_svc = getattr(request.app.state, "audit_log", None)
    if audit_svc is None or not hasattr(audit_svc, "query"):
        raise HTTPException(
            503, "audit log unavailable; refusing to emit an empty evidence package"
        )
    try:
        result = await audit_svc.query(tenant_id=tenant.tenant_id, limit=1000)
    except Exception as exc:
        raise HTTPException(
            503, f"audit log query failed; refusing to emit an empty package: {exc}"
        ) from exc
    events = result.get("events", []) if isinstance(result, dict) else []

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
    store = _approval_store(request)
    if store is not None:
        await store.create(
            tenant_id=tenant.tenant_id,
            approval_id=approval_id,
            goal_id=approval["goal_id"],
            step_description=approval["step_description"],
            tool_name=approval["tool_name"],
            risk_level=approval["risk_level"],
            required_approvers=approval["required_approvers"],
        )
    else:
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
    approver_id = body.get("approver_id", "anonymous")
    note = body.get("note", "")

    store = _approval_store(request)
    if store is not None:
        from app.governance.trust_approval_store import (
            ApprovalNotFoundError,
            ApprovalNotPendingError,
            DuplicateApproverError,
        )

        try:
            refreshed = await store.add_vote(
                tenant_id=tenant.tenant_id,
                approval_id=approval_id,
                approver_id=approver_id,
                note=note,
            )
        except ApprovalNotFoundError:
            raise HTTPException(404, "Approval not found") from None
        except ApprovalNotPendingError as exc:
            raise HTTPException(400, f"Approval is already {exc.status}") from None
        except DuplicateApproverError:
            raise HTTPException(
                409, "approver has already approved this request"
            ) from None
        return {
            "approval_id": approval_id,
            "status": refreshed["status"],
            "approver_count": len(refreshed["approvers"]),
            "required": refreshed["required_approvers"],
        }

    approval = _approvals.get(tenant.tenant_id, {}).get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval["status"] != "pending":
        raise HTTPException(400, f"Approval is already {approval['status']}")

    # Separation of duties: `required_approvers` counts DISTINCT approvers.
    # Without this guard one person calling the endpoint N times — or a
    # double-clicked / retried request — satisfied an N-approver requirement
    # alone, since the completion check is `len(approvers) >= required`.
    if any(a.get("approver_id") == approver_id for a in approval["approvers"]):
        raise HTTPException(409, "approver has already approved this request")

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

    store = _approval_store(request)
    if store is not None:
        from app.governance.trust_approval_store import ApprovalNotFoundError

        try:
            await store.reject(
                tenant_id=tenant.tenant_id,
                approval_id=approval_id,
                reason=body.get("reason", ""),
                rejected_by=body.get("approver_id", "anonymous"),
            )
        except ApprovalNotFoundError:
            raise HTTPException(404, "Approval not found") from None
        return {"approval_id": approval_id, "status": "rejected"}

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
    store = _approval_store(request)
    if store is not None:
        approvals = await store.list(tenant.tenant_id, status)
        return {"approvals": approvals, "total": len(approvals)}

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
