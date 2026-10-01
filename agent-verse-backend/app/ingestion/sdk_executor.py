"""Bounded worker pool for the blocking SDK / driver calls ingestion connectors make.

Most connector SDKs are synchronous (boto3, azure-storage-blob, clickhouse-connect,
snowflake, google-cloud-*, pymysql, neo4j, duckdb...). Called from a coroutine
they stall the whole event loop — every other sync, API request and heartbeat on
that worker — for as long as the network round-trip takes. Connectors run those
calls here instead.

The pool is dedicated and bounded, so a burst of slow syncs cannot exhaust the
loop's default executor (which also serves ``loop.getaddrinfo``) or grow without
limit. Context variables (logging / tracing context) are carried into the worker.

Timeouts stay with the SDK: a thread cannot be interrupted, so each call keeps its
own connect / read timeout. Cancelling the awaiting task stops waiting at once;
the in-flight call finishes (bounded by that timeout) and its result is dropped.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import itertools
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

__all__ = ["iterate_blocking", "run_blocking"]

# Upper bound on concurrent blocking SDK calls per worker process.
SDK_MAX_WORKERS = 16

_executor: ThreadPoolExecutor | None = None
_executor_pid: int | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """The process's SDK pool, created lazily (and again after a fork: a prefork
    Celery child must not inherit a pool whose threads do not exist in it)."""
    global _executor, _executor_pid
    with _executor_lock:
        if _executor is None or _executor_pid != os.getpid():
            _executor = ThreadPoolExecutor(
                max_workers=SDK_MAX_WORKERS, thread_name_prefix="ingest-sdk"
            )
            _executor_pid = os.getpid()
        return _executor


async def run_blocking[T](func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run the blocking ``func(*args, **kwargs)`` on the bounded SDK pool."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
    return await loop.run_in_executor(_get_executor(), call)


async def iterate_blocking[T](
    iterable: Iterable[T],
    *,
    chunk_size: int = 100,
    runner: Callable[..., Any] | None = None,
) -> AsyncIterator[T]:
    """Iterate a blocking iterable (an SDK paginator, a DB cursor) off the loop.

    Items are pulled ``chunk_size`` at a time on a worker thread, so a lazily
    paged result keeps streaming instead of being materialised whole. ``runner``
    replaces :func:`run_blocking` (e.g. an egress-checked driver runner).
    """
    run: Callable[..., Any] = runner or run_blocking
    iterator = await run(iter, iterable)

    def _next_chunk() -> list[T]:
        return list(itertools.islice(iterator, chunk_size))

    while True:
        chunk = await run(_next_chunk)
        if not chunk:
            return
        for item in chunk:
            yield item
