"""The Celery worker builds a goal's graph from its persisted runtime profile.

Regression: ``run_goal`` built a plain AgentGraph whatever the goal's persisted
runtime profile and agent pattern flags said. A v2-rollout goal was recorded as
running the profile's strategy that never ran; optional reasoning nodes and the
in-loop supervisor / debate only ever ran in-process; scorecards on worker goals
had no observed profile. The worker now assembles its graph through the same
builder GoalService uses (``build_profiled_graph`` → GraphFactory), records what
actually runs (``strategy_execution``) on the goal — including an honest
downgrade — and hands the observed profile to the scorecard path.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
    StrategySelection,
)
from app.orchestration.strategy_adapters import ExecutionTier


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


def _profile(primary: str, tier: ExecutionTier = ExecutionTier.LOCAL) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g-prof",
        tenant_id="t-prof",
        properties=GoalProperties(raw_goal="write a report"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection(primary, "1.0.0"),
        execution_tier=tier,
    )


def test_profile_snapshot_round_trips() -> None:
    profile = _profile("self_refine")
    again = GoalRuntimeProfile.from_dict(profile.to_dict())
    assert again.to_dict() == profile.to_dict()


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    seen: dict[str, Any] = {"graphs": [], "merged": {}}
    real_graph_cls = graph_mod.AgentGraph

    class _Graph(real_graph_cls):  # type: ignore[misc, valid-type]
        async def run(self, **kwargs: Any) -> Any:
            seen["graphs"].append(self)
            return _State()

    async def _merge(self: Any, goal_id: str, tenant_id: str, key: str, value: Any) -> None:
        seen["merged"][key] = value

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    async def _update(self: Any, *a: Any, **k: Any) -> bool:
        return True

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(GoalService, "_db_merge_context_key", _merge)
    monkeypatch.setattr(GoalService, "_db_ensure_goal_row", _noop)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    class _NoDb:
        """A configured-but-unreachable database: every session open fails."""

        async def __aenter__(self) -> Any:
            raise RuntimeError("no database in this unit test")

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: _NoDb())
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # The profile's hybrid RAG strategy needs an embedder: give the worker one
    # (construction only — the graph run itself is stubbed), whatever the env.
    monkeypatch.setattr(
        "app.core.config.get_provider_env",
        lambda name: "sk-test" if name == "OPENAI_API_KEY" else "",
    )
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)

    def _with_context(ctx: dict[str, Any]) -> None:
        async def _ctx(goal_id: str, tenant_id: str) -> dict[str, Any]:
            return ctx

        monkeypatch.setattr(tasks, "_goal_execution_context", _ctx)

    seen["with_context"] = _with_context
    return seen


def _run() -> dict[str, Any]:
    from app.scaling import tasks

    result: dict[str, Any] = tasks.run_goal.run("g-prof", "t-prof", "write a report", "normal", False)
    return result


def test_v2_goal_runs_the_profiled_strategy_on_the_worker(worker: dict[str, Any]) -> None:
    profile = _profile("self_refine")
    worker["with_context"](
        {"runtime_profile": profile.to_dict(), "strategy_runtime_path": "v2"}
    )

    result = _run()
    assert result["status"] == "complete", result

    graph = worker["graphs"][-1]
    assert graph._enable_self_refine is True
    assert graph.runtime_profile.primary_strategy.strategy_id == "self_refine"
    execution = worker["merged"]["strategy_execution"]
    assert execution["requested_primary"] == "self_refine"
    assert "self_refine" in execution["patterns"]
    assert "downgrades" not in execution
    assert graph._observed_runtime_profile is not None


def test_legacy_goal_keeps_its_agent_pattern_flags_and_observed_profile(
    worker: dict[str, Any],
) -> None:
    profile = _profile("self_refine")
    worker["with_context"](
        {
            "runtime_profile": profile.to_dict(),
            "strategy_runtime_path": "legacy",
            "agent_pattern_flags": {"enable_debate": True, "enable_supervisor": True},
        }
    )

    _run()

    graph = worker["graphs"][-1]
    # The rollout does not let the profile drive: no profiled strategy …
    assert getattr(graph, "runtime_profile", None) is None
    assert graph._enable_self_refine is False
    # … but the agent's own pattern nodes run on the worker too …
    assert graph._enable_debate is True
    assert graph._enable_supervisor is True
    patterns = worker["merged"]["strategy_execution"]["patterns"]
    assert "debate" in patterns and "supervisor" in patterns
    # … and the scorecard still gets the observed profile.
    assert graph._observed_runtime_profile.profile_id == profile.profile_id


def test_distributed_strategy_records_an_honest_downgrade(worker: dict[str, Any]) -> None:
    profile = _profile("debate", ExecutionTier.DISTRIBUTED)
    worker["with_context"](
        {"runtime_profile": profile.to_dict(), "strategy_runtime_path": "v2"}
    )

    _run()

    execution = worker["merged"]["strategy_execution"]
    assert execution["downgrades"][0]["reason"] == "strategy_runner_unavailable"
    assert execution["driver"] == "agent_graph"
    assert "debate" in execution["patterns"]  # the local debate node really runs


def test_unreadable_profile_is_recorded_not_claimed(worker: dict[str, Any]) -> None:
    worker["with_context"](
        {
            "runtime_profile": {"primary_strategy": {"strategy_id": "self_refine"}},
            "strategy_runtime_path": "v2",
        }
    )

    _run()

    execution = worker["merged"]["strategy_execution"]
    assert execution["downgrades"][0]["reason"] == "runtime_profile_unavailable_on_worker"
    assert "self_refine" not in execution["patterns"]


def test_worker_graph_gets_the_tenant_bulkhead(worker: dict[str, Any]) -> None:
    worker["with_context"]({})

    _run()

    assert worker["graphs"][-1]._bulkhead_registry is not None
