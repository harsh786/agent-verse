"""Enterprise API — compliance, simulation, red-team, marketplace, intelligence,
SAML 2.0 SSO, SCIM 2.0 provisioning, and contract management."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.responses import StreamingResponse

from app.auth.saml_provider import SAMLNotInstalledError, SAMLReplayCheckUnavailableError
from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

router = APIRouter(prefix="/enterprise", tags=["enterprise"])
marketplace_router = APIRouter(prefix="/marketplace", tags=["marketplace"])
intelligence_router = APIRouter(prefix="/intelligence", tags=["intelligence"])
# P2.10: Async GDPR export + consent management at /compliance/*
compliance_router = APIRouter(prefix="/compliance", tags=["compliance"])
# SCIM 2.0 provisioning router — mounted at /scim/v2
scim_router = APIRouter(prefix="/scim/v2", tags=["SCIM 2.0"])

# --- helpers ---


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _require_admin(ctx: Any, action: str) -> None:
    """SSO / provisioning configuration decides who can log in or be provisioned
    as the tenant: admin only (it was gated by nothing more than any key)."""
    from app.tenancy.rbac import has_role

    if not has_role(ctx, "admin"):
        raise HTTPException(403, f"{action} requires the admin role")


def _compliance(request: Request) -> Any:
    from app.api._deps import get_compliance_controller as _gcc

    return _gcc(request)


def _compliance_checker(request: Request) -> Any:
    """Return the v2 ComplianceChecker (dynamically computed, not hardcoded)."""
    return getattr(request.app.state, "compliance_checker", None)


def _simulation(request: Request) -> Any:
    from app.api._deps import get_simulation_runner as _gsr

    return _gsr(request)


def _red_team(request: Request) -> Any:
    from app.api._deps import get_red_team_runner as _grtr

    return _grtr(request)


_mkt_logger = get_logger(__name__)


def _marketplace(request: Request) -> Any:
    from app.api._deps import get_marketplace as _gmp

    return _gmp(request)


def _marketplace_v2(request: Request) -> Any:
    """Return the DB-backed MarketplaceV2 service (falls back to v1)."""
    v2 = getattr(request.app.state, "marketplace_v2", None)
    if v2 is not None:
        return v2
    # Apps built without the lifespan (some test setups): one instance per app,
    # on the app's DB when it has one. A fresh instance per request forgot every
    # publish between calls.
    from app.enterprise.marketplace_v2 import MarketplaceV2

    v2 = MarketplaceV2(db_factory=getattr(request.app.state, "db_session_factory", None))
    request.app.state.marketplace_v2 = v2
    return v2


def _self_optimizer(request: Request) -> Any:
    from app.api._deps import get_self_optimizer as _gso

    return _gso(request)


def _get_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        try:
            from app.db.session import get_session_factory

            db = get_session_factory()
        except Exception:
            pass
    return db


# --- Compliance ---


@router.get("/compliance/export")
async def request_data_export(request: Request) -> dict[str, Any]:
    ctx = _require_tenant(request)
    req = await _compliance(request).request_data_export(tenant_ctx=ctx)
    return {
        "request_id": req.request_id,
        "status": req.status,
        "download_url": req.download_url,
    }


@router.get("/compliance/export/{request_id}/download")
async def download_export(request: Request, request_id: str) -> Response:
    """Download the GDPR data export as a JSON file."""
    ctx = _require_tenant(request)
    req = await _compliance(request).get_export_status(request_id=request_id, tenant_ctx=ctx)
    if req is None:
        raise HTTPException(status_code=404, detail="Export request not found")
    if req.status != "ready":
        raise HTTPException(status_code=202, detail="Export not ready yet")
    content = json.dumps(req.payload, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (f'attachment; filename="agentverse-export-{request_id}.json"')
        },
    )


@router.get("/compliance/export/{request_id}")
async def get_export_status(request: Request, request_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    req = await _compliance(request).get_export_status(request_id=request_id, tenant_ctx=ctx)
    if req is None:
        raise HTTPException(status_code=404, detail="Export request not found")
    return {"request_id": req.request_id, "status": req.status, "payload": req.payload}


@router.post("/compliance/delete", status_code=202)
async def request_data_deletion(request: Request) -> dict[str, Any]:
    """Record a durable GDPR erasure job (executed by the process_tenant_erasures
    beat task after the grace period). 503 if the request could not be recorded —
    never ``deletion_scheduled: true`` for a request that does not exist."""
    ctx = _require_tenant(request)
    try:
        result: dict[str, Any] = await _compliance(request).request_data_deletion(tenant_ctx=ctx)
    except Exception as exc:
        raise HTTPException(503, "Erasure request could not be recorded; retry") from exc
    return result


@router.get("/compliance/delete")
async def get_data_deletion_status(request: Request) -> dict[str, Any]:
    """The tenant's erasure job as stored: pending | processing | on_hold | failed |
    completed, with attempts / last_error / result."""
    ctx = _require_tenant(request)
    try:
        job = await _compliance(request).get_deletion_status(tenant_ctx=ctx)
    except Exception as exc:
        raise HTTPException(503, "Erasure status unavailable") from exc
    if job is None:
        raise HTTPException(404, "No erasure request for this tenant")
    return dict(job)


@router.get("/compliance/residency")
async def get_data_residency(request: Request) -> dict[str, Any]:
    ctx = _require_tenant(request)
    return _compliance(request).get_data_residency(tenant_ctx=ctx)


@router.get("/compliance/regions")
async def list_data_regions(request: Request) -> list[dict[str, Any]]:
    """Return all available data residency regions."""
    ctx = _require_tenant(request)
    residency = _compliance(request).get_data_residency(tenant_ctx=ctx)
    regions = [residency]
    for r in ["us-east-1", "eu-west-1", "ap-southeast-1"]:
        if residency.get("region") != r:
            regions.append({"region": r, "description": f"Region {r}"})
    return regions


# --- Simulation ---


class SimulationRequest(BaseModel):
    goal: str
    mock_tools: dict[str, Any] = {}


@router.post("/simulation", status_code=201)
async def run_simulation(request: Request, body: SimulationRequest) -> dict[str, Any]:
    ctx = _require_tenant(request)
    run = await _simulation(request).start(
        goal=body.goal, mock_tools=body.mock_tools, tenant_ctx=ctx, app_state=request.app.state
    )
    # Flatten result fields to top-level so frontend SimulationResult type is satisfied
    return {
        "run_id": run.run_id,
        # New fields (frontend)
        "status": run.result.get("status", run.status),
        "steps": run.result.get("steps", []),
        "cost_usd": run.result.get("cost_usd", 0.0),
        "iterations": run.result.get("iterations", 0),
        "message": run.result.get("message", ""),
        # Backward-compatible (existing tests expect "result" key and "completed" status)
        "result": run.result,
    }


@router.get("/simulation/available-tools")
async def get_simulation_available_tools(request: Request) -> dict[str, Any]:
    """Return MCP tools available for mock configuration in simulation.

    NOTE: this route MUST be registered before GET /simulation/{run_id} below —
    FastAPI matches routes in registration order, and a static path like
    "available-tools" would otherwise be swallowed by the {run_id} path
    parameter, producing a spurious 404 "Simulation run not found".
    """
    ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)
    tools: list[dict[str, Any]] = []
    if mcp_client is not None:
        try:
            raw_tools = await mcp_client.discover_all_tools(tenant_ctx=ctx)
            for t in raw_tools:
                tools.append(
                    {
                        "name": t.get("name", "") if isinstance(t, dict) else str(t),
                        "description": t.get("description", "") if isinstance(t, dict) else "",
                        "server_id": t.get("server_id", "") if isinstance(t, dict) else "",
                    }
                )
        except Exception:
            pass
    return {"tools": tools, "total": len(tools)}


@router.get("/simulation/{run_id}")
async def get_simulation(request: Request, run_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    # Durable read (Postgres when bound): the sync .get() read only this replica's
    # memory, so a run started on another replica (or before a restart) 404'd.
    runner = _simulation(request)
    aget = getattr(runner, "aget", None)
    run = (
        await aget(run_id=run_id, tenant_ctx=ctx)
        if inspect.iscoroutinefunction(aget)
        else runner.get(run_id=run_id, tenant_ctx=ctx)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return {
        "run_id": run.run_id,
        "status": run.result.get("status", run.status),
        "steps": run.result.get("steps", []),
        "cost_usd": run.result.get("cost_usd", 0.0),
        "iterations": run.result.get("iterations", 0),
        "message": run.result.get("message", ""),
    }


class StreamingSimulationRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    mock_tools: dict[str, Any] = {}
    agent_id: str | None = None
    agent_config: dict[str, Any] | None = None
    max_steps: int = Field(default=10, ge=1, le=30)


@router.post("/simulation/stream")
async def stream_simulation(
    request: Request, body: StreamingSimulationRequest
) -> StreamingResponse:
    """Run simulation with real-time SSE event emission per step."""
    ctx = _require_tenant(request)
    runner = _simulation(request)

    # Resolve agent config override
    agent_override: dict[str, Any] = {}
    if body.agent_config:
        agent_override = body.agent_config
    elif body.agent_id:
        agent_store = getattr(request.app.state, "agent_store", None)
        if agent_store is not None:
            try:
                agent = agent_store.get(body.agent_id, tenant_ctx=ctx)
                if agent:
                    agent_override = dict(agent)
            except Exception:
                pass

    async def generate():
        try:
            yield f"data: {json.dumps({'type': 'simulation_started', 'goal': body.goal[:100]})}\n\n"
            async for event in runner.run_streaming(
                goal=body.goal,
                mock_tools=body.mock_tools,
                tenant_ctx=ctx,
                agent_override=agent_override,
                max_steps=body.max_steps,
            ):
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'simulation_error', 'message': str(exc)[:200]})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --- Red Team ---


class RedTeamRequest(BaseModel):
    cases: list[str] | None = None


@router.post("/red-team", status_code=201)
async def run_red_team(request: Request, body: RedTeamRequest) -> dict[str, Any]:
    ctx = _require_tenant(request)
    report = _red_team(request).run(tenant_ctx=ctx, cases=body.cases)
    return {
        "report_id": report.report_id,
        # New fields (frontend expects)
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "run_at": report.run_at,
        # Backward-compatible fields (existing tests expect)
        "cases_run": report.cases_run,
        "cases_passed": report.cases_passed,
        "cases_failed": report.cases_failed,
        "results": report.results,
    }


# --- Marketplace ---


class PublishTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    domain: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=10, max_length=1000)
    connectors: list[str] = []
    autonomy_mode: str = "bounded-autonomous"
    agent_id: str | None = None  # Optional: publish from existing agent
    visibility: Literal["private", "team", "community", "public"] = "community"


class BundleDeployRequest(BaseModel):
    name: str
    template_ids: list[str]


# ── V2 request/response models ────────────────────────────────────────────────


class PublishTemplateV2Request(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field("", max_length=100)
    domain: str = Field("general", max_length=50)
    description: str = Field("", max_length=1000)
    long_description: str = ""
    category: str = ""
    tags: list[str] = []
    template_config: dict[str, Any] = {}
    parameters_schema: dict[str, Any] = {}
    required_connectors: list[str] = []
    optional_connectors: list[str] = []
    author_name: str = ""
    visibility: Literal["private", "team", "community", "public"] = "private"
    version: str = "1.0.0"


class DeployV2Request(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    # Frontend uses `parameters` for the same payload. Keep both accepted so
    # existing clients do not silently deploy with an empty parameter set.
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _copy_frontend_parameters(self) -> DeployV2Request:
        if not self.params and self.parameters:
            self.params = dict(self.parameters)
        return self


class AddReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    title: str = ""
    body: str = ""
    verified_install: bool = False


class SearchRequest(BaseModel):
    query: str
    domain: str = ""
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


@marketplace_router.post("/publish", status_code=201)
async def publish_template(request: Request, body: PublishTemplateRequest) -> dict[str, Any]:
    """Publish an agent template to the marketplace.

    Stored through the DB-backed marketplace (with security review). It used to
    go into a per-process dict: gone on restart, invisible on other replicas,
    and never security-reviewed.
    """
    ctx = _require_tenant(request)

    connectors = list(body.connectors)
    if body.agent_id:
        store = getattr(request.app.state, "agent_store", None)
        if store is not None:
            agent = await store.get_async(body.agent_id, tenant_ctx=ctx)
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent {body.agent_id} not found")
            connectors = connectors or list(agent.get("connector_ids", []))

    record = await _publish_v2(
        request,
        ctx,
        {
            "name": body.name,
            "domain": body.domain,
            "description": body.description,
            "required_connectors": connectors,
            "template_config": {"autonomy_mode": body.autonomy_mode},
            "visibility": body.visibility,
        },
    )
    return {
        **record,
        "template_id": record["id"],
        "connectors": connectors,
        "autonomy_mode": body.autonomy_mode,
        "author": ctx.tenant_id,
        "published_by": ctx.tenant_id,
        "is_community": body.visibility == "community",
    }


async def _publish_v2(request: Request, ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
    from app.enterprise.marketplace_v2 import TemplateSlugTakenError

    try:
        record: dict[str, Any] = await _marketplace_v2(request).publish_template(
            data=data, tenant_ctx=ctx, run_security_review=True
        )
    except TemplateSlugTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record


@marketplace_router.post("/bundles", status_code=201)
async def deploy_bundle(request: Request, body: BundleDeployRequest) -> dict[str, Any]:
    """Deploy multiple templates as a bundle (group deployment)."""
    ctx = _require_tenant(request)
    return await _marketplace(request).create_bundle(
        name=body.name, template_ids=body.template_ids, tenant_ctx=ctx
    )


@marketplace_router.get("/browse")
async def browse_marketplace(
    request: Request, q: str = "", domain: str = ""
) -> list[dict[str, Any]]:
    ctx = _require_tenant(request)
    # Backed by the DB marketplace (same visibility rules as /templates), so a
    # template published on any replica shows up here.
    result = await _marketplace_v2(request).list_templates(
        search=q, domain=domain, tenant_id=ctx.tenant_id, page=1, page_size=100
    )
    return [
        {
            **t,
            "template_id": t.get("id") or t.get("template_id"),
            "is_community": t.get("visibility") == "community",
        }
        for t in result.get("templates", [])
    ]


# ── V2: paginated template list ───────────────────────────────────────────────


@marketplace_router.get("/templates")
async def list_templates_v2(
    request: Request,
    domain: str = "",
    category: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "install_count",
) -> dict[str, Any]:
    """Paginated marketplace template list with full-text search and filters."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    result = await svc.list_templates(
        domain=domain,
        category=category,
        search=search,
        tenant_id=ctx.tenant_id,
        page=page,
        page_size=page_size,
    )
    # Normalise response: always expose both `items` and `templates` keys so all
    # frontend code paths work regardless of which key they read.
    templates_list = result.get("templates") or result.get("items") or []
    return {
        "items": templates_list,
        "templates": templates_list,
        "total": result.get("total", len(templates_list)),
        "page": result.get("page", page),
        "page_size": result.get("page_size", page_size),
    }


