"""Tests for /memory API endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router as memory_router
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mem", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_CTX2 = TenantContext(tenant_id="tid-mem-2", plan=PlanTier.PROFESSIONAL, api_key_id="kid-2")
_VALID_KEY = "av_test_memkey"
_VALID_KEY2 = "av_test_memkey2"


def _make_app(
    ltm: LongTermMemoryStore | None = None,
    exec_mem: ExecutionMemory | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _VALID_KEY:
            return _CTX
        if key == _VALID_KEY2:
            return _CTX2
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(memory_router)
    app.state.long_term_memory = ltm if ltm is not None else LongTermMemoryStore()
    app.state.exec_memory = exec_mem if exec_mem is not None else ExecutionMemory()
    return app


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------

def test_list_long_term_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/long-term")
    assert resp.status_code == 401


def test_list_execution_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/execution")
    assert resp.status_code == 401


def test_delete_memory_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/memory/long-term/some-id")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

def test_list_long_term_returns_empty_initially() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_execution_returns_empty_initially() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/memory/execution", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_list_long_term_returns_stored_memories() -> None:
    store = LongTermMemoryStore()
    mem = LongTermMemory(
        content="Python prefers explicit imports.",
        source_goal_id="goal-1",
        memory_type="domain_fact",
        confidence=0.9,
        tags=["python"],
    )
    store.store(memory=mem, tenant_ctx=_CTX)

    client = TestClient(_make_app(ltm=store), raise_server_exceptions=False)
    resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["content"] == "Python prefers explicit imports."
    assert body[0]["memory_type"] == "domain_fact"
    assert body[0]["confidence"] == 0.9
    assert "memory_id" in body[0]


def test_delete_specific_memory_returns_204() -> None:
    store = LongTermMemoryStore()
    mem = LongTermMemory(
        content="Use async everywhere.",
        source_goal_id="goal-2",
        memory_type="tool_preference",
    )
    store.store(memory=mem, tenant_ctx=_CTX)

    client = TestClient(_make_app(ltm=store), raise_server_exceptions=False)
    resp = client.delete(
        f"/memory/long-term/{mem.memory_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204

    # Confirm it's gone
    list_resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json() == []


def test_delete_unknown_memory_returns_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/memory/long-term/nonexistent-id",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_clear_all_memories() -> None:
    store = LongTermMemoryStore()
    for i in range(3):
        store.store(
            memory=LongTermMemory(
                content=f"Memory {i}",
                source_goal_id=f"goal-{i}",
                memory_type="domain_fact",
            ),
            tenant_ctx=_CTX,
        )

    client = TestClient(_make_app(ltm=store), raise_server_exceptions=False)
    resp = client.delete("/memory", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204

    list_resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json() == []


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_memory_is_tenant_isolated() -> None:
    """Memories stored for tenant 1 are not visible to tenant 2."""
    store = LongTermMemoryStore()
    store.store(
        memory=LongTermMemory(
            content="Tenant 1 secret fact.",
            source_goal_id="g1",
            memory_type="domain_fact",
        ),
        tenant_ctx=_CTX,
    )

    client = TestClient(_make_app(ltm=store), raise_server_exceptions=False)
    # Tenant 2 should see empty list
    resp = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY2})
    assert resp.status_code == 200
    assert resp.json() == []

    # Tenant 1 should see their memory
    resp1 = client.get("/memory/long-term", headers={"X-API-Key": _VALID_KEY})
    assert len(resp1.json()) == 1
