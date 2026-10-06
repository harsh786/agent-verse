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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from app.observability.logging import get_logger
from app.workflow.audit_middleware import record_workflow_action, record_workflow_created
from app.workflow.dsl import WorkflowDefinition
from app.workflow.permissions import caller_access, require_workflow_access, workflow_access
from app.workflow.runner import WorkflowEngineUnavailableError, WorkflowValidationError
from app.workflow.service import request_plan

_log = get_logger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

# Per-workflow ACL (app/workflow/permissions.py).
_CAN_VIEW = [Depends(workflow_access("viewer"))]
_CAN_RUN = [Depends(workflow_access("runner"))]
_CAN_EDIT = [Depends(workflow_access("editor"))]
_CAN_ADMIN = [Depends(workflow_access("admin"))]


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_tenant(request: Request) -> Any:
    # TenantMiddleware sets ``request.state.tenant`` in production; the rest of the
    # workflow package (router_runs / router_templates / router_hitl / router_versions)
    # resolves the tenant from ``app.state.tenant_context``. Prefer the per-request
    # value and fall back to the app-state context so both wirings work; 401 when
    # neither is present rather than returning None (which 500'd downstream).
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Tenant context not resolved")
    return tenant


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
    # true: publishing needs submit-for-approval + approve-publish (four-eyes).
    # Turning it off needs 'admin' access to the workflow.
    requires_publish_approval: bool | None = None


class TriggerRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    dry_run: bool = False
    callback_url: str | None = None


class NLTriggerPreviewRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000)


class PermissionRequest(BaseModel):
    subject: str = Field(..., description="api_key_id (principal) or RBAC role name")
    role: str = Field(..., pattern="^(viewer|editor|runner|admin)$")
    # "role" grants the level to every caller holding that RBAC role.
    subject_type: str = Field("user", pattern="^(user|role)$")


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
    # Cron expression when the workflow's trigger is a schedule (else None), so
    # the UI can badge scheduled workflows with their cadence.
    schedule_cron: str | None = None
    # Populated by /publish for webhook/api-triggered workflows: the stable signed
    # URL an external system (e.g. the onboarding portal) POSTs to in order to
    # start a run by URL. None on draft/list/get responses.
    webhook_url: str | None = None
    webhook_token: str | None = None
    webhook_path: str | None = None

    model_config = {"from_attributes": True}


class WorkflowDetailResponse(WorkflowResponse):
    """Detail view — includes the full ``definition`` (steps/triggers/edges) so the
    builder can render the canvas. The list view intentionally omits it to stay lean.
    """

    definition: dict[str, Any] = Field(default_factory=dict)

    @field_validator("definition")
    @classmethod
    def _mask_webhook_secret(cls, v: dict[str, Any]) -> dict[str, Any]:
        """B2-OPEN-1: a webhook ``hmac_secret`` is never returned (masked)."""
        from app.workflow.webhook_secrets import redact_definition

        out: dict[str, Any] = redact_definition(v)
        return out

    # The caller's level on this workflow (viewer | runner | editor | admin) so
    # the UI can disable what the per-workflow ACL would refuse.
    access: str | None = None
    # Whether a publish must go through submit-for-approval / approve-publish.
    requires_publish_approval: bool | None = None


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
            plan=request_plan(request),
        )
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_workflow_created(request, result, name=body.name, source="api")
    return result


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


# NOTE: static paths (/templates, /marketplace) MUST be declared before
# GET /{workflow_id}; declared after it they were matched as a workflow id
# and their handlers were unreachable (404 'Workflow not found').
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
    except WorkflowValidationError as exc:  # e.g. below the plan's schedule floor
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_workflow_created(
        request,
        result,
        name=str(result.get("name", "") if isinstance(result, dict) else ""),
        source="template",
        template=slug,
    )
    return result


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
    items, total = await svc.marketplace_list(category=category, q=q, page=page, per_page=per_page)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse, dependencies=_CAN_VIEW)