@marketplace_router.post("/templates", status_code=201)
async def publish_template_v2(request: Request, body: PublishTemplateV2Request) -> dict[str, Any]:
    """Publish a template using the V2 DB-backed service (triggers security review)."""
    ctx = _require_tenant(request)
    return await _publish_v2(request, ctx, body.model_dump())


@marketplace_router.get("/templates/{template_id}")
async def get_template_v2(request: Request, template_id: str) -> dict[str, Any]:
    """Get a template the caller may see (own, or shared and security-approved)."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    t = await svc.get_template(template_id=template_id, tenant_id=ctx.tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@marketplace_router.post("/templates/{template_id}/deploy", status_code=200)
async def deploy_template_v2(
    request: Request, template_id: str, body: DeployV2Request
) -> dict[str, Any]:
    """Atomic install: creates agent + install record in a single DB transaction.

    FIX: replaces the old deploy endpoint that could produce ghost agents on failure.
    Returns success with agent_id, or structured error — never a ghost agent.
    """
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    agent_store = getattr(request.app.state, "agent_store", None)
    result = await svc.install(
        template_id=template_id,
        params=body.params,
        tenant_ctx=ctx,
        agent_store=agent_store,
    )
    if not result.get("success"):
        # Return structured error without raising (lets client inspect details)
        missing = result.get("missing_connectors")
        if missing:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "MISSING_CONNECTORS",
                    "missing_connectors": missing,
                    "message": f"Required connectors not configured: {missing}",
                },
            )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "INSTALL_FAILED",
                "message": result.get("error", "Install failed"),
            },
        )
    return result


@marketplace_router.post("/templates/{template_id}/reviews", status_code=201)
async def add_review_v2(
    request: Request, template_id: str, body: AddReviewRequest
) -> dict[str, Any]:
    """Add a rating/review to a template. One review per tenant."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    if await svc.get_template(template_id=template_id, tenant_id=ctx.tenant_id) is None:
        raise HTTPException(status_code=404, detail="Template not found")
    result = await svc.add_review(
        template_id=template_id,
        tenant_ctx=ctx,
        rating=body.rating,
        title=body.title,
        body=body.body,
        # verified_install is derived server-side from the tenant's install
        # record; a client-supplied flag is ignored.
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Review failed"))
    return result


@marketplace_router.get("/templates/{template_id}/reviews")
async def list_reviews_v2(
    request: Request,
    template_id: str,
    page: int = 1,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """List reviews for a template (verified installs first)."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    if await svc.get_template(template_id=template_id, tenant_id=ctx.tenant_id) is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return await svc.list_reviews(
        template_id=template_id,
        page=page,
        page_size=page_size,
        tenant_id=ctx.tenant_id,
    )


@marketplace_router.post("/search")
async def search_templates_v2(request: Request, body: SearchRequest) -> dict[str, Any]:
    """Full-text (+ optional semantic) search for marketplace templates."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    templates = await svc.search_templates(
        query=body.query,
        domain=body.domain,
        tenant_id=ctx.tenant_id,
        page=body.page,
        page_size=body.page_size,
    )
    return {"templates": templates, "query": body.query}


@marketplace_router.get("/domains/counts")
async def get_domain_counts(request: Request) -> dict[str, Any]:
    """Counts per domain: marketplace templates the caller can see ("agents") and
    the caller's goal templates ("templates").

    This used to call ``list_templates`` on the deprecated v1 gallery (which has
    no such method) and swallow the error, so marketplace templates were never
    counted. Errors now surface as 503 instead of silently empty counts.
    """
    tenant = _require_tenant(request)

    counts: dict[str, dict[str, int]] = {}

    def _bucket(domain: str) -> dict[str, int]:
        return counts.setdefault(domain or "general", {"agents": 0, "templates": 0})

    try:
        by_domain = await _marketplace_v2(request).count_by_domain(tenant_id=tenant.tenant_id)
    except Exception as exc:
        _mkt_logger.error("marketplace_domain_counts_failed", error=str(exc))
        raise HTTPException(
            status_code=503, detail="Marketplace template counts are unavailable"
        ) from exc
    for domain, n in by_domain.items():
        _bucket(domain)["agents"] += int(n)

    template_store = getattr(request.app.state, "template_store", None)
    if template_store is not None:
        try:
            goal_templates = await template_store.list(tenant.tenant_id)
        except Exception as exc:
            _mkt_logger.error("goal_template_domain_counts_failed", error=str(exc))
            raise HTTPException(
                status_code=503, detail="Goal template counts are unavailable"
            ) from exc
        for t in goal_templates:
            _bucket(t.get("domain", "general"))["templates"] += 1

    return {"counts": counts}


@marketplace_router.get("/installs")
async def list_installs(request: Request) -> dict[str, Any]:
    """The caller's marketplace installs (from marketplace_installs in DB mode).

    This used to probe the deprecated v1 gallery for methods it does not have
    and swallow every error, so it always answered an empty list.
    """
    tenant = _require_tenant(request)
    try:
        installs = await _marketplace_v2(request).list_installs(tenant_id=tenant.tenant_id)
    except Exception as exc:
        _mkt_logger.error("marketplace_list_installs_failed", error=str(exc))
        raise HTTPException(
            status_code=503, detail="Marketplace installs are unavailable"
        ) from exc
    installed_ids = list(dict.fromkeys(str(i["template_id"]) for i in installs))
    return {"installed_ids": installed_ids, "installs": installs}


@marketplace_router.get("/{template_id}/versions")
async def get_template_versions(request: Request, template_id: str) -> list[dict[str, Any]]:
    """Return version history for a template the caller may see."""
    ctx = _require_tenant(request)
    t = await _marketplace_v2(request).get_template(
        template_id=template_id, tenant_id=ctx.tenant_id
    )
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    # Try DB-backed version history first
    db = _get_db(request)
    history = await _marketplace(request).get_version_history(template_id=template_id, db=db)
    if history:
        return history
    # Fall back: return current version record
    return [
        {
            "version": t.get("version", "1.0.0"),
            "template_id": template_id,
            "published_at": t.get("published_at", ""),
            "is_current": True,
        }
    ]


@marketplace_router.post("/{template_id}/publish", status_code=201)
async def publish_template_version(
    request: Request, template_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Publish a versioned snapshot of one of the caller's own templates.

    Any tenant could previously snapshot any template id as a new "version".
    """
    ctx = _require_tenant(request)
    t = await _marketplace_v2(request).get_template(
        template_id=template_id, tenant_id=ctx.tenant_id
    )
    if t is None or t.get("tenant_id") != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Template not found")
    db = _get_db(request)
    version = str(body.get("version", "1.0.0"))
    changelog = str(body.get("changelog", ""))
    return await _marketplace(request).publish_version(
        template_id=template_id, version=version, changelog=changelog, db=db, template=t
    )


@marketplace_router.get("/{template_id}")
async def get_template(request: Request, template_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    t = await _marketplace_v2(request).get_template(
        template_id=template_id, tenant_id=ctx.tenant_id
    )
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


class DeployRequest(BaseModel):
    params: dict[str, Any] = {}


@marketplace_router.post("/{template_id}/deploy", status_code=201)
async def deploy_template(
    request: Request, template_id: str, body: DeployRequest
) -> dict[str, Any]:
    """Legacy deploy route — same atomic, visibility-checked install as
    ``POST /templates/{id}/deploy`` (it used to deploy from a separate
    in-memory catalogue that did not include DB-published templates)."""
    ctx = _require_tenant(request)
    result = await _marketplace_v2(request).install(
        template_id=template_id,
        params=body.params,
        tenant_ctx=ctx,
        agent_store=getattr(request.app.state, "agent_store", None),
    )
    if not result.get("success"):
        if result.get("missing_connectors"):
            raise HTTPException(
                status_code=400,
                detail={"error": "MISSING_CONNECTORS",
                        "missing_connectors": result["missing_connectors"]},
            )
        status_code = 404 if result.get("error") == "Template not found" else 422
        raise HTTPException(status_code=status_code, detail=result.get("error", "Deploy failed"))
    return {
        "deployment_id": result.get("install_id"),
        "agent_id": result.get("agent_id"),
        "template_id": template_id,
    }


# --- Intelligence / Self-optimization ---


@intelligence_router.get("/experiments")
async def list_experiments(request: Request) -> list[dict]:
    """List all A/B optimization experiments for this tenant (M-2)."""
    ctx = _require_tenant(request)
    self_opt_v2 = getattr(request.app.state, "self_optimizer_v2", None)
    if self_opt_v2 is None:
        return []
    try:
        return await self_opt_v2.list_experiments(tenant_id=ctx.tenant_id)
    except Exception:
        return []


from pydantic import BaseModel as _BaseModel  # noqa: E402


class RollbackExperimentRequest(_BaseModel):
    reason: str = "Manual rollback via UI"


@intelligence_router.post("/experiments/{experiment_id}/rollback")
async def rollback_experiment(
    request: Request,
    experiment_id: str,
    body: RollbackExperimentRequest,
) -> dict:
    """Roll back a concluded experiment to its control configuration."""
    ctx = _require_tenant(request)
    self_opt_v2 = getattr(request.app.state, "self_optimizer_v2", None)
    if self_opt_v2 is None:
        from fastapi import HTTPException as _HTTPException
        from fastapi import status as _status

        raise _HTTPException(
            status_code=_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Self-optimizer v2 not available",
        )
    try:
        experiments = await self_opt_v2.list_experiments(tenant_id=ctx.tenant_id)
    except Exception:
        experiments = []
    experiment = next((e for e in experiments if e.get("id") == experiment_id), None)
    if experiment is None:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=404, detail=f"Experiment {experiment_id!r} not found")
    success = await self_opt_v2.rollback(
        tenant_id=ctx.tenant_id,
        agent_id=experiment["agent_id"],
        experiment_id=experiment_id,
        reason=body.reason,
    )
    if not success:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=400, detail="Rollback failed")
    return {
        "experiment_id": experiment_id,
        "agent_id": experiment["agent_id"],
        "status": "rolled_back",
        "reason": body.reason,
    }


@intelligence_router.post("/experiments/{experiment_id}/apply")
async def apply_experiment(request: Request, experiment_id: str) -> dict:
    """Manually apply a concluded experiment's winning candidate config.

    This is the human-in-the-loop half of the self-improvement loop. When
    autonomous auto-apply is disabled (``enable_self_improvement_auto_apply``
    off — the default), a winning candidate is left pending; an operator applies
    it explicitly here. Fails closed: 404 for unknown experiments, 409 when the
    experiment is not an applicable winner (not a candidate win, or already
    applied).
    """
    ctx = _require_tenant(request)
    self_opt_v2 = getattr(request.app.state, "self_optimizer_v2", None)
    if self_opt_v2 is None:
        from fastapi import HTTPException as _HTTPException
        from fastapi import status as _status

        raise _HTTPException(
            status_code=_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Self-optimizer v2 not available",
        )
    result = await self_opt_v2.apply_pending(
        tenant_id=ctx.tenant_id, experiment_id=experiment_id
    )
    if not result.get("applied"):
        from fastapi import HTTPException as _HTTPException

        reason = result.get("reason")
        if reason == "not_found":
            raise _HTTPException(status_code=404, detail=f"Experiment {experiment_id!r} not found")
        raise _HTTPException(status_code=409, detail=f"Cannot apply experiment: {reason}")
    return {
        "experiment_id": experiment_id,
        "agent_id": result.get("agent_id"),
        "status": "applied",
    }


@intelligence_router.get("/suggestions")
async def list_suggestions(request: Request, applied: bool | None = None) -> list[dict[str, Any]]:
    """Return suggestions shaped to match the frontend Suggestion interface:
      {id, type, status, confidence, description, agent_id, created_at}

    DB/in-memory mapping:
      suggestion_id → id
      category      → type
      applied True  → status "applied", False → "pending"
    """
    from datetime import UTC
    from datetime import datetime as _dt

    ctx = _require_tenant(request)
    suggestions = _self_optimizer(request).list_suggestions(tenant_ctx=ctx, applied=applied)
    now_iso = _dt.now(UTC).isoformat()
    return [
        {
            "id": s.suggestion_id,
            "type": s.category,
            "description": s.description,
            "confidence": s.confidence,
            "agent_id": getattr(s, "agent_id", None),
            "status": "applied" if s.applied else "pending",
            "created_at": getattr(s, "created_at", now_iso) or now_iso,
        }
        for s in suggestions
    ]


@intelligence_router.post("/suggestions/{suggestion_id}/apply", status_code=410)
async def apply_suggestion(request: Request, suggestion_id: str) -> dict[str, Any]:
    """Deprecated v1 apply — 410 Gone.

    It called the v1 ``SelfOptimizer.apply_suggestion`` with no agent config, so
    nothing was ever changed: it flipped an in-process ``applied`` flag (lost on
    restart, invisible to other replicas) and answered ``applied: true``. v1
    suggestions carry no agent id or candidate config, so there is nothing to
    delegate to ``SelfOptimizerV2.apply_suggestion``. Refuse honestly and point
    at the v2 path, which really updates the agent's config in the DB.
    """
    _require_tenant(request)
    raise HTTPException(
        status_code=410,
        detail=(
            "Applying v1 optimization suggestions is no longer supported: it never changed "
            "any agent. Use POST /intelligence/experiments/{experiment_id}/apply to apply a "
            "self-optimizer v2 candidate config."
        ),
    )


@intelligence_router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(request: Request, suggestion_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    ok = _self_optimizer(request).reject_suggestion(suggestion_id=suggestion_id, tenant_ctx=ctx)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"suggestion_id": suggestion_id, "rejected": True}


# A platform benchmark is only reported when this many tenants / goals
# contributed (k-anonymity guard for the cross-tenant aggregate).
_BENCHMARK_MIN_TENANTS = 5
_BENCHMARK_MIN_GOALS = 10


@intelligence_router.get("/benchmarks")
async def get_benchmarks(
    request: Request,
    days: int = 30,
) -> dict[str, Any]:
    """Return platform vs your-tenant benchmark comparison.

    Response shape:
      {platform_avg_success_rate, platform_avg_cost_usd, platform_avg_eval_score,
       your_success_rate, your_cost_usd, your_eval_score,
       percentile_success, percentile_cost, comparison_label,
       dimensions: {task_completion, efficiency, accuracy, safety, coherence}}
    """
    ctx = _require_tenant(request)
    tenant_id = ctx.tenant_id

    db = _get_db(request)

    # Real schema (the previous SQL referenced columns that do not exist —
    # goals.cost_usd, evaluations.score_* / run_at — so every query raised,
    # the error was swallowed, and hard-coded "platform averages" (0.72 /
    # $0.05 / 0.74 ...) were presented as real):
    #   goals(tenant_id, status, created_at)
    #   cost_ledger(tenant_id, goal_id, cost_usd, created_at)   -- per-call cost
    #   evaluations(tenant_id, scores JSON {dimension: score}, average_score,
    #               created_at)
    # No data -> None + data_source "insufficient_data"; never invented numbers.
    dim_names = ("task_completion", "efficiency", "accuracy", "safety", "coherence")
    dim_sql = ", ".join(f"AVG(CAST(scores->>'{d}' AS FLOAT))" for d in dim_names)
    success_sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status IN ('complete','completed') THEN 1 ELSE 0 END)
                 AS completed,
               COUNT(DISTINCT tenant_id) AS n_tenants
        FROM goals
        WHERE {scope} created_at > NOW() - (:days * INTERVAL '1 day')
          AND status IN ('complete','completed','failed')
    """
    cost_sql = """
        SELECT AVG(goal_cost), COUNT(*) FROM (
            SELECT goal_id, SUM(cost_usd) AS goal_cost FROM cost_ledger
            WHERE {scope} goal_id IS NOT NULL AND goal_id <> ''
              AND created_at > NOW() - (:days * INTERVAL '1 day')
            GROUP BY goal_id
        ) per_goal
    """
    eval_sql = f"""
        SELECT COUNT(*), AVG(average_score), {dim_sql}
        FROM evaluations
        WHERE {{scope}} created_at > NOW() - (:days * INTERVAL '1 day')
    """

    async def _metrics(session: Any, scope: str, params: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text as _t

        out: dict[str, Any] = {
            "success_rate": None, "cost_usd": None, "eval_score": None, "dims": {},
            "n_goals": 0, "n_tenants": 0,
        }
        g = (await session.execute(_t(success_sql.format(scope=scope)), params)).fetchone()
        if g and g[0]:
            out["n_goals"] = int(g[0])
            out["n_tenants"] = int(g[2] or 0) if len(g) > 2 else 0
            out["success_rate"] = round(float(g[1] or 0) / float(g[0]), 4)
        c = (await session.execute(_t(cost_sql.format(scope=scope)), params)).fetchone()
        if c and c[0] is not None:
            out["cost_usd"] = round(float(c[0]), 6)
        e = (await session.execute(_t(eval_sql.format(scope=scope)), params)).fetchone()
        if e and e[0] and e[1] is not None:
            out["eval_score"] = round(float(e[1]), 4)
            out["dims"] = {
                d: round(float(v), 4)
                for d, v in zip(dim_names, e[2:], strict=False)
                if v is not None
            }
        return out

    import logging

    _log = logging.getLogger(__name__)
    yours: dict[str, Any] = {
        "success_rate": None, "cost_usd": None, "eval_score": None, "dims": {}, "n_goals": 0,
    }
    platform: dict[str, Any] = {
        "success_rate": None, "cost_usd": None, "eval_score": None, "dims": {},
        "n_goals": 0, "n_tenants": 0,
    }
    if db is not None:
        try:
            # goals / cost_ledger / evaluations are FORCE RLS: tenant GUC required.
            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                yours = await _metrics(
                    session, "tenant_id = :tid AND", {"tid": tenant_id, "days": days}
                )
        except Exception as exc:
            _log.warning("benchmarks_your_metrics_failed: %s", exc)

        try:
            # DELIBERATELY NOT tenant-scoped (a cross-tenant aggregate). With no GUC
            # under FORCE RLS this matches nothing, so it reports insufficient_data
            # unless the deployment's role can read across tenants. Even then it is
            # only reported when >= _BENCHMARK_MIN_TENANTS tenants contributed, so
            # a small platform cannot de-anonymise another tenant's figures.
            async with db() as session:
                platform = await _metrics(session, "", {"days": days})
        except Exception as exc:
            _log.warning("benchmarks_platform_metrics_failed: %s", exc)

    platform_ok = (
        platform.get("n_tenants", 0) >= _BENCHMARK_MIN_TENANTS
        and platform.get("n_goals", 0) >= _BENCHMARK_MIN_GOALS
    )
    if not platform_ok:
        platform = {"success_rate": None, "cost_usd": None, "eval_score": None, "dims": {}}

    # --- Percentile computation (only when both sides are real) ---
    percentile_success: int | None = None
    comparison_label = "insufficient_data"
    ys, ps = yours["success_rate"], platform["success_rate"]
    if ys is not None and ps:
        if ys >= ps * 1.15:
            percentile_success, comparison_label = 10, "Top 10%"
        elif ys >= ps * 1.05:
            percentile_success, comparison_label = 25, "Top 25%"
        elif ys >= ps * 0.95:
            percentile_success, comparison_label = 50, "Average"
        else:
            percentile_success, comparison_label = 75, "Below Average"

    percentile_cost: int | None = None
    yc, pc = yours["cost_usd"], platform["cost_usd"]
    if yc is not None and pc:
        if yc <= pc * 0.7:
            percentile_cost = 10
        elif yc <= pc * 0.9:
            percentile_cost = 25
        elif yc <= pc * 1.1:
            percentile_cost = 50
        else:
            percentile_cost = 75

    return {
        "platform_avg_success_rate": platform["success_rate"],
        "platform_avg_cost_usd": platform["cost_usd"],
        "platform_avg_eval_score": platform["eval_score"],
        "your_success_rate": ys,
        "your_cost_usd": yc,
        "your_eval_score": yours["eval_score"],
        "percentile_success": percentile_success,
        "percentile_cost": percentile_cost,
        "comparison_label": comparison_label,
        "your_sample_count": yours.get("n_goals", 0),
        "data_source": "live_platform_data" if platform_ok else "insufficient_data",
        "dimensions": {
            "your": yours["dims"],
            "platform": platform["dims"],
        },
    }


# --- Eval Suites ---


class CreateEvalSuiteRequest(BaseModel):
    # Caller-chosen ids are stored in a VARCHAR(32) key; restrict to a safe slug.
    suite_id: str | None = Field(default=None, min_length=1, max_length=32,
                                 pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)


class AddGoldenTaskRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10_000)
    expected_tools: list[str] = []
    forbidden_tools: list[str] = []
    expected_output_contains: list[str] = []
    max_iterations: int = Field(default=15, ge=1, le=100)
    tags: list[str] = []


def _eval_store(request: Request) -> Any:
    """The caller's eval-suite store (Postgres under RLS; tenant-keyed dicts without a DB)."""
    from app.intelligence.eval_suite_store import EvalSuiteStore

    ctx = _require_tenant(request)
    return EvalSuiteStore(getattr(request.app.state, "db_session_factory", None), ctx.tenant_id)


def _eval_runner(request: Request) -> Any:
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    return runner


@intelligence_router.post("/eval-suites", status_code=201)
async def create_eval_suite(request: Request, body: CreateEvalSuiteRequest) -> dict[str, Any]:
    """Create a new eval suite for golden task testing."""
    import uuid as _uuid

    _eval_runner(request)
    suite_id = body.suite_id or _uuid.uuid4().hex
    created = await _eval_store(request).create(
        suite_id, name=body.name or suite_id, description=body.description
    )
    if created is None:
        raise HTTPException(409, f"Eval suite {suite_id} already exists")
    return created


@intelligence_router.get("/eval-suites")
async def list_eval_suites(request: Request) -> list[dict[str, Any]]:
    """List the caller's eval suites with metadata."""
    result: list[dict[str, Any]] = await _eval_store(request).list()
    return result


@intelligence_router.get("/eval-suites/{suite_id}")
async def get_eval_suite(request: Request, suite_id: str) -> dict[str, Any]:
    """Get one of the caller's eval suites, including its golden tasks."""
    suite: dict[str, Any] | None = await _eval_store(request).get(suite_id)
    if suite is None:
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    return suite


@intelligence_router.delete("/eval-suites/{suite_id}", status_code=204)
async def delete_eval_suite(request: Request, suite_id: str) -> Response:
    """Delete one of the caller's eval suites and its run history."""
    if not await _eval_store(request).delete(suite_id):
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    return Response(status_code=204)


@intelligence_router.post("/eval-suites/{suite_id}/tasks", status_code=201)
async def add_golden_task(
    request: Request, suite_id: str, body: AddGoldenTaskRequest
) -> dict[str, Any]:
    """Add a golden task to one of the caller's eval suites."""
    _eval_runner(request)
    from app.intelligence.eval_suite import GoldenTask
    from app.intelligence.eval_suite_store import task_to_dict

    task = GoldenTask(
        suite_id=suite_id,
        goal=body.goal,
        expected_tools=body.expected_tools,
        forbidden_tools=body.forbidden_tools,
        expected_output_contains=body.expected_output_contains,
        max_iterations=body.max_iterations,
        tags=body.tags,
    )
    if not await _eval_store(request).add_task(suite_id, task_to_dict(task)):
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    return {"task_id": task.task_id, "suite_id": suite_id, "goal": body.goal}


async def _execute_eval_run(
    store: Any, runner: Any, goal_service: Any, ctx: Any, suite_id: str, run_id: str,
    tasks: list[Any],
) -> None:
    """Background body of a suite run: execute, then record the outcome durably.

    If this replica dies mid-run the row stays ``running`` and reads report it
    ``abandoned`` after ``STALE_RUN_AFTER`` — it never claims a result it lacks.
    """
    from app.observability.logging import get_logger

    log = get_logger(__name__)
    try:
        result = await runner.run_suite(
            suite_id=suite_id, goal_service=goal_service, tenant_ctx=ctx,
            tasks=tasks, run_id=run_id,
        )
        await store.finish_run(suite_id, run_id, result=result)
    except Exception as exc:
        log.warning("eval_suite_run_failed", suite_id=suite_id, run_id=run_id, error=str(exc))
        try:
            await store.finish_run(suite_id, run_id, result=None, error=str(exc)[:2000])
        except Exception as store_exc:
            log.error("eval_suite_run_status_lost", run_id=run_id, error=str(store_exc))


@intelligence_router.post("/eval-suites/{suite_id}/run", status_code=202)
async def run_eval_suite(request: Request, suite_id: str) -> dict[str, Any]:
    """Start a run of one of the caller's eval suites against the live agent.

    Returns 202 immediately with a ``run_id``; poll ``GET .../results`` for the
    outcome. Every golden task is a real goal that may take up to a minute, so
    the run no longer executes inside the request (which held the connection
    for the whole suite and was cut off by any proxy timeout).
    """
    import uuid as _uuid

    from app.intelligence.eval_suite_store import task_from_dict

    ctx = _require_tenant(request)
    runner = _eval_runner(request)
    goal_service = getattr(request.app.state, "goal_service", None)
    if goal_service is None:
        raise HTTPException(503, "Goal service not configured")
    store = _eval_store(request)
    suite = await store.get(suite_id)
    if suite is None:
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    tasks = [task_from_dict(suite_id, t) for t in suite["tasks"]]
    run_id = _uuid.uuid4().hex
    await store.start_run(suite_id, run_id, len(tasks))

    running: set[asyncio.Task[None]] = request.app.state.__dict__.setdefault(
        "_eval_run_tasks", set()
    )
    task = asyncio.create_task(
        _execute_eval_run(store, runner, goal_service, ctx, suite_id, run_id, tasks)
    )
    running.add(task)  # keep a strong reference until it finishes
    task.add_done_callback(running.discard)
    return {"run_id": run_id, "suite_id": suite_id, "status": "running", "total": len(tasks)}


@intelligence_router.get("/eval-suites/{suite_id}/results")
async def get_suite_results(request: Request, suite_id: str) -> list[dict[str, Any]]:
    """Newest-first run history of one of the caller's eval suites."""
    store = _eval_store(request)
    if await store.get(suite_id) is None:
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    runs: list[dict[str, Any]] = await store.list_runs(suite_id)
    return runs


@intelligence_router.get("/eval/dimensions")
async def get_eval_dimensions(request: Request) -> dict[str, Any]:
    """Return all 7 evaluation dimension names produced by EvalRunner."""
    from app.intelligence.eval_runner import EvalRunner

    return {"dimensions": EvalRunner.DIMENSIONS, "count": len(EvalRunner.DIMENSIONS)}


# ── Prompt Variants (PromptOptimizer A/B testing) ─────────────────────────────


def _prompt_optimizer_svc(request: Request) -> Any:
    """Return the PromptOptimizer from app.state, falling back to the default instance."""
    opt = getattr(request.app.state, "prompt_optimizer", None)
    if opt is None:
        from app.intelligence.prompt_optimizer import _default_optimizer

        opt = _default_optimizer
    return opt


class CreateVariantRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    prompt_text: str = Field(..., min_length=1)


def _variant_json(opt: Any, v: Any) -> dict[str, Any]:
    from app.intelligence.prompt_optimizer import VariantStats

    stats = VariantStats.of(v)
    return {
        "id": v.variant_id,
        "key": v.prompt_key,
        "name": v.name,
        "prompt_text": v.prompt_text,
        "is_control": v.is_control,
        "run_count": v.run_count,
        "mean_score": round(stats.mean, 4) if v.run_count else None,
        # Per-run samples are only kept in memory; the DB keeps aggregates.
        "p95_score": opt._percentile(v.eval_scores, 95) if v.eval_scores else None,
        "mean_cost_usd": round(stats.mean_cost_usd, 6) if stats.cost_samples else None,
        "p95_latency_ms": stats.p95_latency_ms if sum(v.latency_hist) else None,
        "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
    }


def _db_mode(opt: Any) -> bool:
    return getattr(opt, "db_mode", False) is True


def _own_variant(opt: Any, tenant_id: str, variant_id: str) -> Any:
    """In-memory lookup restricted to the caller's own scope."""
    return opt._variants.get(tenant_id, {}).get(variant_id)


@intelligence_router.get("/prompt-variants")
async def list_prompt_variants(request: Request, key: str = "") -> list[dict[str, Any]]:
    """List the tenant's prompt variants (the shared "global" ones when it has none)."""
    ctx = _require_tenant(request)
    opt = _prompt_optimizer_svc(request)
    if _db_mode(opt):
        variants = await opt.alist(ctx.tenant_id, key)
    else:
        variants = list(opt._variants.get(ctx.tenant_id, {}).values())
        if not variants:
            variants = list(opt._variants.get("global", {}).values())
        if key:
            variants = [v for v in variants if v.prompt_key == key]
    return [_variant_json(opt, v) for v in variants]


@intelligence_router.post("/prompt-variants", status_code=201)
async def create_prompt_variant(request: Request, body: CreateVariantRequest) -> dict[str, Any]:
    """Register a new challenger prompt variant for A/B testing."""
    ctx = _require_tenant(request)
    opt = _prompt_optimizer_svc(request)
    if _db_mode(opt):
        variant = await opt.aregister(
            body.key, body.name, body.prompt_text, tenant_id=ctx.tenant_id, is_control=False
        )
        if variant is None:
            raise HTTPException(409, f"A variant named {body.name!r} already exists for this key")
    else:
        variant = opt.register_variant(
            body.key, body.name, body.prompt_text, tenant_id=ctx.tenant_id, is_control=False
        )
    return _variant_json(opt, variant)


@intelligence_router.post("/prompt-variants/{variant_id}/promote")
async def promote_prompt_variant(request: Request, variant_id: str) -> dict[str, Any]:
    """Manually promote one of the caller's own variants to control.

    Used to search every tenant's variants and promote whatever matched — one
    tenant could flip another tenant's live planner prompt — and to demote the
    caller's control while promoting a foreign row. Only the caller's own
    variants are eligible now; shared "global" variants are read-only.
    """
    ctx = _require_tenant(request)
    from datetime import UTC
    from datetime import datetime as _dt

    opt = _prompt_optimizer_svc(request)
    tenant_id = ctx.tenant_id
    if _db_mode(opt):
        promoted = await opt.apromote(variant_id, tenant_id)
        if promoted is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        target = promoted
    else:
        target = _own_variant(opt, tenant_id, variant_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        for v in opt._variants[tenant_id].values():
            if v.prompt_key == target.prompt_key and v.is_control and v.variant_id != variant_id:
                v.is_control = False
                v.is_active = False
        target.is_control = True
        target.is_active = True
        target.promoted_at = _dt.now(UTC)
        opt._active.setdefault(tenant_id, {})[target.prompt_key] = variant_id
    return {
        "id": variant_id,
        "key": target.prompt_key,
        "promoted": True,
        "promoted_at": target.promoted_at.isoformat() if target.promoted_at else None,
    }


@intelligence_router.delete("/prompt-variants/{variant_id}", status_code=204)
async def delete_prompt_variant(request: Request, variant_id: str) -> Response:
    """Delete one of the caller's own variants (shared "global" ones are read-only)."""
    ctx = _require_tenant(request)
    opt = _prompt_optimizer_svc(request)
    if _db_mode(opt):
        deleted = await opt.adelete(variant_id, ctx.tenant_id)
    else:
        deleted = opt._variants.get(ctx.tenant_id, {}).pop(variant_id, None) is not None
    if not deleted:
        raise HTTPException(status_code=404, detail="Variant not found")
    return Response(status_code=204)


@intelligence_router.get("/prompt-variants/{variant_id}/report")
async def get_variant_report(request: Request, variant_id: str) -> dict[str, Any]:
    """Score, cost and latency report for a variant the caller can see."""
    ctx = _require_tenant(request)
    opt = _prompt_optimizer_svc(request)
    if _db_mode(opt):
        found = await opt.aget(variant_id, ctx.tenant_id)
        target = found[1] if found is not None else None
    else:
        target = _own_variant(opt, ctx.tenant_id, variant_id) or _own_variant(
            opt, "global", variant_id
        )
    if target is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    body = _variant_json(opt, target)
    body.pop("prompt_text", None)
    return {**body, "win_rate": None, "statistical_significance": None}


# ── P2.10: Async GDPR Export + Consent Management ─────────────────────────────


def _gdpr_db_or_503(request: Request, what: str) -> Any:
    """GDPR export/consent state lives only in Postgres — without it there is
    nothing to record, so answer 503 instead of a fabricated success."""
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, f"{what} is unavailable (no database configured)")
    return db


@compliance_router.post("/export/start")
async def start_gdpr_export(request: Request) -> dict[str, Any]:
    """Start async GDPR data export job. Returns job_id for polling.

    503 when the job row cannot be recorded or the worker task cannot be
    enqueued — this used to swallow both and answer ``pending`` for a job that
    might not exist or that nothing would ever run.
    """
    import logging

    from sqlalchemy import text

    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "GDPR export")
    log = logging.getLogger(__name__)

    job_id = uuid.uuid4().hex
    try:
        # gdpr_export_jobs is tenant-isolated by RLS; under the API's
        # NOBYPASSRLS role the INSERT is rejected unless the tenant GUC is set.
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            await session.execute(
                text("""
                INSERT INTO gdpr_export_jobs (id, tenant_id, status, created_at)
                VALUES (:id, :tid, 'pending', NOW())
            """),
                {"id": job_id, "tid": ctx.tenant_id},
            )
    except Exception as exc:
        log.error("gdpr_export_job_insert_failed: %s", exc)
        raise HTTPException(503, "GDPR export job could not be recorded; retry") from exc

    try:
        from app.scaling.tasks import run_gdpr_export

        run_gdpr_export.delay(job_id, ctx.tenant_id)
    except Exception as exc:
        log.error("gdpr_export_enqueue_failed: %s", exc)
        # The row exists but no worker will pick it up: mark it failed so a poll
        # reports the truth instead of 'pending' forever.
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, ctx.tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE gdpr_export_jobs SET status = 'failed', "
                        "error_message = :err WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": job_id, "tid": ctx.tenant_id, "err": "could not be enqueued"},
                )
        except Exception as mark_exc:
            log.error("gdpr_export_mark_failed_failed: %s", mark_exc)
        raise HTTPException(503, "GDPR export could not be queued; retry") from exc

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_url": f"/compliance/export/jobs/{job_id}",
    }


