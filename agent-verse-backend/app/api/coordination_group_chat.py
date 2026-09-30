"""Authorized, replayable WebSocket transport for canonical group-chat messages."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict

from app.coordination.contracts import Classification
from app.coordination.live_bus import (
    CoordinationLiveBus,
    LiveDeliveryUnavailableError,
    live_bus_for,
)
from app.tenancy.context import TenantContext

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
            "authentication": (
                "X-API-Key, Bearer, or an av.v1.<base64url(key)> subprotocol before upgrade"
            ),
            "replay_cursor": "after_sequence query parameter",
        },
        headers={"Upgrade": "websocket"},
    )


_SUBPROTOCOL = "agentverse.coordination.v1"


async def _authenticate(websocket: WebSocket) -> TenantContext | None:
    """Delegate to the shared WebSocket authenticator.

    This socket used to resolve the API key itself, which skipped the tenant IP
    allowlist, the key's explicit scopes / roles and MFA that
    ``app.tenancy.ws_auth`` enforces (HTTP middleware never runs for WebSocket
    scopes). Coordination endpoints have no registered scope, and the socket
    appends messages, so it is authorised like an unregistered write endpoint.
    """
    from app.tenancy.ws_auth import resolve_ws_tenant

    return cast(
        TenantContext | None,
        await resolve_ws_tenant(websocket, required_scope=None, write=True),
    )


def _selected_subprotocol(websocket: WebSocket) -> str | None:
    """Echo an offered subprotocol — a browser aborts the handshake otherwise."""
    offered = [
        p.strip()
        for p in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if p.strip()
    ]
    if _SUBPROTOCOL in offered:
        return _SUBPROTOCOL
    return next((p for p in offered if p.startswith("av.v1.")), None)


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
    if tenant is None:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    if not _origin_allowed(websocket):
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
    await websocket.accept(subprotocol=_selected_subprotocol(websocket))
    try:
        after_sequence = max(0, int(websocket.query_params.get("after_sequence", "0")))
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    tenant_id = str(tenant.tenant_id)
    bus = live_bus_for(websocket.app.state)
    connection_id = uuid.uuid4().hex
    try:
        # Subscribe BEFORE replaying so nothing published between the replay read and
        # the subscription is lost; overlap is dropped by sequence below.
        async with bus.subscribe(tenant_id, session_id) as live:
            await _serve(
                websocket,
                service=service,
                tenant=tenant,
                session_id=session_id,
                after_sequence=after_sequence,
                bus=bus,
                live=live,
                connection_id=connection_id,
            )
    except LiveDeliveryUnavailableError:
        # Live fan-out is part of the contract: refuse rather than silently degrade
        # to a sender-only ack. The client reconnects and replays from the transcript.
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
    except (WebSocketDisconnect, RuntimeError):
        return


async def _serve(
    websocket: WebSocket,
    *,
    service: Any,
    tenant: TenantContext,
    session_id: str,
    after_sequence: int,
    bus: CoordinationLiveBus,
    live: AsyncIterator[dict[str, Any]],
    connection_id: str,
) -> None:
    tenant_id = str(tenant.tenant_id)
    send_lock = asyncio.Lock()

    async def send(frame: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(frame)

    replay = await service.page(tenant_id, session_id, after_sequence=after_sequence, limit=500)
    for message in replay:
        await send({"type": "message", "message": message.model_dump(mode="json")})
    last_sequence = replay[-1].sequence if replay else after_sequence
    await send({"type": "replay_complete", "last_sequence": last_sequence})

    async def forward() -> None:
        async for frame in live:
            if frame.get("origin") == connection_id:
                continue  # the sender already received its ack
            message = frame.get("message")
            sequence = message.get("sequence") if isinstance(message, dict) else None
            if (
                frame.get("type") == "message"
                and isinstance(sequence, int)
                and sequence <= last_sequence
            ):
                continue  # already delivered by the replay above
            outbound = {key: value for key, value in frame.items() if key != "origin"}
            await send(outbound)

    forwarder = asyncio.create_task(forward())
    try:
        await _receive_loop(
            websocket,
            service=service,
            tenant=tenant,
            session_id=session_id,
            bus=bus,
            connection_id=connection_id,
            send=send,
            forwarder=forwarder,
        )
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await forwarder


async def _receive_loop(
    websocket: WebSocket,
    *,
    service: Any,
    tenant: TenantContext,
    session_id: str,
    bus: CoordinationLiveBus,
    connection_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    forwarder: asyncio.Task[None],
) -> None:
    received_at: deque[float] = deque()
    while True:
        receive = asyncio.ensure_future(websocket.receive_json())
        done, _pending = await asyncio.wait(
            {receive, forwarder}, return_when=asyncio.FIRST_COMPLETED
        )
        if forwarder in done:
            receive.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receive
            # The live subscription died (e.g. Redis dropped): surface it.
            forwarder.result()
            raise LiveDeliveryUnavailableError("live subscription ended")
        payload = receive.result()
        message_type = str(payload.get("type", "message"))
        if message_type == "close":
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return
        if message_type == "ping":
            await send({"type": "pong"})
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
            await send({"type": "error", "code": "idempotency_key_required"})
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
        public = stored.model_dump(mode="json")
        await send({"type": "ack", "message": public})
        # Persisted first, then fanned out: every other participant (any replica)
        # receives it live; receivers de-duplicate by message_id.
        await bus.publish(
            str(tenant.tenant_id),
            session_id,
            {"type": "message", "message": public, "origin": connection_id},
        )


__all__ = ["router"]
