"""Durable execution of eval-suite runs (MEM-53).

A run used to be one asyncio task on the API replica that received POST /run,
holding every result in memory until the end: a deploy lost the whole run, a
large golden dataset could not finish, and a long run was reported abandoned
while still executing.

Now a run is a set of ``eval_suite_task_results`` rows (enqueued when it
starts, ``pending -> submitting -> waiting -> done``) advanced by short,
NON-BLOCKING steps (:func:`run_step`). A worker never sits on a Celery slot
waiting for a golden goal — the goals themselves need worker slots — it:

1. polls the run's ``waiting`` tasks that are due: a terminal goal is scored
   (:func:`score_golden_task`: the goal must complete, checks, min_score,
   judge) and its result written, fenced on the lease; a goal past its
   deadline is cancelled and reported timed out; otherwise the check is
   deferred;
2. tops up the in-flight goals to ``eval_suite_run_concurrency``: claims a
   pending task, checks the agent still has the config the run is pinned to
   (MEM-52), submits the golden goal ON the agent and records its goal id and
   deadline (a resumed task polls the SAME goal; a step that died between
   submit and record re-submits with the same per-(run, task) dedup scope,
   which returns the in-flight goal);
3. finalizes the run when every task is done — exactly one step wins, and runs
   the post-run hooks.

On Celery (``app.scaling.tasks.run_eval_suite_worker``) each step re-enqueues
the next one with a countdown; without Celery the same steps loop in-process.
A beat sweeper re-dispatches runs that made no progress (worker restart, lost
message); duplicate steps are harmless (``SKIP LOCKED`` + lease fencing).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.intelligence.eval_suite import (
    GoldenTaskResult,
    LLMJudge,
    cancel_unscored_goal,
    invalid_task_result,
    score_golden_task,
)
from app.intelligence.eval_suite_store import EvalSuiteStore, task_from_dict
from app.observability.logging import get_logger

logger = get_logger(__name__)

_TERMINAL_STATUS_EVENT = {
    "complete": "goal_complete",
    "failed": "goal_failed",
    "cancelled": "goal_cancelled",
}
_TERMINAL_EVENTS = frozenset({"goal_complete", "goal_failed", "goal_cancelled", "goal_rejected"})

AgentLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
RunHook = Callable[[EvalSuiteStore, dict[str, Any], Any], Awaitable[None]]


class RunSettings:
    """The Settings knobs a step needs."""

    def __init__(self, settings: Any = None) -> None:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.lease_seconds = float(getattr(settings, "eval_suite_lease_seconds", 60.0))
        self.seconds_per_iteration = float(
            getattr(settings, "eval_suite_task_seconds_per_iteration", 20.0)
        )
        self.timeout_max = float(getattr(settings, "eval_suite_task_timeout_max_seconds", 1800.0))
        self.poll_seconds = float(getattr(settings, "eval_suite_goal_poll_seconds", 5.0))
        self.max_attempts = int(getattr(settings, "eval_suite_max_task_attempts", 3))
        self.concurrency = int(getattr(settings, "eval_suite_run_concurrency", 4))

    def task_timeout(self, max_iterations: int) -> float:
        return min(self.timeout_max, max(60.0, max_iterations * self.seconds_per_iteration))


def _error(task_id: str, goal: str, reason: str, *, goal_id: str | None = None,
           status: str = "error") -> GoldenTaskResult:
    return GoldenTaskResult(task_id=task_id, goal=goal, passed=False, failure_reasons=[reason],
                            status=status, goal_id=goal_id)


async def _check_waiting(
    *,
    store: EvalSuiteStore,
    run: dict[str, Any],
    row: dict[str, Any],
    owner: str,
    goal_service: Any,
    tenant_ctx: Any,
    judge: LLMJudge | None,
    cfg: RunSettings,
) -> None:
    """One status check of a waiting task's goal: score it, time it out, or defer."""
    run_id = str(run["run_id"])
    task = task_from_dict(str(run["suite_id"]), row["task"])
    goal_id = str(row["goal_id"])
    started = time.monotonic()
    try:
        goal = await goal_service.get_goal(goal_id, tenant_ctx)
        status = str(goal.get("status") or "")
    except Exception as exc:
        logger.warning("eval_goal_status_read_failed", run_id=run_id, goal_id=goal_id,
                       error=str(exc)[:200])
        status = ""
    if status not in _TERMINAL_STATUS_EVENT:
        if row.get("overdue"):
            await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)
            result = _error(task.task_id, task.goal,
                            f"goal did not finish within "
                            f"{cfg.task_timeout(task.max_iterations):.0f}s",
                            goal_id=goal_id, status="timeout")
            await store.record_task_result(run_id, owner, result)
        else:
            await store.defer_check(run_id, task.task_id, owner, cfg.poll_seconds)
        return
    try:
        events = list(await goal_service.get_events(goal_id, tenant_ctx))
    except Exception as exc:
        await store.record_task_result(run_id, owner, _error(
            task.task_id, task.goal, f"could not read the goal's events: {str(exc)[:300]}",
            goal_id=goal_id))
        return
    # The goal row's status is authoritative: a goal that did not complete never
    # passes on its events.
    if status != "complete" or not any(e.get("type") in _TERMINAL_EVENTS for e in events):
        events.append({"type": _TERMINAL_STATUS_EVENT[status]})
    result = await score_golden_task(
        task, events=events, goal_id=goal_id, judge=judge, tenant_ctx=tenant_ctx,
        duration_seconds=time.monotonic() - started,
    )
    await store.record_task_result(run_id, owner, result)


