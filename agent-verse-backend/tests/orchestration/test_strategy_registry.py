"""Tests for StrategyRegistry — canonical pattern catalogue."""

from __future__ import annotations

import enum

import pytest

from app.orchestration.strategy_adapters import (
    AdapterDescriptor,
    AgentGraphCompatibilityAdapter,
    ExecutionTier,
)
from app.orchestration.strategy_contracts import StrategyLifecycleState
from app.orchestration.strategy_registry import (
    DEFAULT_STRATEGY_ALIASES,
    StrategyCapability,
    StrategyCategory,
    StrategyRegistry,
    StrategyState,
    build_default_registry,
)
from app.rag.contracts import RAGStrategy, resolve_rag_strategy


def test_registry_has_all_required_agent_patterns():
    registry = build_default_registry()
    required = {
        "react",
        "plan_execute",
        "reflection",
        "reflexion",
        "self_refine",
        "self_consistency",
        "tree_of_thoughts",
        "supervisor",
        "debate",
        "goal_tree",
        "consensus",
        "chain_of_thought",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.AGENT)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_all_required_rag_patterns():
    registry = build_default_registry()
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}

    assert registered == {strategy.value for strategy in RAGStrategy}


def test_registry_has_all_safety_patterns():
    registry = build_default_registry()
    required = {
        "guardrails",
        "hitl",
        "consensus_verification",
        "sandbox",
        "plan_verification",
        "data_classification",
        "provenance_verification",
        "grounding_checker",
        "circuit_breaker",
        "rollback",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.SAFETY)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_90_plus_entries():
    assert len(build_default_registry().list_all()) >= 90


def test_every_entry_has_valid_state():
    valid_states = {
        StrategyState.IMPLEMENTED,
        StrategyState.CERTIFIED,
        StrategyState.PARTIAL,
        StrategyState.PLANNED,
        StrategyState.DISABLED,
    }
    for entry in build_default_registry().list_all():
        assert entry.state in valid_states


def test_registry_enums_preserve_original_string_enum_bases() -> None:
    assert StrategyState.__bases__ == (str, enum.Enum)
    assert StrategyCategory.__bases__ == (str, enum.Enum)


def test_lookup_by_id():
    registry = build_default_registry()
    cap = registry.get("hybrid")
    assert cap is not None
    assert cap.strategy_id == "hybrid"
    assert cap.category == StrategyCategory.RAG


@pytest.mark.parametrize(
    ("historical_id", "canonical"),
    [
        ("fusion_rag", RAGStrategy.FUSION),
        ("corrective_rag", RAGStrategy.CORRECTIVE),
        ("speculative_rag", RAGStrategy.SPECULATIVE),
        ("colbert_late_interaction", RAGStrategy.COLBERT),
        ("multi_hop_rag", RAGStrategy.MULTI_HOP),
        ("graph_rag", RAGStrategy.GRAPH),
    ],
)
def test_only_boundary_resolver_accepts_historical_ids(
    historical_id: str,
    canonical: RAGStrategy,
) -> None:
    registry = build_default_registry()

    assert resolve_rag_strategy(historical_id) is canonical
    assert registry.get(historical_id) is None
    capability = registry.get(canonical.value)
    assert capability is not None
    assert capability.strategy_id == canonical.value


def test_lookup_nonexistent_returns_none():
    assert build_default_registry().get("does_not_exist") is None


def test_filter_by_cost_class():
    registry = build_default_registry()
    low_cost = registry.filter(cost_class="low")
    assert all(s.cost_class == "low" for s in low_cost)


def test_is_available_checks_state():
    registry = build_default_registry()
    assert not registry.is_available(
        "hybrid",
        available_deps={"knowledge_store", "embedder"},
    )
    assert registry.is_available("chain_of_thought")
    cap = registry.get("tree_of_thoughts")
    assert cap is not None
    assert cap.strategy_id == "tree_of_thoughts"


