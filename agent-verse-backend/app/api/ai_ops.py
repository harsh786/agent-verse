"""AI Ops Center API - evals, drift, regression, alerts."""

from __future__ import annotations

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


# In-memory stores
_datasets: dict[str, dict] = {}
_eval_results: dict[str, list] = {}  # tenant → results
_drift_alerts: dict[str, list] = {}  # tenant → alerts
_judges: dict[str, dict] = {}
_baselines: dict[str, dict] = {}  # tenant → metric baselines


class CreateDatasetRequest(BaseModel):
    name: str
    description: str = ""
    golden_tasks: list[dict[str, Any]] = Field(default_factory=list)


class RunEvalRequest(BaseModel):
    dataset_id: str = ""
    goal_id: str | None = None
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
    _datasets[f"{tenant.tenant_id}:{dataset_id}"] = dataset
    return {"dataset_id": dataset_id, "task_count": len(body.golden_tasks), "status": "created"}


@router.get("/datasets")
async def list_eval_datasets(request: Request) -> dict[str, Any]:
    """List evaluation datasets for the tenant."""
    tenant = _require_tenant(request)
    datasets = [v for k, v in _datasets.items() if k.startswith(f"{tenant.tenant_id}:")]
    return {"datasets": datasets, "total": len(datasets)}


@router.post("/datasets/{dataset_id}/run")
async def run_eval(request: Request, dataset_id: str, body: RunEvalRequest) -> dict[str, Any]:
    """Run an evaluation suite against a dataset."""
    tenant = _require_tenant(request)
    dataset = _datasets.get(f"{tenant.tenant_id}:{dataset_id}")
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    now = datetime.datetime.now(datetime.UTC).isoformat()
    result_id = str(uuid.uuid4())

    # Evaluate using LLM judge if available
    provider = getattr(request.app.state, "_app_provider", None)

    scores: dict[str, float] = {}
    failed_tasks = []

    for task in dataset["golden_tasks"][:10]:  # Limit to 10 for performance
        expected = str(task.get("expected_output", ""))
        # Use expected as default when actual_output is absent
        actual = str(task.get("actual_output", task.get("expected_output", "")))

        if provider:
            try:
                from app.providers.base import CompletionRequest, Message

                judge_prompt = (
                    "Rate this output on a scale of 0 to 1 for: "
                    "accuracy, relevance, completeness.\n"
                    f"Expected: {expected[:200]}\n"
                    f"Actual: {actual[:200]}\n"
                    f"Return JSON only: "
                    '{"accuracy": 0.0, "relevance": 0.0, "completeness": 0.0}'
                )
                resp = await provider.complete(
                    CompletionRequest(
                        messages=[Message(role="user", content=judge_prompt)],
                        model="",
                        max_tokens=100,
                    )
                )
                import json

                task_scores = json.loads(resp.content.strip())
                for dim, score in task_scores.items():
                    scores[dim] = scores.get(dim, 0) + float(score)
            except Exception:
                pass

        # Simple deterministic scoring fallback
        overlap = len(set(expected.lower().split()) & set(actual.lower().split()))
        total = max(len(set(expected.lower().split())), 1)
        similarity = overlap / total
        scores["lexical_similarity"] = scores.get("lexical_similarity", 0) + similarity

        if similarity < 0.3:
            failed_tasks.append(
                {
                    "task": str(task.get("input", ""))[:100],
                    "expected": expected[:100],
                    "actual": actual[:100],
                    "score": similarity,
                }
            )

    n = max(len(dataset["golden_tasks"]), 1)
    normalized_scores = {k: round(v / n, 3) for k, v in scores.items()}
    avg_score = sum(normalized_scores.values()) / max(len(normalized_scores), 1)

    result = {
        "result_id": result_id,
        "dataset_id": dataset_id,
        "tenant_id": tenant.tenant_id,
        "goal_id": body.goal_id,
        "scores": normalized_scores,
        "avg_score": round(avg_score, 3),
        "passed": avg_score >= 0.7,
        "failed_task_count": len(failed_tasks),
        "failed_tasks": failed_tasks[:5],
        "judge_model": body.judge_model,
        "created_at": now,
    }
    _eval_results.setdefault(tenant.tenant_id, []).append(result)

    # Check regression
    if _baselines.get(tenant.tenant_id, {}).get(f"eval_{dataset_id}"):
        baseline = _baselines[tenant.tenant_id][f"eval_{dataset_id}"]
        if avg_score < baseline - 0.05:  # 5% regression threshold
            alert = {
                "alert_id": str(uuid.uuid4()),
                "tenant_id": tenant.tenant_id,
                "drift_type": "model_output",
                "severity": "warning",
                "metric_name": f"eval_{dataset_id}",
                "baseline_value": baseline,
                "current_value": avg_score,
                "drift_score": baseline - avg_score,
                "message": f"Eval score regressed: {baseline:.2f} → {avg_score:.2f}",
                "created_at": now,
            }
            _drift_alerts.setdefault(tenant.tenant_id, []).append(alert)

    # Auto-set baseline on first run
    if not _baselines.get(tenant.tenant_id, {}).get(f"eval_{dataset_id}"):
        _baselines.setdefault(tenant.tenant_id, {})[f"eval_{dataset_id}"] = avg_score

    return result


@router.get("/eval-results")
async def list_eval_results(request: Request) -> dict[str, Any]:
    """List evaluation results for the tenant."""
    tenant = _require_tenant(request)
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
    _judges[f"{tenant.tenant_id}:{judge_id}"] = judge
    return {"judge_id": judge_id, "status": "created"}


@router.post("/baselines")
async def set_metric_baseline(request: Request, body: SetBaselineRequest) -> dict[str, Any]:
    """Set a baseline value for drift detection."""
    tenant = _require_tenant(request)
    _baselines.setdefault(tenant.tenant_id, {})[body.metric_name] = body.value
    return {"metric_name": body.metric_name, "baseline": body.value, "status": "set"}


@router.post("/drift")
async def compute_drift(request: Request, body: ComputeDriftRequest) -> dict[str, Any]:
    """Compute drift score relative to baseline."""
    tenant = _require_tenant(request)
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
    alerts = list(reversed(_drift_alerts.get(tenant.tenant_id, [])))
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    return {"alerts": alerts[:limit], "total": len(alerts)}


@router.get("/regression-status")
async def get_regression_status(request: Request) -> dict[str, Any]:
    """Get overall regression status across all metrics."""
    tenant = _require_tenant(request)
    alerts = _drift_alerts.get(tenant.tenant_id, [])
    critical = sum(1 for a in alerts if a.get("severity") == "critical")
    warnings = sum(1 for a in alerts if a.get("severity") == "warning")

    return {
        "status": "critical" if critical > 0 else "warning" if warnings > 0 else "ok",
        "critical_alerts": critical,
        "warning_alerts": warnings,
        "total_alerts": len(alerts),
        "baselines_tracked": len(_baselines.get(tenant.tenant_id, {})),
    }
