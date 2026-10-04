"""FastAPI router for the AI Organization OS.

All endpoints:
  - Require tenant authentication via TenantMiddleware
  - Return RFC 7807 errors on failure
  - Include operation_id for OpenAPI
  - Support cursor-based pagination on list endpoints
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid as uuid_mod
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Any
from uuid import uuid4

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.requests import HTTPConnection

from app.org.brain_settings import resolve_autonomy_settings
from app.org.brain_store import BrainDecisionStore
from app.org.rbac import OrgRole, require_org_role
from app.org.schemas import (
    CreateDepartmentRequest,
    CreateMissionRequest,
    CreateOrganizationRequest,
    CreateTaskRequest,
    CreateTeamRequest,
    CursorPage,
    DepartmentResponse,
    MissionResponse,
    MissionStatusUpdate,
    OrganizationResponse,
    OrgEventResponse,
    OrgHealthResponse,
    TaskResponse,
    TaskStatusUpdate,
    TeamResponse,
    UpdateDepartmentRequest,
    UpdateMissionRequest,
    UpdateOrganizationRequest,
)
from app.org.service import OrgService, resolve_llm_provider


async def _org_owned(app: Any, org_id: str, tenant_id: str) -> bool:
    """Whether ``org_id`` exists and belongs to ``tenant_id`` (Postgres, under RLS)."""
    session_factory = getattr(app.state, "db_session_factory", None)
    if session_factory is None:
        return True  # no database: nothing persisted to leak (dev/in-memory mode)
    try:
        uuid_mod.UUID(str(org_id))
        uuid_mod.UUID(str(tenant_id))
    except (ValueError, TypeError):
        # Orgs are keyed by UUID and owned by UUID tenants; anything else cannot
        # own this org.
        return False

    from sqlalchemy import text as _t

    from app.db.rls import sqlalchemy_rls_context

    async with (
        session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, str(tenant_id)),
    ):
        owned = (
            await session.execute(
                _t(
                    "SELECT 1 FROM organizations "
                    "WHERE id = CAST(:oid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"oid": str(org_id), "tid": str(tenant_id)},
            )
        ).scalar_one_or_none()
    return owned is not None


async def _verify_org_ownership(request: HTTPConnection) -> None:
    """Router-wide guard: any ``{org_id}`` in the path must be the caller's org.

    Many org handlers acted on the path's ``org_id`` without checking who owns
    it — keyed a module-level dict by it, subscribed to its Redis channel, or
    passed it on — so a tenant that knew another tenant's org id could stream
    that org's live events (approvals, missions, agent activity), read and
    create its custom roles, read its command history, and more. Checking per
    handler would leave gaps across 70+ routes; this runs before every one of
    them. A foreign or unknown org answers 404, indistinguishable from absent.

    WebSocket routes authenticate inside the handler (HTTP middleware never runs
    for them, so there is no tenant on the connection yet) and call
    :func:`_org_owned` themselves before accepting.
    """
    if request.scope.get("type") == "websocket":
        return
    org_id = request.path_params.get("org_id")
    if not org_id:
        return
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    tenant_id = str(getattr(tenant, "tenant_id", "") or "")
    if not await _org_owned(request.app, str(org_id), tenant_id):
        raise _not_found("Organization", str(org_id))


router = APIRouter(
    prefix="/v1/org", tags=["org"], dependencies=[Depends(_verify_org_ownership)]
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _request_id() -> str:
    return str(uuid4())


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Missing or invalid API key",
            },
        )
    return ctx


async def _owned_org_or_404(service: OrgService, org_id: str, request_id: str | None = None) -> Any:
    """The caller's org, or 404 (also for a malformed id / another tenant's org).

    ``get_organization`` is tenant-scoped (explicit filter + RLS), so an org id
    belonging to another tenant looks exactly like a missing one.
    """
    try:
        org = await service.get_organization(org_id)
    except ValueError:  # not a UUID
        org = None
    if org is None:
        raise _not_found("Organization", org_id, request_id)
    return org


def _not_found(resource: str, rid: str, request_id: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "type": "not-found",
            "title": "Not Found",
            "status": 404,
            "detail": f"{resource} '{rid}' not found",
            "request_id": request_id or _request_id(),
        },
    )


def _conflict(detail: str, request_id: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "type": "conflict",
            "title": "Conflict",
            "status": 409,
            "detail": detail,
            "request_id": request_id or _request_id(),
        },
    )


def _unprocessable(detail: str, request_id: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "type": "validation-error",
            "title": "Validation Error",
            "status": 422,
            "detail": detail,
            "request_id": request_id or _request_id(),
        },
    )


def _validate_uuid(value: str, field: str, request_id: str | None = None) -> None:
    """Raise 422 if value is not a valid UUID4 string."""
    import re

    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    if not uuid_re.match(value):
        raise _unprocessable(f"'{field}' must be a valid UUID, got: {value!r}", request_id)


async def get_org_service(request: Request) -> AsyncGenerator[OrgService, None]:
    """Async generator dependency: creates a DB session, sets RLS, yields OrgService.

    FastAPI will call this as an async generator dependency, keeping the session
    alive for the duration of the endpoint and closing it afterwards.
    """
    from app.db.rls import sqlalchemy_rls_context

    tenant = _require_tenant(request)
    tenant_id: str = getattr(tenant, "tenant_id", None) or getattr(tenant, "id", None) or ""
    if not tenant_id:
        raise HTTPException(status_code=500, detail="Tenant ID missing from context")

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=503,
            detail={
                "type": "service-unavailable",
                "title": "Service Unavailable",
                "status": 503,
                "detail": "Database not initialised yet",
            },
        )

    async with (
        session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        yield OrgService(session=session, tenant_id=tenant_id)


# ── Organization Endpoints ────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    operation_id="org_create",
    summary="Create a new AI Organization",
)
async def create_organization(
    body: CreateOrganizationRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> OrganizationResponse:
    org = await service.create_organization(
        name=body.name,
        description=body.description,
        industry=body.industry,
        jurisdiction=body.jurisdiction,
        mission=body.mission,
        vision=body.vision,
        autonomy_level=body.autonomy_level,
        risk_tolerance=body.risk_tolerance,
        monthly_budget_usd=body.monthly_budget_usd,
        blueprint_ids=body.blueprint_ids,
    )
    return OrganizationResponse.model_validate(org)


@router.get(
    "",
    response_model=CursorPage[OrganizationResponse],
    operation_id="org_list",
    summary="List organizations for this tenant",
)
async def list_organizations(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: OrgService = Depends(get_org_service),
) -> CursorPage[OrganizationResponse]:
    orgs = await service.list_organizations(status=status_filter, limit=limit, offset=offset)
    data = [OrganizationResponse.model_validate(o) for o in orgs]
    return CursorPage(data=data, cursor=None, hasMore=len(orgs) == limit)


@router.get(
    "/{org_id}",
    response_model=OrganizationResponse,
    operation_id="org_get",
    summary="Get a single organization",
)
async def get_organization(
    org_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> OrganizationResponse:
    org = await service.get_organization(org_id)
    if not org:
        raise _not_found("Organization", org_id, x_request_id)
    return OrganizationResponse.model_validate(org)


@router.patch(
    "/{org_id}",
    response_model=OrganizationResponse,
    operation_id="org_update",
    summary="Update an organization",
)
async def update_organization(
    org_id: str,
    body: UpdateOrganizationRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.DEPT_ADMIN),
) -> OrganizationResponse:
    org = await service.update_organization(org_id, body.model_dump(exclude_none=True))
    if not org:
        raise _not_found("Organization", org_id, x_request_id)
    return OrganizationResponse.model_validate(org)


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="org_delete",
    summary="Archive an organization",
)
async def delete_organization(
    org_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.ORG_ADMIN),
) -> None:
    deleted = await service.delete_organization(org_id)
    if not deleted:
        raise _not_found("Organization", org_id, x_request_id)


@router.get(
    "/{org_id}/health",
    response_model=OrgHealthResponse,
    operation_id="org_health",
    summary="Get organization health summary for Command Center",
)
async def get_org_health(
    org_id: str,
    service: OrgService = Depends(get_org_service),
) -> OrgHealthResponse:
    health = await service.get_org_health(org_id)
    return OrgHealthResponse(**health)


# ── Autonomy Settings (Autonomous Org Brain) ─────────────────────────────────


def _autonomy_view(org: Any) -> dict[str, Any]:
    """Resolved ``{autonomy_level, settings}`` view shared by GET and PATCH."""
    return {
        "autonomy_level": org.autonomy_level,
        "settings": asdict(resolve_autonomy_settings(org.settings, org.monthly_budget_usd)),
    }


class _AutonomyUpdateRequest(BaseModel):
    autonomy_level: int | None = None
    settings: dict[str, Any] | None = None


@router.get(
    "/{org_id}/autonomy",
    operation_id="org_autonomy_get",
    summary="Get the org's autonomy level and resolved brain settings",
)
async def get_org_autonomy(
    org_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> dict[str, Any]:
    org = await service.get_organization(org_id)
    if not org:
        raise _not_found("Organization", org_id, x_request_id)
    return _autonomy_view(org)


@router.patch(
    "/{org_id}/autonomy",
    operation_id="org_autonomy_update",
    summary="Update the org's autonomy level and brain settings",
)
async def update_org_autonomy(
    org_id: str,
    body: _AutonomyUpdateRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.DEPT_ADMIN),
) -> dict[str, Any]:
    org = await service.get_organization(org_id)
    if not org:
        raise _not_found("Organization", org_id, x_request_id)

    updates: dict[str, Any] = {}
    if body.autonomy_level is not None:
        if not (0 <= body.autonomy_level <= 5):
            raise _unprocessable(
                f"'autonomy_level' must be between 0 and 5, got: {body.autonomy_level}",
                x_request_id,
            )
        updates["autonomy_level"] = body.autonomy_level

    if body.settings is not None:
        # Shallow-merge into Organization.settings["autonomy"] so unrelated
        # autonomy keys and other top-level settings keys are never dropped.
        merged_settings = dict(org.settings or {})
        autonomy_block = dict(merged_settings.get("autonomy") or {})
        autonomy_block.update(body.settings)
        merged_settings["autonomy"] = autonomy_block
        updates["settings"] = merged_settings

    if updates:
        org = await service.update_organization(org_id, updates)
        if not org:
            raise _not_found("Organization", org_id, x_request_id)

    return _autonomy_view(org)


# ── Department Endpoints ──────────────────────────────────────────────────────


@router.post(
    "/{org_id}/departments",
    status_code=status.HTTP_201_CREATED,
    response_model=DepartmentResponse,
    operation_id="org_dept_create",
    summary="Create a department",
)
async def create_department(
    org_id: str,
    body: CreateDepartmentRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> DepartmentResponse:
    dept = await service.create_department(
        org_id=org_id,
        name=body.name,
        purpose=body.purpose,
        capability_domains=body.capability_domains,
        parent_dept_id=body.parent_dept_id,
        manager_agent_id=body.manager_agent_id,
    )
    return DepartmentResponse.model_validate(dept)


@router.get(
    "/{org_id}/departments",
    response_model=list[DepartmentResponse],
    operation_id="org_dept_list",
    summary="List departments",
)
async def list_departments(
    org_id: str,
    service: OrgService = Depends(get_org_service),
) -> list[DepartmentResponse]:
    depts = await service.list_departments(org_id)
    return [DepartmentResponse.model_validate(d) for d in depts]


@router.get(
    "/{org_id}/departments/{dept_id}",
    response_model=DepartmentResponse,
    operation_id="org_dept_get",
)
async def get_department(
    org_id: str,
    dept_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> DepartmentResponse:
    dept = await service.get_department(dept_id)
    if not dept:
        raise _not_found("Department", dept_id, x_request_id)
    return DepartmentResponse.model_validate(dept)


@router.patch(
    "/{org_id}/departments/{dept_id}",
    response_model=DepartmentResponse,
    operation_id="org_dept_update",
)
async def update_department(
    org_id: str,
    dept_id: str,
    body: UpdateDepartmentRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> DepartmentResponse:
    dept = await service.update_department(dept_id, body.model_dump(exclude_none=True))
    if not dept:
        raise _not_found("Department", dept_id, x_request_id)
    return DepartmentResponse.model_validate(dept)


# ── Mission Endpoints ─────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/missions",
    status_code=status.HTTP_201_CREATED,
    response_model=MissionResponse,
    operation_id="org_mission_create",
    summary="Create a mission",
)
async def create_mission(
    org_id: str,
    body: CreateMissionRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.TEAM_LEAD),
) -> MissionResponse:
    mission = await service.create_mission(
        org_id=org_id,
        title=body.title,
        objective=body.objective,
        why=body.why,
        expected_outcome=body.expected_outcome,
        priority=body.priority,
        dept_id=body.dept_id,
        source=body.source,
        tags=body.tags,
        budget_usd=body.budget_usd,
        deadline=body.deadline,
        autonomy_level=body.autonomy_level,
        created_by=body.created_by,
    )
    return MissionResponse.model_validate(mission)


@router.get(
    "/{org_id}/missions",
    response_model=CursorPage[MissionResponse],
    operation_id="org_mission_list",
    summary="List missions with optional filters",
)
async def list_missions(
    org_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    dept_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: OrgService = Depends(get_org_service),
) -> CursorPage[MissionResponse]:
    missions = await service.list_missions(
        org_id,
        status=status_filter,
        priority=priority,
        dept_id=dept_id,
        limit=limit,
        offset=offset,
    )
    data = [MissionResponse.model_validate(m) for m in missions]
    return CursorPage(data=data, cursor=None, hasMore=len(missions) == limit)


@router.get(
    "/{org_id}/missions/{mission_id}",
    response_model=MissionResponse,
    operation_id="org_mission_get",
)
async def get_mission(
    org_id: str,
    mission_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> MissionResponse:
    mission = await service.get_mission(mission_id)
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant: get_mission is tenant-scoped only,
    # so without this a caller could read org B's mission (same tenant) by id
    # via org A's URL. 404 hides cross-org existence, same as
    # approve_brain_proposal/reject_brain_proposal below.
    if str(mission.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    return MissionResponse.model_validate(mission)


@router.patch(
    "/{org_id}/missions/{mission_id}",
    response_model=MissionResponse,
    operation_id="org_mission_update",
)
async def update_mission(
    org_id: str,
    mission_id: str,
    body: UpdateMissionRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> MissionResponse:
    existing = await service.get_mission(mission_id)
    if not existing:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_mission above.
    if str(existing.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    if body.status:
        mission = await service.update_mission_status(mission_id, body.status)
    else:
        updates = body.model_dump(exclude_none=True, exclude={"status"})
        mission = await service.update_mission(mission_id, updates) if updates else existing
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    return MissionResponse.model_validate(mission)


@router.post(
    "/{org_id}/missions/{mission_id}/status",
    response_model=MissionResponse,
    operation_id="org_mission_status_update",
    summary="Update mission status",
)
async def update_mission_status(
    org_id: str,
    mission_id: str,
    body: MissionStatusUpdate,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> MissionResponse:
    existing = await service.get_mission(mission_id)
    if not existing:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_mission above.
    if str(existing.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    try:
        mission = await service.update_mission_status(mission_id, body.status)
    except ValueError as exc:
        raise _unprocessable(str(exc), x_request_id) from None
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    return MissionResponse.model_validate(mission)


@router.get(
    "/{org_id}/missions/{mission_id}/timeline",
    operation_id="org_mission_timeline",
    summary="Mission phase timeline (Situation Room Gantt ribbon)",
)
async def get_mission_timeline(
    org_id: str,
    mission_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> dict[str, Any]:
    timeline = await service.get_mission_timeline(org_id, mission_id)
    if timeline is None:
        raise _not_found("Mission", mission_id, x_request_id)
    return timeline


# ── Autonomous Org Brain: decisions + proposal approve/reject ───────────────


@router.get(
    "/{org_id}/brain/decisions",
    operation_id="org_brain_decisions_list",
    summary="List recent autonomous org-brain tick decisions",
)
async def list_brain_decisions(
    org_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> list[dict[str, Any]]:
    store = BrainDecisionStore(service._session)
    return await store.list(org_id, service._tenant_id, limit=limit)


@router.post(
    "/{org_id}/brain/proposals/{mission_id}/approve",
    operation_id="org_brain_proposal_approve",
    summary="Approve a brain-proposed mission and dispatch its goal",
)
async def approve_brain_proposal(
    org_id: str,
    mission_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.DEPT_ADMIN),
) -> dict[str, Any]:
    mission = await service.get_mission(mission_id)
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant: get_mission is tenant-scoped, and the
    # RBAC gate above authorises the URL's org — without this a dept-admin of org A
    # could approve org B's proposed mission (same tenant) by id. 404 hides
    # cross-org ids, same as approve_org_request/reject_org_request above.
    if str(mission.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    if str(mission.status) != "proposed":
        raise _conflict(
            f"Mission '{mission_id}' is not in 'proposed' status (status={mission.status!r})",
            x_request_id,
        )
    return await service.dispatch_mission_goal(
        mission_id, app_state=request.app.state, tenant_ctx=None
    )


@router.post(
    "/{org_id}/brain/proposals/{mission_id}/reject",
    response_model=MissionResponse,
    operation_id="org_brain_proposal_reject",
    summary="Reject a brain-proposed mission",
)
async def reject_brain_proposal(
    org_id: str,
    mission_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    _rbac: str = require_org_role(OrgRole.DEPT_ADMIN),
) -> MissionResponse:
    mission = await service.get_mission(mission_id)
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant: get_mission is tenant-scoped, and the
    # RBAC gate above authorises the URL's org — without this a dept-admin of org A
    # could reject org B's proposed mission (same tenant) by id. 404 hides
    # cross-org ids, same as approve_org_request/reject_org_request above.
    if str(mission.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    if str(mission.status) != "proposed":
        raise _conflict(
            f"Mission '{mission_id}' is not in 'proposed' status (status={mission.status!r})",
            x_request_id,
        )
    mission = await service.update_mission_status(mission_id, "cancelled")
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    return MissionResponse.model_validate(mission)


# ── Task Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskResponse,
    operation_id="org_task_create",
    summary="Create a task (with anti-runaway depth check)",
)
async def create_task(
    org_id: str,
    body: CreateTaskRequest,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> TaskResponse:
    try:
        task = await service.create_task(
            org_id=org_id,
            title=body.title,
            objective=body.objective,
            mission_id=body.mission_id,
            parent_task_id=body.parent_task_id,
            priority=body.priority,
            risk_level=body.risk_level,
            depth=body.depth,
            assigned_agent_ids=body.assigned_agent_ids,
            required_capabilities=body.required_capabilities,
            required_tools=body.required_tools,
            budget_usd=body.budget_usd,
            deadline=body.deadline,
            dependencies=body.dependencies,
        )
    except ValueError as exc:
        raise _unprocessable(str(exc), x_request_id) from None
    return TaskResponse.model_validate(task)


@router.get(
    "/{org_id}/tasks",
    response_model=CursorPage[TaskResponse],
    operation_id="org_task_list",
)
async def list_tasks(
    org_id: str,
    mission_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: OrgService = Depends(get_org_service),
) -> CursorPage[TaskResponse]:
    tasks = await service.list_tasks(
        org_id,
        mission_id=mission_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    data = [TaskResponse.model_validate(t) for t in tasks]
    return CursorPage(data=data, cursor=None, hasMore=len(tasks) == limit)


@router.get(
    "/{org_id}/tasks/{task_id}",
    response_model=TaskResponse,
    operation_id="org_task_get",
)
async def get_task(
    org_id: str,
    task_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> TaskResponse:
    task = await service.get_task(task_id)
    if not task:
        raise _not_found("Task", task_id, x_request_id)
    # Scope to THIS org, not just the tenant: get_task is tenant-scoped only, so
    # without this a caller could read org B's task (same tenant) by id via org
    # A's URL. 404 hides cross-org existence, same as approve_org_request /
    # reject_org_request above.
    if str(task.org_id) != org_id:
        raise _not_found("Task", task_id, x_request_id)
    return TaskResponse.model_validate(task)


@router.post(
    "/{org_id}/tasks/{task_id}/status",
    response_model=TaskResponse,
    operation_id="org_task_status_update",
)
async def update_task_status(
    org_id: str,
    task_id: str,
    body: TaskStatusUpdate,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> TaskResponse:
    existing = await service.get_task(task_id)
    if not existing:
        raise _not_found("Task", task_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_task above.
    if str(existing.org_id) != org_id:
        raise _not_found("Task", task_id, x_request_id)
    try:
        task = await service.update_task_status(
            task_id,
            body.status,
            outputs=body.outputs,
            evidence=body.evidence,
            actual_cost_usd=body.actual_cost_usd,
        )
    except ValueError as exc:
        raise _unprocessable(str(exc), x_request_id) from None
    if not task:
        raise _not_found("Task", task_id, x_request_id)
    return TaskResponse.model_validate(task)


def _decision_actor(request: Request) -> str:
    """The approver is the authenticated key, never a request-body field.

    The ``approver`` body field let one caller record decisions as anyone ("alice", "cto",
    ...) and satisfy multi-approver gates alone (same fix as
    ``app.api.governance._approver_identity``).
    """
    tenant = getattr(request.state, "tenant", None)
    key_id = str(getattr(tenant, "api_key_id", "") or "")
    if not key_id:
        raise HTTPException(status_code=403, detail="Approver identity unavailable")
    return key_id


class _TaskApprovalDecision(BaseModel):
    approver: str = Field(
        default="", description="Ignored: the approver is the authenticated caller"
    )
    note: str = Field(default="", description="Optional reason / note")


# ── G-28: Task-level approve endpoint ────────────────────────────────────────


@router.post(
    "/{org_id}/tasks/{task_id}/approve",
    response_model=TaskResponse,
    operation_id="org_task_approve",
    summary="Approve a blocked task (transitions approval_required → running)",
)
async def approve_task(
    org_id: str,
    task_id: str,
    body: _TaskApprovalDecision,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    # HITL-04: deciding a gate is a team-lead action (as on the inbox routes);
    # these had no role check, so any authenticated key could approve.
    _rbac: str = require_org_role(OrgRole.TEAM_LEAD),
) -> TaskResponse:
    """G-28: Approve a task that is in approval_required status.

    - Sets task status to 'running'
    - Publishes org.approval.granted event via OrgEventPublisher
    - Returns updated TaskResponse
    """
    approver = _decision_actor(request)
    task = await service.get_task(task_id)
    if not task:
        raise _not_found("Task", task_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_task above.
    if str(task.org_id) != org_id:
        raise _not_found("Task", task_id, x_request_id)

    # WS-3b: resolve the paired HITLGateway request BEFORE flipping status, so
    # any agent blocked on this gate is released via the ONE shared gateway (no
    # parallel approval mechanism). The request id was recorded on the task's
    # outputs when the approval gate was created (create_mission_and_execute).
    await _resolve_task_hitl_request(
        request, service._tenant_id, task, "approve", body, approver=approver
    )

    updated = await service.update_task_status(
        task_id,
        "running",
        outputs=[{"approved_by": approver, "approval_note": body.note}],
    )
    if not updated:
        raise _not_found("Task", task_id, x_request_id)

    # Publish approval-granted event for OrgRealtimeManager
    try:
        from app.org.events import get_org_event_publisher

        pub = get_org_event_publisher()
        if pub:
            await pub.publish(
                event_type="org.approval.granted",
                org_id=org_id,
                tenant_id=service._tenant_id,
                payload={"task_id": task_id, "approver": approver, "note": body.note},
            )
    except Exception:
        pass  # Non-critical

    return TaskResponse.model_validate(updated)


def _extract_hitl_request_id(task: Any) -> str | None:
    """Find the paired HITLGateway request id recorded on an approval-gate task."""
    for bucket in (getattr(task, "outputs", None) or [], [getattr(task, "extra_data", None) or {}]):
        for entry in bucket:
            if isinstance(entry, dict):
                rid = entry.get("hitl_request_id") or entry.get("approval_request_id")
                if rid:
                    return str(rid)
    return None


async def _resolve_task_hitl_request(
    request: Request, tenant_id: str, task: Any, action: str, body: Any, *, approver: str
) -> None:
    """Resolve the HITLGateway request paired with an org task — or refuse.

    Approving/rejecting an org task must release the same gateway approval a
    blocked agent waits on. It used to ignore approve_async's ``False`` and
    swallow every error (including HITLResolutionUnavailableError), so the task
    moved to 'running' while the agent's gate was still pending or had already
    been rejected/expired (HITL-04). Now:

    * no paired request → nothing to resolve (a gate without an agent waiting);
    * the decision did not take (not pending) → 409, the task is not flipped;
    * the gateway is missing or the decision cannot be written → 503.
    """
    request_id = _extract_hitl_request_id(task)
    if not request_id:
        return
    gateway = getattr(getattr(request.app, "state", None), "hitl_gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approval gateway unavailable; the decision was not recorded",
        )
    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = getattr(getattr(request, "state", None), "tenant", None)
    if getattr(tenant_ctx, "tenant_id", None) != tenant_id:
        tenant_ctx = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="org_task_approval"
        )
    note = getattr(body, "note", "") or getattr(body, "reason", "") or ""
    try:
        if action == "approve":
            # DB-first (approve_async): finds a request raised on any replica and
            # releases the blocked agent only after the decision is committed.
            decided = await gateway.approve_async(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
        else:
            decided = await gateway.reject(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The approval decision could not be recorded; nothing changed",
        ) from exc
    if not decided:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The agent's approval is no longer pending (already decided or expired)",
        )


# ── G-28: Task-level reject endpoint ─────────────────────────────────────────


@router.post(
    "/{org_id}/tasks/{task_id}/reject",
    response_model=TaskResponse,
    operation_id="org_task_reject",
    summary="Reject a blocked task (transitions approval_required → failed)",
)
async def reject_task(
    org_id: str,
    task_id: str,
    body: _TaskApprovalDecision,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
    # HITL-04: deciding a gate is a team-lead action (as on the inbox routes);
    # these had no role check, so any authenticated key could approve.
    _rbac: str = require_org_role(OrgRole.TEAM_LEAD),
) -> TaskResponse:
    """G-28: Reject a task that is in approval_required status.

    - Sets task status to 'failed' with rejection reason
    - Publishes org.approval.rejected event via OrgEventPublisher
    - Returns updated TaskResponse
    """
    approver = _decision_actor(request)
    task = await service.get_task(task_id)
    if not task:
        raise _not_found("Task", task_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_task above.
    if str(task.org_id) != org_id:
        raise _not_found("Task", task_id, x_request_id)

    # WS-3b: resolve the paired HITLGateway request (reject) so a blocked agent
    # is released via the ONE shared gateway.
    await _resolve_task_hitl_request(
        request, service._tenant_id, task, "reject", body, approver=approver
    )

    updated = await service.update_task_status(
        task_id,
        "failed",
        outputs=[{"rejected_by": approver, "rejection_reason": body.note}],
    )
    if not updated:
        raise _not_found("Task", task_id, x_request_id)

    # Publish approval-rejected event for OrgRealtimeManager
    try:
        from app.org.events import get_org_event_publisher

        pub = get_org_event_publisher()
        if pub:
            await pub.publish(
                event_type="org.approval.rejected",
                org_id=org_id,
                tenant_id=service._tenant_id,
                payload={"task_id": task_id, "approver": approver, "reason": body.note},
            )
    except Exception:
        pass  # Non-critical

    return TaskResponse.model_validate(updated)


# ── Team Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/teams",
    status_code=status.HTTP_201_CREATED,
    response_model=TeamResponse,
    operation_id="org_team_create",
)
async def create_team(
    org_id: str,
    body: CreateTeamRequest,
    service: OrgService = Depends(get_org_service),
) -> TeamResponse:
    team = await service.create_team(
        org_id=org_id,
        name=body.name,
        purpose=body.purpose,
        dept_id=body.dept_id,
        team_type=body.team_type,
        member_agent_ids=body.member_agent_ids,
        capability_ids=body.capability_ids,
        tool_ids=body.tool_ids,
    )
    return TeamResponse.model_validate(team)


@router.get(
    "/{org_id}/teams",
    response_model=list[TeamResponse],
    operation_id="org_team_list",
)
async def list_teams(
    org_id: str,
    dept_id: str | None = None,
    service: OrgService = Depends(get_org_service),
) -> list[TeamResponse]:
    teams = await service.list_teams(org_id, dept_id=dept_id)
    return [TeamResponse.model_validate(t) for t in teams]


@router.get(
    "/{org_id}/teams/{team_id}/members",
    operation_id="org_team_members",
    summary="List resolved member profiles for a team",
)
async def list_team_members(
    org_id: str,
    team_id: str,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> dict[str, Any]:
    team = await service.get_team(team_id)
    if team is None:
        raise _not_found("Team", team_id, x_request_id)

    member_ids = list(getattr(team, "member_agent_ids", None) or [])
    members = await service.get_team_member_profiles(org_id=org_id, team_id=team_id)
    return {
        "team_id": team_id,
        "member_ids": member_ids,
        "members": members,
    }


# ── Events Endpoints ──────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/events",
    response_model=CursorPage[OrgEventResponse],
    operation_id="org_events_list",
    summary="Org event stream (activity feed)",
)
async def list_events(
    org_id: str,
    event_type: str | None = None,
    severity: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: OrgService = Depends(get_org_service),
) -> CursorPage[OrgEventResponse]:
    events = await service.list_events(
        org_id,
        event_type=event_type,
        severity=severity,
        entity_id=entity_id,
        limit=limit,
        offset=offset,
    )
    data = [OrgEventResponse.model_validate(e) for e in events]
    return CursorPage(data=data, cursor=None, hasMore=len(events) == limit)


@router.get(
    "/{org_id}/agents/{agent_id}/audit",
    operation_id="org_agent_audit",
    summary="Per-agent activity trail (Situation Room black box)",
)
async def get_agent_audit(
    org_id: str,
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> list[dict[str, Any]]:
    return await service.get_agent_audit(org_id, agent_id, limit=limit)


# ── G-23: Org-level SSE stream (OrgRealtimeManager subscribes here) ──────────


@router.get(
    "/{org_id}/events/stream",
    operation_id="org_events_sse",
    summary="SSE stream for real-time org events (approval, mission, team updates)",
    response_class=StreamingResponse,
)
async def org_events_stream(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> StreamingResponse:
    """G-23: Server-Sent Events stream for all org-level events.

    OrgRealtimeManager.ts subscribes to this endpoint.
    Publishes: org.approval.*, org.mission.*, org.team.*, org.agent.*
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        import asyncio

        try:
            yield f"data: {json.dumps({'type': 'connected', 'org_id': org_id})}\n\n"

            # Subscribe to Redis pub/sub channel for this org. The runtime redis
            # client is app.state._redis (app.state.redis is never set — the same
            # trap that silently killed proactive voice alerts); reading "redis"
            # here left the stream on keepalive-only, so no live event ever arrived.
            app_state = getattr(request.app, "state", None)
            redis = getattr(app_state, "_redis", None) or getattr(app_state, "redis", None)
            if redis is None:
                # No Redis — keepalive only
                while not await request.is_disconnected():
                    yield ": keepalive\n\n"
                    await asyncio.sleep(15)
                return

            channel = f"org:{org_id}:events"
            # `async with` guarantees the pubsub's dedicated connection is reset and
            # returned to the pool on exit. A bare `pubsub()` that only unsubscribes
            # leaks one pooled connection per SSE disconnect (every OrgPage reload),
            # which exhausts the Redis pool and 503s the whole backend.
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe(channel)
                try:
                    async for message in pubsub.listen():
                        if await request.is_disconnected():
                            break
                        if message["type"] not in ("message", "pmessage"):
                            yield ": keepalive\n\n"
                            continue
                        data = message.get("data", b"")
                        if isinstance(data, bytes):
                            data = data.decode()
                        yield f"data: {data}\n\n"
                finally:
                    with contextlib.suppress(Exception):
                        await pubsub.unsubscribe(channel)
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── G-04: Org-scoped approvals endpoint ──────────────────────────────────────


