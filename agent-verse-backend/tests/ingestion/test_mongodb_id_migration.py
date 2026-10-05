"""D2 — one-time reindex of MongoDB documents still under pre-v8 (host-based) ids.

Unit level (in-memory store, real pipeline, fake connector). The container test
(real MongoDB + Postgres) is ``test_mongodb_id_migration_integration.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from urllib.parse import quote

import pytest

from app.ingestion.mongodb_id_migration import (
    MODE_MAINTENANCE,
    MODE_SYNC,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_ISSUES,
    STATUS_RUNNING,
    MigrationState,
    MigrationStateStore,
    legacy_ref,
    migrate_legacy_mongodb_ids,
    should_run,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import DuplicateContentError, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_TENANT = "tid-d2"
_SOURCE = "src-mongo"
_CTX = TenantContext(tenant_id=_TENANT, plan=PlanTier.FREE, api_key_id="k")
_DIM = 768


def _legacy(key: str, *, host: str = "old-host:27017", db: str = "shop",
            coll: str = "tickets") -> tuple[str, str]:
    url = f"mongodb://{host}/{db}/{coll}"
    return (str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}/{key}")),
            f"{url}/{quote(key, safe='')}")


def _v8(key: str) -> str:
    return str(uuid.UUID(int=(int(hashlib.sha256(key.encode()).hexdigest()[:32], 16)
                              & ~(0xF << 76)) | (8 << 76)))


_FILL = " — the ticket body keeps enough words to pass the minimum text length." * 3


def _body(text: str) -> str:
    return text + _FILL if text else ""


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


class _Connector:
    """Upstream: key -> current text; ``fail`` makes the read raise."""

    def __init__(self, upstream: dict[str, str], *, collections: list[str] | None = None) -> None:
        self.upstream = upstream
        self.collections = collections or []
        self.fail = False
        self.reads: list[list[tuple[str, str]]] = []

    def legacy_scope(self, config: Any) -> tuple[str, list[str]]:
        return "shop", list(self.collections)

    async def read_legacy_documents(
        self, config: Any, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[RawDocument]]:
        if self.fail:
            raise RuntimeError("mongodb: server selection timed out")
        self.reads.append(list(refs))
        out: dict[tuple[str, str], list[RawDocument]] = {}
        for coll, key in refs:
            text = self.upstream.get(key)
            out[(coll, key)] = [] if text is None else [RawDocument(
                doc_id=_v8(key), source_id=_SOURCE, tenant_id=_TENANT,
                source_url=f"mongodb://new-host/shop/{coll}/{key}",
                content=_body(text).encode(), content_type="text/plain",
            )]
        return out


def _config() -> SourceConfig:
    return SourceConfig(source_id=_SOURCE, tenant_id=_TENANT, name="m",
                        family=SourceFamily.NOSQL_DATABASE, source_type="mongodb",
                        collection_id="", min_quality_score=0.0)


def _world() -> tuple[KnowledgeStore, SourceConfig, IngestionPipeline]:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="kb"), tenant_ctx=_CTX)
    config = _config()
    config.collection_id = cid
    return store, config, IngestionPipeline(knowledge_store=store, embedder=_Embedder())


def _seed(store: KnowledgeStore, cid: str, doc_id: str, text: str, url: str) -> None:
    store.ingest_chunk(
        Chunk(document_id=doc_id, content=text, embedding=[0.2] * _DIM, chunk_index=0,
              metadata={"source_id": _SOURCE, "source_url": url,
                        "doc_content_hash": hashlib.sha256(text.encode()).hexdigest()}),
        collection_id=cid, tenant_ctx=_CTX)


def _docs(store: KnowledgeStore, cid: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in store._data[(_TENANT, cid)].chunks:
        out[c.document_id] = out.get(c.document_id, "") + c.content
    return out


async def _run(store: KnowledgeStore, config: SourceConfig, pipeline: Any, connector: Any,
               states: MigrationStateStore, **kw: Any) -> dict[str, Any]:
    return await migrate_legacy_mongodb_ids(
        config=config, connector=connector, pipeline=pipeline, knowledge_store=store,
        state_store=states, **kw)


# ── legacy id recognition ─────────────────────────────────────────────────────


def test_legacy_ref_accepts_only_a_recomputable_v5_id() -> None:
    doc_id, url = _legacy("64f0c0ffee00000000000001")
    ref = legacy_ref(doc_id, url)
    assert ref is not None
    assert (ref.database, ref.collection, ref.key) == ("shop", "tickets",
                                                      "64f0c0ffee00000000000001")
    slashed_id, slashed_url = _legacy("a/b c")
    assert legacy_ref(slashed_id, slashed_url) is not None
    assert legacy_ref(slashed_id, slashed_url).key == "a/b c"  # type: ignore[union-attr]
    assert legacy_ref(_v8("x"), url) is None  # current ids are never legacy
    assert legacy_ref(doc_id, url.replace("tickets", "orders")) is None  # tampered URL
    assert legacy_ref(doc_id, "https://example.test/x") is None
    assert legacy_ref("not-a-uuid", url) is None


def test_should_run_matrix() -> None:
    done = MigrationState(_TENANT, _SOURCE, status=STATUS_COMPLETED)
    issues = MigrationState(_TENANT, _SOURCE, status=STATUS_COMPLETED_WITH_ISSUES)
    running = MigrationState(_TENANT, _SOURCE, status=STATUS_RUNNING)
    assert should_run(None, MODE_SYNC) and should_run(running, MODE_SYNC)
    assert not should_run(done, MODE_SYNC) and not should_run(done, MODE_MAINTENANCE)
    assert not should_run(issues, MODE_SYNC) and should_run(issues, MODE_MAINTENANCE)


# ── the migration ─────────────────────────────────────────────────────────────


async def test_each_legacy_document_ends_with_exactly_one_current_copy() -> None:
    store, config, pipeline = _world()
    cid = config.collection_id
    upd_id, upd_url = _legacy("upd")
    same_id, same_url = _legacy("same")
    dup_id, dup_url = _legacy("dup")
    gone_id, gone_url = _legacy("gone")
    other_id, other_url = _legacy("other", db="archive")
    _seed(store, cid, upd_id, "title: old text before the edit", upd_url)
    _seed(store, cid, same_id, _body("title: unchanged ticket"), same_url)
    _seed(store, cid, dup_id, "title: dup before the edit", dup_url)
    _seed(store, cid, _v8("dup"), "title: dup after the edit", "mongodb://new-host/shop/tickets/dup")
    _seed(store, cid, gone_id, "title: deleted upstream", gone_url)
    _seed(store, cid, other_id, "title: other database", other_url)
    _seed(store, cid, _v8("fresh"), "title: fresh", "mongodb://new-host/shop/tickets/fresh")
    connector = _Connector({"upd": "title: new text after the edit",
                            "same": "title: unchanged ticket",
                            "dup": "title: dup after the edit", "fresh": "title: fresh"})
    states = MigrationStateStore()

    result = await _run(store, config, pipeline, connector, states)

    docs = _docs(store, cid)
    assert set(docs) == {_v8("upd"), _v8("same"), _v8("dup"), _v8("fresh"), other_id}
    assert "new text after the edit" in docs[_v8("upd")]
    assert "unchanged ticket" in docs[_v8("same")]
    assert docs[_v8("dup")] == "title: dup after the edit"  # the copy already indexed
    assert docs[other_id] == "title: other database"  # another database: never deleted
    assert (result["migrated"], result["deleted"], result["skipped"], result["failed"]) == (
        3, 1, 1, 0)
    assert result["status"] == STATUS_COMPLETED_WITH_ISSUES and result["ran"] is True
    # Idempotent: a later sync never runs it again; maintenance re-passes the unclean finish.
    again = await _run(store, config, pipeline, connector, states)
    assert again["ran"] is False
    maint = await _run(store, config, pipeline, connector, states, mode=MODE_MAINTENANCE)
    assert maint["ran"] is True and maint["migrated"] == 0 and maint["skipped"] == 1
    assert _docs(store, cid) == docs


async def test_a_clean_finish_is_a_noop_for_every_trigger() -> None:
    store, config, pipeline = _world()
    upd_id, upd_url = _legacy("upd")
    _seed(store, config.collection_id, upd_id, "title: old", upd_url)
    connector = _Connector({"upd": "title: new"})
    states = MigrationStateStore()
    first = await _run(store, config, pipeline, connector, states)
    assert first["status"] == STATUS_COMPLETED
    for mode in (MODE_SYNC, MODE_MAINTENANCE):
        assert (await _run(store, config, pipeline, connector, states, mode=mode))["ran"] is False
    assert len(connector.reads) == 1


async def test_bounded_runs_resume_from_the_persisted_cursor() -> None:
    store, config, pipeline = _world()
    keys = [f"k{i}" for i in range(7)]
    for key in keys:
        doc_id, url = _legacy(key)
        _seed(store, config.collection_id, doc_id, f"title: old {key}", url)
    connector = _Connector({k: f"title: new {k}" for k in keys})
    states = MigrationStateStore()

    first = await _run(store, config, pipeline, connector, states, page_size=2, max_documents=4)
    assert first["status"] == STATUS_RUNNING and first["scanned"] == 4 and first["cursor"]
    assert all(len(batch) <= 2 for batch in connector.reads)
    persisted = await states.get(_TENANT, _SOURCE)
    assert persisted is not None and persisted.cursor == first["cursor"]

    second = await _run(store, config, pipeline, connector, states, page_size=2, max_documents=4)
    third = await _run(store, config, pipeline, connector, states, page_size=2, max_documents=4)
    final = third if third["ran"] else second
    assert final["status"] == STATUS_COMPLETED
    assert final["migrated"] == 7
    assert set(_docs(store, config.collection_id)) == {_v8(k) for k in keys}


async def test_a_failed_upstream_read_deletes_nothing_and_resumes_at_that_page() -> None:
    store, config, pipeline = _world()
    doc_id, url = _legacy("upd")
    _seed(store, config.collection_id, doc_id, "title: old", url)
    connector = _Connector({"upd": "title: new"})
    connector.fail = True
    states = MigrationStateStore()
    with pytest.raises(RuntimeError, match="server selection"):
        await _run(store, config, pipeline, connector, states)
    state = await states.get(_TENANT, _SOURCE)
    assert state is not None and state.status == STATUS_RUNNING and state.cursor is None
    assert "server selection" in (state.last_error or "")
    assert set(_docs(store, config.collection_id)) == {doc_id}

    connector.fail = False
    done = await _run(store, config, pipeline, connector, states)
    assert done["status"] == STATUS_COMPLETED and done["last_error"] is None
    assert set(_docs(store, config.collection_id)) == {_v8("upd")}


async def test_a_document_under_legal_hold_is_left_untouched() -> None:
    class _HeldStore(KnowledgeStore):
        held: set[str] = set()

        async def held_document_ids_async(self, collection_id: str, document_ids: list[str],
                                          *, tenant_ctx: TenantContext) -> set[str]:
            return {d for d in document_ids if d in self.held}

    store = _HeldStore()
    cid = store.create_collection(KnowledgeCollection(name="kb"), tenant_ctx=_CTX)
    config = _config()
    config.collection_id = cid
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())
    held_id, held_url = _legacy("held")
    free_id, free_url = _legacy("free")
    _seed(store, cid, held_id, "title: held old", held_url)
    _seed(store, cid, free_id, "title: free old", free_url)
    store.held = {held_id}
    connector = _Connector({"held": "title: held new", "free": "title: free new"})

    result = await _run(store, config, pipeline, connector, MigrationStateStore())

    assert result["held"] == 1 and result["migrated"] == 1
    assert result["status"] == STATUS_COMPLETED_WITH_ISSUES
    docs = _docs(store, cid)
    assert set(docs) == {held_id, _v8("free")} and docs[held_id] == "title: held old"
    assert [key for batch in connector.reads for _c, key in batch] == ["free"]


async def test_a_failed_write_keeps_the_legacy_copy() -> None:
    store, config, pipeline = _world()
    doc_id, url = _legacy("upd")
    _seed(store, config.collection_id, doc_id, "title: old", url)
    connector = _Connector({"upd": ""})  # empty upstream text: the pipeline skips it
    result = await _run(store, config, pipeline, connector, MigrationStateStore())
    assert result["failed"] == 1 and result["status"] == STATUS_COMPLETED_WITH_ISSUES
    assert set(_docs(store, config.collection_id)) == {doc_id}


# ── store: atomic supersede ──────────────────────────────────────────────────


async def test_supersede_takes_over_identical_content_atomically() -> None:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="kb"), tenant_ctx=_CTX)
    meta = {"doc_content_hash": "h1"}
    store.ingest_chunk(Chunk(document_id="old", content="same", embedding=[0.1] * _DIM,
                             chunk_index=0, metadata=dict(meta)), collection_id=cid,
                       tenant_ctx=_CTX)
    new = Chunk(document_id="new", content="same", embedding=[0.1] * _DIM, chunk_index=0,
                metadata=dict(meta))
    with pytest.raises(DuplicateContentError):
        await store.ingest_chunks_async([new], collection_id=cid, tenant_ctx=_CTX)
    await store.ingest_chunks_async([new], collection_id=cid, tenant_ctx=_CTX,
                                    supersedes_document_id="old")
    assert {c.document_id for c in store._data[(_TENANT, cid)].chunks} == {"new"}


# ── sync hook ────────────────────────────────────────────────────────────────


async def test_the_sync_hook_runs_only_for_connectors_with_a_legacy_migration() -> None:
    from app.ingestion.scheduler import _migrate_legacy_ids

    class _Lease:
        def check(self) -> None:
            return None

    class _SourceStore:
        _db = None

    store, config, pipeline = _world()
    assert await _migrate_legacy_ids(object(), config, pipeline, _SourceStore(), _Lease()) is None
    connector = _Connector({})
    connector.fail = True
    doc_id, url = _legacy("upd")
    _seed(store, config.collection_id, doc_id, "title: old", url)
    # A sync never fails because of the migration; the error is reported and kept.
    out = await _migrate_legacy_ids(connector, config, pipeline, _SourceStore(), _Lease())
    assert out is not None and out["status"] == "error" and "server selection" in out["error"]
    with pytest.raises(RuntimeError):
        await _migrate_legacy_ids(connector, config, pipeline, _SourceStore(), _Lease(),
                                  mode=MODE_MAINTENANCE)
