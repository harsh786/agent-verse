"""GET /marketplace/installs and GET /marketplace/domains/counts.

Both endpoints read ``app.state.marketplace`` — the deprecated v1 gallery,
which has neither ``list_installs`` nor ``list_templates`` — and swallowed the
resulting errors, so ``/installs`` always answered ``[]`` and
``/domains/counts`` never counted a marketplace template. They now read the
v2 service (DB in DB mode, memory otherwise) and fail loudly on errors.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import marketplace_router
from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

T_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.ENTERPRISE, api_key_id="ka")
T_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.STARTER, api_key_id="kb")
_HDR_A = {"X-API-Key": "key-a"}


def _app(v2: Any, template_store: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return {"key-a": T_A, "key-b": T_B}.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(marketplace_router)
    app.state.marketplace_v2 = v2
    if template_store is not None:
        app.state.template_store = template_store
    return app


# ── service: list_installs ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_installs_in_memory_is_tenant_scoped() -> None:
    svc = MarketplaceV2(db_factory=None)
    a = await svc.install(template_id="tpl-bug-fix", params={"repo": "r"}, tenant_ctx=T_A)
    b = await svc.install(template_id="tpl-devops", params={"service": "api"}, tenant_ctx=T_B)
    assert a["success"] is True and b["success"] is True

    installs = await svc.list_installs(tenant_id=T_A.tenant_id)
    assert [i["template_id"] for i in installs] == ["tpl-bug-fix"]
    assert installs[0]["agent_id"] == a["agent_id"]
    assert installs[0]["install_id"] == a["install_id"]


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping


class _Session:
    def __init__(self, rows: list[Any] | None = None, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(stmt).split())
        self.sql.append((sql, params or {}))
        if "set_config" in sql:
            return _Rows([])
        if self.fail:
            raise RuntimeError("db down")
        return _Rows(self.rows)


def _factory(session: _Session) -> Any:
    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


@pytest.mark.asyncio
async def test_list_installs_db_reads_marketplace_installs_under_tenant_guc() -> None:
    session = _Session(
        rows=[
            _Row(
                {
                    "install_id": "i1",
                    "template_id": "tpl-1",
                    "agent_id": "ag-1",
                    "installed_at": None,
                }
            )
        ]
    )
    svc = MarketplaceV2(db_factory=_factory(session))
    installs = await svc.list_installs(tenant_id=T_A.tenant_id)

    assert installs == [
        {"install_id": "i1", "template_id": "tpl-1", "agent_id": "ag-1", "installed_at": None}
    ]
    guc, query = session.sql[0], session.sql[1]
    assert "set_config('app.tenant_id'" in guc[0] and guc[1]["tid"] == T_A.tenant_id
    assert "FROM marketplace_installs" in query[0]
    assert "installer_tenant_id = :tid" in query[0]
    assert "uninstalled_at IS NULL" in query[0]
    assert query[1]["tid"] == T_A.tenant_id


@pytest.mark.asyncio
async def test_list_installs_db_error_propagates() -> None:
    svc = MarketplaceV2(db_factory=_factory(_Session(fail=True)))
    with pytest.raises(RuntimeError, match="db down"):
        await svc.list_installs(tenant_id=T_A.tenant_id)


# ── endpoint: /installs ─────────────────────────────────────────────────────


def test_installs_endpoint_returns_real_installs() -> None:
    svc = MarketplaceV2(db_factory=None)
    client = TestClient(_app(svc))
    deployed = client.post(
        "/marketplace/templates/tpl-devops/deploy", json={"params": {"service": "api"}}, headers=_HDR_A
    )
    assert deployed.status_code == 200, deployed.text

    resp = client.get("/marketplace/installs", headers=_HDR_A)
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed_ids"] == ["tpl-devops"]
    assert body["installs"][0]["agent_id"] == deployed.json()["agent_id"]

    other = client.get("/marketplace/installs", headers={"X-API-Key": "key-b"})
    assert other.json()["installed_ids"] == []


def test_installs_endpoint_fails_loudly_on_store_error() -> None:
    svc = MagicMock()
    svc.list_installs = AsyncMock(side_effect=RuntimeError("db down"))
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.get("/marketplace/installs", headers=_HDR_A)
    assert resp.status_code == 503


# ── service + endpoint: domain counts ───────────────────────────────────────


@pytest.mark.asyncio
async def test_count_by_domain_in_memory_respects_visibility() -> None:
    svc = MarketplaceV2(db_factory=None)
    await svc.publish_template(
        data={"name": "Secret", "slug": "secret-x", "domain": "zz-private"},
        tenant_ctx=T_A,
        run_security_review=False,
    )
    counts_a = await svc.count_by_domain(tenant_id=T_A.tenant_id)
    counts_b = await svc.count_by_domain(tenant_id=T_B.tenant_id)

    assert counts_a["zz-private"] == 1
    assert "zz-private" not in counts_b
    assert counts_b.get("software", 0) >= 1  # built-ins are counted


@pytest.mark.asyncio
async def test_count_by_domain_db_groups_visible_templates() -> None:
    session = _Session(rows=[("sales", 3), ("hr", 1)])
    svc = MarketplaceV2(db_factory=_factory(session))
    counts = await svc.count_by_domain(tenant_id=T_A.tenant_id)

    assert counts == {"sales": 3, "hr": 1}
    query, params = session.sql[1]
    assert "GROUP BY domain" in query
    assert "tenant_id = :vis_tid" in query
    assert params["vis_tid"] == T_A.tenant_id


def test_domain_counts_endpoint_counts_marketplace_templates() -> None:
    svc = MagicMock()
    svc.count_by_domain = AsyncMock(return_value={"sales": 2, "support": 1})
    store = MagicMock()
    store.list = AsyncMock(return_value=[{"domain": "sales"}, {"domain": "ops"}])
    client = TestClient(_app(svc, template_store=store))

    resp = client.get("/marketplace/domains/counts", headers=_HDR_A)
    assert resp.status_code == 200
    assert resp.json() == {
        "counts": {
            "sales": {"agents": 2, "templates": 1},
            "support": {"agents": 1, "templates": 0},
            "ops": {"agents": 0, "templates": 1},
        }
    }
    svc.count_by_domain.assert_awaited_once_with(tenant_id=T_A.tenant_id)


def test_domain_counts_endpoint_fails_loudly_on_marketplace_error() -> None:
    svc = MagicMock()
    svc.count_by_domain = AsyncMock(side_effect=RuntimeError("db down"))
    client = TestClient(_app(svc), raise_server_exceptions=False)
    assert client.get("/marketplace/domains/counts", headers=_HDR_A).status_code == 503


def test_domain_counts_endpoint_fails_loudly_on_template_store_error() -> None:
    svc = MagicMock()
    svc.count_by_domain = AsyncMock(return_value={})
    store = MagicMock()
    store.list = AsyncMock(side_effect=RuntimeError("store down"))
    client = TestClient(_app(svc, template_store=store), raise_server_exceptions=False)
    assert client.get("/marketplace/domains/counts", headers=_HDR_A).status_code == 503
