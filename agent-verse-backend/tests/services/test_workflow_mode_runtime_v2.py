"""P5-2: workflow_mode=supervisor|debate actually runs on the strategy runtime v2.

P0 baseline §4.5: with runtime v2 on for every tenant, ``workflow_mode`` set only the
legacy agent pattern flags (``enable_supervisor`` / ``enable_debate``). The v2
profile selected ``react`` and GraphFactory lets an agent config switch a profile
node OFF, never on — so supervisor/debate goals silently ran as plain ReAct
(``patterns: ["react", "self_refine"]``) with no downgrade recorded.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.core import runtime_flags
from app.orchestration.profiled_graph import build_profiled_graph
from app.providers.fake import FakeProvider
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-p5-2", plan=PlanTier.ENTERPRISE, api_key_id="k")
GOAL = "Summarise the quarterly fleet costs for the board"
_FLAG = {"supervisor": "enable_supervisor", "debate": "enable_debate"}


def _v2_for_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    base = runtime_flags.get_runtime_flags()
    patched = replace(
        base,
        dynamic_orchestration=True,
        strategy_runtime_v2_tenant_allowlist=frozenset({"*"}),
        strategy_runtime_v2_shadow=False,
        strategy_runtime_v2_kill_switch=False,
    )
    monkeypatch.setattr(runtime_flags, "get_runtime_flags", lambda: patched)


def _services(**flags: bool) -> dict[str, Any]:
    fake = FakeProvider()
    return {"planner": fake, "executor": fake, "verifier": fake, **flags}


@pytest.mark.parametrize("mode", ["supervisor", "debate"])
async def test_v2_profile_runs_the_requested_workflow_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _v2_for_everyone(monkeypatch)
    monkeypatch.setattr(GoalService, "_coordination_ready", lambda self: True)
    data = await GoalService()._build_runtime_profile(
        GOAL, goal_id=f"g-{mode}", tenant_ctx=TENANT, workflow_mode=mode
    )
    profile = data["profile_object"]
    assert profile is not None, data["context"]
    assert profile.primary_strategy.strategy_id == mode
    assert data["context"].get("strategy_downgraded") is not True

    # Worker with no StrategyRunner: the same pattern still runs as the local node.
    _graph, execution = build_profiled_graph(
        profile, _services(), {_FLAG[mode]: True}, distributed_loop_builder=lambda: None
    )
    assert mode in execution["patterns"], execution

    # With a StrategyRunner, the runner drives exactly this pattern.
    runner = object()
    graph, execution = build_profiled_graph(
        profile, _services(), {_FLAG[mode]: True}, distributed_loop_builder=lambda: runner
    )
    assert graph is runner
    assert execution["patterns"] == [mode]


@pytest.mark.parametrize("mode", ["supervisor", "debate"])
async def test_without_coordination_the_legacy_kernel_runs_the_mode_not_react(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _v2_for_everyone(monkeypatch)
    monkeypatch.setattr(GoalService, "_coordination_ready", lambda self: False)
    data = await GoalService()._build_runtime_profile(
        GOAL, goal_id=f"g-legacy-{mode}", tenant_ctx=TENANT, workflow_mode=mode
    )
    # The v2 profile cannot host the mode here: the legacy kernel (which honours the
    # agent pattern flags) runs it, and the fallback is recorded — never a bare react.
    assert data["profile_object"] is None
    fallback = data["context"]["runtime_profile_fallback"]
    assert fallback["requested_workflow_mode"] == mode
    _graph, execution = build_profiled_graph(None, _services(**{_FLAG[mode]: True}), None)
    assert mode in execution["patterns"]


async def test_mode_conflicting_with_an_explicit_override_is_recorded_as_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _v2_for_everyone(monkeypatch)
    monkeypatch.setattr(GoalService, "_coordination_ready", lambda self: True)
    data = await GoalService()._build_runtime_profile(
        GOAL,
        goal_id="g-conflict",
        tenant_ctx=TENANT,
        agent_config={"primary_strategy": "plan_execute"},
        workflow_mode="supervisor",
    )
    profile = data["profile_object"]
    assert profile is not None and profile.primary_strategy.strategy_id == "plan_execute"
    ctx = data["context"]
    assert ctx["strategy_downgraded"] is True
    assert ctx["strategy_downgrade"]["requested_strategy"] == "supervisor"
    assert ctx["strategy_downgrade"]["reason"] == "workflow_mode_conflicts_with_strategy_override"


async def test_submit_goal_passes_workflow_mode_into_the_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _v2_for_everyone(monkeypatch)
    monkeypatch.setattr(GoalService, "_coordination_ready", lambda self: True)
    svc = GoalService()
    out = await svc.submit_goal(GOAL, "normal", True, TENANT, workflow_mode="supervisor")
    record = svc._goals[out["goal_id"]]
    assert record.execution_context["runtime_profile"]["primary_strategy"]["strategy_id"] == (
        "supervisor"
    )
    assert record.runtime_profile is not None
    assert record.runtime_profile.primary_strategy.strategy_id == "supervisor"
