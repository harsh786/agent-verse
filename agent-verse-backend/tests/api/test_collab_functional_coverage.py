"""Functional/contract coverage for the collaboration endpoints that were
previously untested: org presence websocket, session insights, the CRDT
short-lived token endpoint, the CRDT room manager, and the Yjs CRDT
websocket sync endpoint.

Supplements tests/api/test_collab.py, test_collab_extra3.py and
test_collab_api_comprehensive.py — see those for the base session/operation
CRUD and the primary collab websocket coverage.
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.collab import (
    CRDTRoomManager,
    _crdt_tokens,
    router as collab_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT_A = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="key-a")
TENANT_B = TenantContext(tenant_id="tid-b", plan=PlanTier.FREE, api_key_id="key-b")
KEY_A = "key-a"
KEY_B = "key-b"


class FakeCollabStore:
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self.operations: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def list_sessions(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [s for (tid, _), s in self.sessions.items() if tid == tenant_ctx.tenant_id]

    async def create_session(
        self,
        *,
        tenant_ctx: TenantContext,
        name: str,
        mode: str,
        participants: list[str],
        goal_id: str | None = None,
        agent_id: str | None = None,
        content: str = "",
    ) -> dict[str, Any]:
        session_id = f"session-{len(self.sessions) + 1}"
        session = {
            "session_id": session_id,
            "tenant_id": tenant_ctx.tenant_id,
            "name": name,
            "mode": mode,
            "participants": participants,
            "participant_count": len(participants),
            "goal_id": goal_id,
            "agent_id": agent_id,
            "status": "active",
            "content": content,
            "rounds": [],
            "created_at": "2026-06-25T00:00:00+00:00",
            "updated_at": "2026-06-25T00:00:00+00:00",
        }
        self.sessions[(tenant_ctx.tenant_id, session_id)] = session
        self.operations[(tenant_ctx.tenant_id, session_id)] = []
        return dict(session)

    async def get_session(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> dict[str, Any] | None:
        session = self.sessions.get((tenant_ctx.tenant_id, session_id))
        return dict(session) if session else None

    async def close_session(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> dict[str, Any] | None:
        session = self.sessions.get((tenant_ctx.tenant_id, session_id))
        if session is None:
            return None
        session["status"] = "closed"
        return dict(session)

    async def append_operation(
        self,
        *,
        tenant_ctx: TenantContext,
        session_id: str,
        operation: dict[str, Any],
        author: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        ops = self.operations[(tenant_ctx.tenant_id, session_id)]
        op = {
            "operation_id": f"op-{len(ops) + 1}",
            "session_id": session_id,
            "tenant_id": tenant_ctx.tenant_id,
            "version": len(ops) + 1,
            "operation": operation,
            "author": author,
            "created_at": "2026-06-25T00:00:00+00:00",
        }
        ops.append(op)
        return dict(op)

    async def list_operations(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> list[dict[str, Any]]:
        return list(self.operations.get((tenant_ctx.tenant_id, session_id), []))


def _make_app(store: FakeCollabStore | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == KEY_A:
            return TENANT_A
        if key == KEY_B:
            return TENANT_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(collab_router)
    app.state.collab_store = store or FakeCollabStore()
    app.state._tenant_key_resolver = _resolve
    return app


# ---------------------------------------------------------------------------
# Org presence websocket — /collab/presence/{org_id}/ws
# ---------------------------------------------------------------------------


def test_presence_websocket_rejects_unauthenticated() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    try:
        with client.websocket_connect("/collab/presence/org-1/ws"):
            raise AssertionError("unauthenticated presence connection unexpectedly succeeded")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_presence_websocket_join_and_section_update_broadcast() -> None:
    """Two viewers of the same org see each other's join and section updates."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    protocol_a = f"av.v1.{_encode(KEY_A)}"

    with client.websocket_connect(
        "/collab/presence/org-1/ws", subprotocols=[protocol_a]
    ) as ws1:
        with client.websocket_connect(
            "/collab/presence/org-1/ws", subprotocols=[protocol_a]
        ) as ws2:
            # ws1 should be told about the newcomer ws2 joining.
            joined = json.loads(ws1.receive_text())
            assert joined["type"] == "presence.update"
            assert joined["section"] is None

            # ws2 announces a section change; ws1 should observe it.
            ws2.send_text(json.dumps({"type": "presence.update", "section": "dashboard"}))
            update = json.loads(ws1.receive_text())
            assert update["type"] == "presence.update"
            assert update["section"] == "dashboard"

        # ws2 disconnected — ws1 should get a presence.leave for it.
        left = json.loads(ws1.receive_text())
        assert left["type"] == "presence.leave"
        assert left["userId"] == update["userId"]


