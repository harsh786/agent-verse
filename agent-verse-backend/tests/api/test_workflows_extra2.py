"""Extra coverage for /workflows API — pushes workflows.py from 67.4% → 85%+.

Covers:
  - _require_tenant raise (line 69)
  - _iso with non-datetime values (line 89)
  - _orm_to_dict helper (line 104)
  - _WorkflowStore DB-backed branches (lines 133, 139, 145, 157, 182, 199)
  - _list_db, _get_db, _create_db, _update_db, _delete_db (lines 209-334)
  - run_workflow with goal_service active (lines 460-476)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.workflows import (
    _WorkflowStore,
    _orm_to_dict,
    _require_tenant,
    _workflow_to_out,
    router as workflows_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-wf-ex2", plan=PlanTier.PROFESSIONAL, api_key_id="kx2")
_VALID_KEY = "av_test_workflows_ex2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_wf(**overrides: Any) -> SimpleNamespace:
    """Return a SimpleNamespace that mimics a Workflow ORM row."""
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "tenant_id": "tid-wf-ex2",
        "name": "Test Workflow",
        "description": "A test workflow",
        "definition": {"nodes": [{"id": "n1"}], "edges": []},
        "status": "draft",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _MockSession:
    """Minimal async SQLAlchemy session mock."""

    def __init__(self, *, one: Any = None, many: list = None) -> None:
        self._one = one
        self._many = many or []
        self.added: list = []
        self.deleted: list = []

    async def execute(self, *args: Any, **kwargs: Any) -> "_MockSession":
        return self

    def scalars(self) -> "_MockSession":
        return self

    def all(self) -> list:
        return self._many

    def scalar_one_or_none(self) -> Any:
        return self._one

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    def begin(self) -> Any:
        @asynccontextmanager
        async def _txn() -> Any:
            yield self

        return _txn()

    async def __aenter__(self) -> "_MockSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _make_db_factory(*, one: Any = None, many: list = None) -> Any:
    @asynccontextmanager
    async def _factory() -> Any:
        yield _MockSession(one=one, many=many)

    return _factory


def _make_app(
    store: _WorkflowStore | None = None,
    goal_service: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = store or _WorkflowStore()
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


def _create_workflow(client: TestClient, name: str = "Test WF") -> dict:
    resp = client.post(
        "/workflows",
        json={"name": name, "description": "desc", "definition": {"nodes": [{"id": "n1"}], "edges": []}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


def test_require_tenant_raises_401() -> None:
    """_require_tenant raises HTTPException(401) when state.tenant is None (line 69)."""
    request = MagicMock()
    request.state.tenant = None

    with pytest.raises(HTTPException) as exc_info:
        _require_tenant(request)
    assert exc_info.value.status_code == 401


def test_iso_with_datetime() -> None:
    """_workflow_to_out handles datetime created_at (covers _iso datetime branch)."""
    now = datetime.now(UTC)
    wf_dict = {
        "id": "wf-1", "name": "Test", "description": "desc",
        "definition": {}, "status": "draft", "version": 1,
        "created_at": now, "updated_at": now,
    }
    out = _workflow_to_out(wf_dict)
    assert "T" in out.created_at  # ISO 8601


def test_iso_with_string() -> None:
    """_workflow_to_out handles string created_at (covers _iso str branch, line 89)."""
    wf_dict = {
        "id": "wf-2", "name": "Test", "description": "",
        "definition": {}, "status": "draft", "version": 1,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    out = _workflow_to_out(wf_dict)
    assert out.created_at == "2024-01-01T00:00:00"


def test_iso_with_none() -> None:
    """_workflow_to_out handles None created_at (covers _iso None branch, line 89)."""
    wf_dict = {
        "id": "wf-3", "name": "Test", "description": "",
        "definition": {}, "status": "draft", "version": 1,
        "created_at": None, "updated_at": None,
    }
    out = _workflow_to_out(wf_dict)
    assert isinstance(out.created_at, str)
    assert "T" in out.created_at  # falls back to datetime.now


def test_orm_to_dict_converts_workflow() -> None:
    """_orm_to_dict maps Workflow ORM fields to dict (line 104)."""
    now = datetime.now(UTC)
    fake = _fake_wf(id="wf-1", name="My Workflow", version=3, created_at=now, updated_at=now)
    result = _orm_to_dict(fake)
    assert result["id"] == "wf-1"
    assert result["name"] == "My Workflow"
    assert result["version"] == 3
    assert result["status"] == "draft"
    assert result["definition"] == {"nodes": [{"id": "n1"}], "edges": []}


# ---------------------------------------------------------------------------
# _WorkflowStore DB-backed: list (lines 133, 209-223)
# ---------------------------------------------------------------------------


async def test_workflow_store_list_db_backed() -> None:
    """_WorkflowStore.list() with _db set calls _list_db (lines 133, 209-223)."""
    fake = _fake_wf(name="DB Workflow")
    store = _WorkflowStore()
    store._db = _make_db_factory(many=[fake])

    results = await store.list("tid-wf-ex2")
    assert len(results) == 1
    assert results[0]["name"] == "DB Workflow"


async def test_workflow_store_list_db_empty() -> None:
    """_WorkflowStore.list() with _db set returns empty list when no rows."""
    store = _WorkflowStore()
    store._db = _make_db_factory(many=[])

    results = await store.list("tid-wf-ex2")
    assert results == []


# ---------------------------------------------------------------------------
# _WorkflowStore DB-backed: get (lines 139, 228-244)
# ---------------------------------------------------------------------------


async def test_workflow_store_get_db_found() -> None:
    """_WorkflowStore.get() with _db and row found (lines 139, 228-244)."""
    fake = _fake_wf(id="wf-abc", name="Found Workflow")
    store = _WorkflowStore()
    store._db = _make_db_factory(one=fake)

    result = await store.get("tid-wf-ex2", "wf-abc")
    assert result is not None
    assert result["name"] == "Found Workflow"
    assert result["id"] == fake.id


async def test_workflow_store_get_db_not_found() -> None:
    """_WorkflowStore.get() with _db, no row → returns None (lines 139, 228-244)."""
    store = _WorkflowStore()
    store._db = _make_db_factory(one=None)

    result = await store.get("tid-wf-ex2", "nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# _WorkflowStore DB-backed: create (lines 145, 253-276)
# ---------------------------------------------------------------------------


async def test_workflow_store_create_db_backed() -> None:
    """_WorkflowStore.create() with _db creates Workflow + returns dict (lines 145, 253-276)."""
    store = _WorkflowStore()
    store._db = _make_db_factory()

    result = await store.create(
        tenant_id="tid-wf-ex2",
        name="New DB Workflow",
        description="DB-created workflow",
        definition={"nodes": [{"id": "start"}], "edges": []},
    )
    assert result["name"] == "New DB Workflow"
    assert result["tenant_id"] == "tid-wf-ex2"
    assert result["status"] == "draft"
    assert result["version"] == 1


# ---------------------------------------------------------------------------
# _WorkflowStore DB-backed: update (lines 157, 286-311)
# ---------------------------------------------------------------------------


async def test_workflow_store_update_db_found() -> None:
    """_WorkflowStore.update() with _db, row found → updates and returns (lines 157, 286-311)."""
    fake = _fake_wf(id="wf-upd", name="Old Name", version=1)
    store = _WorkflowStore()
    store._db = _make_db_factory(one=fake)

    result = await store.update(
        tenant_id="tid-wf-ex2",
        workflow_id="wf-upd",
        name="Updated Name",
        description="Changed",
        definition={"nodes": [], "edges": []},
    )
    assert result is not None
    assert result["name"] == "Updated Name"
    assert result["version"] == 2  # bumped


async def test_workflow_store_update_db_not_found() -> None:
    """_WorkflowStore.update() with _db, no row → returns None (lines 157, 286-311)."""
    store = _WorkflowStore()
    store._db = _make_db_factory(one=None)

    result = await store.update(
        tenant_id="tid-wf-ex2",
        workflow_id="ghost-wf",
        name="X",
        description="",
        definition={},
    )
    assert result is None


# ---------------------------------------------------------------------------
# _WorkflowStore DB-backed: delete (lines 182/199, 314-334)
# ---------------------------------------------------------------------------


async def test_workflow_store_delete_db_found() -> None:
    """_WorkflowStore.delete() with _db, row found → deletes + True (lines 182/199, 314-334)."""
    fake = _fake_wf(id="wf-del")
    store = _WorkflowStore()
    store._db = _make_db_factory(one=fake)

    deleted = await store.delete("tid-wf-ex2", "wf-del")
    assert deleted is True


async def test_workflow_store_delete_db_not_found() -> None:
    """_WorkflowStore.delete() with _db, no row → False (lines 182/199, 314-334)."""
    store = _WorkflowStore()
    store._db = _make_db_factory(one=None)

    deleted = await store.delete("tid-wf-ex2", "nonexistent")
    assert deleted is False


# ---------------------------------------------------------------------------
# run_workflow with goal_service (lines 460-476)
# ---------------------------------------------------------------------------


def test_run_workflow_with_goal_service_active() -> None:
    """run_workflow submits goal via goal_service and returns run_id."""
    mock_goal_service = MagicMock()
    mock_goal_service.submit_goal = AsyncMock(
        return_value={"id": "goal-wf-789", "status": "planning"}
    )
    app = _make_app(goal_service=mock_goal_service)
    client = TestClient(app, raise_server_exceptions=False)

    # Create workflow
    wf = _create_workflow(client, "CI Pipeline")
    wf_id = wf["id"]

    # Run workflow (without dry_run, so it goes to goal_service)
    resp = client.post(
        f"/workflows/{wf_id}/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_id"] == wf_id
    assert body["run_id"] == "goal-wf-789"
    assert body["status"] == "planning"
    mock_goal_service.submit_goal.assert_awaited_once()


def test_run_workflow_dry_run_when_no_goal_service() -> None:
    """run_workflow returns dry_run status when no goal_service (line 452-458)."""
    app = _make_app(goal_service=None)  # no goal_service
    client = TestClient(app, raise_server_exceptions=False)

    wf = _create_workflow(client, "Dry Run WF")
    wf_id = wf["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "dry_run"
    assert body["workflow_id"] == wf_id


def test_run_workflow_with_nodes_count_in_goal() -> None:
    """run_workflow goal text includes node count (line 447)."""
    app = _make_app(goal_service=None)
    client = TestClient(app, raise_server_exceptions=False)

    wf = _create_workflow(client, "Multi-Node WF")
    wf_id = wf["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run?dry_run=true",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "Multi-Node WF" in body["goal"]
    assert "node" in body["goal"]


def test_run_workflow_goal_service_run_id_fallback() -> None:
    """run_workflow falls back to wf- prefix when goal has no id field (line 468-469)."""
    mock_goal_service = MagicMock()
    mock_goal_service.submit_goal = AsyncMock(
        return_value={"status": "planning"}  # no "id" field
    )
    app = _make_app(goal_service=mock_goal_service)
    client = TestClient(app, raise_server_exceptions=False)

    wf = _create_workflow(client, "No-ID WF")
    wf_id = wf["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"].startswith("wf-")
