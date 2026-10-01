"""Strategy ids must agree across the compatibility table and the registry.

Regression: the compatibility table named ``program_of_thoughts`` / ``magentic_one`` /
``swarm`` while the registry registers ``program_of_thought`` / ``magentic`` /
``decentralized_swarm``, so those strategies silently skipped their sandbox /
coordination admission gate.
"""

from __future__ import annotations

import importlib

import pytest

from app.orchestration.compatibility import (
    DISTRIBUTED_STRATEGIES,
    GENERATED_CODE_STRATEGIES,
    CompatibilityEvaluator,
)
from app.orchestration.strategy_adapters import (
    AgentGraphCompatibilityAdapter,
    ExecutionTier,
    RAGCompatibilityAdapter,
    WorkflowCompatibilityAdapter,
)
from app.orchestration.strategy_registry import build_default_registry


def test_compatibility_tables_only_name_registered_strategies() -> None:
    registry = build_default_registry()
    for strategy_id in GENERATED_CODE_STRATEGIES | DISTRIBUTED_STRATEGIES:
        assert registry.get(strategy_id) is not None, strategy_id


def test_compatibility_tables_match_registry_execution_tiers() -> None:
    registry = build_default_registry()
    sandbox = {
        c.strategy_id for c in registry.list_all() if c.execution_tier is ExecutionTier.SANDBOX
    }
    distributed = {
        c.strategy_id for c in registry.list_all() if c.execution_tier is ExecutionTier.DISTRIBUTED
    }
    assert set(GENERATED_CODE_STRATEGIES) == sandbox
    assert set(DISTRIBUTED_STRATEGIES) == distributed


@pytest.mark.parametrize(
    ("strategy_id", "reason"),
    [
        ("program_of_thought", "sandbox_not_ready"),
        ("codeact", "sandbox_not_ready"),
        ("magentic", "coordination_not_ready"),
        ("decentralized_swarm", "coordination_not_ready"),
        ("group_chat", "coordination_not_ready"),  # a coordination pattern since GROUP-CHAT-GOAL
    ],
)
def test_every_gated_strategy_is_actually_gated(strategy_id: str, reason: str) -> None:
    decision = CompatibilityEvaluator(build_default_registry()).compose(
        primary_id=strategy_id,
        candidate_auxiliary_ids=(),
        ready_ids=frozenset({strategy_id}),
    )
    assert decision.primary_rejection == reason


def _import_target(path: str) -> object:
    module_name, _, attribute = path.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attribute) if attribute else module


def test_every_adapter_path_imports() -> None:
    broken: list[str] = []
    for capability in build_default_registry().list_all():
        if not capability.adapter_path:
            continue
        try:
            _import_target(capability.adapter_path)
        except (ImportError, AttributeError) as exc:
            broken.append(f"{capability.strategy_id}: {capability.adapter_path} ({exc})")
    assert broken == []


def test_adapter_path_names_the_adapter_the_descriptor_builds() -> None:
    compatibility_shims = (
        AgentGraphCompatibilityAdapter,
        RAGCompatibilityAdapter,
        WorkflowCompatibilityAdapter,
    )
    mismatched: list[str] = []
    for capability in build_default_registry().list_all():
        descriptor = capability.adapter_descriptor
        if descriptor is None:
            continue
        adapter = descriptor.create_adapter()
        if isinstance(adapter, compatibility_shims):
            continue
        expected = f"{type(adapter).__module__}:{type(adapter).__qualname__}"
        if capability.adapter_path != expected:
            mismatched.append(f"{capability.strategy_id}: {capability.adapter_path} != {expected}")
    assert mismatched == []