@router.get(
    "/{org_id}/approvals",
    operation_id="org_list_approvals",
    summary="List pending approval requests scoped to this org's missions",
)
async def list_org_approvals(
    org_id: str,
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: OrgService = Depends(get_org_service),
) -> dict:
    """G-04: Return approval requests for goals/missions within this org.

    Reads from the governance approval_requests table filtered by org context.
    ApprovalCenter.tsx consumes this endpoint.
    """
    # Backed by the durable OrgTask store (task_kind == 'approval_gate') rather than
    # the in-memory HITL gateway, whose requests vanish on every restart while the
    # DB task lingers — the exact cause of "1 pending" with an empty inbox. This
    # keeps the inbox consistent with the dashboard count and survives restarts.
    _pending_status = "approval_required"
    want_resolved = (status or "").lower() in (
        "resolved",
        "history",
        "approved",
        "rejected",
        "completed",
        "done",
    )
    def _unavailable(exc: Exception) -> HTTPException:
        # A 200 {"data": []} here rendered as "No pending approvals" while gates
        # were waiting. Fail closed with an honest 503 (no raw exception text).
        from app.observability.logging import get_logger

        get_logger(__name__).warning("org_list_approvals_failed", org_id=org_id, error=str(exc))
        # ``status`` is shadowed by the query parameter here, hence the literal.
        return HTTPException(
            status_code=503,
            detail="Approvals are temporarily unavailable; try again shortly",
        )

    try:
        tasks = await service.list_tasks(org_id, limit=200)
    except Exception as exc:
        raise _unavailable(exc) from exc

    def _to_item(t: Any) -> dict[str, Any]:
        meta = t.extra_data or {}
        approver: str | None = None
        note: str | None = None
        for o in t.outputs or []:
            if isinstance(o, dict):
                approver = o.get("approved_by") or o.get("rejected_by") or approver
                note = o.get("approval_note") or o.get("rejection_note") or note
        is_pending = t.status == _pending_status
        gate = (meta.get("gate") if isinstance(meta.get("gate"), dict) else {}) or {}
        gate_type = str(gate.get("type") or t.why or "approval")
        return {
            "id": str(t.id),
            "request_id": str(t.id),
            "task_id": str(t.id),
            "mission_id": str(t.mission_id) if t.mission_id else None,
            "agent_id": str(t.owner_agent_id) if getattr(t, "owner_agent_id", None) else None,
            "action": gate_type,
            "action_type": gate_type,
            "title": t.title,
            "description": t.objective or f"Approval required for: {gate_type}",
            "risk_level": t.risk_level or "high",
            "estimated_cost_usd": (
                float(t.cost_estimate_usd) if getattr(t, "cost_estimate_usd", None) is not None
                else None
            ),
            "status": "pending" if is_pending else str(t.status),
            "gate": gate,
            "prerequisite_approvals": [],
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "expires_at": t.expires_at.isoformat() if getattr(t, "expires_at", None) else None,
            "resolved_at": (
                t.updated_at.isoformat() if (t.updated_at and not is_pending) else None
            ),
            "approver": approver,
            "note": note,
            "already_approved_by": [approver] if approver else [],
            "approvers_needed": [] if not is_pending else [t.risk_level or "approver"],
        }

    gates = [t for t in tasks if (t.extra_data or {}).get("task_kind") == "approval_gate"]
    if want_resolved:
        selected = [t for t in gates if t.status != _pending_status]
    else:
        # Only PENDING gates on a still-open mission — matches the dashboard
        # pending-approvals count so the inbox and the header badge never disagree.
        try:
            missions = await service.list_missions(org_id, limit=500)
            _open = {
                str(m.id)
                for m in missions
                if str(m.status) not in ("completed", "failed", "cancelled", "archived")
            }
        except Exception as exc:
            # An empty fallback set silently dropped every gate on a mission.
            raise _unavailable(exc) from exc
        selected = [
            t
            for t in gates
            if t.status == _pending_status
            and (t.mission_id is None or str(t.mission_id) in _open)
        ]
    items = [_to_item(t) for t in selected][:limit]
    return {"data": items, "org_id": org_id, "total": len(items)}


