"""Durable, NON-BLOCKING execution of one AI-Ops dataset run (MEM-25, P7-1).

History. A run was first an asyncio task on the API replica (lost on deploy),
then one Celery task that submitted each case as a goal and WAITED INLINE on
its event stream for up to 120 s. On a two-slot worker serving both the
``maintenance`` queue (the run) and a goal queue, the run held one slot while
its own case goals waited for the other — held by an unrelated goal parked on
HITL — so every case timed out and was cancelled (live EVAL-GOLDEN: 9/10
``execution_failed``).

Now a run is advanced by short steps (:func:`run_step`), the MEM-53 design of
eval-suite runs. A step never waits on a goal:

1. it claims the run under a lease (``claim_run``: compare-and-set on the run
   row, DB clock — at most one step advances a run at a time, on any replica);
2. polls the in-flight case goals (``get_goal`` / ``get_events`` read the
   durable goal row and event store): a terminal goal is scored and its case
   persisted; a goal past its deadline is cancelled and its case fails as timed
   out; anything else is checked again by a later step;
3. tops up the in-flight goals to the run's concurrency (submitting with a
   per-(run, case) dedup scope, so a step that died between submit and save gets
   the same goal back) and records each goal id and deadline;
4. finalizes the run when every case is done, else saves its progress
   (fenced on the lease) and returns ``running``.

On Celery (``app.scaling.tasks.run_ai_ops_dataset``) each step re-enqueues the
next one with a countdown; a beat sweeper re-dispatches runs whose step chain
died. Without Celery (single-process dev / tests) :func:`execute_dataset_run`
loops the same steps in-process with an ``await asyncio.sleep`` between them.

``store`` is the durable :class:`app.evals.ai_ops_store.AIOpsStore` (or any
object with the same async methods — the API's in-memory fallback).
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from typing import Any

from app.evals.ai_ops_runner import (
    _extract_output,
    aggregate,
    new_case,
    score_case,
    task_input_of,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Statuses of a run that has not produced its outcome yet.
ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_GOAL = {"complete", "failed", "cancelled", "rejected"}


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class RunConfig:
    """The Settings knobs a step needs (overridable in tests)."""

    def __init__(
        self,
        *,
        concurrency: int | None = None,
        poll_seconds: float | None = None,
        lease_seconds: float | None = None,
        case_timeout: float | None = None,
        settings: Any = None,
    ) -> None:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.concurrency = int(
            concurrency if concurrency is not None
            else getattr(settings, "ai_ops_run_concurrency", 4)
        )
        self.poll_seconds = float(
            poll_seconds if poll_seconds is not None
            else getattr(settings, "ai_ops_poll_seconds", 5.0)
        )
        self.lease_seconds = float(
            lease_seconds if lease_seconds is not None
            else getattr(settings, "ai_ops_lease_seconds", 300.0)
        )
        self.case_timeout = float(
            case_timeout if case_timeout is not None
            else getattr(settings, "ai_ops_case_timeout_seconds", 900.0)
        )


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


def _done_cases(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(c["index"]): c
        for c in result.get("cases") or []
        if isinstance(c, dict) and "index" in c
    }


def _tools_called(events: list[dict[str, Any]]) -> list[str]:
    from app.services.result_artifacts import unwrap_event

    flat = [unwrap_event(e) for e in events]
    return [
        str(e.get("tool_name") or e.get("tool") or "")
        for e in flat
        if e.get("type") == "tool_call_complete"
    ]


async def _poll_case(
    *,
    idx: int,
    info: dict[str, Any],
    task: dict[str, Any],
    goal_service: Any,
    tenant_ctx: Any,
    judge: dict[str, Any] | None,
    provider: Any,
) -> dict[str, Any] | None:
    """One status check of an in-flight case: its finished case, or None (keep waiting)."""
    from app.intelligence.eval_suite import cancel_unscored_goal

    goal_id = str(info["goal_id"])
    case = new_case(idx, task)
    case["goal_id"] = goal_id
    case["duration_seconds"] = round(max(0.0, time.time() - float(info.get("submitted", 0))), 3)
    try:
        goal = await goal_service.get_goal(goal_id, tenant_ctx)
        status = str(goal.get("status") or "")
    except Exception as exc:
        logger.warning("ai_ops_goal_status_read_failed", goal_id=goal_id, error=str(exc)[:200])
        goal, status = {}, ""
    if status not in _TERMINAL_GOAL:
        if time.time() < float(info["deadline"]):
            return None
        # Past its deadline: the case is not scored, and the goal must not keep
        # running (and spending) after the run moved on.
        await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)
        timeout = float(info["deadline"]) - float(info.get("submitted", info["deadline"]))
        case.update(
            goal_status="timeout",
            status="execution_failed",
            score=0.0,
            passed=False,
            error=f"goal did not finish within {timeout:.0f}s",
            tools_called=[],
        )
        return case
    try:
        events = list(await goal_service.get_events(goal_id, tenant_ctx))
    except Exception as exc:
        case.update(
            goal_status=status,
            status="execution_failed",
            score=0.0,
            passed=False,
            error=f"could not read the goal's events: {str(exc)[:300]}",
            tools_called=[],
        )
        return case
    case["goal_status"] = status
    case["tools_called"] = _tools_called(events)
    if status != "complete":
        reason = goal.get("failure_reason") or goal.get("terminal_reason") or f"goal {status}"
        case.update(status="execution_failed", score=0.0, passed=False, error=str(reason)[:500])
        return case
    # The goal's own answer, read exactly as GET /goals/{id} shows it (P7-2).
    return await score_case(
        case,
        task=task,
        actual=_extract_output(events),
        judge=judge,
        provider=provider,
        tenant_ctx=tenant_ctx,
        goal_id=goal_id,
    )


async def _submit_case(
    *,
    idx: int,
    task: dict[str, Any],
    result_id: str,
    agent_id: str | None,
    goal_service: Any,
    tenant_ctx: Any,
    cfg: RunConfig,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Submit a case's goal. ``(inflight_info, None)`` or ``(None, finished_case)``."""
    task_input = task_input_of(task)
    case = new_case(idx, task)
    if not task_input:
        case.update(status="invalid", score=0.0, passed=False, error="golden task has no input")
        return None, case
    submitted = time.time()
    try:
        sub = await goal_service.submit_goal(
            goal=task_input,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=str(task.get("agent_id") or agent_id or "") or None,
            # Traceability, and one dedup scope per (run, case): a step that died
            # after submitting gets the same in-flight goal back on retry, and two
            # identical golden tasks never collapse into one goal.
            execution_context={"ai_ops_result_id": result_id, "ai_ops_case_index": idx},
        )
        goal_id = str(sub["goal_id"])
    except Exception as exc:
        case.update(
            goal_id=None,
            goal_status="submit_failed",
            status="execution_failed",
            score=0.0,
            passed=False,
            error=str(exc)[:500],
            tools_called=[],
        )
        return None, case
    deadline = submitted + cfg.case_timeout
    return {"goal_id": goal_id, "submitted": submitted, "deadline": deadline}, None