@compliance_router.get("/export/jobs")
async def list_gdpr_export_jobs(
    request: Request, limit: int = Query(10, ge=1, le=100)
) -> dict[str, Any]:
    """The tenant's most recent GDPR export jobs, newest first.

    Lets the privacy page show the real state of an export after a reload.
    503 without a database or on a read error — never an empty list for a
    read that did not happen.
    """
    import logging

    from sqlalchemy import text

    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "GDPR export status")
    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, status, created_at, completed_at, download_url, "
                        "error_message FROM gdpr_export_jobs WHERE tenant_id = :tid "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"tid": ctx.tenant_id, "lim": limit},
                )
            ).fetchall()
    except Exception as exc:
        logging.getLogger(__name__).error("gdpr_export_jobs_list_failed: %s", exc)
        raise HTTPException(503, "GDPR export jobs could not be read; retry") from exc
    return {
        "jobs": [
            {
                "job_id": r[0],
                "status": r[1],
                "created_at": r[2].isoformat() if r[2] else None,
                "completed_at": r[3].isoformat() if r[3] else None,
                "download_url": r[4],
                "error": r[5],
            }
            for r in rows
        ]
    }


@compliance_router.get("/export/jobs/{job_id}")
async def get_gdpr_export_status(request: Request, job_id: str) -> dict[str, Any]:
    """Poll status of async GDPR export job (503 without a database — never a
    fabricated 'pending')."""
    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "GDPR export status")
    from sqlalchemy import text

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, ctx.tenant_id),
    ):
        row = (
            await session.execute(
                text("""
            SELECT status, completed_at, download_url, error_message
            FROM gdpr_export_jobs WHERE id = :id AND tenant_id = :tid
        """),
                {"id": job_id, "tid": ctx.tenant_id},
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Export job not found")
    return {
        "job_id": job_id,
        "status": row[0],
        "completed_at": row[1].isoformat() if row[1] else None,
        "download_url": row[2],
        "error": row[3],
    }


class ConsentRequest(BaseModel):
    purpose: str  # "analytics", "marketing", "ai_processing", etc.
    legal_basis: str = "legitimate_interest"  # GDPR legal basis


@compliance_router.get("/consent")
async def list_consent(request: Request) -> dict[str, Any]:
    """The tenant's active (non-revoked) consent records.

    503 without a database or on a read error: an unreadable consent state must
    never be shown as "not granted" (or granted).
    """
    import logging

    from sqlalchemy import text

    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "Consent state")
    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT purpose, legal_basis, granted_at FROM consent_records "
                        "WHERE tenant_id = :tid AND revoked_at IS NULL "
                        "ORDER BY granted_at DESC"
                    ),
                    {"tid": ctx.tenant_id},
                )
            ).fetchall()
    except Exception as exc:
        logging.getLogger(__name__).error("consent_list_failed: %s", exc)
        raise HTTPException(503, "Consent state could not be read; retry") from exc
    consents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for purpose, basis, granted_at in rows:
        if purpose in seen:  # newest active record per purpose
            continue
        seen.add(purpose)
        consents.append(
            {
                "purpose": purpose,
                "legal_basis": basis,
                "granted_at": granted_at.isoformat() if granted_at else None,
            }
        )
    return {"consents": consents, "active_purposes": [c["purpose"] for c in consents]}