class _OrgApprovalDecision(BaseModel):
    approver: str = Field(
        default="", description="Ignored: the approver is the authenticated caller"
    )
    note: str = Field(default="", description="Optional note or reason")
    # The org ApprovalCenter UI posts the reason as `notes`; accept both.
    notes: str = Field(default="", description="Alias for note (frontend field name)")

    @property
    def reason(self) -> str:
        return self.note or self.notes


# ── G-24: Org-scoped approve endpoint ────────────────────────────────────────


@router.post(
    "/{org_id}/approvals/{approval_id}/approve",
    operation_id="org_approve_request",
    summary="Approve a pending approval request scoped to this org",
    status_code=status.HTTP_200_OK,
)
async def approve_org_request(
    org_id: str,
    approval_id: str,
    body: _OrgApprovalDecision,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
    _rbac: str = require_org_role(OrgRole.TEAM_LEAD),
) -> dict:
    """G-24: Approve an org approval gate.

    ``approval_id`` is the durable approval-gate task id. Approving releases the
    gate: best-effort resolve of any paired (in-memory) HITL request so a blocked
    agent is freed, then flip the DB task so the inbox and dashboard count clear
    and survive restarts.
    """
    from datetime import UTC, datetime

    approver = _decision_actor(request)
    task = await service.get_task(approval_id)
    # Scope to THIS org, not just the tenant: get_task is tenant-scoped, and the
    # RBAC gate above authorises the URL's org — without this a team-lead of org A
    # could action org B's gate (same tenant) by id. 404 hides cross-org ids.
    if (
        task is None
        or str(task.org_id) != org_id
        or (task.extra_data or {}).get("task_kind") != "approval_gate"
    ):
        raise _not_found("Approval", approval_id, x_request_id)

    # The paired gateway request must take, or the task is not flipped (HITL-04).
    await _resolve_task_hitl_request(
        request, service._tenant_id, task, "approve", body, approver=approver
    )

    updated = await service.update_task_status(
        approval_id,
        "running",
        outputs=[
            {
                "approved_by": approver,
                "approval_note": body.reason,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ],
    )
    if updated is None:
        raise _not_found("Approval", approval_id, x_request_id)

    # Hard stop released: if this was the LAST pending gate on the mission, launch
    # the deferred goal now. create_mission_and_execute paused the mission (status
    # 'review') and stashed the dispatch params instead of running the work.
    dispatched: dict[str, Any] = {}
    if task.mission_id:
        with contextlib.suppress(Exception):
            gate_tasks = await service.list_tasks(org_id, mission_id=str(task.mission_id))
            remaining = [
                t
                for t in gate_tasks
                if (t.extra_data or {}).get("task_kind") == "approval_gate"
                and t.status == "approval_required"
            ]
            if not remaining:
                dispatched = await service.dispatch_mission_goal(
                    str(task.mission_id), app_state=request.app.state
                )

    with contextlib.suppress(Exception):
        from app.org.events import get_org_event_publisher

        pub = get_org_event_publisher()
        if pub:
            await pub.publish(
                event_type="org.approval.granted",
                org_id=org_id,
                tenant_id=service._tenant_id,
                payload={"task_id": approval_id, "approver": approver, "note": body.reason},
            )

    return {
        "status": "approved",
        "approval_id": approval_id,
        "task_id": approval_id,
        "approver": approver,
        "mission_dispatched": bool(dispatched.get("dispatched")),
        "goal_id": dispatched.get("goal_id"),
    }


# ── G-24: Org-scoped reject endpoint ─────────────────────────────────────────


@router.post(
    "/{org_id}/approvals/{approval_id}/reject",
    operation_id="org_reject_request",
    summary="Reject a pending approval request scoped to this org",
    status_code=status.HTTP_200_OK,
)
async def reject_org_request(
    org_id: str,
    approval_id: str,
    body: _OrgApprovalDecision,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
    _rbac: str = require_org_role(OrgRole.TEAM_LEAD),
) -> dict:
    """G-24: Reject an org approval gate (durable, task-backed).

    Rejecting cancels the gate's task and, when it belongs to a still-open
    mission, marks the mission failed — the human declined the gated action.
    """
    from datetime import UTC, datetime

    approver = _decision_actor(request)
    task = await service.get_task(approval_id)
    if (
        task is None
        or str(task.org_id) != org_id
        or (task.extra_data or {}).get("task_kind") != "approval_gate"
    ):
        raise _not_found("Approval", approval_id, x_request_id)

    await _resolve_task_hitl_request(
        request, service._tenant_id, task, "reject", body, approver=approver
    )

    updated = await service.update_task_status(
        approval_id,
        "cancelled",
        outputs=[
            {
                "rejected_by": approver,
                "rejection_note": body.reason or "Rejected via org approval center",
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ],
    )
    if updated is None:
        raise _not_found("Approval", approval_id, x_request_id)

    # A declined gate stops the mission it guards.
    try:
        if task.mission_id:
            mission = await service.get_mission(str(task.mission_id))
            if mission and str(mission.status) not in (
                "completed",
                "failed",
                "cancelled",
                "archived",
            ):
                await service.update_mission_status(str(task.mission_id), "failed")
    except Exception:
        pass

    try:
        from app.org.events import get_org_event_publisher

        pub = get_org_event_publisher()
        if pub:
            await pub.publish(
                event_type="org.approval.rejected",
                org_id=org_id,
                tenant_id=service._tenant_id,
                payload={"task_id": approval_id, "approver": approver, "note": body.note},
            )
    except Exception:
        pass

    return {
        "status": "rejected",
        "approval_id": approval_id,
        "task_id": approval_id,
        "approver": approver,
    }


# ── SSE: Mission Progress Stream ──────────────────────────────────────────────


@router.get(
    "/{org_id}/missions/{mission_id}/stream",
    operation_id="org_mission_stream",
    summary="SSE stream for mission progress updates",
    response_class=StreamingResponse,
)
async def mission_stream(
    org_id: str,
    mission_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    """Server-Sent Events stream for real-time mission progress."""

    # Scope to THIS tenant + org before subscribing — get_mission is tenant-scoped
    # (via the RLS-scoped service session), and the org_id check additionally closes
    # the cross-org gap. Without this, any authenticated caller could subscribe to
    # ANY mission's Redis event channel by id, across tenants. Mirrors get_mission/
    # approve_brain_proposal above.
    mission = await service.get_mission(mission_id)
    if mission is None or str(mission.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        import asyncio

        try:
            # Initial state
            yield f"data: {json.dumps({'type': 'connected', 'mission_id': mission_id})}\n\n"

            # Subscribe to Redis pub/sub for this mission's events
            # (Falls back to polling if Redis pub/sub unavailable)

            redis = getattr(request.app.state, "_redis", None)

            if redis:
                async with redis.pubsub() as ps:
                    channel = f"mission:{mission_id}:events"
                    await ps.subscribe(channel)
                    async for msg in ps.listen():
                        if await request.is_disconnected():
                            break
                        if msg["type"] == "message":
                            yield f"data: {msg['data'].decode()}\n\n"
            else:
                # Polling fallback
                for _ in range(300):  # 5 minutes max
                    if await request.is_disconnected():
                        break
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    await asyncio.sleep(1)
        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'message': 'stream disconnected'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Graphify: Knowledge-Graph Build ───────────────────────────────────────────


@router.post(
    "/{org_id}/graphify",
    operation_id="org_graphify_start",
    summary="Start a knowledge-graph build job for an organisation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def org_graphify_start(
    org_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, str]:
    """Launch an async Graphify job that extracts entities + relationships from
    the org's knowledge base and returns a streaming job ID.

    The client should then connect to ``/{org_id}/graphify/{job_id}/stream``
    to receive SSE progress events.
    """
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("org.graphify.start") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("org_id", org_id)

        # Validate org exists
        org = await service.get_organization(org_id)
        if org is None:
            raise _not_found("Organization", org_id, x_request_id)

        job_id = str(uuid4())

        # Graphify jobs have no DB row/table of their own (see _run_graphify_job
        # below) -- the job_id only ever lives in this closure and in the Redis
        # pub/sub channel name. That means org_graphify_stream has no store to
        # consult to find out which tenant/org a job_id belongs to. Write a
        # short-lived ownership record now, at the one point where org_id has
        # just been validated as belonging to this tenant, so the stream
        # endpoint has a real (not fabricated) linkage to check against. TTL
        # comfortably covers the job's lifetime (the polling fallback below
        # caps a stream at 5 minutes; the real build should finish well before
        # that) plus reconnects.
        redis = getattr(request.app.state, "_redis", None)
        if redis is not None:
            await redis.set(
                f"graphify:{job_id}:owner",
                json.dumps({"tenant_id": tenant_id, "org_id": org_id}),
                ex=3600,
            )

        # Fire-and-forget: start the build in the background
        asyncio.get_event_loop().create_task(_run_graphify_job(org_id, tenant_id, job_id, request))

        span.set_attribute("job_id", job_id)
        return {"job_id": job_id, "status": "accepted", "org_id": org_id}


@router.get(
    "/{org_id}/graphify/{job_id}/stream",
    operation_id="org_graphify_stream",
    summary="SSE stream for Graphify job progress",
    response_class=StreamingResponse,
)
async def org_graphify_stream(
    org_id: str,
    job_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    """Server-Sent Events stream for the Graphify knowledge-graph build job.

    Events emitted:
      * ``phase``      - pipeline step started   ``{phase, total_phases, label}``
      * ``progress``   - incremental progress    ``{phase, done, total, entity}``
      * ``stats``      - running totals          ``{nodes, edges, communities}``
      * ``complete``   - job finished            ``{nodes, edges, communities}``
      * ``error``      - job failed              ``{message}``
    """
    # Scope to THIS tenant + org BEFORE subscribing — same leak class already
    # fixed for mission_stream (which checks a real DB row). Graphify jobs have
    # no DB row: the only durable link between a job_id and its owning
    # tenant/org is the "graphify:{job_id}:owner" Redis key written by
    # org_graphify_start at job-creation time (see the comment there). Treat a
    # missing or mismatched owner record -- unknown job_id, foreign tenant/org,
    # expired TTL, or Redis unavailable -- as not-found, same as mission_stream,
    # so we never disclose whether a job exists in another tenant/org.
    redis = getattr(request.app.state, "_redis", None)
    owner: dict[str, Any] | None = None
    if redis is not None:
        raw_owner = await redis.get(f"graphify:{job_id}:owner")
        if raw_owner is not None:
            text_owner = raw_owner.decode() if isinstance(raw_owner, bytes) else raw_owner
            with contextlib.suppress(Exception):
                owner = json.loads(text_owner)
    owner_ok = (
        owner is not None
        and owner.get("tenant_id") == service._tenant_id
        and owner.get("org_id") == org_id
    )
    if not owner_ok:
        raise _not_found("Graphify job", job_id, x_request_id)

    async def _stream() -> AsyncGenerator[str, None]:
        try:
            yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"

            if redis:
                channel = f"graphify:{job_id}:events"
                async with redis.pubsub() as ps:
                    await ps.subscribe(channel)
                    async for msg in ps.listen():
                        if await request.is_disconnected():
                            break
                        if msg["type"] == "message":
                            payload = msg["data"]
                            text = payload.decode() if isinstance(payload, bytes) else payload
                            yield f"data: {text}\n\n"
                            parsed = json.loads(text)
                            if parsed.get("type") in ("complete", "error"):
                                break
            else:
                # Fallback: emit synthetic progress ticks so the UI isn't stuck
                phases = [
                    "Ingesting documents",
                    "Extracting entities",
                    "Building relationships",
                    "Detecting communities",
                    "Persisting graph",
                ]
                for idx, label in enumerate(phases, start=1):
                    if await request.is_disconnected():
                        break
                    evt = {
                        "type": "phase",
                        "phase": idx,
                        "total_phases": len(phases),
                        "label": label,
                    }
                    yield f"data: {json.dumps(evt)}\n\n"
                    await asyncio.sleep(0.8)
                done_evt = {"type": "complete", "nodes": 0, "edges": 0, "communities": 0}
                yield f"data: {json.dumps(done_evt)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_graphify_job(org_id: str, tenant_id: str, job_id: str, request: Request) -> None:
    """Background coroutine that builds the knowledge graph and emits SSE progress."""
    import structlog
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    log = structlog.get_logger(__name__)

    with tracer.start_as_current_span("org.graphify.job") as span:
        span.set_attribute("org_id", org_id)
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("job_id", job_id)


        redis = getattr(request.app.state, "_redis", None)
        channel = f"graphify:{job_id}:events"

        async def _emit(payload: dict) -> None:  # type: ignore[type-arg]
            if redis:
                await redis.publish(channel, json.dumps(payload))
            # NB: 'event' is structlog's reserved positional arg — passing it as a
            # kwarg raised "multiple values for keyword argument 'event'" and killed
            # the whole graphify job. Use 'event_type' instead.
            log.info("graphify.event", job_id=job_id, event_type=payload.get("type"))

        try:
            # The client connects to the SSE stream a moment after this
            # fire-and-forget job is dispatched (it first fetches a stream token).
            # Redis pub/sub does not replay, so give the subscriber time to attach
            # before the first event — otherwise the whole build is missed and the
            # UI sits on "Queued" forever.
            await asyncio.sleep(1.5)

            from app.db.rls import sqlalchemy_rls_context
            from app.db.session import get_session_factory
            from app.knowledge_graph.org_builder import build_org_knowledge_graph

            provider = getattr(request.app.state, "_app_provider", None)
            db = get_session_factory()
            async with db() as sess, sqlalchemy_rls_context(sess, tenant_id):
                svc = OrgService(sess, tenant_id)
                final = await build_org_knowledge_graph(
                    service=svc,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    provider=provider,
                    emit=_emit,
                )
            log.info(
                "graphify.job.complete",
                job_id=job_id,
                nodes=final["nodes"],
                edges=final["edges"],
                communities=final["communities"],
            )
        except Exception as exc:
            log.error("graphify.job.failed", job_id=job_id, error=str(exc))
            await _emit({"type": "error", "message": str(exc)})


# ── Org-scoped RBAC Role Management (AA3) ────────────────────────────────────


class _RolePermission(BaseModel):
    feature: str
    view: bool = False
    edit: bool = False
    delete: bool = False


class _OrgRoleCreate(BaseModel):
    model_config = {"populate_by_name": True}
    name: str
    description: str = ""
    permissions: list[_RolePermission] = []
    member_count: int = 0


class _OrgRoleResponse(_OrgRoleCreate):
    id: str
    is_built_in: bool = False


_BUILT_IN_ROLES: list[_OrgRoleResponse] = [
    _OrgRoleResponse(
        id="org_owner", name="Org Owner", description="Full control.", is_built_in=True
    ),
    _OrgRoleResponse(
        id="org_admin",
        name="Org Admin",
        description="Manage members, connectors, settings.",
        is_built_in=True,
    ),
    _OrgRoleResponse(
        id="mission_lead",
        name="Mission Lead",
        description="Create/edit missions and tasks.",
        is_built_in=True,
    ),
    _OrgRoleResponse(
        id="agent_runner", name="Agent Runner", description="Execute missions.", is_built_in=True
    ),
    _OrgRoleResponse(id="observer", name="Observer", description="View only.", is_built_in=True),
]

def _role_store(request: Request, org_id: str) -> Any:
    from app.org.runtime_store import OrgRoleStore

    ctx = _require_tenant(request)
    return OrgRoleStore(
        getattr(request.app.state, "db_session_factory", None),
        str(getattr(ctx, "tenant_id", ctx)),
        org_id,
    )


def _command_store(app_state: Any, tenant_id: str, org_id: str) -> Any:
    from app.org.runtime_store import OrgCommandStore

    return OrgCommandStore(getattr(app_state, "db_session_factory", None), tenant_id, org_id)


@router.get(
    "/{org_id}/roles",
    operation_id="org_list_roles",
    summary="List org roles (built-in + custom)",
    response_model=list[_OrgRoleResponse],
)
async def org_list_roles(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> list[_OrgRoleResponse]:
    """Return all roles for this organisation including built-in and custom."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.list_roles") as span:
        span.set_attribute("org_id", org_id)
        custom = await _role_store(request, org_id).list()
        return [*_BUILT_IN_ROLES, *(_OrgRoleResponse(**r) for r in custom)]


@router.post(
    "/{org_id}/roles",
    operation_id="org_create_role",
    summary="Create a custom org role",
    status_code=status.HTTP_201_CREATED,
    response_model=_OrgRoleResponse,
)
async def org_create_role(
    org_id: str,
    body: _OrgRoleCreate,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> _OrgRoleResponse:
    """Create a new custom role with granular permissions."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.create_role") as span:
        span.set_attribute("org_id", org_id)
        span.set_attribute("role_name", body.name)
        role = _OrgRoleResponse(id=str(uuid4()), is_built_in=False, **body.model_dump())
        await _role_store(request, org_id).create(role.model_dump(exclude={"is_built_in"}))
        return role


@router.put(
    "/{org_id}/roles/{role_id}",
    operation_id="org_update_role",
    summary="Update a custom org role",
    response_model=_OrgRoleResponse,
)
async def org_update_role(
    org_id: str,
    role_id: str,
    body: _OrgRoleCreate,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> _OrgRoleResponse:
    """Update permissions on a custom role."""
    updated = _OrgRoleResponse(id=role_id, is_built_in=False, **body.model_dump())
    if await _role_store(request, org_id).update(
        role_id, updated.model_dump(exclude={"is_built_in"})
    ):
        return updated
    raise _not_found("Role", role_id, x_request_id)


@router.delete(
    "/{org_id}/roles/{role_id}",
    operation_id="org_delete_role",
    summary="Delete a custom org role",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def org_delete_role(
    org_id: str,
    role_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> None:
    """Delete a custom role (built-in roles cannot be deleted)."""
    if not await _role_store(request, org_id).delete(role_id):
        raise _not_found("Role", role_id, x_request_id)


# ── Emergency Stop / Pause (QA10) ────────────────────────────────────────────


async def _publish_stop_event(event_type: str, tenant_id: str, org_id: str, **payload: Any) -> None:
    """Announce a stop/resume on the org event stream (a08-F180-03).

    Best effort by design: the persisted flag is the source of truth and the UI
    re-reads it via GET /emergency-stop; a lost event only delays the banner.
    """
    try:
        from app.org.events import get_org_event_publisher

        await get_org_event_publisher().publish(
            event_type=event_type, org_id=org_id, tenant_id=tenant_id, payload=payload
        )
    except Exception as exc:
        import structlog as _slog

        _slog.get_logger(__name__).warning(
            "org.emergency_stop_event_failed", event_type=event_type, error=str(exc)[:200]
        )


@router.get(
    "/{org_id}/emergency-stop",
    operation_id="org_emergency_stop_status",
    summary="Whether this organisation's autonomous work is emergency-stopped",
)
async def org_emergency_stop_status(org_id: str, request: Request) -> dict[str, Any]:
    """Read the shared stop flags (org, then tenant-wide) from Redis.

    Fails closed with 503 when the flags cannot be read: "not stopped" is never
    reported for a state nobody could verify.
    """
    ctx = _require_tenant(request)
    tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
    redis = getattr(request.app.state, "_redis", None)
    from app.governance.emergency_stop import (
        EmergencyStopUnavailableError,
        org_stop_key,
        read_stop,
        tenant_stop_key,
    )

    try:
        org_rec = await read_stop(redis, org_stop_key(tenant_id, org_id))
        tenant_rec = None if org_rec else await read_stop(redis, tenant_stop_key(tenant_id))
    except EmergencyStopUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Emergency stop state could not be verified",
        ) from exc
    rec = org_rec or tenant_rec
    return {
        "org_id": org_id,
        "stopped": rec is not None,
        "scope": "org" if org_rec else ("tenant" if tenant_rec else None),
        "activated_at": (rec or {}).get("activated_at"),
        "activated_by": (rec or {}).get("activated_by"),
        "reason": (rec or {}).get("reason"),
    }


@router.post(
    "/{org_id}/emergency-stop",
    operation_id="org_emergency_stop",
    summary="Immediately pause all autonomous work for this organisation",
    status_code=status.HTTP_200_OK,
)
async def org_emergency_stop(
    org_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
    _rbac: str = require_org_role(OrgRole.ORG_ADMIN),
) -> dict[str, str]:
    """Stop all autonomous work of this organisation until an org admin resumes it.

    Org-admin only (it had no role check: any authenticated key could halt, or
    resume, any org). The flag ``emergency_stop:{tenant_id}:{org_id}`` is stored
    in Redis WITHOUT a TTL (it used to auto-expire after 24 h) and is enforced
    at goal submission, goal start and every step boundary of the org's goals.
    """
    import structlog as _slog
    from opentelemetry import trace as _trace

    _log = _slog.get_logger(__name__)
    with _trace.get_tracer(__name__).start_as_current_span("org.emergency_stop") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("org_id", org_id)


        redis = getattr(request.app.state, "_redis", None)
        from app.governance.emergency_stop import (
            EmergencyStopUnavailableError,
            activate_org_stop,
        )

        try:
            # Read by app.governance.emergency_stop at submit, start and each step.
            stop_record = await activate_org_stop(
                redis, tenant_id, org_id, activated_by=str(getattr(ctx, "api_key_id", "") or "")
            )
        except EmergencyStopUnavailableError as exc:
            # Answering "stopped" without persisting the flag told operators the
            # org was halted while nothing (worker or API) could ever see it.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Emergency stop unavailable: the flag could not be persisted; "
                "nothing was stopped",
            ) from exc
        await _publish_stop_event(
            "org.emergency_stop.triggered",
            tenant_id,
            org_id,
            activated_at=stop_record.get("activated_at"),
            activated_by=stop_record.get("activated_by"),
        )

        # Mark the org's non-terminal goals cancelled off the request path (keyset
        # batches on a worker). The flag above already halts them at their next
        # step / signal poll wherever they run; this records the cancellation.
        cancellation = "enqueued"
        try:
            import asyncio as _asyncio

            from app.scaling.tasks import cancel_goals_for_emergency_stop

            # Bounded: an unreachable broker must not hold the stop response.
            await _asyncio.wait_for(
                _asyncio.to_thread(
                    cancel_goals_for_emergency_stop.apply_async,
                    kwargs={"tenant_id": tenant_id, "org_id": org_id},
                    retry=False,
                ),
                timeout=5.0,
            )
        except Exception as exc:
            cancellation = "not_enqueued"
            _log.error(
                "org.emergency_stop_cancel_enqueue_failed",
                tenant_id=tenant_id,
                org_id=org_id,
                error=type(exc).__name__,
            )

        _log.warning(
            "org.emergency_stop_activated",
            tenant_id=tenant_id,
            org_id=org_id,
            request_id=x_request_id,
        )
        return {
            "status": "stopped",
            "org_id": org_id,
            "goal_cancellation": cancellation,
            "message": (
                "All autonomous work of this organisation is stopped until resumed: "
                "no new goal starts and running goals halt at their next step."
            ),
            "request_id": x_request_id,
        }


@router.post(
    "/{org_id}/emergency-stop/resume",
    operation_id="org_emergency_resume",
    summary="Resume autonomous work after an emergency stop",
    status_code=status.HTTP_200_OK,
)
async def org_emergency_resume(
    org_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
    _rbac: str = require_org_role(OrgRole.ORG_ADMIN),
) -> dict[str, str]:
    """Clears the emergency stop flag, allowing autonomous work to resume."""
    import structlog as _slog
    from opentelemetry import trace as _trace

    _log = _slog.get_logger(__name__)
    with _trace.get_tracer(__name__).start_as_current_span("org.emergency_resume") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("org_id", org_id)


        redis = getattr(request.app.state, "_redis", None)
        from app.governance.emergency_stop import (
            EmergencyStopUnavailableError,
            clear_org_stop,
        )

        try:
            await clear_org_stop(redis, tenant_id, org_id)
        except EmergencyStopUnavailableError as exc:
            # The stop may still be set: never answer "resumed".
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Emergency stop could not be cleared; the organisation is still stopped",
            ) from exc

        _log.info("org.emergency_stop_cleared", tenant_id=tenant_id, org_id=org_id)
        await _publish_stop_event("org.emergency_stop.resumed", tenant_id, org_id)
        return {
            "status": "resumed",
            "org_id": org_id,
            "message": "Autonomous work resumed.",
            "request_id": x_request_id,
        }


# ── Morning Brief (N11) ───────────────────────────────────────────────────────


@router.get(
    "/{org_id}/brief/morning",
    operation_id="org_morning_brief",
    summary="AI-generated executive morning brief for the organisation",
    status_code=status.HTTP_200_OK,
)
async def org_morning_brief(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Returns a structured morning brief covering: accomplishments, priorities,
    risks, opportunities, recommendations, and cost overview.

    The brief is generated from live org health data. Full LLM synthesis is
    done when an LLM provider is configured; otherwise returns structured data.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.morning_brief") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        health = await service.get_org_health(org_id)
        org = await service.get_organization(org_id)
        if org is None:
            raise _not_found("Organization", org_id)

        pending = health.get("pending_approvals", 0)
        blocked = health.get("task_counts", {}).get("blocked", 0)
        failed = health.get("task_counts", {}).get("failed", 0)
        active_missions = health.get("active_missions", 0)

        return {
            "org_id": org_id,
            "org_name": org.name,
            "overall_health": health.get("health", "healthy"),
            "active_missions": active_missions,
            "active_teams": health.get("active_teams", 0),
            "pending_approvals": pending,
            "priorities": [
                *(
                    [{"urgency": "critical", "text": f"{pending} approvals awaiting your decision"}]
                    if pending > 0
                    else []
                ),
                *(
                    [{"urgency": "warning", "text": f"{blocked} tasks are blocked"}]
                    if blocked > 0
                    else []
                ),
                *(
                    [{"urgency": "info", "text": f"{active_missions} missions running smoothly"}]
                    if active_missions > 0 and pending == 0 and blocked == 0
                    else []
                ),
            ],
            "risks": [
                *(
                    [{"severity": "high", "text": f"{failed} tasks failed today — review required"}]
                    if failed > 0
                    else []
                ),
            ],
            "cost_overview": health.get("event_counts_24h", {}),
            "items_needing_attention": health.get("items_needing_attention", 0),
        }


# ── Q2/Q3: Universal Command Gateway — REST intake (QA10) ────────────────────


class _OrgCommandRequest(BaseModel):
    command: str
    channel: str = "rest"
    conversation_id: str | None = None
    metadata: dict[str, object] = {}


@router.post(
    "/{org_id}/command",
    operation_id="org_universal_command",
    summary="Q2/Q3 Universal Command Gateway — accept NL command via REST",
    status_code=status.HTTP_202_ACCEPTED,
)
async def org_universal_command(
    org_id: str,
    body: _OrgCommandRequest,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Universal Command Gateway — accepts any natural-language command
    directed at the organisation from any channel (REST, Telegram, Slack…).

    Commands are routed to the agent loop. High-risk commands
    (``delete``, ``deploy``, ``change-autonomy``) require 2FA confirmation.
    Returns a ``command_id`` for polling progress via SSE.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    import structlog as _sl
    from opentelemetry import trace as _trace

    _log = _sl.get_logger(__name__)
    with _trace.get_tracer(__name__).start_as_current_span("org.command_gateway") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("org_id", org_id)
        span.set_attribute("channel", body.channel)

        # Validate org exists
        org = await service.get_organization(org_id)
        if org is None:
            raise _not_found("Organization", org_id, x_request_id)

        # High-risk command detection
        cmd_lower = body.command.lower()
        _high_risk_words = ("delete", "deploy", "change-autonomy", "pause all", "emergency")
        high_risk = any(word in cmd_lower for word in _high_risk_words)
        command_id = str(uuid4())

        # Store in command history
        record: dict[str, object] = {
            "command_id": command_id,
            "command": body.command,
            "channel": body.channel,
            "org_id": org_id,
            "tenant_id": tenant_id,
            "status": "pending_2fa" if high_risk else "queued",
            "requires_2fa": high_risk,
            "conversation_id": body.conversation_id,
            "submitted_at": _dt.now(UTC).isoformat(),
            "result": None,
        }
        # Persisted (not a module dict) so every replica can poll it; the store
        # keeps the newest COMMAND_HISTORY_CAP per org.
        await _command_store(request.app.state, tenant_id, org_id).add(record)

        # Route to the agent loop before answering (a08-F180-01). This used to be
        # a fire-and-forget task: a replica crash between the 202 and submit_goal
        # left the persisted command 'queued' forever. submit_goal only persists
        # and enqueues the goal (durable from then on), so awaiting it is cheap.
        # Pass the full TenantContext — GoalService.submit_goal requires it.
        goal_id: str | None = None
        if not high_risk:
            routed_ok, goal_id, route_error = await _route_command_to_agent(
                command_id, org_id, ctx, body.command, request.app.state
            )
            if not routed_ok:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "command_id": command_id,
                        "status": "routing_failed",
                        "message": "The command was not started: " + (route_error or "")[:200],
                    },
                )
            record["status"] = "routed"

        _log.info(
            "org.command_received",
            tenant_id=tenant_id,
            org_id=org_id,
            channel=body.channel,
            command_id=command_id,
            high_risk=high_risk,
        )
        span.set_attribute("high_risk", high_risk)
        span.set_attribute("command_id", command_id)

        return {
            "command_id": command_id,
            "status": record["status"],
            "goal_id": goal_id,
            "requires_2fa": high_risk,
            "org_id": org_id,
            "channel": body.channel,
            "message": (
                "Command queued for 2FA confirmation before execution."
                if high_risk
                else "Command accepted and submitted to the agent loop."
            ),
        }


async def _route_command_to_agent(
    command_id: str, org_id: str, tenant_ctx: Any, command: str, app_state: Any = None
) -> tuple[bool, str | None, str | None]:
    """Route an accepted UCG command to the agent goal loop.

    ``app_state`` is the request's ``app.state`` (threaded by the caller) so this
    uses the lifespan-wired, DB/Redis-backed GoalService instead of the
    module-level app.main.app singleton (whose state carries unwired in-memory
    fallbacks). ``tenant_ctx`` is the request's TenantContext, which
    ``GoalService.submit_goal`` requires (a bare tenant_id string is not
    accepted — passing one previously raised TypeError and every command
    silently became ``routing_failed``).

    Returns ``(submitted, goal_id, error)``. Awaited by the endpoint (a08-F180-01).
    """
    import structlog as _sl

    _log = _sl.get_logger(__name__)
    tenant_id = getattr(tenant_ctx, "tenant_id", str(tenant_ctx))
    try:
        goal_service = getattr(app_state, "goal_service", None)
        if goal_service is None or not hasattr(goal_service, "submit_goal"):
            raise RuntimeError("goal_service unavailable on app.state")
        result = await goal_service.submit_goal(
            goal=command,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            execution_context={"source": "ucg", "org_id": org_id, "command_id": command_id},
        )
        goal_id = result.get("goal_id") if isinstance(result, dict) else None
    except Exception as exc:
        _log.warning(
            "org.command_route_failed",
            command_id=command_id,
            tenant_id=tenant_id,
            org_id=org_id,
            error=str(exc),
        )
        try:
            await _command_store(app_state, tenant_id, org_id).update(
                command_id, status="routing_failed", error=str(exc)
            )
        except Exception as store_exc:
            _log.warning(
                "org.command_status_write_failed", command_id=command_id, error=str(store_exc)
            )
        return False, None, str(exc)
    # The goal is durable from here; a failed status write must not report it
    # as not started.
    try:
        await _command_store(app_state, tenant_id, org_id).update(
            command_id, status="routed", goal_id=goal_id
        )
    except Exception as store_exc:
        _log.warning(
            "org.command_status_write_failed", command_id=command_id, error=str(store_exc)
        )
    _log.info(
        "org.command_routed",
        command_id=command_id,
        tenant_id=tenant_id,
        org_id=org_id,
        goal_id=goal_id,
    )
    return True, goal_id, None


# ── N2: Org Composer — NL to Organisation ────────────────────────────────────


class _OrgComposeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    goals: list[str] = []
    industry: str = ""
    autonomy_level: int = 2
    budget_usd: float = 0.0
    constraints: list[str] = []


@router.post(
    "/compose",
    operation_id="org_compose",
    summary="N2 Org Composer — create an org from natural language description",
    status_code=status.HTTP_201_CREATED,
)
async def org_compose(
    body: _OrgComposeRequest,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Organisation Composer — accepts a natural-language description and
    autonomously creates an organisation with appropriate departments,
    capabilities, and initial mission scaffolding.

    When an LLM provider is configured, uses AI to infer the optimal org
    structure. Falls back to a template-based composition for the given industry.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.compose") as span:
        _require_tenant(request)
        span.set_attribute("industry", body.industry)
        span.set_attribute("autonomy_level", body.autonomy_level)

        # Deep N2: delegate to service layer which uses LLM when available.
        # Resolve the real provider from the wired app.state so the composer
        # actually designs a bespoke org structure for the objective instead of
        # always falling back to the industry template (the provider attribute
        # was previously never threaded, so the LLM path was unreachable).
        _llm_provider = resolve_llm_provider(request.app.state)
        result = await service.compose_from_nl(
            description=body.description,
            goals=body.goals,
            industry=body.industry,
            autonomy_level=body.autonomy_level,
            budget_usd=body.budget_usd,
            constraints=body.constraints,
            llm_provider=_llm_provider,
        )
        span.set_attribute("composition_method", str(result.get("composition_method", "")))

        span.set_attribute("org_id", result.get("org_id", ""))
        span.set_attribute("departments_created", len(result.get("departments", [])))
        result["request_id"] = x_request_id
        return result


# ── P13: Team Lifecycle State Machine ────────────────────────────────────────

_TEAM_LIFECYCLE_STATES = frozenset(
    {
        "create",
        "staff",
        "brief",
        "execute",
        "review",
        "complete",
        "archive",
    }
)

_TEAM_LIFECYCLE_TRANSITIONS: dict[str, list[str]] = {
    "create": ["staff"],
    "staff": ["brief"],
    "brief": ["execute"],
    "execute": ["review"],
    "review": ["complete"],
    "complete": ["archive"],
    "archive": [],
}


@router.post(
    "/{org_id}/teams/{team_id}/lifecycle",
    operation_id="org_team_lifecycle_transition",
    summary="P13 Team lifecycle — advance team to next lifecycle state",
    status_code=status.HTTP_200_OK,
)
async def org_team_lifecycle_transition(
    org_id: str,
    team_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Advance a team to its next lifecycle state (CREATE → STAFF → BRIEF →
    EXECUTE → REVIEW → COMPLETE → ARCHIVE).

    The transition emits a ``team.lifecycle.{state}`` org event.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.team_lifecycle") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("team_id", team_id)

        team = await service.get_team(team_id)
        if team is None:
            raise _not_found("Team", team_id, x_request_id)

        existing_meta = dict(
            getattr(team, "extra_data", None) or getattr(team, "metadata", None) or {}
        )
        current = existing_meta.get("lifecycle_state", "create")
        allowed = _TEAM_LIFECYCLE_TRANSITIONS.get(current, [])

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "type": "lifecycle-terminal",
                    "title": "No further transitions",
                    "status": 422,
                    "detail": f"Team is in terminal state '{current}'.",
                    "request_id": x_request_id,
                },
            )

        next_state = allowed[0]
        existing_meta["lifecycle_state"] = next_state
        await service.update_team(team_id, {"extra_data": existing_meta})
        span.set_attribute("transition", f"{current} -> {next_state}")

        return {
            "team_id": team_id,
            "previous_state": current,
            "current_state": next_state,
            "next_allowed": _TEAM_LIFECYCLE_TRANSITIONS.get(next_state, []),
        }


@router.get(
    "/{org_id}/teams/{team_id}/lifecycle",
    operation_id="org_team_lifecycle_get",
    summary="Get current team lifecycle state",
    status_code=status.HTTP_200_OK,
)
async def org_team_lifecycle_get(
    org_id: str,
    team_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the current lifecycle state and allowed transitions for a team."""
    _require_tenant(request)
    team = await service.get_team(team_id)
    if team is None:
        raise _not_found("Team", team_id, x_request_id)

    current = (getattr(team, "extra_data", None) or getattr(team, "metadata", None) or {}).get(
        "lifecycle_state", "create"
    )
    return {
        "team_id": team_id,
        "current_state": current,
        "next_allowed": _TEAM_LIFECYCLE_TRANSITIONS.get(current, []),
        "all_states": list(_TEAM_LIFECYCLE_STATES),
    }


# ── Q2/Q3 Command History ─────────────────────────────────────────────────────


@router.get(
    "/{org_id}/commands",
    operation_id="org_list_commands",
    summary="Q3 UCG — list command history for the organisation",
)
async def org_list_commands(
    org_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    channel: str | None = Query(default=None),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the command history for this organisation.

    Supports filtering by channel (``rest``, ``telegram``, ``slack``, …).
    Commands are ordered newest-first.
    """
    ctx = _require_tenant(request)
    store = _command_store(request.app.state, str(getattr(ctx, "tenant_id", ctx)), org_id)
    commands, total = await store.list(limit=limit, channel=channel)
    return {"org_id": org_id, "commands": commands, "total": total}


@router.get(
    "/{org_id}/commands/{command_id}",
    operation_id="org_get_command",
    summary="Q3 UCG — get status of a specific command",
)
async def org_get_command(
    org_id: str,
    command_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the status and result of a previously submitted UCG command."""
    ctx = _require_tenant(request)
    store = _command_store(request.app.state, str(getattr(ctx, "tenant_id", ctx)), org_id)
    cmd = await store.get(command_id)
    if cmd is None:
        raise _not_found("Command", command_id, x_request_id)
    return cmd


# ── SUPP-H: Digital Twin endpoints ───────────────────────────────────────────


class _SimulateRequest(BaseModel):
    title: str
    priority: str = "medium"
    description: str = ""
    required_capabilities: list[str] = []


class _WhatIfRequest(BaseModel):
    scenario: dict[str, object]


@router.post(
    "/{org_id}/twin/simulate",
    operation_id="org_twin_simulate",
    summary="SUPP-H Digital Twin — simulate a mission resource/time estimate",
    status_code=status.HTTP_200_OK,
)
async def org_twin_simulate(
    org_id: str,
    body: _SimulateRequest,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Simulate what resources and time a mission would require WITHOUT
    modifying any production state.  Uses the OrgDigitalTwin which reads
    current org health, team capacity, and active workloads to estimate.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.twin.simulate") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("priority", body.priority)

        org = await service.get_organization(org_id)
        if org is None:
            raise _not_found("Organization", org_id)

        from app.org.digital_twin import get_twin

        twin = get_twin()
        # Estimates come from this org's completed-mission history and staffing
        # (read through the RLS-scoped service); no history → null + reason.
        result = await twin.simulate_mission(
            org_id=org_id,
            mission_config={
                "title": body.title,
                "priority": body.priority,
                "description": body.description,
                "required_capabilities": body.required_capabilities,
            },
            service=service,
        )
        span.set_attribute("sample_size", result.sample_size)
        span.set_attribute("feasible", bool(result.feasible))

        return {
            "org_id": org_id,
            "mission_title": body.title,
            "estimated_duration_h": result.estimated_duration_h,
            "estimated_cost_usd": result.estimated_cost_usd,
            "resource_usage": result.resource_usage,
            "bottlenecks": result.bottlenecks,
            "recommendations": result.recommendations,
            "feasible": result.feasible,
            "confidence": result.confidence,
            "sample_size": result.sample_size,
            "estimate_reason": result.estimate_reason,
            "simulated_at": result.simulated_at,
        }


@router.get(
    "/{org_id}/twin/capacity",
    operation_id="org_twin_capacity",
    summary="SUPP-H Digital Twin — org capacity plan and utilisation",
    status_code=status.HTTP_200_OK,
)
async def org_twin_capacity(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the current capacity plan — per-department utilisation and queued work.

    Utilisation is measured, not estimated: staffed agents (members/managers of
    the department's active teams) with in-flight tasks / staffed agents. A
    department with no staffed agents reports ``utilisation: null`` plus a
    ``reason``; time-to-clear is ``null`` because there is no throughput model.
    Never modifies production state.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.twin.capacity") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        from app.org.digital_twin import get_twin

        health = await service.get_org_health(org_id)
        plan = await get_twin().capacity_plan(org_id, service)

        span.set_attribute("departments", len(plan.departments))
        span.set_attribute("overloaded", len(plan.overloaded))

        return {
            "org_id": org_id,
            "current_utilisation": plan.current_utilisation,
            "departments": [asdict(d) for d in plan.departments],
            "queued_missions": plan.queued_missions,
            "estimated_clear_h": plan.estimated_clear_h,
            "estimated_clear_reason": plan.estimated_clear_reason,
            "underutilised_depts": plan.underutilised,
            "overloaded_depts": plan.overloaded,
            "active_teams": health.get("active_teams", 0),
            "pending_approvals": health.get("pending_approvals", 0),
            "recommendations": plan.recommendations,
        }


@router.post(
    "/{org_id}/twin/what-if",
    operation_id="org_twin_what_if",
    summary="SUPP-H Digital Twin — what-if scenario analysis (not implemented: 501)",
    status_code=status.HTTP_200_OK,
    responses={501: {"description": "What-if re-simulation is not implemented"}},
)
async def org_twin_what_if(
    org_id: str,
    body: _WhatIfRequest,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Run a what-if scenario through the digital twin.

    The twin has no throughput model to re-simulate a scenario against, so this
    returns **501 Not Implemented** rather than a canned projection (it used to
    answer every scenario with "Estimated 15-20% throughput gain").
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.twin.what_if") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("scenario", str(list(body.scenario.keys())))

        from app.org.digital_twin import WhatIfNotSupportedError, get_twin

        twin = get_twin()
        try:
            return await twin.what_if(org_id=org_id, scenario=dict(body.scenario))
        except WhatIfNotSupportedError as exc:
            span.set_attribute("not_implemented", True)
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "type": "not-implemented",
                    "title": "Not Implemented",
                    "status": 501,
                    "detail": str(exc),
                    "request_id": _request_id(),
                },
            ) from exc


# ── P4: Strategic Advisor endpoint ──────────────────────────────────────────


@router.get(
    "/{org_id}/brief/strategic",
    operation_id="org_strategic_brief",
    summary="P4 Strategic Advisor — weekly intelligence brief",
    status_code=status.HTTP_200_OK,
)
async def org_strategic_brief(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Generate or return the cached strategic brief for this organisation.

    Uses LLM when provider available; falls back to template composition.
    Designed to be called by Celery Beat every Sunday, or on demand.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.strategic_brief") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        org = await service.get_organization(org_id)
        if org is None:
            raise _not_found("Organization", org_id)

        health = await service.get_org_health(org_id)

        from app.org.advanced_services import get_strategic_advisor

        advisor = get_strategic_advisor()
        brief = await advisor.generate_weekly_brief(
            org_id=org_id,
            org_name=str(org.name),
            health=health,
            tenant_id=_require_tenant(request).tenant_id,
        )
        return {
            "org_id": org_id,
            "org_name": org.name,
            "week_ending": brief.week_ending,
            "health_summary": brief.health_summary,
            "accomplishments": brief.accomplishments,
            "risks": brief.risks,
            "opportunities": brief.opportunities,
            "recommendations": brief.recommendations,
            "kpi_trends": brief.kpi_trends,
            "generation_method": brief.generation_method,
            "generated_at": brief.generated_at,
        }


# ── N5/N6/N9: Intelligence endpoints ────────────────────────────────────────


@router.get(
    "/{org_id}/intelligence/work",
    operation_id="org_discover_work",
    summary="N9/N6 — Discover work + score by Work Value Engine",
    status_code=status.HTTP_200_OK,
)
async def org_discover_work(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Run the Work Discovery Pipeline (N9) and return ranked work items
    scored by the Work Value Engine (N6)."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.discover_work") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        health = await service.get_org_health(org_id)

        from app.org.intelligence import get_work_discovery

        pipeline = get_work_discovery()
        items = await pipeline.discover(org_id, health)

        return {
            "org_id": org_id,
            "discovered_count": len(items),
            "work_items": [
                {
                    "id": i.id,
                    "title": i.title,
                    "source": i.source,
                    "urgency": i.urgency,
                    "strategic_fit": i.strategic_fit,
                    "estimated_cost_usd": i.estimated_cost,
                    "estimated_roi_usd": i.estimated_roi,
                    "risk_level": i.risk_level,
                    "value_score": i.value_score,
                    "discovered_at": i.discovered_at,
                }
                for i in items
            ],
        }


@router.get(
    "/{org_id}/intelligence/capabilities",
    operation_id="org_capability_graph",
    summary="N5 — Capability graph and gap analysis",
    status_code=status.HTTP_200_OK,
)
async def org_capability_graph(
    org_id: str,
    request: Request,
    required: str = Query(
        default="", description="Comma-separated required capabilities for gap analysis"
    ),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the org capability graph and optional gap analysis.

    Built per request from this org's durable rows (a08-F181-01): its
    ``org_capabilities`` plus each active department's ``capability_domains``.
    The old process-global graph was never written and not tenant-keyed.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.capability_graph") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        from app.org.intelligence import CapabilityGraph, CapabilityNode

        graph = CapabilityGraph()
        for cap in await service.list_capabilities(org_id):
            graph.add_capability(
                CapabilityNode(
                    capability_id=str(cap.id),
                    name=str(cap.name),
                    domain=str(cap.domain or ""),
                )
            )
        for dept in await service.list_departments(org_id):
            domains = getattr(dept, "capability_domains", None)
            for domain in domains if isinstance(domains, list) else []:
                graph.add_capability(
                    CapabilityNode(
                        capability_id=f"dept:{dept.id}:{domain}",
                        name=str(domain),
                        domain=str(domain),
                        agent_count=0,
                    )
                )
        caps = graph.all_capabilities()

        result: dict[str, object] = {
            "org_id": org_id,
            "capabilities": caps,
            "total": len(caps),
        }

        if required:
            req_list = [r.strip() for r in required.split(",") if r.strip()]
            result["gap_analysis"] = graph.gap_analysis(req_list)

        return result


@router.get(
    "/{org_id}/intelligence/decisions",
    operation_id="org_decision_history",
    summary="SUPP-J Decision Intelligence — decision history and quality report",
    status_code=status.HTTP_200_OK,
)
async def org_decision_history(
    org_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the organisation's most recent recorded decisions.

    Reads ``org_decisions`` (tenant + org scoped, newest first, bounded by
    ``limit``) instead of a process-global recorder nothing wrote
    (a08-F181-01). The report summarises the returned window by approval status.
    """
    from collections import Counter

    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.decision_history") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        rows = await service.list_decisions(org_id, limit=limit)
        decisions = [
            {
                "decision_id": str(d.id),
                "decision_type": d.decision_type,
                "description": d.description or "",
                "why": d.why or "",
                "entity_type": d.entity_type,
                "entity_id": d.entity_id,
                "risk_level": d.risk_level,
                "autonomy_level": d.autonomy_level,
                "outcome": d.approval_status,
                "actor_agent_id": d.actor_agent_id,
                "made_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
        by_status = Counter(str(d["outcome"]) for d in decisions)
        return {
            "org_id": org_id,
            "decisions": decisions,
            "quality_report": {
                "total": len(decisions),
                "window": limit,
                "by_status": dict(by_status),
            },
        }


# ── W6: Batch Operations ────────────────────────────────────────────────────


class _BatchMissionCreate(BaseModel):
    missions: list[dict[str, object]]


@router.post(
    "/{org_id}/missions/batch",
    operation_id="org_batch_create_missions",
    summary="W6 Batch Operations — create multiple missions in one request",
    status_code=status.HTTP_201_CREATED,
)
async def org_batch_create_missions(
    org_id: str,
    body: _BatchMissionCreate,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Create up to 20 missions atomically. All succeed or all fail.
    Ideal for initialising orgs from templates or importing existing plans.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.batch_create_missions") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("batch_size", len(body.missions))

        if len(body.missions) > 20:
            raise HTTPException(
                status_code=422,
                detail={
                    "type": "batch-too-large",
                    "title": "Batch too large",
                    "status": 422,
                    "detail": "Maximum 20 missions per batch.",
                    "request_id": x_request_id,
                },
            )

        created = []
        for spec in body.missions:
            m = await service.create_mission(
                org_id=org_id,
                title=str(spec.get("title", ""))[:120],
                objective=str(spec.get("objective", "")),
                priority=str(spec.get("priority", "medium")),
                source="batch",
            )
            created.append({"id": str(m.id), "title": m.title})

        span.set_attribute("created_count", len(created))
        return {
            "org_id": org_id,
            "created": created,
            "count": len(created),
            "request_id": x_request_id,
        }


# ── U8: KG Versioning / Time Travel ─────────────────────────────────────────


@router.post(
    "/{org_id}/graph/version",
    operation_id="org_graph_snapshot",
    summary="U8 KG Versioning — snapshot the org knowledge graph",
    status_code=status.HTTP_201_CREATED,
)
async def org_graph_snapshot(
    org_id: str,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Save a point-in-time snapshot of the org knowledge graph.
    Supports rollback and time-travel queries via version history.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.graph_snapshot") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("org_id", org_id)
        await _owned_org_or_404(service, org_id, x_request_id)

        from app.knowledge_graph.store import kg_store

        # Read through the store, not its private dicts: with a database wired
        # the graph is not held in process, and reading _tenant_nodes snapshotted
        # whatever subset this replica happened to have (often nothing).
        page = await kg_store.aexport(tenant_id, limit=200)
        sample_ids = [n.node_id for n in page["nodes"]]
        node_total = await kg_store.acount_nodes(tenant_id)

        # Persisted, tenant-scoped (RLS) history — was a process-global
        # in-memory store keyed by org id alone, readable by any tenant.
        rec = await service.save_graph_version(
            org_id,
            snapshot={"node_ids": sample_ids, "node_count": node_total},
            changed_by="api",
            change_reason="manual snapshot",
        )
        span.set_attribute("version_num", int(rec.version_num))
        created_at = getattr(rec, "created_at", None)
        return {
            "org_id": org_id,
            "version_id": str(rec.id),
            "version_num": rec.version_num,
            "node_count": node_total,
            "content_hash": rec.content_hash,
            "created_at": created_at.isoformat() if created_at is not None else None,
        }


@router.get(
    "/{org_id}/graph/versions",
    operation_id="org_graph_version_history",
    summary="U8 KG Versioning — list version history for the org graph",
    status_code=status.HTTP_200_OK,
)
async def org_graph_version_history(
    org_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the version history of the org's knowledge graph (caller's tenant only)."""
    _require_tenant(request)
    await _owned_org_or_404(service, org_id)
    return {
        "org_id": org_id,
        "versions": await service.list_graph_versions(org_id, limit=limit),
    }


# ── PART 14: Department Memory (6th memory tier) ─────────────────────────────


class _DeptMemoryAdd(BaseModel):
    content: str
    source: str
    confidence: float = 0.9
    tags: list[str] = []


@router.get(
    "/{org_id}/departments/{dept_id}/memory",
    operation_id="org_dept_memory_list",
    summary="PART 14 — List department memory entries",
)
async def org_dept_memory_list(
    org_id: str,
    dept_id: str,
    request: Request,
    active_only: bool = Query(default=True),
    limit: int = Query(default=20, ge=1, le=100),
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Return the persistent knowledge stored for this department."""
    ctx = _require_tenant(request)
    tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
    from app.memory.dept_memory import get_dept_memory

    dm = get_dept_memory()
    entries = await dm.list_entries_async(
        dept_id, tenant_id, active_only=active_only, limit=limit
    )
    active = [e for e in entries if e["is_active"]]
    summary = {
        "dept_id": dept_id,
        "total_entries": len(entries),
        "active_entries": len(active),
        "avg_confidence": round(
            sum(e["confidence"] for e in active) / len(active), 3
        )
        if active
        else 0.0,
    }
    return {"dept_id": dept_id, "entries": entries, "summary": summary}


@router.post(
    "/{org_id}/departments/{dept_id}/memory",
    operation_id="org_dept_memory_add",
    summary="PART 14 — Add a department memory entry",
    status_code=status.HTTP_201_CREATED,
)
async def org_dept_memory_add(
    org_id: str,
    dept_id: str,
    body: _DeptMemoryAdd,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Add new persistent knowledge to a department's memory store."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.dept_memory.add") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("dept_id", dept_id)

        from app.memory.dept_memory import (
            DepartmentMemoryBlockedError,
            DepartmentMemoryUnavailableError,
            get_dept_memory,
        )

        dm = get_dept_memory()
        try:
            entry = await dm.add(
                dept_id=dept_id,
                org_id=org_id,
                tenant_id=tenant_id,
                content=body.content,
                source=body.source,
                confidence=body.confidence,
                tags=body.tags,
            )
        except DepartmentMemoryBlockedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DepartmentMemoryUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:  # below the promotion confidence threshold
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "entry_id": entry.entry_id,
            "dept_id": dept_id,
            "confidence": entry.confidence,
            "created_at": entry.created_at,
        }


# ── Integration Point 3: Real MCP WebSocket Endpoint ──────────────────────────
#
# This is the actual WS transport layer for OrgMCPServer.
# Clients: Claude Desktop, Cursor, any JSON-RPC 2.0 / MCP client.
# Auth: "Authorization: Bearer <api_key>" header or ?api_key= query param.


@router.websocket("/{org_id}/mcp")
async def org_mcp_websocket(
    org_id: str,
    websocket: WebSocket,
    api_key: str | None = None,  # ?api_key= query param fallback
) -> None:
    """WebSocket MCP server for the organisation.

    Speaks JSON-RPC 2.0 / MCP protocol:
      → { "id": 1, "method": "tools/list" }
      ← { "id": 1, "result": { "tools": [...] } }

      → { "id": 2, "method": "tools/call", "params": { "name": "get_status", "arguments": {} } }
      ← { "id": 2, "result": { "content": [...] } }
    """
    import json as _json

    from app.gateway.mcp_server import OrgMCPServer

    # Authenticate BEFORE accepting. HTTP middleware never runs for WebSocket
    # connections, and this handler used to accept everyone and take the tenant
    # from an attacker-controlled X-Tenant-Id header (default "system") — while
    # OrgMCPServer never validated the key. Anyone could connect, name any
    # tenant, and read org status or START MISSIONS in it. The tenant now comes
    # only from a verified API key, and the org must belong to that tenant.
    from app.tenancy.ws_auth import resolve_ws_tenant

    # write=True: this socket can start missions (needs a role that may write).
    tenant_ctx = await resolve_ws_tenant(websocket, write=True)
    if tenant_ctx is None:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    tenant_id = str(tenant_ctx.tenant_id)
    if not await _org_owned(websocket.app, org_id, tenant_id):
        await websocket.close(code=4404, reason="Organization not found")
        return
    await websocket.accept()

    # Attach the request's app.state (lifespan-wired services) for live service
    # injection — not the module-level app.main.app singleton.
    _app_state = None
    with contextlib.suppress(Exception):
        _app_state = getattr(websocket.app, "state", None)

    mcp_server = OrgMCPServer(
        org_id=org_id,
        api_key=api_key,
        app_state=_app_state,
        tenant_id=tenant_id,
        tenant_ctx=tenant_ctx,
    )

    _log = structlog.get_logger(__name__)
    _log.info("mcp.ws.connected", org_id=org_id, tenant_id=tenant_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _json.loads(raw)
            except Exception:
                await websocket.send_text(
                    _json.dumps(
                        {
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
                )
                continue

            msg_id = msg.get("id")
            method = msg.get("method", "")
            params = msg.get("params", {})

            # ── MCP protocol methods ──────────────────────────────────────────
            if method == "initialize":
                resp = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "agentverse-org-mcp", "version": "1.0.0"},
                }

            elif method == "tools/list":
                resp = {"tools": mcp_server.list_tools()}

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                resp = await mcp_server.call_tool(tool_name, arguments)

            elif method == "resources/list":
                try:
                    from app.gateway.mcp_server.resources import OrgMCPResources

                    res_server = OrgMCPResources(org_id)
                    resp = {"resources": res_server.list_resources()}
                except Exception:
                    resp = {"resources": []}

            elif method == "resources/read":
                try:
                    from app.gateway.mcp_server.resources import OrgMCPResources

                    res_server = OrgMCPResources(org_id)
                    resp = await res_server.read_resource(params.get("uri", ""))
                except Exception as exc:
                    resp = {"error": str(exc)[:100]}

            elif method == "prompts/list":
                try:
                    from app.gateway.mcp_server.resources import OrgMCPPrompts

                    prompt_server = OrgMCPPrompts(org_id)
                    resp = {"prompts": prompt_server.list_prompts()}
                except Exception:
                    resp = {"prompts": []}

            elif method == "prompts/get":
                try:
                    from app.gateway.mcp_server.resources import OrgMCPPrompts

                    prompt_server = OrgMCPPrompts(org_id)
                    resp = await prompt_server.get_prompt(
                        params.get("name", ""), params.get("arguments", {})
                    )
                except Exception as exc:
                    resp = {"error": str(exc)[:100]}

            elif method in ("notifications/initialized", "ping"):
                resp = {}

            else:
                resp = {
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

            envelope: dict = {"jsonrpc": "2.0"}
            if msg_id is not None:
                envelope["id"] = msg_id
            envelope["result"] = resp

            await websocket.send_text(_json.dumps(envelope))

    except WebSocketDisconnect:
        _log.info("mcp.ws.disconnected", org_id=org_id)
    except Exception as exc:
        _log.error("mcp.ws.error", org_id=org_id, error=str(exc)[:150])
        with contextlib.suppress(Exception):
            await websocket.send_text(
                _json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32603, "message": "Internal error"},
                    }
                )
            )


# ── Mission attachments — upload a file an agent can OCR/process at run time ──

# What a mission agent can actually read via the extract_document / vision tools.
_ATTACHMENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post(
    "/{org_id}/attachments",
    operation_id="org_upload_attachment",
    summary="Upload a file (image/PDF/doc) a mission's agent can read via its OCR/"
    "vision tools; returns an attachment_id to reference in the mission objective",
    status_code=status.HTTP_201_CREATED,
)
async def org_upload_attachment(
    org_id: str,
    request: Request,
    file: UploadFile = File(...),
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> dict[str, Any]:
    """Accept one mission attachment, validate its type/size, and store it durably.

    The bytes go to ``org_attachments`` (Postgres, RLS-forced, retention purge) in
    the request's tenant-scoped transaction, so any replica or worker can read
    them. The agent reads the file with ``extract_document(attachment_id=...)``;
    no host filesystem path is written or returned (a08-F177-01). A failed write
    is an error, never a 201.
    """
    import os

    ctx = _require_tenant(request)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = _ATTACHMENT_TYPES.get(content_type)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported file type. Allowed: images (PNG/JPEG/WebP/GIF), PDF, "
                "TXT, CSV, Markdown, JSON, DOCX."
            ),
        )

    data = await file.read(_ATTACHMENT_MAX_BYTES + 1)
    if len(data) > _ATTACHMENT_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Attachment exceeds the 25 MB limit.",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    # Preserve a display name (sanitized) without trusting it as a path.
    raw_name = os.path.basename(file.filename or "").strip()
    display_name = "".join(
        c for c in raw_name if c.isalnum() or c in " ._-()"
    ).strip() or f"attachment{ext}"

    try:
        org_uuid = uuid_mod.UUID(org_id)
    except ValueError as exc:
        raise _not_found("Organization", org_id) from exc
    record = await service.add_attachment(
        org_id=org_uuid,
        filename=display_name,
        content_type=content_type,
        content=data,
        uploaded_by=getattr(ctx, "api_key_id", None),
    )
    return {**record, "ref": f"org-attachment:{record['attachment_id']}"}


# ── create_mission_and_execute REST endpoint ─────────────────────────────────


class _MissionExecuteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=2000)
    why: str = Field(default="", max_length=1000)
    expected_outcome: str = Field(default="", max_length=1000)
    priority: str = Field(default="medium")
    dept_id: str | None = None
    assigned_team_id: str | None = None
    autonomy_level: int | None = None
    budget_usd: float | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _MissionPreviewRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


@router.post(
    "/{org_id}/missions/preview",
    operation_id="org_preview_mission",
    summary="Fast pre-flight estimate for a mission goal (no dispatch)",
)
async def org_preview_mission(
    org_id: str,
    body: _MissionPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    """Return an instant heuristic estimate for a goal — team size, departments,
    cost, duration, and risk — WITHOUT creating or dispatching a mission.

    Powers the Cmd+K command bar's preview step. This is a fast keyword
    heuristic (no LLM, no DB writes); the real team is formed by the
    MetaOrchestrator when the mission is actually launched.
    """
    _require_tenant(request)  # authn/tenant scoping only; estimate is stateless
    from app.org.loop_detector import OrgSimulationEngine

    est = await OrgSimulationEngine().estimate_mission(body.goal, org_id=org_id)
    # NB: `similar_missions_count` is a random placeholder in the engine — omit it
    # so the preview never shows a fabricated number.
    return {
        "departments": est.departments_needed,
        "estimated_agents": est.estimated_agents,
        "estimated_duration_hours": est.estimated_duration_hours,
        "estimated_cost_usd": est.estimated_cost_usd,
        "estimated_risk": est.estimated_risk,
        "confidence": est.confidence,
        "success_probability": est.success_probability,
        "potential_blockers": est.potential_blockers,
    }


@router.post(
    "/{org_id}/missions/execute",
    operation_id="org_create_mission_execute",
    summary="Create a mission AND immediately dispatch it to the agent engine",
    status_code=status.HTTP_201_CREATED,
)
async def org_create_mission_execute(
    org_id: str,
    body: _MissionExecuteRequest,
    request: Request,
) -> dict[str, Any]:
    """Create an OrgMission and return immediately; a Celery worker forms the
    team (LLM, 30-90s) and dispatches the goal.

    The heavy work runs in the worker — a separate process with its own event
    loop and DB connections — so the request returns in milliseconds and the
    New Mission drawer never hangs. The mission appears over SSE right away
    (mission.created) as ``planned`` and streams to ``active`` when the worker
    dispatches it.
    """
    log = structlog.get_logger(__name__)
    ctx = _require_tenant(request)
    tenant_id: str = getattr(ctx, "tenant_id", "") or getattr(ctx, "id", "")

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()

    # Persist + COMMIT the mission in its own session so the worker (a separate
    # process) is guaranteed to see it when it picks up the task.
    async with db() as sess, sess.begin(), sqlalchemy_rls_context(sess, tenant_id):
        svc = OrgService(session=sess, tenant_id=tenant_id)
        mission = await svc.create_mission(
            org_id=org_id,
            title=body.title,
            objective=body.objective,
            why=body.why,
            expected_outcome=body.expected_outcome,
            priority=body.priority,
            dept_id=body.dept_id,
            assigned_team_id=body.assigned_team_id,
            autonomy_level=body.autonomy_level,
            budget_usd=body.budget_usd,
            tags=body.tags,
            metadata=body.metadata,
            source="api",
        )
        mission_id = str(mission.id)
        mission_title = mission.title
        await svc.update_mission_status(mission_id, "planned")

    # Dispatch mode. With a real Celery worker (the goal service carries a task
    # queue), offload the slow LLM team-formation + dispatch and return
    # immediately as 'planning' (the worker streams the mission to 'active'). In a
    # single-process deployment or a test with no worker, offloading would strand
    # the mission at 'planned' forever — nothing would consume the queued task — so
    # dispatch INLINE and return the real goal_id/team_id. Awaited inline dispatch
    # (not the old fire-and-forget background task) does not hit the pool-contention
    # hang that motivated the worker offload.
    goal_svc = getattr(request.app.state, "goal_service", None)
    worker_available = goal_svc is not None and getattr(goal_svc, "_task_queue", None) is not None

    if worker_available:
        queued = True
        try:
            from app.scaling.tasks import execute_org_mission

            execute_org_mission.apply_async(
                kwargs={
                    "mission_id": mission_id,
                    "tenant_id": tenant_id,
                    "org_id": org_id,
                    "objective": body.objective,
                    "title": body.title,
                    "expected_outcome": body.expected_outcome,
                    "dept_id": body.dept_id,
                    "assigned_team_id": body.assigned_team_id,
                    "autonomy_level": body.autonomy_level,
                    "priority": body.priority,
                },
            )
        except Exception as exc:  # broker unavailable — mission stays 'planned'
            queued = False
            log.error("org.execute.enqueue_failed", mission_id=mission_id, error=str(exc)[:200])

        return {
            "mission_id": mission_id,
            "title": mission_title,
            "status": "planned",
            "goal_id": None,
            "team_id": None,
            "dispatched": False,
            "planning": True,
            "warning": None if queued else "dispatch_queue_unavailable",
        }

    # ── No worker: form the team + dispatch the goal inline, synchronously ──
    dispatch: dict[str, Any] = {}
    try:
        from sqlalchemy import text as _sa_text

        async with db() as sess, sess.begin(), sqlalchemy_rls_context(sess, tenant_id):
            # This transaction spans the LLM-heavy team-formation (multi-second,
            # idle DB meanwhile). The engine's default 30s
            # idle_in_transaction_session_timeout would reclaim the connection
            # mid-planning ("Can't operate on closed transaction"); disable it for
            # THIS transaction only (SET LOCAL reverts on commit) — mirrors the
            # worker path in scaling/tasks.execute_org_mission.
            await sess.execute(_sa_text("SET LOCAL idle_in_transaction_session_timeout = 0"))
            svc = OrgService(session=sess, tenant_id=tenant_id)
            mission = await svc.get_mission(mission_id)
            dispatch = await svc.form_team_and_dispatch(
                mission=mission,
                org_id=org_id,
                objective=body.objective,
                title=body.title,
                expected_outcome=body.expected_outcome,
                dept_id=body.dept_id,
                assigned_team_id=body.assigned_team_id,
                autonomy_level=body.autonomy_level,
                priority=body.priority,
                tenant_ctx=ctx,
                app_state=request.app.state,
            )
    except Exception as exc:
        log.error("org.execute.inline_dispatch_failed", mission_id=mission_id, error=str(exc)[:200])
        dispatch = {"error": str(exc)[:200]}

    dispatched_goal_id = dispatch.get("goal_id")
    return {
        "mission_id": mission_id,
        "title": mission_title,
        "status": "active" if dispatched_goal_id else "planned",
        "goal_id": dispatched_goal_id,
        "team_id": dispatch.get("team_id"),
        "agent_ids": dispatch.get("agent_ids", []),
        "topology": dispatch.get("topology"),
        "departments": dispatch.get("departments", []),
        "agent_count": dispatch.get("agent_count"),
        "autonomy_level": dispatch.get("autonomy_level"),
        "estimated_cost_usd": dispatch.get("estimated_cost_usd"),
        "dispatched": bool(dispatched_goal_id),
        "planning": False,
        "warning": dispatch.get("error"),
    }


# ── Mission schedules — autonomous cron-driven missions ──────────────────────


class _SchedulePublishConfig(BaseModel):
    """Where a scheduled mission publishes its deliverable each run.

    ``arguments`` is a tool-argument template; any string value may contain the
    tokens ``{{deliverable}}``, ``{{title}}`` or ``{{objective}}``, substituted
    at publish time. Publishing starts gated — the first run holds until the org
    approves it via the approve-publishing endpoint (``approved`` cannot be set
    to true at creation; that is a deliberate, separate confirmation step).
    """

    connector_server_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class _MissionScheduleRequest(BaseModel):
    """Create-schedule body.

    Every field is bounded to what ``org_mission_schedules`` can store and what
    the mission it launches accepts (``priority`` / ``autonomy_level`` mirror
    ``CreateMissionRequest``). Previously ``priority`` was unbounded (a value over
    the VARCHAR(20) column was a DB truncation error → 500), ``autonomy_level``
    accepted any integer (int32 overflow → 500), a non-UUID ``dept_id`` crashed
    ``uuid.UUID()`` in the service (→ 500), and an unknown ``timezone`` was
    stored and then silently evaluated as UTC.
    """

    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(default="", max_length=2000)
    cron_expression: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    dept_id: str | None = None
    name: str = Field(default="", max_length=200)
    enabled: bool = True
    publish: _SchedulePublishConfig | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception as exc:  # ZoneInfoNotFoundError, ValueError on bad keys
            raise ValueError(f"unknown timezone {value!r} (use an IANA name)") from exc
        return value

    @field_validator("dept_id")
    @classmethod
    def _dept_id_is_uuid(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            return str(uuid_mod.UUID(value))
        except ValueError as exc:
            raise ValueError("dept_id must be a UUID") from exc


class _ScheduleToggleRequest(BaseModel):
    enabled: bool


class _SchedulePublishApproval(BaseModel):
    approved: bool = True


def _schedule_to_dict(s: Any) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "org_id": str(s.org_id),
        "name": s.name,
        "title": s.title,
        "objective": s.objective,
        "priority": s.priority,
        "autonomy_level": s.autonomy_level,
        "cron_expression": s.cron_expression,
        "timezone": s.timezone,
        "enabled": s.enabled,
        "next_fire_at": s.next_fire_at.isoformat() if s.next_fire_at else None,
        "last_fired_at": s.last_fired_at.isoformat() if s.last_fired_at else None,
        "last_mission_id": str(s.last_mission_id) if s.last_mission_id else None,
        "fire_count": s.fire_count,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "publish": _publish_config_to_dict(s.publish_config),
    }


def _publish_config_to_dict(cfg: Any) -> dict[str, Any] | None:
    """Expose a schedule's publish target without leaking argument secrets.

    Argument values are templates the org authored (they may embed the
    deliverable placeholder), so they are safe to echo; but we never echo any
    resolved secret — connectors resolve their own credentials at call time from
    the vault, so none live here. We surface only the shape + approval state.
    """
    if not isinstance(cfg, dict) or not cfg.get("connector_server_id"):
        return None
    return {
        "connector_server_id": str(cfg.get("connector_server_id", "")),
        "tool_name": str(cfg.get("tool_name", "")),
        "arguments": cfg.get("arguments") or {},
        "approved": bool(cfg.get("approved", False)),
    }


def _validate_cron(expr: str) -> None:
    from croniter import croniter

    if not croniter.is_valid(expr):
        # HTTP_422_UNPROCESSABLE_CONTENT, not the deprecated ..._ENTITY alias:
        # touching the deprecated name emits StarletteDeprecationWarning, which
        # under warnings-as-errors (the test suites, the e2e sweep) turned every
        # invalid cron into an unhandled 500 instead of this 422.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid cron expression: {expr!r}",
        )


def _schedule_id_or_404(schedule_id: str) -> str:
    """Reject a schedule id that cannot exist before it reaches the service.

    Schedule ids are UUIDs; the service does ``uuid.UUID(schedule_id)``, so any
    other string raised ValueError there — an unhandled 500 for what is simply
    an unknown schedule.
    """
    try:
        return str(uuid_mod.UUID(schedule_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


@router.post(
    "/{org_id}/schedules",
    operation_id="org_create_schedule",
    status_code=status.HTTP_201_CREATED,
    summary="Create a cron schedule that autonomously launches a mission",
)
async def org_create_schedule(
    org_id: str,
    body: _MissionScheduleRequest,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, Any]:
    _require_tenant(request)
    _validate_cron(body.cron_expression)
    # IDOR guard: the org must belong to this tenant (get_organization is
    # tenant-scoped via RLS) before we create a schedule under it.
    if await service.get_organization(org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    publish_config = None
    if body.publish is not None:
        # Starts unapproved regardless of client input — approval is a separate,
        # deliberate step (POST …/approve-publishing), never granted at creation.
        publish_config = {
            "connector_server_id": body.publish.connector_server_id,
            "tool_name": body.publish.tool_name,
            "arguments": body.publish.arguments,
            "approved": False,
        }
    sched = await service.create_mission_schedule(
        org_id=org_id,
        title=body.title,
        objective=body.objective,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        priority=body.priority,
        autonomy_level=body.autonomy_level,
        dept_id=body.dept_id,
        name=body.name,
        enabled=body.enabled,
        publish_config=publish_config,
    )
    return _schedule_to_dict(sched)


@router.get("/{org_id}/schedules", operation_id="org_list_schedules")
async def org_list_schedules(
    org_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> list[dict[str, Any]]:
    _require_tenant(request)
    return [_schedule_to_dict(s) for s in await service.list_mission_schedules(org_id)]


@router.patch("/{org_id}/schedules/{schedule_id}", operation_id="org_toggle_schedule")
async def org_toggle_schedule(
    org_id: str,
    schedule_id: str,
    body: _ScheduleToggleRequest,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, Any]:
    _require_tenant(request)
    schedule_id = _schedule_id_or_404(schedule_id)
    sched = await service.set_mission_schedule_enabled(org_id, schedule_id, body.enabled)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _schedule_to_dict(sched)


@router.delete(
    "/{org_id}/schedules/{schedule_id}",
    operation_id="org_delete_schedule",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def org_delete_schedule(
    org_id: str,
    schedule_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> None:
    _require_tenant(request)
    schedule_id = _schedule_id_or_404(schedule_id)
    if not await service.delete_mission_schedule(org_id, schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")


@router.post(
    "/{org_id}/schedules/{schedule_id}/approve-publishing",
    operation_id="org_approve_schedule_publishing",
    summary="Approve (or revoke) autonomous publishing for a schedule",
)
async def org_approve_schedule_publishing(
    org_id: str,
    schedule_id: str,
    body: _SchedulePublishApproval,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, Any]:
    """One-time gate: approve a schedule so its runs publish autonomously.

    Approving also releases any earlier run whose deliverable is waiting at the
    gate — those missions publish immediately. Revoking (``approved=false``)
    re-arms the gate for future runs without unpublishing anything already sent.
    """
    _require_tenant(request)
    schedule_id = _schedule_id_or_404(schedule_id)
    sched = await service.approve_schedule_publishing(org_id, schedule_id, body.approved)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    released: list[str] = []
    if body.approved:
        released = await service.release_pending_publish_missions(org_id, schedule_id)
        if released:
            from app.scaling.tasks import publish_mission_deliverable

            # Use the service's tenant id — the exact format the worker's RLS
            # context expects (matches what the beat/execute tasks pass).
            for mid in released:
                publish_mission_deliverable.apply_async(
                    kwargs={"mission_id": mid, "tenant_id": service._tenant_id},
                )
    out = _schedule_to_dict(sched)
    out["released_missions"] = released
    return out


# ── WS-2b: Mission finalize — reconcile subtasks vs real goal + aggregate ─────


@router.post(
    "/{org_id}/missions/{mission_id}/finalize",
    operation_id="org_mission_finalize",
    summary="Reconcile a mission's subtasks against its real goal outcome and "
    "aggregate the deliverable",
    status_code=status.HTTP_200_OK,
)
async def org_finalize_mission(
    org_id: str,
    mission_id: str,
    request: Request,
    service: OrgService = Depends(get_org_service),
    x_request_id: str = Header(default_factory=_request_id),
) -> dict[str, Any]:
    """Pull the dispatched goal's terminal status, mark subtasks to match, and —
    when terminal — aggregate a real deliverable/report onto the mission and emit
    mission.progress (100%) + mission.completed / mission.failed.

    Idempotent-ish: safe to call repeatedly; returns ``finalized=False`` while the
    goal is still running.
    """
    mission = await service.get_mission(mission_id)
    if not mission:
        raise _not_found("Mission", mission_id, x_request_id)
    # Scope to THIS org, not just the tenant — see get_mission above.
    if str(mission.org_id) != org_id:
        raise _not_found("Mission", mission_id, x_request_id)
    ctx = _require_tenant(request)
    tenant_ctx = ctx if hasattr(ctx, "tenant_id") else None
    result = await service.finalize_mission(
        mission_id, app_state=request.app.state, tenant_ctx=tenant_ctx
    )
    return result
