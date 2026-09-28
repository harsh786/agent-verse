"""AI Ops Center API - evals, drift, regression, alerts."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/ai-ops", tags=["ai-ops"])


def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


# Legacy in-process fallback, used only when no durable store is wired (tests and
# single-process dev). Production swaps in `app.state.ai_ops_store` during
# lifespan — see app/evals/ai_ops_store.py. Before that store existed these dicts
# WERE production: datasets, results, judges, baselines and drift alerts were all
# lost on restart and invisible to every other replica, so a baseline set on one
# pod meant drift was computed against "no baseline" on the next request.
_datasets: dict[str, dict] = {}
_eval_results: dict[str, list] = {}  # tenant → results
_drift_alerts: dict[str, list] = {}  # tenant → alerts
_judges: dict[str, dict] = {}
_baselines: dict[str, dict] = {}  # tenant → metric baselines


def _store(request: Request) -> Any:
    """Durable AI-Ops store (DB-backed in prod). None → in-memory fallback."""
    return getattr(request.app.state, "ai_ops_store", None)


class CreateDatasetRequest(BaseModel):
    name: str
    description: str = ""
    golden_tasks: list[dict[str, Any]] = Field(default_factory=list)


class RunEvalRequest(BaseModel):
    dataset_id: str = ""
    goal_id: str | None = None
    # Agent that executes every case (a golden task's own ``agent_id`` wins);
    # omitted → the platform's goal auto-routing picks one.
    agent_id: str | None = None
    # A judge created via POST /ai-ops/judges; scores every case LLM-as-judge.
    judge_id: str | None = None
    # Ad-hoc judge model (default dimensions) when no ``judge_id`` is given.
    judge_model: str = ""


class CreateJudgeRequest(BaseModel):
    name: str
    provider: str
    model: str
    evaluation_dimensions: list[str] = Field(default_factory=list)
    prompt_template: str = ""


class SetBaselineRequest(BaseModel):
    metric_name: str
    value: float


class ComputeDriftRequest(BaseModel):
    metric_name: str
    current_value: float


@router.post("/datasets")
async def create_eval_dataset(request: Request, body: CreateDatasetRequest) -> dict[str, Any]:
    """Create an evaluation dataset with golden tasks."""
    tenant = _require_tenant(request)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    dataset_id = str(uuid.uuid4())

    dataset = {
        "dataset_id": dataset_id,
        "tenant_id": tenant.tenant_id,
        "name": body.name,
        "description": body.description,
        "golden_tasks": body.golden_tasks,
        "task_count": len(body.golden_tasks),
        "created_at": now,
        "version": 1,
    }
    store = _store(request)
    if store is not None:
        await store.create_dataset(
            tenant_id=tenant.tenant_id,
            dataset_id=dataset_id,
            name=body.name,
            description=body.description,
            golden_tasks=body.golden_tasks,
        )
    else:
        _datasets[f"{tenant.tenant_id}:{dataset_id}"] = dataset
    return {"dataset_id": dataset_id, "task_count": len(body.golden_tasks), "status": "created"}


@router.get("/datasets")
async def list_eval_datasets(request: Request) -> dict[str, Any]:
    """List evaluation datasets for the tenant."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        datasets = await store.list_datasets(tenant.tenant_id)
        return {"datasets": datasets, "total": len(datasets)}
    datasets = [v for k, v in _datasets.items() if k.startswith(f"{tenant.tenant_id}:")]
    return {"datasets": datasets, "total": len(datasets)}


#: A ``running`` result older than this is reported ``abandoned``: the replica
#: executing it died (the background task is per-process), so it never claims a
#: result it does not have. Mirrors eval_suite_store.STALE_RUN_AFTER.
STALE_RUN_AFTER = datetime.timedelta(hours=1)


async def _load_judge(request: Request, tenant_id: str, judge_id: str) -> dict[str, Any] | None:
    store = _store(request)
    if store is not None:
        judge: dict[str, Any] | None = await store.get_judge(tenant_id, judge_id)
        return judge
    return _judges.get(f"{tenant_id}:{judge_id}")


