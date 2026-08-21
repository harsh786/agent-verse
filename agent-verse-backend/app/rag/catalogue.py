"""Authoritative runtime catalogue for the 18 canonical RAG strategies."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from app.rag.contracts import RAGRuntimeAdapter, RAGStrategy


class RAGRuntimeDependency(StrEnum):
    """Named runtime capabilities that can make a strategy unavailable."""

    DATABASE = "database"
    EMBEDDER = "embedder"
    PROVIDER = "provider"
    GRAPH = "graph"
    WEB = "web"
    COLBERT_LIBRARY = "colbert_library"
    COLBERT_CHECKPOINT = "colbert_checkpoint"
    RAFT_SERVICE = "raft_service"
    RAFT_MODEL = "raft_model"


@dataclass(frozen=True, slots=True)
class ReadinessFact:
    """One sanitized dependency fact shared across catalogue predicates."""

    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ReadinessContext:
    """Immutable shared facts evaluated without retrieval or external calls."""

    facts: Mapping[RAGRuntimeDependency, ReadinessFact]
    strategy_facts: Mapping[RAGStrategy, Mapping[RAGRuntimeDependency, ReadinessFact]] = field(
        default_factory=dict
    )
    adapter_facts: Mapping[RAGStrategy, ReadinessFact] = field(default_factory=dict)

    @classmethod
    def all_available(cls) -> ReadinessContext:
        return cls(
            MappingProxyType(
                {dependency: ReadinessFact(True, "ready") for dependency in RAGRuntimeDependency}
            )
        )

    @property
    def available_dependencies(self) -> frozenset[RAGRuntimeDependency]:
        return frozenset(dependency for dependency, fact in self.facts.items() if fact.available)

    def with_fact(
        self,
        dependency: RAGRuntimeDependency,
        fact: ReadinessFact,
    ) -> ReadinessContext:
        return ReadinessContext(
            MappingProxyType({**self.facts, dependency: fact}),
            self.strategy_facts,
            self.adapter_facts,
        )

    def without(self, dependency: RAGRuntimeDependency) -> ReadinessContext:
        return self.with_fact(
            dependency,
            ReadinessFact(False, f"{dependency.value}_unavailable"),
        )

    def fact(
        self,
        dependency: RAGRuntimeDependency,
        strategy: RAGStrategy,
    ) -> ReadinessFact:
        return self.strategy_facts.get(strategy, {}).get(
            dependency,
            self.facts.get(
                dependency,
                ReadinessFact(False, f"{dependency.value}_unavailable"),
            ),
        )


RAGRuntimeReadiness = ReadinessContext


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    """Catalogue-owned readiness result for one strategy."""

    available: bool
    reason: str
    missing_dependencies: tuple[RAGRuntimeDependency, ...] = ()


ReadinessPredicate = Callable[
    [ReadinessContext, "RAGCapabilityCatalogueEntry"],
    ReadinessDecision,
]


@dataclass(frozen=True, slots=True)
class RAGAdapterConfiguration:
    """Trusted process configuration supplied to concrete runtime adapters."""

    colbert_checkpoint: str = "colbert-ir/colbertv2.0"

    def __post_init__(self) -> None:
        if not self.colbert_checkpoint.strip():
            raise ValueError("ColBERT checkpoint cannot be empty")


def _required_dependencies_ready(
    readiness: ReadinessContext,
    entry: RAGCapabilityCatalogueEntry,
) -> ReadinessDecision:
    adapter_fact = readiness.adapter_facts.get(entry.strategy)
    if adapter_fact is not None and not adapter_fact.available:
        return ReadinessDecision(False, adapter_fact.reason)
    missing = tuple(
        dependency
        for dependency in entry.required_dependencies
        if not readiness.fact(dependency, entry.strategy).available
    )
    if missing:
        return ReadinessDecision(
            False,
            readiness.fact(missing[0], entry.strategy).reason,
            missing,
        )
    return ReadinessDecision(True, "ready")


@dataclass(frozen=True, slots=True)
class RAGCapabilityCatalogueEntry:
    """One strategy's adapter, dependencies, readiness rule, and probe owner."""

    strategy: RAGStrategy
    adapter_path: str
    required_dependencies: tuple[RAGRuntimeDependency, ...]
    adapter_version: str = "1.0.0"
    readiness_predicate: ReadinessPredicate = _required_dependencies_ready

    @property
    def adapter_class(self) -> type[RAGRuntimeAdapter]:
        module_name, class_name = self.adapter_path.split(":", maxsplit=1)
        module = importlib.import_module(module_name)
        return cast(type[RAGRuntimeAdapter], getattr(module, class_name))

    def create_adapter(
        self,
        configuration: RAGAdapterConfiguration | None = None,
    ) -> RAGRuntimeAdapter:
        resolved_configuration = configuration or RAGAdapterConfiguration()
        factory = cast(Callable[..., RAGRuntimeAdapter], self.adapter_class)
        adapter = (
            factory(colbert_checkpoint=resolved_configuration.colbert_checkpoint)
            if self.strategy is RAGStrategy.COLBERT
            else factory()
        )
        if adapter.strategy is not self.strategy:
            raise TypeError(f"Adapter strategy mismatch for {self.strategy.value}")
        return adapter

    def missing_dependencies(
        self,
        readiness: ReadinessContext,
    ) -> tuple[RAGRuntimeDependency, ...]:
        return self.evaluate_readiness(readiness).missing_dependencies

    def evaluate_readiness(self, readiness: ReadinessContext) -> ReadinessDecision:
        return self.readiness_predicate(readiness, self)


_DB_EMBED = (RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER)
_DB_EMBED_PROVIDER = (*_DB_EMBED, RAGRuntimeDependency.PROVIDER)

