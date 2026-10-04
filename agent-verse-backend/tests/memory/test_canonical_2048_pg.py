"""MEM-38 (integration): canonical memory works with the default 2048-d
embedder, and the re-embed sweep always makes progress.

* memory_records.embedding is vector(2048) behind a halfvec HNSW index; a
  pre-existing 1536-d vector is zero-padded by the migration (cosine unchanged).
* a 2048-d embedder's write is stored with a vector and recalled semantically.
* a too-wide (3072-d) embedder: the sweep marks the records unembeddable for
  that model, and a second sweep makes zero embed calls.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_canonical_2048_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.contracts import MemoryRecallRequest, MemoryWriteRequest
from app.memory.embedding import ProviderMemoryEmbedder
from app.memory.postgres_repository import PostgresMemoryRepository
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem38-tenant"


def _vec(dim: int, axis: int) -> list[float]:
    v = [0.01] * dim
    v[axis] = 1.0
    return v


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url, "f1e790b4050a")

        async def _legacy() -> None:
            eng = create_async_engine(url)
            async with eng.begin() as c:
                await c.execute(
                    text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                    {"t": TENANT, "e": f"{TENANT}@example.test"},
                )
                await c.execute(text("ALTER TABLE memory_records NO FORCE ROW LEVEL SECURITY"))
                await c.execute(
                    text(
                        "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, "
                        "safe_summary, source_goal_id, source_execution_id, evidence_refs, "
                        "classification, confidence, lifecycle_state, version, "
                        "embedding_model, embedding_dimension, embedding, "
                        "embedding_source_model, outcome_score, effectiveness_score, "
                        "recall_count, helpful_count, harmful_count, retention_policy_id, "
                        "idempotency_key, created_at, updated_at) VALUES ('legacy1536', :t, "
                        "'reflexion', 'memory://legacy1536', 'old lesson', 'g', 'e', "
                        "'[\"goal://g\"]', 'internal', 7000, 'active', 1, "
                        "'memory-embedding-v1', 1536, CAST(:v AS vector), 'old-1536-model', "
                        "0, 0, 0, 0, 0, 'default', 'legacy', :now, :now)"
                    ),
                    {"t": TENANT, "v": str(_vec(1536, 3)), "now": datetime.now(UTC)},
                )
                await c.execute(text("ALTER TABLE memory_records FORCE ROW LEVEL SECURITY"))
            await eng.dispose()

        asyncio.run(_legacy())
        alembic_upgrade(url, "head")
        yield url


@pytest.fixture(scope="module")
async def factory(admin_url: str) -> Any:
    engine = await app_role_engine(admin_url, ["memory_records"])
    yield sessionmaker_for(engine)
    await engine.dispose()


class _Provider:
    def __init__(self, dim: int, model: str) -> None:
        self.dim = dim
        self.embedding_model = model
        self.calls = 0

    async def embed(self, req: Any) -> Any:
        from types import SimpleNamespace

        self.calls += 1
        axis = 5 if "deploy" in req.texts[0] or "release" in req.texts[0] else 9
        return SimpleNamespace(embeddings=[_vec(self.dim, axis)])


def _write(key: str, content: str) -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id=TENANT, memory_kind="reflexion", content=content, source_goal_id="g",
        source_execution_id="e", evidence_refs=("goal://g/outcome/failed",),
        classification="internal", confidence=7000, retention_policy_id="default",
        idempotency_key=key,
    )


async def test_migration_padded_the_legacy_vector(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.connect() as c:
        dims, profile = (
            await c.execute(
                text(
                    "SELECT vector_dims(embedding), embedding_dimension FROM memory_records "
                    "WHERE id = 'legacy1536'"
                )
            )
        ).one()
    await eng.dispose()
    assert (dims, profile) == (2048, 2048)


async def test_2048d_embedder_writes_vectors_and_recalls_semantically(factory: Any) -> None:
    provider = _Provider(2048, "nemotron-2048")
    repo = PostgresMemoryRepository(factory, embedder=ProviderMemoryEmbedder(provider))
    await repo.write(_write("a", "Run the deploy checklist before every release"))
    await repo.write(_write("b", "Summarise the weekly sales numbers"))
    hits = await repo.recall(
        MemoryRecallRequest(
            tenant_id=TENANT, query="ship a release", memory_kinds=frozenset({"reflexion"}),
            top_k=1, min_confidence=1, allowed_data_classes=frozenset({"internal"}),
            as_of=datetime.now(UTC), token_budget=500,
        )
    )
    assert hits and hits[0].record.idempotency_key == "a"
    assert hits[0].record.embedding is not None and len(hits[0].record.embedding) == 2048
    assert await repo.reembed_pending(TENANT, limit=50) == 1  # the legacy (other model) row
    assert await repo.reembed_pending(TENANT, limit=50) == 0


async def test_too_wide_embedder_marks_records_and_the_sweep_moves_on(factory: Any) -> None:
    provider = _Provider(3072, "wide-3072")
    repo = PostgresMemoryRepository(factory, embedder=ProviderMemoryEmbedder(provider))
    first = await repo.reembed_pending(TENANT, limit=50)
    assert first == 0
    calls_after_first = provider.calls
    assert calls_after_first == 3  # every record tried once ...
    assert await repo.reembed_pending(TENANT, limit=50) == 0
    assert provider.calls == calls_after_first  # ... and never again
