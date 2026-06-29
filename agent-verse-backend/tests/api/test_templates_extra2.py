"""Extra coverage for /templates API — pushes templates.py from 64.6% → 85%+.

Covers:
  - _require_tenant raise (line 20)
  - _TemplateStore DB-backed paths (lines 75, 79, 94, 108, 119)
  - increment_use_count with DB (lines 128-139)
  - _list_db, _get_db, _create_db, _update_db, _delete_db (lines 145-223)
  - _orm_to_dict static method (line 227)
  - instantiate with submit=True, no goal_service (line 323-324)
  - instantiate with submit=True, goal_service available (lines 325-331)
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

import app.api.templates as tmpl_module
from app.api.templates import (
    _TemplateStore,
    _require_tenant,
    router as templates_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-tmpl-ex2", plan=PlanTier.PROFESSIONAL, api_key_id="kx2")
_VALID_KEY = "av_test_templates_ex2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_tmpl(**overrides: Any) -> SimpleNamespace:
    """Return a SimpleNamespace that mimics a GoalTemplate ORM row."""
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "tenant_id": "tid-tmpl-ex2",
        "name": "Test Template",
        "description": "Test description",
        "goal_text": "Deploy {{env}} to {{region}}",
        "domain": "devops",
        "parameters": [{"name": "env", "required": True}],
        "use_count": 0,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _MockSession:
    """Minimal async session mock for store DB-backed tests."""

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
        pass  # no-op — object keeps values set in constructor

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


def _make_app(goal_service: Any = None) -> tuple[FastAPI, _TemplateStore]:
    store = _TemplateStore()
    tmpl_module.template_store = store

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(templates_router)
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app, store


# ---------------------------------------------------------------------------
# _require_tenant unit test (line 20)
# ---------------------------------------------------------------------------


def test_require_tenant_raises_401_without_tenant() -> None:
    """_require_tenant raises HTTPException(401) when state.tenant is None."""
    request = MagicMock()
    request.state.tenant = None

    with pytest.raises(HTTPException) as exc_info:
        _require_tenant(request)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# _orm_to_dict unit test (line 227)
# ---------------------------------------------------------------------------


def test_orm_to_dict_with_datetime_fields() -> None:
    """_orm_to_dict converts ORM row to dict; datetime fields → isoformat."""
    now = datetime.now(UTC)
    fake = _fake_tmpl(created_at=now, updated_at=now)
    result = _TemplateStore._orm_to_dict(fake)
    assert result["id"] == fake.id
    assert result["name"] == "Test Template"
    assert result["version"] == 1
    assert "T" in result["created_at"]  # ISO 8601 contains T separator


def test_orm_to_dict_with_string_fields() -> None:
    """_orm_to_dict handles non-datetime created_at/updated_at (str fallback)."""
    fake = _fake_tmpl(created_at="2024-01-01", updated_at="2024-01-01")
    result = _TemplateStore._orm_to_dict(fake)
    assert result["created_at"] == "2024-01-01"
    assert result["updated_at"] == "2024-01-01"


# ---------------------------------------------------------------------------
# _TemplateStore DB-backed: list (lines 75, 145-155)
# ---------------------------------------------------------------------------


async def test_template_store_list_db_backed() -> None:
    """_TemplateStore.list() with _db set calls _list_db (lines 75, 145-155)."""
    fake = _fake_tmpl(name="DB Template")
    store = _TemplateStore()
    store._db = _make_db_factory(many=[fake])

    results = await store.list("tid-tmpl-ex2")
    assert len(results) == 1
    assert results[0]["name"] == "DB Template"


async def test_template_store_list_db_with_domain_filter() -> None:
    """_TemplateStore.list() passes domain to _list_db (lines 75, 145-155)."""
    fake = _fake_tmpl(name="DevOps Tmpl", domain="devops")
    store = _TemplateStore()
    store._db = _make_db_factory(many=[fake])

    results = await store.list("tid-tmpl-ex2", domain="devops")
    assert results[0]["domain"] == "devops"


# ---------------------------------------------------------------------------
# _TemplateStore DB-backed: get (lines 79, 158-167)
# ---------------------------------------------------------------------------


async def test_template_store_get_db_found() -> None:
    """_TemplateStore.get() with _db set and row found (lines 79, 158-167)."""
    fake = _fake_tmpl(id="tmpl-xyz")
    store = _TemplateStore()
    store._db = _make_db_factory(one=fake)

    result = await store.get("tid-tmpl-ex2", "tmpl-xyz")
    assert result is not None
    assert result["id"] == fake.id
    assert result["name"] == "Test Template"


async def test_template_store_get_db_not_found() -> None:
    """_TemplateStore.get() with _db set and no row (lines 79, 158-167)."""
    store = _TemplateStore()
    store._db = _make_db_factory(one=None)

    result = await store.get("tid-tmpl-ex2", "nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# _TemplateStore DB-backed: create (lines 94, 171-183)
# ---------------------------------------------------------------------------


async def test_template_store_create_db_backed() -> None:
    """_TemplateStore.create() with _db creates GoalTemplate + calls _orm_to_dict (lines 94, 171-183)."""
    store = _TemplateStore()
    store._db = _make_db_factory()  # No pre-seeded rows needed; create constructs its own obj

    result = await store.create(
        tenant_id="tid-tmpl-ex2",
        name="New DB Template",
        description="A description",
        goal_text="Do {{task}} in {{env}}",
        domain="ops",
        parameters=[{"name": "task", "required": True}],
    )
    assert result["name"] == "New DB Template"
    assert result["tenant_id"] == "tid-tmpl-ex2"
    assert result["domain"] == "ops"


# ---------------------------------------------------------------------------
# _TemplateStore DB-backed: update (lines 108, 187-207)
# ---------------------------------------------------------------------------


async def test_template_store_update_db_found() -> None:
    """_TemplateStore.update() with _db set and row found (lines 108, 187-207)."""
    fake = _fake_tmpl(id="tmpl-upd", name="Old Name", version=1)
    store = _TemplateStore()
    store._db = _make_db_factory(one=fake)

    result = await store.update(
        tenant_id="tid-tmpl-ex2",
        template_id="tmpl-upd",
        name="New Name",
        description="Updated",
        goal_text="Improved {{task}}",
        domain="devops",
        parameters=[],
    )
    assert result is not None
    assert result["name"] == "New Name"
    assert result["version"] == 2  # incremented


async def test_template_store_update_db_not_found() -> None:
    """_TemplateStore.update() with _db set, no row → returns None (lines 108, 187-207)."""
    store = _TemplateStore()
    store._db = _make_db_factory(one=None)

    result = await store.update(
        tenant_id="tid-tmpl-ex2",
        template_id="no-such-tmpl",
        name="X",
        description="",
        goal_text="Do X",
        domain="general",
        parameters=[],
    )
    assert result is None


# ---------------------------------------------------------------------------
# _TemplateStore DB-backed: delete (lines 119, 210-223)
# ---------------------------------------------------------------------------


async def test_template_store_delete_db_found() -> None:
    """_TemplateStore.delete() with _db, row found → deletes + returns True (lines 119, 210-223)."""
    fake = _fake_tmpl(id="tmpl-del")
    store = _TemplateStore()
    store._db = _make_db_factory(one=fake)

    deleted = await store.delete("tid-tmpl-ex2", "tmpl-del")
    assert deleted is True


async def test_template_store_delete_db_not_found() -> None:
    """_TemplateStore.delete() with _db, no row → returns False (lines 119, 210-223)."""
    store = _TemplateStore()
    store._db = _make_db_factory(one=None)

    deleted = await store.delete("tid-tmpl-ex2", "nonexistent")
    assert deleted is False


# ---------------------------------------------------------------------------
# increment_use_count with DB (lines 128-139)
# ---------------------------------------------------------------------------


async def test_increment_use_count_db_backed() -> None:
    """increment_use_count with _db calls DB execute (lines 128-139)."""
    store = _TemplateStore()
    store._db = _make_db_factory()  # DB execute is no-op in mock; passes silently

    # Should not raise
    await store.increment_use_count("tid-tmpl-ex2", "some-template-id")


async def test_increment_use_count_db_exception_silenced() -> None:
    """increment_use_count with _db swallows exceptions silently (line 137-138)."""
    store = _TemplateStore()

    # DB factory that raises on every execute
    class _ErrSession:
        async def execute(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("DB down")

        async def commit(self) -> None:
            pass

        async def __aenter__(self) -> "_ErrSession":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

    @asynccontextmanager
    async def _err_factory() -> Any:
        yield _ErrSession()

    store._db = _err_factory
    # Should silently swallow the error
    await store.increment_use_count("tid-tmpl-ex2", "some-id")


# ---------------------------------------------------------------------------
# instantiate endpoint with submit=True (lines 321-331)
# ---------------------------------------------------------------------------


def test_instantiate_submit_true_no_goal_service_returns_503() -> None:
    """instantiate with submit=True when goal_service is unavailable → 503."""
    app, store = _make_app(goal_service=None)
    client = TestClient(app, raise_server_exceptions=False)

    # Create a template with no required parameters
    resp = client.post(
        "/templates",
        json={"name": "Simple", "goal_text": "Deploy to staging", "parameters": []},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    tmpl_id = resp.json()["id"]

    # Instantiate with submit=True — goal_service not in app.state
    resp2 = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {}, "submit": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp2.status_code == 503
    assert "Goal service" in resp2.json()["detail"]


def test_instantiate_submit_true_with_goal_service_returns_result() -> None:
    """instantiate with submit=True and goal_service → 200 with submitted_goal (lines 325-331)."""
    mock_goal_service = MagicMock()
    mock_goal_service.submit_goal = AsyncMock(
        return_value={"id": "goal-123", "status": "planning"}
    )
    app, store = _make_app(goal_service=mock_goal_service)
    client = TestClient(app, raise_server_exceptions=False)

    # Create template
    resp = client.post(
        "/templates",
        json={"name": "Deploy Tmpl", "goal_text": "Deploy to staging", "parameters": []},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    tmpl_id = resp.json()["id"]

    # Instantiate with submit=True
    resp2 = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {}, "submit": True, "agent_id": "agent-abc", "priority": "high"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert "submitted_goal" in body
    assert body["submitted_goal"]["id"] == "goal-123"
    mock_goal_service.submit_goal.assert_awaited_once()
