"""MEM-40 (integration): episodic recall is index-backed.

* Pre-migration episodes (JSONB embedding only) are backfilled into
  ``embedding_vec`` in batches; the backfilled episode is recalled semantically
  (a small tenant is searched exactly).
* A 10k-episode tenant is served by the HNSW index (iterative, tenant-filtered)
  within a time bound; each recall query (HNSW halfvec, GIN trigram, quality
  window) has its index.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_episodic_indexed_recall_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.episodic import EpisodicMemoryStore
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

SMALL = "mem40-small"
BIG = "mem40-big"
DIM = 8

_DISTRACTORS = (
    "INSERT INTO episodic_memories (id, tenant_id, goal_id, goal_text, action_summary, "
    "outcome, lessons, embedding, quality_score, steps_count, tools_used, created_at) "
    "SELECT :p || g, :t, 'g', 'weekly marketing digest number ' || g, 's', 'success', '', "
    "to_jsonb(ARRAY(SELECT (random() * 0.9 + 0.1 + 0 * g)::float4 FROM generate_series(1, 7)) "
    "|| ARRAY[0.05::float4]), 0.9, 1, '[]', now() - (g * interval '1 minute') "
    "FROM generate_series(1, :n) AS g"
)


def _axis7() -> list[float]:
    v = [0.3] * DIM
    v[7] = 1.0
    return v


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url, "c45f9a1b3e74")

        async def _pre_migration_rows() -> None:
            eng = create_async_engine(url)
            async with eng.begin() as c:
                for t in (SMALL, BIG):
                    await c.execute(
                        text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                        {"t": t, "e": f"{t}@example.test"},
                    )
                await c.execute(text("ALTER TABLE episodic_memories NO FORCE ROW LEVEL SECURITY"))
                await c.execute(
                    text(
                        "INSERT INTO episodic_memories (id, tenant_id, goal_id, goal_text, "
                        "action_summary, outcome, lessons, embedding, quality_score, "
                        "steps_count, tools_used, created_at) VALUES ('legacy', :t, 'g', "
                        "'rotate the production database credentials', 's', 'success', '', "
                        "CAST(:e AS jsonb), 0.1, 1, '[]', now() - interval '400 days')"
                    ),
                    {"t": SMALL, "e": json.dumps(_axis7())},
                )
                await c.execute(text(_DISTRACTORS), {"p": "s", "t": SMALL, "n": 100})
                await c.execute(text(_DISTRACTORS), {"p": "b", "t": BIG, "n": 10_000})
                await c.execute(text("ALTER TABLE episodic_memories FORCE ROW LEVEL SECURITY"))
            await eng.dispose()

        asyncio.run(_pre_migration_rows())
        alembic_upgrade(url, "head")  # backfills 10,101 rows in batches of 5,000
        yield url


@pytest.fixture(scope="module")
async def sessions(admin_url: str) -> Any:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("ANALYZE episodic_memories"))
    await eng.dispose()
    app_eng = await app_role_engine(admin_url, ["episodic_memories"])
    yield sessionmaker_for(app_eng)
    await app_eng.dispose()


class _Embedder:
    async def embed(self, _req: Any) -> Any:
        return SimpleNamespace(embeddings=[_axis7()], model="")


async def test_migration_backfilled_every_episode(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.connect() as c:
        missing, dims, total = (
            await c.execute(
                text(
                    "SELECT count(*) FILTER (WHERE embedding_vec IS NULL), "
                    "count(DISTINCT embedding_dim), count(*) FROM episodic_memories"
                )
            )
        ).one()
    await eng.dispose()
    assert (missing, dims, total) == (0, 1, 10_101)


async def test_backfilled_episode_is_recalled_semantically(sessions: Any) -> None:
    store = EpisodicMemoryStore(db_factory=sessions, embedder=_Embedder())
    eps = await store.recall(goal="cycle db secrets", tenant_id=SMALL, limit=3)
    assert eps and eps[0].episode_id == "legacy"
    assert eps[0].similarity is not None and eps[0].similarity > 0.99
    lexical = await EpisodicMemoryStore(db_factory=sessions).recall(
        goal="rotate database credentials", tenant_id=SMALL, limit=3
    )
    assert lexical and lexical[0].episode_id == "legacy"


async def test_large_tenant_recall_is_index_served_and_fast(sessions: Any) -> None:
    store = EpisodicMemoryStore(db_factory=sessions, embedder=_Embedder())
    start = time.monotonic()
    eps = await store.recall(goal="marketing digest", tenant_id=BIG, limit=3)
    elapsed = time.monotonic() - start
    assert len(eps) == 3 and all(e.tenant_id == BIG for e in eps)
    assert elapsed < 3.0


async def _explain(admin_url: str, sql: str, params: dict[str, Any]) -> str:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("SET LOCAL enable_seqscan = off"))
        rows = (await c.execute(text(f"EXPLAIN {sql}"), params)).fetchall()
    await eng.dispose()
    return "\n".join(r[0] for r in rows)


async def test_each_recall_query_is_index_backed(admin_url: str) -> None:
    q = "[" + ",".join(["0.3"] * 7 + ["1"] + ["0"] * (2048 - 8)) + "]"
    semantic = await _explain(
        admin_url,
        "SELECT id FROM episodic_memories ORDER BY (embedding_vec::halfvec(2048) "
        "<=> CAST(:q AS halfvec(2048))) LIMIT 50",
        {"q": q},
    )
    assert "ix_episodic_embedding_halfvec" in semantic
    lexical = await _explain(
        admin_url,
        "SELECT id FROM episodic_memories WHERE goal_text % :g OR :g <% goal_text",
        {"g": "rotate database credentials"},
    )
    assert "ix_episodic_goal_text_trgm" in lexical
    window = await _explain(
        admin_url,
        "SELECT id FROM episodic_memories WHERE tenant_id = :t "
        "ORDER BY quality_score DESC, created_at DESC LIMIT 50",
        {"t": BIG},
    )
    assert "ix_episodic_tenant_quality" in window