RAG_CAPABILITY_CATALOGUE: Mapping[RAGStrategy, RAGCapabilityCatalogueEntry] = MappingProxyType(
    {
        RAGStrategy.NAIVE: RAGCapabilityCatalogueEntry(
            RAGStrategy.NAIVE, "app.rag.contracts:NaiveRAGRuntimeAdapter", _DB_EMBED
        ),
        RAGStrategy.HYBRID: RAGCapabilityCatalogueEntry(
            RAGStrategy.HYBRID, "app.rag.contracts:HybridRAGRuntimeAdapter", _DB_EMBED
        ),
        RAGStrategy.HYDE: RAGCapabilityCatalogueEntry(
            RAGStrategy.HYDE, "app.rag.contracts:HyDERAGRuntimeAdapter", _DB_EMBED_PROVIDER
        ),
        RAGStrategy.MULTI_HOP: RAGCapabilityCatalogueEntry(
            RAGStrategy.MULTI_HOP,
            "app.rag.contracts:MultiHopRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.GRAPH: RAGCapabilityCatalogueEntry(
            RAGStrategy.GRAPH,
            "app.rag.contracts:GraphRAGRuntimeAdapter",
            (*_DB_EMBED, RAGRuntimeDependency.GRAPH),
        ),
        RAGStrategy.CORRECTIVE: RAGCapabilityCatalogueEntry(
            RAGStrategy.CORRECTIVE,
            "app.rag.contracts:CorrectiveRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.ADAPTIVE: RAGCapabilityCatalogueEntry(
            RAGStrategy.ADAPTIVE, "app.rag.contracts:AdaptiveRAGRuntimeAdapter", _DB_EMBED
        ),
        RAGStrategy.MODULAR: RAGCapabilityCatalogueEntry(
            RAGStrategy.MODULAR,
            "app.rag.agentic.patterns.modular:ModularRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.SPECULATIVE: RAGCapabilityCatalogueEntry(
            RAGStrategy.SPECULATIVE,
            "app.rag.agentic.patterns.speculative:SpeculativeRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.AGENTIC: RAGCapabilityCatalogueEntry(
            RAGStrategy.AGENTIC,
            "app.rag.agentic.patterns.agentic:AgenticRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.WEB_AUGMENTED: RAGCapabilityCatalogueEntry(
            RAGStrategy.WEB_AUGMENTED,
            "app.rag.contracts:WebAugmentedRAGRuntimeAdapter",
            (*_DB_EMBED, RAGRuntimeDependency.WEB),
        ),
        RAGStrategy.FUSION: RAGCapabilityCatalogueEntry(
            RAGStrategy.FUSION, "app.rag.contracts:FusionRAGRuntimeAdapter", _DB_EMBED_PROVIDER
        ),
        RAGStrategy.SELF_RAG: RAGCapabilityCatalogueEntry(
            RAGStrategy.SELF_RAG,
            "app.rag.agentic.patterns.self_rag:SelfRAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.FLARE: RAGCapabilityCatalogueEntry(
            RAGStrategy.FLARE,
            "app.rag.agentic.patterns.flare:FLARERAGRuntimeAdapter",
            _DB_EMBED_PROVIDER,
        ),
        RAGStrategy.RAPTOR: RAGCapabilityCatalogueEntry(
            RAGStrategy.RAPTOR, "app.rag.contracts:RAPTORRAGRuntimeAdapter", _DB_EMBED
        ),
        RAGStrategy.AGENTIC_CHUNKING: RAGCapabilityCatalogueEntry(
            RAGStrategy.AGENTIC_CHUNKING,
            "app.rag.contracts:AgenticChunkingRAGRuntimeAdapter",
            _DB_EMBED,
        ),
        RAGStrategy.COLBERT: RAGCapabilityCatalogueEntry(
            RAGStrategy.COLBERT,
            "app.rag.agentic.patterns.colbert:ColBERTRAGRuntimeAdapter",
            (
                *_DB_EMBED,
                RAGRuntimeDependency.COLBERT_LIBRARY,
                RAGRuntimeDependency.COLBERT_CHECKPOINT,
            ),
        ),
        RAGStrategy.RAFT: RAGCapabilityCatalogueEntry(
            RAGStrategy.RAFT,
            "app.rag.agentic.patterns.raft:RAFTRAGRuntimeAdapter",
            (
                *_DB_EMBED,
                RAGRuntimeDependency.RAFT_SERVICE,
                RAGRuntimeDependency.RAFT_MODEL,
            ),
        ),
    }
)


class _LazyRuntimeCapabilities(Mapping[RAGStrategy, type[RAGRuntimeAdapter]]):
    def __getitem__(self, strategy: RAGStrategy) -> type[RAGRuntimeAdapter]:
        return RAG_CAPABILITY_CATALOGUE[strategy].adapter_class

    def __iter__(self) -> Iterator[RAGStrategy]:
        return iter(RAG_CAPABILITY_CATALOGUE)

    def __len__(self) -> int:
        return len(RAG_CAPABILITY_CATALOGUE)


RAG_RUNTIME_CAPABILITIES: Mapping[RAGStrategy, type[RAGRuntimeAdapter]] = _LazyRuntimeCapabilities()


__all__ = [
    "RAG_CAPABILITY_CATALOGUE",
    "RAG_RUNTIME_CAPABILITIES",
    "RAGAdapterConfiguration",
    "RAGCapabilityCatalogueEntry",
    "RAGRuntimeDependency",
    "RAGRuntimeReadiness",
    "ReadinessContext",
    "ReadinessDecision",
    "ReadinessFact",
]
