"""Canonical retrieval adapter for completed tenant-scoped RAFT models."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar

from app.rag.contracts import RAFTRAGRuntimeAdapter as RAFTRAGRuntimeContract
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.engine import RetrievalStrategyExecutionError
from app.rag.raft import RAFTModelUnavailableError, RAFTService


class RAFTRAGRuntimeAdapter(RAFTRAGRuntimeContract):
    """Retrieve only after a compatible completed RAFT model is durable."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.RAFT

    def __init__(self, service: RAFTService | None = None) -> None:
        self._service = service

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.gateway import (
            _canonical_result,
            _embed_text,
            _extend_trace,
            _search_persisted,
        )

        if context is None or not request.collection_id:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                "tenant-scoped collection context is required",
            )
        service = self._service or getattr(context.dependencies, "raft_service", None)

        # Honest degradation (D-8): when no completed RAFT model is durable for
        # this tenant/collection, fall back to hybrid retrieval with a recorded
        # reason rather than raising an unhandled error that breaks the request.
        # TODO(main.py): app.main.create_app wires RAFTService(providers={}),
        # so no completed model can ever exist and RAFT always degrades here.
        # Pass real fine-tune/inference providers so completed RAFT models can
        # actually serve inference before removing this fallback.
        degrade_cause: str | None = None
        if not isinstance(service, RAFTService):
            degrade_cause = "raft_service_unavailable"
        else:
            try:
                has_model = await service.has_completed_model(
                    context.tenant_context,
                    collection_id=request.collection_id,
                )
            except Exception:
                has_model = False
            if not has_model:
                degrade_cause = "no_completed_model"
        if degrade_cause is not None:
            return await self._degrade_to_hybrid(request, context, cause=degrade_cause)
        assert isinstance(service, RAFTService)

        dataset_id = request.filters.get("raft_dataset_id")
        provider_id = request.filters.get("raft_provider_id")
        base_model = request.filters.get("raft_base_model")
        capability = request.filters.get("raft_capability")
        if (
            not isinstance(dataset_id, str)
            or not dataset_id
            or not isinstance(provider_id, str)
            or not provider_id
            or not isinstance(base_model, str)
            or not base_model
            or not isinstance(capability, str)
            or not capability
        ):
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                "exact RAFT dataset, provider, base model, and capability are required",
            )
        try:
            compatibility_key = await service.compatibility_key_for_dataset(
                context.tenant_context,
                dataset_id=dataset_id,
                collection_id=request.collection_id,
                provider_id=provider_id,
                base_model=base_model,
                capability=capability,
            )
            job = await service.require_completed_model(
                context.tenant_context,
                compatibility_key=compatibility_key,
            )
        except RAFTModelUnavailableError as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                str(exc),
            ) from exc

        embedding = await _embed_text(context, request.query, self.strategy)
        evidence: list[dict[str, Any]] = []
        retrieval_filters = {
            key: value for key, value in request.filters.items() if not key.startswith("raft_")
        }
        retrieval_request = request.model_copy(update={"filters": retrieval_filters})
        retrieval_context = replace(context, filters=retrieval_filters)
        results = await _search_persisted(
            retrieval_context,
            retrieval_request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=evidence,
        )
        result = _canonical_result(request, self.strategy, results, evidence)
        try:
            answer = await service.infer(
                job,
                query=request.query,
                evidence=tuple(item.content for item in results),
            )
        except RAFTModelUnavailableError as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                str(exc),
            ) from exc
        result = result.model_copy(update={"answer": answer, "grounded": bool(results)})
        return _extend_trace(
            result,
            [
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="raft_retrieval",
                    status="complete",
                    detail={
                        "job_id": job.job_id,
                        "model_id": job.fine_tuned_model,
                        "provider_id": job.provider_id,
                        "base_model": job.base_model,
                        "compatibility_key": job.compatibility_key,
                        "capability": job.capability,
                    },
                )
            ],
        )

    async def _degrade_to_hybrid(
        self,
        request: RAGExecutionRequest,
        context: Any,
        *,
        cause: str,
    ) -> RAGExecutionResult:
        """Serve a recorded hybrid-retrieval result when RAFT is unavailable."""
        from app.rag.gateway import (
            _canonical_result,
            _embed_text,
            _extend_trace,
            _search_persisted,
        )

        embedding = await _embed_text(context, request.query, self.strategy)
        evidence: list[dict[str, Any]] = []
        results = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=evidence,
        )
        result = _canonical_result(request, self.strategy, results, evidence)
        result = result.model_copy(update={"grounded": bool(results)})
        return _extend_trace(
            result,
            [
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="raft_fallback",
                    status="degraded",
                    detail={
                        "reason": "raft_unavailable",
                        "fallback_retrieval": "hybrid",
                        "cause": cause,
                        "result_count": len(results),
                    },
                )
            ],
        )


__all__ = ["RAFTRAGRuntimeAdapter"]
