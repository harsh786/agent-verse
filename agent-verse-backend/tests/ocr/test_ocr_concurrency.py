"""Process-wide OCR concurrency primitives (OCR-PAR-1).

Every OCR caller in a process (concurrent API requests, ZIP members, several
ingestion jobs in one worker) must share ONE bounded pool: a dedicated thread
pool for the CPU work and a FIFO page-slot limiter that works across event loops
(Celery runs every task in a fresh loop, so a loop-bound asyncio.Semaphore cannot
be process-wide).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.ocr import concurrency as oc


@pytest.fixture(autouse=True)
def _fresh_pool() -> Any:
    oc.reset_ocr_concurrency()
    yield
    oc.reset_ocr_concurrency()


# ── limits / config ──────────────────────────────────────────────────────────


def _limits(monkeypatch: pytest.MonkeyPatch, cpus: int, **env: str) -> oc.OcrLimits:
    from app.core.config import get_settings

    for key in ("OCR_MAX_CONCURRENCY", "OCR_PAGE_CONCURRENCY", "OCR_VISION_CONCURRENCY",
                "OCR_RENDER_DPI", oc.WORKER_PROCESSES_ENV):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(oc, "available_cpus", lambda: cpus)
    get_settings.cache_clear()
    try:
        return oc.ocr_limits()
    finally:
        get_settings.cache_clear()


def test_defaults_follow_the_cpus_and_leave_a_slot_for_another_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = _limits(monkeypatch, 8)
    assert limits.max_concurrency == 8
    # One document never takes every slot: a second document still progresses.
    assert limits.page_concurrency == 7
    assert limits.vision_concurrency == 4
    assert limits.render_dpi == 300


@pytest.mark.parametrize(("cpus", "pages"), [(1, 1), (2, 2), (3, 2), (4, 3)])
def test_small_hosts_keep_some_page_parallelism(
    monkeypatch: pytest.MonkeyPatch, cpus: int, pages: int
) -> None:
    limits = _limits(monkeypatch, cpus)
    assert limits.max_concurrency == cpus
    assert limits.page_concurrency == pages


def test_settings_override_and_page_concurrency_never_exceeds_the_global_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = _limits(monkeypatch, 16, OCR_MAX_CONCURRENCY="3", OCR_PAGE_CONCURRENCY="9",
                     OCR_VISION_CONCURRENCY="2", OCR_RENDER_DPI="200")
    assert (limits.max_concurrency, limits.page_concurrency) == (3, 3)
    assert (limits.vision_concurrency, limits.render_dpi) == (2, 200)


def test_prefork_children_split_the_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Celery prefork worker with --concurrency=2 runs two OCR pools (one per
    child): each child gets half the CPUs, not all of them."""
    limits = _limits(monkeypatch, 8, **{oc.WORKER_PROCESSES_ENV: "2"})
    assert limits.max_concurrency == 4
    assert limits.page_concurrency == 3


def test_note_worker_processes_is_inherited_by_forked_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(oc.WORKER_PROCESSES_ENV, raising=False)
    oc.note_worker_processes(3)
    assert os.environ[oc.WORKER_PROCESSES_ENV] == "3"
    oc.note_worker_processes(None)  # an unknown pool size changes nothing
    assert os.environ[oc.WORKER_PROCESSES_ENV] == "3"


