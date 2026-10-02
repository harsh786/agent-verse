"""MEM-35 (integration): every long_term_memory read path is index-backed.

Semantic recall uses the halfvec(2048) HNSW index, keyword fallback the GIN
trigram index, and the paged list the (tenant_id, created_at DESC) index.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_ltm_recall_indexes_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def _explain(admin_url: str, sql: str, params: dict[str, Any]) -> str:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("SET LOCAL enable_seqscan = off"))
        rows = (await c.execute(text(f"EXPLAIN {sql}"), params)).fetchall()
    await eng.dispose()
    return "\n".join(r[0] for r in rows)


async def test_semantic_recall_uses_the_halfvec_hnsw_index(admin_url: str) -> None:
    vec = "[" + ",".join(["0.01"] * 2048) + "]"
    plan = await _explain(
        admin_url,
        "SELECT id FROM long_term_memory ORDER BY embedding::halfvec(2048) "
        "<=> CAST(:q AS halfvec(2048)) LIMIT 5",
        {"q": vec},
    )
    assert "idx_long_term_memory_embedding_halfvec" in plan


async def test_keyword_fallback_uses_the_trigram_index(admin_url: str) -> None:
    plan = await _explain(
        admin_url,
        "SELECT id FROM long_term_memory WHERE content ILIKE :t0 OR content ILIKE :t1",
        {"t0": "%jira%", "t1": "%deploy%"},
    )
    assert "ix_long_term_memory_content_trgm" in plan


async def test_paged_list_uses_the_tenant_recency_index(admin_url: str) -> None:
    plan = await _explain(
        admin_url,
        "SELECT id FROM long_term_memory WHERE tenant_id = :t "
        "ORDER BY created_at DESC LIMIT 50 OFFSET 0",
        {"t": "t1"},
    )
    assert "ix_long_term_memory_tenant_created" in plan
    assert "Sort" not in plan
