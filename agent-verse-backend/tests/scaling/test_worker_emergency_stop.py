"""CORE-13: the worker enforces an emergency stop at start and while a run is going.

* The graph's start-of-goal stop check reads Redis through ``_app_state._redis``,
  but the worker's app-state namespace had no ``_redis`` — the check was inert.
* ``_run_with_signals`` polled only the cancel flag, so a runner without a step
  gate (or one stuck in a long step) kept running through a stop.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.governance.emergency_stop import tenant_stop_key
from app.reliability.goal_lifecycle import GoalCancelledError
from app.tenancy.context import PlanTier, TenantContext
from tests.scaling.test_worker_runtime_profile import _run, worker  # noqa: F401

T = TenantContext(tenant_id="t-estop", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _SyncRedis:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, key: str) -> str | None:
        return self.values.get(key)


class _SlowRunner:
    """No step gate: only the signal poll can stop it."""

    def __init__(self) -> None:
        self.cancelled = False

    async def run(self, **kwargs: Any) -> Any:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_run_with_signals_stops_on_a_tenant_emergency_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    sync_r = _SyncRedis({})
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.01)
    runner = _SlowRunner()

    async def _activate() -> None:
        await asyncio.sleep(0.05)
        sync_r.values[tenant_stop_key(T.tenant_id)] = "1"

    activator = asyncio.create_task(_activate())
    with pytest.raises(GoalCancelledError, match="stopped"):
        await asyncio.wait_for(
            tasks._run_with_signals(runner, "g", T, None, "goal-estop"), timeout=5
        )
    await activator
    assert runner.cancelled is True


def test_worker_graph_app_state_carries_redis_for_the_start_check(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    sentinel = object()
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: sentinel)
    worker["with_context"]({})

    _run()

    graph = worker["graphs"][-1]
    assert getattr(graph._app_state, "_redis", None) is sentinel
