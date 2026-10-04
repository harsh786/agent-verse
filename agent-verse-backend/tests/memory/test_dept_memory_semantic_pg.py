"""MEM-42 (integration): semantic department-memory recall in Postgres under
the least-privilege app role: RLS-scoped per tenant and per department, exact
for a small department and through the halfvec HNSW index for a large one.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_dept_memory_semantic_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.dept_memory import DepartmentMemory
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for
from tests.memory.test_dept_memory_semantic import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

T1, T2 = "dm-pg-t1", "dm-pg-t2"
FILLER = 6_000  # > the exact-search threshold: the big department uses HNSW


async def _allow(*, content: str, **_kw: Any) -> dict[str, Any]:
    return {"blocked": False}


@pytest.fixture(autouse=True)
def _guardrail() -> Any:
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow):
        yield


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def mem(admin_url: str) -> Any:
    engine = await app_role_engine(admin_url, ["department_memory_entries"])
    store = DepartmentMemory()
    store.set_db(sessionmaker_for(engine))
    store.set_embedder(FakeEmbedder())
    for tenant in (T1, T2):
        await store.add("support", "o1", tenant, f"Refunds over $500 need approval ({tenant})",
                        "ops")
        await store.add("support", "o1", tenant, "Release notes go out every Friday", "ops")
    await store.add("eng", "o1", T1, "Reimburse conference travel within 30 days", "ops")
    await store.add("big", "o1", T1, "Refunds for annual plans are prorated", "ops")
    # Filler on another concept axis, same model, in the big department.
    admin = create_async_engine(admin_url)
    async with admin.begin() as c:
        await c.execute(text("SET LOCAL row_security = off"))
        await c.execute(
            text(
                "INSERT INTO department_memory_entries (entry_id, dept_id, org_id, tenant_id, "
                "content, source, confidence, embedding, embedding_model) "
                "SELECT 'f' || g, 'big', 'o1', :t, 'weekly standup notes ' || g, 'bot', 0.9, "
                # Graded, distinct vectors (real embeddings are; identical or
                # all-orthogonal ones degenerate any HNSW graph): each filler
                # leans a little toward the refund concept and mostly toward
                # one of 50 other topics.
                "(SELECT CAST(array_agg(CASE WHEN i = 1 THEN 0.05 "
                "WHEN i = 2 THEN (g % 10) * 0.05 "
                "WHEN i = 7 + (g % 50) THEN 1 ELSE 0 END ORDER BY i) AS vector) "
                "FROM generate_series(1, 2048) i), 'fake-concepts-v1' "
                f"FROM generate_series(1, {FILLER}) g"
            ),
            {"t": T1},
        )
        await c.execute(text("ANALYZE department_memory_entries"))
    await admin.dispose()
    yield store
    await engine.dispose()


async def test_paraphrase_recalls_the_sop_in_this_tenant_and_department(mem: Any) -> None:
    hits = await mem.retrieve("support", "customers want their money back", top_k=1,
                              tenant_id=T1)
    assert [h.content for h in hits] == [f"Refunds over $500 need approval ({T1})"]
    # The other department's paraphrase match is not in this department's recall.
    assert all(h.dept_id == "support" for h in
               await mem.retrieve("support", "reimburse", top_k=5, tenant_id=T1))
    # Another tenant only ever sees its own entries.
    other = await mem.retrieve("support", "money back", top_k=5, tenant_id=T2)
    assert other and all(h.tenant_id == T2 for h in other)


async def test_large_department_finds_the_match_through_the_index(mem: Any) -> None:
    hits = await mem.retrieve("big", "money back on the yearly subscription", top_k=1,
                              tenant_id=T1)
    assert [h.content for h in hits] == ["Refunds for annual plans are prorated"]


async def test_org_wide_recall_is_semantic_too(mem: Any) -> None:
    hits = await mem.retrieve_for_org("o1", "reimburse my travel", top_k=1, tenant_id=T1)
    assert hits and hits[0].dept_id == "eng"

