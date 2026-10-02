"""Durable execution of eval-suite runs (MEM-53).

A run used to be one asyncio task on the API replica that received POST /run,
holding every result in memory until the end: a deploy lost the whole run, a
large golden dataset could not finish, and a long run was reported abandoned
while still executing.

Now a run is a set of ``eval_suite_task_results`` rows (enqueued when it
starts) and up to ``eval_suite_run_concurrency`` workers per run — Celery tasks
(``app.scaling.tasks.run_eval_suite_worker``), or in-process tasks without
Celery — each looping:

1. claim the next unfinished task under a renewable lease (``FOR UPDATE SKIP
   LOCKED``; a task whose worker died has an expired lease and is claimed again);
2. check the agent still has the config the run is pinned to (MEM-52);
3. submit the golden goal ON the agent and record its goal id before waiting
   (a resumed task waits on the SAME goal — it is never submitted twice);
4. wait for the goal's terminal status (polling, renewing the lease, which also
   heartbeats the run), cancel it on timeout;
5. score it (:func:`score_golden_task`: the goal must complete, checks,
   min_score, judge) and write the result, fenced on the lease.

When nothing is left to claim and every task is done, the run is finalized by
exactly one worker, which then runs the post-run hooks. A beat sweeper
re-dispatches workers for a run with no progress (``resume_stalled_runs``).
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
_TERMINAL_EVENTS = frozenset(
    {"goal_complete", "goal_failed", "goal_cancelled", "goal_rejected"}
)

AgentLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
RunHook = Callable[[EvalSuiteStore, dict[str, Any], Any], Awaitable[None]]


class RunSettings:
    """The Settings knobs a worker needs (read once per worker)."""

    def __init__(self, settings: Any = None) -> None:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.lease_seconds = float(getattr(settings, "eval_suite_lease_seconds", 60.0))
        self.seconds_per_iteration = float(
            getattr(settings, "eval_suite_task_seconds_per_iteration", 20.0)
        )
        self.timeout_max = float(getattr(settings, "eval_suite_task_timeout_max_seconds", 1800.0))
        self.poll_seconds = float(getattr(settings, "eval_suite_goal_poll_seconds", 2.0))
        self.max_attempts = int(getattr(settings, "eval_suite_max_task_attempts", 3))
        self.concurrency = int(getattr(settings, "eval_suite_run_concurrency", 4))

    def task_timeout(self, max_iterations: int) -> float:
        return min(self.timeout_max, max(60.0, max_iterations * self.seconds_per_iteration))


def _error(task_id: str, goal: str, reason: str, *, goal_id: str | None = None,
           status: str = "error", started: float | None = None) -> GoldenTaskResult:
    return GoldenTaskResult(
        task_id=task_id, goal=goal, passed=False, failure_reasons=[reason], status=status,
        goal_id=goal_id,
        duration_seconds=(time.monotonic() - started) if started is not None else 0.0,
    )


class _LeaseLostError(Exception):
    """Another worker owns the task now; this worker must not record anything."""


async def _wait_for_goal(
    *,
    store: EvalSuiteStore,
    goal_service: Any,
    tenant_ctx: Any,
    run_id: str,
    task_id: str,
    owner: str,
    goal_id: str,
    timeout: float,
    cfg: RunSettings,
) -> str | None:
    """The goal's terminal status, or None on timeout. Renews the lease meanwhile."""
    deadline = time.monotonic() + timeout
    renew_every = max(cfg.lease_seconds / 3.0, cfg.poll_seconds)
    next_renew = time.monotonic() + renew_every
    while True:
        goal = await goal_service.get_goal(goal_id, tenant_ctx)
        status = str(goal.get("status") or "")
        if status in _TERMINAL_STATUS_EVENT:
            return status
        now = time.monotonic()
        if now >= deadline:
            return None
        if now >= next_renew:
            if not await store.renew_lease(run_id, task_id, owner, cfg.lease_seconds):
                raise _LeaseLostError
            next_renew = now + renew_every
        await asyncio.sleep(min(cfg.poll_seconds, max(0.0, deadline - now)))


