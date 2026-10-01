"""Workflow Celery tasks run on fresh, fully torn-down event loops.

``app.workflow.celery_tasks._run_async`` drove every task on the persistent
``get_event_loop()``. In a worker process that also runs the scaling tasks
(fresh loops + engine disposal) that mixed loops on the shared DB engine — the
"idle in transaction" leak (TX-LEAK). It now uses ``run_in_fresh_loop`` and the
cached worker runner's loop-bound Redis clients are closed with the loop, so a
runner never outlives the loop it was used on.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.workflow import celery_tasks as ct


async def _loop_of() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def test_each_task_gets_its_own_loop_and_it_is_closed() -> None:
    first = ct._run_async(_loop_of())
    second = ct._run_async(_loop_of())
    assert first is not second
    assert first.is_closed() and second.is_closed()


def test_leftover_tasks_are_cancelled_before_the_loop_closes() -> None:
    spawned: list[asyncio.Task[None]] = []

    async def body() -> None:
        spawned.append(asyncio.get_running_loop().create_task(asyncio.sleep(30)))

    ct._run_async(body())
    assert spawned[0].cancelled()


def test_worker_runner_clients_are_closed_with_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    closed_on: list[asyncio.AbstractEventLoop] = []

    class _Client:
        async def aclose(self) -> None:
            closed_on.append(asyncio.get_running_loop())

    monkeypatch.setattr(ct, "_WORKER_RUNNER", object())
    monkeypatch.setattr(ct, "_WORKER_RUNNER_CLIENTS", [_Client(), _Client()])
    used = ct._run_async(_loop_of())
    assert ct._WORKER_RUNNER is None  # rebuilt for the next task's loop
    assert ct._WORKER_RUNNER_CLIENTS == []
    assert closed_on == [used, used]


async def test_still_works_when_called_from_a_running_loop() -> None:
    outer = asyncio.get_running_loop()
    inner: Any = ct._run_async(_loop_of())
    assert inner is not outer
    assert inner.is_closed()
