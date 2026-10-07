"""Supervisor agent — coordinates multiple sub-agents to achieve complex goals.

Pattern: decompose goal → spawn sub-agents → monitor → synthesize results.
Each sub-agent runs with full governance, memory, and tool context inheritance.

Two ways to wait for the sub-goals:

* **continuation** (a worker parent with a durable ledger, a01-F006-05): every
  sub-goal is a real goal with a per-child timeout; the parent dispatches up to
  ``max_parallel`` of them and returns ``parked=True`` instead of waiting. The
  worker releases its Celery slot (goal status ``waiting_children``) and the last
  sub-goal to finish re-queues it; the next run re-attaches to the ledger,
  folds the finished children in (from their goal rows / persisted events),
  dispatches the rest, and synthesizes once every child is terminal. A child
  past its timeout is cancelled by the fan-out sweeper.
* **in-slot** (in-process runs, no ledger): the parent streams each sub-goal's
  events under that child's timeout and cancels a child it gives up on.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agent.fanout_ledger import (
    FANOUT_KIND_KEY,
    FANOUT_TASK_KEY,
    FanoutLedger,
    LedgerEntry,
    child_timeout_seconds,
    failure_reason,
    reconcile_children,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)


def _unwrap_bridged_event(event: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Celery-worker event ``{type, goal_id, tenant_id, payload: {...}}``.

    Sub-goals run on workers publish that envelope (and the Redis bridge /
    cross-replica subscribe paths forward it as-is), so ``output`` / ``result``
    sat under ``payload`` and every worker-run sub-task looked output-less.
    """
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event
    merged = dict(payload)
    for key, value in event.items():
        if key != "payload":
            merged.setdefault(key, value)
    return merged


