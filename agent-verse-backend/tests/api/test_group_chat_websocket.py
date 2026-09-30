from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.coordination_group_chat import router
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext


def _app() -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key == "valid":
            return TenantContext(
                tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="key"
            )
        return None

    app.include_router(router)
    app.state._tenant_key_resolver = resolve
    app.state.transcript_service = TranscriptService(InMemoryTranscriptRepository())
    app.state.settings = SimpleNamespace(cors_origins=("https://app.example",))
    app.state.coordination_session_authorizer = (
        lambda tenant_id, session_id: _authorized(tenant_id, session_id)
    )
    return app


async def _authorized(tenant_id: str, session_id: str) -> bool:
    return tenant_id == "tenant" and session_id == "session"


def test_websocket_auth_origin_append_idempotency_and_replay() -> None:
    client = TestClient(_app())
    with client.websocket_connect(
        "/api/v1/coordination/sessions/session/group-chat/ws",
        headers={"X-API-Key": "valid", "Origin": "https://app.example"},
    ) as socket:
        assert socket.receive_json() == {"type": "replay_complete", "last_sequence": 0}
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}
        payload = {"content": "hello", "client_message_id": "client-1"}
        socket.send_json(payload)
        first = socket.receive_json()
        socket.send_json(payload)
        duplicate = socket.receive_json()
        assert first["message"]["sequence"] == duplicate["message"]["sequence"] == 1
        socket.send_json({"type": "close"})
    with client.websocket_connect(
        "/api/v1/coordination/sessions/session/group-chat/ws?after_sequence=0",
        headers={"X-API-Key": "valid", "Origin": "https://app.example"},
    ) as socket:
        assert socket.receive_json()["message"]["safe_content"] == "hello"
        assert socket.receive_json()["type"] == "replay_complete"


@pytest.mark.parametrize(
    ("headers", "session_id", "code"),
    [
        # Authentication failures use the shared WebSocket authenticator's code.
        ({"X-API-Key": "invalid", "Origin": "https://app.example"}, "session", 4401),
        ({"X-API-Key": "valid", "Origin": "https://evil.example"}, "session", 1008),
        ({"X-API-Key": "valid", "Origin": "https://app.example"}, "other", 1008),
    ],
)
def test_websocket_rejects_unauthorized_connections(
    headers: dict[str, str], session_id: str, code: int
) -> None:
    client = TestClient(_app())
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            f"/api/v1/coordination/sessions/{session_id}/group-chat/ws", headers=headers
        ),
    ):
        pass
    assert exc.value.code == code


def test_websocket_rejects_privileged_messages() -> None:
    client = TestClient(_app())
    with client.websocket_connect(
        "/api/v1/coordination/sessions/session/group-chat/ws",
        headers={"X-API-Key": "valid", "Origin": "https://app.example"},
    ) as socket:
        assert socket.receive_json()["type"] == "replay_complete"
        socket.send_json(
            {"type": "tool_call", "content": "delete", "client_message_id": "x"}
        )
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
    assert exc.value.code == 1008


_WS = "/api/v1/coordination/sessions/session/group-chat/ws"
_HEADERS = {"X-API-Key": "valid", "Origin": "https://app.example"}


def _assert_live_delivery(client: TestClient) -> None:
    with (
        client.websocket_connect(_WS, headers=_HEADERS) as sender,
        client.websocket_connect(_WS, headers=_HEADERS) as receiver,
    ):
        assert sender.receive_json()["type"] == "replay_complete"
        assert receiver.receive_json()["type"] == "replay_complete"
        sender.send_json({"content": "hello team", "client_message_id": "live-1"})
        ack = sender.receive_json()
        assert ack["type"] == "ack"
        # ORG-23: the other participant receives the message without reconnecting.
        live = receiver.receive_json()
        assert live["type"] == "message"
        assert live["message"]["message_id"] == ack["message"]["message_id"]
        assert live["message"]["safe_content"] == "hello team"
        # The sender is not sent its own echo: the next frame it sees is the pong.
        sender.send_json({"type": "ping"})
        assert sender.receive_json() == {"type": "pong"}
        sender.send_json({"type": "close"})
        receiver.send_json({"type": "close"})


def test_group_chat_message_reaches_other_connections_live() -> None:
    _assert_live_delivery(TestClient(_app()))


def test_group_chat_live_delivery_uses_redis_when_configured() -> None:
    import fakeredis

    app = _app()
    app.state._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    _assert_live_delivery(TestClient(app))
