"""Regression tests: the legal-hold API writes the real schema and fails loudly.

``POST /governance/legal-hold`` inserted a ``reason`` column that ``legal_holds``
(migration 0057) does not have, omitted the NOT NULL ``name``/``resource_type``
and ran without the RLS tenant GUC, so no hold could ever be placed; ``GET
/governance/legal-holds`` turned the resulting error into ``[]``. Deletion paths
also ignored the tenant-wide hold the API is meant to place.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.legal_holds import LegalHoldManager
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEY = "ak_test_hold"
_CTX = TenantContext(
    tenant_id="tid-hold", plan=PlanTier.ENTERPRISE, api_key_id="kid-h", roles=("admin",)
)

# Columns of legal_holds as created by migration 0057_audit_rails_v2.
_REAL_COLUMNS = {
    "id",
    "tenant_id",
    "name",
    "description",
    "resource_type",
    "resource_ids",
    "user_ids",
    "date_range_start",
    "date_range_end",
    "status",
    "legal_matter_id",
    "created_by",
    "created_at",
    "released_at",
    "released_by",
    "release_reason",
    "expires_at",
}


class _Session:
    def __init__(self, fail: bool = False) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.fail = fail

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.sql.append(str(stmt))
        self.params.append(params or {})
        if self.fail and "legal_holds" in str(stmt):
            raise RuntimeError("relation unavailable")
        res = MagicMock()
        res.fetchall.return_value = []
        return res

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


def _db(session: _Session) -> Any:
    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


def _client(session: _Session) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.db_session_factory = _db(session)
    return TestClient(app, raise_server_exceptions=False)


def test_create_hold_inserts_only_real_columns_under_rls() -> None:
    session = _Session()
    resp = _client(session).post(
        "/governance/legal-hold",
        json={"reason": "SEC inquiry", "expires_at": "2027-01-01T00:00:00"},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "legal_hold_placed"
    assert body["resource_type"] == "tenant"

    # RLS tenant GUC is set before the INSERT.
    guc = next(i for i, s in enumerate(session.sql) if "set_config('app.tenant_id'" in s)
    ins = next(i for i, s in enumerate(session.sql) if "INSERT INTO legal_holds" in s)
    assert guc < ins
    cols_sql = session.sql[ins].split("(", 1)[1].split(")", 1)[0]
    cols = {c.strip() for c in cols_sql.split(",")}
    assert cols <= _REAL_COLUMNS, cols - _REAL_COLUMNS
    assert "reason" not in cols
    p = session.params[ins]
    assert (p["tid"], p["name"], p["desc"], p["rtype"]) == (
        "tid-hold",
        "SEC inquiry",
        "SEC inquiry",
        "tenant",
    )
    assert p["expires"] is not None and p["expires"].tzinfo is not None


def test_create_hold_db_failure_is_503_not_success() -> None:
    resp = _client(_Session(fail=True)).post(
        "/governance/legal-hold", json={"reason": "x"}, headers={"X-API-Key": _KEY}
    )
    assert resp.status_code == 503


def test_list_holds_db_failure_is_503_not_empty_list() -> None:
    resp = _client(_Session(fail=True)).get("/governance/legal-holds", headers={"X-API-Key": _KEY})
    assert resp.status_code == 503


def test_non_tenant_hold_without_targets_is_rejected() -> None:
    resp = _client(_Session()).post(
        "/governance/legal-hold",
        json={"reason": "x", "resource_type": "goal"},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tenant_wide_hold_covers_any_resource_and_honours_expiry() -> None:
    session = _Session()
    mgr = LegalHoldManager(redis=None, db_factory=_db(session))
    await mgr.is_under_hold("tid-hold", "some-collection")
    check = next(s for s in session.sql if "FROM legal_holds" in s)
    assert "resource_type = :tenant_wide" in check
    assert "expires_at IS NULL OR expires_at > now()" in check


def test_retention_sweeps_exempt_tenants_under_hold() -> None:
    from app.scaling.tasks import _RETENTION_DELETES, _TENANT_HOLD_EXEMPT

    for label, sql in _RETENTION_DELETES:
        assert _TENANT_HOLD_EXEMPT in sql, label
    assert "resource_type = 'tenant'" in _TENANT_HOLD_EXEMPT


@pytest.mark.asyncio
async def test_partition_drop_skipped_while_a_tenant_hold_is_active() -> None:
    from app.scaling import tasks

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value=True), rowcount=0)
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=cm)
    factory = MagicMock(return_value=cm)
    drop = AsyncMock(return_value=["goal_events_p2020_01"])
    with (
        patch("app.db.session.get_system_session_factory", return_value=factory),
        patch("app.db.rls.system_session") as sys_s,
        patch.object(tasks, "_drop_expired_partitions", drop),
    ):
        sys_s.return_value.__aenter__ = AsyncMock(return_value=session)
        sys_s.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await tasks._delete_expired_records(90)
    drop.assert_not_awaited()
    assert result["deleted"]["goal_events_partitions_dropped"].startswith("skipped")
