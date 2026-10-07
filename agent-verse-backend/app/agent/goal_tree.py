"""Goal-tree decomposition and parallel sub-agent execution.

The GoalTreeExecutor:
1. Asks an LLM to decompose the goal into sub-goals
2. Builds a dependency graph
3. Executes independent sub-goals in parallel (asyncio.gather)
4. Executes dependent sub-goals sequentially
5. Returns aggregated results for the parent verifier

On a worker with a durable ledger the tree runs as a continuation instead
(:func:`advance_goal_tree`, a01-F007-01 / a01-F006-05): each child is a real goal
(``goals`` row with ``parent_goal_id``, its own events, checkpoints, cancel and
HITL), dispatched wave by wave as its dependencies succeed; the parent parks in
``waiting_children`` between waves and is re-queued by the last child to finish.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
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
from app.agent.state import AgentState, GoalStatus, SubGoal
from app.ai_router.role_preference import resolve_role_model
from app.providers.base import CompletionRequest, LLMProvider, Message
from app.tenancy.context import TenantContext


def _sub_goal_from_entry(entry: LedgerEntry, parent_goal_id: str) -> SubGoal:
    """A planned child rebuilt from the durable ledger (finished ones keep their outcome)."""
    status = {"complete": GoalStatus.COMPLETE, "failed": GoalStatus.FAILED}.get(
        entry.status, GoalStatus.PLANNING
    )
    deps = entry.spec.get("depends_on")
    return SubGoal(
        sub_goal_id=entry.task_key,
        description=str(entry.spec.get("description", "")),
        parent_goal_id=parent_goal_id,
        depends_on=[str(d) for d in deps] if isinstance(deps, list) else [],
        status=status,
        result=entry.result,
        error=entry.error,
    )


@dataclass
class DecompositionResult:
    should_decompose: bool
    sub_goals: list[SubGoal] = field(default_factory=list)


async def decompose_goal(
    goal: str,
    planner: LLMProvider,
    tenant_ctx: TenantContext,
    parent_goal_id: str,
    model: str = "",
) -> DecompositionResult:
    """Ask the planner LLM whether to decompose and how.

    ``model`` is the graph's planning model; the hard-coded platform default is
    only a last resort (it was always used, so a tenant/role-routed planner got a
    model id its provider may not even serve).
    """
    from app.agent.prompts import GOAL_TREE_SYSTEM  # lazy to avoid import cycles

    req = CompletionRequest(
        messages=[
            Message(role="system", content=GOAL_TREE_SYSTEM),
            Message(role="user", content=f"Goal: {goal}"),
        ],
        # The graph's planning model; else the goal_tree role's own resolution
        # (saved order > env pin > the planner's model) — never a hardcoded slug.
        model=model or resolve_role_model("goal_tree", provider=planner),
    )
    from app.providers.guarded_completion import complete_decision

    resp = await complete_decision(
        planner, req, role="goal_tree", tenant_ctx=tenant_ctx, goal_id=parent_goal_id
    )
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
    event_callback: Any = None,
) -> SubGoal:
    """Execute a single sub-goal using a spawned AgentGraph instance."""
    async with semaphore:
        try:
            graph = graph_factory()
            parent_goal_id = str(sub_goal.parent_goal_id or "")
            # The child runs under its own id (its own checkpoint thread — unique
            # per execution, so a replanned parent never resumes a stale child),
            # derived from the parent so its records trace back to it; its LLM
            # spend is charged to the PARENT goal's budget (_budget_goal_id). It
            # used to run with no goal id at all: a random one, so the spend hit
            # an unrelated per-goal budget and was not attributable.
            child_goal_id = (
                f"{parent_goal_id}-{sub_goal.sub_goal_id}-{uuid.uuid4().hex[:8]}"
                if parent_goal_id
                else None
            )
            initial_context: dict[str, Any] = {"sub_goal_id": sub_goal.sub_goal_id}
            if parent_goal_id:
                initial_context["parent_goal_id"] = parent_goal_id
                initial_context["_budget_goal_id"] = parent_goal_id
                # Stable across re-runs of this child (the goal id above is not):
                # the action ledger and idempotency keys use it, so a child re-run
                # after a crash does not repeat its side effects (a01-F007-01).
                initial_context["_action_scope_id"] = f"{parent_goal_id}:{sub_goal.sub_goal_id}"
            state: AgentState = await graph.run(
                goal=sub_goal.description,
                tenant_ctx=tenant_ctx,
                event_callback=event_callback,
                goal_id=child_goal_id,
                initial_context=initial_context,
            )
            sub_goal.status = state.status
            sub_goal.provenance = list(state.provenance)
            sub_goal.retrieval_trace = list(state.context.get("rag_strategy_trace", []))
            sub_goal.events = list(state.events)
            if state.status is GoalStatus.FAILED:
                sub_goal.error = state.error_message or "Child goal failed"
                sub_goal.result = ""
            else:
                sub_goal.result = "\n".join(f"[{s.description}]: {s.output}" for s in state.steps)
        except Exception as exc:
            sub_goal.status = GoalStatus.FAILED
            sub_goal.error = str(exc)
    return sub_goal


async def _synthesize_goal_tree_results(
    original_goal: str,
    sub_results: list[dict[str, Any]],
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

        results_text = "\n\n".join(
            [
                f"Sub-task: {r['goal']}\nResult: {r['result'][:400]}"
                for r in sub_results
                if r.get("success")
            ]
        )
        prompt = (
            f"Original goal: {original_goal}\n\n"
            f"Sub-task results:\n{results_text}\n\n"
            "Synthesize a clear, concise, actionable answer to the original goal "
            "based on all sub-task results. Be specific."
        )
        from app.providers.guarded_completion import (
            complete_decision,
            generation_timeout_seconds,
        )

        model = resolve_role_model("goal_tree_synthesis", provider=provider)
        resp = await complete_decision(
            provider,
            CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=model,
                max_tokens=2000,
            ),
            role="goal_tree_synthesis",
            timeout_seconds=generation_timeout_seconds(),
        )
        return str(resp.content)
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
    event_callback: Any = None,
    max_parallel: int = 4,
    model: str = "",
    ledger: FanoutLedger | None = None,
) -> list[SubGoal]:
    """Decompose goal → build dependency DAG → execute with parallelism.

    Returns list of completed SubGoal objects in topological order.
    The final element (when sub-goals succeed) is a synthesis SubGoal whose
    ``result`` contains the LLM-synthesized answer to the original goal.
    """
    # CORE-10: with a ledger (persisted parent + Postgres) the decomposition and
    # every child's completion are durable, so a parent redelivered after a crash
    # reuses the stored plan and never re-runs a child that already finished.
    entries = await ledger.load() if ledger is not None else []
    if entries:
        planned = [_sub_goal_from_entry(e, parent_goal_id) for e in entries]
    else:
        decomp = await decompose_goal(goal, planner, tenant_ctx, parent_goal_id, model=model)
        if not decomp.should_decompose or not decomp.sub_goals:
            return []
        planned = decomp.sub_goals
        if ledger is not None:
            # Durable before any child runs; raises (no child runs) otherwise.
            stored = await ledger.plan(
                [
                    LedgerEntry(
                        task_key=sg.sub_goal_id,
                        position=i,
                        spec={"description": sg.description, "depends_on": sg.depends_on},
                    )
                    for i, sg in enumerate(planned)
                ]
            )
            planned = [_sub_goal_from_entry(e, parent_goal_id) for e in stored]

    async def _record(sg: SubGoal) -> None:
        if ledger is None:
            return
        failed = sg.status is GoalStatus.FAILED or bool(sg.error)
        try:
            await ledger.mark_finished(
                sg.sub_goal_id,
                status="failed" if failed else "complete",
                result=sg.result,
                error=sg.error[:2000],
            )
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "goal_tree_ledger_write_failed: %s", type(exc).__name__
            )

    semaphore = asyncio.Semaphore(max_parallel)
    succeeded: set[str] = set()
    not_succeeded: set[str] = set()
    results: list[SubGoal] = []

    # Children that finished before a crash: reuse, never re-run.
    remaining = []
    for sg in planned:
        if sg.status in (GoalStatus.COMPLETE, GoalStatus.FAILED):
            (not_succeeded if sg.status is GoalStatus.FAILED else succeeded).add(sg.sub_goal_id)
            results.append(sg)
        else:
            remaining.append(sg)

    # Topological execution: process waves of ready sub-goals
    max_waves = len(remaining) + 1
    wave = 0

    while remaining and wave < max_waves:
        wave += 1
        # A sub-goal whose dependency failed (or was itself skipped) must not run
        # on missing inputs: every finished sub-goal used to count as "completed"
        # regardless of status, so dependents ran anyway.
        for sg in list(remaining):
            blocked = [dep for dep in sg.depends_on if dep in not_succeeded]
            if blocked:
                sg.status = GoalStatus.FAILED
                sg.error = f"skipped: dependency {', '.join(blocked)} did not succeed"
                not_succeeded.add(sg.sub_goal_id)
                results.append(sg)
                remaining.remove(sg)
                await _record(sg)
        if not remaining:
            break
        # Find all sub-goals whose dependencies all succeeded
        ready = [sg for sg in remaining if all(dep in succeeded for dep in sg.depends_on)]
        if not ready:
            # Circular dependency or unknown dependency ids — execute all remaining
            # (none of them depends on a failed sub-goal; checked above).
            ready = remaining[:]

        # Execute ready sub-goals in parallel (bounded by semaphore)
        async def _run_and_record(sg: SubGoal) -> SubGoal:
            out = await execute_sub_goal(
                sg,
                tenant_ctx=tenant_ctx,
                graph_factory=graph_factory,
                semaphore=semaphore,
                event_callback=event_callback,
            )
            await _record(out)  # as soon as THIS child finishes
            return out

        tasks = [_run_and_record(sg) for sg in ready]
        done: list[SubGoal] = list(await asyncio.gather(*tasks, return_exceptions=False))

        for sg in done:
            if sg.status is not GoalStatus.FAILED and not sg.error:
                succeeded.add(sg.sub_goal_id)
            else:
                not_succeeded.add(sg.sub_goal_id)
            results.append(sg)
            if sg in remaining:
                remaining.remove(sg)

    # Always produce a terminal synthesis record.  Partial failures are evidence
    # the parent verifier must see, rather than an alternate return shape.
    sub_results = [
        {
            "goal": sg.description,
            "result": sg.result or sg.error or "",
            "success": sg.status is not GoalStatus.FAILED and not bool(sg.error),
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
        # A tree where no sub-goal succeeded produced nothing to synthesize.
        status=GoalStatus.COMPLETE if succeeded else GoalStatus.FAILED,
        error="" if succeeded else "no sub-goal succeeded",
    )
    results.append(synthesis_sg)

    return results


def goal_tree_child_outcome(
    goal_status: str, error_message: str, events: list[dict[str, Any]]
) -> tuple[str, str, str]:
    """(status, result, error) of a finished goal-tree child goal.

    The result has the shape the in-process child produced: one
    ``[step]: output`` line per executed step.
    """
    if goal_status != "complete":
        return "failed", "", failure_reason(goal_status, error_message, events)
    lines: list[str] = []
    for raw in events:
        payload = raw.get("payload")
        evt = payload if isinstance(payload, dict) else raw
        if evt.get("type") == "step_complete":
            lines.append(f"[{evt.get('step', '')}]: {evt.get('output', '')}")
    return "complete", "\n".join(lines), ""


@dataclass
class GoalTreeAdvance:
    """One continuation step of a durable goal tree."""

    # The parent dispatched (or still has) running children: park, do not finish.
    parked: bool = False
    # Planned children still unfinished (parked) — 0 when finished.
    pending: int = 0
    # When finished: every child in PLAN order, then the synthesis record.
    sub_goals: list[SubGoal] = field(default_factory=list)


async def advance_goal_tree(
    goal: str,
    *,
    planner: LLMProvider,
    tenant_ctx: TenantContext,
    parent_goal_id: str,
    goal_service: Any,
    ledger: FanoutLedger,
    agent_id: str | None = None,
    event_callback: Any = None,
    max_parallel: int = 4,
    model: str = "",
    child_timeout_s: float | None = None,
) -> GoalTreeAdvance:
    """Decompose once, then dispatch ready children as real goals; never wait on one.

    Each call (first run or a re-entry after the parent was re-queued) folds the
    children that finished into the ledger, fails dependents of a failed child,
    dispatches the children whose dependencies all succeeded (bounded by
    ``max_parallel`` in flight) and returns ``parked=True`` while any child is
    unfinished. Once every child is terminal it synthesizes and returns the
    results in plan order. Re-entry is idempotent: a dispatched child is
    re-attached (``find_child_goal``), never resubmitted.
    """

    async def emit(event: dict[str, Any]) -> None:
        if event_callback is not None:
            try:
                await event_callback(event)
            except Exception:  # an observer never breaks the tree
                import logging as _logging

                _logging.getLogger(__name__).debug("goal_tree_event_emit_failed")

    entries = await ledger.load()
    if not entries:
        decomp = await decompose_goal(goal, planner, tenant_ctx, parent_goal_id, model=model)
        if not decomp.should_decompose or not decomp.sub_goals:
            return GoalTreeAdvance()
        # Durable before any child is dispatched; raises (nothing dispatched) otherwise.
        entries = await ledger.plan(
            [
                LedgerEntry(
                    task_key=sg.sub_goal_id,
                    position=i,
                    spec={"description": sg.description, "depends_on": sg.depends_on},
                )
                for i, sg in enumerate(decomp.sub_goals)
            ]
        )
        await emit({"type": "goal_tree_decomposed", "sub_goal_count": len(entries)})

    for done in await reconcile_children(ledger, entries, goal_tree_child_outcome):
        await emit(
            {
                "type": "sub_goal_complete",
                "sub_goal_id": done.task_key,
                "goal_id": done.child_goal_id,
                "status": done.status,
            }
        )

    def _deps(entry: LedgerEntry) -> list[str]:
        raw = entry.spec.get("depends_on")
        return [str(d) for d in raw] if isinstance(raw, list) else []

    # A child whose dependency failed (or was itself skipped) never runs on
    # missing inputs; repeat until no new skip appears (skips cascade).
    changed = True
    while changed:
        changed = False
        status = {e.task_key: e.status for e in entries}
        for entry in entries:
            if entry.status != "planned":
                continue
            blocked = [d for d in _deps(entry) if status.get(d) == "failed"]
            if blocked:
                entry.status = "failed"
                entry.error = f"skipped: dependency {', '.join(blocked)} did not succeed"
                await ledger.mark_finished(entry.task_key, status="failed", error=entry.error)
                changed = True

    status = {e.task_key: e.status for e in entries}
    planned = [e for e in entries if e.status == "planned"]
    in_flight = [e for e in entries if e.status == "dispatched"]
    ready = [e for e in planned if all(status.get(d) == "complete" for d in _deps(e))]
    if not ready and not in_flight and planned:
        # Circular / unknown dependency ids: run what remains (none of it depends
        # on a failed child — checked above), as the in-process tree does.
        ready = planned[:]
    free = max(0, max(1, max_parallel) - len(in_flight))
    timeout = child_timeout_seconds(default=child_timeout_s, tenant_ctx=tenant_ctx)
    for entry in ready[:free]:
        await _dispatch_tree_child(
            entry,
            goal_service=goal_service,
            ledger=ledger,
            tenant_ctx=tenant_ctx,
            parent_goal_id=parent_goal_id,
            agent_id=agent_id,
            timeout_s=timeout,
            emit=emit,
        )

    pending = [e for e in entries if not e.finished]
    if pending:
        await emit(
            {
                "type": "goal_tree_waiting_children",
                "pending": len(pending),
                "goal_ids": [e.child_goal_id for e in pending if e.child_goal_id],
            }
        )
        return GoalTreeAdvance(parked=True, pending=len(pending))

    results = [_sub_goal_from_entry(e, parent_goal_id) for e in entries]  # plan order
    sub_results = [
        {
            "goal": sg.description,
            "result": sg.result or sg.error or "",
            "success": sg.status is not GoalStatus.FAILED and not bool(sg.error),
        }
        for sg in results
    ]
    succeeded = any(r["success"] for r in sub_results)
    synthesized_text = await _synthesize_goal_tree_results(
        original_goal=goal, sub_results=sub_results, provider=planner
    )
    results.append(
        SubGoal(
            sub_goal_id="synthesis",
            description="Synthesized result",
            parent_goal_id=parent_goal_id,
            depends_on=[sg.sub_goal_id for sg in results],
            result=synthesized_text,
            status=GoalStatus.COMPLETE if succeeded else GoalStatus.FAILED,
            error="" if succeeded else "no sub-goal succeeded",
        )
    )
    return GoalTreeAdvance(sub_goals=results)


async def _dispatch_tree_child(
    entry: LedgerEntry,
    *,
    goal_service: Any,
    ledger: FanoutLedger,
    tenant_ctx: TenantContext,
    parent_goal_id: str,
    agent_id: str | None,
    timeout_s: float,
    emit: Any,
) -> None:
    """Submit (or re-attach) one goal-tree child as a real sub-goal with its deadline."""
    import logging as _logging

    from app.agent.supervisor import SUBGOAL_MARKER

    log = _logging.getLogger(__name__)
    try:
        found = entry.child_goal_id or await ledger.find_child_goal(entry.task_key)
        if found:
            goal_id = str(found)
        else:
            sub = await goal_service.submit_goal(
                goal=str(entry.spec.get("description", "")),
                priority="normal",
                dry_run=False,
                tenant_ctx=tenant_ctx,
                # The child acts as its parent's agent (its grants, connectors).
                agent_id=agent_id,
                execution_context={
                    SUBGOAL_MARKER: parent_goal_id,
                    FANOUT_TASK_KEY: entry.task_key,
                    FANOUT_KIND_KEY: "goal_tree",
                    # Never auto-routed to another agent: the in-process child
                    # always ran as its parent's agent (or none).
                    "routing_decision": {
                        "mode": "parent_agent",
                        "reason": "goal-tree sub-goal runs as its parent's agent",
                    },
                },
            )
            goal_id = str(sub["goal_id"])
            if goal_id == str(parent_goal_id):
                raise RuntimeError("sub-goal resolved to the parent goal")
    except Exception as exc:
        entry.status, entry.error = "failed", f"sub-goal could not be dispatched: {exc}"[:2000]
        try:
            await ledger.mark_finished(entry.task_key, status="failed", error=entry.error)
        except Exception as write_exc:
            log.warning("goal_tree_ledger_write_failed: %s", type(write_exc).__name__)
        return
    entry.child_goal_id, entry.status = goal_id, "dispatched"
    try:
        await ledger.mark_dispatched(entry.task_key, goal_id, timeout_s=timeout_s)
    except Exception as exc:
        # The goals row (parent + task key) still finds it on re-entry.
        log.warning("goal_tree_ledger_dispatch_write_failed: %s", type(exc).__name__)
    await emit(
        {
            "type": "sub_goal_dispatched",
            "sub_goal_id": entry.task_key,
            "goal_id": goal_id,
            "timeout_s": timeout_s,
        }
    )