def test_implemented_and_certified_entries_have_complete_runtime_metadata() -> None:
    registry = build_default_registry()

    for capability in registry.list_all():
        if capability.state not in {
            StrategyState.IMPLEMENTED,
            StrategyState.CERTIFIED,
        }:
            continue

        assert capability.adapter_version
        assert capability.state_schema_version > 0
        assert capability.spec.lifecycle_state in {
            StrategyLifecycleState.IMPLEMENTED,
            StrategyLifecycleState.CERTIFIED,
        }
        assert capability.adapter_descriptor is not None
        assert capability.adapter_descriptor.is_executable
        assert callable(capability.adapter_factory)
        assert capability.execution_tier is capability.adapter_descriptor.execution_tier
        assert capability.default_limits == capability.spec.default_limits
        assert capability.compatibility_metadata is not None
        assert capability.readiness_requirements


@pytest.mark.parametrize(
    ("requested_id", "canonical_id"),
    [
        ("cot", "chain_of_thought"),
        ("zero_shot_cot", "chain_of_thought"),
        ("plan_and_execute", "plan_execute"),
    ],
)
def test_registry_resolves_canonical_alias_once_and_records_decision(
    requested_id: str,
    canonical_id: str,
) -> None:
    resolution = build_default_registry().resolve(requested_id)

    assert resolution.requested_id == requested_id
    assert resolution.canonical_id == canonical_id
    assert resolution.was_aliased
    assert resolution.capability.strategy_id == canonical_id


def test_registry_rejects_duplicate_ids_and_aliases() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None

    with pytest.raises(ValueError, match="Duplicate strategy ID: react"):
        StrategyRegistry([capability, capability])

    with pytest.raises(ValueError, match="Duplicate strategy alias: cot"):
        StrategyRegistry(
            [capability],
            aliases=(("cot", "react"), ("cot", "react")),
        )


def test_registry_rejects_alias_collisions_and_unknown_targets() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None

    with pytest.raises(ValueError, match="conflicts with a canonical strategy ID"):
        StrategyRegistry([capability], aliases=(("react", "react"),))

    with pytest.raises(ValueError, match="Unknown canonical strategy ID"):
        StrategyRegistry([capability], aliases=(("legacy", "missing"),))


def test_default_aliases_are_registry_owned() -> None:
    assert DEFAULT_STRATEGY_ALIASES == (
        ("cot", "chain_of_thought"),
        ("zero_shot_cot", "chain_of_thought"),
        ("plan_and_execute", "plan_execute"),
    )


def test_loop_engineering_is_a_non_executable_bundle() -> None:
    capability = build_default_registry().get("loop_engineering")

    assert capability is not None
    assert capability.state is StrategyState.PARTIAL
    assert capability.adapter_descriptor is None
    assert capability.bundle_components == ("loop_until", "wave_execution")


def test_execution_memory_has_explicit_canonical_ownership() -> None:
    registry = build_default_registry()
    session_memory = registry.get("session_memory")
    execution_memory = registry.get("execution_memory")

    assert session_memory is not None
    assert execution_memory is not None
    assert session_memory.canonical_owner_id == "execution_memory"
    assert execution_memory.canonical_owner_id == "execution_memory"


def test_empty_or_untrusted_adapter_cannot_be_implemented_or_certified() -> None:
    for capability in build_default_registry().list_all():
        if capability.adapter_descriptor is None:
            assert capability.state not in {
                StrategyState.IMPLEMENTED,
                StrategyState.CERTIFIED,
            }

    malformed = StrategyCapability(
        strategy_id="malformed",
        category=StrategyCategory.AGENT,
        state=StrategyState.IMPLEMENTED,
    )
    with pytest.raises(ValueError, match="requires an executable adapter descriptor"):
        StrategyRegistry([malformed], aliases=())


def test_registry_rejects_capability_descriptor_execution_tier_mismatch() -> None:
    capability = StrategyCapability(
        strategy_id="tier-mismatch",
        category=StrategyCategory.AGENT,
        state=StrategyState.PARTIAL,
        adapter_descriptor=AdapterDescriptor(
            descriptor_id="local.tier-mismatch",
            execution_tier=ExecutionTier.LOCAL,
            factory=lambda: AgentGraphCompatibilityAdapter("tier-mismatch"),
        ),
        execution_tier=ExecutionTier.WORKFLOW,
    )

    with pytest.raises(ValueError, match="execution tier mismatch: tier-mismatch"):
        StrategyRegistry([capability], aliases=())


