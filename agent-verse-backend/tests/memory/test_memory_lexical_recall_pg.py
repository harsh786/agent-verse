"""a05-F081-03 (integration): lexical canonical-memory recall is index-backed.

Without a query embedding, the relevance candidate query used to be
``ORDER BY similarity(safe_summary, :q) DESC LIMIT n`` with no trigram
predicate, so the GIN ``gin_trgm_ops`` index on ``safe_summary`` could not
serve it: every eligible row of the tenant was scanned and sorted per recall.
The query now carries the pg_trgm ``%`` / ``<%`` predicates, the same shape as
episodic (MEM-40) and execution-memory (MEM-36) recall.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_memory_lexical_recall_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT, OTHER = "f081-lex", "f081-other"
QUERY = "postgres replica failover runbook"

_ROW_SQL = (
    "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, safe_summary, "
    "source_goal_id, source_execution_id, evidence_refs, classification, confidence, "
    "lifecycle_state, version, embedding_model, embedding_dimension, outcome_score, "
    "effectiveness_score, recall_count, helpful_count, harmful_count, retention_policy_id, "
    "idempotency_key, created_at, updated_at) "
)
_COMMON = (
    "'reflexion', 'memory://x', {summary}, 'g', 'e', '[\"goal://g\"]', 'internal', 9000, "
    "'active', 1, 'memory-embedding-v1', 2048, 0, 0, 0, 0, 0, 'default', {idem}, {ts}, {ts}"
)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def seeded(admin_url: str) -> str:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        for t in (TENANT, OTHER):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                {"t": t, "e": f"{t}@example.test"},
            )
        # 50k recent, unrelated records: the recency window never reaches the
        # relevant one, and the tenant predicate alone is not selective.
        await c.execute(
            text(
                _ROW_SQL
                + "SELECT 'lex' || g, :t, "
                + _COMMON.format(
                    summary="'routine heartbeat status note number ' || g",
                    idem="'lex' || g",
                    ts="now() - (g * interval '1 minute')",
                )
                + " FROM generate_series(1, 50000) AS g"
            ),
            {"t": TENANT},
        )
        # The relevant record: the tenant's oldest.
        await c.execute(
            text(
                _ROW_SQL
                + "VALUES ('target', :t, "
                + _COMMON.format(
                    summary="'postgres replica failover runbook'",
                    idem="'target'",
                    ts="now() - interval '400 days'",
                )
                + ")"
            ),
            {"t": TENANT},
        )
        # Another tenant's equally relevant record must never be recalled.
        await c.execute(
            text(
                _ROW_SQL
                + "VALUES ('foreign', :t, "
                + _COMMON.format(
                    summary="'postgres replica failover runbook for another tenant'",
                    idem="'foreign'",
                    ts="now()",
                )
                + ")"
            ),
            {"t": OTHER},
        )
        await c.execute(text("ANALYZE memory_records"))
    await eng.dispose()
    return admin_url


def _request():  # type: ignore[no-untyped-def]
    from app.memory.contracts import MemoryRecallRequest

    return MemoryRecallRequest(
        tenant_id=TENANT,
        query=QUERY,
        memory_kinds=frozenset({"reflexion"}),
        top_k=5,
        min_confidence=0,
        allowed_data_classes=frozenset({"internal"}),
        as_of=datetime.now(UTC),
        token_budget=10_000,
    )


async def test_lexical_recall_finds_the_old_relevant_record_under_rls(seeded: str) -> None:
    from app.memory.postgres_repository import PostgresMemoryRepository

    engine = await app_role_engine(seeded, ["memory_records"])
    try:
        repo = PostgresMemoryRepository(sessionmaker_for(engine))  # no embedder: lexical
        hits = await repo.recall(_request())
    finally:
        await engine.dispose()
    ids = [h.record.memory_id for h in hits]
    assert ids and ids[0] == "target", ids
    assert "foreign" not in ids
    assert all(h.record.tenant_id == TENANT for h in hits)


async def test_lexical_relevance_query_can_use_the_trigram_index(seeded: str) -> None:
    from app.memory.postgres_repository import recall_candidate_queries

    relevance = recall_candidate_queries(_request(), query_embedding=None, embedding_model=None)[0]
    sql = str(
        relevance.compile(
            dialect=postgresql.asyncpg.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    eng = create_async_engine(seeded)
    try:
        async with eng.begin() as c:
            # With seq scans off the plan shows which index serves the query:
            # before the fix the only option was the tenant index — read and
            # sort all 50k rows; ORDER BY similarity() alone never uses the
            # trigram index.
            await c.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                str(r[0]) for r in (await c.execute(text("EXPLAIN " + sql))).fetchall()
            )
    finally:
        await eng.dispose()
    assert "ix_memory_records_safe_summary_trgm" in plan, plan
