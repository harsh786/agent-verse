"""ColBERT token-level late-interaction reranking."""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from functools import partial
from threading import Lock
from typing import Any, ClassVar, Protocol, cast

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.contracts import ColBERTRAGRuntimeAdapter as ColBERTRAGRuntimeContract
from app.rag.contracts import RAGExecutionRequest, RAGExecutionResult, RAGStrategy
from app.rag.engine import RetrievalStrategyExecutionError
from app.rag_platform.reranker_contract import (
    AsyncCloseableProtocol,
    BoundedAsyncExecutor,
    RerankerInferenceError,
    RerankerLoadError,
    RerankerProtocol,
)

DEFAULT_COLBERT_CHECKPOINT = "colbert-ir/colbertv2.0"
_MAX_CANDIDATES = 100


class RAGatouilleColBERTModel(Protocol):
    """Supported blocking RAGatouille surface for checkpoint-correct scoring."""

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        k: int,
    ) -> object: ...


ColBERTModelLoader = Callable[[], RAGatouilleColBERTModel]


class ColBERTCompatibilityReranker(
    RerankerProtocol,
    AsyncCloseableProtocol,
    Protocol,
):
    """Async and synchronous scoring required by the legacy pattern wrapper."""

    def score_sync(self, query: str, documents: list[str]) -> list[float]: ...


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    magnitude_left = math.sqrt(sum(value * value for value in left))
    magnitude_right = math.sqrt(sum(value * value for value in right))
    if magnitude_left == 0.0 or magnitude_right == 0.0:
        return 0.0
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    ) / (magnitude_left * magnitude_right)


def maxsim_score(
    query_embeddings: list[list[float]],
    document_embeddings: list[list[float]],
) -> float:
    """Compute ColBERT MaxSim across every query token and document token."""
    if not query_embeddings or not document_embeddings:
        return 0.0
    similarities = [
        max(
            _cosine(query_token, document_token)
            for document_token in document_embeddings
        )
        for query_token in query_embeddings
    ]
    return sum(similarities)


def _load_colbert_model(
    checkpoint: str = DEFAULT_COLBERT_CHECKPOINT,
) -> RAGatouilleColBERTModel:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"(?s).*RAGatouille WARNING: Future Release Notice.*",
                category=UserWarning,
            )
            from ragatouille import RAGPretrainedModel  # type: ignore[import-untyped]

        return cast(
            RAGatouilleColBERTModel,
            RAGPretrainedModel.from_pretrained(checkpoint),
        )
    except Exception as exc:
        raise RerankerLoadError(
            f"ColBERT model could not be loaded: {checkpoint}"
        ) from exc


def _result_index(
    result: Mapping[str, object],
    documents: list[str],
) -> int:
    content = result.get("content")
    if not isinstance(content, str):
        raise RerankerInferenceError("ColBERT result content is missing or invalid")

    raw_index = result.get("result_index")
    if raw_index is not None:
        if (
            isinstance(raw_index, bool)
            or not isinstance(raw_index, int)
            or not 0 <= raw_index < len(documents)
        ):
            raise RerankerInferenceError("ColBERT result_index is invalid")
        if documents[raw_index] != content:
            raise RerankerInferenceError(
                "ColBERT result_index does not match result content"
            )
        return raw_index

    matching_indices = [
        index for index, document in enumerate(documents) if document == content
    ]
    if not matching_indices:
        raise RerankerInferenceError("ColBERT result content is not a candidate")
    if len(matching_indices) > 1:
        raise RerankerInferenceError(
            "ColBERT result content maps to multiple candidates"
        )
    return matching_indices[0]


def _scores_in_candidate_order(results: object, documents: list[str]) -> list[float]:
    if not isinstance(results, list) or len(results) != len(documents):
        raise RerankerInferenceError(
            "ColBERT returned a result count that does not match the candidates"
        )

    scores: list[float | None] = [None] * len(documents)
    for raw_result in results:
        if not isinstance(raw_result, Mapping):
            raise RerankerInferenceError("ColBERT returned an invalid result record")
        index = _result_index(raw_result, documents)
        if scores[index] is not None:
            raise RerankerInferenceError("ColBERT returned a duplicate candidate result")
        raw_score = raw_result.get("score")
        if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
            raise RerankerInferenceError("ColBERT result score is missing or invalid")
        score = float(raw_score)
        if not math.isfinite(score):
            raise RerankerInferenceError("ColBERT result score must be finite")
        scores[index] = score

    if any(score is None for score in scores):
        raise RerankerInferenceError("ColBERT omitted a candidate result")
    return cast(list[float], scores)


