"""Makes a DISTRIBUTED-tier ``GoalRuntimeProfile`` executable via ``StrategyRunner``.

``GraphFactory`` (see ``graph_factory.py``) refuses to compile a DISTRIBUTED-tier profile —
that tier's strategies (``supervisor``, ``debate``, ``goal_tree``, ...) are not LangGraph
nodes, they are durable coordination adapters meant to run through ``StrategyRunner``. Before
this module, nothing ever called ``StrategyRunner.run()``, so a DISTRIBUTED profile could only
end in the local-kernel fallback or a failed goal (see D-1 finding).

``DistributedStrategyLoop`` presents the same ``async def run(goal=..., tenant_ctx=...,
initial_context=..., event_callback=..., goal_id=...)`` surface that ``AgentGraph.run()``
exposes, so ``GoalService`` can treat it as a drop-in replacement: build one in
``_make_agent_loop_for_tenant`` when the profile is DISTRIBUTED and a wired
``StrategyRunner`` is available, then let the existing event-driven goal lifecycle
(``goal_complete`` / ``goal_failed`` handling in ``GoalService._dispatch_event``) take over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import ExecutionTerminalState, StrategyExecutionRequest

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.orchestration.strategy_runner import StrategyRunner
    from app.tenancy.context import TenantContext

# Terminal states that represent the strategy genuinely running and producing (or failing to
# produce) an answer, as opposed to being rejected before execution started.
_FAILURE_REASON_BY_STATE = {
    ExecutionTerminalState.FAILED: "Strategy execution failed.",
    ExecutionTerminalState.CANCELLED: "Strategy execution was cancelled.",
    ExecutionTerminalState.DEADLINE_EXCEEDED: "Strategy execution exceeded its deadline.",
    ExecutionTerminalState.LIMIT_EXCEEDED: "Strategy execution exceeded a bounded limit.",
    ExecutionTerminalState.POLICY_DENIED: "Strategy execution was denied by policy.",
}


@dataclass
class DistributedStrategyLoop:
    """AgentGraph-shaped adapter that dispatches a goal through ``StrategyRunner``.

    Deliberately *not* frozen/slotted: ``GoalService._make_agent_loop_for_tenant`` sets extra
    attributes on whatever it returns (e.g. ``loop._db_session_factory = ...``,
    ``loop._agent_collection_ids = ...``) the same way it does for a real ``AgentGraph``. A
    plain dataclass keeps a normal instance ``__dict__`` so those assignments keep working.
    """

    strategy_runner: StrategyRunner
    context_store: StrategyGoalContextStore
    profile: GoalRuntimeProfile
    provider: Any
    agent_id: str | None = None

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: TenantContext,
        initial_context: dict[str, Any] | None = None,
        event_callback: Any = None,
        goal_id: str | None = None,
        **_ignored_agent_graph_kwargs: Any,
    ) -> dict[str, Any]:
        profile = self.profile
        resolved_goal_id = goal_id or profile.goal_id
        context_snapshot_ref = f"strategy-goal-context://{tenant_ctx.tenant_id}:{resolved_goal_id}"

        await self.context_store.put(
            context_snapshot_ref,
            StrategyGoalContext(
                goal_text=goal,
                provider=self.provider,
                initial_context=initial_context or {},
            ),
        )
        try:
            deadline = profile.deadline
            if deadline is None or deadline <= datetime.now(UTC):
                deadline = datetime.now(UTC) + timedelta(
                    seconds=profile.effective_limits.duration_seconds
                )
            request = StrategyExecutionRequest(
                tenant_id=tenant_ctx.tenant_id,
                goal_id=resolved_goal_id,
                strategy_id=profile.primary_strategy.strategy_id,
                adapter_version=profile.primary_strategy.adapter_version,
                state_schema_version=1,
                agent_id=self.agent_id or "unassigned",
                runtime_profile_ref=profile.profile_id,
                context_snapshot_ref=context_snapshot_ref,
                policy_ref=profile.policy_snapshot_ref,
                budget_ref=profile.budget_snapshot_ref,
                cancellation_token=resolved_goal_id,
                deadline=deadline,
                idempotency_key=profile.profile_id,
            )
            result = await self.strategy_runner.run(request, profile.effective_limits)
        finally:
            await self.context_store.discard(context_snapshot_ref)

        if event_callback is not None:
            if result.terminal_state is ExecutionTerminalState.SUCCEEDED:
                await event_callback(
                    {
                        "type": "goal_complete",
                        "answer": result.answer,
                        "strategy_id": profile.primary_strategy.strategy_id,
                        "execution_tier": "distributed",
                    }
                )
            else:
                reason = _FAILURE_REASON_BY_STATE.get(
                    result.terminal_state, "Strategy execution stopped safely."
                )
                reason_codes = ", ".join(result.trace_summary.reason_codes)
                await event_callback(
                    {
                        "type": "goal_failed",
                        "reason": f"{reason} ({reason_codes})" if reason_codes else reason,
                        "strategy_id": profile.primary_strategy.strategy_id,
                        "execution_tier": "distributed",
                    }
                )
        return {
            "terminal_state": result.terminal_state.value,
            "answer": result.answer,
            "cost_usd": result.cost_usd,
        }


__all__ = ["DistributedStrategyLoop"]
