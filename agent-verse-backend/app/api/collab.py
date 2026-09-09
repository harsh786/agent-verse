"""Collaboration endpoints for persisted human-agent sessions."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging as _logging
import secrets
import time
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

# ---------------------------------------------------------------------------
# Short-lived CRDT token store
# ---------------------------------------------------------------------------

_CRDT_TOKEN_TTL = 3600  # seconds (1 hour)
# Map: token → { tenant_id, expires_at }
_crdt_tokens: dict[str, dict[str, Any]] = {}


def _cleanup_expired_crdt_tokens() -> None:
    """Evict tokens past their TTL from the in-memory store."""
    now = time.monotonic()
    expired = [k for k, v in _crdt_tokens.items() if v["expires_at"] < now]
    for k in expired:
        del _crdt_tokens[k]


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
                payload = json.dumps({"rid": _REPLICA_ID, "sid": session_id, "msg": message})
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
                "hint": (
                    "Fetch the latest session state and retry "
                    "with current_version as expected_version"
                ),
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
    redis_url: str = getattr(getattr(websocket.app.state, "settings", None), "redis_url", "")
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
            with contextlib.suppress(Exception):
                await other_ws.send_json(presence_join)

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
            with contextlib.suppress(Exception):
                await other_ws.send_json(presence_leave)


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


# ---------------------------------------------------------------------------
# Session insights — AI-powered post-session analysis
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/insights")
async def get_session_insights(request: Request, session_id: str) -> dict[str, Any]:
    """Analyse a completed collaboration session and extract:
    - Key decisions made
    - Action items identified
    - Open questions remaining
    - Sentiment / agreement level
    Returns structured JSON, optionally LLM-powered.
    """
    tenant = _require_tenant(request)
    store = _store(request)

    session = await store.get_session(tenant_ctx=tenant, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    operations = await store.list_operations(tenant_ctx=tenant, session_id=session_id)
    rounds = getattr(session, "rounds", []) or []

    # Collect all textual content from operations
    content_pieces: list[str] = []
    for op in operations:
        raw_op = op if isinstance(op, dict) else {}
        op_data = raw_op.get("operation", {}) or {}
        if op_data.get("content"):
            author = raw_op.get("author", "unknown")
            content_pieces.append(f"[{author}]: {str(op_data['content'])[:500]}")

    for r in rounds:
        if isinstance(r, dict) and r.get("content"):
            content_pieces.append(f"[{r.get('round_type', 'round')}]: {str(r['content'])[:500]}")

    session_text = "\n".join(content_pieces[:50])  # cap at 50 entries
    session_name = (
        session.get("name", "Session")
        if isinstance(session, dict)
        else getattr(session, "name", "Session")
    )

    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None or not session_text.strip():
        # Fallback: rule-based extraction
        return {
            "session_id": session_id,
            "session_name": session_name,
            "key_decisions": [session_text[:200]] if session_text else [],
            "action_items": [],
            "open_questions": [],
            "agreement_level": "unknown",
            "sentiment": "neutral",
            "participant_count": len(operations),
            "llm_powered": False,
        }

    from app.providers.base import CompletionRequest, Message

    system_prompt = (
        "You are a meeting analyst. Analyse the following collaboration session transcript "
        "and extract structured insights. Respond with valid JSON only:\n"
        '{"key_decisions": ["..."], "action_items": ["..."], "open_questions": ["..."], '
        '"agreement_level": "high|medium|low|none", "sentiment": "positive|neutral|negative", '
        '"summary": "..."}'
    )
    user_msg = f"Session: {session_name}\n\nTranscript:\n{session_text}"
    try:
        resp = await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_msg),
                ],
                model="",
                max_tokens=800,
            )
        )
        raw = resp.content.strip().lstrip("```json").lstrip("```").rstrip("```")
        data = json.loads(raw)
    except Exception:
        data = {
            "key_decisions": [],
            "action_items": [],
            "open_questions": [],
            "agreement_level": "unknown",
            "sentiment": "neutral",
            "summary": "Insight extraction failed — LLM unavailable.",
        }

    return {
        "session_id": session_id,
        "session_name": session_name,
        "llm_powered": True,
        **data,
    }


# ---------------------------------------------------------------------------
# Yjs CRDT WebSocket sync endpoint
# ---------------------------------------------------------------------------

_crdt_log = _logging.getLogger("collab.crdt")


@router.post("/crdt-token", status_code=201)
async def generate_crdt_token(request: Request) -> dict[str, Any]:
    """Generate a short-lived token for CRDT WebSocket authentication.

    Clients should use this token as the ``?token=`` query parameter when
    connecting to the ``/collab/crdt/{room_id}`` WebSocket.  This avoids
    sending the long-lived API key in every WebSocket URL.
    """
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    _cleanup_expired_crdt_tokens()

    token = secrets.token_urlsafe(32)
    _crdt_tokens[token] = {
        "tenant_id": ctx.tenant_id,
        "expires_at": time.monotonic() + _CRDT_TOKEN_TTL,
    }

    return {"token": token, "expires_in": _CRDT_TOKEN_TTL}


class CRDTRoomManager:
    """Manages Yjs CRDT room connections.

    Uses Redis pub/sub when available for cross-process message delivery.
    Falls back to in-process dict when Redis is unavailable.
    """

    def __init__(self) -> None:
        from collections import defaultdict

        self._local_rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._redis: Any = None
        self._redis_available = False

    def set_redis(self, redis: Any) -> None:
        """Wire Redis client (called by app lifespan if Redis is configured)."""
        self._redis = redis
        self._redis_available = redis is not None

    async def join(self, room_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._local_rooms[room_id].add(websocket)

    async def leave(self, room_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._local_rooms[room_id].discard(websocket)
            if not self._local_rooms[room_id]:
                del self._local_rooms[room_id]

    async def save_snapshot(self, room_id: str, data: bytes) -> None:
        """Save full Yjs document snapshot to Redis for late-joining peers."""
        if self._redis is None:
            return
        try:
            key = f"crdt:snapshot:{room_id}"
            await self._redis.set(key, data, ex=86400)  # 24h TTL
        except Exception as exc:
            _crdt_log.debug("Failed to save CRDT snapshot: %s", exc)

    async def load_snapshot(self, room_id: str) -> bytes | None:
        """Load Yjs document snapshot for a new peer."""
        if self._redis is None:
            return None
        try:
            key = f"crdt:snapshot:{room_id}"
            data = await self._redis.get(key)
            return data if isinstance(data, bytes) else (data.encode() if data else None)
        except Exception:
            return None

    async def broadcast(self, room_id: str, data: bytes, sender: WebSocket) -> None:
        """Broadcast binary Yjs update to all peers in the room."""
        # 1. Publish to Redis (for other processes)
        if self._redis_available and self._redis is not None:
            try:
                channel = f"crdt:{room_id}"
                await self._redis.publish(channel, data)
            except Exception as exc:
                _crdt_log.debug("Redis publish failed: %s", exc)

        # 2. Deliver locally (this process)
        async with self._lock:
            peers = set(self._local_rooms.get(room_id, set()))

        dead: set[WebSocket] = set()
        for peer in peers:
            if peer is sender:
                continue
            try:
                await peer.send_bytes(data)
            except Exception:
                dead.add(peer)

        if dead:
            async with self._lock:
                self._local_rooms[room_id] -= dead

        # 3. Periodically save a best-effort snapshot (every ~50 updates)
        if self._redis is not None:
            try:
                counter_key = f"crdt:update_count:{room_id}"
                count = await self._redis.incr(counter_key)
                await self._redis.expire(counter_key, 3600)
                if count % 50 == 0:
                    await self.save_snapshot(room_id, data)
            except Exception:
                pass

    async def subscribe_redis(self, room_id: str, websocket: WebSocket) -> None:
        """Subscribe to Redis channel and forward messages to this WebSocket."""
        if not self._redis_available or self._redis is None:
            return
        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(f"crdt:{room_id}")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message["data"]
                if isinstance(data, str):
                    data = data.encode()
                try:
                    await websocket.send_bytes(data)
                except Exception:
                    break
        except Exception as exc:
            _crdt_log.debug("Redis subscribe loop ended: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(f"crdt:{room_id}")
                await pubsub.close()
            except Exception:
                pass


# Module-level singleton
_crdt_manager = CRDTRoomManager()


@router.websocket("/crdt/{room_id}")
async def yjs_crdt_sync(websocket: WebSocket, room_id: str) -> None:
    """Yjs CRDT WebSocket — binary message fan-out with Redis pub/sub.

    Authentication accepts either:
    - ``?token=<crdt_token>``  — short-lived token from POST /collab/crdt-token (preferred)
    - ``?api_key=<key>``       — long-lived API key (fallback for older clients)

    Supports multi-process deployments when Redis is configured.
    Falls back to in-process routing when Redis is unavailable.
    """
    crdt_token = websocket.query_params.get("token", "")
    api_key = websocket.query_params.get("api_key", "")

    tenant_id: str | None = None

    # 1. Try short-lived CRDT token first (preferred path)
    if crdt_token:
        _cleanup_expired_crdt_tokens()
        token_data = _crdt_tokens.get(crdt_token)
        if token_data and token_data["expires_at"] > time.monotonic():
            tenant_id = token_data["tenant_id"]
        else:
            await websocket.close(code=4401, reason="Invalid or expired CRDT token")
            return

    # 2. Fall back to API key validation
    elif api_key:
        try:
            app_state = websocket.app.state if hasattr(websocket, "app") else None
            tenant_service = getattr(app_state, "tenant_service", None) if app_state else None
            if tenant_service is None:
                await websocket.close(code=4401, reason="Unauthorized")
                return
            tenant = await tenant_service.resolve_api_key(api_key)
            if tenant is None:
                await websocket.close(code=4401, reason="Invalid API key")
                return
            tenant_id = tenant.tenant_id
        except Exception:
            await websocket.close(code=4401, reason="Auth error")
            return

    else:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    # 3. Verify the room belongs to this tenant
    # Room format: collab-{tenantId}-{sessionId}
    expected_prefix = f"collab-{tenant_id}-"
    if not room_id.startswith(expected_prefix):
        await websocket.close(code=4403, reason="Forbidden: room not owned by tenant")
        return

    await websocket.accept()

    # Wire Redis from app state if available
    try:
        redis = getattr(websocket.app.state if hasattr(websocket, "app") else None, "_redis", None)
        if redis is not None:
            _crdt_manager.set_redis(redis)
    except Exception:
        pass

    # Send existing document snapshot to new peer so they get full history.
    snapshot = await _crdt_manager.load_snapshot(room_id)
    if snapshot:
        with contextlib.suppress(Exception):
            await websocket.send_bytes(snapshot)

    await _crdt_manager.join(room_id, websocket)

    # Start Redis subscription task (no-op if Redis not available)
    redis_task = asyncio.create_task(_crdt_manager.subscribe_redis(room_id, websocket))

    _crdt_log.debug("crdt_client_joined room_id=%s", room_id)

    try:
        async for data in websocket.iter_bytes():
            await _crdt_manager.broadcast(room_id, data, websocket)
    except Exception:  # WebSocketDisconnect or network error
        pass
    finally:
        redis_task.cancel()
        await _crdt_manager.leave(room_id, websocket)
        _crdt_log.debug("crdt_client_left room_id=%s", room_id)
        import contextlib

        with contextlib.suppress(Exception):
            await websocket.close()
