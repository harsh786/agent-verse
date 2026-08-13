from __future__ import annotations

from typing import Any

from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.strategy_registry import build_default_registry


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_keys(item) for item in value.values()), set())
    if isinstance(value, (list, tuple)):
        return set().union(*(_keys(item) for item in value), set())
    return set()


def test_zero_shot_cot_resolves_to_one_chain_of_thought_adapter() -> None:
    registry = build_default_registry()
    resolution = registry.resolve("zero_shot_cot")

    assert registry.get("zero_shot_cot") is None
    assert resolution.canonical_id == "chain_of_thought"
    assert registry.resolve_adapter("zero_shot_cot") is registry.resolve_adapter(
        "chain_of_thought"
    )


async def test_alias_preserves_request_and_configuration_in_safe_trace() -> None:
    profile, trace = await RuntimeProfileBuilder().build_with_trace(
        "reason through this carefully",
        tenant_id="tenant-1",
        goal_id="goal-1",
        agent_config={
            "primary_strategy": "zero_shot_cot",
            "ready_strategy_ids": ["chain_of_thought"],
        },
    )

    assert profile.primary_strategy.strategy_id == "chain_of_thought"
    assert [item.strategy_id for item in profile.selected_alternatives].count(
        "chain_of_thought"
    ) == 1
    resolution = next(
        decision
        for decision in trace.to_dict()["decisions"]
        if decision["dimension"] == "primary_strategy_resolution"
    )
    assert resolution["selected"] == {
        "requested_id": "zero_shot_cot",
        "resolved_id": "chain_of_thought",
        "configuration": {"mode": "zero_shot"},
    }

    forbidden = {"thoughts", "cot_reasoning", "hidden_reasoning"}
    assert not forbidden.intersection(_keys(profile.to_dict()))
    assert not forbidden.intersection(_keys(trace.to_dict()))


async def test_peer_review_requires_independent_reviewer_identity() -> None:
    builder = RuntimeProfileBuilder()
    rejected = await builder.build(
        "create a public announcement",
        tenant_id="tenant-1",
        goal_id="goal-rejected-review",
        agent_config={
            "auxiliary_strategies": ["peer_review"],
            "ready_strategy_ids": ["react", "peer_review"],
            "executor_model": "provider/model-a",
            "reviewer_model": "provider/model-a",
        },
    )
    admitted = await builder.build(
        "create a public announcement",
        tenant_id="tenant-1",
        goal_id="goal-admitted-review",
        agent_config={
            "auxiliary_strategies": ["peer_review"],
            "ready_strategy_ids": ["react", "peer_review"],
            "executor_model": "provider/model-a",
            "reviewer_model": "provider/model-b",
        },
    )

    assert rejected.auxiliary_strategies == ()
    assert rejected.rejected_alternatives[0].reason_code == "reviewer_not_independent"
    assert [item.strategy_id for item in admitted.auxiliary_strategies] == [
        "peer_review"
    ]
    assert dict(admitted.model_role_assignments)["reviewer"] == "provider/model-b"
