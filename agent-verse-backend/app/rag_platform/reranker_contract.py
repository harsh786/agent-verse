"""Backend-neutral contracts and lifecycle for blocking rerankers."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import Lock
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

_T = TypeVar("_T")


class RerankerLoadError(RuntimeError):
    """Raised when a configured reranking model cannot be loaded."""


class RerankerInferenceError(RuntimeError):
    """Raised when a configured reranking model cannot score candidates."""


RerankSkipReason = Literal["busy", "budget_exceeded", "warming_up"]


class RerankSkipped(Exception):  # noqa: N818 - a load-shedding outcome, not an error
    """The reranker was deliberately not run for this request (load shedding).

    Not a failure of the model: the caller keeps the retrieval order and flags
    the results (``rerank_skipped: <reason>``) instead of falling back to another
    ranker. ``reason`` is one of:

    * ``busy`` — the bounded inference queue is full;
    * ``budget_exceeded`` — the job would not finish (predicted) or did not
      finish (observed) inside the rerank time budget;
    * ``warming_up`` — the model is still loading.
    """

    def __init__(self, reason: RerankSkipReason, detail: str = "") -> None:
        message = f"rerank skipped: {reason}"
        super().__init__(f"{message} ({detail})" if detail else message)
        self.reason: RerankSkipReason = reason
        self.detail = detail


@runtime_checkable
class AsyncCloseableProtocol(Protocol):
    """Optional lifecycle capability for adapters and rerankers that own resources."""

    async def aclose(self) -> None: ...


@runtime_checkable
class RerankerProtocol(Protocol):
    """Query-document scorer used by concrete reranking strategies."""

    async def score(self, query: str, documents: list[str]) -> list[float]: ...


class BoundedAsyncExecutor:
    """Dedicated executor with async admission before worker submission."""

    def __init__(
        self,
        *,
        max_workers: int,
        max_queue_size: int,
        thread_name_prefix: str,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_queue_size < 0:
            raise ValueError("max_queue_size cannot be negative")
        self._admission = asyncio.Semaphore(max_workers + max_queue_size)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._state_lock = Lock()
        self._futures: set[Future[Any]] = set()
        self._is_closed = False

    async def run(
        self,
        function: Callable[..., _T],
        *args: object,
        **kwargs: object,
    ) -> _T:
        await self._admission.acquire()
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._is_closed:
                self._admission.release()
                raise RuntimeError("reranker executor is closed")
            try:
                future = self._executor.submit(partial(function, *args, **kwargs))
            except Exception:
                self._admission.release()
                raise
            self._futures.add(future)
        future.add_done_callback(lambda completed: self._schedule_completion(loop, completed))
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    def _schedule_completion(
        self,
        loop: asyncio.AbstractEventLoop,
        future: Future[Any],
    ) -> None:
        try:
            loop.call_soon_threadsafe(self._complete, future)
        except RuntimeError:
            with self._state_lock:
                self._futures.discard(future)

    def _complete(self, future: Future[Any]) -> None:
        with self._state_lock:
            self._futures.discard(future)
        self._admission.release()

    async def aclose(self) -> None:
        with self._state_lock:
            if self._is_closed:
                return
            self._is_closed = True
            futures = tuple(self._futures)
        self._executor.shutdown(wait=False, cancel_futures=True)
        if futures:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures),
                return_exceptions=True,
            )

    def close(self) -> None:
        """Synchronously close an executor that has no async work in flight."""
        with self._state_lock:
            if self._is_closed:
                return
            self._is_closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


class BoundedInferenceLane:
    """ONE bounded queue in front of a blocking model, shared by sync and async callers.

    Admission is thread-safe (a plain lock, no asyncio primitive), so callers on
    any thread or event loop — the async rerank stage, the synchronous
    ``RerankPolicy`` path, Celery tasks each running their own loop — share the
    same bound: at most ``max_workers`` jobs run and at most ``max_queue_depth``
    wait behind them. Nothing ever queues without limit:

    * a full lane refuses at once — :class:`RerankSkipped` ``busy``;
    * a budgeted caller whose job cannot finish inside its budget, predicted from
      the work already admitted and the observed seconds per unit of work (one
      unit = one query/passage pair), is refused at once — ``budget_exceeded``.
      An idle lane always admits, so a pessimistic estimate can never lock the
      model out (each admitted job refreshes the estimate);
    * a caller that does wait never waits past its budget — ``budget_exceeded``:
      its job is cancelled when still queued; a job already on the worker runs to
      completion (a model call cannot be interrupted) and its result is dropped.
    """

    _EWMA_ALPHA = 0.3

    def __init__(
        self,
        *,
        max_workers: int,
        max_queue_depth: int,
        thread_name_prefix: str,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_queue_depth < 0:
            raise ValueError("max_queue_depth cannot be negative")
        self._max_workers = max_workers
        self._capacity = max_workers + max_queue_depth
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = Lock()
        self._futures: set[Future[Any]] = set()
        self._pending_units = 0
        self._seconds_per_unit: float | None = None
        self._is_closed = False

    @property
    def capacity(self) -> int:
        """Jobs the lane holds at most (running + queued)."""
        return self._capacity

    @property
    def pending(self) -> int:
        """Jobs admitted and not finished (running + queued)."""
        with self._lock:
            return len(self._futures)

    @property
    def seconds_per_unit(self) -> float | None:
        """Smoothed observed inference seconds per unit of work (None before any)."""
        with self._lock:
            return self._seconds_per_unit

    def observe(self, units: int, seconds: float) -> None:
        """Record the duration of one finished model call (inference only)."""
        if units < 1 or seconds < 0:
            return
        sample = seconds / units
        with self._lock:
            previous = self._seconds_per_unit
            self._seconds_per_unit = (
                sample
                if previous is None
                else self._EWMA_ALPHA * sample + (1 - self._EWMA_ALPHA) * previous
            )

    def submit(
        self,
        function: Callable[..., _T],
        *args: object,
        units: int = 1,
        budget_seconds: float | None = None,
    ) -> Future[_T]:
        """Admit one job or refuse it at once (:class:`RerankSkipped`)."""
        units = max(1, units)
        with self._lock:
            if self._is_closed:
                raise RuntimeError("inference lane is closed")
            in_flight = len(self._futures)
            if in_flight >= self._capacity:
                raise RerankSkipped("busy", f"{in_flight} jobs in flight")
            if budget_seconds is not None:
                if budget_seconds <= 0:
                    raise RerankSkipped("budget_exceeded", "no time left in the budget")
                per_unit = self._seconds_per_unit
                if in_flight and per_unit is not None:
                    predicted = (self._pending_units / self._max_workers + units) * per_unit
                    if predicted > budget_seconds:
                        raise RerankSkipped(
                            "budget_exceeded",
                            f"predicted {predicted:.2f}s > budget {budget_seconds:.2f}s",
                        )
            future = self._executor.submit(partial(function, *args))
            self._futures.add(future)
            self._pending_units += units
        future.add_done_callback(lambda done: self._release(done, units))
        return future

    def _release(self, future: Future[Any], units: int) -> None:
        with self._lock:
            if future in self._futures:
                self._futures.discard(future)
                self._pending_units -= units

    def run_sync(
        self,
        function: Callable[..., _T],
        *args: object,
        units: int = 1,
        budget_seconds: float | None = None,
    ) -> _T:
        """Run on the lane from a synchronous caller, waiting at most the budget."""
        future = self.submit(function, *args, units=units, budget_seconds=budget_seconds)
        done, _ = concurrent.futures.wait([future], timeout=budget_seconds)
        if not done:
            future.cancel()
            raise RerankSkipped("budget_exceeded", f"no result within {budget_seconds:.2f}s")
        return future.result()

    async def run(
        self,
        function: Callable[..., _T],
        *args: object,
        units: int = 1,
        budget_seconds: float | None = None,
    ) -> _T:
        """Run on the lane from a coroutine; the event loop never blocks."""
        future = self.submit(function, *args, units=units, budget_seconds=budget_seconds)
        wrapped = asyncio.wrap_future(future)
        try:
            done, _ = await asyncio.wait({wrapped}, timeout=budget_seconds)
        except asyncio.CancelledError:
            future.cancel()
            wrapped.cancel()
            raise
        if not done:
            future.cancel()
            wrapped.cancel()
            raise RerankSkipped("budget_exceeded", f"no result within {budget_seconds:.2f}s")
        return wrapped.result()

    def close(self) -> None:
        """Refuse new work and cancel queued jobs (a running job finishes)."""
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def aclose(self) -> None:
        """Close, then wait for the job still on the worker without blocking the loop."""
        with self._lock:
            already_closed = self._is_closed
            self._is_closed = True
            futures = tuple(self._futures)
        if not already_closed:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if futures:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures),
                return_exceptions=True,
            )


__all__ = [
    "AsyncCloseableProtocol",
    "BoundedAsyncExecutor",
    "BoundedInferenceLane",
    "RerankSkipReason",
    "RerankSkipped",
    "RerankerInferenceError",
    "RerankerLoadError",
    "RerankerProtocol",
]
