"""TRG-42: tenant gateway bindings — Slack/Teams /chat, ownership proof, cache.

Bindings were a per-process registry seeded from CHANNEL_TENANT_MAP: Slack and
Teams ``/v1/gateway/{channel}/chat`` always answered 401 (no verification for
them), tenants could not manage bindings, and a change on one replica never
reached the others. (The DB-backed, cross-replica path is covered on real
Postgres + Redis in test_gateway_bindings_integration.py.)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import router as gw
from app.gateway.binding_store import ChannelBindingStore, resolve_binding
from app.gateway.binding_verification import BindingOwnershipError, verify_ownership
from app.gateway.channel_registry import ChannelBinding, ChannelRegistry

_M365 = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


class _Chat:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def achannel_turn(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"session_id": "s1", "reply": "ok", "actions": []}


def _app(reg: ChannelRegistry) -> tuple[TestClient, _Chat]:
    chat = _Chat()
    app = FastAPI()
    app.include_router(gw.router)
    app.state.chat_service = chat
    app.state.channel_registry = reg
    return TestClient(app), chat


def _slack_headers(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    stamp = str(ts if ts is not None else int(time.time()))
    sig = (
        "v0="
        + hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    )
    return {
        "content-type": "application/json",
        "x-slack-request-timestamp": stamp,
        "x-slack-signature": sig,
    }


def _slack_event(team: str = "TACME01") -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "team_id": team,
            "event_id": "Ev1",
            "event": {"type": "message", "text": "hello", "user": "U1", "channel": "C1"},
        }
    ).encode()


# ── Slack ────────────────────────────────────────────────────────────────────


def test_slack_chat_signed_with_the_binding_secret_is_accepted() -> None:
    reg = ChannelRegistry()
    reg.register("slack", "TACME01", "tenant-acme", secret="acme-signing-secret")
    client, chat = _app(reg)
    body = _slack_event()
    r = client.post(
        "/v1/gateway/slack/chat", content=body, headers=_slack_headers("acme-signing-secret", body)
    )
    assert r.status_code == 200, r.text
    assert chat.calls and chat.calls[0]["tenant_id"] == "tenant-acme"


def test_slack_chat_with_another_secret_is_refused() -> None:
    reg = ChannelRegistry()
    reg.register("slack", "TACME01", "tenant-acme", secret="acme-signing-secret")
    client, chat = _app(reg)
    body = _slack_event()
    r = client.post(
        "/v1/gateway/slack/chat", content=body, headers=_slack_headers("someone-else", body)
    )
    assert r.status_code == 401
    assert chat.calls == []


def test_stale_slack_signature_is_refused() -> None:
    reg = ChannelRegistry()
    reg.register("slack", "TACME01", "tenant-acme", secret="s")
    client, chat = _app(reg)
    body = _slack_event()
    r = client.post(
        "/v1/gateway/slack/chat",
        content=body,
        headers=_slack_headers("s", body, ts=int(time.time()) - 3600),
    )
    assert r.status_code == 401
    assert chat.calls == []


def test_slack_url_verification_answers_the_challenge_after_the_signature() -> None:
    reg = ChannelRegistry()
    reg.register("slack", "TACME01", "tenant-acme", secret="s")
    client, chat = _app(reg)
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()
    ok = client.post(
        "/v1/gateway/slack/chat/TACME01", content=body, headers=_slack_headers("s", body)
    )
    assert ok.json() == {"challenge": "c-123"}
    bad = client.post(
        "/v1/gateway/slack/chat/TACME01", content=body, headers=_slack_headers("x", body)
    )
    assert bad.status_code == 401
    assert chat.calls == []


# ── Teams ────────────────────────────────────────────────────────────────────


def _teams_activity(m365: str = _M365) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "act-1",
        "text": "status please",
        "from": {"id": "29:user"},
        "conversation": {"id": "conv-1"},
        "channelData": {"tenant": {"id": m365}},
    }


@pytest.fixture
def teams_jwt(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand-in for Bot Framework JWT validation: valid iff aud == the app id."""
    audiences: list[str] = []

    async def _verify(self: Any, headers: dict[str, str], raw: Any, raw_body: Any = None) -> bool:
        audiences.append(self._app_id)
        return headers.get("authorization") == f"Bearer jwt-for-{self._app_id}"

    monkeypatch.setattr(gw.MicrosoftTeamsAdapter, "verify_auth", _verify)
    return audiences


def test_teams_chat_with_a_jwt_for_the_binding_app_is_accepted(teams_jwt: list[str]) -> None:
    reg = ChannelRegistry()
    reg.register("teams", _M365, "tenant-contoso")
    reg._map[("teams", _M365)] = ChannelBinding(tenant_id="tenant-contoso", app_id="app-contoso")
    client, chat = _app(reg)
    r = client.post(
        "/v1/gateway/teams/chat",
        json=_teams_activity(),
        headers={"authorization": "Bearer jwt-for-app-contoso"},
    )
    assert r.status_code == 200, r.text
    assert teams_jwt == ["app-contoso"]
    assert chat.calls[0]["tenant_id"] == "tenant-contoso"


def test_teams_jwt_for_another_app_is_refused(teams_jwt: list[str]) -> None:
    reg = ChannelRegistry()
    reg._map[("teams", _M365)] = ChannelBinding(tenant_id="tenant-contoso", app_id="app-contoso")
    client, chat = _app(reg)
    r = client.post(
        "/v1/gateway/teams/chat",
        json=_teams_activity(),
        headers={"authorization": "Bearer jwt-for-app-attacker"},
    )
    assert r.status_code == 401
    assert chat.calls == []