async def execute_claimed_task(
    *,
    store: EvalSuiteStore,
    run: dict[str, Any],
    claim: dict[str, Any],
    owner: str,
    goal_service: Any,
    tenant_ctx: Any,
    judge: LLMJudge | None,
    agent_loader: AgentLoader | None,
    cfg: RunSettings,
) -> GoldenTaskResult:
    """Run (or resume) one claimed golden task to a scored / unscored result."""
    from app.intelligence.rollout_gate import agent_config_hash

    run_id = str(run["run_id"])
    task = task_from_dict(str(run["suite_id"]), claim["task"])
    started = time.monotonic()
    if not task.has_checks:
        return invalid_task_result(task)
    if int(claim["attempts"]) > cfg.max_attempts:
        return _error(task.task_id, task.goal,
                      f"gave up after {cfg.max_attempts} attempts (workers kept dying)",
                      goal_id=claim.get("goal_id"))

    goal_id = claim.get("goal_id")
    if not goal_id:
        agent_id = run.get("agent_id")
        if agent_id:
            agent = await agent_loader(str(agent_id)) if agent_loader is not None else None
            if agent is None:
                return _error(task.task_id, task.goal, f"agent {agent_id} no longer exists")
            if agent_config_hash(agent) != run.get("agent_config_hash"):
                return _error(task.task_id, task.goal,
                              "the agent's configuration changed during the run")
        try:
            submitted = await goal_service.submit_goal(
                goal=task.goal,
                priority="normal",
                dry_run=False,
                tenant_ctx=tenant_ctx,
                agent_id=agent_id or None,
                # Traceability, and a distinct dedup scope per (run, task).
                execution_context={"eval_suite_run_id": run_id, "eval_task_id": task.task_id},
            )
            goal_id = str(submitted["goal_id"])
        except Exception as exc:
            return _error(task.task_id, task.goal, f"goal submission failed: {str(exc)[:300]}",
                          started=started)
        if not await store.set_task_goal(run_id, task.task_id, owner, goal_id):
            raise _LeaseLostError

    timeout = cfg.task_timeout(task.max_iterations)
    try:
        status = await _wait_for_goal(
            store=store, goal_service=goal_service, tenant_ctx=tenant_ctx, run_id=run_id,
            task_id=task.task_id, owner=owner, goal_id=goal_id, timeout=timeout, cfg=cfg,
        )
    except _LeaseLostError:
        raise
    except Exception as exc:
        await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)
        return _error(task.task_id, task.goal, f"could not read the goal: {str(exc)[:300]}",
                      goal_id=goal_id, started=started)
    if status is None:
        await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)
        return _error(task.task_id, task.goal, f"goal did not finish within {timeout:.0f}s",
                      goal_id=goal_id, status="timeout", started=started)

    try:
        events = list(await goal_service.get_events(goal_id, tenant_ctx))
    except Exception as exc:
        return _error(task.task_id, task.goal, f"could not read the goal's events: {exc!s:.300}",
                      goal_id=goal_id, started=started)
    if not any(e.get("type") in _TERMINAL_EVENTS for e in events):
        events.append({"type": _TERMINAL_STATUS_EVENT[status]})
    elif status != "complete":
        # The status is authoritative: a goal that FAILED never passes on events.
        events.append({"type": _TERMINAL_STATUS_EVENT[status]})
    return await score_golden_task(
        task, events=events, goal_id=goal_id, judge=judge, tenant_ctx=tenant_ctx,
        duration_seconds=time.monotonic() - started,
    )


async def run_suite_worker(
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
    """One worker of a run: claim → execute → record, until nothing is claimable."""
    cfg = cfg or RunSettings()
    owner = owner or f"evalw-{uuid.uuid4().hex[:12]}"
    run = await store.get_run(run_id)
    if run is None:
        return {"status": "missing", "run_id": run_id, "processed": 0}
    if run.get("status") != "running":
        return {"status": run.get("status"), "run_id": run_id, "processed": 0}
    processed = 0
    while True:
        claim = await store.claim_next(run_id, owner, cfg.lease_seconds)
        if claim is None:
            break
        try:
            result = await execute_claimed_task(
                store=store, run=run, claim=claim, owner=owner, goal_service=goal_service,
                tenant_ctx=tenant_ctx, judge=judge, agent_loader=agent_loader, cfg=cfg,
            )
        except _LeaseLostError:
            logger.warning("eval_task_lease_lost", run_id=run_id, task_id=claim["task_id"])
            continue
        if await store.record_task_result(run_id, owner, result):
            processed += 1
        else:
            logger.warning("eval_task_result_fenced", run_id=run_id, task_id=claim["task_id"])
    final = await store.finalize_run(run_id)
    if final is not None:
        logger.info("eval_suite_run_completed", run_id=run_id)
        if on_completed is not None:
            try:
                await on_completed(store, final, tenant_ctx)
            except Exception as exc:  # the run's result is recorded; a hook must not undo it
                logger.error("eval_suite_post_run_hook_failed", run_id=run_id, error=str(exc))
    return {"status": "completed" if final else "running", "run_id": run_id,
            "processed": processed}
