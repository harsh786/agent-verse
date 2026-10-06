"""Version history API endpoints.

Endpoints:
  GET    /workflows/{id}/versions                Get version list
  GET    /workflows/{id}/versions/{ver}          Get a specific version
  POST   /workflows/{id}/versions/{ver}/restore  Restore to a previous version
  GET    /workflows/{id}/versions/{ver_a}/diff/{ver_b}  Diff two versions
  POST   /workflows/{id}/submit-for-approval     Submit draft for publish approval
  POST   /workflows/{id}/approve-publish         Approve a submitted workflow
  POST   /workflows/{id}/reject-publish          Reject a submitted workflow
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.workflow.permissions import workflow_access
from app.workflow.runner import WorkflowValidationError
from app.workflow.service import request_plan

_log = get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflow-versions"])

# Per-workflow ACL (app/workflow/permissions.py).
_CAN_VIEW = [Depends(workflow_access("viewer"))]
_CAN_RUN = [Depends(workflow_access("runner"))]
_CAN_EDIT = [Depends(workflow_access("editor"))]
_CAN_ADMIN = [Depends(workflow_access("admin"))]


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "workflow_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Workflow service not available")
    return svc


def _tenant_id(request: Request) -> str:
    # Prefer the per-request tenant set by TenantMiddleware; fall back to the
    # app-state context (used in some test harnesses). Nothing sets
    # app.state.tenant_context in production, so the per-request path is normal.
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Tenant context not resolved")
    return tenant.tenant_id


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ApprovalDecisionRequest(BaseModel):
    note: str = ""
    # Ignored: the approver is always the authenticated caller. Kept so older
    # clients that send it are not rejected.
    approver_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{workflow_id}/versions",
    operation_id="workflow_versions_list",
    dependencies=_CAN_VIEW,
)
async def list_versions(workflow_id: str, request: Request) -> list[dict[str, Any]]:
    """List all saved versions of a workflow definition."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    versions = await svc.list_versions(tenant_id=tenant_id, workflow_id=workflow_id)
    return versions


@router.get("/{workflow_id}/versions/{version}", dependencies=_CAN_VIEW)
async def get_version(workflow_id: str, version: int, request: Request) -> dict[str, Any]:
    """Get a specific workflow version."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    ver = await svc.get_version(tenant_id=tenant_id, workflow_id=workflow_id, version=version)
    if ver is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _masked_version(ver)


def _masked(item: Any) -> Any:
    """B2-OPEN-1: a workflow dict with its webhook ``hmac_secret`` masked."""
    from app.workflow.webhook_secrets import redact_workflow

    return redact_workflow(item)


def _masked_version(ver: dict[str, Any]) -> dict[str, Any]:
    """A version with its secret masked in both the JSON and the YAML copy
    (snapshots recorded before B2-OPEN-1 hold it in their YAML too)."""
    import yaml as _yaml  # type: ignore[import-untyped]

    out: dict[str, Any] = _masked(ver)
    if out is not ver and out.get("definition_yaml"):
        out["definition_yaml"] = _yaml.safe_dump(out["definition"], sort_keys=False)
    return out


@router.post(
    "/{workflow_id}/versions/{version}/restore",
    status_code=status.HTTP_200_OK,
    operation_id="workflow_versions_restore",
    dependencies=_CAN_EDIT,
)
async def restore_version(workflow_id: str, version: int, request: Request) -> dict[str, Any]:
    """Restore a workflow to a specific historical version (creates new draft)."""
    from app.workflow.service import WorkflowPublishedError

    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.restore_version(
            tenant_id=tenant_id, workflow_id=workflow_id, version=version
        )
    except WorkflowPublishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from app.workflow.audit_middleware import record_workflow_action

    record_workflow_action(
        request, "version_restored", workflow_id=workflow_id, note=f"version={version}"
    )
    return _masked(result)  # type: ignore[no-any-return]


@router.get("/{workflow_id}/versions/{version_a}/diff/{version_b}", dependencies=_CAN_VIEW)
async def diff_versions(
    workflow_id: str,
    version_a: int,
    version_b: int,
    request: Request,
) -> dict[str, Any]:
    """Return a semantic diff between two recorded versions.

    Response shape:
      {
        "version_a": "...", "version_b": "...",
        "added_steps": [step ids], "removed_steps": [...], "modified_steps": [...],
        "trigger_changed": bool,
        "input_changes": [{"name": ..., "change": "added|removed|modified"}],
      }
    """
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        diff = await svc.diff_versions(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            version_a=version_a,
            version_b=version_b,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return diff


# ---------------------------------------------------------------------------
# Publishing approval workflow (for requires_publish_approval=True)
#
# Enable it with PATCH /workflows/{id} {"requires_publish_approval": true}; a
# direct POST /publish is then refused (409). The submitter and the approver
# are always the authenticated caller (never a body field), and the submitter
# cannot approve their own request (four-eyes).
# ---------------------------------------------------------------------------


def _principal(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    return str(getattr(tenant, "api_key_id", "") or "")


async def _approval_call(
    request: Request, workflow_id: str, action: str, coro: Any, *, note: str = ""
) -> dict[str, Any]:
    from app.workflow.audit_middleware import record_workflow_action
    from app.workflow.service import WorkflowPersistenceUnavailableError

    try:
        result = await coro
    except WorkflowPersistenceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        record_workflow_action(
            request, action, workflow_id=workflow_id, outcome="denied", note=str(exc)
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    approver = _principal(request) if action != "publish_submitted" else None
    record_workflow_action(
        request,
        action,
        workflow_id=workflow_id,
        approver=approver,
        note=f"version={result.get('version')}" + (f"; note={note}" if note else ""),
    )
    return dict(_masked(dict(result)))


@router.post(
    "/{workflow_id}/submit-for-approval",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_CAN_EDIT,
)
async def submit_for_approval(workflow_id: str, request: Request) -> dict[str, Any]:
    """Submit a draft workflow for publish approval (enterprise feature)."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await _approval_call(
        request,
        workflow_id,
        "publish_submitted",
        svc.submit_for_approval(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            submitted_by=_principal(request),
            plan=request_plan(request),
        )
    )


