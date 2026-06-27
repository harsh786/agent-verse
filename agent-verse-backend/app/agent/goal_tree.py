"""Goal-tree decomposition and parallel sub-agent execution.

The GoalTreeExecutor:
1. Asks an LLM to decompose the goal into sub-goals
2. Builds a dependency graph
3. Executes independent sub-goals in parallel (asyncio.gather)
4. Executes dependent sub-goals sequentially
5. Returns aggregated results for the parent verifier
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.state import AgentState, GoalStatus, SubGoal
from app.providers.base import CompletionRequest, LLMProvider, Message
from app.tenancy.context import TenantContext


@dataclass
class DecompositionResult:
    should_decompose: bool
    sub_goals: list[SubGoal] = field(default_factory=list)


async def decompose_goal(
    goal: str,
    planner: LLMProvider,
    tenant_ctx: TenantContext,
    parent_goal_id: str,
) -> DecompositionResult:
    """Ask the planner LLM whether to decompose and how."""
    from app.agent.prompts import GOAL_TREE_SYSTEM  # lazy to avoid import cycles

    req = CompletionRequest(
        messages=[
            Message(role="system", content=GOAL_TREE_SYSTEM),
            Message(role="user", content=f"Goal: {goal}"),
        ],
        model="claude-opus-4-8",
    )
    resp = await planner.complete(req)
    text = re.sub(r"```(?:json)?\n?", "", resp.content).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return DecompositionResult(should_decompose=False)

    if not obj.get("decompose", False):
        return DecompositionResult(should_decompose=False)

    sub_goals = [
        SubGoal(
            sub_goal_id=sg.get("id", f"sg-{i}"),
            description=sg.get("description", ""),
            parent_goal_id=parent_goal_id,
            depends_on=sg.get("depends_on", []),
        )
        for i, sg in enumerate(obj.get("sub_goals", []))
    ]
    return DecompositionResult(should_decompose=bool(sub_goals), sub_goals=sub_goals)


async def execute_sub_goal(
    sub_goal: SubGoal,
    *,
    tenant_ctx: TenantContext,
    graph_factory: Any,  # Callable[[], AgentGraph] — Any avoids circular import
    semaphore: asyncio.Semaphore,
) -> SubGoal:
    """Execute a single sub-goal using a spawned AgentGraph instance."""
    async with semaphore:
        try:
            graph = graph_factory()
            state: AgentState = await graph.run(
                goal=sub_goal.description,
                tenant_ctx=tenant_ctx,
            )
            sub_goal.status = state.status
            # Aggregate step outputs as the sub-goal result
            sub_goal.result = "\n".join(
                f"[{s.description}]: {s.output}" for s in state.steps
            )
        except Exception as exc:
            sub_goal.status = GoalStatus.FAILED
            sub_goal.error = str(exc)
    return sub_goal


async def _synthesize_goal_tree_results(
    original_goal: str,
    sub_results: list[dict],  # [{"goal": str, "result": str, "success": bool}, ...]
    provider: Any,
) -> str:
    """Synthesize sub-goal results into a coherent final answer using LLM.

    Falls back to joining successful results when no provider is supplied or
    when the LLM call fails.
    """
    import logging as _logging

    if not sub_results or provider is None:
        successful = [r["result"] for r in sub_results if r.get("success")]
        return "\n\n".join(successful) if successful else "All sub-goals failed."

    try:
        from app.providers.base import CompletionRequest, Message

        results_text = "\n\n".join([
            f"Sub-task: {r['goal']}\nResult: {r['result'][:400]}"
            for r in sub_results
            if r.get("success")
        ])
        prompt = (
            f"Original goal: {original_goal}\n\n"
            f"Sub-task results:\n{results_text}\n\n"
            "Synthesize a clear, concise, actionable answer to the original goal "
            "based on all sub-task results. Be specific."
        )
        model = getattr(provider, "_default_model", "")
        resp = await provider.complete(CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=model,
            max_tokens=2000,
        ))
        return resp.content
    except Exception as exc:
        _logging.getLogger(__name__).warning("goal_tree_synthesis_failed: %s", exc)
        successful = [r["result"] for r in sub_results if r.get("success")]
        return "\n\n".join(successful) if successful else "Sub-goal synthesis failed."


async def execute_goal_tree(
    goal: str,
    *,
    planner: LLMProvider,
    tenant_ctx: TenantContext,
    parent_goal_id: str,
    graph_factory: Any,
    max_parallel: int = 4,
) -> list[SubGoal]:
    """Decompose goal → build dependency DAG → execute with parallelism.

    Returns list of completed SubGoal objects in topological order.
    The final element (when sub-goals succeed) is a synthesis SubGoal whose
    ``result`` contains the LLM-synthesized answer to the original goal.
    """
    decomp = await decompose_goal(goal, planner, tenant_ctx, parent_goal_id)
    if not decomp.should_decompose or not decomp.sub_goals:
        return []

    semaphore = asyncio.Semaphore(max_parallel)
    completed: set[str] = set()
    results: list[SubGoal] = []

    # Topological execution: process waves of ready sub-goals
    remaining = list(decomp.sub_goals)
    max_waves = len(remaining) + 1
    wave = 0

    while remaining and wave < max_waves:
        wave += 1
        # Find all sub-goals whose dependencies are already completed
        ready = [
            sg for sg in remaining
            if all(dep in completed for dep in sg.depends_on)
        ]
        if not ready:
            # Circular dependency or impossible — execute all remaining sequentially
            ready = remaining[:]

        # Execute ready sub-goals in parallel (bounded by semaphore)
        tasks = [
            execute_sub_goal(
                sg,
                tenant_ctx=tenant_ctx,
                graph_factory=graph_factory,
                semaphore=semaphore,
            )
            for sg in ready
        ]
        done: list[SubGoal] = list(await asyncio.gather(*tasks, return_exceptions=False))

        for sg in done:
            completed.add(sg.sub_goal_id)
            results.append(sg)
            if sg in remaining:
                remaining.remove(sg)

    # LLM synthesis step: merge sub-goal results into one coherent answer
    sub_results = [
        {
            "goal": sg.description,
            "result": sg.result or sg.error or "",
            "success": not bool(sg.error),
        }
        for sg in results
    ]
    synthesized_text = await _synthesize_goal_tree_results(
        original_goal=goal,
        sub_results=sub_results,
        provider=planner,
    )
    synthesis_sg = SubGoal(
        sub_goal_id="synthesis",
        description="Synthesized result",
        parent_goal_id=parent_goal_id,
        depends_on=[sg.sub_goal_id for sg in results],
        result=synthesized_text,
        status=GoalStatus.COMPLETE,
    )
    results.append(synthesis_sg)

    return results
