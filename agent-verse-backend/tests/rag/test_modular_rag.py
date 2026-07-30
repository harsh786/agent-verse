"""Validated graph and execution coverage for canonical Modular RAG."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.rag.agentic.patterns.modular import ModularRAGRuntimeAdapter
from app.rag.contracts import (
    DIRECT_CORE_RAG_STRATEGIES,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
)
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError
from app.rag.gateway import (
    ResolvedLLM,
    RetrievalExecutionContext,
    RetrievalRuntimeDependencies,
    core_strategy_capabilities,
)
from app.rag.modular import (
    MODULAR_CAPABILITY_REGISTRY,
    ModularExecutor,
    ModularGraphValidationError,
    ModularModuleExecutionError,
    ModularPipelineSpec,
    ModularValueType,
    ModuleEdge,
    ModuleSpec,
    ModuleValue,
    default_modular_pipeline,
    modular_pipeline_from_config,
    validate_modular_pipeline,
)
from app.tenancy.context import TenantContext


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        system = str(request.messages[0].content).casefold()
        if "expand" in system:
            content = '["retention period", "records retention policy"]'
        elif "grade" in system:
            content = '{"accepted_chunk_ids": ["chunk-1"], "reason": "supported"}'
        else:
            content = "Records are retained for seven years."
        return CompletionResponse(content=content, model=request.model)


class RecordingEmbedder:
    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(
            embeddings=[[float(len(text)), 1.0] for text in request.texts]
        )


def request() -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id="tenant-1",
        query="What is the retention period?",
        requested_strategy_id=RAGStrategy.MODULAR.value,
        collection_id="collection-1",
        top_k=3,
    )


def context(
    *,
    provider: object | None = None,
    available_strategies: tuple[RAGStrategy, ...] = (RAGStrategy.HYBRID,),
) -> RetrievalExecutionContext:
    async def unused_runner(operation: Any) -> Any:
        return await operation(SimpleNamespace())

    resolved_llm = (
        ResolvedLLM(provider=provider, model="modular-model", provider_type="test")
        if provider is not None
        else None
    )
    return RetrievalExecutionContext(
        tenant_context=TenantContext(
            tenant_id="tenant-1",
            api_key_id="key-1",
            plan="enterprise",
        ),
        strategy=RAGStrategy.MODULAR,
        filters={},
        dependencies=RetrievalRuntimeDependencies(
            embedder=RecordingEmbedder(),
            llm=resolved_llm,
            graph_capability=None,
            search_capability=None,
            policy_services=(),
            available_strategies=available_strategies,
        ),
        _db_operation_runner=unused_runner,
    )


def pipeline_with_fallback(strategy_id: str) -> Any:
    pipeline = default_modular_pipeline()
    return modular_pipeline_from_config(
        {
            "modules": [
                {
                    "id": module.module_id,
                    "capability": module.capability_id,
                    "config": (
                        {"strategy": strategy_id}
                        if module.capability_id == "fallback"
                        else dict(module.config)
                    ),
                }
                for module in pipeline.modules
            ],
            "edges": [
                {"source": edge.source_id, "target": edge.target_id}
                for edge in pipeline.edges
            ],
            "entry": pipeline.entry_module_id,
            "output": pipeline.output_module_id,
        }
    )


def fan_in_pipeline() -> ModularPipelineSpec:
    return modular_pipeline_from_config(
        {
            "modules": [
                {
                    "id": "expand",
                    "capability": "query_expander",
                    "config": {"max_queries": 1},
                },
                {"id": "retrieve-primary", "capability": "retriever"},
                {"id": "retrieve-secondary", "capability": "retriever"},
                {"id": "rerank", "capability": "reranker"},
                {"id": "synthesize", "capability": "synthesizer"},
            ],
            "edges": [
                {"source": "expand", "target": "retrieve-primary"},
                {"source": "expand", "target": "retrieve-secondary"},
                {"source": "retrieve-primary", "target": "rerank"},
                {"source": "retrieve-secondary", "target": "rerank"},
                {"source": "rerank", "target": "synthesize"},
            ],
            "entry": "expand",
            "output": "synthesize",
            "max_branches": 2,
            "max_steps": 5,
        }
    )


def test_default_pipeline_is_immutable_and_valid() -> None:
    pipeline = default_modular_pipeline()

    validate_modular_pipeline(pipeline)

    assert [module.capability_id for module in pipeline.modules] == [
        "query_expander",
        "retriever",
        "reranker",
        "grader",
        "fallback",
        "synthesizer",
    ]
    with pytest.raises(FrozenInstanceError):
        pipeline.modules[0].module_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        pipeline.modules[0].config["max_queries"] = 99  # type: ignore[index]


def test_validator_rejects_incompatible_typed_edge() -> None:
    pipeline = modular_pipeline_from_config(
        {
            "modules": [
                {"id": "expand", "capability": "query_expander"},
                {"id": "synthesize", "capability": "synthesizer"},
            ],
            "edges": [{"source": "expand", "target": "synthesize"}],
        }
    )

    with pytest.raises(ModularGraphValidationError, match="incompatible"):
        validate_modular_pipeline(pipeline)


def test_validator_rejects_reachable_dead_end_branch() -> None:
    pipeline = modular_pipeline_from_config(
        {
            "modules": [
                {"id": "expand", "capability": "query_expander"},
                {"id": "retrieve-output", "capability": "retriever"},
                {"id": "retrieve-dead-end", "capability": "retriever"},
                {"id": "synthesize", "capability": "synthesizer"},
            ],
            "edges": [
                {"source": "expand", "target": "retrieve-output"},
                {"source": "expand", "target": "retrieve-dead-end"},
                {"source": "retrieve-output", "target": "synthesize"},
            ],
            "entry": "expand",
            "output": "synthesize",
        }
    )

    with pytest.raises(ModularGraphValidationError, match="reach the output"):
        validate_modular_pipeline(pipeline)


def test_validated_per_agent_configuration_preserves_registered_types() -> None:
    pipeline = modular_pipeline_from_config(
        {
            "modules": [
                {
                    "id": module.module_id,
                    "capability": module.capability_id,
                    "config": dict(module.config),
                }
                for module in default_modular_pipeline().modules
            ],
            "edges": [
                {"source": edge.source_id, "target": edge.target_id}
                for edge in default_modular_pipeline().edges
            ],
            "entry": "expand",
            "output": "synthesize",
            "max_branches": 2,
            "max_steps": 6,
        }
    )

    validate_modular_pipeline(pipeline)

    assert all(
        module.input_type is MODULAR_CAPABILITY_REGISTRY[module.capability_id].input_type
        and module.output_type
        is MODULAR_CAPABILITY_REGISTRY[module.capability_id].output_type
        for module in pipeline.modules
    )


def test_validator_rejects_branching_above_pipeline_bound() -> None:
    modules = (
        ModuleSpec(
            module_id="expand",
            capability_id="query_expander",
            input_type=ModularValueType.QUERY,
            output_type=ModularValueType.QUERIES,
        ),
        *(
            ModuleSpec(
                module_id=f"retrieve-{index}",
                capability_id="retriever",
                input_type=ModularValueType.QUERIES,
                output_type=ModularValueType.DOCUMENTS,
            )
            for index in range(3)
        ),
    )
    pipeline = SimpleNamespace(
        modules=modules,
        edges=tuple(
            ModuleEdge(source_id="expand", target_id=f"retrieve-{index}")
            for index in range(3)
        ),
        entry_module_id="expand",
        output_module_id="retrieve-0",
        max_branches=2,
        max_steps=8,
    )

    with pytest.raises(ModularGraphValidationError, match="branch"):
        validate_modular_pipeline(pipeline)


def test_validator_binds_query_expansion_to_pipeline_branch_limit() -> None:
    config = {
        "modules": [
            {
                "id": module.module_id,
                "capability": module.capability_id,
                "config": (
                    {"max_queries": 3}
                    if module.capability_id == "query_expander"
                    else dict(module.config)
                ),
            }
            for module in default_modular_pipeline().modules
        ],
        "edges": [
            {"source": edge.source_id, "target": edge.target_id}
            for edge in default_modular_pipeline().edges
        ],
        "entry": "expand",
        "output": "synthesize",
        "max_branches": 2,
    }

    with pytest.raises(ModularGraphValidationError, match="branch bound"):
        validate_modular_pipeline(modular_pipeline_from_config(config))


def test_validator_rejects_unknown_fallback_capability() -> None:
    pipeline = default_modular_pipeline()
    config = {
        "modules": [
            {
                "id": module.module_id,
                "capability": module.capability_id,
                "config": (
                    {"strategy": "renamed_hybrid"}
                    if module.capability_id == "fallback"
                    else dict(module.config)
                ),
            }
            for module in pipeline.modules
        ],
        "edges": [
            {"source": edge.source_id, "target": edge.target_id}
            for edge in pipeline.edges
        ],
        "entry": pipeline.entry_module_id,
        "output": pipeline.output_module_id,
    }

    with pytest.raises(ModularGraphValidationError, match="not canonical"):
        validate_modular_pipeline(modular_pipeline_from_config(config))


@pytest.mark.parametrize(
    "fallback_strategy",
    sorted(DIRECT_CORE_RAG_STRATEGIES, key=lambda strategy: strategy.value),
)
async def test_every_supported_fallback_validates_and_uses_internal_dispatch(
    fallback_strategy: RAGStrategy,
) -> None:
    class EmptyGradeProvider(RecordingProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            response = await super().complete(request)
            if "grade" in str(request.messages[0].content).casefold():
                return CompletionResponse(
                    content='{"accepted_chunk_ids": [], "reason": "unsupported"}',
                    model=request.model,
                )
            return response

    pipeline = pipeline_with_fallback(fallback_strategy.value)
    validate_modular_pipeline(pipeline)
    fallback_result = RAGExecutionResult(
        requested_strategy_id=RAGStrategy.MODULAR.value,
        resolved_strategy_id=fallback_strategy,
        citations=[
            RAGCitation(
                citation_id="fallback:chunk-2",
                chunk_id="chunk-2",
                content="Fallback evidence.",
                score=0.88,
                source="fallback.pdf",
            )
        ],
    )
    retrieved = [
        RetrievalResult(
            chunk_id="chunk-1",
            content="Unsupported initial evidence.",
            score=0.4,
            source_metadata={"source": "initial.pdf"},
        )
    ]

    with (
        patch("app.rag.gateway._search_persisted", return_value=retrieved),
        patch(
            "app.rag.gateway.execute_core_strategy",
            new=AsyncMock(return_value=fallback_result),
        ) as internal_execute,
        patch(
            "app.rag.gateway.RetrievalGateway.execute",
            new=AsyncMock(side_effect=AssertionError("recursive public gateway call")),
        ) as public_execute,
    ):
        result = await ModularRAGRuntimeAdapter(pipeline).execute(
            request(),
            context(
                provider=EmptyGradeProvider(),
                available_strategies=(fallback_strategy,),
            ),
        )

    assert result.citations[0].chunk_id == "chunk-2"
    internal_execute.assert_awaited_once()
    assert internal_execute.await_args.args[0] is fallback_strategy
    public_execute.assert_not_awaited()


def test_supported_fallbacks_are_exactly_the_direct_internal_dispatch_branches() -> None:
    assert {
        RAGStrategy.NAIVE,
        RAGStrategy.HYBRID,
        RAGStrategy.HYDE,
        RAGStrategy.MULTI_HOP,
        RAGStrategy.FUSION,
        RAGStrategy.GRAPH,
        RAGStrategy.CORRECTIVE,
        RAGStrategy.WEB_AUGMENTED,
        RAGStrategy.RAPTOR,
        RAGStrategy.AGENTIC_CHUNKING,
    } == DIRECT_CORE_RAG_STRATEGIES


@pytest.mark.parametrize(
    "fallback_strategy",
    [
        RAGStrategy.ADAPTIVE,
        RAGStrategy.MODULAR,
        RAGStrategy.SPECULATIVE,
        RAGStrategy.AGENTIC,
        RAGStrategy.SELF_RAG,
        RAGStrategy.FLARE,
        RAGStrategy.COLBERT,
        RAGStrategy.RAFT,
    ],
)
def test_validator_rejects_unsupported_or_recursive_fallbacks(
    fallback_strategy: RAGStrategy,
) -> None:
    with pytest.raises(ModularGraphValidationError, match="cannot use internal dispatch"):
        validate_modular_pipeline(pipeline_with_fallback(fallback_strategy.value))


async def test_executor_uses_internal_primitives_and_emits_ordered_module_trace() -> None:
    provider = RecordingProvider()
    retrieved = [
        RetrievalResult(
            chunk_id="chunk-1",
            content="Policy records are retained for seven years.",
            score=0.91,
            source_metadata={"source": "policy.pdf"},
            retrieval_legs=["hybrid"],
        )
    ]

    async def search(*args: object, **kwargs: Any) -> list[RetrievalResult]:
        kwargs["evidence"].append(
            {"component": "hybrid", "result_count": 1, "query": kwargs["query"]}
        )
        return retrieved

    with (
        patch("app.rag.gateway._search_persisted", side_effect=search),
        patch(
            "app.rag.gateway.RetrievalGateway.execute",
            new=AsyncMock(side_effect=AssertionError("recursive public gateway call")),
        ) as public_execute,
    ):
        result = await ModularRAGRuntimeAdapter().execute(request(), context(provider=provider))

    module_trace = [
        trace for trace in result.strategy_trace if trace.action == "modular_module"
    ]
    assert [trace.detail["capability_id"] for trace in module_trace] == [
        "query_expander",
        "retriever",
        "reranker",
        "grader",
        "fallback",
        "synthesizer",
    ]
    assert [trace.detail["sequence"] for trace in module_trace] == list(range(6))
    assert result.answer == "Records are retained for seven years."
    assert result.citations[0].chunk_id == "chunk-1"
    assert result.resolved_strategy_id is RAGStrategy.MODULAR
    public_execute.assert_not_awaited()


async def test_fan_in_citations_use_exact_terminal_documents_and_dedupe_provenance() -> None:
    class FanInProvider(RecordingProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            self.requests.append(request)
            system = str(request.messages[0].content).casefold()
            if "expand" in system:
                content = '["retention policy"]'
            else:
                content = "Records are retained for seven years."
            return CompletionResponse(content=content, model=request.model)

    weak = RetrievalResult(
        chunk_id="shared",
        content="Older wording.",
        score=0.4,
        source_metadata={"source": "archive.pdf", "page": 2},
        retrieval_legs=["lexical"],
        component_scores={"lexical": 0.4},
    )
    strong = RetrievalResult(
        chunk_id="shared",
        content="Records are retained for seven years.",
        score=0.95,
        source_metadata={"source": "policy.pdf", "page": 7},
        retrieval_legs=["vector"],
        component_scores={"vector": 0.95},
    )
    unique = RetrievalResult(
        chunk_id="unique",
        content="The policy is reviewed annually.",
        score=0.7,
        source_metadata={"source": "review.pdf", "page": 1},
        retrieval_legs=["hybrid"],
        component_scores={"hybrid": 0.7},
    )
    search_results = iter(([weak, unique], [strong]))
    provider = FanInProvider()

    with patch(
        "app.rag.gateway._search_persisted",
        side_effect=lambda *args, **kwargs: next(search_results),
    ):
        result = await ModularRAGRuntimeAdapter(fan_in_pipeline()).execute(
            request(),
            context(provider=provider),
        )

    assert [citation.chunk_id for citation in result.citations] == ["shared", "unique"]
    assert result.citations[0].content == strong.content
    assert result.citations[0].score == pytest.approx(0.95)
    assert result.citations[0].metadata["retrieval_legs"] == ["lexical", "vector"]
    assert result.citations[0].metadata["component_scores"] == {
        "lexical": 0.4,
        "vector": 0.95,
    }
    assert result.citations[0].metadata["source_provenance"] == [
        {"page": 2, "source": "archive.pdf"},
        {"page": 7, "source": "policy.pdf"},
    ]
    synthesis_prompt = str(provider.requests[-1].messages[-1].content)
    assert f"[shared] {strong.content}" in synthesis_prompt
    assert weak.content not in synthesis_prompt


@pytest.mark.parametrize(
    ("output_type", "malformed"),
    [
        (ModularValueType.QUERIES, ["valid", 7]),
        (ModularValueType.DOCUMENTS, [{"chunk_id": "not-a-result"}]),
        (ModularValueType.GRADED_DOCUMENTS, "not-a-document-list"),
        (ModularValueType.ANSWER, 42),
    ],
)
async def test_executor_rejects_malformed_module_output_payloads(
    output_type: ModularValueType,
    malformed: object,
) -> None:
    module = ModuleSpec(
        module_id="module",
        capability_id="query_expander",
        input_type=ModularValueType.QUERY,
        output_type=output_type,
    )
    pipeline = SimpleNamespace(
        modules=(module,),
        edges=(),
        entry_module_id="module",
        output_module_id="module",
        max_branches=1,
        max_steps=1,
    )

    async def malformed_runner(
        module: ModuleSpec,
        module_input: ModuleValue,
    ) -> tuple[ModuleValue, dict[str, Any]]:
        del module, module_input
        return ModuleValue(output_type, malformed), {}

    with (
        patch("app.rag.modular.validate_modular_pipeline", return_value=None),
        pytest.raises(ModularModuleExecutionError) as raised,
    ):
        await ModularExecutor(pipeline, malformed_runner).execute("query")

    assert isinstance(raised.value.__cause__, ModularGraphValidationError)
    assert "payload" in str(raised.value.__cause__)


async def test_executor_rejects_malformed_payload_during_fan_in_merge() -> None:
    values = [
        ModuleValue(
            ModularValueType.DOCUMENTS,
            [RetrievalResult("chunk-1", "ok", 0.8, {})],
        ),
        ModuleValue(ModularValueType.DOCUMENTS, ["not-a-retrieval-result"]),
    ]

    with pytest.raises(ModularGraphValidationError, match="payload"):
        from app.rag.modular import _merge_inputs

        _merge_inputs(ModularValueType.DOCUMENTS, values)


async def test_request_pipeline_override_is_ignored_and_removed_from_execution_filters() -> None:
    class TrustedPipelineProvider(RecordingProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            self.requests.append(request)
            content = (
                '["retention policy"]'
                if "expand" in str(request.messages[0].content).casefold()
                else "Records are retained for seven years."
            )
            return CompletionResponse(content=content, model=request.model)

    untrusted_override = {"modules": "execute-me", "edges": []}
    pipeline = fan_in_pipeline()
    observed_filters: list[dict[str, Any]] = []
    execution_request = request().model_copy(
        update={
            "filters": {
                "department": "legal",
                "modular_pipeline": untrusted_override,
            }
        }
    )

    async def search(*args: object, **kwargs: Any) -> list[RetrievalResult]:
        del kwargs
        search_request = args[1]
        assert isinstance(search_request, RAGExecutionRequest)
        observed_filters.append(dict(search_request.filters))
        return [
            RetrievalResult(
                chunk_id="chunk-1",
                content="Records are retained for seven years.",
                score=0.9,
                source_metadata={"source": "policy.pdf"},
            )
        ]

    base_context = context(provider=TrustedPipelineProvider())
    trusted_context = replace(
        base_context,
        dependencies=replace(
            base_context.dependencies,
            modular_pipeline=pipeline,
        ),
    )
    with patch("app.rag.gateway._search_persisted", side_effect=search):
        result = await ModularRAGRuntimeAdapter().execute(
            execution_request,
            trusted_context,
        )

    assert observed_filters == [{"department": "legal"}, {"department": "legal"}]
    module_ids = [
        trace.detail["module_id"]
        for trace in result.strategy_trace
        if trace.action == "modular_module"
    ]
    assert module_ids == [
        "expand",
        "retrieve-primary",
        "retrieve-secondary",
        "rerank",
        "synthesize",
    ]


async def test_module_failure_preserves_typed_context_and_failed_trace() -> None:
    module = default_modular_pipeline().modules[0]
    pipeline = ModularPipelineSpec(
        modules=(module,),
        edges=(),
        entry_module_id=module.module_id,
        output_module_id=module.module_id,
        max_branches=1,
        max_steps=1,
    )
    cause = RuntimeError("provider unavailable")

    async def failing_runner(
        module: ModuleSpec,
        module_input: ModuleValue,
    ) -> tuple[ModuleValue, dict[str, Any]]:
        del module, module_input
        raise cause

    with (
        patch("app.rag.modular.validate_modular_pipeline", return_value=None),
        pytest.raises(ModularModuleExecutionError) as raised,
    ):
        await ModularExecutor(pipeline, failing_runner).execute("query")

    assert raised.value.module_id == "expand"
    assert raised.value.capability_id == "query_expander"
    assert raised.value.sequence == 0
    assert raised.value.__cause__ is cause
    assert raised.value.failed_trace == {
        "sequence": 0,
        "module_id": "expand",
        "capability_id": "query_expander",
        "status": "failed",
        "failure_type": "RuntimeError",
    }


async def test_missing_required_module_capability_fails_explicitly() -> None:
    with pytest.raises(ModularModuleExecutionError) as raised:
        await ModularRAGRuntimeAdapter().execute(request(), context())

    assert isinstance(raised.value.__cause__, RetrievalStrategyExecutionError)
    assert "query_expander capability requires a resolved LLM" in str(
        raised.value.__cause__
    )


async def test_empty_grade_never_silently_uses_an_unavailable_fallback() -> None:
    class EmptyGradeProvider(RecordingProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            response = await super().complete(request)
            if "grade" in str(request.messages[0].content).casefold():
                return CompletionResponse(
                    content='{"accepted_chunk_ids": [], "reason": "unsupported"}',
                    model=request.model,
                )
            return response

    retrieved = [
        RetrievalResult(
            chunk_id="chunk-1",
            content="Unrelated evidence.",
            score=0.4,
            source_metadata={"source": "unrelated.pdf"},
        )
    ]

    with (
        patch("app.rag.gateway._search_persisted", return_value=retrieved),
        pytest.raises(ModularModuleExecutionError) as raised,
    ):
        await ModularRAGRuntimeAdapter().execute(
            request(),
            context(
                provider=EmptyGradeProvider(),
                available_strategies=(),
            ),
        )

    assert isinstance(raised.value.__cause__, RetrievalStrategyExecutionError)
    assert "fallback capability is unavailable: hybrid" in str(raised.value.__cause__)


def test_modular_runtime_is_registered_as_its_concrete_capability() -> None:
    capability = core_strategy_capabilities()[RAGStrategy.MODULAR]

    assert isinstance(capability.adapter, ModularRAGRuntimeAdapter)
    assert capability.requires_embedder
    assert capability.requires_provider
    assert capability.requires_database