async def _save_result(request: Request, tenant_id: str, result: dict[str, Any]) -> None:
    store = _store(request)
    if store is not None:
        await store.update_eval_result(
            tenant_id=tenant_id, result_id=result["result_id"], payload=result
        )
    # In-memory fallback: the dict in _eval_results is mutated in place.


async def _finish_eval_run(
    request: Request,
    *,
    tenant: Any,
    dataset: dict[str, Any],
    result: dict[str, Any],
    goal_service: Any,
    judge: dict[str, Any] | None,
    provider: Any,
    agent_id: str | None,
) -> None:
    """Background body of a dataset run: execute every case, score, persist."""
    from app.evals.ai_ops_runner import run_dataset
    from app.observability.logging import get_logger

    log = get_logger(__name__)
    tenant_id = tenant.tenant_id
    store = _store(request)
    try:
        outcome = await run_dataset(
            dataset=dataset,
            goal_service=goal_service,
            tenant_ctx=tenant,
            agent_id=agent_id,
            judge=judge,
            provider=provider,
        )
        result.update(outcome)
        result["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        await _save_result(request, tenant_id, result)
    except Exception as exc:
        log.warning("ai_ops_eval_run_failed", result_id=result["result_id"], error=str(exc)[:300])
        result.update(status="failed", passed=False, error=str(exc)[:2000])
        try:
            await _save_result(request, tenant_id, result)
        except Exception as store_exc:
            log.error(
                "ai_ops_eval_run_status_lost", result_id=result["result_id"], error=str(store_exc)
            )
        return

    # Regression / baseline bookkeeping only for a run that actually completed.
    avg_score = float(result["avg_score"])
    metric = f"eval_{result['dataset_id']}"
    now = datetime.datetime.now(datetime.UTC).isoformat()
    # `is None`, not truthiness: a legitimate baseline of 0.0 is falsy, which
    # previously made every run look like a first run.
    try:
        if store is not None:
            baseline = await store.get_baseline(tenant_id, metric)
        else:
            baseline = _baselines.get(tenant_id, {}).get(metric)

        if baseline is not None and avg_score < baseline - 0.05:  # 5% regression
            alert = {
                "alert_id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "drift_type": "model_output",
                "severity": "warning",
                "metric_name": metric,
                "baseline_value": baseline,
                "current_value": avg_score,
                "drift_score": baseline - avg_score,
                "message": f"Eval score regressed: {baseline:.2f} → {avg_score:.2f}",
                "created_at": now,
            }
            if store is not None:
                await store.add_alert(tenant_id=tenant_id, alert=alert)
            else:
                _drift_alerts.setdefault(tenant_id, []).append(alert)

        # Auto-set baseline on first run only; never clobber an existing one.
        if baseline is None:
            if store is not None:
                await store.set_baseline_if_absent(
                    tenant_id=tenant_id, metric_name=metric, value=avg_score
                )
            else:
                _baselines.setdefault(tenant_id, {})[metric] = avg_score
    except Exception as exc:
        log.warning("ai_ops_eval_baseline_update_failed", metric=metric, error=str(exc)[:300])


@router.post("/datasets/{dataset_id}/run", status_code=202)
async def run_eval(request: Request, dataset_id: str, body: RunEvalRequest) -> dict[str, Any]:
    """Start a run of a dataset's golden tasks against the live agent.

    Every case is executed as a real goal (tenant-scoped, through GoalService)
    and scored against its REAL output — by the configured LLM judge when
    ``judge_id`` (or an ad-hoc ``judge_model``) is given, else by lexical
    overlap with ``expected_output``. Returns 202 with a ``result_id``; poll
    ``GET /ai-ops/eval-results/{result_id}``.

    This used to score each case's ``expected_output`` against itself (1.0 →
    every run passed without executing anything) and ignored created judges.
    """
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        dataset = await store.get_dataset(tenant.tenant_id, dataset_id)
    else:
        dataset = _datasets.get(f"{tenant.tenant_id}:{dataset_id}")
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if not dataset.get("golden_tasks"):
        raise HTTPException(422, "Dataset has no golden tasks to evaluate")

    goal_service = getattr(request.app.state, "goal_service", None)
    if goal_service is None:
        # No agent execution path → nothing can be evaluated; never fake a score.
        raise HTTPException(503, "Agent execution unavailable; eval not run")

    judge: dict[str, Any] | None = None
    if body.judge_id:
        judge = await _load_judge(request, tenant.tenant_id, body.judge_id)
        if judge is None:
            raise HTTPException(404, "Judge not found")
    elif body.judge_model:
        judge = {"judge_id": None, "name": "ad-hoc", "model": body.judge_model}
    provider = getattr(request.app.state, "_app_provider", None)
    if judge is not None and provider is None:
        raise HTTPException(503, "Judge configured but no LLM provider is available")

    now = datetime.datetime.now(datetime.UTC).isoformat()
    result_id = str(uuid.uuid4())
    result: dict[str, Any] = {
        "result_id": result_id,
        "dataset_id": dataset_id,
        "tenant_id": tenant.tenant_id,
        "goal_id": body.goal_id,
        "agent_id": body.agent_id,
        "status": "running",
        "passed": False,
        "total_cases": len(dataset["golden_tasks"]),
        "judge_model": (judge or {}).get("model", ""),
        "judge": (
            {
                "judge_id": judge.get("judge_id"),
                "name": judge.get("name"),
                "provider": judge.get("provider"),
                "model": judge.get("model"),
                "evaluation_dimensions": judge.get("evaluation_dimensions"),
            }
            if judge is not None
            else None
        ),
        "created_at": now,
    }
    if store is not None:
        await store.add_eval_result(
            tenant_id=tenant.tenant_id, result_id=result_id, dataset_id=dataset_id, payload=result
        )
    else:
        _eval_results.setdefault(tenant.tenant_id, []).append(result)

    running: set[asyncio.Task[None]] = request.app.state.__dict__.setdefault(
        "_ai_ops_run_tasks", set()
    )
    task = asyncio.create_task(
        _finish_eval_run(
            request,
            tenant=tenant,
            dataset=dataset,
            result=result,
            goal_service=goal_service,
            judge=judge,
            provider=provider,
            agent_id=body.agent_id,
        )
    )
    running.add(task)  # strong reference until it finishes
    task.add_done_callback(running.discard)
    return {
        "result_id": result_id,
        "dataset_id": dataset_id,
        "status": "running",
        "total": len(dataset["golden_tasks"]),
    }


def _with_staleness(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "running":
        return result
    try:
        created = datetime.datetime.fromisoformat(str(result.get("created_at")))
    except ValueError:
        return result
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.UTC)
    if datetime.datetime.now(datetime.UTC) - created > STALE_RUN_AFTER:
        return {**result, "status": "abandoned", "passed": False}
    return result


@router.get("/eval-results/{result_id}")
async def get_eval_result(request: Request, result_id: str) -> dict[str, Any]:
    """One eval result (poll target for a 202 dataset run)."""
    tenant = _require_tenant(request)
    store = _store(request)
    found: dict[str, Any] | None
    if store is not None:
        found = await store.get_eval_result(tenant.tenant_id, result_id)
    else:
        found = next(
            (r for r in _eval_results.get(tenant.tenant_id, []) if r.get("result_id") == result_id),
            None,
        )
    if found is None:
        raise HTTPException(404, "Eval result not found")
    return _with_staleness(found)


@router.get("/eval-results")
async def list_eval_results(request: Request) -> dict[str, Any]:
    """List evaluation results for the tenant."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        results = await store.list_eval_results(tenant.tenant_id, limit=50)
        return {"results": results, "total": await store.count_eval_results(tenant.tenant_id)}
    results = list(reversed(_eval_results.get(tenant.tenant_id, [])))
    return {"results": results[:50], "total": len(results)}


@router.post("/judges")
async def create_llm_judge(request: Request, body: CreateJudgeRequest) -> dict[str, Any]:
    """Create an LLM-as-judge configuration."""
    tenant = _require_tenant(request)
    judge_id = str(uuid.uuid4())

    judge = {
        "judge_id": judge_id,
        "tenant_id": tenant.tenant_id,
        "name": body.name,
        "provider": body.provider,
        "model": body.model,
        "evaluation_dimensions": body.evaluation_dimensions
        or ["accuracy", "relevance", "completeness"],
        "prompt_template": body.prompt_template,
        "calibrated": False,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    store = _store(request)
    if store is not None:
        await store.create_judge(
            tenant_id=tenant.tenant_id, judge_id=judge_id, payload=judge
        )
    else:
        _judges[f"{tenant.tenant_id}:{judge_id}"] = judge
    return {"judge_id": judge_id, "status": "created"}


@router.post("/baselines")
async def set_metric_baseline(request: Request, body: SetBaselineRequest) -> dict[str, Any]:
    """Set a baseline value for drift detection."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        await store.set_baseline(
            tenant_id=tenant.tenant_id, metric_name=body.metric_name, value=body.value
        )
    else:
        _baselines.setdefault(tenant.tenant_id, {})[body.metric_name] = body.value
    return {"metric_name": body.metric_name, "baseline": body.value, "status": "set"}


