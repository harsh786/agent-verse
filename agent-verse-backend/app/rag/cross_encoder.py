"""Event-loop-safe cross-encoder reranking."""

from __future__ import annotations

import asyncio
import importlib.util
import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from numbers import Real
from threading import Lock
from typing import Any, Literal, Protocol, cast

from app.observability.logging import get_logger
from app.rag_platform.reranker_contract import (
    BoundedAsyncExecutor,
    RerankerInferenceError,
    RerankerLoadError,
)

_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Rerank strategies that run the local cross-encoder (``llm`` is served by it).
CROSS_ENCODER_STRATEGIES = frozenset({"auto", "cross_encoder"})

WarmupStatus = Literal["ready", "warming_up", "unavailable"]

logger = get_logger(__name__)


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
        warmup_retry_seconds: float = 60.0,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._model_loader = model_loader
        self._warmup_retry_seconds = warmup_retry_seconds
        self._warmup_future: Future[None] | None = None
        self._warmup_failed_at: float | None = None
        self._warmup_lock = Lock()
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

    @property
    def is_ready(self) -> bool:
        """True once the model is loaded (a score call will not pay the load)."""
        return self._model is not None

    def start_warmup(self) -> Future[None]:
        """Load the model on a background daemon thread; never blocks the caller.

        Idempotent: while a load is in flight (or once it succeeded) the same
        future is returned. A failed load is retried only after
        ``warmup_retry_seconds`` so a broken model does not make every search
        start a fresh load.
        """
        with self._warmup_lock:
            current = self._warmup_future
            if current is not None:
                failed = current.done() and current.exception() is not None
                retry_due = (
                    self._warmup_failed_at is not None
                    and time.monotonic() - self._warmup_failed_at >= self._warmup_retry_seconds
                )
                if not failed or not retry_due:
                    return current
            future: Future[None] = Future()
            self._warmup_future = future
            self._warmup_failed_at = None
            if self._model is not None:
                future.set_result(None)
                return future

            def _load() -> None:
                try:
                    self._get_model()
                except BaseException as exc:
                    with self._warmup_lock:
                        self._warmup_failed_at = time.monotonic()
                    future.set_exception(exc)
                else:
                    future.set_result(None)

            threading.Thread(target=_load, name="cross-encoder-warmup", daemon=True).start()
            return future

    async def ensure_ready(self, timeout_seconds: float) -> WarmupStatus:
        """Wait (at most ``timeout_seconds``) for the model, starting its warm-up.

        ``ready`` — the model is loaded; ``warming_up`` — still loading after the
        budget (the load keeps going in the background); ``unavailable`` — the
        model could not be loaded.
        """
        if self.is_ready:
            return "ready"
        future = self.start_warmup()
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)), max(timeout_seconds, 0.0)
            )
        except TimeoutError:
            return "warming_up"
        except Exception:
            return "unavailable"
        return "ready"

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


def _sentence_transformers_installed() -> bool:
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def cross_encoder_in_use(settings: Any) -> bool:
    """True when the default rerank stage is configured to run the cross-encoder."""
    if not bool(getattr(settings, "rag_default_rerank_enabled", False)):
        return False
    strategy = str(getattr(settings, "rag_default_rerank_strategy", "auto")).lower()
    return strategy in CROSS_ENCODER_STRATEGIES


async def ensure_default_cross_encoder_ready(timeout_seconds: float) -> WarmupStatus:
    """``ensure_ready`` on the process-default cross-encoder."""
    return await _get_default_reranker().ensure_ready(timeout_seconds)


def preload_default_cross_encoder(settings: Any = None) -> Future[None] | None:
    """Start warming the process-default cross-encoder in the background.

    Called at API startup and in each Celery worker process so the first search
    after a restart does not pay the model load. Returns the warm-up future, or
    None when preloading is off, the cross-encoder is not used by the configured
    rerank strategy, or sentence-transformers is not installed.
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    if not bool(getattr(settings, "rag_rerank_preload", True)):
        return None
    if not cross_encoder_in_use(settings):
        return None
    if not _sentence_transformers_installed():
        logger.info("cross_encoder_preload_skipped", reason="sentence_transformers_missing")
        return None
    started = time.monotonic()
    future = _get_default_reranker().start_warmup()

    def _report(done: Future[None]) -> None:
        exc = done.exception()
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if exc is None:
            logger.info("cross_encoder_warm", model=_CROSS_ENCODER_MODEL, load_ms=elapsed_ms)
        else:
            logger.warning(
                "cross_encoder_warmup_failed",
                model=_CROSS_ENCODER_MODEL,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    future.add_done_callback(_report)
    logger.info("cross_encoder_warmup_started", model=_CROSS_ENCODER_MODEL)
    return future


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
