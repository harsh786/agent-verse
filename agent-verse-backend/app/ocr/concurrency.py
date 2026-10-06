"""Process-wide OCR concurrency: one bounded pool for every document (OCR-PAR).

Every OCR caller in a process — concurrent API requests, the members of a ZIP
upload, several ingestion jobs in one worker — shares the primitives here:

* :func:`run_ocr_work` runs one CPU-bound OCR step (PDF rasterisation, image
  decode / preprocessing, a Tesseract pass, PNG encoding, an office conversion)
  on ONE dedicated thread pool of ``OCR_MAX_CONCURRENCY`` threads. Never on the
  event loop, and never on the default executor (which every ``to_thread`` user
  shares: N documents x M pages used to queue there, unbounded by OCR).
* :func:`ocr_page_slot` — a page holds one of ``OCR_MAX_CONCURRENCY``
  process-wide slots from rasterisation until its text is read, which bounds the
  page bitmaps in memory. The slots are FIFO across documents and work across
  event loops: Celery runs every task in a fresh loop, so a loop-bound
  ``asyncio.Semaphore`` could never be process-wide.
* :func:`map_bounded` runs ONE document's pages, at most
  ``OCR_PAGE_CONCURRENCY`` at once (below the global cap by default, so a second
  document always gets a slot while a 200-page scan is running), returns results
  in page order and, on the first error, cancels the pages still running.
* :func:`ocr_vision_slot` bounds the LLM-vision fallback calls in flight
  (``OCR_VISION_CONCURRENCY``); the provider's rate limits / cost guard still
  apply to each call.

:func:`shutdown_ocr_concurrency` stops the pool in order (pending work cancelled,
running work awaited). It runs at interpreter exit (``atexit``) and from the test
session's teardown, so no OCR thread is mid-call in tesseract / poppler while the
interpreter tears down (a ``libc++abi ... recursive_mutex lock failed`` abort,
EXIT 134, was seen once at test-suite exit).

Tesseract's own OpenMP threading is pinned to one thread (``OMP_THREAD_LIMIT=1``,
unless the operator set it): parallelism comes from running pages side by side,
and N Tesseracts each spawning one thread per core oversubscribe the CPU.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import contextvars
import functools
import math
import os
import threading
import weakref
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Before any tesseract subprocess is spawned (they inherit this environment).
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Prefork Celery workers: the parent records its pool size here before forking,
# so each child sizes its own OCR pool to its share of the CPUs.
WORKER_PROCESSES_ENV = "AGENTVERSE_OCR_WORKER_PROCESSES"

_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


@dataclass(frozen=True)
class OcrLimits:
    max_concurrency: int  # OCR threads and page slots in this process
    page_concurrency: int  # pages of ONE document in flight
    vision_concurrency: int  # LLM-vision fallback calls in flight in this process
    render_dpi: int  # PDF rasterisation resolution


def _cgroup_cpu_quota() -> float | None:
    """The container's CPU limit (cgroup v2, else v1), or None when unlimited."""
    try:
        quota, period = _CGROUP_V2_CPU_MAX.read_text().split()[:2]
        if quota != "max" and int(period) > 0:
            return int(quota) / int(period)
        return None
    except (OSError, ValueError):
        pass
    try:
        quota_us = int(_CGROUP_V1_QUOTA.read_text().strip())
        period_us = int(_CGROUP_V1_PERIOD.read_text().strip())
        if quota_us > 0 and period_us > 0:
            return quota_us / period_us
    except (OSError, ValueError):
        pass
    return None


def available_cpus() -> int:
    """CPUs this process may really use: affinity and the container's CPU quota.

    ``os.cpu_count()`` is the NODE's count inside a container: a 2-CPU pod on a
    64-core node must not start 64 Tesseracts.
    """
    count = os.cpu_count() or 1
    with contextlib.suppress(AttributeError, OSError):  # macOS: no sched_getaffinity
        count = min(count, len(os.sched_getaffinity(0)))
    quota = _cgroup_cpu_quota()
    if quota is not None:
        count = min(count, max(1, math.ceil(quota)))
    return max(1, count)


def _worker_processes() -> int:
    try:
        return max(1, int(os.environ.get(WORKER_PROCESSES_ENV, "1")))
    except ValueError:
        return 1


