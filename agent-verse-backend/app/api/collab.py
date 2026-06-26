"""Collaboration endpoints for persisted human-agent sessions."""
from __future__ import annotations

import base64
import json
from binascii import Error as BinasciiError
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.collab.agent_collab import AgentCollabSession, CollabRound
from app.collab.store import CollaborationStore
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/collab", tags=["collaboration"])

_ws_connections: dict[str, list[WebSocket]] = {}


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
    if await _store(request).get_session(tenant_ctx=tenant_ctx, session_id=session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await _store(request).append_operation(
        tenant_ctx=tenant_ctx,
        session_id=session_id,
        operation=body.to_operation(),
        author=body.author,
    )


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

    await websocket.accept()
    _ws_connections.setdefault(session_id, []).append(websocket)

    # Broadcast presence_join to all other connections in this session
    presence_join = {
        "type": "presence_join",
        "tenant_id": tenant_ctx.tenant_id,
        "session_id": session_id,
        "participants": len(_ws_connections[session_id]),
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

            broadcast = json.dumps({"type": "operation", "operation": operation})
            dead: list[WebSocket] = []
            for conn in _ws_connections.get(session_id, []):
                if conn is not websocket:
                    try:
                        await conn.send_text(broadcast)
                    except Exception:
                        dead.append(conn)
            for conn in dead:
                _ws_connections[session_id].remove(conn)

            await websocket.send_text(json.dumps({"type": "ack", "operation": operation}))
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_connections.get(session_id, []):
            _ws_connections[session_id].remove(websocket)
        # Broadcast presence_leave to remaining connections
        presence_leave = {
            "type": "presence_leave",
            "tenant_id": tenant_ctx.tenant_id,
            "session_id": session_id,
            "participants": len(_ws_connections.get(session_id, [])),
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
