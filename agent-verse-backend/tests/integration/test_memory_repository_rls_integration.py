"""Integration: PostgresMemoryRepository against real Postgres + pgvector under RLS.

Runs ``alembic upgrade head`` (incl. c8d2f4a6b1e3) in a PostgresContainer and
drives the canonical memory repository as a NOSUPERUSER/NOBYPASSRLS app role —
the production posture — covering persisted scope, sealed sensitive payloads,
SQL-side recall ordering (vector + recency), effectiveness feedback and tenant
isolation.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_memory_repository_rls_integration.py \\
        -m integration --no-cov
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.contracts import MemoryFeedback, MemoryRecallRequest, MemoryWriteRequest
from app.memory.postgres_repository import PostgresMemoryRepository

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _Cipher:
    def encrypt(self, plaintext: str) -> str:
        return plaintext.encode().hex()

    def decrypt(self, ciphertext: str) -> str:
        return bytes.fromhex(ciphertext).decode()


class _AxisEmbedder:
    """Deterministic embedder: a text maps onto the axis named by its first word."""

    model_id = "axis-embedder:v1"
    _axes = {"alpha": 0, "beta": 1, "gamma": 2}

    async def __call__(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * 1536
        word = (text.split() or [""])[0].casefold()
        vec[self._axes.get(word, 3)] = 1.0
        vec[4] = 0.01  # keep every vector non-zero and distinct from a pure axis
        return tuple(vec)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield url


@pytest_asyncio.fixture
async def dbs(postgres_url: str) -> AsyncIterator[tuple[Any, Any]]:
    """(admin, app [NOBYPASSRLS]) session factories."""
    pw = secrets.token_urlsafe(16)
    app_role = f"it_mem_{secrets.token_hex(3)}"
    admin_engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        q = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": pw})).scalar_one()
        await conn.execute(text(f"CREATE ROLE {app_role} LOGIN PASSWORD {q} NOBYPASSRLS"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {app_role}"))
        await conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {app_role}"
            )
        )
    url = make_url(postgres_url).set(username=app_role, password=pw)
    app_engine = create_async_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_tenant(admin: Any) -> str:
    tid = uuid.uuid4().hex
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return tid


def _write(tenant_id: str, content: str, **overrides: Any) -> MemoryWriteRequest:
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "memory_kind": "reflexion",
        "content": content,
        "source_goal_id": uuid.uuid4().hex,
        "source_execution_id": "exec",
        "evidence_refs": ("goal://evidence",),
        "classification": "internal",
        "confidence": 8000,
        "idempotency_key": uuid.uuid4().hex,
        "retention_policy_id": "reflexion-standard",
    }
    values.update(overrides)
    return MemoryWriteRequest(**values)


def _recall(tenant_id: str, query: str, **overrides: Any) -> MemoryRecallRequest:
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "query": query,
        "memory_kinds": frozenset({"reflexion"}),
        "top_k": 5,
        "min_confidence": 1,
        "allowed_data_classes": frozenset({"public", "internal"}),
        "as_of": datetime.now(UTC),
        "token_budget": 1_000,
    }
    values.update(overrides)
    return MemoryRecallRequest(**values)


@pytest.mark.asyncio
async def test_scope_fields_persist_and_scope_recall_under_rls(dbs: tuple[Any, Any]) -> None:
    admin, app = dbs
    tid = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, cipher=_Cipher())
    mine = await repo.write(
        _write(tid, "retry the deploy", agent_id="agent-a", collection_id="c1", source="goal")
    )
    await repo.write(_write(tid, "retry the deploy slowly", agent_id="agent-b"))

    async with admin() as s:
        row = (
            await s.execute(
                text(
                    "SELECT agent_id, collection_id, source FROM memory_records WHERE id = :i"
                ),
                {"i": mine.memory_id},
            )
        ).one()
    assert tuple(row) == ("agent-a", "c1", "goal")

    hits = await repo.recall(_recall(tid, "retry the deploy", agent_id="agent-a"))
    assert [h.record.memory_id for h in hits] == [mine.memory_id]
    assert hits[0].record.agent_id == "agent-a"


@pytest.mark.asyncio
async def test_vector_recall_orders_by_similarity_in_sql(dbs: tuple[Any, Any]) -> None:
    admin, app = dbs
    tid = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, embedder=_AxisEmbedder(), cipher=_Cipher())
    alpha = await repo.write(_write(tid, "alpha lesson about caching"))
    await repo.write(_write(tid, "beta lesson about retries"))
    await repo.write(_write(tid, "gamma lesson about quotas"))

    async with admin() as s:
        stored_model = (
            await s.execute(
                text("SELECT embedding_source_model FROM memory_records WHERE id = :i"),
                {"i": alpha.memory_id},
            )
        ).scalar_one()
    assert stored_model == "axis-embedder:v1"

    hits = await repo.recall(_recall(tid, "alpha", top_k=3))
    assert hits[0].record.memory_id == alpha.memory_id
    assert hits[0].semantic_score > hits[-1].semantic_score


@pytest.mark.asyncio
async def test_recency_bounds_candidates_and_excludes_ineligible_rows(
    dbs: tuple[Any, Any],
) -> None:
    admin, app = dbs
    tid = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, cipher=_Cipher())
    old = await repo.write(_write(tid, "zeta note"))
    new = await repo.write(_write(tid, "zeta note"))
    expired = await repo.write(_write(tid, "zeta note"))
    quarantined = await repo.write(_write(tid, "zeta note", evidence_refs=()))
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE memory_records SET updated_at = :t WHERE id = :i"),
            {"t": datetime.now(UTC) - timedelta(days=30), "i": old.memory_id},
        )
        await s.execute(
            text("UPDATE memory_records SET expires_at = :t WHERE id = :i"),
            {"t": datetime.now(UTC) - timedelta(days=1), "i": expired.memory_id},
        )

    hits = await repo.recall(_recall(tid, "zeta note"))
    ids = [h.record.memory_id for h in hits]
    assert ids == [new.memory_id, old.memory_id]  # newest first on equal relevance
    assert expired.memory_id not in ids and quarantined.memory_id not in ids


@pytest.mark.asyncio
async def test_sensitive_payload_is_sealed_at_rest_and_round_trips(
    dbs: tuple[Any, Any],
) -> None:
    admin, app = dbs
    tid = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, cipher=_Cipher())
    record = await repo.write(
        _write(tid, "customer SSN lives in vault 7", classification="restricted")
    )
    async with admin() as s:
        sealed, summary = (
            await s.execute(
                text("SELECT sealed_content, safe_summary FROM memory_records WHERE id = :i"),
                {"i": record.memory_id},
            )
        ).one()
    assert sealed.startswith("enc:v1:") and "vault 7" not in sealed
    assert summary == "[REDACTED]"
    assert await repo.read_sensitive_content(tid, record.memory_id) == (
        "customer SSN lives in vault 7"
    )

    # The DB refuses a sensitive row without a sealed payload (no dangling ref).
    with pytest.raises(Exception, match="ck_memory_sensitive_sealed"):
        async with admin() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE memory_records SET sealed_content = NULL WHERE id = :i"
                ),
                {"i": record.memory_id},
            )


@pytest.mark.asyncio
async def test_feedback_updates_effectiveness_under_rls(dbs: tuple[Any, Any]) -> None:
    admin, app = dbs
    tid = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, cipher=_Cipher())
    record = await repo.write(_write(tid, "check quotas first"))
    updated = await repo.feedback(
        MemoryFeedback(
            memory_id=record.memory_id,
            tenant_id=tid,
            execution_id="goal-2",
            was_used=True,
            was_helpful=True,
            was_harmful=False,
            outcome_score=5000,
            feedback_reason="goal complete",
            recorded_at=datetime.now(UTC),
        )
    )
    assert updated.helpful_count == 1 and updated.effectiveness_score > 0
    async with admin() as s:
        helpful = (
            await s.execute(
                text("SELECT helpful_count FROM memory_records WHERE id = :i"),
                {"i": record.memory_id},
            )
        ).scalar_one()
    assert helpful == 1


@pytest.mark.asyncio
async def test_tenants_cannot_see_each_others_memory(dbs: tuple[Any, Any]) -> None:
    admin, app = dbs
    tenant_a = await _seed_tenant(admin)
    tenant_b = await _seed_tenant(admin)
    repo = PostgresMemoryRepository(app, cipher=_Cipher())
    record = await repo.write(_write(tenant_a, "tenant a private lesson"))
    secret = await repo.write(
        _write(tenant_a, "tenant a sealed", classification="confidential")
    )

    assert await repo.recall(_recall(tenant_b, "tenant a private lesson")) == ()
    with pytest.raises(KeyError):
        await repo.read_sensitive_content(tenant_b, secret.memory_id)
    # Even an explicit cross-tenant id lookup under B's RLS context sees nothing.
    async with app() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
        visible = (
            await s.execute(
                text("SELECT count(*) FROM memory_records WHERE id = :i"),
                {"i": record.memory_id},
            )
        ).scalar_one()
    assert visible == 0


@pytest.mark.asyncio
async def test_backfill_checkpoint_table_is_rls_forced(dbs: tuple[Any, Any]) -> None:
    admin, _ = dbs
    async with admin() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('memory_records', 'memory_backfill_checkpoints')"
                )
            )
        ).all()
    assert {r[0]: (r[1], r[2]) for r in rows} == {
        "memory_records": (True, True),
        "memory_backfill_checkpoints": (True, True),
    }
