"""Proactive outreach is opt-in and its daily cap is shared (a10-F227-01/02).

* F227-02: ``_wire_proactive_engine`` built the engine with no preferences
  provider, so every principal got the permissive default (enabled, no quiet
  hours) and nothing was stored. Now a principal is reached only once an operator
  stored its preferences; quiet hours use the principal's timezone.
* F227-01: with no count hooks the wired cap was the per-replica ``_sent`` dict —
  N replicas allowed N x ``max_per_day`` and a restart reset it. The wired engine
  now reserves slots atomically on Redis (fail closed in production without it).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.bootstrap.routers import _wire_proactive_engine
from app.chat.proactive import ProactivePreferences
from app.chat.service import ChatService
from app.proactive.engine import ProactiveEngine
from app.proactive.limits import (
    AppStateDailyCap,
    DailyCapUnavailableError,
    InMemoryDailyCap,
    RedisDailyCap,
    cap_key,
)
from app.proactive.planner import ProactiveProposal
from app.proactive.preferences import ProactivePreferencesStore
from app.proactive.router import router as proactive_router
from app.proactive.signals import ProactiveSignal, SignalKind


def _clock(hour: int = 12, day: int = 1):  # type: ignore[no-untyped-def]
    return lambda: datetime(2026, 1, day, hour, 0, tzinfo=UTC)


def _signal(tenant: str = "t1", principal: str = "p1", channel: str = "web") -> ProactiveSignal:
    return ProactiveSignal(
        kind=SignalKind.MEMORY_FOLLOWUP, tenant_id=tenant, principal_id=principal,
        channel=channel, payload={"note": "water the plants"},
    )


class _Sink:
    def __init__(self) -> None:
        self.sent: list[ProactiveSignal] = []

    async def __call__(self, signal: ProactiveSignal, _p: ProactiveProposal) -> None:
        self.sent.append(signal)


# ── opt-in ───────────────────────────────────────────────────────────────────


async def test_engine_without_preferences_contacts_nobody() -> None:
    sink = _Sink()
    out = await ProactiveEngine(deliver=sink, clock=_clock()).handle(_signal())
    assert not out.delivered and out.reason == "not_opted_in"
    assert sink.sent == []


async def test_preferences_lookup_error_sends_nothing() -> None:
    def broken(*_a: Any) -> None:
        raise ConnectionError("db down")

    sink = _Sink()
    eng = ProactiveEngine(deliver=sink, clock=_clock(), preferences_provider=broken)
    out = await eng.handle(_signal())
    assert out.reason == "preferences_unavailable" and sink.sent == []


async def test_quiet_hours_are_in_the_principals_timezone() -> None:
    # 14:00 UTC is 23:00 in Tokyo: inside a 22-07 quiet window there, not in UTC.
    tokyo = ProactivePreferences(quiet_hours=(22, 7), timezone="Asia/Tokyo")
    utc = ProactivePreferences(quiet_hours=(22, 7))
    sink = _Sink()
    quiet = ProactiveEngine(deliver=sink, clock=_clock(14), preferences_provider=lambda *_: tokyo)
    awake = ProactiveEngine(deliver=sink, clock=_clock(14), preferences_provider=lambda *_: utc)
    assert (await quiet.handle(_signal())).reason == "quiet_hours"
    assert (await awake.handle(_signal())).delivered


async def test_daily_cap_day_is_the_principals_local_day() -> None:
    # 23:00 and 01:00 UTC are the same day in New York (18:00 / 20:00).
    prefs = ProactivePreferences(max_per_day=1, timezone="America/New_York")
    cap = InMemoryDailyCap()
    sink = _Sink()
    late = ProactiveEngine(
        deliver=sink, clock=_clock(23, 1), preferences_provider=lambda *_: prefs, daily_cap=cap
    )
    next_utc_day = ProactiveEngine(
        deliver=sink, clock=_clock(1, 2), preferences_provider=lambda *_: prefs, daily_cap=cap
    )
    assert (await late.handle(_signal())).delivered
    assert (await next_utc_day.handle(_signal())).reason == "rate_limited"


# ── shared cap ───────────────────────────────────────────────────────────────


async def test_two_replicas_sharing_redis_never_exceed_the_cap() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    prefs = ProactivePreferences(max_per_day=3)
    sink = _Sink()
    replicas = [
        ProactiveEngine(
            deliver=sink, clock=_clock(), preferences_provider=lambda *_: prefs,
            daily_cap=RedisDailyCap(redis),
        )
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        *(replicas[i % 2].handle(_signal()) for i in range(10))
    )
    assert sum(o.delivered for o in outcomes) == 3
    assert {o.reason for o in outcomes if not o.delivered} == {"rate_limited"}
    assert len(sink.sent) == 3
    assert int(await redis.get(cap_key("t1", "p1", "2026-01-01"))) == 3
    assert 0 < await redis.ttl(cap_key("t1", "p1", "2026-01-01")) <= 2 * 86_400


async def test_in_memory_cap_would_let_each_replica_send_its_own_budget() -> None:
    # The pre-fix wiring: one per-process counter per replica.
    prefs = ProactivePreferences(max_per_day=3)
    sink = _Sink()
    replicas = [
        ProactiveEngine(deliver=sink, clock=_clock(), preferences_provider=lambda *_: prefs)
        for _ in range(2)
    ]
    for i in range(10):
        await replicas[i % 2].handle(_signal())
    assert len(sink.sent) == 6  # 2 x the cap - why the wired engine needs Redis


async def test_failed_delivery_gives_the_redis_slot_back() -> None:
    redis = fakeredis.aioredis.FakeRedis()

    async def fail(*_a: Any) -> None:
        raise RuntimeError("thread write failed")

    eng = ProactiveEngine(
        deliver=fail, clock=_clock(),
        preferences_provider=lambda *_: ProactivePreferences(max_per_day=1),
        daily_cap=RedisDailyCap(redis),
    )
    assert (await eng.handle(_signal())).reason == "delivery_failed"
    assert int(await redis.get(cap_key("t1", "p1", "2026-01-01"))) == 0


async def test_redis_error_fails_closed() -> None:
    class _Down:
        def pipeline(self, **_kw: Any) -> Any:
            raise ConnectionError("redis down")

    sink = _Sink()
    eng = ProactiveEngine(
        deliver=sink, clock=_clock(),
        preferences_provider=lambda *_: ProactivePreferences(),
        daily_cap=RedisDailyCap(_Down()),
    )
    out = await eng.handle(_signal())
    assert out.reason == "rate_limit_unavailable" and sink.sent == []


def test_cap_key_is_tenant_scoped_and_unforgeable() -> None:
    assert cap_key("tA", "p", "d") != cap_key("tB", "p", "d")
    assert cap_key("a:b", "c", "d") != cap_key("a", "b:c", "d")


async def test_app_state_cap_uses_redis_when_bound() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    state = SimpleNamespace()
    cap = AppStateDailyCap(state)
    assert await cap.reserve("t1", "p1", "2026-01-01", 1)  # dev: in-memory fallback
    state._redis = redis  # the lifespan binds Redis later
    assert await cap.reserve("t1", "p1", "2026-01-01", 1)
    assert int(await redis.get(cap_key("t1", "p1", "2026-01-01"))) == 1


async def test_app_state_cap_fails_closed_in_production_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.config as config

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(is_production=True)
    )
    with pytest.raises(DailyCapUnavailableError):
        await AppStateDailyCap(SimpleNamespace()).reserve("t1", "p1", "2026-01-01", 3)


# ── the wired engine (app/bootstrap/routers.py) ──────────────────────────────


def _wired_app(redis: Any = None) -> FastAPI:
    app = FastAPI()
    app.state.chat_service = ChatService()
    if redis is not None:
        app.state._redis = redis
    _wire_proactive_engine(app)
    return app


async def test_wired_engine_requires_a_stored_opt_in() -> None:
    app = _wired_app()
    engine = app.state.proactive_engine
    assert (await engine.handle(_signal())).reason == "not_opted_in"

    store: ProactivePreferencesStore = app.state.proactive_preferences
    await store.put("t1", "p1", ProactivePreferences(enabled=False))
    assert (await engine.handle(_signal())).reason == "disabled"

    await store.put("t1", "p1", ProactivePreferences(enabled=True))
    assert (await engine.handle(_signal())).delivered
    # Opted in for tenant t1 only.
    assert (await engine.handle(_signal(tenant="t2"))).reason == "not_opted_in"

    assert await store.delete("t1", "p1")
    assert (await engine.handle(_signal())).reason == "not_opted_in"


async def test_wired_engines_on_two_replicas_share_the_redis_cap() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    apps = [_wired_app(redis), _wired_app(redis)]
    for app in apps:
        await app.state.proactive_preferences.put(
            "t1", "p1", ProactivePreferences(max_per_day=2)
        )
    delivered = 0
    for i in range(6):
        delivered += (await apps[i % 2].state.proactive_engine.handle(_signal())).delivered
    assert delivered == 2


# ── preferences API ──────────────────────────────────────────────────────────


class _Tenant:
    def __init__(self, tid: str, roles: tuple[str, ...]) -> None:
        self.tenant_id = tid
        self.roles = roles
        self.api_key_id = f"key-{tid}"
        self.user_id = None


def _client(app: FastAPI, tenant: str = "t1", roles: tuple[str, ...] = ("operator",)) -> Any:
    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.tenant = _Tenant(request.headers.get("x-t", tenant), roles)
        return await call_next(request)

    app.include_router(proactive_router)
    return TestClient(app)


def test_preferences_api_opt_in_read_and_opt_out() -> None:
    app = _wired_app()
    client = _client(app)
    assert client.get("/v1/proactive/preferences/p1").json()["opted_in"] is False
    assert client.post(
        "/v1/proactive/signals", json={"kind": "memory_followup", "principal_id": "p1",
                                       "payload": {"note": "x"}},
    ).json()["reason"] == "not_opted_in"

    r = client.put("/v1/proactive/preferences/p1", json={
        "enabled": True, "channels": ["web"], "timezone": "Europe/Berlin",
        "quiet_hours": {"start": 22, "end": 7}, "max_per_day": 2,
    })
    assert r.status_code == 200, r.text
    view = client.get("/v1/proactive/preferences/p1").json()
    assert view == {
        "principal_id": "p1", "opted_in": True, "enabled": True, "channels": ["web"],
        "quiet_hours": {"start": 22, "end": 7}, "timezone": "Europe/Berlin",
        "max_per_day": 2,
    }
    # Another tenant does not see tenant t1's record.
    other = client.get("/v1/proactive/preferences/p1", headers={"x-t": "t2"}).json()
    assert other["opted_in"] is False

    assert client.delete("/v1/proactive/preferences/p1").status_code == 204
    assert client.get("/v1/proactive/preferences/p1").json()["opted_in"] is False
    assert client.delete("/v1/proactive/preferences/p1").status_code == 204  # idempotent


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "max_per_day": 21},  # over the hard ceiling
        {"enabled": True, "max_per_day": 0},
        {"enabled": True, "timezone": "Mars/Olympus"},
        {"enabled": True, "channels": ["Web Chat"]},
        {"enabled": True, "channels": []},
        {"enabled": True, "quiet_hours": {"start": 9, "end": 9}},
        {"enabled": True, "quiet_hours": {"start": 24, "end": 7}},
        {"channels": ["web"]},  # enabled is required
    ],
)
def test_preferences_api_rejects_unsafe_values(body: dict[str, Any]) -> None:
    client = _client(_wired_app())
    assert client.put("/v1/proactive/preferences/p1", json=body).status_code == 422


def test_viewer_can_read_but_not_opt_in_or_send() -> None:
    client = _client(_wired_app(), roles=("viewer",))
    assert client.get("/v1/proactive/preferences/p1").status_code == 200
    assert client.put(
        "/v1/proactive/preferences/p1", json={"enabled": True}
    ).status_code == 403
    assert client.delete("/v1/proactive/preferences/p1").status_code == 403
    assert client.post(
        "/v1/proactive/signals", json={"kind": "memory_followup"}
    ).status_code == 403


def test_preferences_store_error_is_503() -> None:
    app = _wired_app()

    class _Broken:
        async def get(self, *_a: Any) -> None:
            raise ConnectionError("db down")

    app.state.proactive_preferences = _Broken()
    client = _client(app)
    assert client.get("/v1/proactive/preferences/p1").status_code == 503
