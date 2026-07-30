"""Immutable, typed module graphs for canonical Modular RAG execution."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import pairwise
from numbers import Real
from types import MappingProxyType
from typing import Any, Protocol

from app.rag.contracts import (
    DIRECT_CORE_RAG_STRATEGIES,
    UnknownRAGStrategyError,
    resolve_rag_strategy,
)


class ModularValueType(StrEnum):
    """Values that may cross a Modular RAG graph edge."""

    QUERY = "query"
    QUERIES = "queries"
    DOCUMENTS = "documents"
    GRADED_DOCUMENTS = "graded_documents"
    ANSWER = "answer"


class ModularGraphValidationError(ValueError):
    """Raised when a configured module graph cannot execute safely."""


class ModularModuleExecutionError(RuntimeError):
    """A module failure with stable graph execution context."""

    def __init__(
        self,
        *,
        module_id: str,
        capability_id: str,
        sequence: int,
        cause: Exception,
    ) -> None:
        super().__init__(
            f"Modular module execution failed at sequence {sequence}: {module_id}"
        )
        self.module_id = module_id
        self.capability_id = capability_id
        self.sequence = sequence
        self.failed_trace: Mapping[str, Any] = MappingProxyType(
            {
                "sequence": sequence,
                "module_id": module_id,
                "capability_id": capability_id,
                "status": "failed",
                "failure_type": type(cause).__name__,
            }
        )


@dataclass(frozen=True, slots=True)
class ModuleCapability:
    """Registered type and runtime requirements for one module capability."""

    capability_id: str
    input_type: ModularValueType
    output_type: ModularValueType
    requires_llm: bool = False
    requires_embedder: bool = False
    requires_database: bool = False
    config_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """Immutable placement of a registered capability in a module graph."""

    module_id: str
    capability_id: str
    input_type: ModularValueType
    output_type: ModularValueType
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.module_id.strip() or not self.capability_id.strip():
            raise ValueError("Module and capability IDs cannot be empty")
        object.__setattr__(self, "config", _freeze_mapping(self.config))


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        {key: _freeze_value(item) for key, item in value.items()}
    )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ModuleEdge:
    """Directed typed connection between two module placements."""

    source_id: str
    target_id: str


@dataclass(frozen=True, slots=True)
class ModularPipelineSpec:
    """Validated, bounded Modular RAG graph."""

    modules: tuple[ModuleSpec, ...]
    edges: tuple[ModuleEdge, ...]
    entry_module_id: str
    output_module_id: str
    max_branches: int = 2
    max_steps: int = 12


@dataclass(frozen=True, slots=True)
class ModuleValue:
    """Typed runtime value produced by one module."""

    value_type: ModularValueType
    value: Any


@dataclass(frozen=True, slots=True)
class ModuleExecution:
    """One ordered module execution result and its trace-safe evidence."""

    module: ModuleSpec
    output: ModuleValue
    detail: Mapping[str, Any] = field(default_factory=dict)
    input_value: ModuleValue | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))


class ModularPipelineShape(Protocol):
    @property
    def modules(self) -> tuple[ModuleSpec, ...]: ...

    @property
    def edges(self) -> tuple[ModuleEdge, ...]: ...

    @property
    def entry_module_id(self) -> str: ...

    @property
    def output_module_id(self) -> str: ...

    @property
    def max_branches(self) -> int: ...

    @property
    def max_steps(self) -> int: ...


ModuleRunner = Callable[
    [ModuleSpec, ModuleValue],
    Awaitable[tuple[ModuleValue, Mapping[str, Any]]],
]


MODULAR_CAPABILITY_REGISTRY: Mapping[str, ModuleCapability] = MappingProxyType(
    {
        capability.capability_id: capability
        for capability in (
            ModuleCapability(
                "query_expander",
                ModularValueType.QUERY,
                ModularValueType.QUERIES,
                requires_llm=True,
                config_keys=frozenset({"max_queries"}),
            ),
            ModuleCapability(
                "retriever",
                ModularValueType.QUERIES,
                ModularValueType.DOCUMENTS,
                requires_embedder=True,
                requires_database=True,
            ),
            ModuleCapability(
                "reranker",
                ModularValueType.DOCUMENTS,
                ModularValueType.DOCUMENTS,
            ),
            ModuleCapability(
                "grader",
                ModularValueType.DOCUMENTS,
                ModularValueType.GRADED_DOCUMENTS,
                requires_llm=True,
            ),
            ModuleCapability(
                "fallback",
                ModularValueType.GRADED_DOCUMENTS,
                ModularValueType.DOCUMENTS,
                config_keys=frozenset({"strategy"}),
            ),
            ModuleCapability(
                "synthesizer",
                ModularValueType.DOCUMENTS,
                ModularValueType.ANSWER,
                requires_llm=True,
            ),
        )
    }
)


def _module(
    module_id: str,
    capability_id: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> ModuleSpec:
    capability = MODULAR_CAPABILITY_REGISTRY[capability_id]
    return ModuleSpec(
        module_id=module_id,
        capability_id=capability_id,
        input_type=capability.input_type,
        output_type=capability.output_type,
        config=config or {},
    )


def default_modular_pipeline() -> ModularPipelineSpec:
    """Return the conservative query-to-grounded-answer pipeline."""

    modules = (
        _module("expand", "query_expander", config={"max_queries": 2}),
        _module("retrieve", "retriever"),
        _module("rerank", "reranker"),
        _module("grade", "grader"),
        _module("fallback", "fallback", config={"strategy": "hybrid"}),
        _module("synthesize", "synthesizer"),
    )
    pipeline = ModularPipelineSpec(
        modules=modules,
        edges=tuple(
            ModuleEdge(source.module_id, target.module_id)
            for source, target in pairwise(modules)
        ),
        entry_module_id=modules[0].module_id,
        output_module_id=modules[-1].module_id,
    )
    validate_modular_pipeline(pipeline)
    return pipeline


def modular_pipeline_from_config(config: Mapping[str, Any]) -> ModularPipelineSpec:
    """Parse per-agent configuration without accepting undeclared capabilities."""

    raw_modules = config.get("modules")
    raw_edges = config.get("edges")
    if not isinstance(raw_modules, list) or not isinstance(raw_edges, list):
        raise ModularGraphValidationError("modules and edges must be lists")
    modules: list[ModuleSpec] = []
    for raw_module in raw_modules:
        if not isinstance(raw_module, Mapping):
            raise ModularGraphValidationError("module entries must be objects")
        module_id = raw_module.get("id")
        capability_id = raw_module.get("capability")
        module_config = raw_module.get("config", {})
        if not isinstance(module_id, str) or not isinstance(capability_id, str):
            raise ModularGraphValidationError("module id and capability must be strings")
        if not isinstance(module_config, Mapping):
            raise ModularGraphValidationError("module config must be an object")
        capability = MODULAR_CAPABILITY_REGISTRY.get(capability_id)
        if capability is None:
            raise ModularGraphValidationError(
                f"module capability is not registered: {capability_id}"
            )
        modules.append(_module(module_id, capability_id, config=module_config))

    edges: list[ModuleEdge] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise ModularGraphValidationError("edge entries must be objects")
        source_id = raw_edge.get("source")
        target_id = raw_edge.get("target")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            raise ModularGraphValidationError("edge endpoints must be strings")
        edges.append(ModuleEdge(source_id, target_id))

    pipeline = ModularPipelineSpec(
        modules=tuple(modules),
        edges=tuple(edges),
        entry_module_id=str(config.get("entry", modules[0].module_id if modules else "")),
        output_module_id=str(config.get("output", modules[-1].module_id if modules else "")),
        max_branches=_bounded_integer(config.get("max_branches", 2), "max_branches"),
        max_steps=_bounded_integer(config.get("max_steps", 12), "max_steps"),
    )
    return pipeline


def _bounded_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModularGraphValidationError(f"{name} must be an integer")
    return value


def validate_modular_pipeline(pipeline: ModularPipelineShape) -> None:
    """Validate capability identity, edge types, graph bounds, and reachability."""

    if not 1 <= pipeline.max_branches <= 4:
        raise ModularGraphValidationError("pipeline branch bound must be between 1 and 4")
    if not 1 <= pipeline.max_steps <= 32:
        raise ModularGraphValidationError("pipeline step bound must be between 1 and 32")
    if not pipeline.modules or len(pipeline.modules) > pipeline.max_steps:
        raise ModularGraphValidationError("module count exceeds the pipeline step bound")

    modules = {module.module_id: module for module in pipeline.modules}
    if len(modules) != len(pipeline.modules):
        raise ModularGraphValidationError("module IDs must be unique")
    if pipeline.entry_module_id not in modules or pipeline.output_module_id not in modules:
        raise ModularGraphValidationError("entry and output modules must exist")

    for module in pipeline.modules:
        capability = MODULAR_CAPABILITY_REGISTRY.get(module.capability_id)
        if capability is None:
            raise ModularGraphValidationError(
                f"module capability is not registered: {module.capability_id}"
            )
        if (
            module.input_type is not capability.input_type
            or module.output_type is not capability.output_type
        ):
            raise ModularGraphValidationError(
                f"module {module.module_id} does not match its registered capability types"
            )
        unknown_config = set(module.config) - capability.config_keys
        if unknown_config:
            keys = ", ".join(sorted(unknown_config))
            raise ModularGraphValidationError(
                f"module {module.module_id} has undeclared config keys: {keys}"
            )
        if module.capability_id == "query_expander":
            max_queries = module.config.get("max_queries", pipeline.max_branches)
            if (
                isinstance(max_queries, bool)
                or not isinstance(max_queries, int)
                or not 1 <= max_queries <= pipeline.max_branches
            ):
                raise ModularGraphValidationError(
                    "query_expander max_queries exceeds the pipeline branch bound"
                )
        if module.capability_id == "fallback":
            strategy = module.config.get("strategy", "hybrid")
            if not isinstance(strategy, str) or not strategy.strip():
                raise ModularGraphValidationError(
                    "fallback strategy must be a non-empty string"
                )
            try:
                fallback_strategy = resolve_rag_strategy(strategy)
            except UnknownRAGStrategyError as exc:
                raise ModularGraphValidationError(
                    f"fallback strategy is not canonical: {strategy}"
                ) from exc
            if fallback_strategy not in DIRECT_CORE_RAG_STRATEGIES:
                raise ModularGraphValidationError(
                    f"fallback cannot use internal dispatch: {fallback_strategy.value}"
                )

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming_count = dict.fromkeys(modules, 0)
    seen_edges: set[tuple[str, str]] = set()
    for edge in pipeline.edges:
        if edge.source_id not in modules or edge.target_id not in modules:
            raise ModularGraphValidationError("edge references an unknown module")
        edge_key = (edge.source_id, edge.target_id)
        if edge_key in seen_edges or edge.source_id == edge.target_id:
            raise ModularGraphValidationError("duplicate or self-referential edge")
        seen_edges.add(edge_key)
        source = modules[edge.source_id]
        target = modules[edge.target_id]
        if source.output_type is not target.input_type:
            raise ModularGraphValidationError(
                f"incompatible edge {edge.source_id} -> {edge.target_id}"
            )
        outgoing[edge.source_id].append(edge.target_id)
        incoming_count[edge.target_id] += 1
        if len(outgoing[edge.source_id]) > pipeline.max_branches:
            raise ModularGraphValidationError("module branch count exceeds pipeline bound")

    if incoming_count[pipeline.entry_module_id] != 0:
        raise ModularGraphValidationError("entry module cannot have incoming edges")
    if outgoing[pipeline.output_module_id]:
        raise ModularGraphValidationError("output module cannot have outgoing edges")
    if modules[pipeline.entry_module_id].input_type is not ModularValueType.QUERY:
        raise ModularGraphValidationError("entry module must accept a query")
    if modules[pipeline.output_module_id].output_type is not ModularValueType.ANSWER:
        raise ModularGraphValidationError("output module must produce an answer")

    queue = deque(
        module_id for module_id, count in incoming_count.items() if count == 0
    )
    visited: list[str] = []
    remaining_incoming = dict(incoming_count)
    while queue:
        module_id = queue.popleft()
        visited.append(module_id)
        for target_id in outgoing[module_id]:
            remaining_incoming[target_id] -= 1
            if remaining_incoming[target_id] == 0:
                queue.append(target_id)
    if len(visited) != len(modules):
        raise ModularGraphValidationError("module graph contains a cycle")
    reachable = {pipeline.entry_module_id}
    for module_id in visited:
        if module_id in reachable:
            reachable.update(outgoing[module_id])
    if reachable != set(modules):
        raise ModularGraphValidationError("all modules must be reachable from the entry")

    can_reach_output = {pipeline.output_module_id}
    for module_id in reversed(visited):
        if any(target_id in can_reach_output for target_id in outgoing[module_id]):
            can_reach_output.add(module_id)
    if can_reach_output != set(modules):
        raise ModularGraphValidationError("all modules must be able to reach the output")


class ModularExecutor:
    """Execute a validated graph once, within its declared step bound."""

    def __init__(self, pipeline: ModularPipelineSpec, runner: ModuleRunner) -> None:
        validate_modular_pipeline(pipeline)
        self._pipeline = pipeline
        self._runner = runner

    async def execute(self, query: str) -> tuple[ModuleValue, list[ModuleExecution]]:
        _validate_payload(ModuleValue(ModularValueType.QUERY, query))
        modules = {module.module_id: module for module in self._pipeline.modules}
        order = {
            module.module_id: index
            for index, module in enumerate(self._pipeline.modules)
        }
        incoming: dict[str, list[str]] = defaultdict(list)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self._pipeline.edges:
            incoming[edge.target_id].append(edge.source_id)
            outgoing[edge.source_id].append(edge.target_id)

        pending = {module_id: len(sources) for module_id, sources in incoming.items()}
        pending.setdefault(self._pipeline.entry_module_id, 0)
        ready = [self._pipeline.entry_module_id]
        outputs: dict[str, ModuleValue] = {}
        executions: list[ModuleExecution] = []
        while ready:
            ready.sort(key=order.__getitem__)
            module_id = ready.pop(0)
            module = modules[module_id]
            sequence = len(executions)
            if sequence >= self._pipeline.max_steps:
                raise ModularGraphValidationError("pipeline exceeded its execution bound")
            try:
                module_input = (
                    ModuleValue(ModularValueType.QUERY, query)
                    if module_id == self._pipeline.entry_module_id
                    else _merge_inputs(
                        module.input_type,
                        [outputs[item] for item in incoming[module_id]],
                    )
                )
                if module_input.value_type is not module.input_type:
                    raise ModularGraphValidationError(
                        f"module {module_id} received an incompatible input type"
                    )
                _validate_payload(module_input)
                output, detail = await self._runner(module, module_input)
                if output.value_type is not module.output_type:
                    raise ModularGraphValidationError(
                        f"module {module_id} returned an incompatible output type"
                    )
                _validate_payload(output)
            except ModularModuleExecutionError:
                raise
            except Exception as exc:
                raise ModularModuleExecutionError(
                    module_id=module.module_id,
                    capability_id=module.capability_id,
                    sequence=sequence,
                    cause=exc,
                ) from exc
            outputs[module_id] = output
            executions.append(
                ModuleExecution(
                    module=module,
                    output=output,
                    detail=detail,
                    input_value=module_input,
                )
            )
            for target_id in outgoing[module_id]:
                pending[target_id] -= 1
                if pending[target_id] == 0:
                    ready.append(target_id)

        if self._pipeline.output_module_id not in outputs:
            raise ModularGraphValidationError("pipeline did not produce its declared output")
        return outputs[self._pipeline.output_module_id], executions


def _merge_inputs(
    value_type: ModularValueType,
    values: list[ModuleValue],
) -> ModuleValue:
    if not values or any(value.value_type is not value_type for value in values):
        raise ModularGraphValidationError("module inputs are missing or incompatible")
    for value in values:
        _validate_payload(value)
    if value_type is ModularValueType.QUERIES:
        return ModuleValue(
            value_type,
            list(
                dict.fromkeys(
                    query for value in values for query in value.value
                )
            ),
        )
    if value_type in {
        ModularValueType.DOCUMENTS,
        ModularValueType.GRADED_DOCUMENTS,
    }:
        return ModuleValue(value_type, _merge_documents(values))
    if len(values) == 1:
        return values[0]
    raise ModularGraphValidationError(f"multiple {value_type.value} inputs cannot be merged")


def _validate_payload(module_value: ModuleValue) -> None:
    value = module_value.value
    value_type = module_value.value_type
    if value_type in {ModularValueType.QUERY, ModularValueType.ANSWER}:
        is_valid = isinstance(value, str)
    elif value_type is ModularValueType.QUERIES:
        is_valid = isinstance(value, list | tuple) and all(
            isinstance(item, str) for item in value
        )
    else:
        is_valid = isinstance(value, list | tuple) and all(
            _is_retrieval_result(item) for item in value
        )
    if not is_valid:
        raise ModularGraphValidationError(
            f"invalid {value_type.value} payload at module boundary"
        )


def _is_retrieval_result(value: object) -> bool:
    from app.rag.engine import RetrievalResult

    return (
        isinstance(value, RetrievalResult)
        and isinstance(value.chunk_id, str)
        and isinstance(value.content, str)
        and not isinstance(value.score, bool)
        and isinstance(value.score, Real)
        and isinstance(value.source_metadata, Mapping)
        and isinstance(value.retrieval_legs, list | tuple)
        and all(isinstance(leg, str) for leg in value.retrieval_legs)
        and isinstance(value.component_scores, Mapping)
        and all(
            isinstance(key, str)
            and not isinstance(score, bool)
            and isinstance(score, Real)
            for key, score in value.component_scores.items()
        )
    )


def _merge_documents(values: list[ModuleValue]) -> list[Any]:
    from app.rag.engine import RetrievalResult

    by_chunk_id: dict[str, list[RetrievalResult]] = defaultdict(list)
    for value in values:
        for document in value.value:
            by_chunk_id[document.chunk_id].append(document)

    merged: list[RetrievalResult] = []
    for candidates in by_chunk_id.values():
        ranked = sorted(candidates, key=_document_rank_key)
        strongest = ranked[0]
        component_scores: dict[str, float] = {}
        for candidate in ranked:
            for component, score in candidate.component_scores.items():
                component_scores[component] = max(
                    float(score),
                    component_scores.get(component, float("-inf")),
                )
        retrieval_legs = sorted(
            {leg for candidate in ranked for leg in candidate.retrieval_legs}
        )
        provenance = _source_provenance(ranked)
        source_metadata = {
            key: value
            for key, value in strongest.source_metadata.items()
            if key not in {"component_scores", "retrieval_legs", "source_provenance"}
        }
        source_metadata.update(
            {
                "component_scores": dict(sorted(component_scores.items())),
                "retrieval_legs": retrieval_legs,
                "source_provenance": provenance,
            }
        )
        merged.append(
            replace(
                strongest,
                source_metadata=source_metadata,
                retrieval_legs=retrieval_legs,
                component_scores=dict(sorted(component_scores.items())),
            )
        )
    return sorted(merged, key=lambda item: (-item.score, item.chunk_id))


def _document_rank_key(document: Any) -> tuple[float, str, str]:
    return (
        -float(document.score),
        document.content,
        json.dumps(document.source_metadata, sort_keys=True, default=str),
    )


def _source_provenance(documents: list[Any]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for document in documents:
        existing = document.source_metadata.get("source_provenance")
        candidates = existing if isinstance(existing, list | tuple) else ()
        if not candidates:
            candidates = (
                {
                    key: value
                    for key, value in document.source_metadata.items()
                    if key
                    not in {"component_scores", "retrieval_legs", "source_provenance"}
                },
            )
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            record = dict(candidate)
            key = json.dumps(record, sort_keys=True, default=str)
            records[key] = record
    return [records[key] for key in sorted(records)]


__all__ = [
    "MODULAR_CAPABILITY_REGISTRY",
    "ModularExecutor",
    "ModularGraphValidationError",
    "ModularModuleExecutionError",
    "ModularPipelineSpec",
    "ModularValueType",
    "ModuleCapability",
    "ModuleEdge",
    "ModuleExecution",
    "ModuleSpec",
    "ModuleValue",
    "default_modular_pipeline",
    "modular_pipeline_from_config",
    "validate_modular_pipeline",
]