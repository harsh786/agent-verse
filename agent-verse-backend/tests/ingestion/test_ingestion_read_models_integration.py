"""Integration: the formerly-stubbed ingestion/knowledge read paths, against a
real schema (``alembic upgrade head``) under a NOBYPASSRLS application role
(plus a BYPASSRLS maintenance role for the cross-tenant DLQ scan).

Covers: pipeline Stage 1 quota + Stage 6 PII with the real store; the per-Source
document listing, collection counters and source-type sample; the tenant DLQ
listing, retry backoff and monthly token usage; and the semantic-cache pgvector
write-through. Each also proves tenant B cannot see tenant A's rows.

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_ingestion_read_models_integration.py -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pii import RegexPIIAnalyzer
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.quota import IngestionQuotaEnforcer
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.rag.vector_cache_backend import PgVectorCacheBackend
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SSN = "123-45-6789"


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


async def _create_role(conn: Any, role: str, password: str, *, bypass_rls: bool) -> None:
    quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar_one()
    await conn.execute(
        text(
            f"CREATE ROLE {role} LOGIN PASSWORD {quoted} NOSUPERUSER NOCREATEDB NOCREATEROLE "
            + ("BYPASSRLS" if bypass_rls else "NOBYPASSRLS")
        )
    )
    await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
    await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    await conn.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    )


@pytest_asyncio.fixture(scope="function")
async def dbs(postgres_url: str) -> AsyncIterator[SimpleNamespace]:
    suffix = secrets.token_hex(4)
    app_role, maint_role = f"rm_app_{suffix}", f"rm_maint_{suffix}"
    app_pw, maint_pw = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    tenant_a, tenant_b = f"rm-a-{suffix}", f"rm-b-{suffix}"
    async with admin_engine.begin() as conn:
        await _create_role(conn, app_role, app_pw, bypass_rls=False)
        await _create_role(conn, maint_role, maint_pw, bypass_rls=True)
        for tid in (tenant_a, tenant_b):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true)"
                ),
                {"id": tid, "email": f"{tid}@example.test"},
            )

    def _url(role: str, password: str) -> str:
        return (
            make_url(postgres_url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )

    app_engine = create_async_engine(_url(app_role, app_pw), pool_size=4, max_overflow=0)
    maint_engine = create_async_engine(_url(maint_role, maint_pw), pool_size=2, max_overflow=0)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
        maint=async_sessionmaker(maint_engine, expire_on_commit=False),
        a=TenantContext(tenant_a, PlanTier.FREE, "k-a"),
        b=TenantContext(tenant_b, PlanTier.FREE, "k-b"),
    )
    await app_engine.dispose()
    await maint_engine.dispose()
    await admin_engine.dispose()


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.seen.extend(request.texts)
        return EmbedResponse(
            embeddings=[[1.0, float(i), *([0.0] * 766)] for i, _ in enumerate(request.texts)],
            model="fake-768",
        )


def _body(extra: str) -> bytes:
    return (
        "Customer onboarding record for the quarterly review of the operations "
        f"team. The applicant SSN is {_SSN}. {extra} All other fields were verified."
    ).encode()


@pytest.mark.asyncio
async def test_pipeline_pii_quota_and_document_read_models(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KnowledgeStore(dbs.app)
    sources = SourceConfigStore(db=dbs.app)
    col_a = await store.create_collection_async(KnowledgeCollection(name="a"), tenant_ctx=dbs.a)
    col_b = await store.create_collection_async(KnowledgeCollection(name="b"), tenant_ctx=dbs.b)
    src_a = SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}", tenant_id=dbs.a.tenant_id, name="s",
        family=SourceFamily.WEB, source_type="http", collection_id=col_a,
        min_quality_score=0.0,
    )
    await sources.create(src_a)
    embedder = _Embedder()
    quota = IngestionQuotaEnforcer(dbs.app)
    pipeline = IngestionPipeline(
        knowledge_store=store, embedder=embedder,
        pii_analyzer=RegexPIIAnalyzer(), quota_enforcer=quota,
    )

    for doc_id in ("s3://bucket/a.txt", "s3://bucket/b.txt"):
        raw = RawDocument(
            doc_id=doc_id, source_id=src_a.source_id, tenant_id=dbs.a.tenant_id,
            content=_body(doc_id), content_type="text/plain", title=doc_id.upper(),
        )
        result = await pipeline.ingest(raw, src_a)
        assert result.status == "indexed", result

    # Stage 6: the raw SSN never reached the embedder or the chunk table.
    assert embedder.seen and all(_SSN not in t for t in embedder.seen)
    async with dbs.admin() as s:
        stored = (
            await s.execute(
                text("SELECT content FROM knowledge_chunks_768 WHERE tenant_id = :t"),
                {"t": dbs.a.tenant_id},
            )
        ).scalars().all()
    assert stored and all(_SSN not in c for c in stored)
    assert any("[REDACTED:SSN]" in c for c in stored)

    # Documents of the Source, aggregated in SQL, keyset-paginated.
    docs = await store.list_source_documents_async(
        tenant_ctx=dbs.a, collection_id=col_a, source_id=src_a.source_id, limit=10
    )
    assert [d["id"] for d in docs] == ["s3://bucket/a.txt", "s3://bucket/b.txt"]
    assert all(d["has_pii_redacted"] is True and d["chunk_count"] >= 1 for d in docs)
    assert docs[0]["title"] == "S3://BUCKET/A.TXT" and docs[0]["ingested_at"]
    page2 = await store.list_source_documents_async(
        tenant_ctx=dbs.a, collection_id=col_a, source_id=src_a.source_id,
        limit=10, after="s3://bucket/a.txt",
    )
    assert [d["id"] for d in page2] == ["s3://bucket/b.txt"]
    # Tenant B cannot read tenant A's collection.
    assert await store.list_source_documents_async(
        tenant_ctx=dbs.b, collection_id=col_a, source_id=src_a.source_id
    ) == []

    # Collection counters + bounded source-type sample.
    (counters,) = await store.collection_counters_async(tenant_ctx=dbs.a, collection_id=col_a)
    assert counters["document_count"] == 2
    assert counters["chunk_count"] == len(stored)
    assert counters["embedding_dim"] == 768
    assert counters["last_indexed_at"] is not None
    dist, sampled = await store.source_type_sample_async(
        col_a, tenant_ctx=dbs.a, embedding_dim=768
    )
    assert dist == {"http": len(stored)} and sampled == len(stored)
    assert await store.collection_counters_async(tenant_ctx=dbs.b, collection_id=col_a) == []
    assert (await store.collection_counters_async(tenant_ctx=dbs.b))[0]["collection_id"] == col_b

    # Quota usage straight from the DB; Stage 1 enforces the plan document limit.
    usage = await quota.usage(dbs.a.tenant_id)
    assert (usage.plan, usage.sources_used, usage.documents_indexed) == ("free", 1, 2)
    assert (await quota.usage(dbs.b.tenant_id)).sources_used == 0
    monkeypatch.setitem(__import__("app.ingestion.quota").ingestion.quota.DOCUMENT_LIMITS, "free", 2)
    raw = RawDocument(
        doc_id="s3://bucket/c.txt", source_id=src_a.source_id, tenant_id=dbs.a.tenant_id,
        content=_body("third"), content_type="text/plain",
    )
    over = await pipeline.ingest(raw, src_a)
    assert (over.status, over.skip_reason) == ("skipped", "quota_exceeded")


@pytest.mark.asyncio
async def test_dlq_listing_backoff_and_monthly_usage(dbs: SimpleNamespace) -> None:
    sources = SourceConfigStore(db=dbs.app)
    tracker = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)
    src = SourceConfig(
        source_id=f"dlq-{uuid.uuid4().hex[:8]}", tenant_id=dbs.a.tenant_id, name="s",
        family=SourceFamily.WEB, source_type="http",
        # A Source without a target collection is parked (needs_configuration)
        # and the DLQ retry scan rightly skips it.
        collection_id="kb-dlq",
    )
    await sources.create(src)
    await tracker.add_to_dlq(
        source_id=src.source_id, tenant_id=dbs.a.tenant_id, doc_id="d1", error="boom",
        raw_doc={"doc_id": "d1"},
    )
    entries = await tracker.list_dlq_entries(dbs.a.tenant_id)
    assert [(e["doc_id"], e["error_message"], e["retry_count"]) for e in entries] == [
        ("d1", "boom", 0)
    ]
    assert isinstance(entries[0]["created_at"], str)
    assert await tracker.list_dlq_entries(dbs.b.tenant_id) == []

    # A failed retry schedules the next attempt in the future → not rescanned now.
    scan = [e for e in await tracker.get_retryable_dlq_entries() if e["source_id"] == src.source_id]
    assert len(scan) == 1
    await tracker.increment_dlq_retry(scan[0]["dlq_id"], dbs.a.tenant_id, error="again")
    rescan = await tracker.get_retryable_dlq_entries()
    assert all(e["source_id"] != src.source_id for e in rescan)
    (after,) = await tracker.list_dlq_entries(dbs.a.tenant_id)
    assert after["retry_count"] == 1 and after["next_retry_at"] is not None

    # Job tokens are persisted and aggregated for the month.
    job_id = uuid.uuid4().hex
    job = await tracker.create_job(src, job_id=job_id, triggered_by="manual")
    await tracker.increment_counters(job, indexed=2, chunks=5, tokens=1234)
    await tracker.complete_job(job)
    usage = await tracker.monthly_usage(dbs.a.tenant_id)
    assert (usage["jobs"], usage["tokens"], usage["chunks_created"]) == (1, 1234, 5)
    assert (await tracker.monthly_usage(dbs.b.tenant_id))["tokens"] == 0


@pytest.mark.asyncio
async def test_semantic_cache_pgvector_write_through(dbs: SimpleNamespace) -> None:
    from app.rag.semantic_cache import SemanticCache

    backend = PgVectorCacheBackend(dbs.app, ttl_seconds=3600)
    emb = [0.3, 0.4, 0.5, 0.6]
    await SemanticCache(backend=backend).store_async(emb, "q", "durable answer", dbs.a.tenant_id)
    async with dbs.admin() as s:
        count = (
            await s.execute(
                text("SELECT COUNT(*) FROM semantic_cache_entries WHERE tenant_id = :t"),
                {"t": dbs.a.tenant_id},
            )
        ).scalar_one()
    assert count == 1, "store_async never wrote semantic_cache_entries"

    # Another replica (fresh L1, no Redis) hits through the durable backend.
    hit = await SemanticCache(backend=backend).get_similar(emb, dbs.a.tenant_id)
    assert hit is not None and hit.response == "durable answer"
    assert await backend.get_similar(emb, dbs.b.tenant_id) is None  # RLS

    # Expired rows are not served.
    async with dbs.admin() as s, s.begin():
        await s.execute(
            text("UPDATE semantic_cache_entries SET created_at = now() - interval '2 hours'")
        )
    assert await backend.get_similar(emb, dbs.a.tenant_id) is None

    await SemanticCache(backend=backend).store_async(emb, "q", "fresh", dbs.a.tenant_id)
    await SemanticCache(backend=backend).clear_async(tenant_ctx=dbs.a)
    async with dbs.admin() as s:
        left = (
            await s.execute(
                text("SELECT COUNT(*) FROM semantic_cache_entries WHERE tenant_id = :t"),
                {"t": dbs.a.tenant_id},
            )
        ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_sync_status_reads_the_job_the_worker_recorded(dbs: SimpleNamespace) -> None:
    """SRC-MONGO-SYNC: ``GET /sources/{id}/sync/status`` read the API process's
    in-memory job list, but the sync runs in a Celery worker — so the job was
    never visible and the UI showed "never synced" after every sync."""
    sources = SourceConfigStore(db=dbs.app)
    src = SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}", tenant_id=dbs.a.tenant_id, name="m",
        family=SourceFamily.NOSQL_DATABASE, source_type="mongodb",
    )
    await sources.create(src)

    worker = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)
    job = await worker.create_job(src, job_id=str(uuid.uuid4()), triggered_by="manual")
    job.docs_indexed = 7
    await worker.complete_job(job, error="")

    api = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)  # another process
    latest = await api.latest_job(src.source_id, dbs.a.tenant_id)
    assert latest is not None
    assert latest.job_id == job.job_id
    assert latest.status == "completed"
    assert latest.docs_indexed == 7
    assert latest.triggered_by == "manual"
    # RLS: tenant B cannot read tenant A's job.
    assert await api.latest_job(src.source_id, dbs.b.tenant_id) is None


@pytest.mark.asyncio
async def test_stable_id_resync_replaces_the_document_in_postgres(dbs: SimpleNamespace) -> None:
    """STABLE-DOC-IDS: an edited item re-synced under its stable id replaces the
    indexed version atomically — no stale chunks, no unique-key failure on
    (collection, document, chunk_index), counters unchanged — and an unchanged
    item is still a dedup skip."""
    from app.ingestion.base_connector import stable_doc_id

    store = KnowledgeStore(dbs.app)
    col = await store.create_collection_async(KnowledgeCollection(name="r"), tenant_ctx=dbs.a)
    src = SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}", tenant_id=dbs.a.tenant_id, name="s",
        family=SourceFamily.WEB, source_type="http", collection_id=col,
        min_quality_score=0.0, pii_action="allow",
    )
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())
    doc_id = stable_doc_id(src, "PROJ-1")

    def _raw(text_: str) -> RawDocument:
        return RawDocument(
            doc_id=doc_id, source_id=src.source_id, tenant_id=dbs.a.tenant_id,
            content=(text_ * 30).encode(), content_type="text/plain",
        )

    first = await pipeline.ingest(_raw("Original ticket description text. "), src)
    assert first.status == "indexed", first.error
    edited = await pipeline.ingest(_raw("Edited ticket description, new facts. "), src)
    assert edited.status == "indexed", edited.error
    unchanged = await pipeline.ingest(_raw("Edited ticket description, new facts. "), src)
    assert (unchanged.status, unchanged.skip_reason) == ("skipped", "dedup")

    async with dbs.admin() as s:
        contents = (
            await s.execute(
                text("SELECT content FROM knowledge_chunks_768 WHERE document_id = :d"),
                {"d": doc_id},
            )
        ).scalars().all()
        counters = (
            await s.execute(
                text("SELECT document_count, chunk_count FROM knowledge_collections "
                     "WHERE id = :c"),
                {"c": col},
            )
        ).one()
    assert contents and all("Edited" in c for c in contents)
    assert counters[0] == 1
    assert counters[1] == len(contents)
