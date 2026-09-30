"""Durable execution of one AI-Ops dataset run (MEM-25).

A run used to be an asyncio task on the API replica that received the request:
a deploy or crash lost it (reads eventually reported it ``abandoned``). The run
body now lives here and is executed by a Celery task
(``app.scaling.tasks.run_ai_ops_dataset``, acks_late + reject_on_worker_lost),
so a worker that dies mid-run has its message redelivered. Every finished case
is persisted into the result as it completes; a redelivered run reuses those
cases and executes only the remaining ones.

``store`` is the durable :class:`app.evals.ai_ops_store.AIOpsStore` (or any
object with the same async methods — the API's in-memory fallback for
single-process dev and tests).
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

from app.evals.ai_ops_runner import CASE_CONCURRENCY, run_dataset
from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Statuses of a run that has not produced its outcome yet.
ACTIVE_STATUSES = frozenset({"queued", "running"})


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


async def _judge_for(store: Any, tenant_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    summary = result.get("judge")
    if not summary:
        return None
    judge_id = summary.get("judge_id")
    if judge_id:
        judge: dict[str, Any] | None = await store.get_judge(tenant_id, judge_id)
        if judge is None:
            raise LookupError(f"judge {judge_id} no longer exists")
        return judge
    return {"judge_id": None, "name": "ad-hoc", "model": summary.get("model") or ""}


async def execute_dataset_run(
    *,
    store: Any,
    tenant_ctx: Any,
    result_id: str,
    goal_service: Any,
    provider: Any,
    concurrency: int = CASE_CONCURRENCY,
) -> dict[str, Any] | None:
    """Run (or resume) the dataset run ``result_id`` and persist its outcome.

    Returns the final result, or None when the result does not exist. A run
    that already finished (a redelivered message) is returned untouched.
    Ordinary errors mark the run ``failed``; a worker dying mid-run
    (``BaseException``) leaves it ``running`` with its finished cases saved,
    for the redelivered task to resume.
    """
    tenant_id = tenant_ctx.tenant_id
    result = await store.get_eval_result(tenant_id, result_id)
    if result is None:
        logger.warning("ai_ops_run_missing", result_id=result_id)
        return None
    if result.get("status") not in ACTIVE_STATUSES:
        return dict(result)

    lock = asyncio.Lock()
    done: dict[int, dict[str, Any]] = {
        int(c["index"]): c
        for c in result.get("cases") or []
        if isinstance(c, dict) and "index" in c
    }

    async def _save() -> None:
        result["heartbeat_at"] = _now()
        await store.update_eval_result(tenant_id=tenant_id, result_id=result_id, payload=result)

    async def _on_case(case: dict[str, Any]) -> None:
        async with lock:
            done[int(case["index"])] = case
            result["cases"] = [done[i] for i in sorted(done)]
            result["completed_cases"] = len(done)
            await _save()

    try:
        dataset = await store.get_dataset(tenant_id, result["dataset_id"])
        if not dataset or not dataset.get("golden_tasks"):
            raise LookupError("dataset no longer exists or has no golden tasks")
        judge = await _judge_for(store, tenant_id, result)
        if judge is not None and provider is None:
            raise RuntimeError("judge configured but no LLM provider is available")
        result.update(status="running", started_at=result.get("started_at") or _now())
        result["resumed_cases"] = len(done)
        await _save()
        outcome = await run_dataset(
            dataset=dataset,
            goal_service=goal_service,
            tenant_ctx=tenant_ctx,
            agent_id=result.get("agent_id"),
            judge=judge,
            provider=provider,
            concurrency=concurrency,
            done_cases=done,
            on_case=_on_case,
        )
    except Exception as exc:
        logger.warning("ai_ops_eval_run_failed", result_id=result_id, error=str(exc)[:300])
        result.update(status="failed", passed=False, error=str(exc)[:2000], finished_at=_now())
        try:
            await _save()
        except Exception as store_exc:
            logger.error("ai_ops_eval_run_status_lost", result_id=result_id, error=str(store_exc))
        return result

    result.update(outcome)
    result["finished_at"] = _now()
    await _save()
    await _record_baseline(store, tenant_id, result)
    return result


async def _record_baseline(store: Any, tenant_id: str, result: dict[str, Any]) -> None:
    """Regression alert + first-run baseline, only for a run that completed."""
    avg_score = float(result["avg_score"])
    metric = f"eval_{result['dataset_id']}"
    try:
        # `is None`, not truthiness: a legitimate baseline of 0.0 is falsy.
        baseline = await store.get_baseline(tenant_id, metric)
        if baseline is not None and avg_score < baseline - 0.05:  # 5% regression
            await store.add_alert(
                tenant_id=tenant_id,
                alert={
                    "alert_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "drift_type": "model_output",
                    "severity": "warning",
                    "metric_name": metric,
                    "baseline_value": baseline,
                    "current_value": avg_score,
                    "drift_score": baseline - avg_score,
                    "message": f"Eval score regressed: {baseline:.2f} → {avg_score:.2f}",
                    "created_at": _now(),
                },
            )
        if baseline is None:
            # First run only; never clobber an existing baseline.
            await store.set_baseline_if_absent(
                tenant_id=tenant_id, metric_name=metric, value=avg_score
            )
    except Exception as exc:
        logger.warning("ai_ops_eval_baseline_update_failed", metric=metric, error=str(exc)[:300])
