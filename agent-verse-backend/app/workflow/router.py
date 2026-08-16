"""Workflow Automation Engine — main API router.

Endpoints:
  POST   /workflows                     Create workflow definition
  GET    /workflows                     List workflows (paginated)
  GET    /workflows/{id}                Get workflow definition
  PATCH  /workflows/{id}               Update (draft only)
  DELETE /workflows/{id}               Archive workflow
  POST   /workflows/{id}/publish        Publish → activates triggers
  POST   /workflows/{id}/unpublish      Unpublish → deactivates triggers
  POST   /workflows/{id}/trigger        Manually trigger a run
  GET    /workflows/{id}/runs           List runs for a workflow
  POST   /workflows/nl-trigger-preview  Preview NL trigger parsing (no save)
  POST   /workflows/{id}/validate       Validate DSL without saving
  POST   /workflows/{id}/test           Run in sandbox (no real tools)
  GET    /workflows/{id}/versions       List definition versions
  POST   /workflows/{id}/versions/{v}/restore  Restore a version
  GET    /workflows/{id}/permissions    Get ACL
  PUT    /workflows/{id}/permissions    Replace ACL
  POST   /workflows/{id}/permissions    Add permission
  DELETE /workflows/{id}/permissions/{pid}  Remove permission
  GET    /workflows/templates           List system templates
  GET    /workflows/templates/{slug}    Get a template
  POST   /workflows/templates/{slug}/instantiate  Create workflow from template
  GET    /workflows/analytics/summary   Tenant-level analytics
  GET    /workflows/{id}/analytics      Per-workflow analytics
  GET    /workflows/{id}/webhooks       List webhook events
  GET    /workflows/marketplace         Public marketplace listing
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from app.observability.logging import get_logger
from app.workflow.dsl import WorkflowDefinition
from app.workflow.runner import WorkflowValidationError

_log = get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_tenant(request: Request) -> Any:
    return request.app.state.tenant_context  # set by TenantMiddleware


def _get_workflow_service(request: Request) -> Any:
    return getattr(request.app.state, "workflow_service", None)


def _get_runner(request: Request) -> Any:
    return getattr(request.app.state, "workflow_runner", None)


def _get_nl_resolver(request: Request) -> Any:
    return getattr(request.app.state, "nl_trigger_resolver", None)


def _svc(request: Request) -> Any:
    svc = _get_workflow_service(request)
    if svc is None:
        raise HTTPException(status_code=503, detail="Workflow service not available")
    return svc


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    definition: dict[str, Any] = Field(...)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_no_special(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class WorkflowUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    definition: dict[str, Any] | None = None
    labels: dict[str, str] | None = None


class TriggerRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    dry_run: bool = False
    callback_url: str | None = None


class NLTriggerPreviewRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000)


class PermissionRequest(BaseModel):
    subject: str = Field(..., description="user_id or api_key_id")
    role: str = Field(..., pattern="^(viewer|editor|runner|admin)$")


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str  # draft | published | archived
    version: int
    labels: dict[str, str]
    created_at: datetime
    updated_at: datetime
    trigger_type: str | None = None

    model_config = {"from_attributes": True}


class RunResponse(BaseModel):
    run_id: str
    workflow_id: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedWorkflows(BaseModel):
    items: list[WorkflowResponse]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Endpoints — CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkflowResponse)
async def create_workflow(
    body: WorkflowCreateRequest,
    request: Request,
) -> Any:
    """Create a new workflow definition (starts as draft)."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        WorkflowDefinition(**body.definition)  # validate DSL
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid workflow DSL: {exc}") from exc
    try:
        result = await svc.create(
            tenant_id=tenant.tenant_id,
            name=body.name,
            description=body.description,
            definition=body.definition,
            labels=body.labels,
        )
        return result
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=PaginatedWorkflows)
async def list_workflows(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    label_filter: str | None = Query(None, alias="label"),
) -> Any:
    """List workflow definitions for the current tenant."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    items, total = await svc.list(
        tenant_id=tenant.tenant_id,
        page=page,
        per_page=per_page,
        status_filter=status_filter,
        label_filter=label_filter,
    )
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return item


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdateRequest,
    request: Request,
) -> Any:
    """Update a draft workflow. Published workflows must be unpublished first."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    if body.definition is not None:
        try:
            WorkflowDefinition(**body.definition)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid workflow DSL: {exc}") from exc
    try:
        result = await svc.update(
            tenant_id=tenant.tenant_id,
            workflow_id=workflow_id,
            updates=body.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return result


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: str, request: Request) -> None:
    """Archive a workflow (soft delete)."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    ok = await svc.archive(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Workflow not found")


# ---------------------------------------------------------------------------
# Lifecycle — publish / unpublish
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/publish", response_model=WorkflowResponse)
async def publish_workflow(workflow_id: str, request: Request) -> Any:
    """Validate DSL and activate cron/webhook triggers."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        result = await svc.publish(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return result


