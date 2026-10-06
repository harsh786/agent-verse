"""Trust and Governance 2.0 API - audit integrity, policy simulation, evidence export."""

from __future__ import annotations

import datetime
import hashlib
import inspect
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.governance.audit_v2 import audit_admin_action
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/trust", tags=["trust-governance"])


def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


def _acting_principal(tenant: Any, body: dict[str, Any]) -> str:
    """The approver/rejector identity: the authenticated key, never the body.

    ``approver_id`` used to be read from the JSON body (default "anonymous"), so
    one API key could vote as "alice", then "bob", ... — satisfying an
    N-approver requirement alone and writing people who never approved into the
    trail. A body value is still tolerated for backward compatibility, but only
    when it names the caller; anything else is an impersonation attempt.
    """
    principal = str(getattr(tenant, "api_key_id", "") or "")
    if not principal:
        raise HTTPException(403, "Cannot attribute this decision to an authenticated principal")
    claimed = body.get("approver_id")
    if claimed is not None and claimed != principal:
        raise HTTPException(403, "approver_id must match the authenticated principal")
    return principal


def _approval_store(request: Request) -> Any:
    """The approval store wired on app.state, or 503.

    The lifespan wires the Postgres ``TrustApprovalStore``; the in-memory app
    build (``create_app(manage_pools=False)``) wires ``InMemoryTrustApprovalStore``.
    There is no module-dict fallback any more (a03-F057-04): it answered 200
    for approvals that lived in one process's memory, vanished on restart and
    did not exist on the next replica.
    """
    store = getattr(request.app.state, "trust_approval_store", None)
    if store is None:
        raise HTTPException(503, "Approval store unavailable; retry shortly.")
    return store


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
        # The stored (DB) chain when the verifier has one — never this replica's
        # in-memory records presented as the fleet's chain.
        averify = getattr(target, "averify_chain", None)
        result: Any = (
            averify(tenant.tenant_id) if averify is not None else target.verify_chain(
                tenant.tenant_id
            )
        )
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


def _audit_event_dict(event: Any) -> dict[str, Any]:
    import dataclasses
    import enum

    is_instance = dataclasses.is_dataclass(event) and not isinstance(event, type)
    raw = dataclasses.asdict(event) if is_instance else dict(event)
    return {k: (v.value if isinstance(v, enum.Enum) else v) for k, v in raw.items()}


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
    if audit_svc is None or not hasattr(audit_svc, "query_db"):
        raise HTTPException(
            503, "audit log unavailable; refusing to emit an empty evidence package"
        )
    # This awaited the SYNC ``AuditLog.query(tenant_id=...)`` — wrong signature
    # (it takes tenant_ctx) and not awaitable — so the export was always a 503.
    try:
        rows = await audit_svc.query_db(tenant_ctx=tenant, limit=1000)
    except Exception as exc:
        raise HTTPException(
            503, f"audit log query failed; refusing to emit an empty package: {exc}"
        ) from exc
    events = [_audit_event_dict(e) for e in rows]

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

    store = _approval_store(request)
    approval_id = str(uuid.uuid4())
    required = body.get("required_approvers", 1)
    await store.create(
        tenant_id=tenant.tenant_id,
        approval_id=approval_id,
        goal_id=body.get("goal_id"),
        step_description=body.get("step_description", ""),
        tool_name=body.get("tool_name", ""),
        risk_level=body.get("risk_level", "high"),
        required_approvers=required,
    )
    return {"approval_id": approval_id, "status": "pending", "required_approvers": required}


@router.post("/approvals/{approval_id}/approve")
async def approve_request(
    request: Request,
    approval_id: str,
    # Any key could vote (as itself) — a viewer key counted toward an N-approver
    # quorum. Same role as /governance/approvals.
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Approve a pending request. Supports multi-approver."""
    tenant = _require_tenant(request)
    body = await request.json()
    approver_id = _acting_principal(tenant, body)
    note = body.get("note", "")

    store = _approval_store(request)
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


@router.post("/approvals/{approval_id}/reject")
async def reject_request(
    request: Request,
    approval_id: str,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Reject a pending request."""
    tenant = _require_tenant(request)
    body = await request.json()
    rejected_by = _acting_principal(tenant, body)

    store = _approval_store(request)
    from app.governance.trust_approval_store import ApprovalNotFoundError

    try:
        await store.reject(
            tenant_id=tenant.tenant_id,
            approval_id=approval_id,
            reason=body.get("reason", ""),
            rejected_by=rejected_by,
        )
    except ApprovalNotFoundError:
        raise HTTPException(404, "Approval not found") from None
    return {"approval_id": approval_id, "status": "rejected"}


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = None) -> dict[str, Any]:
    """List approval requests for the tenant, optionally filtered by status."""
    tenant = _require_tenant(request)
    approvals = await _approval_store(request).list(tenant.tenant_id, status)
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


# The guardrails-v2 rule bundle (POST /guardrails-v2/bundles/{name}) that holds
# the content rules for each governance bundle; the ids differ for two of them.
_GUARDRAIL_BUNDLE_FOR: dict[str, str] = {
    "hipaa": "hipaa",
    "gdpr": "gdpr",
    "soc2": "soc2",
    "pci_dss": "pci",
    "india_dpdp": "dpdp",
}


