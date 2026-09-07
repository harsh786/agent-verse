"""Integration tests for the data-subject deletion cascade (P0-2).

Proves that ``DeletionOrchestrator.execute_deletion`` performs a real,
verifiable GDPR/DPDP right-to-erasure cascade across every store that
supports subject-scoped deletion, that an active legal hold SUSPENDS the
cascade (never destroys held data), and that an independent re-scan
(``verify_deleted``) returns no residue.

Requires a real PostgreSQL container (pgvector) with all migrations applied
and a NOBYPASSRLS runtime role so Row-Level Security is genuinely enforced.

Run with:  uv run pytest tests/lifecycle/test_deletion_cascade.py -m integration
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.governance.audit_v3 import AuditV3
from app.lifecycle.deletion_orchestrator import DeletionOrchestrator
from app.lifecycle.deletion_receipt import DeletionReceipt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Stores that genuinely support subject-scoped deletion (must show counts > 0).
_SUBJECT_SCOPED_KEYS = {
    "documents",
    "knowledge_chunks",
    "memory_episodic",
    "memory_canonical",
    "memory_long_term",
    "goal_feedback",
    "goals",
    "knowledge_graph_nodes",
    "dpdp_consents",
}


def _zeros_vector(dim: int) -> str:
    return "[" + ",".join(["0"] * dim) + "]"


def _runtime_url(admin_url: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username="del_runtime", password=password)
        .render_as_string(hide_password=False)
    )


async def _prepare_runtime_role(admin_url: str, password: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.begin() as connection:
        quoted = (
            await connection.execute(
                text("SELECT quote_literal(:pw)"), {"pw": password}
            )
        ).scalar_one()
        await connection.execute(
            text(
                "CREATE ROLE del_runtime LOGIN PASSWORD "
                f"{quoted} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await connection.execute(text("GRANT CONNECT ON DATABASE test TO del_runtime"))
        await connection.execute(text("GRANT USAGE ON SCHEMA public TO del_runtime"))
        await connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                "IN SCHEMA public TO del_runtime"
            )
        )
        await connection.execute(
            text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO del_runtime")
        )
    await engine.dispose()


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        environment = {**os.environ, "DATABASE_URL": admin_url}
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factories(
    postgres_url: str,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]]]:
    password = secrets.token_urlsafe(24)
    await _prepare_runtime_role(postgres_url, password)
    admin_engine = create_async_engine(postgres_url)
    runtime_engine = create_async_engine(_runtime_url(postgres_url, password), pool_size=5)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(runtime_engine, expire_on_commit=False),
    )
    await runtime_engine.dispose()
    await admin_engine.dispose()


async def _seed_subject(
    admin: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    subject_ref: str,
) -> str:
    """Seed one subject's personal data across every covered store.

    Returns the goal_id anchoring the subject's goal-linked data.
    """
    goal_id = uuid.uuid4().hex
    coll_id = uuid.uuid4().hex
    doc_id = uuid.uuid4().hex
    ctx = json.dumps({"data_principal_id": subject_ref, "note": "seeded"})
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'enterprise', true) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
        # Goal anchoring subject-linked data (subject_ref lives in execution_context)
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, execution_context) "
                "VALUES (:id, :tid, :gt, CAST(:ctx AS JSON))"
            ),
            {"id": goal_id, "tid": tenant_id, "gt": "seeded goal", "ctx": ctx},
        )
        await s.execute(
            text(
                "INSERT INTO goal_feedback (id, goal_id, tenant_id, rating) "
                "VALUES (:id, :gid, :tid, 1)"
            ),
            {"id": uuid.uuid4().hex, "gid": goal_id, "tid": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO episodic_memories (id, tenant_id, goal_id, goal_text) "
                "VALUES (:id, :tid, :gid, :gt)"
            ),
            {"id": uuid.uuid4().hex, "tid": tenant_id, "gid": goal_id, "gt": "episode"},
        )
        await s.execute(
            text(
                "INSERT INTO memory_records "
                "(id, tenant_id, memory_kind, content_ref, safe_summary, source_goal_id, "
                " source_execution_id, evidence_refs, classification, confidence, "
                " lifecycle_state, version, embedding_model, embedding_dimension, "
                " outcome_score, effectiveness_score, recall_count, helpful_count, "
                " harmful_count, retention_policy_id, idempotency_key, created_at, updated_at) "
                "VALUES (:id, :tid, 'episodic', 'ref', 'summary', :gid, 'exec-1', "
                " CAST('[]' AS JSONB), 'internal', 80, 'active', 1, 'voyage', 1536, "
                " 0, 0, 0, 0, 0, 'default', :idem, now(), now())"
            ),
            {
                "id": uuid.uuid4().hex,
                "tid": tenant_id,
                "gid": goal_id,
                "idem": uuid.uuid4().hex,
            },
        )
        await s.execute(
            text(
                "INSERT INTO long_term_memory (id, tenant_id, content, source_goal_id) "
                "VALUES (:id, :tid, :content, :gid)"
            ),
            {"id": uuid.uuid4().hex, "tid": tenant_id, "content": "ltm", "gid": goal_id},
        )
        # Knowledge collection + document (+ inline embedding) tagged with subject_ref
        await s.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, name) "
                "VALUES (:id, :tid, 'seed-coll')"
            ),
            {"id": coll_id, "tid": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO documents "
                "(id, collection_id, tenant_id, source, content, content_hash, metadata) "
                "VALUES (:id, :cid, :tid, 'seed', 'body', :h, CAST(:md AS JSONB))"
            ),
            {
                "id": doc_id,
                "cid": coll_id,
                "tid": tenant_id,
                "h": uuid.uuid4().hex,
                "md": json.dumps({"subject_ref": subject_ref}),
            },
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_chunks_768 "
                "(id, tenant_id, collection_id, document_id, chunk_index, content, "
                " content_hash, embedding, metadata) "
                "VALUES (:id, :tid, :cid, :did, 0, 'chunk', :h, CAST(:emb AS vector), "
                " CAST(:md AS JSONB))"
            ),
            {
                "id": uuid.uuid4().hex,
                "tid": tenant_id,
                "cid": coll_id,
                "did": doc_id,
                "h": uuid.uuid4().hex,
                "emb": _zeros_vector(768),
                "md": json.dumps({"subject_ref": subject_ref}),
            },
        )
        # Knowledge-graph node derived from the subject's goal
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, tenant_id, node_type, label, source_id) "
                "VALUES (:id, :tid, 'entity', 'seed-node', :gid)"
            ),
            {"id": uuid.uuid4().hex, "tid": tenant_id, "gid": goal_id},
        )
        # Direct DPDP consent for the data principal
        await s.execute(
            text(
                "INSERT INTO dpdp_consents (id, tenant_id, data_principal_id, purpose, "
                " consent_given) VALUES (:id, :tid, :dpid, 'marketing', true)"
            ),
            {"id": uuid.uuid4().hex, "tid": tenant_id, "dpid": subject_ref},
        )
    return goal_id


async def test_execute_deletion_cascades_and_verifies(
    factories: tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]],
) -> None:
    admin, runtime = factories
    suffix = uuid.uuid4().hex[:16]
    tenant_id = f"del-{suffix}"
    subject_ref = f"subject-{suffix}"
    # A second tenant + subject that must be UNTOUCHED (isolation proof).
    other_tenant = f"other-{suffix}"
    other_subject = f"osubj-{suffix}"

    await _seed_subject(admin, tenant_id=tenant_id, subject_ref=subject_ref)
    await _seed_subject(admin, tenant_id=other_tenant, subject_ref=other_subject)

    audit = AuditV3()
    orch = DeletionOrchestrator(db_factory=runtime, audit=audit)

    receipt = await orch.execute_deletion(tenant_id, subject_ref)

    assert isinstance(receipt, DeletionReceipt)
    assert receipt.suspended is False
    assert receipt.subject_ref == subject_ref
    assert receipt.total_deleted > 0
    # Every subject-scoped store deleted at least one row.
    for key in _SUBJECT_SCOPED_KEYS:
        assert receipt.per_store.get(key, 0) >= 1, (
            f"expected deletion in {key}: {receipt.per_store}"
        )
    assert receipt.verified is True

    # Independent re-scan: nothing survives for this subject.
    residue = await orch.verify_deleted(tenant_id, subject_ref)
    assert residue == {}, f"unexpected residue: {residue}"

    # Audit entry emitted.
    assert any(r.action == "data_subject_deletion" for r in audit._records)

    # Isolation: the other tenant's data is intact.
    other_residue = await orch.verify_deleted(other_tenant, other_subject)
    assert other_residue.get("goals", 0) >= 1
    assert other_residue.get("dpdp_consents", 0) >= 1


async def test_legal_hold_suspends_deletion(
    factories: tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]],
) -> None:
    admin, runtime = factories
    suffix = uuid.uuid4().hex[:16]
    tenant_id = f"hold-{suffix}"
    subject_ref = f"held-{suffix}"
    await _seed_subject(admin, tenant_id=tenant_id, subject_ref=subject_ref)

    # Place an active legal hold covering the subject (as a user id).
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO legal_holds (id, tenant_id, name, resource_type, user_ids, status) "
                "VALUES (:id, :tid, 'litigation', 'data_subject', CAST(:uids AS JSONB), 'active')"
            ),
            {
                "id": uuid.uuid4().hex,
                "tid": tenant_id,
                "uids": json.dumps([subject_ref]),
            },
        )

    orch = DeletionOrchestrator(db_factory=runtime, audit=AuditV3())
    receipt = await orch.execute_deletion(tenant_id, subject_ref)

    assert receipt.suspended is True
    assert receipt.total_deleted == 0
    assert receipt.suspension_reason
    # Held data must still exist.
    residue = await orch.verify_deleted(tenant_id, subject_ref)
    assert residue.get("goals", 0) >= 1
    assert residue.get("dpdp_consents", 0) >= 1


async def test_dry_run_reports_without_deleting(
    factories: tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]],
) -> None:
    admin, runtime = factories
    suffix = uuid.uuid4().hex[:16]
    tenant_id = f"dry-{suffix}"
    subject_ref = f"drys-{suffix}"
    await _seed_subject(admin, tenant_id=tenant_id, subject_ref=subject_ref)

    orch = DeletionOrchestrator(db_factory=runtime, audit=AuditV3())
    receipt = await orch.execute_deletion(tenant_id, subject_ref, dry_run=True)

    assert receipt.total_deleted > 0  # would-delete counts
    # Data still present after a dry run.
    residue = await orch.verify_deleted(tenant_id, subject_ref)
    assert residue.get("goals", 0) >= 1
