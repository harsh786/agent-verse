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

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.observability.logging import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflow-versions"])


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "workflow_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Workflow service not available")
    return svc


def _tenant_id(request: Request) -> str:
    return request.app.state.tenant_context.tenant_id


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ApprovalDecisionRequest(BaseModel):
    note: str = ""
    approver_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/versions")
async def list_versions(workflow_id: str, request: Request) -> list[dict[str, Any]]:
    """List all saved versions of a workflow definition."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    versions = await svc.list_versions(tenant_id=tenant_id, workflow_id=workflow_id)
    return versions


@router.get("/{workflow_id}/versions/{version}")
async def get_version(
    workflow_id: str, version: int, request: Request
) -> dict[str, Any]:
    """Get a specific workflow version."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    ver = await svc.get_version(
        tenant_id=tenant_id, workflow_id=workflow_id, version=version
    )
    if ver is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return ver


@router.post(
    "/{workflow_id}/versions/{version}/restore",
    status_code=status.HTTP_200_OK,
)
async def restore_version(
    workflow_id: str, version: int, request: Request
) -> dict[str, Any]:
    """Restore a workflow to a specific historical version (creates new draft)."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.restore_version(
            tenant_id=tenant_id, workflow_id=workflow_id, version=version
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/{workflow_id}/versions/{version_a}/diff/{version_b}")
async def diff_versions(
    workflow_id: str,
    version_a: int,
    version_b: int,
    request: Request,
) -> dict[str, Any]:
    """Return a semantic diff between two versions.

    Response shape:
      {
        "added_steps": [...],
        "removed_steps": [...],
        "modified_steps": [...],
        "trigger_changed": bool,
        "input_changes": [...],
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
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/submit-for-approval", status_code=status.HTTP_202_ACCEPTED)
async def submit_for_approval(workflow_id: str, request: Request) -> dict[str, Any]:
    """Submit a draft workflow for publish approval (enterprise feature)."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.submit_for_approval(
            tenant_id=tenant_id, workflow_id=workflow_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.post("/{workflow_id}/approve-publish", status_code=status.HTTP_200_OK)
async def approve_publish(
    workflow_id: str, body: ApprovalDecisionRequest, request: Request
) -> dict[str, Any]:
    """Approve and publish a submitted workflow."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.approve_publish(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            approver_id=body.approver_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.post("/{workflow_id}/reject-publish", status_code=status.HTTP_200_OK)
async def reject_publish(
    workflow_id: str, body: ApprovalDecisionRequest, request: Request
) -> dict[str, Any]:
    """Reject a publish submission and return to draft."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    try:
        result = await svc.reject_publish(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            approver_id=body.approver_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# YAML import / export
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/yaml")
async def export_yaml(workflow_id: str, request: Request) -> str:
    """Export the current workflow definition as canonical YAML."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    item = await svc.get(tenant_id=tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    from app.workflow.dsl import WorkflowDefinition
    raw_def = item.definition if hasattr(item, "definition") else item["definition"]
    wf = WorkflowDefinition(**raw_def)
    return wf.to_yaml()


@router.post("/import-yaml", status_code=status.HTTP_201_CREATED)
async def import_yaml(request: Request) -> dict[str, Any]:
    """Import a workflow definition from a YAML body."""
    import yaml as _yaml  # type: ignore[import]
    body_bytes = await request.body()
    try:
        data = _yaml.safe_load(body_bytes.decode())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    from app.workflow.dsl import WorkflowDefinition
    try:
        wf = WorkflowDefinition(**data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid workflow DSL: {exc}") from exc

    svc = _svc(request)
    tenant_id = _tenant_id(request)
    result = await svc.create(
        tenant_id=tenant_id,
        name=wf.name,
        description=wf.description,
        definition=data,
        labels={},
    )
    return result


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Create a copy of a workflow definition as a new draft."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    original = await svc.get(tenant_id=tenant_id, workflow_id=workflow_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    name = (original.name if hasattr(original, "name") else original["name"]) + " (copy)"  # type: ignore[union-attr]
    definition = original.definition if hasattr(original, "definition") else original["definition"]  # type: ignore[union-attr]
    result = await svc.create(
        tenant_id=tenant_id,
        name=name,
        description="",
        definition=definition,
        labels={},
    )
    return result
