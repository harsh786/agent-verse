"""a10-F236-01..06: agent snapshots, rollback, clone and store reads tell the truth.

* POST /agents/{id}/snapshot reported a snapshot whose INSERT failed (the helper
  logged a warning); loading snapshots returned [] on error, resetting version
  numbering (F236-01).
* rollback ignored update_async's False (agent gone) and said "rolled_back" (F236-02).
* get_async / list_async / count_async fell back to this replica's cache on a DB
  error (F236-03 / F236-05).
* clone checked the plan limit against the replica cache (F236-06).
* versions were ``len(existing) + 1`` — now allocated in the INSERT (F236-04,
  covered on real Postgres in tests/api/test_agent_snapshots_pg.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import agents as agents_api
from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.core.errors import PlatformError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import PLAN_LIMITS
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-agent-life", plan=PlanTier.FREE, api_key_id="k")
_KEY = "ak_test_agent_lifecycle"
H = {"X-API-Key": _KEY}


def _client(store: Any) -> TestClient:
    from fastapi.responses import JSONResponse

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    @app.exception_handler(PlatformError)
    async def _platform(_: Any, exc: PlatformError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    app.state.agent_store = store
    return TestClient(app, raise_server_exceptions=False)


class _BrokenDb:
    def __call__(self) -> _BrokenDb:
        return self

    async def __aenter__(self) -> Any:
        raise ConnectionError("postgres://u:pw@db refused")

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _db_store_with_agent(agent_id: str = "a1") -> AgentStore:
    """A DB-configured store whose agent reads succeed and whose DB is broken."""
    store = AgentStore(db_session_factory=_BrokenDb())
    record = {"agent_id": agent_id, "tenant_id": _CTX.tenant_id, "name": "Agent"}
    store.get_async = AsyncMock(return_value=record)  # type: ignore[method-assign]
    return store


def test_snapshot_whose_insert_fails_is_503_not_a_phantom_snapshot() -> None:
    r = _client(_db_store_with_agent()).post("/agents/a1/snapshot", headers=H)
    assert r.status_code == 503
    assert "pw@db" not in r.text


def test_versions_read_failure_is_503_not_empty() -> None:
    r = _client(_db_store_with_agent()).get("/agents/a1/versions", headers=H)
    assert r.status_code == 503


def test_rollback_read_failure_is_503() -> None:
    r = _client(_db_store_with_agent()).post("/agents/a1/rollback/s1", headers=H)
    assert r.status_code == 503


def test_rollback_of_a_deleted_agent_is_404_not_rolled_back() -> None:
    store = AgentStore()
    client = _client(store)
    created = client.post("/agents", json={"name": "Rolly", "goal_template": "do"}, headers=H)
    aid = created.json()["agent_id"]
    snap = client.post(f"/agents/{aid}/snapshot", headers=H).json()
    assert client.delete(f"/agents/{aid}", headers=H).status_code == 204
    r = client.post(f"/agents/{aid}/rollback/{snap['snapshot_id']}", headers=H)
    assert r.status_code == 404
    agents_api._AGENT_SNAPSHOTS.clear()


def test_in_memory_snapshot_versions_count_up() -> None:
    store = AgentStore()
    client = _client(store)
    aid = client.post("/agents", json={"name": "V", "goal_template": "do"}, headers=H).json()[
        "agent_id"
    ]
    versions = [client.post(f"/agents/{aid}/snapshot", headers=H).json()["version"] for _ in "ab"]
    assert versions == [1, 2]
    agents_api._AGENT_SNAPSHOTS.clear()


@pytest.mark.parametrize("method", ["get_async", "list_async", "count_async"])
async def test_store_reads_do_not_serve_the_replica_cache_on_db_error(method: str) -> None:
    store = AgentStore(db_session_factory=_BrokenDb())
    store._data[(_CTX.tenant_id, "cached")] = {"agent_id": "cached", "name": "Stale"}
    call = getattr(store, method)
    args = ("cached",) if method == "get_async" else ()
    with pytest.raises(HTTPException) as exc:
        await call(*args, tenant_ctx=_CTX)
    assert exc.value.status_code == 503


def test_get_agent_with_db_down_is_503_not_cached_copy() -> None:
    store = AgentStore(db_session_factory=_BrokenDb())
    store._data[(_CTX.tenant_id, "cached")] = {"agent_id": "cached", "name": "Stale"}
    assert _client(store).get("/agents/cached", headers=H).status_code == 503


def test_clone_checks_the_durable_count_not_the_replica_cache() -> None:
    store = AgentStore()
    client = _client(store)
    aid = client.post("/agents", json={"name": "C", "goal_template": "do"}, headers=H).json()[
        "agent_id"
    ]
    # Other replicas created agents up to the limit: this cache holds only one.
    store.count_async = AsyncMock(  # type: ignore[method-assign]
        return_value=PLAN_LIMITS[_CTX.plan].max_agents
    )
    r = client.post(f"/agents/{aid}/clone", headers=H)
    assert r.status_code in (402, 403, 429)
    assert len(store.list_all(tenant_ctx=_CTX)) == 1