@compliance_router.post("/consent")
async def record_consent(request: Request, body: ConsentRequest) -> dict[str, Any]:
    """Record tenant consent for data processing purposes.

    503 when the record could not be written (the old handler swallowed the DB
    error and answered ``recorded`` for consent that was never stored).
    """
    import logging

    from sqlalchemy import text

    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "Consent recording")
    consent_id = uuid.uuid4().hex
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            await session.execute(
                text("""
                INSERT INTO consent_records
                    (id, tenant_id, purpose, legal_basis, ip_address, user_agent)
                VALUES (:id, :tid, :purpose, :basis, :ip, :ua)
            """),
                {
                    "id": consent_id,
                    "tid": ctx.tenant_id,
                    "purpose": body.purpose,
                    "basis": body.legal_basis,
                    "ip": ip,
                    "ua": ua,
                },
            )
    except Exception as exc:
        logging.getLogger(__name__).error("consent_record_insert_failed: %s", exc)
        raise HTTPException(503, "Consent could not be recorded; retry") from exc
    return {"consent_id": consent_id, "purpose": body.purpose, "status": "recorded"}


@compliance_router.delete("/consent/{purpose}")
async def revoke_consent(request: Request, purpose: str) -> dict[str, Any]:
    """Revoke previously granted consent.

    503 when the revocation could not be written, 404 when there is no active
    consent for the purpose — never ``revoked`` for a write that did not happen.
    """
    import logging

    from sqlalchemy import text

    ctx = _require_tenant(request)
    db = _gdpr_db_or_503(request, "Consent revocation")
    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            result = await session.execute(
                text("""
                UPDATE consent_records SET revoked_at = NOW()
                WHERE tenant_id = :tid AND purpose = :purpose AND revoked_at IS NULL
            """),
                {"tid": ctx.tenant_id, "purpose": purpose},
            )
            revoked = int(getattr(result, "rowcount", 0) or 0)
    except Exception as exc:
        logging.getLogger(__name__).error("consent_revoke_failed: %s", exc)
        raise HTTPException(503, "Consent could not be revoked; retry") from exc
    if revoked == 0:
        raise HTTPException(404, f"No active consent for purpose {purpose!r}")
    return {"purpose": purpose, "status": "revoked", "revoked": revoked}