def test_presence_websocket_tenant_isolation() -> None:
    """A viewer under tenant B never sees tenant A's presence for the same org id."""
    from app.api.collab import _presence_conns

    client = TestClient(_make_app(), raise_server_exceptions=False)
    protocol_a = f"av.v1.{_encode(KEY_A)}"
    protocol_b = f"av.v1.{_encode(KEY_B)}"

    with client.websocket_connect(
        "/collab/presence/org-1/ws", subprotocols=[protocol_a]
    ) as ws_a:
        with client.websocket_connect(
            "/collab/presence/org-1/ws", subprotocols=[protocol_b]
        ) as ws_b:
            ws_b.send_text(json.dumps({"type": "presence.update", "section": "settings"}))
            # Tenants are keyed separately, so tenant A's and B's peer lists
            # for the "same" org id never overlap.
            key_a = (TENANT_A.tenant_id, "org-1")
            key_b = (TENANT_B.tenant_id, "org-1")
            assert len(_presence_conns.get(key_a, [])) == 1
            assert len(_presence_conns.get(key_b, [])) == 1
            assert _presence_conns[key_a][0]["ws"] is not _presence_conns[key_b][0]["ws"]


def test_presence_websocket_ignores_non_json_and_non_presence_messages() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    protocol_a = f"av.v1.{_encode(KEY_A)}"
    with client.websocket_connect(
        "/collab/presence/org-1/ws", subprotocols=[protocol_a]
    ) as ws:
        ws.send_text("not-json")
        ws.send_text(json.dumps({"type": "something.else"}))
        # Connection stays open — send a real update to confirm liveness.
        ws.send_text(json.dumps({"type": "presence.update", "section": "x"}))
    # No assertion needed beyond "no exception raised" — the endpoint must not
    # crash or close the connection on malformed/irrelevant input.


def _encode(key: str) -> str:
    import base64

    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


# ---------------------------------------------------------------------------
# Session insights — POST /collab/sessions/{session_id}/insights
# ---------------------------------------------------------------------------


def test_insights_session_not_found_returns_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/sessions/ghost/insights", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 404


def test_insights_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/sessions/s1/insights")
    assert resp.status_code == 401