@router.post(
    "/{workflow_id}/approve-publish",
    status_code=status.HTTP_200_OK,
    dependencies=_CAN_ADMIN,
)
async def approve_publish(
    workflow_id: str, body: ApprovalDecisionRequest, request: Request
) -> dict[str, Any]:
    """Approve and publish a submitted workflow (the caller is the approver)."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await _approval_call(
        request,
        workflow_id,
        "publish_approved",
        svc.approve_publish(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            approver_id=_principal(request),
            note=body.note,
            plan=request_plan(request),
        ),
        note=body.note,
    )


@router.post(
    "/{workflow_id}/reject-publish",
    status_code=status.HTTP_200_OK,
    dependencies=_CAN_ADMIN,
)
async def reject_publish(
    workflow_id: str, body: ApprovalDecisionRequest, request: Request
) -> dict[str, Any]:
    """Reject a publish submission and return to draft."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await _approval_call(
        request,
        workflow_id,
        "publish_rejected",
        svc.reject_publish(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            approver_id=_principal(request),
            note=body.note,
        ),
        note=body.note,
    )


# ---------------------------------------------------------------------------
# YAML import / export
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/yaml", dependencies=_CAN_VIEW)
async def export_yaml(workflow_id: str, request: Request) -> str:
    """Export the current workflow definition as canonical YAML."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    item = await svc.get(tenant_id=tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    from app.workflow.dsl import WorkflowDefinition
    from app.workflow.webhook_secrets import redact_definition

    raw_def = item.definition if hasattr(item, "definition") else item["definition"]
    wf = WorkflowDefinition(**redact_definition(raw_def))  # B2-OPEN-1: secret masked
    return wf.to_yaml()


@router.post("/import-yaml", status_code=status.HTTP_201_CREATED)
async def import_yaml(request: Request) -> dict[str, Any]:
    """Import a workflow definition from a YAML body (always a NEW draft workflow).

    Webhook HMAC secrets (B2-GAP-3): an exported YAML carries ``auth: hmac``
    webhooks with the secret masked (``hmac_secret: '********'``) — exports never
    contain it. Importing such a file is refused with **422** naming the fix: put
    a new secret in ``hmac_secret`` (and configure the sender with it), or remove
    ``auth: hmac``. The import is never created with an empty secret (it would
    refuse every delivery), never copies a secret from another workflow, and no
    secret is generated or echoed back. A plaintext ``hmac_secret`` in the YAML
    is stored vault-encrypted and masked in the response.
    """
    import yaml as _yaml  # type: ignore[import]

    body_bytes = await request.body()
    try:
        data = _yaml.safe_load(body_bytes.decode())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    from app.workflow.dsl import WorkflowDefinition
    from app.workflow.webhook_secrets import MASKED_SECRET_DETAIL, has_masked_secret

    try:
        wf = WorkflowDefinition(**data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid workflow DSL: {exc}") from exc
    if has_masked_secret(data):
        raise HTTPException(
            status_code=422, detail=f"Cannot import a masked webhook secret: {MASKED_SECRET_DETAIL}"
        )

    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.create(
            tenant_id=tenant_id,
            name=wf.name,
            description=wf.description,
            definition=data,
            labels={},
            plan=request_plan(request),
        )
    except WorkflowValidationError as exc:  # e.g. below the plan's schedule floor
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.workflow.audit_middleware import record_workflow_created

    record_workflow_created(request, result, name=wf.name, source="import")
    return _masked(result)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/clone", status_code=status.HTTP_201_CREATED, dependencies=_CAN_VIEW)
async def clone_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Create a copy of a workflow definition as a new draft."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    original = await svc.get(tenant_id=tenant_id, workflow_id=workflow_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    name = (original.name if hasattr(original, "name") else original["name"]) + " (copy)"  # type: ignore[union-attr]
    definition = original.definition if hasattr(original, "definition") else original["definition"]  # type: ignore[union-attr]
    try:
        result = await svc.create(
            tenant_id=tenant_id,
            name=name,
            description="",
            definition=definition,
            labels={},
            plan=request_plan(request),
        )
    except WorkflowValidationError as exc:  # e.g. below the plan's schedule floor
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.workflow.audit_middleware import record_workflow_created

    record_workflow_created(
        request, result, name=name, source="clone", cloned_from=workflow_id
    )
    return _masked(result)  # type: ignore[no-any-return]