# =============================================================================
# Compliance v2 — dynamic compliance status (no hardcoded booleans)
# =============================================================================


@router.get("/compliance/{framework}")
async def get_compliance_status(request: Request, framework: str) -> dict[str, Any]:
    """
    Dynamic compliance status for a framework.

    FIX: Replaces the hardcoded gdpr_compliant=True from get_data_residency().
    Reads actual DB state via ComplianceChecker.
    """
    ctx = _require_tenant(request)
    checker = _compliance_checker(request)
    if checker is None:
        raise HTTPException(503, "Compliance checker not configured")

    framework_lower = framework.lower()
    if framework_lower == "hipaa":
        return await checker.check_hipaa(ctx.tenant_id)
    if framework_lower == "gdpr":
        return await checker.check_gdpr(ctx.tenant_id)
    if framework_lower in ("soc2", "soc2_type2"):
        return await checker.check_soc2(ctx.tenant_id)
    raise HTTPException(400, f"Unsupported framework '{framework}'. Use: hipaa, gdpr, soc2")


@router.post("/compliance/{framework}/check")
async def rerun_compliance_check(request: Request, framework: str) -> dict[str, Any]:
    """Re-run compliance checks and return fresh results."""
    return await get_compliance_status(request, framework)


# =============================================================================
# Contract management — BAA, DPA, MSA signing
# =============================================================================


