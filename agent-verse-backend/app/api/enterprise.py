"""Enterprise API — compliance, simulation, red-team, marketplace, intelligence."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/enterprise", tags=["enterprise"])
marketplace_router = APIRouter(prefix="/marketplace", tags=["marketplace"])
intelligence_router = APIRouter(prefix="/intelligence", tags=["intelligence"])

# --- helpers ---

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _compliance(request: Request) -> Any:
    return request.app.state.compliance_controller


def _simulation(request: Request) -> Any:
    return request.app.state.simulation_runner


def _red_team(request: Request) -> Any:
    return request.app.state.red_team_runner


def _marketplace(request: Request) -> Any:
    return request.app.state.marketplace


def _self_optimizer(request: Request) -> Any:
    return request.app.state.self_optimizer


# --- Compliance ---

@router.get("/compliance/export")
async def request_data_export(request: Request) -> dict[str, Any]:
    ctx = _require_tenant(request)
    req = _compliance(request).request_data_export(tenant_ctx=ctx)
    return {
        "request_id": req.request_id,
        "status": req.status,
        "download_url": req.download_url,
    }


@router.get("/compliance/export/{request_id}/download")
async def download_export(request: Request, request_id: str) -> Response:
    """Download the GDPR data export as a JSON file."""
    ctx = _require_tenant(request)
    req = _compliance(request).get_export_status(request_id=request_id, tenant_ctx=ctx)
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
    req = _compliance(request).get_export_status(request_id=request_id, tenant_ctx=ctx)
    if req is None:
        raise HTTPException(status_code=404, detail="Export request not found")
    return {"request_id": req.request_id, "status": req.status, "payload": req.payload}


@router.post("/compliance/delete", status_code=202)
async def request_data_deletion(request: Request) -> dict[str, Any]:
    ctx = _require_tenant(request)
    return _compliance(request).request_data_deletion(tenant_ctx=ctx)


@router.get("/compliance/residency")
async def get_data_residency(request: Request) -> dict[str, Any]:
    ctx = _require_tenant(request)
    return _compliance(request).get_data_residency(tenant_ctx=ctx)


# --- Simulation ---

class SimulationRequest(BaseModel):
    goal: str
    mock_tools: dict[str, Any] = {}


@router.post("/simulation", status_code=201)
async def run_simulation(request: Request, body: SimulationRequest) -> dict[str, Any]:
    ctx = _require_tenant(request)
    run = _simulation(request).start(
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


@marketplace_router.get("/browse")
async def browse_marketplace(
    request: Request, q: str = "", domain: str = ""
) -> list[dict[str, Any]]:
    ctx = _require_tenant(request)
    return _marketplace(request).browse(query=q, domain=domain, tenant_ctx=ctx)


@marketplace_router.get("/{template_id}/versions")
async def get_template_versions(request: Request, template_id: str) -> list[dict[str, Any]]:
    """Return version history for a template."""
    _require_tenant(request)
    t = _marketplace(request).get_template(template_id=template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    # Return current version (history would come from DB in production)
    return [
        {
            "version": t.get("version", "1.0.0"),
            "template_id": template_id,
            "published_at": t.get("published_at", ""),
            "is_current": True,
        }
    ]


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
    import uuid
    runner = getattr(request.app.state, "eval_suite_runner", None)
    if runner is None:
        raise HTTPException(503, "Eval suite runner not configured")
    suite_id = body.suite_id or uuid.uuid4().hex
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
