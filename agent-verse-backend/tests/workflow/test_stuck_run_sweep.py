"""WF-22: a run abandoned by a dead worker is re-dispatched within minutes,
not after the broker's 25-hour visibility timeout — and a run whose worker is
alive (live execution lease) is never executed twice.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from celery.exceptions import Retry  # type: ignore[import-untyped]

import app.workflow.celery_tasks as ct
from app.workflow.run_lease import KEY_PREFIX, RunLease, lease_alive


class _SyncRedis:
    """Enough of redis-py for the lease (SET NX EX, owner-checked EVAL, EXISTS)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key], self.ttl[key] = value, int(ex or 0)
        return True

    def exists(self, key: str) -> int:
        return int(key in self.store)

    def eval(self, script: str, numkeys: int, key: str, token: str, *args: Any) -> int:
        if self.store.get(key) != token:
            return 0
        if "expire" in script:
            self.ttl[key] = int(args[0])
            return 1
        del self.store[key]
        return 1


def test_lease_is_exclusive_renewed_and_owner_released() -> None:
    r = _SyncRedis()
    first = RunLease(r, "run-1", ttl=3)
    assert first.acquire()
    assert not RunLease(r, "run-1", ttl=3).acquire()  # a second executor backs off
    assert lease_alive(r, "run-1")
    r.ttl[f"{KEY_PREFIX}run-1"] = 0
    time.sleep(1.3)  # the renew thread runs every ttl/3 seconds
    assert r.ttl[f"{KEY_PREFIX}run-1"] == 3
    first.release()
    assert not lease_alive(r, "run-1")


class _StallStore:
    def __init__(self, stalled: list[dict[str, Any]]) -> None:
        self.stalled = stalled
        self.marked: list[str] = []

    async def list_stalled_runs(self, *, stall_seconds: float, limit: int = 100) -> Any:
        return self.stalled

    async def mark_stuck_redispatched(self, tenant_id: str, run_id: str) -> None:
        self.marked.append(run_id)


class _Runner:
    def __init__(self, store: _StallStore) -> None:
        self._run_store = store

    async def _get_plan_tier(self, tenant_id: str) -> str:
        return "starter"


@pytest.mark.asyncio
async def test_sweep_redispatches_only_runs_without_a_live_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _SyncRedis()
    assert RunLease(r, "busy", ttl=60).acquire()  # its worker is alive (long step)
    store = _StallStore(
        [
            {"run_id": "dead", "tenant_id": "t", "workflow_id": "wf"},
            {"run_id": "busy", "tenant_id": "t", "workflow_id": "wf"},
        ]
    )
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ct.execute_workflow_run, "apply_async", lambda **kw: sent.append(kw)
    )

    counts = await ct.redispatch_stuck_runs_async(_Runner(store), r, stall_seconds=600)

    assert counts == {"stalled": 2, "redispatched": 1, "alive": 1}
    assert [s["args"] for s in sent] == [["dead", "wf", "t"]]
    assert sent[0]["queue"] == "workflows.starter"
    assert store.marked == ["dead"]


def test_task_backs_off_while_another_worker_holds_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow.run_lease as run_lease

    r = _SyncRedis()
    assert RunLease(r, "run-9", ttl=60).acquire()
    monkeypatch.setattr(ct.celery_app.conf, "broker_url", "redis://fake:6379/0")
    monkeypatch.setattr(run_lease, "lease_client", lambda url: r)
    ran: list[str] = []

    class _R:
        async def execute_fresh(self, *a: Any, **k: Any) -> None:
            ran.append("x")

    monkeypatch.setattr(ct, "_get_runner", lambda: _R())
    with pytest.raises(Retry):
        ct.execute_workflow_run.run(run_id="run-9", workflow_id="wf", tenant_id="t")
    assert ran == []


def test_task_holds_and_releases_the_lease_around_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow.run_lease as run_lease

    r = _SyncRedis()
    monkeypatch.setattr(ct.celery_app.conf, "broker_url", "redis://fake:6379/0")
    monkeypatch.setattr(run_lease, "lease_client", lambda url: r)
    seen: list[bool] = []

    class _R:
        async def execute_fresh(self, *a: Any, **k: Any) -> None:
            seen.append(lease_alive(r, "run-7"))

    monkeypatch.setattr(ct, "_get_runner", lambda: _R())
    ct.execute_workflow_run.run(run_id="run-7", workflow_id="wf", tenant_id="t")
    assert seen == [True]
    assert not lease_alive(r, "run-7")
