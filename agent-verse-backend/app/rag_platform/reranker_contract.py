"""Backend-neutral contracts and lifecycle for blocking rerankers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import Lock
from typing import Any, Protocol, TypeVar, runtime_checkable

_T = TypeVar("_T")


class RerankerLoadError(RuntimeError):
    """Raised when a configured reranking model cannot be loaded."""


class RerankerInferenceError(RuntimeError):
    """Raised when a configured reranking model cannot score candidates."""


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
        future.add_done_callback(
            lambda completed: self._schedule_completion(loop, completed)
        )
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


__all__ = [
    "AsyncCloseableProtocol",
    "BoundedAsyncExecutor",
    "RerankerInferenceError",
    "RerankerLoadError",
    "RerankerProtocol",
]
