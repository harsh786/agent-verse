"""VOYAGER-CRASH: registered strategies can be built through the runner contract.

The StrategyRunner hands its executor ``adapter.create_runtime`` and the
executor builds the runtime as ``create_runtime(checkpoint_store=...)``. The
voyager adapter forwarded those kwargs to ``VoyagerRuntime``, which also
requires ``skill_store`` — so building it raised ``TypeError`` (the strategy is
registered as implemented, with a skill-learning adapter that could not be
constructed). These tests pin:

* voyager builds through the registry descriptor with the executor's kwargs
  and runs a trivial goal end-to-end with fakes (skill published);
* every strategy the runner ADMITS has a driver: the coordination patterns go
  to the executor's coordination pattern bridge (whose pattern table must name
  exactly the same strategies), every other one builds the same way and runs a
  trivial goal with a fake provider;
* the only registered DISTRIBUTED strategies that cannot be built that way are
  a known set that is either a bridged coordination pattern or denied at
  admission (group_chat) — so nothing is admitted that nothing can run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.memory.voyager_skills import VoyagerSkillStore
from app.orchestration.execution_drivers import COORDINATION_PATTERN_STRATEGIES
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_executor import (
    SUPPORTED_DISTRIBUTED_STRATEGIES,
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.fake import FakeProvider

# Registered DISTRIBUTED adapters whose runtimes need infrastructure the generic
# executor does not supply (transcripts, ledgers, repositories, an allocator
# with no constructor args). They run through the coordination pattern bridge
# or are denied at admission; see below.
_NEEDS_EXTRA_RUNTIME_DEPENDENCIES = frozenset(
    {"camel", "group_chat", "magentic", "mixture_of_agents", "market_auction"}
)


def _request(strategy_id: str) -> StrategyExecutionRequest:
    return StrategyExecutionRequest.model_validate(
        {
            "tenant_id": "tenant-1",
            "goal_id": f"goal-{strategy_id}",
            "strategy_id": strategy_id,
            "adapter_version": "1.0.0",
            "state_schema_version": 1,
            "agent_id": "agent-1",
            "runtime_profile_ref": "profile-1",
            "context_snapshot_ref": f"context-{strategy_id}",
            "policy_ref": "policy-1",
            "budget_ref": "budget-1",
            "cancellation_token": f"cancel-{strategy_id}",
            "deadline": datetime.now(UTC) + timedelta(minutes=1),
            "idempotency_key": f"idem-{strategy_id}",
        }
    )


def _limits() -> PatternLimits:
    return PatternLimits.model_validate(
        {
            "calls": 32, "nodes": 32, "edges": 32, "depth": 8, "fan_out": 8,
            "rounds": 16, "tokens": 20_000, "duration_seconds": 30, "cost_usd": 5.0,
        }
    )


def _distributed_strategy_ids() -> list[str]:
    registry = build_default_registry()
    return sorted(
        c.strategy_id
        for c in registry.list_all()
        if c.adapter_descriptor is not None
        and c.adapter_descriptor.execution_tier is ExecutionTier.DISTRIBUTED
    )


def _build_with_executor_contract(strategy_id: str) -> Any:
    capability = build_default_registry().get(strategy_id)
    assert capability is not None and capability.adapter_descriptor is not None
    adapter = capability.adapter_descriptor.create_adapter()
    return adapter.create_runtime(checkpoint_store=InMemoryPatternCheckpointStore())


# ── voyager ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voyager_builds_via_executor_contract_and_runs_a_trivial_goal() -> None:
    runtime = _build_with_executor_contract("voyager")

    async def run_task(gap: str) -> dict[str, str]:
        return {"evidence_ref": f"evidence://{gap}"}

    state = await runtime.execute(
        session_id="s",
        execution_id="e",
        tenant_id="tenant-1",
        capability_gaps=("search",),
        run_task=run_task,
        synthesize_skill=lambda *_: {
            "procedure_id": "skill",
            "skill_version": "v1",
            "tool_sequence": ("search",),
            "required_capabilities": frozenset({"research"}),
            "tool_schema_versions": {"search": "v1"},
            "connector_ids": frozenset({"docs"}),
            "policy_fingerprint": "p1",
        },
        maximum_tasks=1,
        publication_context={
            "available_tools": {"search": "v1"},
            "allowed_capabilities": frozenset({"research"}),
            "ready_connectors": frozenset({"docs"}),
            "policy_fingerprint": "p1",
        },
    )
    assert state.phase == "completed"
    assert state.published_skill_id == "skill"


def test_voyager_uses_an_injected_skill_store() -> None:
    from app.agent.patterns.voyager import VoyagerAdapter

    store = VoyagerSkillStore()
    runtime = VoyagerAdapter().create_runtime(
        checkpoint_store=InMemoryPatternCheckpointStore(), skill_store=store
    )
    assert runtime._skills is store


def test_voyager_adapters_share_one_skill_library() -> None:
    """Skill versions are immutable per (tenant, procedure, version): two runs
    must publish into the same library, not each into a throwaway one."""
    a = _build_with_executor_contract("voyager")
    b = _build_with_executor_contract("voyager")
    assert a._skills is b._skills


# ── every admitted strategy ───────────────────────────────────────────────────

_FAKE_RESPONSES: dict[str, list[str]] = {
    "supervisor": ['{"steps": [{"id": "s1", "summary": "do it"}]}', "done", "final: done"],
    "goal_tree": ['{"steps": [{"id": "s1", "summary": "do it"}]}', "done", "final: done"],
    "debate": ["proposal", "critique", "proposer-a"],
    "voyager": ['{"steps": [{"id": "s1", "summary": "greet"}]}', "hello", "final: hello"],
}


class _AsyncSkillLibrary:
    """Stand-in for PostgresVoyagerSkillStore (validates, records provenance)."""

    def __init__(self) -> None:
        self.published: list[tuple[Any, dict[str, Any]]] = []

    async def publish(self, skill: Any, *, provenance: Any = None, **ctx: Any) -> Any:
        from app.memory.procedural_validator import validate_procedure

        validate_procedure(skill, tenant_id=skill.tenant_id, **ctx)
        self.published.append((skill, dict(provenance or {})))
        return skill


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy_id", sorted(SUPPORTED_DISTRIBUTED_STRATEGIES - COORDINATION_PATTERN_STRATEGIES)
)
async def test_every_admitted_strategy_builds_and_runs_a_trivial_goal(strategy_id: str) -> None:
    _build_with_executor_contract(strategy_id)  # constructible with the executor kwargs

    context_store = StrategyGoalContextStore()
    await context_store.put(
        f"context-{strategy_id}",
        StrategyGoalContext(
            goal_text="Say hello", provider=FakeProvider(responses=_FAKE_RESPONSES[strategy_id])
        ),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(
            context_store=context_store, skill_store=_AsyncSkillLibrary()
        ),
        admission=default_distributed_admission,
    )
    result = await runner.run(_request(strategy_id), _limits())
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert result.answer


@pytest.mark.parametrize("strategy_id", _distributed_strategy_ids())
def test_registered_distributed_strategy_builds_or_is_never_admitted(strategy_id: str) -> None:
    admitted, _ = default_distributed_admission(_request(strategy_id))
    if strategy_id in _NEEDS_EXTRA_RUNTIME_DEPENDENCIES:
        assert not admitted or strategy_id in COORDINATION_PATTERN_STRATEGIES, (
            f"{strategy_id} is admitted but neither constructible nor bridged"
        )
        with pytest.raises(TypeError):
            _build_with_executor_contract(strategy_id)
        return
    assert _build_with_executor_contract(strategy_id) is not None


def test_bridged_strategies_match_the_coordination_pattern_table() -> None:
    """The runner's coordination set and the pattern runtime's table must name the
    same strategies, or a selected goal reaches a bridge that cannot run it."""
    from app.coordination.pattern_runs.service import PATTERNS

    assert set(PATTERNS) == set(COORDINATION_PATTERN_STRATEGIES)
    assert COORDINATION_PATTERN_STRATEGIES <= SUPPORTED_DISTRIBUTED_STRATEGIES
    assert "group_chat" not in SUPPORTED_DISTRIBUTED_STRATEGIES  # no goal driver exists


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", sorted(COORDINATION_PATTERN_STRATEGIES))
async def test_every_bridged_strategy_reaches_the_pattern_bridge(strategy_id: str) -> None:
    from app.orchestration.strategy_runner import ExecutionMetrics, StrategyRunOutput

    seen: list[str] = []

    class _Bridge:
        async def run(self, request: Any, context: Any, cancelled: Any) -> Any:
            seen.append(request.strategy_id)
            return StrategyRunOutput(
                answer="bridged",
                metrics=ExecutionMetrics(calls=1, tokens=1, cost_usd=0.0),
                safe_rationale_summary="fake bridge",
            )

    context_store = StrategyGoalContextStore()
    await context_store.put(
        f"context-{strategy_id}",
        StrategyGoalContext(goal_text="Say hello", provider=FakeProvider(responses=["x"])),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store, pattern_bridge=_Bridge()),
        admission=default_distributed_admission,
    )
    result = await runner.run(_request(strategy_id), _limits())
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert seen == [strategy_id]