@router.post("/{workflow_id}/unpublish", response_model=WorkflowResponse)
async def unpublish_workflow(workflow_id: str, request: Request) -> Any:
    """Deactivate triggers; running executions are unaffected."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    result = await svc.unpublish(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@router.post(
    "/{workflow_id}/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResponse,
)
async def trigger_workflow(
    workflow_id: str,
    body: TriggerRequest,
    request: Request,
) -> Any:
    """Manually trigger a workflow run."""
    runner = _get_runner(request)
    if runner is None:
        raise HTTPException(status_code=503, detail="Workflow runner not available")
    tenant = _get_tenant(request)
    try:
        run = await runner.trigger(
            workflow_id=workflow_id,
            tenant_id=tenant.tenant_id,
            inputs=body.inputs,
            idempotency_key=body.idempotency_key,
            dry_run=body.dry_run,
            callback_url=body.callback_url,
        )
        return run
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _log.error("trigger_failed", workflow_id=workflow_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Trigger failed") from exc


# ---------------------------------------------------------------------------
# Validation & testing
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/validate", status_code=status.HTTP_200_OK)
async def validate_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Validate the current DSL without running it."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    errors: list[str] = []
    try:
        WorkflowDefinition(**item.definition if hasattr(item, "definition") else item["definition"])
    except Exception as exc:
        errors.append(str(exc))
    return {"valid": not errors, "errors": errors}


@router.post(
    "/{workflow_id}/test",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResponse,
)
async def test_workflow(
    workflow_id: str,
    body: TriggerRequest,
    request: Request,
) -> Any:
    """Run workflow in sandbox mode (tools are mocked, no side effects)."""
    runner = _get_runner(request)
    if runner is None:
        raise HTTPException(status_code=503, detail="Workflow runner not available")
    tenant = _get_tenant(request)
    try:
        run = await runner.trigger(
            workflow_id=workflow_id,
            tenant_id=tenant.tenant_id,
            inputs=body.inputs,
            dry_run=True,  # force sandbox
            callback_url=body.callback_url,
        )
        return run
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# NL trigger preview
# ---------------------------------------------------------------------------


@router.post("/nl-trigger-preview", status_code=status.HTTP_200_OK)
async def nl_trigger_preview(body: NLTriggerPreviewRequest, request: Request) -> dict[str, Any]:
    """Parse a natural-language trigger description and return the TriggerDefinition."""
    resolver = _get_nl_resolver(request)
    if resolver is None:
        raise HTTPException(status_code=503, detail="NL trigger resolver not configured")
    try:
        trigger = await resolver.resolve(body.description)
        return {"trigger": trigger.model_dump(), "description": body.description}
    except Exception as exc:  # NLTriggerParseError
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/versions")
async def list_versions(workflow_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    return await svc.list_versions(tenant_id=tenant.tenant_id, workflow_id=workflow_id)


@router.post("/{workflow_id}/versions/{version}/restore", response_model=WorkflowResponse)
async def restore_version(
    workflow_id: str, version: int, request: Request
) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        result = await svc.restore_version(
            tenant_id=tenant.tenant_id, workflow_id=workflow_id, version=version
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# Permissions / ACL
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/permissions")
async def get_permissions(workflow_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    return await svc.get_permissions(tenant_id=tenant.tenant_id, workflow_id=workflow_id)


@router.post("/{workflow_id}/permissions", status_code=status.HTTP_201_CREATED)
async def add_permission(
    workflow_id: str, body: PermissionRequest, request: Request
) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    perm = await svc.add_permission(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        subject=body.subject,
        role=body.role,
    )
    return perm


@router.delete("/{workflow_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_permission(
    workflow_id: str, permission_id: str, request: Request
) -> None:
    svc = _svc(request)
    tenant = _get_tenant(request)
    ok = await svc.remove_permission(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        permission_id=permission_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Permission not found")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/templates", tags=["workflow-templates"])
async def list_templates(
    request: Request,
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    svc = _svc(request)
    items, total = await svc.list_templates(category=category, page=page, per_page=per_page)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/templates/{slug}", tags=["workflow-templates"])
async def get_template(slug: str, request: Request) -> Any:
    svc = _svc(request)
    item = await svc.get_template(slug=slug)
    if item is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return item


@router.post(
    "/templates/{slug}/instantiate",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowResponse,
)
async def instantiate_template(
    slug: str,
    body: dict[str, Any] = Body(default_factory=dict),
    request: Request = None,  # type: ignore[assignment]
) -> Any:
    """Create a new workflow from a system template."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        result = await svc.instantiate_template(
            tenant_id=tenant.tenant_id, slug=slug, overrides=body
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/summary", tags=["workflow-analytics"])
async def analytics_summary(
    request: Request,
    days: int = Query(7, ge=1, le=90),
) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    return await svc.analytics_summary(tenant_id=tenant.tenant_id, days=days)


@router.get("/{workflow_id}/analytics", tags=["workflow-analytics"])
async def workflow_analytics(
    workflow_id: str,
    request: Request,
    days: int = Query(7, ge=1, le=90),
) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    return await svc.workflow_analytics(
        tenant_id=tenant.tenant_id, workflow_id=workflow_id, days=days
    )


# ---------------------------------------------------------------------------
# Webhook events
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/webhooks", tags=["workflow-webhooks"])
async def list_webhook_events(
    workflow_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    items, total = await svc.list_webhook_events(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        page=page,
        per_page=per_page,
    )
    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------


@router.get("/marketplace", tags=["workflow-marketplace"])
async def marketplace(
    request: Request,
    category: str | None = Query(None),
    q: str | None = Query(None, description="Search query"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> Any:
    svc = _svc(request)
    items, total = await svc.marketplace_list(
        category=category, q=q, page=page, per_page=per_page
    )
    return {"items": items, "total": total, "page": page, "per_page": per_page}
