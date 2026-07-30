"""Canonical Modular RAG adapter over validated internal module graphs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, ClassVar

from app.providers.base import CompletionRequest, Message
from app.rag.contracts import (
    ModularRAGRuntimeAdapter as ModularRAGRuntimeContract,
)
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.engine import (
    RetrievalResult,
    RetrievalStrategyExecutionError,
    merge_grounding_results,
)
from app.rag.modular import (
    MODULAR_CAPABILITY_REGISTRY,
    ModularExecutor,
    ModularPipelineSpec,
    ModularValueType,
    ModuleSpec,
    ModuleValue,
    default_modular_pipeline,
    validate_modular_pipeline,
)

_EXPAND_SYSTEM = (
    "Expand the query into distinct factual retrieval queries. Return only a JSON array "
    "of non-empty strings and obey the requested maximum query count."
)
_GRADE_SYSTEM = (
    "Grade which candidate chunks directly support the question. Return only JSON with "
    "accepted_chunk_ids (an array of chunk IDs) and reason (a non-empty string)."
)
_SYNTHESIZE_SYSTEM = (
    "Answer only from the supplied evidence. If evidence is insufficient, state that "
    "clearly. Do not introduce unsupported claims."
)


class ModularRAGRuntimeAdapter(ModularRAGRuntimeContract):
    """Execute a safe default or validated per-agent Modular RAG pipeline."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.MODULAR

    def __init__(self, pipeline: ModularPipelineSpec | None = None) -> None:
        self._pipeline = pipeline
        if pipeline is not None:
            validate_modular_pipeline(pipeline)

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.gateway import _canonical_result, _extend_trace

        if context is None:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "tenant-scoped gateway context is required"
            )
        pipeline, execution_request, execution_context = self._resolve_pipeline(
            request,
            context,
        )
        evidence: list[dict[str, Any]] = []

        async def run_module(
            module: ModuleSpec,
            module_input: ModuleValue,
        ) -> tuple[ModuleValue, Mapping[str, Any]]:
            self._require_capabilities(module, execution_context)
            return await self._execute_module(
                module,
                module_input,
                execution_request,
                execution_context,
                evidence,
            )

        output, executions = await ModularExecutor(pipeline, run_module).execute(
            execution_request.query
        )
        terminal_execution = next(
            execution
            for execution in executions
            if execution.module.module_id == pipeline.output_module_id
        )
        terminal_input = terminal_execution.input_value
        if (
            terminal_input is None
            or terminal_input.value_type is not ModularValueType.DOCUMENTS
        ):
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                "declared output module did not consume documents",
            )
        terminal_documents: list[RetrievalResult] = list(terminal_input.value)
        result = _canonical_result(
            execution_request,
            self.strategy,
            terminal_documents,
            evidence,
        ).model_copy(update={"answer": output.value})
        traces = [
            RAGStrategyTrace(
                strategy=self.strategy,
                action="modular_module",
                status="complete",
                detail={
                    "sequence": sequence,
                    "module_id": execution.module.module_id,
                    "capability_id": execution.module.capability_id,
                    "input_type": execution.module.input_type.value,
                    "output_type": execution.module.output_type.value,
                    **dict(execution.detail),
                },
            )
            for sequence, execution in enumerate(executions)
        ]
        return _extend_trace(result, traces)

    def _resolve_pipeline(
        self,
        request: RAGExecutionRequest,
        context: Any,
    ) -> tuple[ModularPipelineSpec, RAGExecutionRequest, Any]:
        trusted_pipeline = context.dependencies.modular_pipeline
        if self._pipeline is not None and trusted_pipeline is not None:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                "pipeline cannot be supplied by both adapter and trusted dependencies",
            )
        try:
            pipeline = self._pipeline or trusted_pipeline or default_modular_pipeline()
            validate_modular_pipeline(pipeline)
        except ValueError as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, f"invalid modular pipeline: {exc}"
            ) from exc

        filters = {
            key: value
            for key, value in request.filters.items()
            if key != "modular_pipeline"
        }
        return (
            pipeline,
            request.model_copy(update={"filters": filters}),
            replace(context, filters=filters),
        )

    def _require_capabilities(self, module: ModuleSpec, context: Any) -> None:
        capability = MODULAR_CAPABILITY_REGISTRY[module.capability_id]
        if capability.requires_llm and (
            context.llm is None
            or context.llm.provider is None
            or not context.llm.model
        ):
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                f"{module.capability_id} capability requires a resolved LLM",
            )
        if capability.requires_embedder and (
            context.dependencies.embedder is None
            or not callable(getattr(context.dependencies.embedder, "embed", None))
        ):
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                f"{module.capability_id} capability requires an embedder",
            )
        if capability.requires_database and context._db_operation_runner is None:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                f"{module.capability_id} capability requires persistence",
            )

    async def _execute_module(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        if module.capability_id == "query_expander":
            return await self._expand(module, module_input, request, context, evidence)
        if module.capability_id == "retriever":
            return await self._retrieve(module, module_input, request, context, evidence)
        if module.capability_id == "reranker":
            return await self._rerank(module, module_input, request, context, evidence)
        if module.capability_id == "grader":
            return await self._grade(module, module_input, request, context, evidence)
        if module.capability_id == "fallback":
            return await self._fallback(module, module_input, request, context, evidence)
        if module.capability_id == "synthesizer":
            return await self._synthesize(module, module_input, request, context, evidence)
        raise RetrievalStrategyExecutionError(
            self.strategy.value,
            f"module capability has no executor: {module.capability_id}",
        )

    async def _expand(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        del request, evidence
        max_queries = module.config.get("max_queries", 2)
        if isinstance(max_queries, bool) or not isinstance(max_queries, int):
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "query_expander max_queries must be an integer"
            )
        if not 1 <= max_queries <= 4:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "query_expander max_queries is outside the safe bound"
            )
        response = await context.llm.provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=_EXPAND_SYSTEM),
                    Message(
                        role="user",
                        content=f"Maximum: {max_queries}\nQuery: {module_input.value}",
                    ),
                ],
                model=context.llm.model,
                max_tokens=240,
                temperature=0.0,
            )
        )
        try:
            payload = json.loads(response.content)
            if not isinstance(payload, list) or not all(
                isinstance(query, str) and query.strip() for query in payload
            ):
                raise TypeError("expanded queries must be non-empty strings")
        except (json.JSONDecodeError, TypeError) as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "query_expander returned invalid typed output"
            ) from exc
        queries = list(dict.fromkeys(query.strip() for query in payload))
        if not queries or len(queries) > max_queries:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "query_expander exceeded its query bound"
            )
        return ModuleValue(ModularValueType.QUERIES, queries), {
            "query_count": len(queries)
        }

    async def _retrieve(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        del module
        from app.rag.gateway import _embed_text, _search_persisted

        groups: list[list[RetrievalResult]] = []
        for query in module_input.value:
            embedding = await _embed_text(context, query, self.strategy)
            query_evidence: list[dict[str, Any]] = []
            results = await _search_persisted(
                context,
                request,
                query=query,
                embedding=embedding,
                retrieval_mode="hybrid",
                evidence=query_evidence,
            )
            for item in query_evidence:
                item.update({"modular_module": "retriever", "expanded_query": query})
            evidence.extend(query_evidence)
            groups.append(results)
        merged = merge_grounding_results(groups, top_k=request.top_k)
        return ModuleValue(ModularValueType.DOCUMENTS, merged), {
            "query_count": len(module_input.value),
            "result_count": len(merged),
        }

    async def _rerank(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        del module, context, evidence
        documents = sorted(
            module_input.value,
            key=lambda document: (-document.score, document.chunk_id),
        )[: request.top_k]
        return ModuleValue(ModularValueType.DOCUMENTS, documents), {
            "reranker": "canonical_score_order",
            "result_count": len(documents),
        }

    async def _grade(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        del module, evidence
        documents: list[RetrievalResult] = module_input.value
        candidates = "\n".join(
            f"[{document.chunk_id}] {document.content[:800]}" for document in documents
        )
        response = await context.llm.provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=_GRADE_SYSTEM),
                    Message(
                        role="user",
                        content=f"Question: {request.query}\nCandidates:\n{candidates}",
                    ),
                ],
                model=context.llm.model,
                max_tokens=300,
                temperature=0.0,
            )
        )
        try:
            payload = json.loads(response.content)
            accepted_ids = payload["accepted_chunk_ids"]
            reason = payload["reason"]
            if (
                not isinstance(accepted_ids, list)
                or not all(isinstance(chunk_id, str) for chunk_id in accepted_ids)
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise TypeError("invalid grader fields")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "grader returned invalid typed output"
            ) from exc
        known_ids = {document.chunk_id for document in documents}
        if any(chunk_id not in known_ids for chunk_id in accepted_ids):
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "grader accepted an unknown chunk"
            )
        accepted = [
            document for document in documents if document.chunk_id in accepted_ids
        ]
        return ModuleValue(ModularValueType.GRADED_DOCUMENTS, accepted), {
            "accepted_count": len(accepted),
            "reason": reason.strip(),
        }

    async def _fallback(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        documents: list[RetrievalResult] = module_input.value
        if documents:
            return ModuleValue(ModularValueType.DOCUMENTS, documents), {
                "fallback_invoked": False,
                "result_count": len(documents),
            }

        from app.rag.contracts import resolve_rag_strategy
        from app.rag.gateway import execute_core_strategy

        configured_strategy = module.config.get("strategy", RAGStrategy.HYBRID.value)
        if not isinstance(configured_strategy, str):
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "fallback strategy must be a canonical string ID"
            )
        fallback_strategy = resolve_rag_strategy(configured_strategy)
        if fallback_strategy is self.strategy:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "fallback cannot recursively invoke Modular RAG"
            )
        if fallback_strategy not in context.dependencies.available_strategies:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                f"fallback capability is unavailable: {fallback_strategy.value}",
            )
        fallback_dependencies = replace(
            context.dependencies,
            llm=context.dependencies.strategy_llms.get(
                fallback_strategy,
                context.llm,
            ),
        )
        fallback_context = replace(
            context,
            strategy=fallback_strategy,
            dependencies=fallback_dependencies,
        )
        fallback_result = await execute_core_strategy(
            fallback_strategy,
            request,
            fallback_context,
        )
        fallback_documents = [
            RetrievalResult(
                chunk_id=citation.chunk_id,
                content=citation.content,
                score=citation.score,
                source_metadata={
                    **citation.metadata,
                    "source": citation.source,
                    "modular_fallback_strategy": fallback_strategy.value,
                },
                retrieval_legs=[fallback_strategy.value],
            )
            for citation in fallback_result.citations
        ]
        evidence.append(
            {
                "component": "modular_fallback",
                "strategy": fallback_strategy.value,
                "result_count": len(fallback_documents),
            }
        )
        return ModuleValue(ModularValueType.DOCUMENTS, fallback_documents), {
            "fallback_invoked": True,
            "fallback_strategy": fallback_strategy.value,
            "result_count": len(fallback_documents),
        }

    async def _synthesize(
        self,
        module: ModuleSpec,
        module_input: ModuleValue,
        request: RAGExecutionRequest,
        context: Any,
        evidence: list[dict[str, Any]],
    ) -> tuple[ModuleValue, Mapping[str, Any]]:
        del module, evidence
        documents: list[RetrievalResult] = module_input.value
        rendered = "\n\n".join(
            f"[{document.chunk_id}] {document.content}" for document in documents
        )
        response = await context.llm.provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=_SYNTHESIZE_SYSTEM),
                    Message(
                        role="user",
                        content=f"Evidence:\n{rendered}\n\nQuestion: {request.query}",
                    ),
                ],
                model=context.llm.model,
                max_tokens=800,
                temperature=0.0,
            )
        )
        answer = response.content.strip()
        if not answer:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "synthesizer returned empty content"
            )
        return ModuleValue(ModularValueType.ANSWER, answer), {
            "evidence_count": len(documents)
        }


__all__ = ["ModularRAGRuntimeAdapter"]