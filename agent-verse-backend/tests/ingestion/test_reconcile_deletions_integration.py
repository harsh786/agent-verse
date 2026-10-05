"""KB-44 on real Postgres (app role, RLS): upstream-deletion reconciliation at scale.

The upstream listing (120,000 ids here) is streamed into the UNLOGGED
``ingestion_live_listings`` table in bounded batches and diffed in SQL, so the
process never holds the bucket listing. Exactly the documents deleted upstream
are removed — documents under legal hold survive, and so does a document
indexed while the run was listing (it is not in the listing, but it is new, not
gone). Deletes are capped per run; the next run continues.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_reconcile_deletions_integration.py -m integration
"""

from __future__ import annotations

import json
import tracemalloc
import uuid
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.scheduler import _reconcile_upstream_deletions
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_LISTED = 120_000  # upstream ids streamed per run
_INDEXED_EVERY = 12  # every 12th listed key is indexed → 10,000 live documents
_GONE = 600  # indexed documents deleted upstream


def _key(i: int) -> str:
    return f"s3://b/key-{i:07d}"


def _gone_key(i: int) -> str:
    return f"s3://b/gone-{i:05d}"


class _Bucket(BaseConnector):
    """A lazily generated listing; ``on_yield`` runs side effects mid-listing."""

    source_type = "fake-bucket"

    def __init__(
        self,
        total: int,
        on_yield: Callable[[int], Any] | None = None,
        extra: tuple[str, ...] = (),
    ) -> None:
        self.total = total
        self.yielded = 0
        self.on_yield = on_yield
        self.extra = extra

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        return ConnectionHealth(ok=True)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        return
        yield  # pragma: no cover

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        for i in range(self.total):
            if self.on_yield is not None:
                await self.on_yield(i)
            self.yielded += 1
            yield _key(i)
        for doc_id in self.extra:
            yield doc_id

    def manages_doc_id(self, doc_id: str) -> bool:
        return doc_id.startswith("s3://")


async def _bulk_index(pg_url: str, tid: str, cid: str, sql_doc_id: str, count: int) -> None:
    """Insert ``count`` one-chunk documents of Source ``src-1`` (admin, set-based)."""
    await admin_exec(
        pg_url,
        "INSERT INTO knowledge_chunks_768 (id, tenant_id, collection_id, document_id, "
        "chunk_index, content, content_hash, metadata, domain_metadata, hierarchy_level, "
        "is_proposition, strategy_metadata, embedding) "
        f"SELECT md5(:t || {sql_doc_id}), :t, :c, {sql_doc_id}, 0, 'body', md5({sql_doc_id}), "
        "jsonb_build_object('source_id', 'src-1'), CAST('{}' AS jsonb), 0, false, "
        "CAST('{}' AS jsonb), CAST(:vec AS vector) FROM generate_series(0, :n - 1) g",
        {"t": tid, "c": cid, "n": count, "vec": json.dumps([0.01] * 768)},
    )


async def _indexed(pg_url: str, cid: str) -> set[str]:
    rows = await admin_exec(
        pg_url,
        "SELECT DISTINCT document_id FROM knowledge_chunks_768 WHERE collection_id = :c",
        {"c": cid},
    )
    return {str(r[0]) for r in rows}


def _config(tid: str, cid: str) -> SourceConfig:
    return SourceConfig(
        source_id="src-1",
        tenant_id=tid,
        name="s",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="fake-bucket",
        collection_id=cid,
    )