def _final_answer(event: dict[str, Any], step_outputs: list[str]) -> str:
    """The sub-goal's real result: an answer carried on the terminal event, else the
    outputs of the steps it executed. Empty when nothing real was produced."""
    for key in ("answer", "cited_answer", "output", "result", "summary"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "\n\n".join(o for o in step_outputs if o.strip())


def supervisor_child_outcome(
    goal_status: str, error_message: str, events: list[dict[str, Any]]
) -> tuple[str, str, str]:
    """(status, result, error) of a finished sub-goal, from its row + persisted events.

    The same reading the in-slot stream applied: the answer on ``goal_complete``,
    else the outputs of the steps it executed; a completed sub-goal with no real
    output is a failure, never the literal string 'completed'.
    """
    if goal_status == "complete":
        step_outputs: list[str] = []
        answer = ""
        for raw in events:
            evt = _unwrap_bridged_event(raw)
            etype = evt.get("type")
            if etype == "step_complete" and evt.get("output"):
                step_outputs.append(str(evt["output"]))
            elif etype == "goal_complete":
                answer = _final_answer(evt, step_outputs) or answer
        answer = answer or _final_answer({}, step_outputs)
        if answer:
            return "complete", answer, ""
        return "failed", "", "sub-goal completed without producing any output"
    return "failed", "", failure_reason(goal_status, error_message, events)


# execution_context / agent-state context key set on goals a supervisor spawned.
SUBGOAL_MARKER = "_supervisor_parent_goal_id"

@dataclass
class SubAgentTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    goal: str = ""
    agent_id: str | None = None
    # The real goal id created for this sub-task by GoalService.submit_goal, so
    # callers can correlate the synthesized result back to the persisted goal.
    goal_id: str = ""
    status: str = "pending"  # pending | running | complete | failed
    result: str = ""
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    # The plan's own timeout for this sub-task (decomposition ``timeout_seconds``).
    timeout_s: float | None = None


def _spec_timeout(spec: dict[str, Any]) -> float | None:
    value = spec.get("timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value > 0 else None


def _task_from_entry(entry: LedgerEntry, *, live: bool = False) -> SubAgentTask:
    if entry.finished:
        status = entry.status
    elif live and entry.status == "dispatched":
        status = "running"
    else:
        status = "pending"
    return SubAgentTask(
        task_id=entry.task_key,
        goal=str(entry.spec.get("goal", "")),
        goal_id=entry.child_goal_id or "",
        status=status,
        result=entry.result,
        error=entry.error,
        timeout_s=_spec_timeout(entry.spec),
    )


@dataclass
class SupervisionResult:
    success: bool
    tasks: list[SubAgentTask]
    synthesized_result: str = ""
    total_cost_usd: float = 0.0
    # Continuation: the parent dispatched its sub-goals and must wait for them
    # outside its worker slot (status ``waiting_children``); nothing synthesized.
    parked: bool = False


class SupervisorAgent:
    """Decomposes a complex goal and coordinates multiple sub-agents.

    Unlike goal_tree.py (which is LangGraph-internal), the SupervisorAgent
    is a top-level orchestrator that:
    1. Uses LLM to decompose the goal into sub-tasks
    2. Assigns sub-tasks to appropriate agents (via router or explicit assignment)
    3. Monitors sub-agent progress via GoalService
    4. Handles failures (retry, reassign, or skip optional tasks)
    5. Synthesizes all sub-agent results into a final coherent output
    """

    def __init__(
        self,
        *,
        planner_provider: Any,
        goal_service: Any,
        agent_router: Any = None,
        max_parallel: int = 5,
        timeout_per_subtask: float | None = None,
        continuation: bool = False,
        child_timeout_seconds: float | None = None,
    ) -> None:
        self._planner = planner_provider
        self._goal_service = goal_service
        self._router = agent_router
        self._max_parallel = max(1, int(max_parallel))
        # An explicit per-sub-task timeout overrides the per-child resolution.
        self._timeout = timeout_per_subtask
        # Park instead of waiting in the slot (needs a ledger; worker runs only).
        self._continuation = continuation
        # The goal's default child timeout (subgoal_timeout_seconds / the parent's
        # effective goal timeout); per-task plan timeouts win over it.
        self._child_timeout_default = child_timeout_seconds

    def _task_timeout(self, task: SubAgentTask, tenant_ctx: Any) -> float:
        """This sub-task's timeout: the plan's, else the goal's/agent's, else the plan tier's."""
        if self._timeout is not None:
            return float(self._timeout)
        return child_timeout_seconds(
            requested=task.timeout_s, default=self._child_timeout_default, tenant_ctx=tenant_ctx
        )

    async def _cancel_abandoned(self, task: SubAgentTask, tenant_ctx: Any) -> None:
        cancel = getattr(self._goal_service, "cancel_goal", None)
        if not task.goal_id or cancel is None:
            return
        try:
            await cancel(goal_id=task.goal_id, tenant_ctx=tenant_ctx)
        except Exception as exc:
            logger.warning(
                "supervisor_abandoned_subgoal_cancel_failed",
                goal_id=task.goal_id,
                error=type(exc).__name__,
            )

    async def run(
        self,
        goal: str,
        tenant_ctx: Any,
        event_callback: Any = None,
        parent_goal_id: str | None = None,
        ledger: FanoutLedger | None = None,
    ) -> SupervisionResult:
        """Decompose and execute goal across multiple sub-agents.

        With a ``ledger`` (the parent is a persisted goal and Postgres is wired)
        the decomposition and every child's goal id / outcome are durable, so a
        parent redelivered after a crash re-attaches instead of re-dispatching.
        In continuation mode (``continuation=True`` + a ledger) it never waits:
        the result is ``parked`` until every child is terminal (see module doc).
        """

        async def emit(event: dict) -> None:
            if event_callback:
                with contextlib.suppress(Exception):
                    await event_callback(event)

        # Step 1: Decompose goal into sub-tasks — or, when this parent was already
        # here before a crash/redelivery (or a continuation re-entry), re-attach to
        # the durable plan (CORE-31).
        entries = await ledger.load() if ledger is not None else []
        if entries:
            sub_tasks = [_task_from_entry(e) for e in entries]
            await emit(
                {
                    "type": "supervisor_resumed",
                    "task_count": len(sub_tasks),
                    "finished": sum(1 for e in entries if e.finished),
                }
            )
        else:
            sub_tasks = await self._decompose(goal, tenant_ctx)
            await emit(
                {
                    "type": "supervisor_decomposed",
                    "task_count": len(sub_tasks),
                    "tasks": [t.goal for t in sub_tasks],
                }
            )

            # A single sub-task is the goal itself: supervising it only spawns a
            # redundant copy and waits on it. Let the normal loop handle it.
            if len(sub_tasks) <= 1:
                return SupervisionResult(success=False, tasks=sub_tasks)
            if ledger is not None:
                # Durable BEFORE any dispatch; raises (no fan-out) when it cannot
                # be written. First writer wins, so a concurrent redelivery of the
                # same parent converges on one plan.
                entries = await ledger.plan(
                    [
                        LedgerEntry(
                            task_key=t.task_id,
                            position=i,
                            spec={
                                "goal": t.goal,
                                **({"timeout_seconds": t.timeout_s} if t.timeout_s else {}),
                            },
                        )
                        for i, t in enumerate(sub_tasks)
                    ]
                )
                sub_tasks = [_task_from_entry(e) for e in entries]

        if self._continuation and ledger is not None:
            return await self._advance(
                goal, tenant_ctx, emit, parent_goal_id=parent_goal_id, ledger=ledger,
                entries=entries,
            )

        # Step 2: Execute sub-tasks in parallel batches
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def run_task(task: SubAgentTask) -> None:
            if task.status in ("complete", "failed"):
                return  # finished before a crash: reuse its recorded result
            # Per child (the plan's / agent's timeout) — no longer a fixed 300 s.
            _timeout = self._task_timeout(task, tenant_ctx)
            async with semaphore:
                task.status = "running"
                task.started_at = datetime.now(UTC).isoformat()
                await emit(
                    {
                        "type": "supervisor_task_started",
                        "task_id": task.task_id,
                        "goal": task.goal[:100],
                    }
                )
                try:
                    goal_id: Any = task.goal_id
                    if not goal_id and ledger is not None:
                        goal_id = await ledger.find_child_goal(task.task_id)
                        if goal_id:
                            # Created just before a crash, never recorded: do now
                            # (best effort — the goals row keeps finding it).
                            try:
                                await ledger.mark_dispatched(task.task_id, str(goal_id))
                            except Exception as exc:
                                logger.warning(
                                    "supervisor_ledger_dispatch_write_failed",
                                    error=type(exc).__name__,
                                )
                    if goal_id:
                        # Already dispatched by an earlier (crashed) run of this
                        # parent: re-attach (its events replay) — never resubmit.
                        task.goal_id = str(goal_id)
                        await emit(
                            {
                                "type": "supervisor_task_reattached",
                                "task_id": task.task_id,
                                "goal_id": task.goal_id,
                            }
                        )
                    else:
                        sub = await self._goal_service.submit_goal(
                            goal=task.goal,
                            priority="normal",
                            dry_run=False,
                            tenant_ctx=tenant_ctx,
                            agent_id=task.agent_id,
                            # Marks the sub-goal so its own graph does not run the
                            # supervisor again: without it every sub-goal decomposed
                            # itself, recursively, each parent waiting on children
                            # that never finished (goals stuck in "planning").
                            # The task key lets a resumed parent find this row.
                            execution_context={
                                SUBGOAL_MARKER: parent_goal_id or "supervisor",
                                FANOUT_TASK_KEY: task.task_id,
                            },
                        )
                        goal_id = sub["goal_id"]
                        if parent_goal_id and str(goal_id) == str(parent_goal_id):
                            # Never wait on ourselves (deadlock).
                            raise RuntimeError("sub-goal resolved to the parent goal")
                        task.goal_id = str(goal_id)
                        if ledger is not None:
                            try:
                                await ledger.mark_dispatched(task.task_id, task.goal_id)
                            except Exception as exc:
                                # The goals row (parent + task key) still finds it.
                                logger.warning(
                                    "supervisor_ledger_dispatch_write_failed",
                                    error=type(exc).__name__,
                                )
                        await emit(
                            {
                                "type": "supervisor_task_goal_created",
                                "task_id": task.task_id,
                                "goal_id": task.goal_id,
                            }
                        )

                    # Wait for completion, collecting the sub-goal's REAL output. The
                    # goal_complete event carries no "output" key, so the result used to
                    # be the literal string 'completed' for every sub-task.
                    step_outputs: list[str] = []
                    async with asyncio.timeout(_timeout):
                        async for raw_evt in self._goal_service.subscribe_events(
                            goal_id=goal_id, tenant_ctx=tenant_ctx
                        ):
                            evt = _unwrap_bridged_event(raw_evt)
                            etype = evt.get("type")
                            if etype == "step_complete" and evt.get("output"):
                                step_outputs.append(str(evt["output"]))
                            elif etype == "goal_complete":
                                answer = _final_answer(evt, step_outputs)
                                if answer:
                                    task.status = "complete"
                                    task.result = answer
                                else:
                                    task.status = "failed"
                                    task.error = "sub-goal completed without producing any output"
                                break
                            elif etype in ("goal_failed", "goal_cancelled"):
                                task.status = "failed"
                                task.error = str(evt.get("reason") or etype)
                                break
                    if task.status == "running":
                        task.status = "failed"
                        task.error = "event stream ended before the sub-goal finished"
                except TimeoutError:
                    task.status = "failed"
                    task.error = f"Timeout after {_timeout:g}s"
                    # The parent gives up on it: stop the sub-goal too, or it keeps
                    # running (and spending) with a result nobody will read.
                    await self._cancel_abandoned(task, tenant_ctx)
                except Exception as exc:
                    task.status = "failed"
                    task.error = str(exc)
                finally:
                    task.completed_at = datetime.now(UTC).isoformat()
                    if ledger is not None and task.status in ("complete", "failed"):
                        try:
                            await ledger.mark_finished(
                                task.task_id,
                                status=task.status,
                                result=task.result,
                                error=task.error[:2000],
                            )
                        except Exception as exc:
                            # A resume re-attaches and replays it instead.
                            logger.warning(
                                "supervisor_ledger_finish_write_failed",
                                error=type(exc).__name__,
                            )
                    await emit(
                        {
                            "type": "supervisor_task_complete",
                            "task_id": task.task_id,
                            "status": task.status,
                            "error": task.error[:100] if task.error else None,
                        }
                    )

        await asyncio.gather(*[run_task(t) for t in sub_tasks])
        return await self._finish(goal, sub_tasks, tenant_ctx, emit)

    async def _advance(
        self,
        goal: str,
        tenant_ctx: Any,
        emit: Any,
        *,
        parent_goal_id: str | None,
        ledger: FanoutLedger,
        entries: list[LedgerEntry],
    ) -> SupervisionResult:
        """Continuation step: fold finished children in, dispatch more, park or finish.

        Never waits on a child. Idempotent on re-entry: finished children are read
        from the ledger, dispatched ones are re-attached (never resubmitted).
        """
        for done in await reconcile_children(ledger, entries, supervisor_child_outcome):
            await emit(
                {
                    "type": "supervisor_task_complete",
                    "task_id": done.task_key,
                    "status": done.status,
                    "error": done.error[:100] if done.error else None,
                }
            )
        in_flight = sum(1 for e in entries if e.status == "dispatched")
        free = max(0, self._max_parallel - in_flight)
        for entry in [e for e in entries if e.status == "planned"][:free]:
            await self._dispatch_entry(entry, tenant_ctx, emit, parent_goal_id, ledger)
        tasks = [_task_from_entry(e, live=True) for e in entries]
        pending = [e for e in entries if not e.finished]
        if pending:
            await emit(
                {
                    "type": "supervisor_waiting_children",
                    "task_count": len(entries),
                    "pending": len(pending),
                    "goal_ids": [e.child_goal_id for e in pending if e.child_goal_id],
                }
            )
            return SupervisionResult(success=False, tasks=tasks, parked=True)
        return await self._finish(goal, tasks, tenant_ctx, emit)

    async def _dispatch_entry(
        self,
        entry: LedgerEntry,
        tenant_ctx: Any,
        emit: Any,
        parent_goal_id: str | None,
        ledger: FanoutLedger,
    ) -> None:
        """Submit (or re-attach) one planned child as a real goal with its deadline."""
        task = _task_from_entry(entry)
        await emit(
            {"type": "supervisor_task_started", "task_id": task.task_id, "goal": task.goal[:100]}
        )
        goal_id: str = ""
        try:
            found = entry.child_goal_id or await ledger.find_child_goal(entry.task_key)
            if found:
                goal_id = str(found)
                await emit(
                    {
                        "type": "supervisor_task_reattached",
                        "task_id": task.task_id,
                        "goal_id": goal_id,
                    }
                )
            else:
                sub = await self._goal_service.submit_goal(
                    goal=task.goal,
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                    agent_id=task.agent_id,
                    execution_context={
                        SUBGOAL_MARKER: parent_goal_id or "supervisor",
                        FANOUT_TASK_KEY: task.task_id,
                        FANOUT_KIND_KEY: "supervisor",
                    },
                )
                goal_id = str(sub["goal_id"])
                if parent_goal_id and goal_id == str(parent_goal_id):
                    raise RuntimeError("sub-goal resolved to the parent goal")
                await emit(
                    {
                        "type": "supervisor_task_goal_created",
                        "task_id": task.task_id,
                        "goal_id": goal_id,
                    }
                )
        except Exception as exc:
            entry.status, entry.error = "failed", str(exc)[:2000]
            try:
                await ledger.mark_finished(entry.task_key, status="failed", error=entry.error)
            except Exception as write_exc:
                logger.warning(
                    "supervisor_ledger_finish_write_failed", error=type(write_exc).__name__
                )
            await emit(
                {
                    "type": "supervisor_task_complete",
                    "task_id": task.task_id,
                    "status": "failed",
                    "error": entry.error[:100],
                }
            )
            return
        entry.child_goal_id, entry.status = goal_id, "dispatched"
        try:
            await ledger.mark_dispatched(
                entry.task_key, goal_id, timeout_s=self._task_timeout(task, tenant_ctx)
            )
        except Exception as exc:
            # The goals row (parent + task key) still finds it on re-entry, and the
            # parent is woken by the row reaching a terminal status.
            logger.warning("supervisor_ledger_dispatch_write_failed", error=type(exc).__name__)

    async def _finish(
        self, goal: str, sub_tasks: list[SubAgentTask], tenant_ctx: Any, emit: Any
    ) -> SupervisionResult:
        # Step 3: Synthesize results (in plan order)
        completed = [t for t in sub_tasks if t.status == "complete"]
        failed = [t for t in sub_tasks if t.status == "failed"]

        synthesis = await self._synthesize(goal, completed, failed, tenant_ctx)

        result = SupervisionResult(
            # Every sub-task must succeed: a majority-completed run used to report
            # success while silently dropping the failed sub-tasks' work.
            success=bool(sub_tasks) and len(failed) == 0,
            tasks=sub_tasks,
            synthesized_result=synthesis,
        )

        await emit(
            {
                "type": "supervisor_complete",
                "success": result.success,
                "completed_tasks": len(completed),
                "failed_tasks": len(failed),
            }
        )

        return result

    async def _decompose(self, goal: str, tenant_ctx: Any) -> list[SubAgentTask]:
        """Use LLM to decompose goal into independent sub-tasks."""
        from app.providers.base import CompletionRequest, Message

        decompose_prompt = (
            "You are a goal decomposer. Break this complex goal into 2-6 independent sub-tasks.\n"
            "Each sub-task should be self-contained and achievable by a single agent.\n\n"
            "Goal: {goal}\n\n"
            'Return JSON only:\n{{"sub_tasks": [{{"goal": "specific sub-task description",'
            ' "optional": false, "timeout_seconds": 600}}]}}\n'
            "timeout_seconds is optional: how long that sub-task may take."
        )

        req = CompletionRequest(
            messages=[Message(role="user", content=decompose_prompt.format(goal=goal))],
            model=getattr(self._planner, "_default_model", "claude-opus-4-8"),
        )
        try:
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                self._planner,
                req,
                role="supervisor",
                tenant_ctx=tenant_ctx,
            )
            import json
            import re

            m = re.search(r"\{.*\}", resp.content, re.DOTALL)
            if m:
                data = json.loads(m.group())
                tasks = []
                for t in data.get("sub_tasks", [])[:6]:
                    tasks.append(
                        SubAgentTask(
                            goal=t.get("goal", ""),
                            timeout_s=_spec_timeout(t) if isinstance(t, dict) else None,
                        )
                    )
                if tasks:
                    return tasks
        except Exception as exc:
            logger.warning("supervisor_decompose_failed", error=str(exc))

        # Fallback: single task
        return [SubAgentTask(goal=goal)]

    async def _synthesize(
        self,
        original_goal: str,
        completed: list[SubAgentTask],
        failed: list[SubAgentTask],
        tenant_ctx: Any,
    ) -> str:
        """Synthesize results from all sub-agents via LLM into a coherent answer."""
        if not completed:
            return f"All {len(failed)} sub-tasks failed. No results to synthesize."

        results_text = "\n\n".join(
            [f"Sub-task: {t.goal}\nResult: {t.result[:500]}" for t in completed]
        )

        failed_text = "\n".join(f"- {t.goal}: {t.error[:200]}" for t in failed)
        prompt = (
            f"Original goal: {original_goal}\n\n"
            f"Completed sub-tasks:\n{results_text}\n\n"
            + (
                f"FAILED sub-tasks (state clearly that these parts were not achieved):\n"
                f"{failed_text}\n\n"
                if failed
                else ""
            )
            +
            "Synthesize a coherent, comprehensive answer to the original goal based on "
            "all sub-task results. Be concise and actionable."
        )

        try:
            from app.providers.base import CompletionRequest, Message

            model = getattr(self._planner, "_default_model", "claude-opus-4-8")
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            resp = await complete_decision(
                self._planner,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=model,
                    max_tokens=2000,
                ),
                role="supervisor",
                tenant_ctx=tenant_ctx,
                timeout_seconds=generation_timeout_seconds(),
            )
            return resp.content
        except Exception as exc:
            logger.warning("supervisor_synthesize_llm_failed", error=str(exc))
            # Fallback: structured text summary if LLM call fails
            lines = [f"Completed {len(completed)}/{len(completed) + len(failed)} sub-tasks.\n"]
            for t in completed:
                lines.append(f"• {t.goal}: {t.result[:200]}")
            return "\n".join(lines)
