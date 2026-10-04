"""U8 KG versioning: graph-version history was cross-tenant readable.

The history lived in a process-global in-memory VersionStore keyed only by
``knowledge_graph:<org_id>`` with no tenant filter and no org-ownership check,
so any tenant could read (and append to) another tenant's org history by id —
and it vanished on restart / differed per replica. It is now persisted in the
RLS-protected ``org_graph_versions`` table through the tenant-scoped OrgService,
and both endpoints 404 unless the org belongs to the caller.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import get_org_service
from app.org.router import router as org_router
from app.org.service import OrgService

TENANT = "00000000-0000-0000-0000-00000000000a"
ORG = str(uuid.uuid4())


def _app(svc: Any) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        # Owner key (org_admin): the test is about org scoping, not role checks.
        request.state.tenant = SimpleNamespace(tenant_id=TENANT, roles=("admin",))
        return await call_next(request)

    async def _svc() -> Any:
        return svc

    app.include_router(org_router)
    app.dependency_overrides[get_org_service] = _svc
    return app


def _svc(*, owns: bool) -> MagicMock:
    svc = MagicMock(spec=OrgService)
    svc.get_organization = AsyncMock(return_value=MagicMock() if owns else None)
    svc.list_graph_versions = AsyncMock(return_value=[])
    svc.save_graph_version = AsyncMock(
        return_value=SimpleNamespace(
            id=uuid.uuid4(), version_num=1, content_hash="abc", created_at=None
        )
    )
    return svc


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_history_of_foreign_org_is_404() -> None:
    svc = _svc(owns=False)
    async with AsyncClient(transport=ASGITransport(app=_app(svc)), base_url="http://t") as c:
        r = await c.get(f"/v1/org/{ORG}/graph/versions")
    assert r.status_code == 404
    svc.list_graph_versions.assert_not_called()


@pytest.mark.anyio
async def test_snapshot_of_foreign_org_is_404() -> None:
    svc = _svc(owns=False)
    with patch("app.knowledge_graph.store.kg_store") as kg:
        kg.aexport = AsyncMock(return_value={"nodes": []})
        kg.acount_nodes = AsyncMock(return_value=0)
        async with AsyncClient(transport=ASGITransport(app=_app(svc)), base_url="http://t") as c:
            r = await c.post(f"/v1/org/{ORG}/graph/version")
    assert r.status_code == 404
    svc.save_graph_version.assert_not_called()


@pytest.mark.anyio
async def test_malformed_org_id_is_404_not_500() -> None:
    svc = _svc(owns=True)
    svc.get_organization = AsyncMock(side_effect=ValueError("badly formed hexadecimal UUID"))
    async with AsyncClient(transport=ASGITransport(app=_app(svc)), base_url="http://t") as c:
        r = await c.get("/v1/org/not-a-uuid/graph/versions")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_history_reads_from_tenant_scoped_service() -> None:
    svc = _svc(owns=True)
    svc.list_graph_versions = AsyncMock(
        return_value=[{"version_id": "v", "version_num": 2, "content_hash": "h"}]
    )
    async with AsyncClient(transport=ASGITransport(app=_app(svc)), base_url="http://t") as c:
        r = await c.get(f"/v1/org/{ORG}/graph/versions?limit=5")
    assert r.status_code == 200
    assert r.json()["versions"][0]["version_num"] == 2
    svc.list_graph_versions.assert_awaited_once_with(ORG, limit=5)


@pytest.mark.anyio
async def test_snapshot_persists_through_service() -> None:
    svc = _svc(owns=True)
    with patch("app.knowledge_graph.store.kg_store") as kg:
        kg.aexport = AsyncMock(return_value={"nodes": [SimpleNamespace(node_id="n1")]})
        kg.acount_nodes = AsyncMock(return_value=1)
        async with AsyncClient(transport=ASGITransport(app=_app(svc)), base_url="http://t") as c:
            r = await c.post(f"/v1/org/{ORG}/graph/version")
    assert r.status_code == 201, r.text
    assert r.json()["version_num"] == 1
    kwargs = svc.save_graph_version.await_args.kwargs
    assert svc.save_graph_version.await_args.args[0] == ORG
    assert kwargs["snapshot"] == {"node_ids": ["n1"], "node_count": 1}


# ── OrgService: the queries themselves are tenant + org scoped ────────────────


class _CapturingSession:
    def __init__(self, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self._scalar = scalar
        self._rows = rows or []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> Any:
        self.statements.append(stmt)
        res = MagicMock()
        res.scalar.return_value = self._scalar
        res.scalars.return_value.all.return_value = self._rows
        return res

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _sql(stmt: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_list_graph_versions_filters_by_tenant_and_org() -> None:
    session = _CapturingSession(rows=[])
    svc = OrgService(session=session, tenant_id=TENANT)  # type: ignore[arg-type]
    assert await svc.list_graph_versions(ORG, limit=3) == []
    sql = _sql(session.statements[0])
    assert "org_graph_versions.tenant_id" in sql
    assert "org_graph_versions.org_id" in sql


async def test_save_graph_version_numbers_per_tenant_org() -> None:
    session = _CapturingSession(scalar=4)
    svc = OrgService(session=session, tenant_id=TENANT)  # type: ignore[arg-type]
    rec = await svc.save_graph_version(
        ORG, snapshot={"node_ids": []}, changed_by="api", change_reason="r"
    )
    assert rec.version_num == 5
    assert str(rec.tenant_id) == TENANT
    assert str(rec.org_id) == ORG
    assert len(rec.content_hash) == 16
    sql = _sql(session.statements[0])
    assert "org_graph_versions.tenant_id" in sql
    assert "org_graph_versions.org_id" in sql
    assert session.added == [rec]
