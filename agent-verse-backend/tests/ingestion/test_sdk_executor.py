"""BLOCKING-SDK: the bounded pool connector SDK calls run on."""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time

import pytest

from app.ingestion import sdk_executor
from app.ingestion.sdk_executor import iterate_blocking, run_blocking

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


async def test_runs_off_the_loop_thread_and_carries_context() -> None:
    _request_id.set("req-42")

    def _where() -> tuple[str, str]:
        return threading.current_thread().name, _request_id.get()

    name, request_id = await run_blocking(_where)
    assert name.startswith("ingest-sdk")
    assert request_id == "req-42"


async def test_pool_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sdk_executor, "SDK_MAX_WORKERS", 2)
    monkeypatch.setattr(sdk_executor, "_executor", None)
    active = 0
    peak = 0
    lock = threading.Lock()

    def _work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    try:
        await asyncio.gather(*(run_blocking(_work) for _ in range(6)))
    finally:
        pool = sdk_executor._executor
        monkeypatch.setattr(sdk_executor, "_executor", None)
        if pool is not None:
            pool.shutdown(wait=True)
    assert peak == 2


async def test_pool_is_recreated_in_a_forked_child(monkeypatch: pytest.MonkeyPatch) -> None:
    first = sdk_executor._get_executor()
    monkeypatch.setattr(sdk_executor, "_executor_pid", -1)  # as if we were a fork
    assert sdk_executor._get_executor() is not first


async def test_iterate_blocking_streams_in_chunks() -> None:
    pulled: list[int] = []

    def _gen() -> object:
        for i in range(5):
            pulled.append(i)
            yield i

    seen = []
    async for item in iterate_blocking(_gen(), chunk_size=2):  # type: ignore[arg-type]
        seen.append(item)
        if item == 1:
            # Only the first chunk was pulled before the consumer saw item 1.
            assert pulled == [0, 1]
    assert seen == [0, 1, 2, 3, 4]