def test_registry_rejects_descriptor_factory_strategy_id_mismatch() -> None:
    capability = StrategyCapability(
        strategy_id="factory-mismatch",
        category=StrategyCategory.AGENT,
        state=StrategyState.PARTIAL,
        adapter_descriptor=AdapterDescriptor(
            descriptor_id="local.factory-mismatch",
            execution_tier=ExecutionTier.LOCAL,
            factory=lambda: AgentGraphCompatibilityAdapter("different-strategy"),
        ),
        execution_tier=ExecutionTier.LOCAL,
    )

    with pytest.raises(ValueError, match="adapter strategy ID mismatch: factory-mismatch"):
        StrategyRegistry([capability], aliases=())


def test_registry_rejects_descriptor_factory_exception_for_partial_capability() -> None:
    def failing_factory() -> AgentGraphCompatibilityAdapter:
        raise RuntimeError("factory failed")

    capability = StrategyCapability(
        strategy_id="factory-error",
        category=StrategyCategory.AGENT,
        state=StrategyState.PARTIAL,
        adapter_descriptor=AdapterDescriptor(
            descriptor_id="local.factory-error",
            execution_tier=ExecutionTier.LOCAL,
            factory=failing_factory,
        ),
        execution_tier=ExecutionTier.LOCAL,
    )

    with pytest.raises(ValueError, match="invalid adapter descriptor: factory-error"):
        StrategyRegistry([capability], aliases=())


def test_strategy_capability_retains_legacy_read_only_properties() -> None:
    capability = build_default_registry().get("react")

    assert isinstance(capability, StrategyCapability)
    assert capability is not None
    assert capability.adapter_path
    assert capability.cost_class == capability.spec.cost.value
    assert capability.latency_class == capability.spec.latency.value
    assert capability.risk_class == capability.spec.risk.value


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("state", StrategyState.PARTIAL),
        ("adapter_descriptor", None),
        ("cost_class", "high"),
        ("required_deps", ("replacement",)),
        ("canonical_owner_id", "replacement"),
        ("_spec", None),
    ],
)
def test_finalized_capability_rejects_metadata_assignment(
    attribute: str,
    replacement: object,
) -> None:
    capability = build_default_registry().get("react")

    assert capability is not None
    lifecycle_state = capability.spec.lifecycle_state
    with pytest.raises(AttributeError, match="sealed"):
        setattr(capability, attribute, replacement)

    assert capability.state.value == lifecycle_state.value


def test_finalized_capability_nested_metadata_is_immutable() -> None:
    capability = StrategyCapability(
        strategy_id="nested-metadata",
        category=StrategyCategory.AGENT,
        state=StrategyState.PARTIAL,
        required_deps=["provider"],
        optional_deps=["cache"],
        compatible_goal_properties={
            "compatible_strategies": ["react"],
            "excluded_strategies": ["debate"],
        },
    )
    registry = StrategyRegistry([capability], aliases=())
    finalized = registry.get("nested-metadata")

    assert finalized is capability
    assert finalized is not None
    assert finalized.required_deps == ("provider",)
    assert finalized.optional_deps == ("cache",)
    with pytest.raises(AttributeError):
        finalized.required_deps.append("database")  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        finalized.optional_deps[0] = "database"  # type: ignore[index]
    with pytest.raises(TypeError):
        finalized.compatible_goal_properties["compatible_strategies"] = ("debate",)  # type: ignore[index]
    with pytest.raises(TypeError):
        finalized.compatible_goal_properties["compatible_strategies"][0] = "debate"  # type: ignore[index]


def test_registry_finalizes_then_seals_mutable_capabilities() -> None:
    capability = StrategyCapability(
        strategy_id="two-phase",
        category=StrategyCategory.AGENT,
        state=StrategyState.PARTIAL,
    )
    capability.cost_class = "low"
    capability.required_deps.append("provider")

    registry = StrategyRegistry([capability], aliases=())

    assert registry.get("two-phase") is capability
    assert capability.spec.cost.value == "low"
    assert capability.spec.dependencies == ("provider",)
    with pytest.raises(AttributeError, match="sealed"):
        capability.state = StrategyState.IMPLEMENTED
