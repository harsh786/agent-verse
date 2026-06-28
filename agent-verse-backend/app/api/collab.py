"""Collaboration endpoints for persisted human-agent sessions."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from binascii import Error as BinasciiError
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.collab.agent_collab import AgentCollabSession, CollabRound
from app.collab.store import CollaborationStore, VersionConflictError
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/collab", tags=["collaboration"])

_ws_connections: dict[str, list[WebSocket]] = {}

# Short unique ID for this process replica — used to skip re-broadcasting own messages.
_REPLICA_ID = uuid.uuid4().hex[:8]
_logger = get_logger(__name__)


class _CollabPubSub:
    """Redis pub/sub fanout for cross-replica WebSocket broadcast.

    When a message arrives on one replica it is published to the Redis channel
    ``collab:{session_id}``.  All replicas subscribe via a pattern subscription and
    forward received messages to their *local* WebSocket connections.

    Falls back gracefully to in-process-only mode when Redis is unavailable —
    the in-process broadcast path in ``collab_websocket`` is always active and
    sufficient for single-replica deployments.

    Participant counts are tracked in a Redis key
    ``collab:participants:{session_id}`` (INCR/DECR) so the count reflects
    connections across all replicas, not just the local process.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._redis_url: str | None = None

    def ensure_started(self, redis_url: str) -> None:
        """Lazily start the subscriber task on first WebSocket connection."""
        if not redis_url:
            return
        if self._task is not None and not self._task.done():
            return  # already running
        self._redis_url = redis_url
        self._task = asyncio.create_task(
            self._listener_loop(),
            name="collab_pubsub_listener",
        )
        _logger.info("collab_pubsub_starting", replica=_REPLICA_ID)

    async def publish(self, session_id: str, message: dict[str, Any]) -> None:
        """Publish a message to all replicas for the given session.

        No-op (silently) when Redis is unavailable — the local broadcast in
        ``collab_websocket`` already handles same-replica delivery.
        """
        if not self._redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            async with aioredis.from_url(self._redis_url, decode_responses=True) as r:
                payload = json.dumps(
                    {"rid": _REPLICA_ID, "sid": session_id, "msg": message}
                )
                await r.publish(f"collab:{session_id}", payload)
        except Exception as exc:
            _logger.debug("collab_pubsub_publish_skipped", error=str(exc))

    async def track_join(self, session_id: str) -> None:
        """Increment cross-replica participant counter in Redis."""
        if not self._redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            async with aioredis.from_url(self._redis_url, decode_responses=True) as r:
                key = f"collab:participants:{session_id}"
                await r.incr(key)
                await r.expire(key, 86400)  # 24 h safety-net TTL
        except Exception as exc:
            _logger.debug("collab_pubsub_track_join_skipped", error=str(exc))

    async def track_leave(self, session_id: str) -> None:
        """Decrement cross-replica participant counter in Redis."""
        if not self._redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            async with aioredis.from_url(self._redis_url, decode_responses=True) as r:
                key = f"collab:participants:{session_id}"
                count = await r.decr(key)
                if count <= 0:
                    await r.delete(key)
        except Exception as exc:
            _logger.debug("collab_pubsub_track_leave_skipped", error=str(exc))

    async def get_participant_count(self, session_id: str) -> int:
        """Return participant count, preferring Redis for cross-replica accuracy."""
        local_count = len(_ws_connections.get(session_id, []))
        if not self._redis_url:
            return local_count
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            async with aioredis.from_url(self._redis_url, decode_responses=True) as r:
                raw = await r.get(f"collab:participants:{session_id}")
                return int(raw) if raw is not None else local_count
        except Exception:
            return local_count

    async def _listener_loop(self) -> None:
        """Subscribe to ``collab:*`` and forward messages to local WS connections."""
        while True:
            try:
                import redis.asyncio as aioredis  # type: ignore[import]

                async with aioredis.from_url(self._redis_url, decode_responses=True) as r:
                    pubsub = r.pubsub()
                    await pubsub.psubscribe("collab:*")
                    _logger.info("collab_pubsub_subscribed", replica=_REPLICA_ID)

                    async for message in pubsub.listen():
                        if message["type"] != "pmessage":
                            continue
                        try:
                            data = json.loads(message["data"])
                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue

                        # Skip messages that originated from this replica —
                        # they were already broadcast locally in collab_websocket.
                        if data.get("rid") == _REPLICA_ID:
                            continue

                        session_id: str = data.get("sid", "")
                        msg: Any = data.get("msg")
                        if not session_id or msg is None:
                            continue

                        broadcast_text = json.dumps(msg)
                        dead: list[WebSocket] = []
                        for conn in list(_ws_connections.get(session_id, [])):
                            try:
                                await conn.send_text(broadcast_text)
                            except Exception:
                                dead.append(conn)
                        for conn in dead:
                            conns = _ws_connections.get(session_id, [])
                            if conn in conns:
                                conns.remove(conn)

            except asyncio.CancelledError:
                _logger.info("collab_pubsub_cancelled", replica=_REPLICA_ID)
                return
            except Exception as exc:
                _logger.warning("collab_pubsub_reconnecting", error=str(exc))
                await asyncio.sleep(5)


