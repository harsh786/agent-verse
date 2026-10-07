"""Event-loop-safe, bounded cross-encoder reranking.

RERANK-BOUNDED: the local cross-encoder is CPU-bound and its model is not
thread-safe, so inference is serialised. Live, a knowledge search pinned the API
at 100% CPU and every search hit the 30 s retrieval deadline: ``score_sync``
opened a fresh ``ThreadPoolExecutor`` per call (bypassing the bounded async
executor), so every concurrent search parked a thread behind the inference lock
and the queue grew without limit; torch ran 8 intra-op threads per call on a VM
whose CPUs it shares with Postgres/Redis/workers; and nothing bounded the time,
the number of candidates or the token length of a rerank.

Now:

* ONE :class:`~app.rag_platform.reranker_contract.BoundedInferenceLane` per
  reranker serves :meth:`CrossEncoderReranker.score` (async) and
  :meth:`CrossEncoderReranker.score_sync` alike: one worker thread, at most
  ``RAG_RERANK_MAX_QUEUE_DEPTH`` jobs waiting. Past that — or when a job cannot
  finish inside its time budget — the caller gets
  :class:`~app.rag_platform.reranker_contract.RerankSkipped` (``busy`` /
  ``budget_exceeded`` / ``warming_up``) at once and keeps its retrieval order.
* A budgeted call never waits past its budget (``RAG_RERANK_BUDGET_MS``, capped
  by the retrieval deadline — :mod:`app.rag.rerank_budget`).
* The model reads at most ``RAG_RERANK_MAX_LENGTH`` tokens per pair, and torch
  runs ``RAG_RERANK_TORCH_THREADS`` intra-op threads (auto: min(2, CPUs available
  to the process, cgroup quota included)), set once when the model loads.
* The model load runs on a background thread (:meth:`start_warmup`) and
  inference on the lane's worker: neither ever runs on an event loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import importlib.util
import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import nullcontext
from numbers import Real
from threading import Lock
from typing import Any, Literal, Protocol, cast

from app.observability.logging import get_logger
from app.rag.rerank_budget import rerank_budget_seconds, rerank_limits
from app.rag_platform.reranker_contract import (
    BoundedInferenceLane,
    RerankerInferenceError,
    RerankerLoadError,
    RerankSkipped,
)

# Default of ``RAG_CROSS_ENCODER_MODEL`` (the local cross-encoder tier). The model
# actually loaded is :func:`configured_cross_encoder_model`.
_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Rerank strategies that run the local cross-encoder (``llm`` is served by it).
CROSS_ENCODER_STRATEGIES = frozenset({"auto", "cross_encoder"})

# torch intra-op threads when ``RAG_RERANK_TORCH_THREADS`` is 0 (auto) — never
# more than the CPUs the process may use.
_AUTO_TORCH_THREADS_CAP = 2

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

_torch_threads_lock = Lock()
_torch_threads_configured: tuple[int, int | None] | None = None


def _auto_torch_threads() -> int:
    from app.core.cpu import available_cpus, cgroup_cpu_quota

    return max(1, min(_AUTO_TORCH_THREADS_CAP, available_cpus(cgroup_cpu_quota())))


def configure_torch_threads(
    threads: int = 0,
    interop_threads: int = 1,
) -> tuple[int, int | None] | None:
    """Pin torch's process-wide thread pools for CPU inference, once per process.

    ``threads`` — intra-op threads (0 = auto: min(2, available CPUs, honouring the
    cgroup CPU quota and affinity)); ``interop_threads`` — inter-op threads (0 =
    leave torch's default). torch accepts the inter-op setting only before its
    first parallel work; when it is too late that is logged and the intra-op
    setting still applies. Returns ``(intra, inter)`` as applied (``inter`` None
    when not applied), or None when torch is not installed.
    """
    global _torch_threads_configured
    with _torch_threads_lock:
        if _torch_threads_configured is not None:
            return _torch_threads_configured
        try:
            torch = importlib.import_module("torch")
        except ImportError:
            return None
        intra = threads if threads > 0 else _auto_torch_threads()
        torch.set_num_threads(intra)
        inter: int | None = None
        if interop_threads > 0:
            try:
                torch.set_num_interop_threads(interop_threads)
                inter = interop_threads
            except RuntimeError as exc:
                logger.info(
                    "cross_encoder_torch_interop_threads_unchanged",
                    requested=interop_threads,
                    reason=str(exc)[:200],
                )
        _torch_threads_configured = (intra, inter)
        logger.info("cross_encoder_torch_threads", intra_op=intra, inter_op=inter)
        return _torch_threads_configured


def configured_cross_encoder_model(settings: Any = None) -> str:
    """The local cross-encoder model: ``RAG_CROSS_ENCODER_MODEL`` (default ms-marco).

    Tolerates hand-built settings objects (a missing attribute takes the default).
    """
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:  # pragma: no cover - settings always load in the app
            return _CROSS_ENCODER_MODEL
    value = str(getattr(settings, "rag_cross_encoder_model", "") or "").strip()
    return value or _CROSS_ENCODER_MODEL


def _load_cross_encoder() -> CrossEncoderBackend:
    limits = rerank_limits()
    model_name = configured_cross_encoder_model()
    try:
        configure_torch_threads(limits.torch_threads, limits.torch_interop_threads)
        from sentence_transformers import CrossEncoder

        return cast(
            CrossEncoderBackend,
            CrossEncoder(model_name, max_length=limits.max_length),
        )
    except Exception as exc:
        raise RerankerLoadError(
            f"Cross-encoder model could not be loaded: {model_name}"
        ) from exc


def _on_event_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class CrossEncoderReranker:
    """Lazy cross-encoder whose model loading and inference stay off the event loop.

    All scoring — async :meth:`score` and sync :meth:`score_sync` — shares ONE
    bounded inference lane (see the module docstring).
    """

    def __init__(
        self,
        *,
        model_loader: CrossEncoderLoader = _load_cross_encoder,
        batch_size: int = 32,
        max_workers: int = 1,
        max_queue_size: int | None = None,
        backend_thread_safe: bool = False,
        warmup_retry_seconds: float = 60.0,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_queue_size is not None and max_queue_size < 0:
            raise ValueError("max_queue_size cannot be negative")
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
        self._worker_count = max_workers if backend_thread_safe else 1
        # None = RAG_RERANK_MAX_QUEUE_DEPTH, read when the lane is first used (the
        # process-default reranker is built at import, before settings matter).
        self._max_queue_size = max_queue_size
        self._lane: BoundedInferenceLane | None = None
        self._lane_lock = Lock()

    def _get_lane(self) -> BoundedInferenceLane:
        lane = self._lane
        if lane is not None:
            return lane
        with self._lane_lock:
            if self._lane is None:
                depth = (
                    self._max_queue_size
                    if self._max_queue_size is not None
                    else rerank_limits().max_queue_depth
                )
                self._lane = BoundedInferenceLane(
                    max_workers=self._worker_count,
                    max_queue_depth=depth,
                    thread_name_prefix="cross-encoder-reranker",
                )
            return self._lane

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
                started = time.monotonic()
                scores = model.predict(pairs, batch_size=self._batch_size)
                elapsed = time.monotonic() - started
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
        # Inference time only (never the model load): it drives the lane's
        # admission estimate.
        self._get_lane().observe(len(documents), elapsed)
        return values

    def _raise_if_load_failed(self, warmup: Future[None]) -> None:
        if warmup.done() and not warmup.cancelled():
            exc = warmup.exception()
            if exc is not None:
                if isinstance(exc, RerankerLoadError):
                    raise exc
                raise RerankerLoadError("Cross-encoder model could not be loaded") from exc

    async def score(
        self,
        query: str,
        documents: list[str],
        *,
        budget_seconds: float | None = None,
    ) -> list[float]:
        """Score candidates on the bounded lane without blocking the event loop.

        ``budget_seconds`` None: no time limit (the model loads inline on the
        worker if needed); the queue bound still applies. With a budget the model
        load is waited for within it, and the whole call never takes longer;
        otherwise :class:`RerankSkipped` is raised.
        """
        if not documents:
            return []
        lane = self._get_lane()
        if budget_seconds is None:
            return await lane.run(self._score_blocking, query, documents, units=len(documents))
        deadline = time.monotonic() + max(budget_seconds, 0.0)
        if not self.is_ready:
            warmup = self.start_warmup()
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(warmup)),
                    max(deadline - time.monotonic(), 0.0),
                )
            except TimeoutError:
                raise RerankSkipped("warming_up", "the model is still loading") from None
            except Exception:
                self._raise_if_load_failed(warmup)
                raise
        return await lane.run(
            self._score_blocking,
            query,
            documents,
            units=len(documents),
            budget_seconds=deadline - time.monotonic(),
        )

    def score_sync(
        self,
        query: str,
        documents: list[str],
        *,
        budget_seconds: float | None = None,
    ) -> list[float]:
        """Synchronous scoring on the SAME bounded lane as :meth:`score`.

        Model work stays on the lane's worker thread. With a budget the caller
        waits at most that long; called on an event-loop thread it never waits
        for a model load (it starts the warm-up and skips).
        """
        if not documents:
            return []
        lane = self._get_lane()
        if budget_seconds is None:
            return lane.run_sync(self._score_blocking, query, documents, units=len(documents))
        deadline = time.monotonic() + max(budget_seconds, 0.0)
        if not self.is_ready:
            warmup = self.start_warmup()
            wait = 0.0 if _on_event_loop_thread() else max(deadline - time.monotonic(), 0.0)
            concurrent.futures.wait([warmup], timeout=wait)
            self._raise_if_load_failed(warmup)
            if not self.is_ready:
                raise RerankSkipped("warming_up", "the model is still loading")
        return lane.run_sync(
            self._score_blocking,
            query,
            documents,
            units=len(documents),
            budget_seconds=deadline - time.monotonic(),
        )

    async def aclose(self) -> None:
        """Cancel queued work and shut down the dedicated worker lifecycle."""
        with self._lane_lock:
            lane = self._lane
        if lane is not None:
            await lane.aclose()

    def close_sync(self) -> None:
        """Refuse new work and cancel queued jobs (a running job finishes)."""
        with self._lane_lock:
            lane = self._lane
        if lane is not None:
            lane.close()


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
    """Synchronous scoring on the bounded lane, with the historical TF-IDF fallback.

    Budgeted (:func:`app.rag.rerank_budget.rerank_budget_seconds`): raises
    :class:`RerankSkipped` when the lane is busy, the budget runs out or the
    model is still loading — a skip is not an error, so it is never replaced by
    TF-IDF scores. A model that cannot load or score falls back to TF-IDF.
    """
    if not documents:
        return []
    default_reranker = _get_default_reranker()
    reranker = default_reranker if batch_size == 32 else CrossEncoderReranker(batch_size=batch_size)
    try:
        return reranker.score_sync(query, documents, budget_seconds=rerank_budget_seconds())
    except (RerankerLoadError, RerankerInferenceError):
        return _tfidf_scores(query, documents)
    finally:
        if reranker is not default_reranker:
            reranker.close_sync()


async def cross_encode_async(
    query: str,
    documents: list[str],
    *,
    budget_seconds: float | None = None,
) -> list[float]:
    """Score on the process-default reranker's bounded lane from a coroutine.

    ``budget_seconds`` None = the configured budget (capped by the retrieval
    deadline). Raises :class:`RerankSkipped` on a skip and the reranker errors
    as they are — the caller decides the fallback (and labels it).
    """
    if not documents:
        return []
    budget = rerank_budget_seconds() if budget_seconds is None else budget_seconds
    return await _get_default_reranker().score(query, documents, budget_seconds=budget)


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
    model_name = configured_cross_encoder_model(settings)
    future = _get_default_reranker().start_warmup()

    def _report(done: Future[None]) -> None:
        exc = done.exception()
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if exc is None:
            logger.info("cross_encoder_warm", model=model_name, load_ms=elapsed_ms)
        else:
            logger.warning(
                "cross_encoder_warmup_failed",
                model=model_name,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    future.add_done_callback(_report)
    logger.info("cross_encoder_warmup_started", model=model_name)
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


def local_cross_encoder_configured(settings: Any = None) -> bool:
    """The local cross-encoder TIER exists: a model is configured and
    sentence-transformers is installed (whether it is warm is a separate,
    per-search question — :func:`ensure_default_cross_encoder_ready`)."""
    return bool(configured_cross_encoder_model(settings)) and _sentence_transformers_installed()


def is_cross_encoder_available(wait_seconds: float = 0.0) -> bool:
    """Whether the process-default cross-encoder is loaded and usable now.

    Never loads the model on the caller's thread and never runs an inference
    probe (it used to do both on every ``auto`` rerank, through the serialised
    model): a cold model starts warming in the background and this answers False
    unless the load finishes within ``wait_seconds``.
    """
    reranker = _get_default_reranker()
    if reranker.is_ready:
        return True
    warmup = reranker.start_warmup()
    if wait_seconds > 0 and not _on_event_loop_thread():
        concurrent.futures.wait([warmup], timeout=wait_seconds)
    return reranker.is_ready