async def _submit_next(
    *,
    store: EvalSuiteStore,
    run: dict[str, Any],
    owner: str,
    goal_service: Any,
    tenant_ctx: Any,
    agent_loader: AgentLoader | None,
    cfg: RunSettings,
) -> bool:
    """Claim and submit one pending task. False when nothing is left to submit."""
    from app.intelligence.rollout_gate import agent_config_hash

    run_id = str(run["run_id"])
    claim = await store.claim_pending(run_id, owner, cfg.lease_seconds)
    if claim is None:
        return False
    task = task_from_dict(str(run["suite_id"]), claim["task"])
    if not task.has_checks:
        await store.record_task_result(run_id, owner, invalid_task_result(task))
        return True
    if int(claim["attempts"]) > cfg.max_attempts:
        await store.record_task_result(run_id, owner, _error(
            task.task_id, task.goal,
            f"gave up after {cfg.max_attempts} attempts (workers kept dying)",
            goal_id=claim.get("goal_id")))
        return True
    agent_id = run.get("agent_id")
    if agent_id:
        agent = await agent_loader(str(agent_id)) if agent_loader is not None else None
        if agent is None:
            await store.record_task_result(run_id, owner, _error(
                task.task_id, task.goal, f"agent {agent_id} no longer exists"))
            return True
        if agent_config_hash(agent) != run.get("agent_config_hash"):
            await store.record_task_result(run_id, owner, _error(
                task.task_id, task.goal, "the agent's configuration changed during the run"))
            return True
    try:
        submitted = await goal_service.submit_goal(
            goal=task.goal,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id or None,
            # Traceability, and one dedup scope per (run, task): a step that died
            # after submitting gets the same in-flight goal back on retry.
            execution_context={"eval_suite_run_id": run_id, "eval_task_id": task.task_id},
        )
        goal_id = str(submitted["goal_id"])
    except Exception as exc:
        await store.record_task_result(run_id, owner, _error(
            task.task_id, task.goal, f"goal submission failed: {str(exc)[:300]}"))
        return True
    if not await store.mark_waiting(run_id, task.task_id, owner, goal_id,
                                    cfg.task_timeout(task.max_iterations)):
        logger.warning("eval_task_lease_lost", run_id=run_id, task_id=task.task_id)
    return True


async def run_step(
    *,
    store: EvalSuiteStore,
    run_id: str,
    goal_service: Any,
    tenant_ctx: Any,
    judge: LLMJudge | None = None,
    agent_loader: AgentLoader | None = None,
    on_completed: RunHook | None = None,
    cfg: RunSettings | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Advance a run by one non-blocking step. ``status`` is ``running`` until done."""
    cfg = cfg or RunSettings()
    owner = owner or f"evalstep-{uuid.uuid4().hex[:12]}"
    run = await store.get_run(run_id)
    if run is None:
        return {"status": "missing", "run_id": run_id}
    if run.get("status") != "running":
        return {"status": run.get("status"), "run_id": run_id}
    await store.heartbeat(run_id)
    concurrency = max(1, int(run.get("concurrency") or cfg.concurrency))

    for row in await store.claim_due(run_id, owner, cfg.lease_seconds, concurrency):
        await _check_waiting(store=store, run=run, row=row, owner=owner,
                             goal_service=goal_service, tenant_ctx=tenant_ctx, judge=judge,
                             cfg=cfg)

    inflight = await store.count_inflight(run_id)
    while inflight < concurrency:
        if not await _submit_next(store=store, run=run, owner=owner, goal_service=goal_service,
                                  tenant_ctx=tenant_ctx, agent_loader=agent_loader, cfg=cfg):
            break
        inflight = await store.count_inflight(run_id)

    final = await store.finalize_run(run_id)
    if final is None:
        return {"status": "running", "run_id": run_id}
    logger.info("eval_suite_run_completed", run_id=run_id)
    if on_completed is not None:
        try:
            await on_completed(store, final, tenant_ctx)
        except Exception as exc:  # the run's result is recorded; a hook must not undo it
            logger.error("eval_suite_post_run_hook_failed", run_id=run_id, error=str(exc))
    return {"status": "completed", "run_id": run_id}


async def run_until_done(
    *, max_steps: int = 1_000_000, **kwargs: Any
) -> dict[str, Any]:
    """In-process execution (no Celery): repeat steps, sleeping the poll interval."""
    cfg: RunSettings = kwargs.pop("cfg", None) or RunSettings()
    out: dict[str, Any] = {"status": "running"}
    for _ in range(max_steps):
        out = await run_step(cfg=cfg, **kwargs)
        if out["status"] != "running":
            return out
        await asyncio.sleep(cfg.poll_seconds)
    return out