def test_available_cpus_honours_a_cgroup_v2_quota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In a container os.cpu_count() is the NODE's CPU count; a 2-CPU pod on a
    64-core node must not start 64 Tesseracts."""
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("150000 100000\n")
    monkeypatch.setattr(oc, "_CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(oc, "_CGROUP_V1_QUOTA", tmp_path / "missing")
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert oc.available_cpus() == 2  # ceil(1.5)
    cpu_max.write_text("max 100000\n")
    assert oc.available_cpus() <= 64


def test_available_cpus_honours_a_cgroup_v1_quota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "cpu.cfs_quota_us").write_text("300000\n")
    (tmp_path / "cpu.cfs_period_us").write_text("100000\n")
    monkeypatch.setattr(oc, "_CGROUP_V2_CPU_MAX", tmp_path / "missing")
    monkeypatch.setattr(oc, "_CGROUP_V1_QUOTA", tmp_path / "cpu.cfs_quota_us")
    monkeypatch.setattr(oc, "_CGROUP_V1_PERIOD", tmp_path / "cpu.cfs_period_us")
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert oc.available_cpus() == 3


def test_tesseract_openmp_is_single_threaded_by_default() -> None:
    """Several tesseract processes in parallel, each spawning one OpenMP thread
    per core, oversubscribe the CPU; the OCR module pins it unless the operator
    set OMP_THREAD_LIMIT explicitly."""
    assert os.environ.get("OMP_THREAD_LIMIT") == "1"


# ── dedicated thread pool ────────────────────────────────────────────────────


async def test_ocr_work_runs_on_the_dedicated_bounded_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oc, "ocr_limits", lambda: oc.OcrLimits(2, 2, 1, 300))
    oc.reset_ocr_concurrency()
    lock = threading.Lock()
    running = peak = 0
    names: set[str] = set()

    def _work(i: int) -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
            names.add(threading.current_thread().name)
        time.sleep(0.05)
        with lock:
            running -= 1
        return i * 10

    results = await asyncio.gather(*(oc.run_ocr_work(_work, i) for i in range(6)))
    assert results == [0, 10, 20, 30, 40, 50]
    assert peak == 2  # never more threads than OCR_MAX_CONCURRENCY
    assert all(name.startswith("ocr") for name in names)  # never the default executor


async def test_ocr_work_does_not_block_the_event_loop() -> None:
    ticks = 0
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    beat = asyncio.create_task(_heartbeat())
    await oc.run_ocr_work(time.sleep, 0.3)
    stop.set()
    await beat
    assert ticks >= 10


# ── process-wide FIFO semaphore ──────────────────────────────────────────────


async def test_process_semaphore_bounds_and_is_fifo() -> None:
    sem = oc.ProcessSemaphore(1)
    order: list[int] = []

    async def _take(i: int) -> None:
        await sem.acquire()
        try:
            order.append(i)
            await asyncio.sleep(0.01)
        finally:
            sem.release()

    await sem.acquire()
    tasks = [asyncio.create_task(_take(i)) for i in range(5)]
    await asyncio.sleep(0.01)
    assert order == []
    sem.release()
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2, 3, 4]
    assert sem.in_use == 0


async def test_process_semaphore_cancelled_waiter_never_leaks_a_slot() -> None:
    sem = oc.ProcessSemaphore(1)
    await sem.acquire()
    waiter = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    sem.release()
    await asyncio.wait_for(sem.acquire(), 1)  # the slot is free again
    sem.release()
    assert sem.in_use == 0


async def test_process_semaphore_grant_racing_a_cancel_passes_the_slot_on() -> None:
    sem = oc.ProcessSemaphore(1)
    await sem.acquire()
    first = asyncio.create_task(sem.acquire())
    second = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0.01)
    sem.release()  # grant to `first` is scheduled ...
    first.cancel()  # ... and it is cancelled before it runs
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 1)  # the slot went on to the next waiter
    sem.release()
    assert sem.in_use == 0


def test_process_semaphore_is_shared_across_event_loops() -> None:
    """Two event loops in two threads (e.g. two Celery task loops, or the API
    loop and a worker loop) draw from the same slots."""
    sem = oc.ProcessSemaphore(1)
    lock = threading.Lock()
    running = peak = 0

    async def _use() -> None:
        nonlocal running, peak
        for _ in range(5):
            await sem.acquire()
            try:
                with lock:
                    running += 1
                    peak = max(peak, running)
                await asyncio.sleep(0.005)
                with lock:
                    running -= 1
            finally:
                sem.release()

    threads = [threading.Thread(target=asyncio.run, args=(_use(),)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert peak == 1
    assert sem.in_use == 0


async def test_page_slot_is_reentrant_within_one_page() -> None:
    """A page that holds a slot and calls the engine for that same page must not
    wait for a second slot (it would deadlock once every slot is held)."""
    oc.reset_ocr_concurrency(oc.OcrLimits(1, 1, 1, 300))
    async with oc.ocr_page_slot():
        async with oc.ocr_page_slot():  # nested: no second slot needed
            assert oc.page_slots_in_use() == 1
    assert oc.page_slots_in_use() == 0


# ── one document's pages ─────────────────────────────────────────────────────


async def test_map_bounded_keeps_order_and_caps_in_flight() -> None:
    running = peak = 0

    async def _page(n: int) -> str:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02 * (5 - n % 5))  # later pages finish first
        running -= 1
        return f"page {n}"

    out = await oc.map_bounded(list(range(1, 11)), _page, limit=3)
    assert out == [f"page {n}" for n in range(1, 11)]
    assert peak == 3


async def test_map_bounded_first_error_cancels_the_other_pages() -> None:
    started: list[int] = []
    cancelled: list[int] = []

    async def _page(n: int) -> int:
        started.append(n)
        if n == 2:
            await asyncio.sleep(0.01)
            raise RuntimeError("budget refused")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(n)
            raise
        return n

    with pytest.raises(RuntimeError, match="budget refused"):
        await asyncio.wait_for(oc.map_bounded([1, 2, 3, 4, 5], _page, limit=3), 2)
    assert 2 in started
    assert sorted(cancelled) == [1, 3]
    assert 4 not in started and 5 not in started  # never started once one failed


async def test_map_bounded_stream_pulls_only_when_a_slot_is_free() -> None:
    source = iter(range(1, 9))
    held = peak_held = 0

    async def _pull() -> int | None:
        nonlocal held, peak_held
        item = next(source, None)
        if item is not None:
            held += 1
            peak_held = max(peak_held, held)
        return item

    async def _work(n: int) -> int:
        nonlocal held
        await asyncio.sleep(0.01 * (9 - n))
        held -= 1
        return n * n

    out = await oc.map_bounded_stream(_pull, _work, limit=3)
    assert out == [n * n for n in range(1, 9)]
    assert peak_held == 3  # never more items pulled (held in memory) than slots


async def test_map_bounded_stream_stops_pulling_after_a_failure() -> None:
    pulled: list[int] = []
    source = iter(range(1, 30))

    async def _pull() -> int | None:
        item = next(source, None)
        if item is not None:
            pulled.append(item)
        return item

    async def _work(n: int) -> int:
        if n == 1:
            raise RuntimeError("refused")
        await asyncio.sleep(0.05)
        return n

    with pytest.raises(RuntimeError, match="refused"):
        await oc.map_bounded_stream(_pull, _work, limit=2)
    assert len(pulled) <= 3


# ── orderly shutdown (exit abort: libc++abi recursive_mutex, EXIT 134) ───────


def test_shutdown_is_a_no_op_when_no_pool_was_ever_created() -> None:
    oc.shutdown_ocr_concurrency()  # clears whatever earlier tests left
    assert oc._runtime is None
    assert len(oc._executors) == 0
    oc.shutdown_ocr_concurrency()
    oc.shutdown_ocr_concurrency(wait=False, cancel_futures=False)
    assert oc._runtime is None


async def test_shutdown_is_idempotent_and_a_later_call_builds_a_fresh_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oc, "ocr_limits", lambda: oc.OcrLimits(2, 2, 1, 300))
    assert await oc.run_ocr_work(lambda: 41 + 1) == 42
    first = oc._get_runtime().executor
    oc.shutdown_ocr_concurrency()
    oc.shutdown_ocr_concurrency()
    assert oc._runtime is None
    with pytest.raises(RuntimeError):
        first.submit(int)  # really shut down
    assert await oc.run_ocr_work(lambda: 7) == 7
    assert oc._get_runtime().executor is not first


def test_shutdown_waits_for_running_work_and_cancels_queued_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oc, "ocr_limits", lambda: oc.OcrLimits(1, 2, 1, 300))
    executor = oc._get_runtime().executor
    started = threading.Event()
    finished: list[str] = []

    def _running() -> str:
        started.set()
        time.sleep(0.2)
        finished.append("running")
        return "done"

    running = executor.submit(_running)
    queued = executor.submit(finished.append, "queued")
    assert started.wait(5)
    oc.shutdown_ocr_concurrency()  # wait=True, cancel_futures=True
    assert running.result(timeout=0) == "done"  # finished before shutdown returned
    assert queued.cancelled()
    assert finished == ["running"]


def test_shutdown_also_stops_pools_retired_by_reset() -> None:
    oc.reset_ocr_concurrency(oc.OcrLimits(1, 2, 1, 300))
    retired = oc._get_runtime().executor
    oc.reset_ocr_concurrency(oc.OcrLimits(1, 2, 1, 300))  # shut down with wait=False
    current = oc._get_runtime().executor
    assert retired in oc._executors and current in oc._executors
    oc.shutdown_ocr_concurrency()
    for executor in (retired, current):
        with pytest.raises(RuntimeError):
            executor.submit(int)
    assert len(oc._executors) == 0


def test_shutdown_runs_at_interpreter_exit() -> None:
    """Registered with atexit: run the exit handlers in a child interpreter."""
    import subprocess
    import sys

    code = (
        "import atexit\n"
        "from app.ocr import concurrency as oc\n"
        "ex = oc._get_runtime().executor\n"
        "assert ex.submit(lambda: 1).result() == 1\n"
        "atexit._run_exitfuncs()\n"
        "assert oc._runtime is None\n"
        "try:\n"
        "    ex.submit(int)\n"
        "except RuntimeError:\n"
        "    print('pool shut down at exit')\n"
    )
    backend = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pool shut down at exit" in proc.stdout


def test_session_teardown_shuts_the_pool_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    conftest = sys.modules["tests.conftest"]  # the loaded root conftest, not a re-import
    calls: list[dict[str, bool]] = []
    monkeypatch.setattr(oc, "shutdown_ocr_concurrency", lambda **kw: calls.append(kw))
    conftest._shutdown_ocr_pools()
    assert calls == [{"wait": True, "cancel_futures": True}]
    # Never imports the module itself when no test did.
    monkeypatch.delitem(sys.modules, "app.ocr.concurrency")
    conftest._shutdown_ocr_pools()
    assert "app.ocr.concurrency" not in sys.modules
    assert len(calls) == 1
