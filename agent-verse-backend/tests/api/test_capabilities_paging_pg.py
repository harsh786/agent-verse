"""a02-F032-05 on real Postgres (least-privilege NOBYPASSRLS role).

``GET /connectors/capabilities`` selected every tool_capabilities row of the
tenant with no LIMIT. It now pages in (tool_name, connector_id) order under the
tenant's RLS scope plus an explicit tenant predicate, and says when more exist.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_capabilities_paging_pg.py -m integration
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from app.api.connectors import list_capabilities
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


async def _seed(pg_url: str, tenant_id: str, names: list[str]) -> None:
    for name in names:
        await admin_exec(
            pg_url,
            "INSERT INTO tool_capabilities (id, tenant_id, connector_id, tool_name, "
            "description, http_method, http_path) "
            "VALUES (:id, :tid, 'c1', :name, 'd', 'GET', '/x')",
            {"id": uuid.uuid4().hex, "tid": tenant_id, "name": name},
        )


async def test_capabilities_are_paged_and_tenant_scoped(pg_url: str) -> None:
    ta, tb = uuid.uuid4().hex, uuid.uuid4().hex
    for tid in (ta, tb):
        await seed_tenant(pg_url, tid)
    await _seed(pg_url, ta, ["tool_c", "tool_a", "tool_e", "tool_b", "tool_d"])
    await _seed(pg_url, tb, ["tool_0_other_tenant"])
    engine = await app_engine(pg_url)
    try:
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(db_session_factory=sessions(engine))),
            state=SimpleNamespace(tenant=TenantContext(ta, PlanTier.PROFESSIONAL, "k")),
        )
        seen: list[str] = []
        offset: int | None = 0
        pages = 0
        while offset is not None:
            response = Response()
            page = await list_capabilities(
                request,  # type: ignore[arg-type]
                response,
                q="",
                limit=2,
                offset=offset,
            )
            pages += 1
            seen += [row["tool_name"] for row in page]
            nxt = response.headers.get("X-Next-Offset")
            offset = int(nxt) if nxt is not None else None
        assert seen == ["tool_a", "tool_b", "tool_c", "tool_d", "tool_e"]
        assert pages == 3
    finally:
        await engine.dispose()
