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

