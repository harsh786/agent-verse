"""D2 on a real MongoDB + Postgres: legacy (v5) copies end as exactly one current copy.

A MongoDB Source whose documents were indexed under the pre-v8 host-based ids is
synced after the upgrade. The sync's first run of the one-time migration
re-reads them and leaves every document with exactly one copy under its v8 id:
an UPDATED document (with and without its v8 copy already indexed), an
unchanged one; a document deleted upstream loses its stale copy; a document
under legal hold is untouched. Postgres runs as a least-privilege NOBYPASSRLS
role, so every statement is under the tenant's RLS. The maintenance task then
re-passes the unclean finish idempotently, and the dispatcher finds the Source.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_mongodb_id_migration_integration.py -m integration
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from bson import ObjectId
from pymongo import MongoClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.mongodb import MongoDbContainer  # type: ignore[import-untyped]

from app.ingestion.connectors import mongodb_connector as mod
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_DIM = 768
_FILL = " The ticket keeps enough words to pass the pipeline minimum text length." * 3


@pytest.fixture(scope="module")
def mongo() -> Iterator[tuple[str, int]]:
    with MongoDbContainer("mongo:7.0", username="root", password="pw", dbname="shop") as c:
        yield c.get_container_host_ip(), int(c.get_exposed_port(27017))


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


def _legacy(oid: ObjectId) -> tuple[str, str]:
    url = "mongodb://legacy-host:27017/shop/tickets"
    key = str(oid)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}/{key}")), f"{url}/{quote(key, safe='')}"


def _text(doc: dict[str, Any]) -> str:
    text, _truncated = mod._flatten({**doc, "_id": str(doc["_id"])})
    return text


async def _docs(pg_url: str, tid: str, cid: str) -> dict[str, str]:
    rows = await admin_exec(
        pg_url,
        f"SELECT document_id, string_agg(content, ' ' ORDER BY chunk_index) "
        f"FROM knowledge_chunks_{_DIM} WHERE tenant_id = :t AND collection_id = :c "
        "GROUP BY document_id",
        {"t": tid, "c": cid},
    )
    return {str(r[0]): str(r[1]) for r in rows}


async def test_legacy_copies_end_as_exactly_one_current_copy(
    mongo: tuple[str, int], pg_url: str
) -> None:
    from app.ingestion.scheduler import (
        _dispatch_mongodb_doc_id_migrations_async,
        _migrate_mongodb_doc_ids_source_async,
        _sync_source_async,
    )

    host, port = mongo
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    admin = create_async_engine(pg_url)
    try:
        db = sessions(engine)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        store = KnowledgeStore(db, embedding_dim=_DIM)
        cid = await store.create_collection_async(KnowledgeCollection(name="kb"), tenant_ctx=ctx)
        source_store = SourceConfigStore(db=db)
        config = SourceConfig(
            source_id=str(uuid.uuid4()), tenant_id=tid, name="tickets",
            family=SourceFamily.NOSQL_DATABASE, source_type="mongodb", collection_id=cid,
            min_quality_score=0.0,
            connection_config={"uri": f"mongodb://{host}:{port}/shop", "username": "root",
                               "password": "pw", "database": "shop", "collection": "tickets"},
        )
        await source_store.create(config)
        pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())

        names = ("updated", "updated_seen", "unchanged", "gone", "held")
        oids = {n: ObjectId() for n in names}
        client: MongoClient[dict[str, Any]] = MongoClient(
            host, port, username="root", password="pw", serverSelectionTimeoutMS=30000)
        tickets = client["shop"]["tickets"]
        tickets.delete_many({})
        old = {n: {"_id": oids[n], "title": f"{n} v1", "body": "before the upgrade" + _FILL}
               for n in names}
        tickets.insert_many(list(old.values()))

        # Indexed by the pre-v8 release: one legacy (v5) copy each.
        legacy = {n: _legacy(oids[n]) for n in names}
        for n in names:
            text = _text(old[n])
            await store.ingest_chunks_async(
                [Chunk(document_id=legacy[n][0], content=text, embedding=[0.2] * _DIM,
                       chunk_index=0,
                       metadata={"source_id": config.source_id, "source_url": legacy[n][1],
                                 "doc_content_hash": hashlib.sha256(text.encode()).hexdigest()})],
                collection_id=cid, tenant_ctx=ctx)

        # After the upgrade: edits upstream, one deletion, one legal hold.
        for n in ("updated", "updated_seen", "held"):
            tickets.update_one({"_id": oids[n]}, {"$set": {"title": f"{n} v2 edited"}})
        tickets.delete_one({"_id": oids["gone"]})
        settings = mod._settings(config.connection_config)
        seen = tickets.find_one({"_id": oids["updated_seen"]})
        assert seen is not None
        # The v8 copy of one edited document was already indexed (change stream).
        indexed = await pipeline.ingest(
            mod._raw_document(config, settings, "tickets", seen), config)
        assert indexed.status == "indexed", indexed
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'matter', 'document', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps([legacy["held"][0]])},
        )
        # The Source's cursor is past every document: the delta itself reads nothing.
        last = max(oids.values())
        await source_store.update(config.source_id, tid, cursor_value=mod._encode_cursor(
            settings, {"tickets": {"value": last, "_id": last}}))
        client.close()

        tracker = IngestionJobTracker(db=db, system_db=db)
        task = MagicMock()
        task.retry.side_effect = lambda exc=None, **_kw: exc or RuntimeError("retry")
        with (
            patch("app.ingestion.scheduler._build_worker_ingestion",
                  return_value=(tracker, pipeline, source_store)),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
        ):
            result = await _sync_source_async(
                task=task, source_id=config.source_id, tenant_id=tid, triggered_by="test")
        assert result.get("docs_failed") == 0, result

        docs = await _docs(pg_url, tid, cid)
        v8 = {n: mod._doc_id(config, "tickets", oids[n]) for n in names}
        for n in ("updated", "updated_seen", "unchanged"):
            copies = [d for d, text in docs.items() if f"{n} v" in text]
            assert copies == [v8[n]], (n, copies)
        assert "updated v2 edited" in docs[v8["updated"]]
        assert "updated v1" not in " ".join(docs.values())
        assert not [d for d, text in docs.items() if "gone v" in text]
        assert [d for d, text in docs.items() if "held v" in text] == [legacy["held"][0]]
        assert len(docs) == 4

        state = (await admin_exec(
            pg_url,
            "SELECT status, migrated, deleted, held, skipped, failed, cursor "
            "FROM ingestion_doc_id_migrations WHERE tenant_id = :t AND source_id = :s",
            {"t": tid, "s": config.source_id},
        ))[0]
        assert tuple(state[:6]) == ("completed_with_issues", 3, 1, 1, 0, 0), state

        # The maintenance task re-passes the unclean finish: the held copy stays.
        with patch("app.ingestion.scheduler._build_worker_ingestion",
                   return_value=(tracker, pipeline, source_store)):
            again = await _migrate_mongodb_doc_ids_source_async(
                task=task, source_id=config.source_id, tenant_id=tid)
        assert again["ran"] is True and again["held"] == 1 and again["migrated"] == 0
        assert await _docs(pg_url, tid, cid) == docs

        # ... and the dispatcher (maintenance role, cross-tenant) still lists it.
        with patch("app.ingestion.scheduler.migrate_mongodb_doc_ids_source_task") as per_source:
            out = await _dispatch_mongodb_doc_id_migrations_async(
                system_db=async_sessionmaker(admin, expire_on_commit=False))
        queued = [c.kwargs["kwargs"] for c in per_source.apply_async.call_args_list]
        assert {"source_id": config.source_id, "tenant_id": tid} in queued
        assert out["queued"] == len(queued)
    finally:
        await engine.dispose()
        await admin.dispose()