@router.post("/drift")
async def compute_drift(request: Request, body: ComputeDriftRequest) -> dict[str, Any]:
    """Compute drift score relative to baseline."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        baseline = await store.get_baseline(tenant.tenant_id, body.metric_name)
    else:
        baseline = _baselines.get(tenant.tenant_id, {}).get(body.metric_name)

    if baseline is None:
        return {"status": "no_baseline", "metric_name": body.metric_name, "drift_score": 0.0}

    absolute_drift = abs(body.current_value - baseline)
    # Normalize by baseline so the thresholds are relative (percentage-based),
    # which works for both normalized (0-1) metrics and raw metrics (e.g. latency_ms).
    drift_score = absolute_drift / max(abs(baseline), 1e-9)
    severity = "info" if drift_score < 0.1 else "warning" if drift_score < 0.3 else "critical"

    now = datetime.datetime.now(datetime.UTC).isoformat()
    alert = {
        "alert_id": str(uuid.uuid4()),
        "tenant_id": tenant.tenant_id,
        "drift_type": "model_output",
        "severity": severity,
        "metric_name": body.metric_name,
        "baseline_value": baseline,
        "current_value": body.current_value,
        "drift_score": round(drift_score, 4),
        "message": (
            f"Drift detected on {body.metric_name}: {baseline:.3f} → {body.current_value:.3f}"
        ),
        "created_at": now,
    }
    if severity != "info":
        if store is not None:
            await store.add_alert(tenant_id=tenant.tenant_id, alert=alert)
        else:
            _drift_alerts.setdefault(tenant.tenant_id, []).append(alert)

    return alert


@router.get("/alerts")
async def list_drift_alerts(
    request: Request,
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> dict[str, Any]:
    """List drift and regression alerts for the tenant."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        alerts = await store.list_alerts(tenant.tenant_id, severity, limit)
        return {"alerts": alerts, "total": len(alerts)}
    alerts = list(reversed(_drift_alerts.get(tenant.tenant_id, [])))
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    return {"alerts": alerts[:limit], "total": len(alerts)}


@router.get("/regression-status")
async def get_regression_status(request: Request) -> dict[str, Any]:
    """Get overall regression status across all metrics."""
    tenant = _require_tenant(request)
    store = _store(request)
    if store is not None:
        counts = await store.alert_severity_counts(tenant.tenant_id)
        critical = counts.get("critical", 0)
        warnings = counts.get("warning", 0)
        total = sum(counts.values())
        tracked = await store.count_baselines(tenant.tenant_id)
    else:
        alerts = _drift_alerts.get(tenant.tenant_id, [])
        critical = sum(1 for a in alerts if a.get("severity") == "critical")
        warnings = sum(1 for a in alerts if a.get("severity") == "warning")
        total = len(alerts)
        tracked = len(_baselines.get(tenant.tenant_id, {}))

    return {
        "status": "critical" if critical > 0 else "warning" if warnings > 0 else "ok",
        "critical_alerts": critical,
        "warning_alerts": warnings,
        "total_alerts": total,
        "baselines_tracked": tracked,
    }
