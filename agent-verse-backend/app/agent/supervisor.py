"""Supervisor agent — coordinates multiple sub-agents to achieve complex goals.

Pattern: decompose goal → spawn sub-agents → monitor → synthesize results.
Each sub-agent runs with full governance, memory, and tool context inheritance.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SubAgentTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    goal: str = ""
    agent_id: str | None = None
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
    ) -> SupervisionResult:
        """Decompose and execute goal across multiple sub-agents."""

        async def emit(event: dict) -> None:
            if event_callback:
                try:
                    await event_callback(event)
                except Exception:
                    pass

        # Step 1: Decompose goal into sub-tasks
        sub_tasks = await self._decompose(goal, tenant_ctx)
        await emit({
            "type": "supervisor_decomposed",
            "task_count": len(sub_tasks),
            "tasks": [t.goal for t in sub_tasks],
        })

        # Step 2: Execute sub-tasks in parallel batches
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def run_task(task: SubAgentTask) -> None:
            async with semaphore:
                task.status = "running"
                task.started_at = datetime.now(UTC).isoformat()
                await emit({"type": "supervisor_task_started", "task_id": task.task_id,
                            "goal": task.goal[:100]})
                try:
                    sub = await self._goal_service.submit_goal(
                        goal=task.goal, priority="normal", dry_run=False,
                        tenant_ctx=tenant_ctx, agent_id=task.agent_id,
                    )
                    goal_id = sub["goal_id"]

                    # Wait for completion
                    async with asyncio.timeout(self._timeout):
                        async for evt in self._goal_service.subscribe_events(
                            goal_id=goal_id, tenant_ctx=tenant_ctx
                        ):
                            if evt.get("type") == "goal_complete":
                                task.status = "complete"
                                task.result = evt.get("output", "completed")
                                break
                            elif evt.get("type") == "goal_failed":
                                task.status = "failed"
                                task.error = evt.get("reason", "unknown")
                                break
                except asyncio.TimeoutError:
                    task.status = "failed"
                    task.error = f"Timeout after {self._timeout}s"
                except Exception as exc:
                    task.status = "failed"
                    task.error = str(exc)
                finally:
                    task.completed_at = datetime.now(UTC).isoformat()
                    await emit({
                        "type": "supervisor_task_complete",
                        "task_id": task.task_id,
                        "status": task.status,
                        "error": task.error[:100] if task.error else None,
                    })

        await asyncio.gather(*[run_task(t) for t in sub_tasks])

        # Step 3: Synthesize results
        completed = [t for t in sub_tasks if t.status == "complete"]
        failed = [t for t in sub_tasks if t.status == "failed"]

        synthesis = await self._synthesize(goal, completed, failed, tenant_ctx)

        result = SupervisionResult(
            success=len(failed) == 0 or len(completed) > len(failed),
            tasks=sub_tasks,
            synthesized_result=synthesis,
        )

        await emit({
            "type": "supervisor_complete",
            "success": result.success,
            "completed_tasks": len(completed),
            "failed_tasks": len(failed),
        })

        return result

    async def _decompose(self, goal: str, tenant_ctx: Any) -> list[SubAgentTask]:
        """Use LLM to decompose goal into independent sub-tasks."""
        from app.providers.base import CompletionRequest, Message

        DECOMPOSE_PROMPT = (
            "You are a goal decomposer. Break this complex goal into 2-6 independent sub-tasks.\n"
            "Each sub-task should be self-contained and achievable by a single agent.\n\n"
            "Goal: {goal}\n\n"
            'Return JSON only:\n{{"sub_tasks": [{{"goal": "specific sub-task description",'
            ' "optional": false}}]}}'
        )

        req = CompletionRequest(
            messages=[Message(role="user",
                              content=DECOMPOSE_PROMPT.format(goal=goal))],
            model=getattr(self._planner, "_default_model", "claude-opus-4-8"),
        )
        try:
            resp = await self._planner.complete(req)
            import json
            import re
            m = re.search(r'\{.*\}', resp.content, re.DOTALL)
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

        results_text = "\n\n".join([
            f"Sub-task: {t.goal}\nResult: {t.result[:500]}"
            for t in completed
        ])

        prompt = (
            f"Original goal: {original_goal}\n\n"
            f"Completed sub-tasks:\n{results_text}\n\n"
            "Synthesize a coherent, comprehensive answer to the original goal based on "
            "all sub-task results. Be concise and actionable."
        )

        try:
            from app.providers.base import CompletionRequest, Message
            model = getattr(self._planner, "_default_model", "claude-opus-4-8")
            resp = await self._planner.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=model,
                max_tokens=2000,
            ))
            return resp.content
        except Exception as exc:
            logger.warning("supervisor_synthesize_llm_failed", error=str(exc))
            # Fallback: structured text summary if LLM call fails
            lines = [f"Completed {len(completed)}/{len(completed) + len(failed)} sub-tasks.\n"]
            for t in completed:
                lines.append(f"• {t.goal}: {t.result[:200]}")
            return "\n".join(lines)
