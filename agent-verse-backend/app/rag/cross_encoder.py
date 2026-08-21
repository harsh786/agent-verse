"""Event-loop-safe cross-encoder reranking."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from numbers import Real
from threading import Lock
from typing import Any, Protocol, cast

from app.rag_platform.reranker_contract import (
    BoundedAsyncExecutor,
    RerankerInferenceError,
    RerankerLoadError,
)

_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderBackend(Protocol):
    """Blocking cross-encoder model used behind a worker-thread boundary."""

    def predict(
        self,
        pairs: list[tuple[str, str]],
        *,
        batch_size: int,
    ) -> Any: ...


CrossEncoderLoader = Callable[[], CrossEncoderBackend]


def _load_cross_encoder() -> CrossEncoderBackend:
    try:
        from sentence_transformers import CrossEncoder

        return cast(
            CrossEncoderBackend,
            CrossEncoder(_CROSS_ENCODER_MODEL, max_length=512),
        )
    except Exception as exc:
        raise RerankerLoadError(
            f"Cross-encoder model could not be loaded: {_CROSS_ENCODER_MODEL}"
        ) from exc


class CrossEncoderReranker:
    """Lazy cross-encoder whose model loading and inference stay off the event loop."""

    def __init__(
        self,
        *,
        model_loader: CrossEncoderLoader = _load_cross_encoder,
        batch_size: int = 32,
        max_workers: int = 1,
        max_queue_size: int = 0,
        backend_thread_safe: bool = False,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._model_loader = model_loader
        self._batch_size = batch_size
        self._backend_thread_safe = backend_thread_safe
        self._model: CrossEncoderBackend | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()
        worker_count = max_workers if backend_thread_safe else 1
        self._workers = BoundedAsyncExecutor(
            max_workers=worker_count,
            max_queue_size=max_queue_size,
            thread_name_prefix="cross-encoder-reranker",
        )

    def _get_model(self) -> CrossEncoderBackend:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                try:
                    self._model = self._model_loader()
                except RerankerLoadError:
                    raise
                except Exception as exc:
                    raise RerankerLoadError("Cross-encoder model could not be loaded") from exc
            return self._model

    def _score_blocking(self, query: str, documents: list[str]) -> list[float]:
        model = self._get_model()
        pairs = [(query, document) for document in documents]
        try:
            inference_guard = nullcontext() if self._backend_thread_safe else self._inference_lock
            with inference_guard:
                scores = model.predict(pairs, batch_size=self._batch_size)
            raw_scores = list(scores)
            if any(isinstance(score, bool) or not isinstance(score, Real) for score in raw_scores):
                raise RerankerInferenceError("Cross-encoder scores must be real numbers")
            values = [float(score) for score in raw_scores]
        except (RerankerLoadError, RerankerInferenceError):
            raise
        except Exception as exc:
            raise RerankerInferenceError("Cross-encoder inference failed") from exc
        if len(values) != len(documents):
            raise RerankerInferenceError(
                "Cross-encoder returned a score count that does not match the candidates"
            )
        if not all(math.isfinite(value) for value in values):
            raise RerankerInferenceError("Cross-encoder scores must be finite")
        return values

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Score candidates without blocking the event loop."""
        if not documents:
            return []
        return await self._workers.run(self._score_blocking, query, documents)

    async def aclose(self) -> None:
        """Cancel queued work and shut down the dedicated worker lifecycle."""
        await self._workers.aclose()

    def close_sync(self) -> None:
        """Release a legacy wrapper's unused dedicated async executor."""
        self._workers.close()

    def score_sync(self, query: str, documents: list[str]) -> list[float]:
        """Compatibility boundary that keeps model work in a worker thread."""
        if not documents:
            return []
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(self._score_blocking, query, documents).result()


_default_reranker: CrossEncoderReranker | None = CrossEncoderReranker()


def _get_default_reranker() -> CrossEncoderReranker:
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = CrossEncoderReranker()
    return _default_reranker


def cross_encode(
    query: str,
    documents: list[str],
    batch_size: int = 32,
) -> list[float]:
    """Legacy synchronous wrapper with the historical TF-IDF fallback."""
    if not documents:
        return []
    default_reranker = _get_default_reranker()
    reranker = default_reranker if batch_size == 32 else CrossEncoderReranker(batch_size=batch_size)
    try:
        return reranker.score_sync(query, documents)
    except (RerankerLoadError, RerankerInferenceError):
        return _tfidf_scores(query, documents)
    finally:
        if reranker is not default_reranker:
            reranker.close_sync()


async def close_default_cross_encoder() -> None:
    """Shut down the process-default cross-encoder executor during app teardown."""
    global _default_reranker
    reranker = _default_reranker
    _default_reranker = None
    if reranker is not None:
        await reranker.aclose()


def _tfidf_scores(query: str, documents: list[str]) -> list[float]:
    query_tokens = set(re.findall(r"\b\w+\b", query.lower()))
    all_document_tokens = [re.findall(r"\b\w+\b", document.lower()) for document in documents]
    document_count = len(documents)

    def inverse_document_frequency(token: str) -> float:
        frequency = sum(1 for tokens in all_document_tokens if token in tokens)
        if frequency == 0:
            return 1.0
        return math.log((document_count + 1) / (frequency + 1)) + 1

    values: list[float] = []
    for tokens in all_document_tokens:
        frequencies = Counter(tokens)
        total = len(tokens) or 1
        values.append(
            sum(
                frequencies.get(token, 0) / total * inverse_document_frequency(token)
                for token in query_tokens
            )
        )
    return values


def is_cross_encoder_available() -> bool:
    """Return whether the configured cross-encoder can be loaded."""
    try:
        _get_default_reranker().score_sync("availability", ["availability"])
    except (RerankerLoadError, RerankerInferenceError):
        return False
    return True
