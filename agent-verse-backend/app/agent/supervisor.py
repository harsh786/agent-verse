"""Supervisor agent — coordinates multiple sub-agents to achieve complex goals.

Pattern: decompose goal → spawn sub-agents → monitor → synthesize results.
Each sub-agent runs with full governance, memory, and tool context inheritance.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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


@dataclass
class SupervisionResult:
    success: bool
    tasks: list[SubAgentTask]
    synthesized_result: str = ""
    total_cost_usd: float = 0.0


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
        timeout_per_subtask: float = 300.0,
    ) -> None:
        self._planner = planner_provider
        self._goal_service = goal_service
        self._router = agent_router
        self._max_parallel = max_parallel
        self._timeout = timeout_per_subtask

    async def run(
        self,
        goal: str,
        tenant_ctx: Any,
        event_callback: Any = None,
        parent_goal_id: str | None = None,
    ) -> SupervisionResult:
        """Decompose and execute goal across multiple sub-agents."""

        async def emit(event: dict) -> None:
            if event_callback:
                with contextlib.suppress(Exception):
                    await event_callback(event)

        # Step 1: Decompose goal into sub-tasks
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

        # Step 2: Execute sub-tasks in parallel batches
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def run_task(task: SubAgentTask) -> None:
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
                        execution_context={SUBGOAL_MARKER: parent_goal_id or "supervisor"},
                    )
                    goal_id = sub["goal_id"]
                    if parent_goal_id and str(goal_id) == str(parent_goal_id):
                        # Never wait on ourselves (deadlock).
                        raise RuntimeError("sub-goal resolved to the parent goal")
                    task.goal_id = str(goal_id)
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
                    async with asyncio.timeout(self._timeout):
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
                    task.error = f"Timeout after {self._timeout}s"
                except Exception as exc:
                    task.status = "failed"
                    task.error = str(exc)
                finally:
                    task.completed_at = datetime.now(UTC).isoformat()
                    await emit(
                        {
                            "type": "supervisor_task_complete",
                            "task_id": task.task_id,
                            "status": task.status,
                            "error": task.error[:100] if task.error else None,
                        }
                    )

        await asyncio.gather(*[run_task(t) for t in sub_tasks])

        # Step 3: Synthesize results
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
            ' "optional": false}}]}}'
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
                    tasks.append(SubAgentTask(goal=t.get("goal", "")))
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
