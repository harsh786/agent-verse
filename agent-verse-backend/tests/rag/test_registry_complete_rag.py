"""Completeness contract for the canonical RAG strategy registry."""

from __future__ import annotations

from contextlib import ExitStack
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.api.rag_platform import list_strategies
from app.orchestration.strategy_registry import (
    StrategyCategory,
    StrategyState,
    build_default_registry,
)
from app.rag import contracts as rag_contracts
from app.rag.agentic.patterns import RAG_RUNTIME_ADAPTERS
from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAG_RUNTIME_CAPABILITIES,
    RAGRuntimeDependency,
    RAGRuntimeReadiness,
    ReadinessContext,
    ReadinessFact,
)
from app.rag.contracts import RAGStrategy
from app.rag.gateway import RAGStrategyReadiness, core_strategy_capabilities
from app.tenancy.context import TenantContext


class _ReadyGateway:
    async def readiness(
        self,
        tenant_context: TenantContext,
        *,
        strategy_id: RAGStrategy,
        collection_id: str | None = None,
    ) -> RAGStrategyReadiness:
        del tenant_context, collection_id
        return RAGStrategyReadiness(strategy_id, True, "ready")

    async def readiness_all(
        self,
        tenant_context: TenantContext,
        *,
        collection_id: str | None = None,
    ) -> dict[RAGStrategy, RAGStrategyReadiness]:
        del tenant_context, collection_id
        return {
            strategy: RAGStrategyReadiness(strategy, True, "ready")
            for strategy in RAGStrategy
        }


EXPECTED_PROBE_EVIDENCE = {
    RAGStrategy.NAIVE: "persisted vector retrieval",
    RAGStrategy.HYBRID: "four-leg reciprocal rank fusion",
    RAGStrategy.HYDE: "hypothetical document embedding",
    RAGStrategy.MULTI_HOP: "decomposed hop retrieval",
    RAGStrategy.GRAPH: "tenant-scoped graph evidence",
    RAGStrategy.CORRECTIVE: "graded corrective retry",
    RAGStrategy.ADAPTIVE: "capability-aware adaptive routing",
    RAGStrategy.MODULAR: "validated modular pipeline",
    RAGStrategy.SPECULATIVE: "draft and evidence verification",
    RAGStrategy.AGENTIC: "bounded agentic retrieval loop",
    RAGStrategy.WEB_AUGMENTED: "policy-authorized web augmentation",
    RAGStrategy.FUSION: "expanded-query reciprocal rank fusion",
    RAGStrategy.SELF_RAG: "retrieval relevance self-critique",
    RAGStrategy.FLARE: "uncertainty-triggered retrieval",
    RAGStrategy.RAPTOR: "hierarchical summary retrieval",
    RAGStrategy.AGENTIC_CHUNKING: "semantic boundary chunking",
    RAGStrategy.COLBERT: "late-interaction token scoring",
    RAGStrategy.RAFT: "compatible completed RAFT model inference",
}

EXPECTED_DEPENDENCIES = {
    RAGStrategy.NAIVE: {RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER},
    RAGStrategy.HYBRID: {RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER},
    RAGStrategy.HYDE: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.MULTI_HOP: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.GRAPH: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.GRAPH,
    },
    RAGStrategy.CORRECTIVE: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.ADAPTIVE: {RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER},
    RAGStrategy.MODULAR: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.SPECULATIVE: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.AGENTIC: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.WEB_AUGMENTED: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.WEB,
    },
    RAGStrategy.FUSION: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.SELF_RAG: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.FLARE: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.PROVIDER,
    },
    RAGStrategy.RAPTOR: {RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER},
    RAGStrategy.AGENTIC_CHUNKING: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
    },
    RAGStrategy.COLBERT: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.COLBERT_LIBRARY,
        RAGRuntimeDependency.COLBERT_CHECKPOINT,
    },
    RAGStrategy.RAFT: {
        RAGRuntimeDependency.DATABASE,
        RAGRuntimeDependency.EMBEDDER,
        RAGRuntimeDependency.RAFT_SERVICE,
        RAGRuntimeDependency.RAFT_MODEL,
    },
}


def _request() -> Any:
    tenant = TenantContext(
        tenant_id="tenant-1",
        api_key_id="key-1",
        plan="enterprise",
    )
    return SimpleNamespace(
        state=SimpleNamespace(tenant=tenant),
        app=SimpleNamespace(
            state=SimpleNamespace(retrieval_gateway=_ReadyGateway())
        ),
    )


def test_single_catalogue_owns_all_runtime_contracts() -> None:
    assert isinstance(RAG_CAPABILITY_CATALOGUE, MappingProxyType)
    assert tuple(RAG_CAPABILITY_CATALOGUE) == tuple(RAGStrategy)
    assert len(RAG_CAPABILITY_CATALOGUE) == 18
    assert {
        strategy: set(entry.required_dependencies)
        for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
    } == EXPECTED_DEPENDENCIES
    assert all(
        entry.strategy is strategy
        for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
    )
    assert all(
        entry.adapter_class.strategy is strategy
        for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
    )
    assert len(
        {entry.adapter_class for entry in RAG_CAPABILITY_CATALOGUE.values()}
    ) == 18
    assert RAG_RUNTIME_CAPABILITIES is RAG_RUNTIME_ADAPTERS
    assert not hasattr(rag_contracts, "RAG_RUNTIME_CAPABILITIES")


