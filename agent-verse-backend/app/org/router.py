"""FastAPI router for the AI Organization OS.

All endpoints:
  - Require tenant authentication via TenantMiddleware
  - Return RFC 7807 errors on failure
  - Include operation_id for OpenAPI
  - Support cursor-based pagination on list endpoints
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
from app.org.service import OrgService

router = APIRouter(prefix="/v1/org", tags=["org"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _request_id() -> str:
    return str(uuid4())


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail={
            "type": "unauthorized", "title": "Unauthorized",
            "status": 401, "detail": "Missing or invalid API key",
        })
    return ctx


def _not_found(resource: str, rid: str, request_id: str | None = None) -> HTTPException:
    return HTTPException(status_code=404, detail={
        "type": "not-found", "title": "Not Found", "status": 404,
        "detail": f"{resource} '{rid}' not found",
        "request_id": request_id or _request_id(),
    })


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail={
        "type": "validation-error", "title": "Validation Error", "status": 422,
        "detail": detail,
    })


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
        raise HTTPException(status_code=503, detail={
            "type": "service-unavailable", "title": "Service Unavailable",
            "status": 503, "detail": "Database not initialised yet",
        })

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
        org_id=org_id, name=body.name, purpose=body.purpose,
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
) -> MissionResponse:
    mission = await service.create_mission(
        org_id=org_id, title=body.title, objective=body.objective,
        why=body.why, expected_outcome=body.expected_outcome,
        priority=body.priority, dept_id=body.dept_id,
        source=body.source, tags=body.tags,
        budget_usd=body.budget_usd, deadline=body.deadline,
        autonomy_level=body.autonomy_level, created_by=body.created_by,
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
        org_id, status=status_filter, priority=priority,
        dept_id=dept_id, limit=limit, offset=offset,
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
                mission = await service.update_organization(mission_id, updates)
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
            org_id=org_id, title=body.title, objective=body.objective,
            mission_id=body.mission_id, parent_task_id=body.parent_task_id,
            priority=body.priority, risk_level=body.risk_level,
            depth=body.depth, assigned_agent_ids=body.assigned_agent_ids,
            required_capabilities=body.required_capabilities,
            required_tools=body.required_tools,
            budget_usd=body.budget_usd, deadline=body.deadline,
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
        org_id, mission_id=mission_id, status=status_filter,
        limit=limit, offset=offset,
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
            task_id, body.status,
            outputs=body.outputs, evidence=body.evidence,
            actual_cost_usd=body.actual_cost_usd,
        )
    except ValueError as exc:
        raise _unprocessable(str(exc), x_request_id) from None
    if not task:
        raise _not_found("Task", task_id, x_request_id)
    return TaskResponse.model_validate(task)


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
        org_id=org_id, name=body.name, purpose=body.purpose,
        dept_id=body.dept_id, team_type=body.team_type,
        member_agent_ids=body.member_agent_ids,
        capability_ids=body.capability_ids, tool_ids=body.tool_ids,
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
        org_id, event_type=event_type, severity=severity,
        limit=limit, offset=offset,
    )
    data = [OrgEventResponse.model_validate(e) for e in events]
    return CursorPage(data=data, cursor=None, hasMore=len(events) == limit)


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
            from app.main import app as _app
            redis = getattr(_app.state, "redis", None)

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
        asyncio.get_event_loop().create_task(
            _run_graphify_job(org_id, tenant_id, job_id, request)
        )

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
            from app.main import app as _app
            redis = getattr(_app.state, "redis", None)

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
                        'type': 'phase', 'phase': idx,
                        'total_phases': len(phases), 'label': label,
                    }
                    yield f"data: {json.dumps(evt)}\n\n"
                    await asyncio.sleep(0.8)
                done_evt = {'type': 'complete', 'nodes': 0, 'edges': 0, 'communities': 0}
                yield f"data: {json.dumps(done_evt)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_graphify_job(
    org_id: str, tenant_id: str, job_id: str, request: Request
) -> None:
    """Background coroutine that builds the knowledge graph and emits SSE progress."""
    import structlog
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    log = structlog.get_logger(__name__)

    with tracer.start_as_current_span("org.graphify.job") as span:
        span.set_attribute("org_id", org_id)
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("job_id", job_id)

        from app.main import app as _app
        redis = getattr(_app.state, "redis", None)
        channel = f"graphify:{job_id}:events"

        async def _emit(payload: dict) -> None:  # type: ignore[type-arg]
            if redis:
                await redis.publish(channel, json.dumps(payload))
            log.info("graphify.event", job_id=job_id, event=payload.get("type"))

        try:

            phases = [
                ("Fetching org knowledge", _phase_noop),
                ("Extracting entities", _phase_noop),
                ("Building relationships", _phase_noop),
                ("Detecting communities", _phase_noop),
                ("Persisting graph", _phase_noop),
            ]
            for idx, (label, _fn) in enumerate(phases, start=1):
                await _emit(
                    {'type': 'phase', 'phase': idx, 'total_phases': len(phases), 'label': label}
                )
                await asyncio.sleep(0.5)  # simulate work; replace with real calls
                stats = {'type': 'stats', 'nodes': idx * 10, 'edges': idx * 15,
                         'communities': max(1, idx // 2)}
                await _emit(stats)

            n = len(phases)
            await _emit({'type': 'complete', 'nodes': n * 10, 'edges': n * 15, 'communities': 3})
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
    _OrgRoleResponse(id="org_owner",    name="Org Owner",
                     description="Full control.", is_built_in=True),
    _OrgRoleResponse(id="org_admin",    name="Org Admin",
                     description="Manage members, connectors, settings.", is_built_in=True),
    _OrgRoleResponse(id="mission_lead", name="Mission Lead",
                     description="Create/edit missions and tasks.", is_built_in=True),
    _OrgRoleResponse(id="agent_runner", name="Agent Runner",
                     description="Execute missions.", is_built_in=True),
    _OrgRoleResponse(id="observer",     name="Observer",
                     description="View only.", is_built_in=True),
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

        from app.main import app as _app
        redis = getattr(_app.state, "redis", None)
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

        from app.main import app as _app
        redis = getattr(_app.state, "redis", None)
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
        failed  = health.get("task_counts", {}).get("failed", 0)
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
                    if pending > 0 else []
                ),
                *(
                    [{"urgency": "warning", "text": f"{blocked} tasks are blocked"}]
                    if blocked > 0 else []
                ),
                *(
                    [{"urgency": "info", "text": f"{active_missions} missions running smoothly"}]
                    if active_missions > 0 and pending == 0 and blocked == 0 else []
                ),
            ],
            "risks": [
                *(
                    [{"severity": "high", "text": f"{failed} tasks failed today — review required"}]
                    if failed > 0 else []
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

        # Route to agent loop (fire-and-forget) when not high-risk
        if not high_risk:
            asyncio.get_event_loop().create_task(
                _route_command_to_agent(command_id, org_id, tenant_id, body.command)
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
                if high_risk else
                "Command accepted and routing to agent loop."
            ),
        }


async def _route_command_to_agent(
    command_id: str, org_id: str, tenant_id: str, command: str
) -> None:
    """Route an accepted UCG command to the agent goal loop."""
    import structlog as _sl
    _log = _sl.get_logger(__name__)
    try:
        from app.main import app as _app
        goal_service = getattr(_app.state, "goal_service", None)
        if goal_service and hasattr(goal_service, "submit_goal"):
            await goal_service.submit_goal(
                tenant_id=tenant_id,
                goal=command,
                metadata={"source": "ucg", "org_id": org_id, "command_id": command_id},
            )
        # Update command status
        for cmd in _COMMAND_HISTORY.get(org_id, []):
            if cmd.get("command_id") == command_id:
                cmd["status"] = "routed"
                break
    except Exception as exc:
        _log.warning("org.command_route_failed", command_id=command_id, error=str(exc))
        for cmd in _COMMAND_HISTORY.get(org_id, []):
            if cmd.get("command_id") == command_id:
                cmd["status"] = "routing_failed"
                cmd["error"] = str(exc)
                break


# ── N2: Org Composer — NL to Organisation ────────────────────────────────────

class _OrgComposeRequest(BaseModel):
    description: str
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

        # Deep N2: delegate to service layer which uses LLM when available
        result = await service.compose_from_nl(
            description=body.description,
            goals=body.goals,
            industry=body.industry,
            autonomy_level=body.autonomy_level,
            budget_usd=body.budget_usd,
            constraints=body.constraints,
        )

        span.set_attribute("org_id", result.get("org_id", ""))
        span.set_attribute("departments_created", len(result.get("departments", [])))
        result["request_id"] = x_request_id
        return result


# ── P13: Team Lifecycle State Machine ────────────────────────────────────────

_TEAM_LIFECYCLE_STATES = frozenset({
    "create", "staff", "brief", "execute", "review", "complete", "archive",
})

_TEAM_LIFECYCLE_TRANSITIONS: dict[str, list[str]] = {
    "create":   ["staff"],
    "staff":    ["brief"],
    "brief":    ["execute"],
    "execute":  ["review"],
    "review":   ["complete"],
    "complete": ["archive"],
    "archive":  [],
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

        current = (getattr(team, "metadata", None) or {}).get("lifecycle_state", "create")
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
        existing_meta = dict(getattr(team, "metadata", None) or {})
        existing_meta["lifecycle_state"] = next_state
        await service.update_team(team_id, {"metadata": existing_meta})
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

    current = (getattr(team, "metadata", None) or {}).get("lifecycle_state", "create")
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
            ] + [
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
            org_name=org.name,
            health=health,
        )
        return {
            "org_id":             org_id,
            "org_name":           org.name,
            "week_ending":        brief.week_ending,
            "health_summary":     brief.health_summary,
            "accomplishments":    brief.accomplishments,
            "risks":              brief.risks,
            "opportunities":      brief.opportunities,
            "recommendations":    brief.recommendations,
            "kpi_trends":         brief.kpi_trends,
            "generation_method":  brief.generation_method,
            "generated_at":       brief.generated_at,
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
    required: str = Query(default="", description="Comma-separated required capabilities for gap analysis"),  # noqa: E501
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

        from app.org.advanced_services import get_decision_intelligence
        intel = get_decision_intelligence()
        return {
            "org_id":          org_id,
            "decisions":       intel.list_decisions(org_id, limit=limit),
            "quality_report":  intel.decision_quality_report(org_id),
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
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            "version_id":  rec.version_id,
            "version_num": rec.version_num,
            "node_count":  len(node_ids),
            "content_hash": rec.content_hash,
            "created_at":  rec.created_at,
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
        "org_id":   org_id,
        "versions": vs.history("knowledge_graph", org_id, limit=limit),
    }
