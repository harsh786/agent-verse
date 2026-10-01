"""MEM-10 (integration): vector-less / stale-model canonical records get re-embedded.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_canonical_reembed_pg.py -q -m integration
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

TENANT = "mem10-tenant"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


class _Embedder:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.calls = 0

    async def __call__(self, _text: str) -> tuple[float, ...]:
        self.calls += 1
        return tuple([0.25] * 1536)


def _write(i: int):  # type: ignore[no-untyped-def]
    from app.memory.contracts import MemoryWriteRequest

    return MemoryWriteRequest(
        tenant_id=TENANT,
        memory_kind="reflexion",
        content=f"lesson number {i}",
        source_goal_id="g",
        source_execution_id="e",
        evidence_refs=("goal://g/outcome/failed",),
        classification="internal",
        confidence=7000,
        retention_policy_id="default",
        idempotency_key=f"k{i}",
    )


async def test_reembed_fills_null_and_stale_vectors_but_never_sensitive(admin_url: str) -> None:
    from app.memory.postgres_repository import PostgresMemoryRepository

    admin = create_async_engine(admin_url)
    async with admin.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
    engine = await app_role_engine(admin_url, ["memory_records"])
    factory = sessionmaker_for(engine)

    # Two writes while embedding was unavailable (no vector) ...
    plain = PostgresMemoryRepository(factory)
    await plain.write(_write(1))
    await plain.write(_write(2))
    # ... one embedded by a previous model ...
    old = PostgresMemoryRepository(factory, embedder=_Embedder("old-model"))
    await old.write(_write(3))
    # ... and a sealed (sensitive) record that must never be embedded.
    now = datetime.now(UTC)
    async with admin.begin() as c:
        await c.execute(
            text(
                "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, "
                "safe_summary, source_goal_id, source_execution_id, evidence_refs, "
                "classification, sealed_content, confidence, lifecycle_state, version, "
                "embedding_model, embedding_dimension, outcome_score, effectiveness_score, "
                "recall_count, helpful_count, harmful_count, retention_policy_id, "
                "idempotency_key, created_at, updated_at) VALUES ('sealed1', :t, 'reflexion', "
                "'memory://encrypted/sealed1', '[REDACTED]', 'g', 'e', '[\"goal://g\"]', "
                "'confidential', 'ciphertext', 7000, 'active', 1, 'memory-embedding-v1', 1536, "
                "0, 0, 0, 0, 0, 'default', 'ks', :now, :now)"
            ),
            {"t": TENANT, "now": now},
        )

    current = _Embedder("text-embedding-3-small")
    repo = PostgresMemoryRepository(factory, embedder=current)
    assert await repo.reembed_pending(TENANT, limit=50) == 3
    assert await repo.reembed_pending(TENANT, limit=50) == 0  # idempotent

    async with admin.begin() as c:
        rows = dict(
            (
                await c.execute(
                    text(
                        "SELECT idempotency_key, embedding_source_model FROM memory_records "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": TENANT},
                )
            ).fetchall()
        )
    assert rows == {
        "k1": "text-embedding-3-small",
        "k2": "text-embedding-3-small",
        "k3": "text-embedding-3-small",
        "ks": None,
    }
    await engine.dispose()
    await admin.dispose()
