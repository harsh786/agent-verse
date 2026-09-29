"""install_count / rating updates must not silently match 0 rows under RLS.

``marketplace_templates``' write policy only matches the owning tenant, so the
plain ``UPDATE marketplace_templates SET install_count = ...`` that ``install``
and ``add_review`` ran as the *installing / reviewing* tenant matched nothing
for every other tenant's template and every system built-in — and both still
reported success. The updates now go through the SECURITY DEFINER functions of
migration d2b7e4f1a8c6, and a 0-row result fails the operation (its
transaction is not committed).
"""

from __future__ import annotations

import importlib
import inspect
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext

T_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.STARTER, api_key_id="kb")

_TEMPLATE: dict[str, Any] = {
    "id": "tpl-foreign",
    "template_id": "tpl-foreign",
    "tenant_id": "tenant-a",
    "name": "Foreign",
    "slug": "foreign",
    "visibility": "public",
    "review_status": "approved",
    "template_config": {"goal_template": "do {x}"},
    "parameters_schema": {},
    "required_connectors": [],
}

_MIGRATION = "app.db.migrations.versions.d2b7e4f1a8c6_marketplace_reviews_rls_and_counters"


class _Result:
    def __init__(self, value: Any = None, row: Any = None) -> None:
        self._value = value
        self._row = row

    def scalar_one(self) -> Any:
        return self._value

    def scalar(self) -> Any:
        return self._value

    def fetchone(self) -> Any:
        return self._row

    def first(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return []


class _Session:
    """Records SQL; answers the counter function with a configurable row count."""

    def __init__(self, counter_rows: int) -> None:
        self.counter_rows = counter_rows
        self.sql: list[str] = []
        self.committed = False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        if "marketplace_bump_install_count" in sql or (
            "marketplace_refresh_template_rating" in sql
        ):
            return _Result(value=self.counter_rows)
        if "RETURNING agent_id" in sql:
            return _Result(value="agent-1")
        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


def _svc(session: _Session) -> MarketplaceV2:
    @asynccontextmanager
    async def factory() -> Any:
        yield session

    svc = MarketplaceV2(db_factory=factory)
    # The template lookup falls back to the in-memory cache when the DB SELECT
    # returns no row; seed it there so install() reaches the write path.
    svc._cache["tpl-foreign"] = dict(_TEMPLATE)
    svc._builtin_cache_populated = True
    return svc


def _direct_template_updates(session: _Session) -> list[str]:
    return [s for s in session.sql if s.upper().startswith("UPDATE MARKETPLACE_TEMPLATES")]


@pytest.mark.asyncio
async def test_install_bumps_count_through_definer_function() -> None:
    session = _Session(counter_rows=1)
    result = await _svc(session).install(template_id="tpl-foreign", params={}, tenant_ctx=T_B)

    assert result["success"] is True, result
    assert any("marketplace_bump_install_count" in s for s in session.sql)
    assert _direct_template_updates(session) == []
    assert session.committed is True


@pytest.mark.asyncio
async def test_install_fails_loudly_when_counter_updates_zero_rows() -> None:
    session = _Session(counter_rows=0)
    result = await _svc(session).install(template_id="tpl-foreign", params={}, tenant_ctx=T_B)

    assert result["success"] is False
    assert "install_count" in result["error"]
    assert session.committed is False


@pytest.mark.asyncio
async def test_review_refreshes_rating_through_definer_function() -> None:
    session = _Session(counter_rows=1)
    result = await _svc(session).add_review(template_id="tpl-foreign", tenant_ctx=T_B, rating=4)

    assert result["success"] is True, result
    assert any("marketplace_refresh_template_rating" in s for s in session.sql)
    assert _direct_template_updates(session) == []
    assert session.committed is True


@pytest.mark.asyncio
async def test_review_fails_loudly_when_rating_updates_zero_rows() -> None:
    session = _Session(counter_rows=0)
    result = await _svc(session).add_review(template_id="tpl-foreign", tenant_ctx=T_B, rating=4)

    assert result["success"] is False
    assert "rating" in result["error"]
    assert session.committed is False


@pytest.mark.asyncio
async def test_in_memory_install_fails_when_template_not_counted() -> None:
    # In-memory mode: a template that is visible but has no cache row to count
    # on must not report success with an uncounted install.
    svc = MarketplaceV2(db_factory=None)
    svc._builtin_cache_populated = True

    async def _get(**_: Any) -> dict[str, Any]:
        return dict(_TEMPLATE)

    svc.get_template = _get  # type: ignore[method-assign]
    result = await svc.install(template_id="tpl-foreign", params={}, tenant_ctx=T_B)
    assert result["success"] is False
    assert svc._installs == []


def test_counter_functions_are_security_definer_and_check_visibility() -> None:
    source = inspect.getsource(importlib.import_module(_MIGRATION))
    for fn in ("marketplace_bump_install_count", "marketplace_refresh_template_rating"):
        assert f"CREATE OR REPLACE FUNCTION {fn}(p_template_id TEXT)" in source
    assert source.count("SECURITY DEFINER\nSET search_path = pg_catalog, public") == 2
    # Caller visibility is checked before the owner-context UPDATE; row count
    # is returned so the application can fail on 0.
    assert source.count("GET DIAGNOSTICS updated = ROW_COUNT") == 2
    assert "i.installer_tenant_id = caller" in source
