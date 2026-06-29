"""Comprehensive tests for app/collab API — supplements test_collab.py."""
from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.collab import router as collab_router
from app.collab.store import CollaborationStore, VersionConflictError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_T_A = TenantContext(tenant_id="ct-a", plan=PlanTier.PROFESSIONAL, api_key_id="key-cta")
_T_B = TenantContext(tenant_id="ct-b", plan=PlanTier.FREE, api_key_id="key-ctb")
_KEY_A = "key-cta"
_KEY_B = "key-ctb"
_HEADERS_A = {"X-API-Key": _KEY_A}
_HEADERS_B = {"X-API-Key": _KEY_B}


class FakeCollabStore:
    """Minimal fake for API-level tests."""

    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self.operations: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def list_sessions(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [s for (tid, _), s in self.sessions.items() if tid == tenant_ctx.tenant_id]

    async def create_session(self, *, tenant_ctx: TenantContext, name: str, mode: str,
                              participants: list[str], goal_id: str | None = None,
                              agent_id: str | None = None, content: str = "") -> dict[str, Any]:
        sid = f"sid-{len(self.sessions) + 1}"
        rec = {"session_id": sid, "tenant_id": tenant_ctx.tenant_id, "name": name,
               "mode": mode, "participants": participants, "participant_count": len(participants),
               "goal_id": goal_id, "agent_id": agent_id, "status": "active", "content": content,
               "created_at": "2026-01-01T00:00:00+00:00",
               "updated_at": "2026-01-01T00:00:00+00:00"}
        self.sessions[(tenant_ctx.tenant_id, sid)] = rec
        self.operations[(tenant_ctx.tenant_id, sid)] = []
        return dict(rec)

    async def get_session(self, *, tenant_ctx: TenantContext, session_id: str) -> dict[str, Any] | None:
        s = self.sessions.get((tenant_ctx.tenant_id, session_id))
        return dict(s) if s else None

    async def close_session(self, *, tenant_ctx: TenantContext, session_id: str) -> dict[str, Any] | None:
        s = self.sessions.get((tenant_ctx.tenant_id, session_id))
        if s is None:
            return None
        s["status"] = "closed"
        return dict(s)

    async def append_operation(self, *, tenant_ctx: TenantContext, session_id: str,
                                operation: dict[str, Any], author: str,
                                expected_version: int | None = None) -> dict[str, Any]:
        key = (tenant_ctx.tenant_id, session_id)
        if key not in self.sessions:
            raise KeyError(session_id)
        ops = self.operations[key]
        if expected_version is not None and len(ops) != expected_version:
            raise VersionConflictError(
                f"conflict", current_version=len(ops), expected_version=expected_version
            )
        op = {"operation_id": f"op-{len(ops)+1}", "session_id": session_id,
              "tenant_id": tenant_ctx.tenant_id, "version": len(ops) + 1,
              "operation": operation, "author": author,
              "created_at": "2026-01-01T00:00:00+00:00"}
        ops.append(op)
        if operation.get("type") == "content_update":
            self.sessions[key]["content"] = str(operation.get("content", ""))
        return dict(op)

    async def list_operations(self, *, tenant_ctx: TenantContext, session_id: str) -> list[dict[str, Any]]:
        return list(self.operations.get((tenant_ctx.tenant_id, session_id), []))


def _make_app(store: FakeCollabStore | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _KEY_A:
            return _T_A
        if key == _KEY_B:
            return _T_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(collab_router)
    app.state.collab_store = store or FakeCollabStore()
    app.state._tenant_key_resolver = _resolve
    return app


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_list_sessions_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions")
    assert resp.status_code == 401


def test_create_session_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.post("/collab/sessions", json={"name": "x"})
    assert resp.status_code == 401


def test_get_session_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions/s1")
    assert resp.status_code == 401


# ── List sessions ─────────────────────────────────────────────────────────────

def test_list_sessions_empty() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_returns_only_own_tenant() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    client.post("/collab/sessions", json={"name": "A1"}, headers=_HEADERS_A)
    client.post("/collab/sessions", json={"name": "B1"}, headers=_HEADERS_B)
    resp = client.get("/collab/sessions", headers=_HEADERS_A)
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "A1"


# ── Create session ────────────────────────────────────────────────────────────

def test_create_session_returns_201() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/collab/sessions",
        json={"name": "New Session", "mode": "review", "participants": ["user1"]},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "New Session"


def test_create_session_default_mode() -> None:
    client = TestClient(_make_app())
    resp = client.post("/collab/sessions", json={"name": "Defaults"}, headers=_HEADERS_A)
    assert resp.status_code == 201


def test_create_session_with_goal_and_agent() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/collab/sessions",
        json={"name": "Linked", "goal_id": "g1", "agent_id": "a1"},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["goal_id"] == "g1"
    assert data["agent_id"] == "a1"


# ── Get session ───────────────────────────────────────────────────────────────

def test_get_session_found() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    created = client.post("/collab/sessions", json={"name": "Get Me"}, headers=_HEADERS_A).json()
    resp = client.get(f"/collab/sessions/{created['session_id']}", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Get Me"


def test_get_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions/nonexistent", headers=_HEADERS_A)
    assert resp.status_code == 404


def test_get_session_tenant_isolation() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    created = client.post("/collab/sessions", json={"name": "A Only"}, headers=_HEADERS_A).json()
    resp = client.get(f"/collab/sessions/{created['session_id']}", headers=_HEADERS_B)
    assert resp.status_code == 404


# ── Close session ─────────────────────────────────────────────────────────────

def test_close_session_returns_closed() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    created = client.post("/collab/sessions", json={"name": "Close Me"}, headers=_HEADERS_A).json()
    resp = client.post(f"/collab/sessions/{created['session_id']}/close", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_close_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.post("/collab/sessions/ghost/close", headers=_HEADERS_A)
    assert resp.status_code == 404


def test_close_session_wrong_tenant_returns_404() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    created = client.post("/collab/sessions", json={"name": "A sess"}, headers=_HEADERS_A).json()
    resp = client.post(f"/collab/sessions/{created['session_id']}/close", headers=_HEADERS_B)
    assert resp.status_code == 404


# ── Operations ────────────────────────────────────────────────────────────────

def test_append_operation_returns_201() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Ops"}, headers=_HEADERS_A).json()
    resp = client.post(
        f"/collab/sessions/{sess['session_id']}/operations",
        json={"type": "insert", "text": "hello"},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == 1


def test_append_operation_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/collab/sessions/ghost/operations",
        json={"type": "insert"},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 404


def test_append_operation_version_conflict_returns_409() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Conflict"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]
    # Append one op to advance version to 1
    client.post(f"/collab/sessions/{sid}/operations",
                json={"type": "insert"}, headers=_HEADERS_A)
    # Now try with stale expected_version=0
    resp = client.post(
        f"/collab/sessions/{sid}/operations",
        json={"type": "insert", "expected_version": 0},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert "current_version" in detail
    assert "expected_version" in detail
    assert "hint" in detail


def test_list_operations_empty() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Empty Ops"}, headers=_HEADERS_A).json()
    resp = client.get(f"/collab/sessions/{sess['session_id']}/operations", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_operations_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions/ghost/operations", headers=_HEADERS_A)
    assert resp.status_code == 404


def test_list_operations_includes_version() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Versioned"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]
    for i in range(3):
        client.post(f"/collab/sessions/{sid}/operations",
                    json={"type": f"op{i}"}, headers=_HEADERS_A)
    ops = client.get(f"/collab/sessions/{sid}/operations", headers=_HEADERS_A).json()
    assert len(ops) == 3
    assert [op["version"] for op in ops] == [1, 2, 3]


# ── OperationRequest.to_operation ─────────────────────────────────────────────

def test_operation_request_to_operation_all_fields() -> None:
    from app.api.collab import OperationRequest

    req = OperationRequest(
        type="insert",
        author="alice",
        content="new text",
        position=5,
        text="raw text",
        metadata={"extra": "data"},
    )
    op = req.to_operation()
    assert op["type"] == "insert"
    assert op["content"] == "new text"
    assert op["position"] == 5
    assert op["text"] == "raw text"
    assert op["extra"] == "data"


def test_operation_request_to_operation_minimal() -> None:
    from app.api.collab import OperationRequest

    req = OperationRequest(type="delete")
    op = req.to_operation()
    assert op["type"] == "delete"
    assert "content" not in op
    assert "position" not in op
    assert "text" not in op


# ── Rounds ────────────────────────────────────────────────────────────────────

def test_append_round_returns_201() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Rounds"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]
    resp = client.post(
        f"/collab/sessions/{sid}/rounds",
        json={"agent_id": "agent-x", "round_type": "propose", "content": "My proposal"},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 201


def test_append_round_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/collab/sessions/ghost/rounds",
        json={"agent_id": "a", "round_type": "propose", "content": "p"},
        headers=_HEADERS_A,
    )
    assert resp.status_code == 404


# ── Consensus ─────────────────────────────────────────────────────────────────

def test_get_consensus_no_rounds() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "No Rounds"}, headers=_HEADERS_A).json()
    resp = client.get(f"/collab/sessions/{sess['session_id']}/consensus", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json()["agreed"] is False


def test_get_consensus_with_agreement() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Agree"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]
    client.post(f"/collab/sessions/{sid}/rounds",
                json={"agent_id": "a", "round_type": "propose", "content": "Use Redis"},
                headers=_HEADERS_A)
    client.post(f"/collab/sessions/{sid}/rounds",
                json={"agent_id": "b", "round_type": "agree", "content": "ok"},
                headers=_HEADERS_A)
    resp = client.get(f"/collab/sessions/{sid}/consensus", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json()["agreed"] is True


def test_get_consensus_with_disagreement() -> None:
    store = FakeCollabStore()
    client = TestClient(_make_app(store))
    sess = client.post("/collab/sessions", json={"name": "Disagree"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]
    client.post(f"/collab/sessions/{sid}/rounds",
                json={"agent_id": "a", "round_type": "agree", "content": "ok"},
                headers=_HEADERS_A)
    client.post(f"/collab/sessions/{sid}/rounds",
                json={"agent_id": "b", "round_type": "disagree", "content": "nope"},
                headers=_HEADERS_A)
    resp = client.get(f"/collab/sessions/{sid}/consensus", headers=_HEADERS_A)
    assert resp.status_code == 200
    assert resp.json()["agreed"] is False
    assert resp.json()["dissenter"] == "b"


def test_get_consensus_session_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.get("/collab/sessions/ghost/consensus", headers=_HEADERS_A)
    assert resp.status_code == 404


# ── Delegation ────────────────────────────────────────────────────────────────

def test_delegate_task_calls_goal_service() -> None:
    store = FakeCollabStore()
    app = _make_app(store)
    mock_goal_svc = MagicMock()
    mock_goal_svc.submit_goal = AsyncMock(return_value={"goal_id": "delegated-goal-1"})
    app.state.goal_service = mock_goal_svc

    client = TestClient(app)
    sess = client.post("/collab/sessions", json={"name": "Delegate"}, headers=_HEADERS_A).json()
    sid = sess["session_id"]

    resp = client.post(
        f"/collab/sessions/{sid}/delegate",
        json={
            "from_agent_id": "agent-a",
            "to_agent_id": "agent-b",
            "sub_task": "Analyze the report",
            "context": {"key": "value"},
        },
        headers=_HEADERS_A,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["delegated_goal_id"] == "delegated-goal-1"
    assert data["from_agent_id"] == "agent-a"
    assert data["to_agent_id"] == "agent-b"


# ── _CollabPubSub ─────────────────────────────────────────────────────────────

async def test_pub_sub_publish_noop_without_redis_url() -> None:
    from app.api.collab import _CollabPubSub

    ps = _CollabPubSub()
    # No redis_url configured — should not raise
    await ps.publish("session-x", {"type": "test"})


async def test_pub_sub_track_join_noop_without_redis_url() -> None:
    from app.api.collab import _CollabPubSub

    ps = _CollabPubSub()
    await ps.track_join("session-x")


async def test_pub_sub_track_leave_noop_without_redis_url() -> None:
    from app.api.collab import _CollabPubSub

    ps = _CollabPubSub()
    await ps.track_leave("session-x")


async def test_pub_sub_get_participant_count_local_fallback() -> None:
    from app.api.collab import _CollabPubSub, _ws_connections

    ps = _CollabPubSub()
    count = await ps.get_participant_count("no-such-session")
    assert count == 0