def test_teams_activity_from_another_m365_org_is_refused(teams_jwt: list[str]) -> None:
    reg = ChannelRegistry()
    reg._map[("teams", _M365)] = ChannelBinding(tenant_id="tenant-contoso", app_id="app-contoso")
    client, chat = _app(reg)
    other = "11111111-2222-3333-4444-555555555555"
    r = client.post(
        f"/v1/gateway/teams/chat/{_M365}",
        json=_teams_activity(m365=other),
        headers={"authorization": "Bearer jwt-for-app-contoso"},
    )
    assert r.status_code == 401
    assert chat.calls == []


# ── ownership proof ──────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.status_code = status
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _Http:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.resp = _Resp(status, data)
        self.urls: list[str] = []

    async def get(self, url: str, **_: Any) -> _Resp:
        self.urls.append(url)
        return self.resp

    async def post(self, url: str, **_: Any) -> _Resp:
        self.urls.append(url)
        return self.resp


_TG_TOKEN = "123456789:" + "A" * 35


@pytest.mark.asyncio
async def test_telegram_ownership_needs_getme_to_return_the_bot() -> None:
    http = _Http(200, {"ok": True, "result": {"id": 123456789}})
    await verify_ownership("telegram", "123456789", outbound_token=_TG_TOKEN, http=http)
    assert http.urls == [f"https://api.telegram.org/bot{_TG_TOKEN}/getMe"]


@pytest.mark.asyncio
async def test_telegram_token_of_another_bot_is_refused_without_a_call() -> None:
    http = _Http(200, {"ok": True, "result": {"id": 999}})
    with pytest.raises(BindingOwnershipError):
        await verify_ownership("telegram", "555555555", outbound_token=_TG_TOKEN, http=http)
    assert http.urls == []


@pytest.mark.asyncio
async def test_telegram_rejected_token_is_refused() -> None:
    http = _Http(401, {"ok": False})
    with pytest.raises(BindingOwnershipError):
        await verify_ownership("telegram", "123456789", outbound_token=_TG_TOKEN, http=http)


@pytest.mark.asyncio
async def test_whatsapp_ownership() -> None:
    await verify_ownership(
        "whatsapp", "1098765432", outbound_token="EAAG", http=_Http(200, {"id": "1098765432"})
    )
    with pytest.raises(BindingOwnershipError):
        await verify_ownership(
            "whatsapp", "1098765432", outbound_token="EAAG", http=_Http(400, {"error": {}})
        )
    with pytest.raises(BindingOwnershipError):  # path injection is refused by shape
        await verify_ownership("whatsapp", "../me", outbound_token="EAAG", http=_Http(200, {}))


@pytest.mark.asyncio
async def test_slack_ownership_via_auth_test() -> None:
    await verify_ownership(
        "slack",
        "TACME01",
        outbound_token="xoxb-1",
        http=_Http(200, {"ok": True, "team_id": "TACME01"}),
    )
    with pytest.raises(BindingOwnershipError):
        await verify_ownership(
            "slack",
            "TACME01",
            outbound_token="xoxb-1",
            http=_Http(200, {"ok": True, "team_id": "TOTHER1"}),
        )


# ── per-replica cache invalidated by the shared version ──────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        return str(self.data[key]).encode() if key in self.data else None

    async def incr(self, key: str) -> int:
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]


class _State:
    def __init__(self, redis: _FakeRedis) -> None:
        self._rate_limiter_redis = redis
        self.system_db_session_factory = object()  # replaced by the _load stub


@pytest.mark.asyncio
async def test_a_write_on_one_replica_invalidates_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replica B caches a binding; replica A deletes it and bumps the shared
    version; B's next resolve reloads and stops routing."""
    table: dict[tuple[str, str], ChannelBinding] = {
        ("telegram", "123"): ChannelBinding(tenant_id="t1", secret="s")
    }
    loads: list[str] = []

    async def _load(self: ChannelBindingStore, channel: str, addressee: str) -> Any:
        loads.append(addressee)
        return table.get((channel, addressee))

    monkeypatch.setattr(ChannelBindingStore, "_load", _load)
    redis = _FakeRedis()
    a = ChannelBindingStore(_State(redis))
    b = ChannelBindingStore(_State(redis))
    state_b = type("S", (), {"channel_binding_store": b})()

    assert (await resolve_binding(state_b, "telegram", "123")).tenant_id == "t1"  # type: ignore[union-attr]
    assert (await resolve_binding(state_b, "telegram", "123")) is not None
    assert loads == ["123"]  # second hit served from B's cache

    table.clear()
    await a.invalidate()  # A unbinds
    assert await resolve_binding(state_b, "telegram", "123") is None
    assert loads == ["123", "123"]


@pytest.mark.asyncio
async def test_db_binding_wins_over_the_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _load(self: ChannelBindingStore, channel: str, addressee: str) -> Any:
        return ChannelBinding(tenant_id="db-tenant", secret="s") if addressee == "1" else None

    monkeypatch.setattr(ChannelBindingStore, "_load", _load)
    env = ChannelRegistry()
    env.register("telegram", "1", "env-tenant", secret="e")
    env.register("telegram", "2", "env-only", secret="e")
    store = ChannelBindingStore(_State(_FakeRedis()), env_registry=env)
    assert (await store.aresolve("telegram", "1")).tenant_id == "db-tenant"  # type: ignore[union-attr]
    assert (await store.aresolve("telegram", "2")).tenant_id == "env-only"  # type: ignore[union-attr]
