"""Comprehensive tests for /memory API endpoints — targets 19% → 60%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router as memory_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-memory", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_memory_comp"


@pytest.fixture(autouse=True)
def _no_db_session(monkeypatch):
    """Prevent all tests from connecting to a real DB."""
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.api.memory.get_session_factory", lambda: None, raising=False)


def _make_app(ltm: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(memory_router)
    # Explicitly disable DB to prevent get_session_factory() calls
    app.state.db_session_factory = None
    if ltm:
        app.state.long_term_memory = ltm
    return app


def _make_memory_entry(memory_id: str = "mem-1") -> MagicMock:
    m = MagicMock()
    m.memory_id = memory_id
    m.content = "Agent should prefer batch operations"
    m.memory_type = "lesson"
    m.confidence = 0.85
    m.source_goal_id = "gid-1"
    m.tags = ["performance"]
    return m


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_memories_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory")
    assert resp.status_code == 401


def test_recall_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/recall?q=test")
    assert resp.status_code == 401


def test_clear_memories_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/memory")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# list_memories — no LTM / no DB
# ---------------------------------------------------------------------------

def test_list_memories_no_ltm_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_memories_with_type_filter() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory?memory_type=lesson", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_list_memories_limit_param() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory?limit=5", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_list_memories_limit_too_small() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory?limit=0", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_list_memories_limit_too_large() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory?limit=500", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# list_memories — with LTM in-memory
# ---------------------------------------------------------------------------

def test_list_memories_with_ltm() -> None:
    ltm = MagicMock()
    m = _make_memory_entry()
    m.tenant_id = _CTX.tenant_id
    ltm._memories = [m]
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.get("/memory", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------

def test_recall_no_ltm() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/recall?q=batch+operations", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "batch operations"
    assert body["results"] == []


def test_recall_missing_query() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/recall", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_recall_with_ltm() -> None:
    ltm = MagicMock()
    m = _make_memory_entry()
    m.content = "Batch operations are faster"
    m.confidence = 0.9
    m.memory_type = "lesson"
    m.source_goal_id = "gid-1"
    ltm.recall_async = AsyncMock(return_value=[m])
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.get("/memory/recall?q=batch", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["content"] == "Batch operations are faster"


def test_recall_limit_param() -> None:
    ltm = MagicMock()
    ltm.recall_async = AsyncMock(return_value=[])
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.get("/memory/recall?q=test&limit=10", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    ltm.recall_async.assert_called_once()
    assert ltm.recall_async.call_args.kwargs["top_k"] == 10


# ---------------------------------------------------------------------------
# long_term memories
# ---------------------------------------------------------------------------

def test_list_long_term_memories_no_ltm() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_long_term_memories_with_ltm() -> None:
    ltm = MagicMock()
    m = _make_memory_entry()
    ltm.list_all = MagicMock(return_value=[m])
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["memory_id"] == "mem-1"
    assert body[0]["content"] == "Agent should prefer batch operations"


# ---------------------------------------------------------------------------
# execution memories
# ---------------------------------------------------------------------------

def test_list_execution_memories_no_exec_mem() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/execution", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_execution_memories_with_exec_mem() -> None:
    app = _make_app()
    exec_mem = MagicMock()
    exec_mem._memories = {
        _CTX.tenant_id: [
            {"goal_text": "Deploy services", "success": True, "recorded_at": "2024-01-01"},
        ]
    }
    app.state.exec_memory = exec_mem
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/memory/execution", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["success"] is True


# ---------------------------------------------------------------------------
# delete memory by id
# ---------------------------------------------------------------------------

def test_delete_memory_no_db_no_ltm() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/memory/mem-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_delete_memory_with_ltm_found() -> None:
    ltm = MagicMock()
    ltm.delete = MagicMock(return_value=True)
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.delete("/memory/mem-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "mem-1"


def test_delete_memory_with_ltm_not_found() -> None:
    ltm = MagicMock()
    ltm.delete = MagicMock(return_value=False)
    client = TestClient(_make_app(ltm), raise_server_exceptions=False)
    resp = client.delete("/memory/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# delete long-term memory
# ---------------------------------------------------------------------------

def test_delete_long_term_memory_no_ltm() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/memory/long-term/mem-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_delete_long_term_memory_found() -> None:
    ltm = MagicMock()
    ltm.delete = MagicMock(return_value=True)
    app = _make_app()
    app.state.long_term_memory = ltm
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/memory/long-term/mem-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_delete_long_term_memory_not_found() -> None:
    ltm = MagicMock()
    ltm.delete = MagicMock(return_value=False)
    app = _make_app()
    app.state.long_term_memory = ltm
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/memory/long-term/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# clear all memories
# ---------------------------------------------------------------------------

def test_clear_all_memories_no_ltm() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/memory", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_clear_all_memories_with_ltm() -> None:
    ltm = MagicMock()
    m1 = _make_memory_entry("mem-1")
    m2 = _make_memory_entry("mem-2")
    ltm.list_all = MagicMock(return_value=[m1, m2])
    ltm.delete = MagicMock(return_value=True)
    app = _make_app(ltm)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/memory", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204
    assert ltm.delete.call_count == 2


# ---------------------------------------------------------------------------
# tool reliability
# ---------------------------------------------------------------------------

def test_get_tool_reliability() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/memory/tool-reliability", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