async def get_workflow(workflow_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {**item, "access": await caller_access(request, workflow_id)}


@router.patch("/{workflow_id}", response_model=WorkflowDetailResponse, dependencies=_CAN_EDIT)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdateRequest,
    request: Request,
) -> Any:
    """Update a draft workflow. Published workflows must be unpublished first."""
    from app.workflow.service import WorkflowPersistenceUnavailableError

    svc = _svc(request)
    tenant = _get_tenant(request)
    if body.definition is not None:
        try:
            WorkflowDefinition(**body.definition)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid workflow DSL: {exc}") from exc
    updates = body.model_dump(exclude_none=True)
    requires_approval = updates.pop("requires_publish_approval", None)
    if requires_approval is not None:
        if await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id) is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if requires_approval is False:
            # Switching the approval gate OFF is an admin decision on the
            # workflow — an editor could otherwise remove it and self-publish.
            await require_workflow_access(request, workflow_id, "admin")
        try:
            await svc.set_requires_publish_approval(
                tenant_id=tenant.tenant_id, workflow_id=workflow_id, required=requires_approval
            )
        except WorkflowPersistenceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        if updates:
            result = await svc.update(
                tenant_id=tenant.tenant_id,
                workflow_id=workflow_id,
                updates=updates,
                plan=request_plan(request),
            )
        else:
            result = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    changed = sorted(updates)
    if requires_approval is not None:
        changed.append(f"requires_publish_approval={requires_approval}")
        result = {**result, "requires_publish_approval": requires_approval}
    record_workflow_action(
        request, "updated", workflow_id=workflow_id, note="changed=" + ",".join(changed)
    )
    return result


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_CAN_EDIT)
async def delete_workflow(workflow_id: str, request: Request) -> None:
    """Archive a workflow (soft delete)."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    ok = await svc.archive(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Workflow not found")
    record_workflow_action(request, "archived", workflow_id=workflow_id)


# ---------------------------------------------------------------------------
# Lifecycle — publish / unpublish
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/publish", response_model=WorkflowResponse, dependencies=_CAN_EDIT)
async def publish_workflow(workflow_id: str, request: Request) -> Any:
    """Validate DSL and activate cron/webhook triggers."""
    from app.workflow.service import (
        PublishApprovalRequiredError,
        WorkflowPersistenceUnavailableError,
    )

    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        result = await svc.publish(
            tenant_id=tenant.tenant_id,
            workflow_id=workflow_id,
            published_by=str(getattr(tenant, "api_key_id", "") or "") or None,
            plan=request_plan(request),
        )
    except PublishApprovalRequiredError as exc:
        record_workflow_action(
            request, "published", workflow_id=workflow_id, outcome="denied", note=str(exc)
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowPersistenceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    record_workflow_action(
        request, "published", workflow_id=workflow_id, note=f"version={result.get('version')}"
    )
    return result


@router.post("/{workflow_id}/unpublish", response_model=WorkflowResponse, dependencies=_CAN_EDIT)
async def unpublish_workflow(workflow_id: str, request: Request) -> Any:
    """Deactivate triggers; running executions are unaffected."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    result = await svc.unpublish(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    record_workflow_action(request, "unpublished", workflow_id=workflow_id)
    return result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@router.post(
    "/{workflow_id}/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResponse,
    dependencies=_CAN_RUN,
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
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkflowEngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _log.error("trigger_failed", workflow_id=workflow_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Trigger failed") from exc
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    record_workflow_action(
        request,
        "run_triggered",
        workflow_id=workflow_id,
        note=f"run_id={run_id}; dry_run={body.dry_run}",
    )
    return run


# ---------------------------------------------------------------------------
# Validation & testing
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/validate", status_code=status.HTTP_200_OK, dependencies=_CAN_VIEW)
async def validate_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Validate the current DSL without running it."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    errors: list[str] = []
    definition = item.definition if hasattr(item, "definition") else item["definition"]
    try:
        WorkflowDefinition(**definition)
    except Exception as exc:
        errors.append(str(exc))
    # The same checks publish enforces (unsupported triggers, ...).
    from app.workflow.service import publish_problems

    errors.extend(publish_problems(definition or {}, request_plan(request)))
    return {"valid": not errors, "errors": errors}


@router.post(
    "/{workflow_id}/test",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResponse,
    dependencies=_CAN_RUN,
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
        # The LLM fallback is charged to (and budget-checked for) the caller.
        trigger = await resolver.resolve(body.description, tenant_ctx=_get_tenant(request))
        return {"trigger": trigger.model_dump(), "description": body.description}
    except Exception as exc:  # NLTriggerParseError
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


# GET /{workflow_id}/versions and POST .../versions/{version}/restore are served
# by router_versions.py (they used to be declared here too; one copy was dead).


# ---------------------------------------------------------------------------
# Permissions / ACL
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/permissions", dependencies=_CAN_VIEW)
async def get_permissions(workflow_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    return await svc.get_permissions(tenant_id=tenant.tenant_id, workflow_id=workflow_id)


@router.post(
    "/{workflow_id}/permissions",
    status_code=status.HTTP_201_CREATED,
    dependencies=_CAN_ADMIN,
)
async def add_permission(workflow_id: str, body: PermissionRequest, request: Request) -> Any:
    svc = _svc(request)
    tenant = _get_tenant(request)
    try:
        perm = await svc.add_permission(
            tenant_id=tenant.tenant_id,
            workflow_id=workflow_id,
            subject=body.subject,
            role=body.role,
            subject_type=body.subject_type,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Workflow not found") from None
    record_workflow_action(
        request,
        "permission_granted",
        workflow_id=workflow_id,
        note=f"{body.subject_type}={body.subject}; level={body.role}",
    )
    return perm


@router.delete(
    "/{workflow_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CAN_ADMIN,
)
async def remove_permission(workflow_id: str, permission_id: str, request: Request) -> None:
    svc = _svc(request)
    tenant = _get_tenant(request)
    ok = await svc.remove_permission(
        tenant_id=tenant.tenant_id,
        workflow_id=workflow_id,
        permission_id=permission_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Permission not found")
    record_workflow_action(
        request,
        "permission_revoked",
        workflow_id=workflow_id,
        note=f"permission_id={permission_id}",
    )


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


@router.get("/{workflow_id}/analytics", tags=["workflow-analytics"], dependencies=_CAN_VIEW)
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


@router.get("/{workflow_id}/webhooks", tags=["workflow-webhooks"], dependencies=_CAN_VIEW)
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


@router.get("/{workflow_id}/webhook", tags=["workflow-webhooks"], dependencies=_CAN_RUN)
async def get_webhook_trigger(workflow_id: str, request: Request) -> dict[str, Any]:
    """The workflow's real inbound webhook URL (``POST /wf-hooks/{token}``) and
    the key used to sign its run-completion callbacks.

    The UI used to display ``/api/v1/webhooks/workflows/{id}`` — a route that
    does not exist. The token is only issued for a PUBLISHED workflow (the
    webhook endpoint rejects unpublished ones), so it is withheld otherwise.
    """
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    published = item.get("status") == "published"
    out: dict[str, Any] = {"workflow_id": workflow_id, "published": published}
    if not published:
        return out
    from app.workflow.webhook_tokens import callback_signing_secret

    hook = await svc.webhook_trigger(tenant.tenant_id, workflow_id)
    out.update(
        {
            "webhook_path": hook["webhook_path"],
            "webhook_url": hook["webhook_url"],
            "callback_signature_header": "X-AgentVerse-Signature",
            "callback_signing_secret": callback_signing_secret(tenant.tenant_id, workflow_id),
        }
    )
    return out


@router.post(
    "/{workflow_id}/webhook/rotate",
    tags=["workflow-webhooks"],
    dependencies=_CAN_EDIT,
)
async def rotate_webhook_token(workflow_id: str, request: Request) -> dict[str, Any]:
    """Revoke the workflow's webhook URL and issue a new one (old URLs get 401)."""
    svc = _svc(request)
    tenant = _get_tenant(request)
    item = await svc.get(tenant_id=tenant.tenant_id, workflow_id=workflow_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        hook = await svc.rotate_webhook_token(tenant.tenant_id, workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    record_workflow_action(request, "webhook_rotated", workflow_id=workflow_id)
    return {
        "workflow_id": workflow_id,
        "webhook_path": hook["webhook_path"],
        "webhook_url": hook["webhook_url"],
    }
