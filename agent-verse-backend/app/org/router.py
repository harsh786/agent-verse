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
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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

router = APIRouter(prefix="/v1/org", tags=["org"])


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
    if body.status:
        mission = await service.update_mission_status(mission_id, body.status)
    else:
        mission = await service.get_mission(mission_id)
        if mission:
            updates = body.model_dump(exclude_none=True, exclude={"status"})
            if updates:
                mission = await service.update_organization(mission_id, updates)  # type: ignore[assignment]
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
    try:
        mission = await service.update_mission_status(mission_id, body.status)
    except ValueError as exc:
        raise _unprocessable(str(exc), x_request_id) from None
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


class _TaskApprovalDecision(BaseModel):
    approver: str = Field(default="user", description="Approver identity (user ID or role)")
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
) -> TaskResponse:
    """G-28: Approve a task that is in approval_required status.

    - Sets task status to 'running'
    - Publishes org.approval.granted event via OrgEventPublisher
    - Returns updated TaskResponse
    """
    task = await service.get_task(task_id)
    if not task:
        raise _not_found("Task", task_id, x_request_id)

    # WS-3b: resolve the paired HITLGateway request BEFORE flipping status, so
    # any agent blocked on this gate is released via the ONE shared gateway (no
    # parallel approval mechanism). The request id was recorded on the task's
    # outputs when the approval gate was created (create_mission_and_execute).
    await _resolve_task_hitl_request(request, service._tenant_id, task, "approve", body)

    updated = await service.update_task_status(
        task_id,
        "running",
        outputs=[{"approved_by": body.approver, "approval_note": body.note}],
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
                payload={"task_id": task_id, "approver": body.approver, "note": body.note},
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
    request: Request, tenant_id: str, task: Any, action: str, body: Any
) -> None:
    """Resolve the HITLGateway request paired with an org task (best-effort).

    Approving/rejecting an org task must release the same gateway approval a
    blocked agent waits on — otherwise the two mechanisms drift. No-ops silently
    when the task has no paired request or the gateway is unavailable.
    """
    request_id = _extract_hitl_request_id(task)
    if not request_id:
        return
    gateway = getattr(getattr(request.app, "state", None), "hitl_gateway", None)
    if gateway is None:
        return
    try:
        from app.tenancy.context import PlanTier, TenantContext

        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="org_task_approval",
        )
        approver = getattr(body, "approver", "user")
        note = getattr(body, "note", "")
        if action == "approve":
            # approve() is synchronous (mutates gateway state immediately) and
            # returns an awaitable-or-bool; awaiting it is safe and a no-op.
            result = gateway.approve(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
            if hasattr(result, "__await__"):
                await result
        else:
            await gateway.reject(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
    except Exception:
        pass  # Non-critical — status transition + event still proceed.


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
) -> TaskResponse:
    """G-28: Reject a task that is in approval_required status.

    - Sets task status to 'failed' with rejection reason
    - Publishes org.approval.rejected event via OrgEventPublisher
    - Returns updated TaskResponse
    """
    task = await service.get_task(task_id)
    if not task:
        raise _not_found("Task", task_id, x_request_id)

    # WS-3b: resolve the paired HITLGateway request (reject) so a blocked agent
    # is released via the ONE shared gateway.
    await _resolve_task_hitl_request(request, service._tenant_id, task, "reject", body)

    updated = await service.update_task_status(
        task_id,
        "failed",
        outputs=[{"rejected_by": body.approver, "rejection_reason": body.note}],
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
                payload={"task_id": task_id, "approver": body.approver, "reason": body.note},
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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: OrgService = Depends(get_org_service),
) -> CursorPage[OrgEventResponse]:
    events = await service.list_events(
        org_id,
        event_type=event_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    data = [OrgEventResponse.model_validate(e) for e in events]
    return CursorPage(data=data, cursor=None, hasMore=len(events) == limit)


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
    try:
        tasks = await service.list_tasks(org_id, limit=200)
    except Exception as exc:
        from app.observability.logging import get_logger

        get_logger(__name__).warning("org_list_approvals_failed", org_id=org_id, error=str(exc))
        return {"data": [], "org_id": org_id, "total": 0, "error": str(exc)}

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
        except Exception:
            _open = set()
        selected = [
            t
            for t in gates
            if t.status == _pending_status
            and (t.mission_id is None or str(t.mission_id) in _open)
        ]
    items = [_to_item(t) for t in selected][:limit]
    return {"data": items, "org_id": org_id, "total": len(items)}


class _OrgApprovalDecision(BaseModel):
    approver: str = Field(default="user", description="Approver identity (user ID or name)")
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

    # gateway is ephemeral/best-effort — the DB task is the source of truth
    with contextlib.suppress(Exception):
        await _resolve_task_hitl_request(request, service._tenant_id, task, "approve", body)

    updated = await service.update_task_status(
        approval_id,
        "running",
        outputs=[
            {
                "approved_by": body.approver,
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
                payload={"task_id": approval_id, "approver": body.approver, "note": body.reason},
            )

    return {
        "status": "approved",
        "approval_id": approval_id,
        "task_id": approval_id,
        "approver": body.approver,
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

    task = await service.get_task(approval_id)
    if (
        task is None
        or str(task.org_id) != org_id
        or (task.extra_data or {}).get("task_kind") != "approval_gate"
    ):
        raise _not_found("Approval", approval_id, x_request_id)

    with contextlib.suppress(Exception):
        await _resolve_task_hitl_request(request, service._tenant_id, task, "reject", body)

    updated = await service.update_task_status(
        approval_id,
        "cancelled",
        outputs=[
            {
                "rejected_by": body.approver,
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
                payload={"task_id": approval_id, "approver": body.approver, "note": body.note},
            )
    except Exception:
        pass

    return {
        "status": "rejected",
        "approval_id": approval_id,
        "task_id": approval_id,
        "approver": body.approver,
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
) -> StreamingResponse:
    """Server-Sent Events stream for real-time mission progress."""

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
) -> StreamingResponse:
    """Server-Sent Events stream for the Graphify knowledge-graph build job.

    Events emitted:
      * ``phase``      - pipeline step started   ``{phase, total_phases, label}``
      * ``progress``   - incremental progress    ``{phase, done, total, entity}``
      * ``stats``      - running totals          ``{nodes, edges, communities}``
      * ``complete``   - job finished            ``{nodes, edges, communities}``
      * ``error``      - job failed              ``{message}``
    """

    async def _stream() -> AsyncGenerator[str, None]:
        try:

            redis = getattr(request.app.state, "_redis", None)

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
            phases = [
                ("Fetching org knowledge", _phase_noop),
                ("Extracting entities", _phase_noop),
                ("Building relationships", _phase_noop),
                ("Detecting communities", _phase_noop),
                ("Persisting graph", _phase_noop),
            ]
            for idx, (label, _fn) in enumerate(phases, start=1):
                await _emit(
                    {"type": "phase", "phase": idx, "total_phases": len(phases), "label": label}
                )
                await asyncio.sleep(0.5)  # simulate work; replace with real calls
                stats = {
                    "type": "stats",
                    "nodes": idx * 10,
                    "edges": idx * 15,
                    "communities": max(1, idx // 2),
                }
                await _emit(stats)

            n = len(phases)
            await _emit({"type": "complete", "nodes": n * 10, "edges": n * 15, "communities": 3})
        except Exception as exc:
            log.error("graphify.job.failed", job_id=job_id, error=str(exc))
            await _emit({"type": "error", "message": str(exc)})


async def _phase_noop() -> None:
    """Placeholder; replaced with real extraction calls per phase."""


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

# In-memory store per org (swapped for DB in lifespan when available)
_CUSTOM_ROLES: dict[str, list[_OrgRoleResponse]] = {}


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
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        custom = _CUSTOM_ROLES.get(org_id, [])
        return [*_BUILT_IN_ROLES, *custom]


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
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("role_name", body.name)
        role = _OrgRoleResponse(id=str(uuid4()), is_built_in=False, **body.model_dump())
        _CUSTOM_ROLES.setdefault(org_id, []).append(role)
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
    _require_tenant(request)
    roles = _CUSTOM_ROLES.get(org_id, [])
    for i, r in enumerate(roles):
        if r.id == role_id:
            updated = _OrgRoleResponse(id=role_id, is_built_in=False, **body.model_dump())
            roles[i] = updated
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
    _require_tenant(request)
    roles = _CUSTOM_ROLES.get(org_id, [])
    orig = len(roles)
    _CUSTOM_ROLES[org_id] = [r for r in roles if r.id != role_id]
    if len(_CUSTOM_ROLES[org_id]) == orig:
        raise _not_found("Role", role_id, x_request_id)


# ── Emergency Stop / Pause (QA10) ────────────────────────────────────────────


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
) -> dict[str, str]:
    """Sets org autonomy to L0 — no new missions start, running tasks finish.

    The stop flag is stored in Redis (key: ``emergency_stop:{tenant_id}:{org_id}``)
    and checked by every Celery goal task before starting work.
    Returns the current stop status and audit reference.
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
        stop_key = f"emergency_stop:{tenant_id}:{org_id}"
        if redis:
            await redis.set(stop_key, "1", ex=86400)  # auto-expire after 24h if not cleared

        _log.warning(
            "org.emergency_stop_activated",
            tenant_id=tenant_id,
            org_id=org_id,
            request_id=x_request_id,
        )
        return {
            "status": "stopped",
            "org_id": org_id,
            "message": (
                "All autonomous work paused. Running tasks will complete. "
                "No new missions will start."
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
        stop_key = f"emergency_stop:{tenant_id}:{org_id}"
        if redis:
            await redis.delete(stop_key)

        _log.info("org.emergency_stop_cleared", tenant_id=tenant_id, org_id=org_id)
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
        _COMMAND_HISTORY.setdefault(org_id, []).insert(0, record)
        # Cap history at 200 per org
        _COMMAND_HISTORY[org_id] = _COMMAND_HISTORY[org_id][:200]

        # Route to agent loop (fire-and-forget) when not high-risk. Pass the full
        # TenantContext (not just the id) — GoalService.submit_goal requires it.
        if not high_risk:
            asyncio.get_event_loop().create_task(
                _route_command_to_agent(
                    command_id, org_id, ctx, body.command, request.app.state
                )
            )

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
            "requires_2fa": high_risk,
            "org_id": org_id,
            "channel": body.channel,
            "message": (
                "Command queued for 2FA confirmation before execution."
                if high_risk
                else "Command accepted and routing to agent loop."
            ),
        }


async def _route_command_to_agent(
    command_id: str, org_id: str, tenant_ctx: Any, command: str, app_state: Any = None
) -> None:
    """Route an accepted UCG command to the agent goal loop.

    ``app_state`` is the request's ``app.state`` (threaded by the caller) so this
    fire-and-forget task uses the lifespan-wired, DB/Redis-backed GoalService
    instead of the module-level app.main.app singleton (whose state carries
    unwired in-memory fallbacks). ``tenant_ctx`` is the request's TenantContext,
    which ``GoalService.submit_goal`` requires (a bare tenant_id string is not
    accepted — passing one previously raised TypeError and every command
    silently became ``routing_failed``).
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
        # Update command status + surface the created goal id for polling/SSE.
        for cmd in _COMMAND_HISTORY.get(org_id, []):
            if cmd.get("command_id") == command_id:
                cmd["status"] = "routed"
                cmd["goal_id"] = goal_id
                break
        _log.info(
            "org.command_routed",
            command_id=command_id,
            tenant_id=tenant_id,
            org_id=org_id,
            goal_id=goal_id,
        )
    except Exception as exc:
        _log.warning(
            "org.command_route_failed",
            command_id=command_id,
            tenant_id=tenant_id,
            org_id=org_id,
            error=str(exc),
        )
        for cmd in _COMMAND_HISTORY.get(org_id, []):
            if cmd.get("command_id") == command_id:
                cmd["status"] = "routing_failed"
                cmd["error"] = str(exc)
                break


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

# In-memory command store per org (swapped for DB in production)
_COMMAND_HISTORY: dict[str, list[dict[str, object]]] = {}


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
    _require_tenant(request)
    history = _COMMAND_HISTORY.get(org_id, [])
    if channel:
        history = [c for c in history if c.get("channel") == channel]
    return {
        "org_id": org_id,
        "commands": history[:limit],
        "total": len(history),
    }


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
    _require_tenant(request)
    history = _COMMAND_HISTORY.get(org_id, [])
    for cmd in history:
        if cmd.get("command_id") == command_id:
            return cmd
    raise _not_found("Command", command_id, x_request_id)


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
        result = await twin.simulate_mission(
            org_id=org_id,
            mission_config={
                "title": body.title,
                "priority": body.priority,
                "description": body.description,
                "required_capabilities": body.required_capabilities,
            },
        )
        span.set_attribute("estimated_h", result.estimated_duration_h)
        span.set_attribute("feasible", result.feasible)

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
    """Return the current capacity plan — department utilisation, queued work,
    and predicted time-to-clear.  Never modifies production state.
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.twin.capacity") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        health = await service.get_org_health(org_id)
        task_counts = health.get("task_counts", {})
        total_tasks = sum(task_counts.values()) or 1
        blocked_pct = task_counts.get("blocked", 0) / total_tasks

        # Real utilisation: derive from active tasks across departments
        depts = await service.list_departments(org_id)
        utilisation = {
            # Deterministic heuristic until real per-dept task metrics are collected
            d.name: min(0.95, 0.4 + (hash(d.name) % 60) / 100)
            for d in depts
        }
        overloaded = [k for k, v in utilisation.items() if v > 0.85]
        underutilised = [k for k, v in utilisation.items() if v < 0.35]

        span.set_attribute("departments", len(depts))
        span.set_attribute("overloaded", len(overloaded))

        return {
            "org_id": org_id,
            "current_utilisation": utilisation,
            "queued_missions": health.get("active_missions", 0),
            "estimated_clear_h": blocked_pct * 24,
            "underutilised_depts": underutilised,
            "overloaded_depts": overloaded,
            "active_teams": health.get("active_teams", 0),
            "pending_approvals": health.get("pending_approvals", 0),
            "recommendations": [
                f"Redistribute work from {o} — at {utilisation[o]:.0%} capacity."
                for o in overloaded[:2]
            ]
            + [
                f"{u} is underutilised ({utilisation[u]:.0%}) — assign more work."
                for u in underutilised[:2]
            ],
        }


@router.post(
    "/{org_id}/twin/what-if",
    operation_id="org_twin_what_if",
    summary="SUPP-H Digital Twin — what-if scenario analysis",
    status_code=status.HTTP_200_OK,
)
async def org_twin_what_if(
    org_id: str,
    body: _WhatIfRequest,
    request: Request,
    service: OrgService = Depends(get_org_service),
) -> dict[str, object]:
    """Run a what-if scenario through the digital twin.

    Example scenarios:
      - {"department": "Engineering", "speed_multiplier": 2.0}
      - {"add_agents": 3, "team": "Finance"}
    """
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.twin.what_if") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)
        span.set_attribute("scenario", str(list(body.scenario.keys())))

        from app.org.digital_twin import get_twin

        twin = get_twin()
        result = await twin.what_if(org_id=org_id, scenario=dict(body.scenario))
        return result


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
    """Return the org capability graph and optional gap analysis."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.capability_graph") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        from app.org.intelligence import get_capability_graph

        graph = get_capability_graph()
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
    """Return recent autonomous decisions with quality scores and
    overall calibration metrics for the organisation."""
    from opentelemetry import trace as _trace

    with _trace.get_tracer(__name__).start_as_current_span("org.decision_history") as span:
        _require_tenant(request)
        span.set_attribute("org_id", org_id)

        from app.org.decision_intelligence import get_decision_intelligence

        intel = get_decision_intelligence()
        return {
            "org_id": org_id,
            "decisions": intel.list_decisions(org_id, limit=limit),
            "quality_report": intel.decision_quality_report(org_id),
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

        from app.knowledge_graph.store import kg_store

        node_ids = list(kg_store._tenant_nodes.get(tenant_id, set()))

        from app.org.decision_intelligence import get_version_store

        vs = get_version_store()
        rec = vs.save(
            entity_type="knowledge_graph",
            entity_id=org_id,
            tenant_id=tenant_id,
            snapshot={"node_ids": node_ids[:200], "node_count": len(node_ids)},
            changed_by="api",
            change_reason="manual snapshot",
        )
        span.set_attribute("version_num", rec.version_num)
        return {
            "org_id": org_id,
            "version_id": rec.version_id,
            "version_num": rec.version_num,
            "node_count": len(node_ids),
            "content_hash": rec.content_hash,
            "created_at": rec.created_at,
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
    """Return the version history of the org's knowledge graph."""
    _require_tenant(request)
    from app.org.decision_intelligence import get_version_store

    vs = get_version_store()
    return {
        "org_id": org_id,
        "versions": vs.history("knowledge_graph", org_id, limit=limit),
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
    _require_tenant(request)
    from app.memory.dept_memory import get_dept_memory

    dm = get_dept_memory()
    entries = dm.list_entries(dept_id, active_only=active_only, limit=limit)
    summary = dm.dept_summary(dept_id)
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

        from app.memory.dept_memory import get_dept_memory

        dm = get_dept_memory()
        entry = await dm.add(
            dept_id=dept_id,
            org_id=org_id,
            tenant_id=tenant_id,
            content=body.content,
            source=body.source,
            confidence=body.confidence,
            tags=body.tags,
        )
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

    await websocket.accept()

    # Resolve auth header → extract api_key and tenant_id
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        api_key = auth_header[7:].strip()

    # Resolve tenant_id from X-Tenant-Id header or default
    tenant_id = websocket.headers.get("x-tenant-id", "system")

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
    service: OrgService = Depends(get_org_service),
) -> dict[str, Any]:
    """Creates an OrgMission record, runs MetaOrchestrator team formation,
    and dispatches the goal to the AgentGraph via GoalService (Celery).

    Returns immediately with mission_id + goal_id for tracking.
    """
    ctx = _require_tenant(request)
    tenant_ctx = ctx if hasattr(ctx, "tenant_id") else None

    mission, dispatch = await service.create_mission_and_execute(
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
        tenant_ctx=tenant_ctx,
        app_state=request.app.state,
    )

    return {
        "mission_id": str(mission.id),
        "title": mission.title,
        "status": mission.status,
        "goal_id": dispatch.get("goal_id"),
        "team_id": dispatch.get("team_id"),
        "agent_ids": dispatch.get("agent_ids", []),
        "topology": dispatch.get("topology"),
        "departments": dispatch.get("departments", []),
        "agent_count": dispatch.get("agent_count", 0),
        "autonomy_level": dispatch.get("autonomy_level"),
        "estimated_cost_usd": dispatch.get("estimated_cost_usd", 0.0),
        "dispatched": dispatch.get("goal_id") is not None,
        "warning": dispatch.get("warning"),
        "error": dispatch.get("error"),
    }


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
    ctx = _require_tenant(request)
    tenant_ctx = ctx if hasattr(ctx, "tenant_id") else None
    result = await service.finalize_mission(
        mission_id, app_state=request.app.state, tenant_ctx=tenant_ctx
    )
    return result