class ContractSignRequest(BaseModel):
    signer_name: str = Field(..., min_length=1, max_length=200)
    signer_email: str = Field(..., min_length=3, max_length=200)
    signer_title: str = ""


@router.get("/contracts")
async def list_contracts(request: Request) -> list[dict[str, Any]]:
    """List enterprise contracts for the tenant."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text("""
                SELECT id, contract_type, status, version,
                       signed_by_name, signed_by_email, signed_at, expires_at,
                       document_url, created_at
                FROM enterprise_contracts
                WHERE tenant_id = :tid
                ORDER BY created_at DESC
            """),
                    {"tid": ctx.tenant_id},
                )
            ).fetchall()
        return [
            {
                "id": str(r[0]),
                "contract_type": r[1],
                "status": r[2],
                "version": r[3],
                "signed_by_name": r[4],
                "signed_by_email": r[5],
                "signed_at": str(r[6]) if r[6] else None,
                "expires_at": str(r[7]) if r[7] else None,
                "document_url": r[8],
                "created_at": str(r[9]) if r[9] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("list_contracts_failed: %s", exc)
        return []


@router.post("/contracts/{contract_type}/sign", status_code=201)
async def sign_contract(
    request: Request, contract_type: str, body: ContractSignRequest
) -> dict[str, Any]:
    """Sign a contract (BAA, DPA, MSA, etc.) on the tenant's behalf.

    Admin-only: signing a legal agreement binds the tenant, and any
    authenticated key (a viewer, a CI key) could do it. The response carries the
    stored ``signed_at`` (it used to be the literal string ``"now"``).
    """
    ctx = _require_tenant(request)
    from app.tenancy.rbac import has_role

    if not has_role(ctx, "admin"):
        raise HTTPException(403, "Signing a contract requires the admin role")
    db = _get_db(request)
    valid_types = {"baa", "dpa", "msa", "nda", "sla", "custom"}
    if contract_type not in valid_types:
        raise HTTPException(400, f"Invalid contract_type. Must be one of: {valid_types}")
    if db is None:
        raise HTTPException(503, "Database not configured")

    contract_id = uuid.uuid4().hex
    try:
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                    INSERT INTO enterprise_contracts
                        (id, tenant_id, contract_type, status, signed_by_name,
                         signed_by_email, signed_at, created_at)
                    VALUES
                        (:id, :tid, :ctype, 'signed', :name, :email, NOW(), NOW())
                    RETURNING signed_at
                """),
                    {
                        "id": contract_id,
                        "tid": ctx.tenant_id,
                        "ctype": contract_type,
                        "name": body.signer_name,
                        "email": body.signer_email,
                    },
                )
            ).first()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("contract_sign_failed: %s", exc)
        raise HTTPException(503, "Contract could not be recorded; retry") from exc
    if row is None:
        raise HTTPException(503, "Contract could not be recorded; retry")
    signed_at = row[0]

    return {
        "contract_id": contract_id,
        "contract_type": contract_type,
        "status": "signed",
        "signed_by": body.signer_name,
        "signed_at": signed_at.isoformat() if hasattr(signed_at, "isoformat") else str(signed_at),
    }