def note_worker_processes(count: int | None) -> None:
    """Record how many OCR-running processes share this host's CPUs (Celery
    prefork ``--concurrency``); forked children inherit it via the environment."""
    if count and count > 0:
        os.environ[WORKER_PROCESSES_ENV] = str(int(count))


def default_page_concurrency(max_concurrency: int) -> int:
    """One slot short of the global cap (at least 2 when there are 2 slots), so a
    second document always progresses beside a huge one."""
    if max_concurrency <= 2:
        return max_concurrency
    return max_concurrency - 1


def ocr_limits() -> OcrLimits:
    """The OCR limits from settings (0 = derive from the CPUs)."""
    from app.core.config import get_settings

    settings = get_settings()
    configured = int(getattr(settings, "ocr_max_concurrency", 0) or 0)
    max_conc = configured or max(1, available_cpus() // _worker_processes())
    pages = int(getattr(settings, "ocr_page_concurrency", 0) or 0)
    pages = min(pages, max_conc) if pages else default_page_concurrency(max_conc)
    return OcrLimits(
        max_concurrency=max_conc,
        page_concurrency=max(1, pages),
        vision_concurrency=max(1, int(getattr(settings, "ocr_vision_concurrency", 4) or 4)),
        render_dpi=int(getattr(settings, "ocr_render_dpi", 300) or 300),
    )


class ProcessSemaphore:
    """A FIFO semaphore shared by every event loop (and thread) of the process.

    ``asyncio.Semaphore`` binds to one loop. The API serves from one loop, but a
    Celery task runs in a fresh loop each time and tests run many: the OCR
    limits must hold for all of them. Waiters are woken in arrival order through
    their own loop (``call_soon_threadsafe``); a waiter cancelled while (or just
    as) it is granted passes the slot on, so a slot is never leaked.
    """

    def __init__(self, value: int) -> None:
        self._lock = threading.Lock()
        self._limit = max(1, value)
        self._free = self._limit
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._limit - self._free

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._free > 0 and not self._waiters:
                self._free -= 1
                return
            fut: asyncio.Future[None] = loop.create_future()
            self._waiters.append((loop, fut))
        try:
            await fut
        except BaseException:
            with self._lock:
                try:
                    self._waiters.remove((loop, fut))
                    queued = True
                except ValueError:
                    queued = False
            if not queued and fut.done() and not fut.cancelled():
                self.release()  # granted just as we were cancelled: pass it on
            raise

    def release(self) -> None:
        with self._lock:
            while self._waiters:
                loop, fut = self._waiters.popleft()
                try:
                    loop.call_soon_threadsafe(self._grant, fut)
                except RuntimeError:  # that loop is closed: its waiter is gone
                    continue
                return  # the slot moves straight to the next waiter
            self._free = min(self._limit, self._free + 1)

    def _grant(self, fut: asyncio.Future[None]) -> None:
        if fut.done():  # cancelled while the grant was in flight
            self.release()
        else:
            fut.set_result(None)


class _OcrRuntime:
    def __init__(self, limits: OcrLimits) -> None:
        self.limits = limits
        self.executor = ThreadPoolExecutor(
            max_workers=limits.max_concurrency, thread_name_prefix="ocr"
        )
        _executors.add(self.executor)
        self.page_slots = ProcessSemaphore(limits.max_concurrency)
        self.vision_slots = ProcessSemaphore(limits.vision_concurrency)


_runtime: _OcrRuntime | None = None
_runtime_lock = threading.Lock()
# Every pool this process built, including ones reset_ocr_concurrency retired
# without waiting: the shutdown stops them all. Weak, so a finished pool is freed.
_executors: weakref.WeakSet[ThreadPoolExecutor] = weakref.WeakSet()
_holds_page_slot: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ocr_holds_page_slot", default=False
)


def _get_runtime() -> _OcrRuntime:
    global _runtime
    runtime = _runtime
    if runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = _OcrRuntime(ocr_limits())
            runtime = _runtime
    return runtime


def current_limits() -> OcrLimits:
    """The limits the process-wide OCR pool was built with."""
    return _get_runtime().limits


def reset_ocr_concurrency(limits: OcrLimits | None = None) -> None:
    """Drop the pool (tests; settings changes need a restart otherwise). With
    ``limits`` the next pool is built with them instead of the settings."""
    global _runtime
    with _runtime_lock:
        old, _runtime = _runtime, (_OcrRuntime(limits) if limits is not None else None)
    if old is not None:
        old.executor.shutdown(wait=False)