def test_catalogue_predicate_is_authoritative_for_runtime_readiness() -> None:
    entry = RAG_CAPABILITY_CATALOGUE[RAGStrategy.NAIVE]
    context = ReadinessContext.all_available().with_fact(
        RAGRuntimeDependency.EMBEDDER,
        ReadinessFact(False, "catalogue_embedder_contract_failed"),
    )

    decision = entry.evaluate_readiness(context)

    assert not decision.available
    assert decision.reason == "catalogue_embedder_contract_failed"
    assert decision.missing_dependencies == (RAGRuntimeDependency.EMBEDDER,)


def test_importing_catalogue_does_not_import_pattern_implementation_graph() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.rag.catalogue; "
                "print(any(name.startswith('app.rag.agentic.patterns.') "
                "for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("missing_dependency", list(RAGRuntimeDependency))
def test_catalogue_reports_every_dependency_unavailable_state(
    missing_dependency: RAGRuntimeDependency,
) -> None:
    readiness = RAGRuntimeReadiness.all_available().without(missing_dependency)
    affected = [
        entry
        for entry in RAG_CAPABILITY_CATALOGUE.values()
        if missing_dependency in entry.required_dependencies
    ]

    assert affected
    assert all(
        entry.missing_dependencies(readiness) == (missing_dependency,)
        for entry in affected
    )
    assert all(
        missing_dependency not in entry.missing_dependencies(readiness)
        for entry in RAG_CAPABILITY_CATALOGUE.values()
        if entry not in affected
    )
    available_dependencies = {
        dependency.value
        for dependency in RAGRuntimeDependency
        if dependency is not missing_dependency
    }
    registry = build_default_registry()
    for entry in affected:
        probe = registry.probe_rag_strategy(
            entry.strategy.value,
            available_deps=available_dependencies,
        )
        assert not probe.available
        assert probe.missing_dependencies == (missing_dependency.value,)
        assert probe.evidence == EXPECTED_PROBE_EVIDENCE[entry.strategy]


def test_registry_invokes_every_adapter_owned_probe_with_exact_evidence() -> None:
    registry = build_default_registry()
    with ExitStack() as stack:
        probe_mocks = {
            strategy: stack.enter_context(
                patch.object(
                    entry.adapter_class,
                    "probe_trace",
                    wraps=entry.adapter_class.probe_trace,
                )
            )
            for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
        }
        probes = [
            registry.probe_rag_strategy(strategy.value) for strategy in RAGStrategy
        ]

    assert all(probe_mock.call_count == 1 for probe_mock in probe_mocks.values())
    assert {
        probe.trace.strategy: probe.evidence for probe in probes
    } == EXPECTED_PROBE_EVIDENCE
    assert all(
        probe.trace.detail["adapter_strategy"] == probe.strategy_id
        for probe in probes
    )
    assert len({probe.trace.action for probe in probes}) == 18


async def test_registry_core_capabilities_and_api_derive_from_catalogue() -> None:
    registry = build_default_registry()
    entries = registry.list_by_category(StrategyCategory.RAG)

    assert [entry.strategy_id for entry in entries] == [
        strategy.value for strategy in RAGStrategy
    ]
    assert all(entry.state is StrategyState.IMPLEMENTED for entry in entries)

    adapters = [registry.resolve_rag_adapter(entry.strategy_id) for entry in entries]
    assert len(set(adapters)) == len(RAGStrategy)
    assert all(
        adapter.strategy.value == entry.strategy_id
        for adapter, entry in zip(adapters, entries, strict=True)
    )
    runtime_capabilities = core_strategy_capabilities()
    assert set(runtime_capabilities) == set(RAGStrategy)
    assert all(
        isinstance(runtime_capabilities[adapter.strategy].adapter, adapter)
        for adapter in adapters
    )

    assert {
        RAGStrategy(entry.strategy_id): tuple(entry.required_deps) for entry in entries
    } == {
        strategy: tuple(
            dependency.value for dependency in catalogue.required_dependencies
        )
        for strategy, catalogue in RAG_CAPABILITY_CATALOGUE.items()
    }

    response = await list_strategies(_request())
    public_entries = response["strategies"]
    assert [entry["id"] for entry in public_entries] == [
        entry.strategy_id for entry in entries
    ]
    assert {
        RAGStrategy(entry["id"]): tuple(entry["required_dependencies"])
        for entry in public_entries
    } == {
        strategy: tuple(
            dependency.value for dependency in catalogue.required_dependencies
        )
        for strategy, catalogue in RAG_CAPABILITY_CATALOGUE.items()
    }
    assert all(
        entry["state"] == StrategyState.IMPLEMENTED.value
        for entry in public_entries
    )
    assert all(entry["registry_available"] for entry in public_entries)
    assert all(entry["capability_available"] for entry in public_entries)
    assert all(entry["available"] for entry in public_entries)
