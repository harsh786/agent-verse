from __future__ import annotations

import pytest

from app.orchestration.strategy_adapters import (
    AgentGraphCompatibilityAdapter,
    ExecutionTier,
    RAGCompatibilityAdapter,
    WorkflowCompatibilityAdapter,
)
from app.orchestration.strategy_registry import StrategyState, build_default_registry
from app.rag.contracts import RAGRuntimeAdapter, RAGStrategy


@pytest.mark.parametrize("strategy_id", ["reflection"])
def test_local_agent_graph_descriptor_is_import_safe_and_executable(
    strategy_id: str,
) -> None:
    capability = build_default_registry().get(strategy_id)

    assert capability is not None
    assert capability.adapter_descriptor is not None
    assert capability.adapter_descriptor.execution_tier is ExecutionTier.LOCAL
    adapter = capability.adapter_descriptor.create_adapter()
    assert isinstance(adapter, AgentGraphCompatibilityAdapter)
    assert callable(adapter.create_runtime)


def test_workflow_descriptor_supports_static_and_dag_executor() -> None:
    capability = build_default_registry().get("workflow_dag")

    assert capability is not None
    assert capability.adapter_descriptor is not None
    adapter = capability.adapter_descriptor.create_adapter()
    assert isinstance(adapter, WorkflowCompatibilityAdapter)
    assert callable(adapter.create_runtime)
    assert adapter.supported_apis == ("execute", "run")


@pytest.mark.parametrize("strategy", list(RAGStrategy))
def test_rag_descriptor_uses_existing_runtime_boundary(strategy: RAGStrategy) -> None:
    capability = build_default_registry().get(strategy.value)

    assert capability is not None
    if capability.state not in {StrategyState.IMPLEMENTED, StrategyState.CERTIFIED}:
        pytest.skip("No executable runtime adapter is registered")
    assert capability.adapter_descriptor is not None
    adapter = capability.adapter_descriptor.create_adapter()
    assert isinstance(adapter, RAGCompatibilityAdapter)
    runtime = adapter.create_runtime()
    assert isinstance(runtime, RAGRuntimeAdapter)
    assert runtime.strategy is strategy


def test_registry_never_resolves_user_controlled_import_paths() -> None:
    registry = build_default_registry()

    with pytest.raises(LookupError, match="Unknown strategy"):
        registry.resolve("pathlib:Path")
    with pytest.raises(LookupError, match="Unknown adapter descriptor"):
        registry.resolve_adapter("pathlib:Path")