def test_insights_without_llm_provider_falls_back_to_rule_based() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Insights"}, headers={"X-API-Key": KEY_A}
    ).json()
    sid = session["session_id"]
    client.post(
        f"/collab/sessions/{sid}/operations",
        json={"type": "content_update", "content": "Ship the feature", "author": "human:pm"},
        headers={"X-API-Key": KEY_A},
    )

    resp = client.post(f"/collab/sessions/{sid}/insights", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_powered"] is False
    assert data["agreement_level"] == "unknown"
    assert data["sentiment"] == "neutral"
    assert data["session_id"] == sid
    assert "[human:pm]" in data["key_decisions"][0]


def test_insights_with_no_content_and_no_provider_returns_empty_decisions() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Empty"}, headers={"X-API-Key": KEY_A}
    ).json()
    resp = client.post(
        f"/collab/sessions/{session['session_id']}/insights", headers={"X-API-Key": KEY_A}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["key_decisions"] == []
    assert data["llm_powered"] is False


def test_insights_with_llm_provider_success_returns_structured_json() -> None:
    store = FakeCollabStore()
    app = _make_app(store)
    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "key_decisions": ["Use Postgres"],
            "action_items": ["Write migration"],
            "open_questions": [],
            "agreement_level": "high",
            "sentiment": "positive",
            "summary": "Team agreed on Postgres.",
        }
    )
    mock_provider.complete = AsyncMock(return_value=mock_response)
    app.state.llm_provider = mock_provider

    client = TestClient(app, raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "LLM"}, headers={"X-API-Key": KEY_A}
    ).json()
    sid = session["session_id"]
    client.post(
        f"/collab/sessions/{sid}/operations",
        json={"type": "content_update", "content": "Let's use Postgres", "author": "human:lead"},
        headers={"X-API-Key": KEY_A},
    )

    resp = client.post(f"/collab/sessions/{sid}/insights", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_powered"] is True
    assert data["key_decisions"] == ["Use Postgres"]
    assert data["agreement_level"] == "high"
    mock_provider.complete.assert_awaited_once()


def test_insights_with_llm_provider_strips_markdown_code_fence() -> None:
    store = FakeCollabStore()
    app = _make_app(store)
    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.content = (
        "```json\n"
        + json.dumps(
            {
                "key_decisions": ["Fenced"],
                "action_items": [],
                "open_questions": [],
                "agreement_level": "medium",
                "sentiment": "neutral",
                "summary": "ok",
            }
        )
        + "\n```"
    )
    mock_provider.complete = AsyncMock(return_value=mock_response)
    app.state.llm_provider = mock_provider

    client = TestClient(app, raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Fenced"}, headers={"X-API-Key": KEY_A}
    ).json()
    sid = session["session_id"]
    client.post(
        f"/collab/sessions/{sid}/operations",
        json={"type": "content_update", "content": "text", "author": "human"},
        headers={"X-API-Key": KEY_A},
    )
    resp = client.post(f"/collab/sessions/{sid}/insights", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 200
    assert resp.json()["key_decisions"] == ["Fenced"]


def test_insights_with_llm_provider_failure_returns_fallback_summary() -> None:
    store = FakeCollabStore()
    app = _make_app(store)
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=RuntimeError("provider down"))
    app.state.llm_provider = mock_provider

    client = TestClient(app, raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Fails"}, headers={"X-API-Key": KEY_A}
    ).json()
    sid = session["session_id"]
    client.post(
        f"/collab/sessions/{sid}/operations",
        json={"type": "content_update", "content": "text", "author": "human"},
        headers={"X-API-Key": KEY_A},
    )
    resp = client.post(f"/collab/sessions/{sid}/insights", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 200
    data = resp.json()
    # Provider was invoked (llm_powered True) but the extraction failed, so we
    # get the deterministic failure-shaped payload rather than a 500.
    assert data["llm_powered"] is True
    assert data["agreement_level"] == "unknown"
    assert "failed" in data["summary"].lower()


def test_insights_tenant_isolation() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Private"}, headers={"X-API-Key": KEY_A}
    ).json()
    resp = client.post(
        f"/collab/sessions/{session['session_id']}/insights", headers={"X-API-Key": KEY_B}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CRDT token endpoint — POST /collab/crdt-token
# ---------------------------------------------------------------------------


def test_generate_crdt_token_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/crdt-token")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_generate_crdt_token_raises_401_at_function_level_without_tenant() -> None:
    """Exercise the endpoint function directly (middleware bypassed) so the
    in-function 401 guard itself executes, not just the middleware's own 401."""
    from fastapi import HTTPException

    from app.api.collab import generate_crdt_token

    req = MagicMock()
    req.state = MagicMock(spec=[])  # no "tenant" attribute at all

    with pytest.raises(HTTPException) as excinfo:
        await generate_crdt_token(req)
    assert excinfo.value.status_code == 401


def test_generate_crdt_token_returns_token_and_ttl() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/crdt-token", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 201
    data = resp.json()
    assert isinstance(data["token"], str) and len(data["token"]) > 20
    assert data["expires_in"] == 3600
    assert _crdt_tokens[data["token"]]["tenant_id"] == TENANT_A.tenant_id


def test_generate_crdt_token_evicts_expired_tokens() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    _crdt_tokens["stale-token"] = {"tenant_id": "someone", "expires_at": time.monotonic() - 1}
    resp = client.post("/collab/crdt-token", headers={"X-API-Key": KEY_A})
    assert resp.status_code == 201
    assert "stale-token" not in _crdt_tokens


# ---------------------------------------------------------------------------
# CRDTRoomManager — direct unit tests
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent_bytes: list[bytes] = []
        self.fail_send = False

    async def send_bytes(self, data: bytes) -> None:
        if self.fail_send:
            raise RuntimeError("peer gone")
        self.sent_bytes.append(data)


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def close(self) -> None:
        self.closed = True

    async def listen(self):
        for m in self._messages:
            yield m


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.published: list[tuple[str, bytes]] = []
        self.counters: dict[str, int] = {}
        self._pubsub_messages: list[dict[str, Any]] = []

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def publish(self, channel: str, data: Any) -> None:
        self.published.append((channel, data))

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> None:
        pass

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._pubsub_messages)


@pytest.mark.asyncio
async def test_room_manager_join_and_leave_local_rooms() -> None:
    mgr = CRDTRoomManager()
    ws = _FakeWebSocket()
    await mgr.join("room-1", ws)  # type: ignore[arg-type]
    assert ws in mgr._local_rooms["room-1"]
    await mgr.leave("room-1", ws)  # type: ignore[arg-type]
    assert "room-1" not in mgr._local_rooms


@pytest.mark.asyncio
async def test_room_manager_snapshot_noop_without_redis() -> None:
    mgr = CRDTRoomManager()
    await mgr.save_snapshot("room-1", b"data")  # should not raise
    assert await mgr.load_snapshot("room-1") is None


@pytest.mark.asyncio
async def test_room_manager_snapshot_roundtrip_with_redis() -> None:
    mgr = CRDTRoomManager()
    fake = _FakeRedis()
    mgr.set_redis(fake)
    await mgr.save_snapshot("room-1", b"\x01\x02")
    loaded = await mgr.load_snapshot("room-1")
    assert loaded == b"\x01\x02"


@pytest.mark.asyncio
async def test_room_manager_load_snapshot_decodes_str() -> None:
    mgr = CRDTRoomManager()
    fake = _FakeRedis()
    fake.store["crdt:snapshot:room-9"] = "abc"
    mgr.set_redis(fake)
    loaded = await mgr.load_snapshot("room-9")
    assert loaded == b"abc"


@pytest.mark.asyncio
async def test_room_manager_broadcast_delivers_to_peers_excluding_sender() -> None:
    mgr = CRDTRoomManager()
    sender, peer = _FakeWebSocket(), _FakeWebSocket()
    await mgr.join("room-1", sender)  # type: ignore[arg-type]
    await mgr.join("room-1", peer)  # type: ignore[arg-type]
    await mgr.broadcast("room-1", b"payload", sender)  # type: ignore[arg-type]
    assert peer.sent_bytes == [b"payload"]
    assert sender.sent_bytes == []


@pytest.mark.asyncio
async def test_room_manager_broadcast_drops_dead_peers() -> None:
    mgr = CRDTRoomManager()
    sender, dead_peer = _FakeWebSocket(), _FakeWebSocket()
    dead_peer.fail_send = True
    await mgr.join("room-1", sender)  # type: ignore[arg-type]
    await mgr.join("room-1", dead_peer)  # type: ignore[arg-type]
    await mgr.broadcast("room-1", b"payload", sender)  # type: ignore[arg-type]
    assert dead_peer not in mgr._local_rooms["room-1"]


@pytest.mark.asyncio
async def test_room_manager_broadcast_publishes_to_redis_when_available() -> None:
    mgr = CRDTRoomManager()
    fake = _FakeRedis()
    mgr.set_redis(fake)
    sender = _FakeWebSocket()
    await mgr.broadcast("room-1", b"payload", sender)  # type: ignore[arg-type]
    assert fake.published == [("crdt:room-1", b"payload")]


@pytest.mark.asyncio
async def test_room_manager_broadcast_saves_periodic_snapshot_every_50() -> None:
    mgr = CRDTRoomManager()
    fake = _FakeRedis()
    fake.counters["crdt:update_count:room-1"] = 49  # next incr() -> 50
    mgr.set_redis(fake)
    sender = _FakeWebSocket()
    await mgr.broadcast("room-1", b"payload", sender)  # type: ignore[arg-type]
    assert fake.store["crdt:snapshot:room-1"] == b"payload"


@pytest.mark.asyncio
async def test_room_manager_subscribe_redis_noop_without_redis() -> None:
    mgr = CRDTRoomManager()
    ws = _FakeWebSocket()
    await mgr.subscribe_redis("room-1", ws)  # type: ignore[arg-type]
    assert ws.sent_bytes == []


@pytest.mark.asyncio
async def test_room_manager_subscribe_redis_forwards_messages() -> None:
    mgr = CRDTRoomManager()
    fake = _FakeRedis()
    fake._pubsub_messages = [
        {"type": "subscribe", "data": None},
        {"type": "message", "data": b"binary-update"},
        {"type": "message", "data": "text-update"},
    ]
    mgr.set_redis(fake)
    ws = _FakeWebSocket()
    await mgr.subscribe_redis("room-1", ws)  # type: ignore[arg-type]
    assert ws.sent_bytes == [b"binary-update", b"text-update"]


# ---------------------------------------------------------------------------
# Yjs CRDT websocket sync — /collab/crdt/{room_id}
# ---------------------------------------------------------------------------


def test_crdt_ws_rejects_when_no_token_or_api_key() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    try:
        with client.websocket_connect("/collab/crdt/collab-tid-a-s1"):
            raise AssertionError("unauthenticated CRDT connection unexpectedly succeeded")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_crdt_ws_rejects_invalid_token() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    try:
        with client.websocket_connect("/collab/crdt/collab-tid-a-s1?token=does-not-exist"):
            raise AssertionError("invalid CRDT token unexpectedly accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_crdt_ws_rejects_expired_token() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    _crdt_tokens["expired-tok"] = {
        "tenant_id": TENANT_A.tenant_id,
        "expires_at": time.monotonic() - 1,
    }
    try:
        with client.websocket_connect("/collab/crdt/collab-tid-a-s1?token=expired-tok"):
            raise AssertionError("expired CRDT token unexpectedly accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_crdt_ws_valid_token_wrong_room_prefix_forbidden() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/crdt-token", headers={"X-API-Key": KEY_A})
    token = resp.json()["token"]
    try:
        with client.websocket_connect(f"/collab/crdt/collab-tid-b-s1?token={token}"):
            raise AssertionError("cross-tenant room access unexpectedly accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4403


def test_crdt_ws_valid_token_joins_room_and_broadcasts_binary() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/collab/crdt-token", headers={"X-API-Key": KEY_A})
    token = resp.json()["token"]
    room = "collab-tid-a-session1"

    with client.websocket_connect(f"/collab/crdt/{room}?token={token}") as ws1:
        with client.websocket_connect(f"/collab/crdt/{room}?token={token}") as ws2:
            ws1.send_bytes(b"\x00\x01update")
            received = ws2.receive_bytes()
            assert received == b"\x00\x01update"


def test_crdt_ws_api_key_fallback_success() -> None:
    app = _make_app()
    mock_svc = MagicMock()
    mock_svc.resolve_api_key = AsyncMock(return_value=TENANT_A)
    app.state.tenant_service = mock_svc
    client = TestClient(app, raise_server_exceptions=False)

    with client.websocket_connect(f"/collab/crdt/collab-{TENANT_A.tenant_id}-s1?api_key={KEY_A}"):
        pass  # Reaching accept() without raising is the assertion.
    mock_svc.resolve_api_key.assert_awaited_once_with(KEY_A)


def test_crdt_ws_api_key_fallback_invalid_key_closes() -> None:
    app = _make_app()
    mock_svc = MagicMock()
    mock_svc.resolve_api_key = AsyncMock(return_value=None)
    app.state.tenant_service = mock_svc
    client = TestClient(app, raise_server_exceptions=False)

    try:
        with client.websocket_connect("/collab/crdt/collab-x-s1?api_key=bogus"):
            raise AssertionError("invalid api_key unexpectedly accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_crdt_ws_api_key_fallback_no_tenant_service_closes() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    try:
        with client.websocket_connect("/collab/crdt/collab-x-s1?api_key=anything"):
            raise AssertionError("api_key path without tenant_service unexpectedly accepted")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401


def test_collab_ws_broadcast_drops_dead_local_peer() -> None:
    """A stale connection left in _ws_connections is pruned when a broadcast
    to it fails, instead of breaking the sender's flow (app/api/collab.py
    lines ~544-548)."""
    import app.api.collab as collab_mod

    store = FakeCollabStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    session = client.post(
        "/collab/sessions", json={"name": "Dead Peer"}, headers={"X-API-Key": KEY_A}
    ).json()
    sid = session["session_id"]

    class _BrokenPeer:
        async def send_text(self, _text: str) -> None:
            raise RuntimeError("peer socket is gone")

    protocol_a = f"av.v1.{_encode(KEY_A)}"
    with client.websocket_connect(
        f"/collab/sessions/{sid}/ws", subprotocols=[protocol_a]
    ) as ws:
        broken = _BrokenPeer()
        collab_mod._ws_connections[sid].append(broken)  # type: ignore[arg-type]
        assert broken in collab_mod._ws_connections[sid]

        ws.send_text(json.dumps({"type": "message", "content": "hi", "author": "human"}))
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "ack"

        # The broadcast loop tried to deliver to `broken`, failed, and pruned it.
        assert broken not in collab_mod._ws_connections[sid]


def test_crdt_ws_wires_redis_from_app_state_and_delivers_snapshot() -> None:
    """When app.state._redis is configured, the CRDT websocket wires it into
    the singleton room manager and serves a saved snapshot to new joiners
    (app/api/collab.py lines ~920-929)."""
    import app.api.collab as collab_mod

    app = _make_app()
    fake_redis = _FakeRedis()
    app.state._redis = fake_redis
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/collab/crdt-token", headers={"X-API-Key": KEY_A})
    token = resp.json()["token"]
    room = f"collab-{TENANT_A.tenant_id}-snapshot-room"

    try:
        # Pre-populate a snapshot as if a prior session had saved one.
        with client.websocket_connect(f"/collab/crdt/{room}?token={token}"):
            pass
        assert collab_mod._crdt_manager._redis is fake_redis

        # Seed the fake redis snapshot key the way a prior save_snapshot() would.
        fake_redis.store[f"crdt:snapshot:{room}"] = b"prior-state"

        with client.websocket_connect(f"/collab/crdt/{room}?token={token}") as ws:
            received = ws.receive_bytes()
            assert received == b"prior-state"
    finally:
        collab_mod._crdt_manager.set_redis(None)


def test_crdt_ws_api_key_fallback_resolver_exception_closes() -> None:
    app = _make_app()
    mock_svc = MagicMock()
    mock_svc.resolve_api_key = AsyncMock(side_effect=RuntimeError("db down"))
    app.state.tenant_service = mock_svc
    client = TestClient(app, raise_server_exceptions=False)

    try:
        with client.websocket_connect("/collab/crdt/collab-x-s1?api_key=anything"):
            raise AssertionError("resolver exception unexpectedly resulted in accepted connection")
    except Exception as exc:
        assert getattr(exc, "code", None) == 4401