# Module-level singleton — initialized lazily on first WebSocket connection.
_pub_sub = _CollabPubSub()


class CreateSessionRequest(BaseModel):
    name: str = Field(default="Collaboration Session", min_length=1, max_length=200)
    mode: str = "suggest"
    participants: list[str] = Field(default_factory=list)
    goal_id: str | None = None
    agent_id: str | None = None
    content: str = ""


class OperationRequest(BaseModel):
    type: str = Field(min_length=1)
    author: str = "human"
    content: str | None = None
    position: int | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = None  # For optimistic concurrency

    def to_operation(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type, **self.metadata}
        if self.content is not None:
            payload["content"] = self.content
        if self.position is not None:
            payload["position"] = self.position
        if self.text is not None:
            payload["text"] = self.text
        return payload


class RoundRequest(BaseModel):
    agent_id: str
    round_type: str
    content: str


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _store(request: Request) -> CollaborationStore:
    store: CollaborationStore | None = getattr(request.app.state, "collab_store", None)
    if store is None:
        store = CollaborationStore()
        request.app.state.collab_store = store
    return store


async def _resolve_ws_tenant(websocket: WebSocket) -> TenantContext | None:
    api_key = websocket.headers.get("X-API-Key")
    protocol_header = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in [p.strip() for p in protocol_header.split(",") if p.strip()]:
        if protocol.startswith("av.v1."):
            encoded = protocol.removeprefix("av.v1.")
            padding = "=" * (-len(encoded) % 4)
            try:
                api_key = base64.urlsafe_b64decode(f"{encoded}{padding}").decode()
            except (BinasciiError, UnicodeDecodeError):
                return None
            break
    if not api_key:
        return None
    resolver = getattr(websocket.app.state, "_tenant_key_resolver", None)
    if resolver is None:
        svc = getattr(websocket.app.state, "tenant_service", None)
        if svc is None:
            return None
        return cast(TenantContext | None, await svc.resolve_api_key(api_key))
    return cast(TenantContext | None, await resolver(api_key))


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    return await _store(request).list_sessions(tenant_ctx=tenant_ctx)


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    return await _store(request).create_session(
        tenant_ctx=tenant_ctx,
        name=body.name,
        mode=body.mode,
        participants=body.participants,
        goal_id=body.goal_id,
        agent_id=body.agent_id,
        content=body.content,
    )


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    session = await _store(request).get_session(tenant_ctx=tenant_ctx, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/close")
async def close_session(request: Request, session_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    session = await _store(request).close_session(tenant_ctx=tenant_ctx, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/operations")
async def list_operations(request: Request, session_id: str) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    if await _store(request).get_session(tenant_ctx=tenant_ctx, session_id=session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await _store(request).list_operations(tenant_ctx=tenant_ctx, session_id=session_id)


@router.post("/sessions/{session_id}/operations", status_code=201)
async def append_operation(
    request: Request, session_id: str, body: OperationRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _store(request)
    try:
        return await store.append_operation(
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            operation=body.to_operation(),
            author=body.author,
            expected_version=body.expected_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except VersionConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "message": str(e),
                "current_version": e.current_version,
                "expected_version": e.expected_version,
                "hint": "Fetch the latest session state and retry with current_version as expected_version",
            },
        ) from e


@router.post("/sessions/{session_id}/rounds", status_code=201)
async def append_round(request: Request, session_id: str, body: RoundRequest) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    if await _store(request).get_session(tenant_ctx=tenant_ctx, session_id=session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    operation = {
        "type": "collab_round",
        "agent_id": body.agent_id,
        "round_type": body.round_type,
        "content": body.content,
    }
    return await _store(request).append_operation(
        tenant_ctx=tenant_ctx,
        session_id=session_id,
        operation=operation,
        author=body.agent_id,
    )


@router.get("/sessions/{session_id}/consensus")
async def get_consensus(request: Request, session_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    session = await _store(request).get_session(tenant_ctx=tenant_ctx, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    operations = await _store(request).list_operations(tenant_ctx=tenant_ctx, session_id=session_id)
    collab = AgentCollabSession(goal=session.get("name", "Collaboration"))
    for item in operations:
        op = item.get("operation", {})
        if op.get("type") == "collab_round":
            collab.add_round(
                CollabRound(
                    agent_id=str(op.get("agent_id", "agent")),
                    round_type=str(op.get("round_type", "propose")),
                    content=str(op.get("content", "")),
                )
            )
    result = collab.synthesize_consensus()
    return {"agreed": result.agreed, "summary": result.summary, "dissenter": result.dissenter}


@router.websocket("/sessions/{session_id}/ws")
async def collab_websocket(websocket: WebSocket, session_id: str) -> None:
    tenant_ctx = await _resolve_ws_tenant(websocket)
    if tenant_ctx is None:
        await websocket.close(code=4401)
        return

    store: CollaborationStore = getattr(websocket.app.state, "collab_store", CollaborationStore())
    websocket.app.state.collab_store = store
    if await store.get_session(tenant_ctx=tenant_ctx, session_id=session_id) is None:
        await websocket.close(code=4004)
        return

    # Lazily start Redis pub/sub listener for cross-replica fanout.
    redis_url: str = getattr(
        getattr(websocket.app.state, "settings", None), "redis_url", ""
    )
    _pub_sub.ensure_started(redis_url)

    await websocket.accept()
    _ws_connections.setdefault(session_id, []).append(websocket)
    await _pub_sub.track_join(session_id)

    # Broadcast presence_join to all other connections in this session
    participant_count = await _pub_sub.get_participant_count(session_id)
    presence_join = {
        "type": "presence_join",
        "tenant_id": tenant_ctx.tenant_id,
        "session_id": session_id,
        "participants": participant_count,
    }
    for other_ws in list(_ws_connections[session_id]):
        if other_ws is not websocket:
            try:
                await other_ws.send_json(presence_join)
            except Exception:
                pass

    try:
        while True:
            data = await websocket.receive_text()
            try:
                incoming = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "error": "Invalid JSON message"})
                )
                break
            if not isinstance(incoming, dict):
                await websocket.send_text(
                    json.dumps({"type": "error", "error": "Message must be a JSON object"})
                )
                break
            author = str(incoming.get("author") or incoming.get("sender") or "human")
            operation = await store.append_operation(
                tenant_ctx=tenant_ctx,
                session_id=session_id,
                operation=incoming,
                author=author,
            )

            broadcast_payload: dict[str, Any] = {"type": "operation", "operation": operation}
            broadcast = json.dumps(broadcast_payload)

            # Local same-replica broadcast (excludes sender)
            dead: list[WebSocket] = []
            for conn in _ws_connections.get(session_id, []):
                if conn is not websocket:
                    try:
                        await conn.send_text(broadcast)
                    except Exception:
                        dead.append(conn)
            for conn in dead:
                _ws_connections[session_id].remove(conn)

            # Cross-replica fanout via Redis pub/sub (no-op if Redis unavailable)
            await _pub_sub.publish(session_id, broadcast_payload)

            await websocket.send_text(json.dumps({"type": "ack", "operation": operation}))
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_connections.get(session_id, []):
            _ws_connections[session_id].remove(websocket)
        await _pub_sub.track_leave(session_id)
        # Broadcast presence_leave to remaining connections
        leave_count = await _pub_sub.get_participant_count(session_id)
        presence_leave = {
            "type": "presence_leave",
            "tenant_id": tenant_ctx.tenant_id,
            "session_id": session_id,
            "participants": leave_count,
        }
        for other_ws in list(_ws_connections.get(session_id, [])):
            try:
                await other_ws.send_json(presence_leave)
            except Exception:
                pass


class DelegationRequest(BaseModel):
    from_agent_id: str
    to_agent_id: str
    sub_task: str
    context: dict[str, Any] = {}


@router.post("/sessions/{session_id}/delegate", status_code=202)
async def delegate_task(
    request: Request, session_id: str, body: DelegationRequest
) -> dict[str, Any]:
    """Delegate a sub-task from one agent to another within a session."""
    tenant = _require_tenant(request)
    goal_svc = request.app.state.goal_service
    # Prefix to identify this as a delegated task
    goal_text = f"[Delegated from {body.from_agent_id}] {body.sub_task}"
    result = await goal_svc.submit_goal(
        goal=goal_text,
        priority="normal",
        dry_run=False,
        tenant_ctx=tenant,
        agent_id=body.to_agent_id,
    )
    return {
        "delegated_goal_id": result["goal_id"],
        "from_agent_id": body.from_agent_id,
        "to_agent_id": body.to_agent_id,
        "session_id": session_id,
        "sub_task": body.sub_task[:200],
    }