class ColBERTLateInteractionReranker(RerankerProtocol):
    """Lazy real ColBERT scorer with model work isolated from the event loop."""

    def __init__(
        self,
        *,
        checkpoint: str = DEFAULT_COLBERT_CHECKPOINT,
        model_loader: ColBERTModelLoader | None = None,
        max_workers: int = 1,
        max_queue_size: int = 0,
        backend_thread_safe: bool = False,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if not checkpoint.strip():
            raise ValueError("ColBERT checkpoint cannot be empty")
        self._model_loader = model_loader or partial(_load_colbert_model, checkpoint)
        self._backend_thread_safe = backend_thread_safe
        self._model: RAGatouilleColBERTModel | None = None
        self._model_lock = Lock()
        self._rerank_lock = Lock()
        worker_count = max_workers if backend_thread_safe else 1
        self._workers = BoundedAsyncExecutor(
            max_workers=worker_count,
            max_queue_size=max_queue_size,
            thread_name_prefix="colbert-reranker",
        )

    def _get_model(self) -> RAGatouilleColBERTModel:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = self._model_loader()
            except RerankerLoadError:
                raise
            except Exception as exc:
                raise RerankerLoadError("ColBERT model could not be loaded") from exc
            return self._model

    def _score_blocking(self, query: str, documents: list[str]) -> list[float]:
        model = self._get_model()
        try:
            inference_guard = (
                nullcontext() if self._backend_thread_safe else self._rerank_lock
            )
            with inference_guard:
                results = model.rerank(
                    query=query,
                    documents=documents,
                    k=len(documents),
                )
            return _scores_in_candidate_order(results, documents)
        except (RerankerLoadError, RerankerInferenceError):
            raise
        except Exception as exc:
            raise RerankerInferenceError(
                "ColBERT late-interaction scoring failed"
            ) from exc

    async def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return await self._workers.run(self._score_blocking, query, documents)

    async def aclose(self) -> None:
        """Cancel queued work and shut down the dedicated worker lifecycle."""
        await self._workers.aclose()

    def score_sync(self, query: str, documents: list[str]) -> list[float]:
        """Compatibility boundary that still performs model work in a worker thread."""
        if not documents:
            return []
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(self._score_blocking, query, documents).result()


class ColBERTRAGRuntimeAdapter(ColBERTRAGRuntimeContract):
    """Canonical persisted retrieval followed by real ColBERT late interaction."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.COLBERT

    def __init__(
        self,
        *,
        colbert_checkpoint: str = DEFAULT_COLBERT_CHECKPOINT,
        reranker: RerankerProtocol | None = None,
        owns_reranker: bool = False,
        candidate_multiplier: int = 4,
    ) -> None:
        if candidate_multiplier < 2:
            raise ValueError("ColBERT candidate_multiplier must be at least 2")
        self._reranker: RerankerProtocol
        if reranker is None:
            self._reranker = ColBERTLateInteractionReranker(
                checkpoint=colbert_checkpoint
            )
            self._owns_reranker = True
        else:
            self._reranker = reranker
            self._owns_reranker = owns_reranker
        self._candidate_multiplier = candidate_multiplier

    async def aclose(self) -> None:
        """Shut down an owned reranker lifecycle when the implementation exposes one."""
        if not self._owns_reranker:
            return
        close = getattr(self._reranker, "aclose", None)
        if close is not None:
            await close()

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.contracts import RAGStrategyTrace
        from app.rag.gateway import (
            _canonical_result,
            _embed_text,
            _extend_trace,
            _search_persisted,
        )

        if context is None:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "tenant-scoped gateway context is required"
            )
        candidate_limit = min(
            request.top_k * self._candidate_multiplier,
            _MAX_CANDIDATES,
        )
        embedding = await _embed_text(context, request.query, self.strategy)
        evidence: list[dict[str, Any]] = []
        candidates = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=evidence,
            top_k=candidate_limit,
        )
        try:
            rerank_scores = await self._reranker.score(
                request.query,
                [candidate.content for candidate in candidates],
            )
        except (RerankerLoadError, RerankerInferenceError):
            raise
        except Exception as exc:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "reranker failed"
            ) from exc
        if len(rerank_scores) != len(candidates):
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "reranker returned an invalid score count"
            )

        reranked = sorted(
            (
                replace(
                    candidate,
                    score=rerank_score,
                    source_metadata={
                        **candidate.source_metadata,
                        "original_score": candidate.score,
                        "colbert_score": rerank_score,
                        "rerank_score": rerank_score,
                    },
                    retrieval_legs=list(
                        dict.fromkeys([*candidate.retrieval_legs, self.strategy.value])
                    ),
                    component_scores={
                        **candidate.component_scores,
                        "colbert": rerank_score,
                    },
                )
                for candidate, rerank_score in zip(
                    candidates,
                    rerank_scores,
                    strict=True,
                )
            ),
            key=lambda candidate: (-candidate.score, candidate.chunk_id),
        )[: request.top_k]
        result = _canonical_result(request, self.strategy, reranked, evidence)
        return _extend_trace(
            result,
            [
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="colbert_late_interaction",
                    status="complete",
                    detail={
                        "candidate_count": len(candidates),
                        "candidate_limit": candidate_limit,
                        "returned_count": len(reranked),
                        "rerank_scores": {
                            candidate.chunk_id: candidate.score
                            for candidate in reranked
                        },
                    },
                )
            ],
        )


class ColBERTPattern(RAGPattern):
    """Compatibility wrapper over the real ColBERT late-interaction scorer."""

    def __init__(
        self,
        alpha: float = 0.5,
        *,
        reranker: ColBERTCompatibilityReranker | None = None,
        owns_reranker: bool = False,
    ) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError("alpha must be between zero and one")
        self._alpha = alpha
        self._reranker: ColBERTCompatibilityReranker
        if reranker is None:
            self._reranker = ColBERTLateInteractionReranker()
            self._owns_reranker = True
        else:
            self._reranker = reranker
            self._owns_reranker = owns_reranker

    async def aclose(self) -> None:
        """Shut down the compatibility wrapper's dedicated worker lifecycle."""
        if self._owns_reranker:
            await self._reranker.aclose()

    @property
    def pattern_id(self) -> str:
        return "colbert_late_interaction"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "ColBERT late interaction reranking with contextual token embeddings "
            f"and MaxSim scoring. Alpha={self._alpha}."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        del goal_properties
        try:
            from app.core.config import get_settings

            return bool(get_settings().enable_colbert)
        except Exception:
            return False

    def _merge_scores(
        self,
        chunks: list[dict[str, Any]],
        scores: list[float],
        top_k: int | None,
    ) -> list[dict[str, Any]]:
        if len(scores) != len(chunks):
            raise RerankerInferenceError("ColBERT returned an invalid score count")
        reranked = []
        for chunk, colbert_score in zip(chunks, scores, strict=True):
            original_score = float(chunk.get("score", 0.0))
            rerank_score = (
                self._alpha * colbert_score
                + (1.0 - self._alpha) * original_score
            )
            reranked.append(
                {
                    **chunk,
                    "score": rerank_score,
                    "original_score": original_score,
                    "colbert_score": colbert_score,
                    "rerank_score": rerank_score,
                }
            )
        reranked.sort(
            key=lambda chunk: (-float(chunk["score"]), str(chunk.get("chunk_id", "")))
        )
        return reranked if top_k is None else reranked[:top_k]

    def _maxsim_score(self, query: str, document: str) -> float:
        """Compatibility helper backed by the configured real ColBERT model."""
        return self._reranker.score_sync(query, [document])[0]

    def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []
        scores = self._reranker.score_sync(
            query,
            [str(chunk.get("content", "")) for chunk in chunks],
        )
        return self._merge_scores(chunks, scores, top_k)

    async def rerank_async(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []
        scores = await self._reranker.score(
            query,
            [str(chunk.get("content", "")) for chunk in chunks],
        )
        return self._merge_scores(chunks, scores, top_k)

    async def execute(
        self,
        *,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 5,
        **kwargs: Any,
    ) -> str:
        del kwargs
        reranked = await self.rerank_async(query, chunks, top_k)
        return "\n\n".join(str(chunk.get("content", "")) for chunk in reranked)


def _get_encoder() -> RAGatouilleColBERTModel | None:
    """Compatibility availability probe without substituting another scorer."""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_load_colbert_model).result()
    except RerankerLoadError:
        return None


_maxsim_with_embeddings = maxsim_score
