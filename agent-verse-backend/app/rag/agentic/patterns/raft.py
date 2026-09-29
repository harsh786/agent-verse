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

_EXACT_FILTER_FIELDS = (
    ("raft_dataset_id", "dataset_id"),
    ("raft_provider_id", "provider_id"),
    ("raft_base_model", "base_model"),
    ("raft_capability", "capability"),
)


class RAFTRAGRuntimeAdapter(RAFTRAGRuntimeContract):
    """Answer with the collection's deployed fine-tuned RAFT model — or not at all.

    The model that synthesizes the answer is the one deployed for the collection
    (``RAFTService.deploy_job``) and served by its provider's inference adapter.
    When there is no deployed, servable model the strategy is *unavailable*: it
    raises instead of degrading to plain retrieval, because an answer-less RAFT
    result would be synthesized downstream by the base model while still being
    reported as RAFT.
    """

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
        if not isinstance(service, RAFTService):
            raise RetrievalStrategyExecutionError(self.strategy.value, "raft_service_unavailable")
        try:
            job = await service.resolve_deployed_model(
                context.tenant_context,
                collection_id=request.collection_id,
            )
        except RAFTModelUnavailableError as exc:
            raise RetrievalStrategyExecutionError(self.strategy.value, str(exc)) from exc
        for filter_key, job_field in _EXACT_FILTER_FIELDS:
            pinned = request.filters.get(filter_key)
            if pinned is not None and pinned != getattr(job, job_field):
                raise RetrievalStrategyExecutionError(
                    self.strategy.value,
                    f"requested {filter_key} does not match the deployed RAFT model",
                )

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
                        "dataset_id": job.dataset_id,
                        "compatibility_key": job.compatibility_key,
                        "capability": job.capability,
                    },
                )
            ],
        )


__all__ = ["RAFTRAGRuntimeAdapter"]
