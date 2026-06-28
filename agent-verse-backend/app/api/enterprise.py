"""Enterprise API — compliance, simulation, red-team, marketplace, intelligence,
SAML 2.0 SSO, SCIM 2.0 provisioning, and contract management."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

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


def _compliance(request: Request) -> Any:
    return request.app.state.compliance_controller


def _compliance_checker(request: Request) -> Any:
    """Return the v2 ComplianceChecker (dynamically computed, not hardcoded)."""
    return getattr(request.app.state, "compliance_checker", None)


def _simulation(request: Request) -> Any:
    return request.app.state.simulation_runner


def _red_team(request: Request) -> Any:
    return request.app.state.red_team_runner


def _marketplace(request: Request) -> Any:
    return request.app.state.marketplace


def _marketplace_v2(request: Request) -> Any:
    """Return the DB-backed MarketplaceV2 service (falls back to v1)."""
    v2 = getattr(request.app.state, "marketplace_v2", None)
    if v2 is not None:
        return v2
    # Lazy import fallback: return an un-wired MarketplaceV2 for environments
    # that haven't run the lifespan yet (e.g. some test setups).
    from app.enterprise.marketplace_v2 import MarketplaceV2
    return MarketplaceV2(db_factory=None)


def _self_optimizer(request: Request) -> Any:
    return request.app.state.self_optimizer


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
            "Content-Disposition": (
                f'attachment; filename="agentverse-export-{request_id}.json"'
            )
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
    ctx = _require_tenant(request)
    return await _compliance(request).request_data_deletion(tenant_ctx=ctx)


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
        goal=body.goal, mock_tools=body.mock_tools, tenant_ctx=ctx
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


@router.get("/simulation/{run_id}")
async def get_simulation(request: Request, run_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    run = _simulation(request).get(run_id=run_id, tenant_ctx=ctx)
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
async def stream_simulation(request: Request, body: StreamingSimulationRequest) -> StreamingResponse:
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


@router.get("/simulation/available-tools")
async def get_simulation_available_tools(request: Request) -> dict[str, Any]:
    """Return MCP tools available for mock configuration in simulation."""
    ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)
    tools: list[dict[str, Any]] = []
    if mcp_client is not None:
        try:
            raw_tools = await mcp_client.discover_all_tools(tenant_ctx=ctx)
            for t in raw_tools:
                tools.append({
                    "name": t.get("name", "") if isinstance(t, dict) else str(t),
                    "description": t.get("description", "") if isinstance(t, dict) else "",
                    "server_id": t.get("server_id", "") if isinstance(t, dict) else "",
                })
        except Exception:
            pass
    return {"tools": tools, "total": len(tools)}


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
    visibility: str = "community"  # private | team | community (default: community)


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
    visibility: str = "private"
    version: str = "1.0.0"


class DeployV2Request(BaseModel):
    params: dict[str, Any] = {}


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
    """Publish an agent template to the community marketplace."""
    ctx = _require_tenant(request)

    template_data = body.model_dump()

    # If agent_id provided, enrich with agent config
    if body.agent_id:
        store = getattr(request.app.state, "agent_store", None)
        if store:
            agent = store.get(body.agent_id, tenant_ctx=ctx)
            if agent:
                template_data.setdefault("connectors", agent.get("connector_ids", []))

    return _marketplace(request).publish(template=template_data, tenant_ctx=ctx)


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
    return _marketplace(request).browse(query=q, domain=domain, tenant_ctx=ctx)


# ── V2: paginated template list ───────────────────────────────────────────────

@marketplace_router.get("/templates")
async def list_templates_v2(
    request: Request,
    domain: str = "",
    category: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Paginated marketplace template list with full-text search and filters."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    return await svc.list_templates(
        domain=domain,
        category=category,
        search=search,
        tenant_id=ctx.tenant_id,
        page=page,
        page_size=page_size,
    )


@marketplace_router.post("/templates", status_code=201)
async def publish_template_v2(
    request: Request, body: PublishTemplateV2Request
) -> dict[str, Any]:
    """Publish a template using the V2 DB-backed service (triggers security review)."""
    ctx = _require_tenant(request)
    svc = _marketplace_v2(request)
    return await svc.publish_template(
        data=body.model_dump(),
        tenant_ctx=ctx,
        run_security_review=True,
    )


@marketplace_router.get("/templates/{template_id}")
async def get_template_v2(request: Request, template_id: str) -> dict[str, Any]:
    """Get a template by ID from the V2 DB-backed service."""
    _require_tenant(request)
    svc = _marketplace_v2(request)
    t = await svc.get_template(template_id=template_id)
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
    result = await svc.add_review(
        template_id=template_id,
        tenant_ctx=ctx,
        rating=body.rating,
        title=body.title,
        body=body.body,
        verified_install=body.verified_install,
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


@marketplace_router.get("/{template_id}/versions")
async def get_template_versions(request: Request, template_id: str) -> list[dict[str, Any]]:
    """Return version history for a template."""
    _require_tenant(request)
    t = _marketplace(request).get_template(template_id=template_id)
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
    """Publish a versioned snapshot of a template."""
    _require_tenant(request)
    db = _get_db(request)
    version = str(body.get("version", "1.0.0"))
    changelog = str(body.get("changelog", ""))
    return await _marketplace(request).publish_version(
        template_id=template_id, version=version, changelog=changelog, db=db
    )


@marketplace_router.get("/{template_id}")
async def get_template(request: Request, template_id: str) -> dict[str, Any]:
    _require_tenant(request)
    t = _marketplace(request).get_template(template_id=template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


class DeployRequest(BaseModel):
    params: dict[str, Any] = {}


@marketplace_router.post("/{template_id}/deploy", status_code=201)
async def deploy_template(
    request: Request, template_id: str, body: DeployRequest
) -> dict[str, Any]:
    ctx = _require_tenant(request)
    try:
        dep = await _marketplace(request).deploy(
            template_id=template_id, params=body.params, tenant_ctx=ctx
        )
        return {
            "deployment_id": dep.deployment_id,
            "agent_id": dep.agent_id,
            "template_id": template_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Intelligence / Self-optimization ---

@intelligence_router.get("/suggestions")
async def list_suggestions(
    request: Request, applied: bool | None = None
) -> list[dict[str, Any]]:
    ctx = _require_tenant(request)
    suggestions = _self_optimizer(request).list_suggestions(tenant_ctx=ctx, applied=applied)
    return [
        {
            "suggestion_id": s.suggestion_id,
            "category": s.category,
            "description": s.description,
            "confidence": s.confidence,
            "applied": s.applied,
        }
        for s in suggestions
    ]


@intelligence_router.post("/suggestions/{suggestion_id}/apply")
async def apply_suggestion(request: Request, suggestion_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    ok = _self_optimizer(request).apply_suggestion(
        suggestion_id=suggestion_id, tenant_ctx=ctx
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"suggestion_id": suggestion_id, "applied": True}


@intelligence_router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(request: Request, suggestion_id: str) -> dict[str, Any]:
    ctx = _require_tenant(request)
    ok = _self_optimizer(request).reject_suggestion(
        suggestion_id=suggestion_id, tenant_ctx=ctx
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"suggestion_id": suggestion_id, "rejected": True}


# --- Eval Suites ---

class CreateEvalSuiteRequest(BaseModel):
    suite_id: str | None = None
    name: str = ""


class AddGoldenTaskRequest(BaseModel):
    goal: str
    expected_tools: list[str] = []
    forbidden_tools: list[str] = []
    expected_output_contains: list[str] = []
    max_iterations: int = 15
    tags: list[str] = []


@intelligence_router.post("/eval-suites", status_code=201)
async def create_eval_suite(request: Request, body: CreateEvalSuiteRequest) -> dict[str, Any]:
    """Create a new eval suite for golden task testing."""
    _require_tenant(request)
    import uuid as _uuid
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    suite_id = body.suite_id or _uuid.uuid4().hex
    runner.create_suite(suite_id)
    return {"suite_id": suite_id, "name": body.name, "task_count": 0}


@intelligence_router.get("/eval-suites")
async def list_eval_suites(request: Request) -> list[dict[str, Any]]:
    """List all eval suites."""
    _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        return []
    return [{"suite_id": s, "task_count": len(runner._suites.get(s, []))}
            for s in runner.list_suites()]


@intelligence_router.get("/eval-suites/{suite_id}")
async def get_eval_suite(request: Request, suite_id: str) -> dict[str, Any]:
    """Get a single eval suite by ID."""
    _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    suites = runner.list_suites()
    if suite_id not in suites:
        raise HTTPException(404, f"Eval suite {suite_id} not found")
    return {"suite_id": suite_id, "task_count": len(runner._suites.get(suite_id, []))}


@intelligence_router.post("/eval-suites/{suite_id}/tasks", status_code=201)
async def add_golden_task(
    request: Request, suite_id: str, body: AddGoldenTaskRequest
) -> dict[str, Any]:
    """Add a golden task to an eval suite."""
    _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    from app.intelligence.eval_suite import GoldenTask
    task = GoldenTask(
        suite_id=suite_id,
        goal=body.goal,
        expected_tools=body.expected_tools,
        forbidden_tools=body.forbidden_tools,
        expected_output_contains=body.expected_output_contains,
        max_iterations=body.max_iterations,
        tags=body.tags,
    )
    runner.add_task(suite_id, task)
    return {"task_id": task.task_id, "suite_id": suite_id, "goal": body.goal}


@intelligence_router.post("/eval-suites/{suite_id}/run")
async def run_eval_suite(request: Request, suite_id: str) -> dict[str, Any]:
    """Run an eval suite against the live agent."""
    ctx = _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    goal_service = request.app.state.goal_service
    result = await runner.run_suite(
        suite_id=suite_id, goal_service=goal_service, tenant_ctx=ctx
    )
    return {
        "run_id": result.run_id,
        "suite_id": suite_id,
        "total": result.total_tasks,
        "passed": result.passed_tasks,
        "failed": result.failed_tasks,
        "pass_rate": result.pass_rate,
        "run_at": result.run_at,
        "task_results": [
            {
                "task_id": r.task_id, "passed": r.passed,
                "failure_reasons": r.failure_reasons,
                "duration_seconds": round(r.duration_seconds, 2),
            }
            for r in result.task_results
        ],
    }


@intelligence_router.get("/eval-suites/{suite_id}/results")
async def get_suite_results(request: Request, suite_id: str) -> list[dict[str, Any]]:
    """Get historical results for an eval suite."""
    _require_tenant(request)
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        return []
    return [
        {"run_id": r.run_id, "pass_rate": r.pass_rate,
         "passed": r.passed_tasks, "failed": r.failed_tasks, "run_at": r.run_at}
        for r in runner.get_results(suite_id)
    ]


# ── P2.10: Async GDPR Export + Consent Management ─────────────────────────────

@compliance_router.post("/export/start")
async def start_gdpr_export(request: Request) -> dict[str, Any]:
    """Start async GDPR data export job. Returns job_id for polling."""
    ctx = _require_tenant(request)
    db = _get_db(request)

    job_id = uuid.uuid4().hex
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(text("""
                    INSERT INTO gdpr_export_jobs (id, tenant_id, status, created_at)
                    VALUES (:id, :tid, 'pending', NOW())
                """), {"id": job_id, "tid": ctx.tenant_id})
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("gdpr_export_job_insert_failed: %s", exc)

    # Enqueue Celery task (best-effort — job still exists if this fails)
    try:
        from app.scaling.tasks import run_gdpr_export
        run_gdpr_export.delay(job_id, ctx.tenant_id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("gdpr_export_enqueue_failed: %s", exc)

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_url": f"/compliance/export/jobs/{job_id}",
    }


@compliance_router.get("/export/jobs/{job_id}")
async def get_gdpr_export_status(request: Request, job_id: str) -> dict[str, Any]:
    """Poll status of async GDPR export job."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return {"job_id": job_id, "status": "pending", "completed_at": None,
                "download_url": None, "error": None}
    from sqlalchemy import text
    async with db() as session:
        row = (await session.execute(text("""
            SELECT status, completed_at, download_url, error_message
            FROM gdpr_export_jobs WHERE id = :id AND tenant_id = :tid
        """), {"id": job_id, "tid": ctx.tenant_id})).fetchone()
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