async def test_a_120k_listing_deletes_exactly_the_gone_documents_in_bounded_memory(
    pg_url: str,
) -> None:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        store = KnowledgeStore(sessions(engine), embedding_dim=768)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)
        live_count = _LISTED // _INDEXED_EVERY
        await _bulk_index(
            pg_url, tid, cid,
            f"'s3://b/key-' || lpad((g * {_INDEXED_EVERY})::text, 7, '0')", live_count,
        )
        await _bulk_index(pg_url, tid, cid, "'s3://b/gone-' || lpad(g::text, 5, '0')", _GONE)
        held = {_gone_key(7), _gone_key(300), _gone_key(599)}
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'matter', 'document', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps(sorted(held))},
        )
        live = {_key(i * _INDEXED_EVERY) for i in range(live_count)}
        assert await _indexed(pg_url, cid) == live | {_gone_key(i) for i in range(_GONE)}

        # A sync indexes a NEW upstream object while the run is listing: it is not
        # in this run's listing, but it is not gone either — it must survive.
        mid_run = "s3://b/aa-indexed-mid-run"  # sorts first: run 1 meets it

        async def _index_mid_run(i: int) -> None:
            if i == _LISTED // 2:
                await _bulk_index(pg_url, tid, cid, f"'{mid_run}' || ''", 1)

        pipeline = SimpleNamespace(_kb=store)
        # Warm SQLAlchemy/asyncpg caches so the measurement below is the run itself.
        await _reconcile_upstream_deletions(_Bucket(10), _config(tid, "absent"), pipeline)

        # Instrument staging: how many listed ids were ever held unstaged.
        staged = {"n": 0, "max_batch": 0, "max_unstaged": 0}
        bucket = _Bucket(_LISTED, _index_mid_run)
        original_stage = store.stage_live_doc_ids_async

        async def _stage(run_id: str, ids: list[str], **kw: Any) -> None:
            staged["max_unstaged"] = max(staged["max_unstaged"], bucket.yielded - staged["n"])
            await original_stage(run_id, ids, **kw)
            staged["n"] += len(ids)
            staged["max_batch"] = max(staged["max_batch"], len(ids))

        store.stage_live_doc_ids_async = _stage  # type: ignore[method-assign]

        # Run 1: capped at 250 deletions.
        tracemalloc.start()
        try:
            first = await _reconcile_upstream_deletions(
                bucket, _config(tid, cid), pipeline, max_deletes=250
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert staged["n"] == _LISTED
        assert staged["max_batch"] <= 1000
        assert staged["max_unstaged"] <= 1000
        # A whole-listing set of 120k ids alone is > 10 MB; the run stays far below.
        assert peak < 6 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"
        assert first["deleted"] == 250 and first["truncated"] == 1
        assert mid_run in await _indexed(pg_url, cid)

        # Run 2 finishes the job (the object indexed mid-run 1 is listed now);
        # held documents survive every run.
        second = await _reconcile_upstream_deletions(
            _Bucket(_LISTED, extra=(mid_run,)), _config(tid, cid), pipeline
        )
        assert second["deleted"] == _GONE - len(held) - 250
        assert second["kept_held"] == len(held)
        assert second["truncated"] == 0

        assert await _indexed(pg_url, cid) == live | held | {mid_run}
        # Staging is cleaned up after each run.
        left = await admin_exec(
            pg_url,
            "SELECT count(*) FROM ingestion_live_listings WHERE tenant_id = :t",
            {"t": tid},
        )
        assert int(left[0][0]) == 0
    finally:
        await engine.dispose()


async def test_a_collection_hold_keeps_every_document_gone_upstream(pg_url: str) -> None:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        store = KnowledgeStore(sessions(engine), embedding_dim=768)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)
        await _bulk_index(pg_url, tid, cid, "'s3://b/gone-' || lpad(g::text, 5, '0')", 40)
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'matter', 'collection', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps([cid])},
        )
        counts = await _reconcile_upstream_deletions(
            _Bucket(100), _config(tid, cid), SimpleNamespace(_kb=store), page_size=7
        )
        assert counts["deleted"] == 0 and counts["kept_held"] == 40
        assert len(await _indexed(pg_url, cid)) == 40
    finally:
        await engine.dispose()


async def test_staged_listings_are_tenant_isolated(pg_url: str) -> None:
    tid, other = str(uuid.uuid4()), str(uuid.uuid4())
    for t in (tid, other):
        await seed_tenant(pg_url, t)
    engine = await app_engine(pg_url)
    try:
        store = KnowledgeStore(sessions(engine), embedding_dim=768)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        octx = TenantContext(other, PlanTier.FREE, "k")
        await store.begin_live_listing_async("run-x", tenant_ctx=ctx)
        await store.stage_live_doc_ids_async("run-x", ["s3://b/a"], tenant_ctx=ctx)
        # The other tenant cannot clear (or see) this tenant's staged run.
        await store.clear_live_listing_async("run-x", tenant_ctx=octx)
        rows = await admin_exec(
            pg_url,
            "SELECT count(*) FROM ingestion_live_listings WHERE tenant_id = :t "
            "AND run_id = 'run-x'",
            {"t": tid},
        )
        assert int(rows[0][0]) == 1
        # RLS refuses a write stamped with another tenant's id.
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with sessions(engine)() as session, session.begin():
            async with sqlalchemy_rls_context(session, other):
                with pytest.raises(Exception, match="row-level security"):
                    await session.execute(
                        text(
                            "INSERT INTO ingestion_live_listings (tenant_id, run_id, doc_id) "
                            "VALUES (:t, 'run-y', 'x')"
                        ),
                        {"t": tid},
                    )
        await store.clear_live_listing_async("run-x", tenant_ctx=ctx)
    finally:
        await engine.dispose()


async def test_ingest_path_chunks_are_reconciled_too(pg_url: str) -> None:
    """Documents written by the real ingest path (not bulk SQL) are candidates."""
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        store = KnowledgeStore(sessions(engine), embedding_dim=768)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)
        for doc in (_key(0), _key(1), "s3://b/ünïcode-gone"):
            await store.ingest_chunks_async(
                [
                    Chunk(
                        document_id=doc,
                        content=f"body {doc}",
                        embedding=[0.01] * 768,
                        chunk_index=0,
                        metadata={"source_id": "src-1"},
                    )
                ],
                collection_id=cid,
                tenant_ctx=ctx,
            )
        counts = await _reconcile_upstream_deletions(
            _Bucket(2), _config(tid, cid), SimpleNamespace(_kb=store)
        )
        assert counts["deleted"] == 1
        assert await _indexed(pg_url, cid) == {_key(0), _key(1)}
    finally:
        await engine.dispose()
