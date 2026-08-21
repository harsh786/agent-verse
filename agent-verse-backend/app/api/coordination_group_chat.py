"""Authorized, replayable WebSocket transport for canonical group-chat messages."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict

from app.coordination.contracts import Classification

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-group-chat"])

_MAX_MESSAGE_BYTES = 16_384
_MAX_MESSAGES_PER_WINDOW = 30
_RATE_WINDOW_SECONDS = 10.0
_PRIVILEGED_MESSAGE_TYPES = frozenset({"approval", "policy", "privilege", "tool", "tool_call"})


class GroupChatWebSocketHandshake(BaseModel):
    model_config = ConfigDict(frozen=True)

    websocket_path: str
    subprotocol: str
    authentication: str
    replay_cursor: str


@router.get(
    "/{session_id}/group-chat/ws",
    response_model=GroupChatWebSocketHandshake,
    operation_id="describe_coordination_group_chat_websocket",
    responses={426: {"description": "Connect using a WebSocket upgrade."}},
)
async def describe_group_chat_websocket(session_id: str) -> GroupChatWebSocketHandshake:
    del session_id
    raise HTTPException(
        status.HTTP_426_UPGRADE_REQUIRED,
        detail={
            "websocket_path": "/api/v1/coordination/sessions/{session_id}/group-chat/ws",
            "subprotocol": "agentverse.coordination.v1",
            "authentication": "Bearer or X-API-Key before upgrade",
            "replay_cursor": "after_sequence query parameter",
        },
        headers={"Upgrade": "websocket"},
    )


def _api_key(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip() or None
    return websocket.headers.get("x-api-key")


async def _authenticate(websocket: WebSocket) -> Any | None:
    resolver = getattr(websocket.app.state, "_tenant_key_resolver", None)
    key = _api_key(websocket)
    if resolver is None or key is None:
        return None
    return await resolver(key)


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    settings = getattr(websocket.app.state, "settings", None)
    configured = getattr(settings, "cors_origins", ()) if settings is not None else ()
    if isinstance(configured, str):
        allowed = {item.strip() for item in configured.split(",") if item.strip()}
    else:
        allowed = {str(item) for item in configured}
    return origin in allowed


@router.websocket("/{session_id}/group-chat/ws", name="coordination_group_chat_websocket")
async def group_chat_websocket(websocket: WebSocket, session_id: str) -> None:
    tenant = await _authenticate(websocket)
    if tenant is None or not _origin_allowed(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    authorizer = getattr(websocket.app.state, "coordination_session_authorizer", None)
    if authorizer is not None and not await authorizer(str(tenant.tenant_id), session_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    service = getattr(websocket.app.state, "transcript_service", None)
    if service is None:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return
    await websocket.accept()
    try:
        after_sequence = max(0, int(websocket.query_params.get("after_sequence", "0")))
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    replay = await service.page(
        str(tenant.tenant_id), session_id, after_sequence=after_sequence, limit=500
    )
    for message in replay:
        await websocket.send_json({"type": "message", "message": message.model_dump(mode="json")})
    await websocket.send_json(
        {
            "type": "replay_complete",
            "last_sequence": replay[-1].sequence if replay else after_sequence,
        }
    )
    received_at: deque[float] = deque()
    try:
        while True:
            payload = await websocket.receive_json()
            message_type = str(payload.get("type", "message"))
            if message_type == "close":
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return
            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if message_type in _PRIVILEGED_MESSAGE_TYPES or message_type != "message":
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            now = time.monotonic()
            while received_at and now - received_at[0] > _RATE_WINDOW_SECONDS:
                received_at.popleft()
            if len(received_at) >= _MAX_MESSAGES_PER_WINDOW:
                await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
                return
            received_at.append(now)
            content = str(payload.get("content", ""))
            if not content or len(content.encode()) > _MAX_MESSAGE_BYTES:
                await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
            client_message_id = str(payload.get("client_message_id", ""))
            if not client_message_id:
                await websocket.send_json({"type": "error", "code": "idempotency_key_required"})
                continue
            try:
                classification = Classification(
                    str(payload.get("classification", Classification.INTERNAL.value))
                )
                stored = await service.append(
                    tenant_id=str(tenant.tenant_id),
                    session_id=session_id,
                    sender_agent_id=f"human:{tenant.api_key_id}",
                    message_type="human",
                    content=content,
                    classification=classification,
                    idempotency_key=client_message_id,
                )
            except ValueError:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            await websocket.send_json({"type": "ack", "message": stored.model_dump(mode="json")})
    except (WebSocketDisconnect, RuntimeError):
        return


__all__ = ["router"]