def shutdown_ocr_concurrency(*, wait: bool = True, cancel_futures: bool = True) -> None:
    """Stop every OCR pool of this process in order: queued work is cancelled
    (``cancel_futures``) and running work finishes (``wait``).

    Idempotent, and a no-op when no pool was ever created. Registered with
    ``atexit`` and called from the test session's teardown. A later OCR call
    builds a fresh pool, as after :func:`reset_ocr_concurrency`.
    """
    global _runtime
    with _runtime_lock:
        _runtime = None
        executors = list(_executors)
        _executors.clear()
    for executor in executors:
        executor.shutdown(wait=wait, cancel_futures=cancel_futures)


atexit.register(shutdown_ocr_concurrency)


def _after_fork_in_child() -> None:
    # A forked child (Celery prefork) inherits the parent's pool object but not
    # its threads: build a fresh pool on first use instead of hanging on it. The
    # parent's pools are not this child's to shut down (their locks may have been
    # held at fork time).
    global _runtime, _runtime_lock, _executors
    _runtime = None
    _runtime_lock = threading.Lock()
    _executors = weakref.WeakSet()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


async def run_ocr_work[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run blocking OCR work on the process-wide OCR thread pool."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, fn, *args, **kwargs)
    return await loop.run_in_executor(_get_runtime().executor, call)


@asynccontextmanager
async def ocr_page_slot() -> AsyncIterator[None]:
    """Hold one process-wide page slot. Re-entrant: a page that already holds a
    slot (and calls the engine for that same page) does not take a second one —
    it would deadlock once every slot is held. Do not OCR a multi-page document
    from inside a slot: its pages would then run without the global bound."""
    if _holds_page_slot.get():
        yield
        return
    slots = _get_runtime().page_slots
    await slots.acquire()
    token = _holds_page_slot.set(True)
    try:
        yield
    finally:
        _holds_page_slot.reset(token)
        slots.release()


def page_slots_in_use() -> int:
    return _get_runtime().page_slots.in_use


@asynccontextmanager
async def ocr_vision_slot() -> AsyncIterator[None]:
    """Hold one of the process-wide LLM-vision fallback slots."""
    slots = _get_runtime().vision_slots
    await slots.acquire()
    try:
        yield
    finally:
        slots.release()


async def map_bounded[T, R](
    items: Sequence[T], fn: Callable[[T], Awaitable[R]], *, limit: int
) -> list[R]:
    """``[await fn(i) for i in items]`` with at most ``limit`` running at once.

    Results come back in input order (a page's text stays on its page). The
    first error is raised after every other item is cancelled; items that had
    not started yet never start (no further OCR / vision spend after a refusal).
    """
    if not items:
        return []
    gate = asyncio.Semaphore(max(1, limit))
    failed = False

    async def _one(item: T) -> R:
        nonlocal failed
        async with gate:
            if failed:
                raise asyncio.CancelledError
            try:
                return await fn(item)
            except BaseException:
                failed = True
                raise

    tasks = [asyncio.ensure_future(_one(item)) for item in items]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        failed = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def map_bounded_stream[T, R](
    pull: Callable[[], Awaitable[T | None]],
    fn: Callable[[T], Awaitable[R]],
    *,
    limit: int,
) -> list[R]:
    """:func:`map_bounded` over items pulled one at a time (``None`` ends).

    The next item is pulled only when one of the ``limit`` slots is free, so at
    most ``limit`` items (e.g. inflated archive members) are held at once.
    Results come back in pull order; the first error (from ``pull`` or ``fn``)
    stops pulling and cancels the items still running.
    """
    gate = asyncio.Semaphore(max(1, limit))
    failed = False
    tasks: list[asyncio.Future[R]] = []

    async def _run(item: T) -> R:
        nonlocal failed
        try:
            return await fn(item)
        except BaseException:
            failed = True
            raise
        finally:
            gate.release()

    try:
        while True:
            await gate.acquire()
            if failed:
                gate.release()
                break
            try:
                item = await pull()
            except BaseException:
                gate.release()
                raise
            if item is None:
                gate.release()
                break
            tasks.append(asyncio.ensure_future(_run(item)))
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
