"""MEM-13 (integration): episodic relevance is ranked in SQL, not over a
quality/recency window — an old, low-quality but relevant episode is recalled.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_episodic_relevance_pg.py -q -m integration
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem13-tenant"
DIM = 8


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


def _v(i: int) -> list[float]:
    vec = [0.0] * DIM
    vec[i] = 1.0
    return vec


async def _seed(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
        insert = text(
            "INSERT INTO episodic_memories (id, tenant_id, goal_id, goal_text, action_summary, "
            "outcome, lessons, embedding, quality_score, steps_count, tools_used, created_at) "
            "VALUES (:id, :t, 'g', :goal, 's', 'success', '', CAST(:emb AS jsonb), :q, 1, "
            "'[]', now() - (:age * interval '1 day'))"
        )
        # 300 newer, high-quality, unrelated episodes.
        for i in range(300):
            await c.execute(
                insert,
                {"id": f"n{i}", "t": TENANT, "goal": f"weekly marketing digest {i}",
                 "emb": json.dumps(_v(1)), "q": 0.95, "age": 1},
            )
        # One old, low-quality but relevant episode (vector + wording).
        await c.execute(
            insert,
            {"id": "old", "t": TENANT, "goal": "rotate the production database credentials",
             "emb": json.dumps(_v(0)), "q": 0.2, "age": 500},
        )
    await eng.dispose()


async def test_old_relevant_episode_outranks_newer_unrelated(admin_url: str) -> None:
    from app.memory.episodic import EpisodicMemoryStore

    await _seed(admin_url)

    class _Embedder:
        async def embed(self, _req: Any) -> Any:
            return SimpleNamespace(embeddings=[_v(0)])

    engine = await app_role_engine(admin_url, ["episodic_memories"])
    store = EpisodicMemoryStore(db_factory=sessionmaker_for(engine), embedder=_Embedder())
    eps = await store.recall(goal="rotate database credentials", tenant_id=TENANT, limit=3)
    assert eps and eps[0].episode_id == "old"

    # Keyword-only (no embedder): ranked by text relevance in SQL too.
    lexical = EpisodicMemoryStore(db_factory=sessionmaker_for(engine))
    eps2 = await lexical.recall(goal="rotate database credentials", tenant_id=TENANT, limit=3)
    assert eps2 and eps2[0].episode_id == "old"
    await engine.dispose()