@compliance_router.post("/consent")
async def record_consent(request: Request, body: ConsentRequest) -> dict[str, Any]:
    """Record tenant consent for data processing purposes."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    consent_id = uuid.uuid4().hex
    if db is not None:
        try:
            from sqlalchemy import text
            ip = request.client.host if request.client else ""
            ua = request.headers.get("user-agent", "")
            async with db() as session, session.begin():
                await session.execute(text("""
                    INSERT INTO consent_records
                        (id, tenant_id, purpose, legal_basis, ip_address, user_agent)
                    VALUES (:id, :tid, :purpose, :basis, :ip, :ua)
                """), {
                    "id": consent_id, "tid": ctx.tenant_id,
                    "purpose": body.purpose, "basis": body.legal_basis,
                    "ip": ip, "ua": ua,
                })
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("consent_record_insert_failed: %s", exc)
    return {"consent_id": consent_id, "purpose": body.purpose, "status": "recorded"}


@compliance_router.delete("/consent/{purpose}")
async def revoke_consent(request: Request, purpose: str) -> dict[str, Any]:
    """Revoke previously granted consent."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(text("""
                    UPDATE consent_records SET revoked_at = NOW()
                    WHERE tenant_id = :tid AND purpose = :purpose AND revoked_at IS NULL
                """), {"tid": ctx.tenant_id, "purpose": purpose})
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("consent_revoke_failed: %s", exc)
    return {"purpose": purpose, "status": "revoked"}


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
        async with db() as session:
            rows = (await session.execute(text("""
                SELECT id, contract_type, status, version,
                       signed_by_name, signed_by_email, signed_at, expires_at,
                       document_url, created_at
                FROM enterprise_contracts
                WHERE tenant_id = :tid
                ORDER BY created_at DESC
            """), {"tid": ctx.tenant_id})).fetchall()
        return [
            {
                "id": str(r[0]), "contract_type": r[1], "status": r[2], "version": r[3],
                "signed_by_name": r[4], "signed_by_email": r[5],
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
    """Sign a contract (BAA, DPA, MSA, etc.)."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    valid_types = {"baa", "dpa", "msa", "nda", "sla", "custom"}
    if contract_type not in valid_types:
        raise HTTPException(400, f"Invalid contract_type. Must be one of: {valid_types}")
    if db is None:
        raise HTTPException(503, "Database not configured")

    contract_id = uuid.uuid4().hex
    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(text("""
                INSERT INTO enterprise_contracts
                    (id, tenant_id, contract_type, status, signed_by_name,
                     signed_by_email, signed_at, created_at)
                VALUES
                    (:id, :tid, :ctype, 'signed', :name, :email, NOW(), NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": contract_id, "tid": ctx.tenant_id,
                "ctype": contract_type, "name": body.signer_name,
                "email": body.signer_email,
            })
    except Exception as exc:
        raise HTTPException(500, f"Contract signing failed: {exc}")

    return {
        "contract_id": contract_id,
        "contract_type": contract_type,
        "status": "signed",
        "signed_by": body.signer_name,
        "signed_at": "now",
    }


# =============================================================================
# SAML 2.0 SSO endpoints
# =============================================================================

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
        async with db() as session:
            row = (await session.execute(text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id,
                       attribute_mapping, name_id_format
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """), {"tid": ctx.tenant_id})).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured for this tenant")
        base_url = str(request.base_url).rstrip("/")
        provider = SAMLProvider(
            tenant_id=ctx.tenant_id,
            idp_entity_id=row[0], idp_sso_url=row[1], idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=f"{base_url}/api/enterprise/saml/acs",
            attribute_mapping=row[4] or {},
            name_id_format=row[5] or "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        )
        xml = provider.get_sp_metadata()
        return Response(content=xml, media_type="application/xml")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"SAML metadata error: {exc}")


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
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")
    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(text("""
                INSERT INTO saml_configs
                    (id, tenant_id, idp_entity_id, idp_sso_url, idp_cert,
                     sp_entity_id, attribute_mapping, default_role, jit_provisioning,
                     is_active, created_at, updated_at)
                VALUES
                    (:id, :tid, :idp_entity, :idp_sso, :idp_cert,
                     :sp_entity, :mapping::jsonb, :role, :jit,
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
            """), {
                "id": uuid.uuid4().hex, "tid": ctx.tenant_id,
                "idp_entity": body.idp_entity_id, "idp_sso": body.idp_sso_url,
                "idp_cert": body.idp_cert, "sp_entity": body.sp_entity_id,
                "mapping": json.dumps(body.attribute_mapping),
                "role": body.default_role, "jit": body.jit_provisioning,
            })
    except Exception as exc:
        raise HTTPException(500, f"SAML configuration failed: {exc}")
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
        async with db() as session:
            row = (await session.execute(text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """), {"tid": ctx.tenant_id})).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured")
        base_url = str(request.base_url).rstrip("/")
        provider = SAMLProvider(
            tenant_id=ctx.tenant_id,
            idp_entity_id=row[0], idp_sso_url=row[1], idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=f"{base_url}/api/enterprise/saml/acs",
        )
        redirect_url = provider.initiate_login()
        return RedirectResponse(url=redirect_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"SAML login error: {exc}")


@router.post("/saml/acs")
async def saml_acs(request: Request) -> dict[str, Any]:
    """
    SAML Assertion Consumer Service — validate assertion and return user identity.
    Amendment 8.4: Replay protection via Redis.
    """
    ctx = _require_tenant(request)
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
        async with db() as session:
            row = (await session.execute(text("""
                SELECT idp_entity_id, idp_sso_url, idp_cert, sp_entity_id,
                       attribute_mapping
                FROM saml_configs WHERE tenant_id = :tid AND is_active = TRUE
            """), {"tid": ctx.tenant_id})).fetchone()
        if row is None:
            raise HTTPException(404, "SAML not configured")
        redis = getattr(request.app.state, "redis", None)
        base_url = str(request.base_url).rstrip("/")
        provider = SAMLProvider(
            tenant_id=ctx.tenant_id,
            idp_entity_id=row[0], idp_sso_url=row[1], idp_cert=row[2],
            sp_entity_id=row[3],
            acs_url=f"{base_url}/api/enterprise/saml/acs",
            attribute_mapping=row[4] or {},
            redis=redis,
        )
        identity = await provider.process_acs(str(saml_response))
        return {
            "email": identity.email,
            "name_id": identity.name_id,
            "first_name": identity.first_name,
            "last_name": identity.last_name,
            "department": identity.department,
            "authenticated": True,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(401, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"SAML ACS error: {exc}")


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
    # Load scim_configs for this tenant
    try:
        from sqlalchemy import text
        async with db() as session:
            row = (await session.execute(text("""
                SELECT allow_user_create, allow_user_update, allow_user_delete,
                       allow_group_sync, default_role, group_role_map
                FROM scim_configs WHERE tenant_id = :tid AND is_active = TRUE
            """), {"tid": tenant_id})).fetchone()
        config = {
            "allow_user_create": row[0] if row else True,
            "allow_user_update": row[1] if row else True,
            "allow_user_delete": row[2] if row else False,
            "allow_group_sync": row[3] if row else True,
            "default_role": row[4] if row else "viewer",
            "group_role_map": row[5] if row else {},
        }
    except Exception:
        config = {"allow_user_create": True, "allow_user_update": True,
                  "allow_user_delete": False, "default_role": "viewer", "group_role_map": {}}
    return SCIMHandler(tenant_id=tenant_id, config=config, db_factory=db)


@scim_router.get("/Users")
async def scim_list_users(
    request: Request, startIndex: int = 1, count: int = 100
) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.list_users(start_index=startIndex, count=count)


@scim_router.get("/Users/{scim_id}")
async def scim_get_user(request: Request, scim_id: str) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.get_user(scim_id)


@scim_router.post("/Users", status_code=201)
async def scim_create_user(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.create_user(body)


@scim_router.put("/Users/{scim_id}")
async def scim_replace_user(
    request: Request, scim_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    handler = await _get_scim_handler(request)
    return await handler.update_user(scim_id, body, partial=False)


@scim_router.patch("/Users/{scim_id}")
async def scim_patch_user(
    request: Request, scim_id: str, body: dict[str, Any]
) -> dict[str, Any]:
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
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not configured")

    from app.enterprise.compliance_v2 import generate_scim_token

    raw_token, prefix, token_hash = generate_scim_token(ctx.tenant_id)

    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(text("""
                INSERT INTO scim_tokens (id, tenant_id, token_hash, created_at)
                VALUES (:id, :tid, :hash, NOW())
            """), {"id": uuid.uuid4().hex, "tid": ctx.tenant_id, "hash": token_hash})
    except Exception as exc:
        raise HTTPException(500, f"Token provisioning failed: {exc}")

    return {
        "token": raw_token,
        "prefix": prefix,
        "note": "Store this token securely — it will not be shown again.",
    }