@router.get("/compliance-bundles")
async def list_compliance_bundles(request: Request) -> dict[str, Any]:
    """List the compliance bundles this tenant can enable here.

    a03-F057-03: this listed the guardrails-v2 rule catalogue (ids ``pci``,
    ``dpdp``, ``sox``) while enable/disable below use
    ``app.governance.compliance_bundles`` (``pci_dss``, ``india_dpdp``, no SOX),
    so two listed ids answered 400 on enable and the listed facts were not the
    ones enabling applies. Each entry is now the governance bundle itself, with
    the guardrails-v2 rule bundle that carries its content rules, if any.
    """
    _require_tenant(request)
    from app.governance.compliance_bundles import COMPLIANCE_BUNDLES
    from app.guardrails_v2.models import COMPLIANCE_BUNDLES as GUARDRAIL_RULE_BUNDLES
    from app.guardrails_v2.models import ComplianceBundle as GuardrailBundle

    bundles = []
    for bundle in COMPLIANCE_BUNDLES.values():
        guardrail_id = _GUARDRAIL_BUNDLE_FOR.get(bundle.id)
        rule_count = (
            len(GUARDRAIL_RULE_BUNDLES.get(GuardrailBundle(guardrail_id), []))
            if guardrail_id
            else 0
        )
        if not rule_count:
            guardrail_id = None  # no preset content rules (e.g. DPDP today)
        bundles.append(
            {
                "id": bundle.id,
                "name": bundle.name,
                "description": bundle.description,
                "max_autonomy_mode": bundle.max_autonomy_mode,
                "required_hitl_for": list(bundle.required_hitl_for),
                "audit_retention_days": bundle.audit_retention_days,
                "data_residency_required": bundle.data_residency_required,
                "guardrail_bundle": guardrail_id,
                "rule_count": rule_count,
            }
        )
    return {"bundles": bundles}


def _bundle_store(request: Request) -> Any:
    """The tenant compliance-bundle store wired onto app.state.

    In-memory before the lifespan runs, ``PostgresComplianceBundleStore`` after
    it — the same two-phase swap as every other service. Reading it from
    app.state (rather than importing the module singleton, as this module used
    to) is what makes an enablement visible to other replicas and to the Celery
    workers that actually execute agents.

    An app assembled without the wiring (a router mounted directly in a test)
    falls back to the process-local manager, which is what ``create_app`` puts on
    app.state anyway before the lifespan upgrades it.
    """
    store = getattr(request.app.state, "compliance_bundle_store", None)
    if store is None:
        from app.governance.compliance_bundles import _bundle_manager

        return _bundle_manager
    return store


async def _bundle_state(request: Request, tenant_id: str) -> dict[str, Any]:
    from app.governance.compliance_bundles import (
        active_bundle_ids_for,
        effective_max_autonomy_for,
    )

    store = _bundle_store(request)
    return {
        "active": list(await active_bundle_ids_for(store, tenant_id)),
        "effective_max_autonomy": await effective_max_autonomy_for(store, tenant_id),
    }


@router.get("/compliance-bundles/active")
async def get_active_compliance_bundles(request: Request) -> dict[str, Any]:
    """List the compliance bundles this tenant has enabled, and the resulting
    effective (most restrictive) autonomy cap across all of them."""
    tenant = _require_tenant(request)
    return await _bundle_state(request, tenant.tenant_id)


@router.post("/compliance-bundles/{bundle_id}/enable")
@audit_admin_action(
    "compliance_bundle.enabled",
    "compliance_bundle",
    "enable",
    extract_resource_id=lambda kw: kw.get("bundle_id"),
)
async def enable_compliance_bundle_for_tenant(
    request: Request,
    bundle_id: str,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Enable a compliance bundle for this tenant (governance/autonomy effects —
    distinct from POST /guardrails-v2/bundles/{name}, which materializes a
    bundle's guardrail rules).

    The resulting ``effective_max_autonomy`` is a real ceiling: every goal this
    tenant submits is clamped to it (see
    ``app.services.goal_service.resolve_effective_autonomy_mode``). It used to be
    a number this endpoint computed and nothing ever read.
    """
    tenant = _require_tenant(request)
    from app.governance.compliance_bundles import enable_bundle

    try:
        await enable_bundle(
            _bundle_store(request),
            tenant.tenant_id,
            bundle_id,
            actor=str(getattr(tenant, "api_key_id", "") or ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return await _bundle_state(request, tenant.tenant_id)


@router.delete("/compliance-bundles/{bundle_id}")
@audit_admin_action(
    "compliance_bundle.disabled",
    "compliance_bundle",
    "disable",
    extract_resource_id=lambda kw: kw.get("bundle_id"),
)
async def disable_compliance_bundle_for_tenant(
    request: Request,
    bundle_id: str,
    # Disabling a bundle lifts its autonomy ceiling (e.g. HIPAA): tenant admins
    # only. Any key, even a viewer's, could do it.
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Disable a compliance bundle for this tenant, lifting its autonomy ceiling."""
    tenant = _require_tenant(request)
    from app.governance.compliance_bundles import disable_bundle

    try:
        await disable_bundle(_bundle_store(request), tenant.tenant_id, bundle_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return await _bundle_state(request, tenant.tenant_id)
