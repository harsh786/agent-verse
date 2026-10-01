"""INC-04: an org emergency stop reaches goals running on a worker.

``run_goal`` never put the goal's org into the run's context, so the worker's
step gate and signal poll checked only the tenant stop: a stopped org's worker
goals ran on. Now ``run_goal`` resolves ``goals.execution_context.org_id`` once
(via the WF-17 org-stop index, no keyspace SCAN) and hands it to ``_run_with_signals``,
which also wakes immediately on the stop's pub/sub announcement instead of
waiting out its poll interval.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest

from app.governance.emergency_stop import (
    ORG_STOP_REASON,
    activate_org_stop,
    org_stop_key,
)
from app.reliability.goal_lifecycle import GoalCancelledError
from app.tenancy.context import PlanTier, TenantContext
from tests.scaling.test_worker_runtime_profile import _run, worker  # noqa: F401

T = TenantContext(tenant_id="t-org-estop", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _SlowRunner:
    def __init__(self) -> None:
        self.cancelled = False

    async def run(self, **kwargs: Any) -> Any:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_run_with_signals_stops_on_org_stop_given_explicit_org_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    sync_r = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.01)
    runner = _SlowRunner()

    async def _activate() -> None:
        await asyncio.sleep(0.05)
        sync_r.set(org_stop_key(T.tenant_id, "org-a"), "1")

    activator = asyncio.create_task(_activate())
    with pytest.raises(GoalCancelledError, match=ORG_STOP_REASON):
        await asyncio.wait_for(
            tasks._run_with_signals(runner, "g", T, None, "goal-org", org_id="org-a"),
            timeout=5,
        )
    await activator
    assert runner.cancelled is True


async def test_org_stop_announcement_interrupts_without_waiting_for_the_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll interval 60 s: only the pub/sub interrupt can stop the run in time."""
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    sync_r = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(
        tasks,
        "_worker_async_redis",
        lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 60.0)
    runner = _SlowRunner()
    api_redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    async def _activate() -> None:
        await asyncio.sleep(0.2)  # let the worker subscribe
        await activate_org_stop(api_redis, T.tenant_id, "org-a", activated_by="op")

    activator = asyncio.create_task(_activate())
    with pytest.raises(GoalCancelledError, match=ORG_STOP_REASON):
        await asyncio.wait_for(
            tasks._run_with_signals(runner, "g", T, None, "goal-org", org_id="org-a"),
            timeout=5,
        )
    await activator
    assert runner.cancelled is True


async def test_other_orgs_stop_does_not_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    sync_r = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.01)
    sync_r.set(org_stop_key(T.tenant_id, "org-b"), "1")

    class _Quick:
        async def run(self, **kwargs: Any) -> str:
            await asyncio.sleep(0.05)
            return "ok"

    assert await tasks._run_with_signals(_Quick(), "g", T, None, "goal-org", org_id="org-a") == "ok"


def test_run_goal_passes_the_goals_org_to_the_signal_runner(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.governance import emergency_stop
    from app.scaling import tasks

    sync_r = fakeredis.FakeRedis()

    def _no_scan(*a: Any, **k: Any) -> Any:
        raise AssertionError("keyspace SCAN on goal start")

    monkeypatch.setattr(sync_r, "scan_iter", _no_scan)
    sync_r.set(emergency_stop.ORG_STOP_INDEX_READY_KEY, "1")  # WF-17 index built
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)

    async def _org(db_factory: Any, tenant_id: str, goal_id: str) -> str:
        raise AssertionError("no org is stopped: the start check needs no DB lookup")

    monkeypatch.setattr(emergency_stop, "goal_org_id", _org)
    seen: dict[str, Any] = {}
    real = tasks._run_with_signals

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(tasks, "_run_with_signals", _spy)
    worker["with_context"]({"org_id": "org-a"})

    _run()

    assert seen.get("org_id") == "org-a"
    assert seen.get("org_unverified") is False


async def test_unreadable_org_is_stopped_while_any_org_of_the_tenant_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: a goal whose org could not be read halts on any org stop."""
    from app.governance.emergency_stop import ORG_UNVERIFIED_REASON
    from app.scaling import tasks

    server = fakeredis.FakeServer()
    sync_r = fakeredis.FakeRedis(server=server)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.01)
    api_redis = fakeredis.aioredis.FakeRedis(server=server)
    await activate_org_stop(api_redis, T.tenant_id, "org-z", activated_by="op")

    with pytest.raises(GoalCancelledError, match=ORG_UNVERIFIED_REASON):
        await asyncio.wait_for(
            tasks._run_with_signals(
                _SlowRunner(), "g", T, None, "goal-org", org_id=None, org_unverified=True
            ),
            timeout=5,
        )


def test_start_check_does_no_keyspace_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's stop check is direct GETs, never SCAN over the whole keyspace."""
    from app.governance.emergency_stop import enforce_emergency_stop_sync

    r = fakeredis.FakeRedis()
    r.set(org_stop_key("t1", "org-a"), "1")

    def _no_scan(*a: Any, **k: Any) -> Any:
        raise AssertionError("SCAN on the hot path")

    monkeypatch.setattr(r, "scan_iter", _no_scan)
    monkeypatch.setattr(r, "scan", _no_scan)
    assert enforce_emergency_stop_sync(r, "t1", "org-a") == ORG_STOP_REASON
    assert enforce_emergency_stop_sync(r, "t1", "org-b") is None
