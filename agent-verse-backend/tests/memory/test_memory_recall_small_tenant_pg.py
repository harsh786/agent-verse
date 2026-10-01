"""MEM-09 (integration): a small tenant's vector recall is not starved by the
global HNSW index post-filter.

Setup: 10k other-tenant records sit right next to the query vector; the target
tenant has 300 records, and its one relevant record is its OLDEST (outside the
recency window). A global HNSW scan returns ef_search other-tenant neighbours,
the tenant filter then removes all of them, and the relevant record is lost.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_memory_recall_small_tenant_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

SMALL, BIG = "mem09-small", "mem09-big"
DIM = 1536
MODEL = "test-embed-1536"


def _vec(head: list[float]) -> str:
    return "[" + ",".join(str(v) for v in [*head, *([0.0] * (DIM - len(head)))]) + "]"


QUERY = [1.0]
_ROW_SQL = (
    "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, safe_summary, "
    "source_goal_id, source_execution_id, evidence_refs, classification, confidence, "
    "lifecycle_state, version, embedding_model, embedding_dimension, embedding, "
    "embedding_source_model, outcome_score, effectiveness_score, recall_count, helpful_count, "
    "harmful_count, retention_policy_id, idempotency_key, created_at, updated_at) "
)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def _seed(admin_url: str) -> None:
    zeros = ",".join(["0"] * (DIM - 2))
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        for t in (SMALL, BIG):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                {"t": t, "e": f"{t}@example.test"},
            )
        common = (
            "'reflexion', 'memory://x', {summary}, 'g', 'e', '[\"goal://g\"]', 'internal', 9000, "
            "'active', 1, 'memory-embedding-v1', 1536, {vec}, :model, 0, 0, 0, 0, 0, 'default', {idem}, "
            "{ts}, {ts}"
        )
        # 10k other-tenant records right next to the query vector.
        await c.execute(
            text(
                _ROW_SQL
                + "SELECT 'big' || g, :big, "
                + common.format(
                    summary="'other tenant note ' || g",
                    vec=f"CAST('[1,' || (g / 1000000.0)::text || ',{zeros}]' AS vector)",
                    idem="'big' || g",
                    ts="now()",
                )
                + " FROM generate_series(1, 10000) AS g"
            ),
            {"big": BIG, "model": MODEL},
        )
        # 300 irrelevant (orthogonal) small-tenant records, all recent.
        await c.execute(
            text(
                _ROW_SQL
                + "SELECT 'small' || g, :small, "
                + common.format(
                    summary="'unrelated note ' || g",
                    vec=f"CAST('{_vec([0.0, 1.0])}' AS vector)",
                    idem="'small' || g",
                    ts="now() - (g * interval '1 minute')",
                )
                + " FROM generate_series(1, 300) AS g"
            ),
            {"small": SMALL, "model": MODEL},
        )
        # The relevant record: the small tenant's oldest.
        await c.execute(
            text(
                _ROW_SQL
                + "VALUES ('target', :small, "
                + common.format(
                    summary="'the relevant runbook'",
                    vec=f"CAST('{_vec([0.9, 0.1])}' AS vector)",
                    idem="'target'",
                    ts="now() - interval '400 days'",
                )
                + ")"
            ),
            {"small": SMALL, "model": MODEL},
        )
        await c.execute(text("ANALYZE memory_records"))
    await eng.dispose()


async def test_small_tenant_gets_its_top_k(admin_url: str) -> None:
    from app.memory.contracts import MemoryRecallRequest
    from app.memory.postgres_repository import PostgresMemoryRepository

    await _seed(admin_url)

    async def _embedder(_text: str) -> tuple[float, ...]:
        return tuple(float(v) for v in [*QUERY, *([0.0] * (DIM - 1))])

    _embedder.model_id = MODEL  # type: ignore[attr-defined]
    engine = await app_role_engine(admin_url, ["memory_records"])
    repo = PostgresMemoryRepository(sessionmaker_for(engine), embedder=_embedder)
    hits = await repo.recall(
        MemoryRecallRequest(
            tenant_id=SMALL,
            query="runbook",
            memory_kinds=frozenset({"reflexion"}),
            top_k=5,
            min_confidence=0,
            allowed_data_classes=frozenset({"internal"}),
            as_of=datetime.now(UTC),
            token_budget=10_000,
        )
    )
    ids = [h.record.memory_id for h in hits]
    assert "target" in ids, ids
    assert all(h.record.tenant_id == SMALL for h in hits)
    await engine.dispose()


async def test_trigram_index_exists(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        row = (
            await c.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = 'memory_records' "
                    "AND indexdef ILIKE '%gin_trgm_ops%'"
                )
            )
        ).fetchone()
    await eng.dispose()
    assert row is not None and "safe_summary" in row[0]