# =============================================================================
# SAML 2.0 SSO endpoints
# =============================================================================


def _saml_acs_url(request: Request, tenant_id: str) -> str:
    """The ACS URL advertised to the IdP — the route that actually serves it.

    It used to be ``{base}/api/enterprise/saml/acs`` while the route is
    ``/enterprise/saml/acs`` (no ``/api``), and that route required a tenant API
    key an IdP POST never carries. The tenant is now named in the path.
    """
    return f"{str(request.base_url).rstrip('/')}/enterprise/saml/acs/{tenant_id}"


@router.get("/saml/metadata", response_class=Response)
async def get_saml_metadata(request: Request) -> Response:
    """Return SP metadata XML for IdP configuration."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    try:
        from sqlalchemy import text

        from app.auth.saml_provider import SAMLProvider

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id,
                       attribute_mapping, name_id_format
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """),
                    {"tid": ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured for this tenant")
        provider = SAMLProvider(
            tenant_id=ctx.tenant_id,
            idp_entity_id=row[0],
            idp_sso_url=row[1],
            idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=_saml_acs_url(request, ctx.tenant_id),
            attribute_mapping=row[4] or {},
            name_id_format=row[5] or "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        )
        xml = provider.get_sp_metadata()
        return Response(content=xml, media_type="application/xml")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"SAML metadata error: {exc}") from exc


class SAMLConfigRequest(BaseModel):
    idp_entity_id: str
    idp_sso_url: str
    idp_cert: str
    sp_entity_id: str
    attribute_mapping: dict[str, str] = {}
    default_role: str = "viewer"
    jit_provisioning: bool = True


@router.post("/saml/configure", status_code=201)
async def configure_saml(request: Request, body: SAMLConfigRequest) -> dict[str, Any]:
    """Configure SAML 2.0 IdP for this tenant."""
    ctx = _require_tenant(request)
    _require_admin(ctx, "Configuring SAML")
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    try:
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            await session.execute(
                text("""
                INSERT INTO saml_configs
                    (id, tenant_id, idp_entity_id, idp_sso_url, idp_cert,
                     sp_entity_id, attribute_mapping, default_role, jit_provisioning,
                     is_active, created_at, updated_at)
                VALUES
                    (:id, :tid, :idp_entity, :idp_sso, :idp_cert,
                     :sp_entity, CAST(:mapping AS jsonb), :role, :jit,
                     TRUE, NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE
                  SET idp_entity_id = EXCLUDED.idp_entity_id,
                      idp_sso_url = EXCLUDED.idp_sso_url,
                      idp_cert = EXCLUDED.idp_cert,
                      attribute_mapping = EXCLUDED.attribute_mapping,
                      default_role = EXCLUDED.default_role,
                      jit_provisioning = EXCLUDED.jit_provisioning,
                      is_active = TRUE,
                      updated_at = NOW()
            """),
                {
                    "id": uuid.uuid4().hex,
                    "tid": ctx.tenant_id,
                    "idp_entity": body.idp_entity_id,
                    "idp_sso": body.idp_sso_url,
                    "idp_cert": body.idp_cert,
                    "sp_entity": body.sp_entity_id,
                    "mapping": json.dumps(body.attribute_mapping),
                    "role": body.default_role,
                    "jit": body.jit_provisioning,
                },
            )
    except Exception as exc:
        raise HTTPException(500, f"SAML configuration failed: {exc}") from exc
    return {"status": "configured", "tenant_id": ctx.tenant_id}


@router.get("/saml/login")
async def saml_login(request: Request) -> Response:
    """Initiate SAML SSO — redirect to IdP."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    try:
        from fastapi.responses import RedirectResponse
        from sqlalchemy import text

        from app.auth.saml_provider import SAMLProvider

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """),
                    {"tid": ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured")
        provider = SAMLProvider(
            tenant_id=ctx.tenant_id,
            idp_entity_id=row[0],
            idp_sso_url=row[1],
            idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=_saml_acs_url(request, ctx.tenant_id),
        )
        redirect_url = provider.initiate_login()
        return RedirectResponse(url=redirect_url)
    except HTTPException:
        raise
    except SAMLNotInstalledError as exc:
        raise HTTPException(501, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"SAML login error: {exc}") from exc


@router.post("/saml/acs/{tenant_id}")
async def saml_acs(request: Request, tenant_id: str) -> JSONResponse:
    """SAML Assertion Consumer Service for *tenant_id* (the IdP's HTTP-POST target).

    Public by design (TenantMiddleware bypass ``/enterprise/saml/acs/``): the
    signed assertion, validated against the tenant's configured IdP
    certificate in python3-saml strict mode, is the authentication. Replay
    protection (Redis) fails closed (503).

    NOT IMPLEMENTED: turning a verified identity into an AgentVerse session (JIT
    user provisioning + a credential TenantMiddleware accepts). The endpoint
    used to answer 200 ``{"authenticated": true}`` with no session at all; it
    now answers 501 after verifying the assertion.
    """
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    try:
        form = await request.form()
        saml_response = form.get("SAMLResponse", "")
        if not saml_response:
            raise HTTPException(400, "Missing SAMLResponse in form data")
        from sqlalchemy import text

        from app.auth.saml_provider import SAMLProvider

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id,
                       attribute_mapping
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """),
                    {"tid": tenant_id},
                )
            ).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured")
        # app.state.redis is never set (the runtime client is app.state._redis):
        # reading it silently disabled replay protection.
        redis = getattr(request.app.state, "_redis", None)
        provider = SAMLProvider(
            tenant_id=tenant_id,
            idp_entity_id=row[0],
            idp_sso_url=row[1],
            idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=_saml_acs_url(request, tenant_id),
            attribute_mapping=row[4] or {},
            redis=redis,
        )
        identity = await provider.process_acs(str(saml_response))
    except HTTPException:
        raise
    except SAMLNotInstalledError as exc:
        # Was a 500 ("SAML ACS error: python3-saml is not installed").
        raise HTTPException(501, str(exc)) from exc
    except SAMLReplayCheckUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"SAML ACS error: {exc}") from exc
    return JSONResponse(
        status_code=501,
        content={
            "error": "NOT_IMPLEMENTED",
            "detail": (
                "The SAML assertion is valid, but issuing an AgentVerse session "
                "for SAML users is not implemented."
            ),
            "email": identity.email,
            "name_id": identity.name_id,
            "authenticated": False,
        },
    )


@router.post("/saml/test")
async def test_saml_connection(request: Request) -> dict[str, Any]:
    """Test SAML IdP connectivity by checking SSO URL reachability or validating metadata XML."""
    _require_tenant(request)
    body = await request.json()
    sso_url = body.get("sso_url", "")
    metadata_xml = body.get("metadata_xml", "")

    import time

    import httpx

    from app.net.ssrf_guard import SSRFError, request_public

    start = time.monotonic()

    try:
        if sso_url:
            # Was client.get(sso_url, follow_redirects=True) on any caller URL: an
            # internal-network probe (status code + latency as the oracle), direct
            # or via redirect. Every hop is now SSRF-validated.
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                try:
                    resp = await request_public(client, "GET", str(sso_url), context="saml test")
                except SSRFError as exc:
                    raise HTTPException(
                        400, "sso_url is not an allowed public http(s) URL"
                    ) from exc
                latency_ms = int((time.monotonic() - start) * 1000)
                return {
                    "success": resp.status_code < 500,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "message": f"IdP responded with HTTP {resp.status_code}",
                }
        elif metadata_xml:
            import xml.etree.ElementTree as ET

            try:
                ET.fromstring(metadata_xml)
                return {"success": True, "latency_ms": 1, "message": "Metadata XML is valid"}
            except ET.ParseError as exc:
                return {"success": False, "message": f"Invalid XML: {exc}"}
        else:
            return {"success": False, "message": "No SSO URL or metadata XML provided"}
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "success": False,
            "message": f"Connection failed: {exc}",
            "latency_ms": int((time.monotonic() - start) * 1000),
        }


# =============================================================================
# SCIM 2.0 provisioning endpoints
# =============================================================================


async def _get_scim_handler(request: Request) -> SCIMHandler:  # noqa: F821
    """Authenticate + build SCIMHandler for this request."""
    from app.auth.scim_handler import SCIMHandler, require_scim_auth

    tenant_id = await require_scim_auth(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    # Load scim_configs for this tenant. The tenant is the one the SCIM bearer
    # token resolved to, so from here on everything runs under its RLS GUC.
    try:
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                SELECT allow_user_create, allow_user_update, allow_user_delete,
                       allow_group_sync, default_role, group_role_map
                FROM scim_configs WHERE tenant_id = :tid AND is_active = TRUE
            """),
                    {"tid": tenant_id},
                )
            ).fetchone()
        config = {
            "allow_user_create": row[0] if row else True,
            "allow_user_update": row[1] if row else True,
            "allow_user_delete": row[2] if row else False,
            "allow_group_sync": row[3] if row else True,
            "default_role": row[4] if row else "viewer",
            "group_role_map": row[5] if row else {},
        }
    except Exception as exc:
        # Fail CLOSED: an unreadable config used to fall back to permissive
        # create/update defaults, bypassing a tenant's "no user creation" policy.
        raise HTTPException(503, "SCIM configuration unavailable; retry") from exc
    return SCIMHandler(tenant_id=tenant_id, config=config, db_factory=db)