async def run_step(
    *,
    store: Any,
    tenant_ctx: Any,
    result_id: str,
    goal_service: Any,
    provider: Any,
    cfg: RunConfig | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Advance a run by one non-blocking step. ``status`` stays ``running`` until done.

    Other statuses: ``completed`` / ``failed`` (this step finished the run),
    ``missing``, ``busy`` (another step holds the lease — it continues the run),
    or the status of a run that is already over (a redelivered message).
    """
    cfg = cfg or RunConfig()
    owner = owner or f"aiops-{uuid.uuid4().hex[:12]}"
    tenant_id = tenant_ctx.tenant_id
    result = await store.claim_run(tenant_id, result_id, owner, cfg.lease_seconds)
    if result is None:
        current = await store.get_eval_result(tenant_id, result_id)
        if current is None:
            logger.warning("ai_ops_run_missing", result_id=result_id)
            return {"status": "missing", "result_id": result_id}
        status = str(current.get("status"))
        return {"status": "busy" if status in ACTIVE_STATUSES else status, "result_id": result_id}

    async def _save(*, release: bool = False) -> bool:
        result["heartbeat_at"] = _now()
        saved: bool = await store.save_run(
            tenant_id, result_id, owner, result, cfg.lease_seconds, release=release
        )
        if not saved:
            logger.warning("ai_ops_run_lease_lost", result_id=result_id, owner=owner)
        return saved

    done = _done_cases(result)
    inflight: dict[str, dict[str, Any]] = dict(result.get("inflight") or {})
    try:
        pinned = result.get("dataset_version")
        if pinned is not None:
            # The immutable version this run was started on (P7-3).
            dataset = await store.get_dataset_version(
                tenant_id, result["dataset_id"], int(pinned)
            )
        else:  # a result created before dataset versioning
            dataset = await store.get_dataset(tenant_id, result["dataset_id"])
        if not dataset or not dataset.get("golden_tasks"):
            raise LookupError("dataset version no longer exists or has no golden tasks")
        judge = await _judge_for(store, tenant_id, result)
        if judge is not None and provider is None:
            raise RuntimeError("judge configured but no LLM provider is available")
    except Exception as exc:
        logger.warning("ai_ops_eval_run_failed", result_id=result_id, error=str(exc)[:300])
        result.update(status="failed", passed=False, error=str(exc)[:2000], finished_at=_now())
        result.pop("inflight", None)
        await _save(release=True)
        return {"status": "failed", "result_id": result_id}

    tasks: list[dict[str, Any]] = list(dataset["golden_tasks"])
    if result.get("status") == "queued":
        result.update(status="running", started_at=result.get("started_at") or _now())
        result["resumed_cases"] = len(done)

    def _finish(case: dict[str, Any]) -> None:
        done[int(case["index"])] = case
        result["cases"] = [done[i] for i in sorted(done)]
        result["completed_cases"] = len(done)

    # 1. Poll the in-flight goals.
    for key, info in list(inflight.items()):
        idx = int(key)
        if idx in done or idx >= len(tasks):
            inflight.pop(key, None)
            continue
        finished = await _poll_case(
            idx=idx, info=info, task=tasks[idx], goal_service=goal_service,
            tenant_ctx=tenant_ctx, judge=judge, provider=provider,
        )
        if finished is not None:
            inflight.pop(key, None)
            _finish(finished)
    result["inflight"] = inflight
    if not await _save():
        return {"status": "busy", "result_id": result_id}

    # 2. Top up the in-flight goals to the run's concurrency.
    pending = [i for i in range(len(tasks)) if i not in done and str(i) not in inflight]
    for idx in pending:
        if len(inflight) >= max(1, cfg.concurrency):
            break
        info, finished = await _submit_case(
            idx=idx, task=tasks[idx], result_id=result_id, agent_id=result.get("agent_id"),
            goal_service=goal_service, tenant_ctx=tenant_ctx, cfg=cfg,
        )
        if info is not None:
            inflight[str(idx)] = info
        elif finished is not None:
            _finish(finished)
        result["inflight"] = inflight
        # Record each goal id as soon as it exists (a crash re-submits with the
        # same dedup scope and gets this goal back).
        if not await _save():
            return {"status": "busy", "result_id": result_id}

    # 3. Finalize when every case is done; otherwise hand the run to the next
    # step (the lease only keeps two steps off the run at the same time).
    if len(done) < len(tasks):
        if not await _save(release=True):
            return {"status": "busy", "result_id": result_id}
        return {"status": "running", "result_id": result_id}
    result.update(aggregate([done[i] for i in sorted(done)], judge))
    result.pop("inflight", None)
    result["finished_at"] = _now()
    if not await _save(release=True):
        return {"status": "busy", "result_id": result_id}
    await _record_baseline(store, tenant_id, result)
    logger.info("ai_ops_eval_run_completed", result_id=result_id)
    return {"status": "completed", "result_id": result_id}


async def execute_dataset_run(
    *,
    store: Any,
    tenant_ctx: Any,
    result_id: str,
    goal_service: Any,
    provider: Any,
    cfg: RunConfig | None = None,
    max_steps: int = 1_000_000,
) -> dict[str, Any] | None:
    """In-process execution (no Celery): repeat :func:`run_step`, sleeping between polls.

    Returns the final stored result, or None when it does not exist.
    """
    cfg = cfg or RunConfig()
    owner = f"inproc-{uuid.uuid4().hex[:12]}"
    for _ in range(max_steps):
        out = await run_step(
            store=store, tenant_ctx=tenant_ctx, result_id=result_id,
            goal_service=goal_service, provider=provider, cfg=cfg, owner=owner,
        )
        if out["status"] != "running":
            break
        await asyncio.sleep(cfg.poll_seconds)
    found: dict[str, Any] | None = await store.get_eval_result(tenant_ctx.tenant_id, result_id)
    return found


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


class MemoryRunLease:
    """``claim_run`` / ``save_run`` over a dict of results (in-process fallback, tests).

    Single-process only: the lease lives in the payload exactly as the durable
    store keeps it, so both behave the same.
    """

    @staticmethod
    def claim(
        row: dict[str, Any] | None, owner: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        if row is None or row.get("status") not in ACTIVE_STATUSES:
            return None
        now = time.time()
        if float(row.get("lease_until") or 0) >= now and row.get("lease_owner") != owner:
            return None
        row["lease_owner"] = owner
        row["lease_until"] = now + lease_seconds
        return row

    @staticmethod
    def fenced(row: dict[str, Any] | None, owner: str) -> bool:
        return row is not None and row.get("lease_owner") == owner

    @staticmethod
    def stamp(payload: dict[str, Any], owner: str, lease_seconds: float, release: bool) -> None:
        payload["lease_owner"] = owner
        payload["lease_until"] = 0.0 if release else time.time() + lease_seconds
