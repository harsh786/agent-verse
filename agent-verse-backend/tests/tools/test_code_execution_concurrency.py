"""CODE-03: per-tenant (global) and per-host caps on concurrent sandbox executions.

Each execution held a default-executor thread for up to 60 s with no bound, so
one tenant could starve the replica's thread pool (shared with to_thread SSRF
DNS checks) and launch unbounded containers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools import code_execution as ce
from app.tools.code_interpreter import CodeResult


def _ctx(tenant: str) -> ce.CodeExecutionContext:
    return ce.CodeExecutionContext(
        tenant_ctx=TenantContext(tenant_id=tenant, plan=PlanTier.FREE, api_key_id="k"),
        source="tools.execute_code",
    )


class _SlowInterp:
    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.running = 0
        self.peak = 0

    async def execute(self, **_kw: Any) -> CodeResult:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gate.wait()
        finally:
            self.running -= 1
        return CodeResult(stdout="", stderr="", exit_code=0)


class _FakeLeases:
    """In-process stand-in for the Redis lease set (shared by 'replicas')."""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}

    # Redis EVAL (server-side Lua), not Python eval.
    async def eval(self, script: str, _n: int, key: str, *args: str) -> Any:
        members = self.sets.setdefault(key, set())
        if "ZCARD" in script:  # acquire
            limit, _lease, member = int(args[0]), args[1], args[2]
            if member in members:
                return 1
            if len(members) >= limit:
                return 0
            members.add(member)
            return 1
        raise AssertionError("unexpected script")

    async def zrem(self, key: str, member: str) -> None:
        self.sets.get(key, set()).discard(member)


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce, "_local_slots", ce.LocalSlotCounter())


@pytest.fixture
def caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_EXEC_MAX_CONCURRENT_PER_TENANT", "2")
    monkeypatch.setenv("CODE_EXEC_MAX_CONCURRENT_PER_HOST", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_tenant_cap_is_enforced_without_blocking(caps: None) -> None:
    gate = asyncio.Event()
    interp = _SlowInterp(gate)
    redis = _FakeLeases()
    audit = AuditLog()
    running = [
        asyncio.create_task(
            ce.execute_governed(
                "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
            )
        )
        for _ in range(2)
    ]
    await asyncio.sleep(0.05)
    with pytest.raises(ce.CodeExecutionBusyError) as exc:
        await ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
        )
    assert exc.value.scope == "tenant"
    # Another tenant still gets the host's remaining slot.
    other = asyncio.create_task(
        ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t2"), audit_log=audit, interpreter=interp, redis=redis
        )
    )
    await asyncio.sleep(0.05)
    # The host cap (3) is now full for everyone.
    with pytest.raises(ce.CodeExecutionBusyError) as host_exc:
        await ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t3"), audit_log=audit, interpreter=interp, redis=redis
        )
    assert host_exc.value.scope == "host"
    # A to_thread DNS-style check still completes while executions are running.
    assert await asyncio.wait_for(asyncio.to_thread(lambda: "resolved"), 2) == "resolved"
    gate.set()
    await asyncio.gather(*running, other)
    assert interp.peak == 3
    # Slots are released: the tenant can run again.
    assert redis.sets["code_exec:leases:t1"] == set()
    await ce.execute_governed(
        "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
    )


async def test_cap_is_shared_across_replicas_through_redis(
    caps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two 'replicas' (separate local counters) share one Redis: still 2 for t1."""
    gate = asyncio.Event()
    interp = _SlowInterp(gate)
    redis = _FakeLeases()
    audit = AuditLog()
    first = asyncio.create_task(
        ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
        )
    )
    await asyncio.sleep(0.05)
    monkeypatch.setattr(ce, "_local_slots", ce.LocalSlotCounter())  # replica B
    second = asyncio.create_task(
        ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
        )
    )
    await asyncio.sleep(0.05)
    monkeypatch.setattr(ce, "_local_slots", ce.LocalSlotCounter())  # replica C
    with pytest.raises(ce.CodeExecutionBusyError):
        await ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t1"), audit_log=audit, interpreter=interp, redis=redis
        )
    gate.set()
    await asyncio.gather(first, second)


async def test_redis_outage_degrades_to_a_bounded_local_cap(caps: None) -> None:
    class _Down:
        async def eval(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    gate = asyncio.Event()
    interp = _SlowInterp(gate)
    audit = AuditLog()
    tasks = [
        asyncio.create_task(
            ce.execute_governed(
                "x", "python", 5, ctx=_ctx("t9"), audit_log=audit, interpreter=interp, redis=_Down()
            )
        )
        for _ in range(2)
    ]
    await asyncio.sleep(0.05)
    with pytest.raises(ce.CodeExecutionBusyError):
        await ce.execute_governed(
            "x", "python", 5, ctx=_ctx("t9"), audit_log=audit, interpreter=interp, redis=_Down()
        )
    gate.set()
    await asyncio.gather(*tasks)


def test_api_answers_429_when_the_cap_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.tools import router

    async def _busy(*_a: Any, **_k: Any) -> Any:
        raise ce.CodeExecutionBusyError("tenant", 4)

    monkeypatch.setattr(ce, "execute_governed", _busy)
    ctx = TenantContext(tenant_id="t", plan=PlanTier.FREE, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    resp = TestClient(app).post("/tools/execute-code", json={"code": "print(1)"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"]
    assert "concurrent" in resp.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"code": "x" * 50_001},  # CODE-05: same 50k cap as chat execute
        {"code": ""},
        {"code": "print(1)", "language": "ruby"},
    ],
)
def test_execute_code_request_is_bounded(body: dict[str, Any]) -> None:
    from app.api.tools import router

    ctx = TenantContext(tenant_id="t", plan=PlanTier.FREE, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    assert TestClient(app).post("/tools/execute-code", json=body).status_code == 422


def test_sandbox_runs_on_a_dedicated_bounded_pool(caps: None) -> None:
    import app.tools.code_interpreter as ci

    ci._SANDBOX_POOL = None
    pool = ci._sandbox_pool()
    try:
        assert pool._max_workers == 3
        assert pool is ci._sandbox_pool()
    finally:
        pool.shutdown(wait=False)
        ci._SANDBOX_POOL = None