@scim_router.get("/Users")
async def scim_list_users(
    request: Request,
    startIndex: int = 1,  # noqa: N803  # SCIM RFC 7644 mandates this exact query param name
    count: int = 100,
    filter_: str = Query("", alias="filter", max_length=1000),
) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.list_users(start_index=startIndex, count=count, filter_str=filter_)


@scim_router.get("/Users/{scim_id}")
async def scim_get_user(request: Request, scim_id: str) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.get_user(scim_id)


@scim_router.post("/Users", status_code=201)
async def scim_create_user(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.create_user(body)


@scim_router.put("/Users/{scim_id}")
async def scim_replace_user(request: Request, scim_id: str, body: dict[str, Any]) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.update_user(scim_id, body, partial=False)


@scim_router.patch("/Users/{scim_id}")
async def scim_patch_user(request: Request, scim_id: str, body: dict[str, Any]) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.update_user(scim_id, body, partial=True)


@scim_router.delete("/Users/{scim_id}", status_code=204)
async def scim_delete_user(request: Request, scim_id: str) -> None:
    handler = await _get_scim_handler(request)
    await handler.delete_user(scim_id)


# ── SCIM token provisioning (admin endpoint) ─────────────────────────────────


@router.post("/scim/provision-token", status_code=201)
async def provision_scim_token(request: Request) -> dict[str, Any]:
    """
    Generate a new SCIM bearer token for this tenant.
    Token is SHA-256 hashed before storage.
    """
    ctx = _require_tenant(request)
    _require_admin(ctx, "Provisioning a SCIM token")
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")

    from app.enterprise.compliance_v2 import generate_scim_token

    raw_token, prefix, token_hash = generate_scim_token(ctx.tenant_id)

    try:
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, ctx.tenant_id),
        ):
            await session.execute(
                text("""
                INSERT INTO scim_tokens (id, tenant_id, token_hash, created_at)
                VALUES (:id, :tid, :hash, NOW())
            """),
                {"id": uuid.uuid4().hex, "tid": ctx.tenant_id, "hash": token_hash},
            )
    except Exception as exc:
        raise HTTPException(500, f"Token provisioning failed: {exc}") from exc

    return {
        "token": raw_token,
        "prefix": prefix,
        "note": "Store this token securely — it will not be shown again.",
    }
