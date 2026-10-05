"""OI-4 — Redis document ids no longer depend on the host; legacy ids are migrated.

Ids used to be ``uuid5("<source id>:redis://<host>:<port>/<db>/<key>")``: renaming
the host (DNS, IP, a new Sentinel master) re-ided every key until reconcile ran.
Now they are a UUID v8 of Source + database + key (like MongoDB's TG-13), and a
one-time, resumable migration (the D2 machinery) moves documents still under
the legacy ids. Unit level: in-memory store, real pipeline, the real connector's
id / legacy hooks with the upstream read faked. The container test is
``test_redis_id_migration_integration.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from urllib.parse import quote

import pytest

from app.ingestion.connectors.redis_connector import (
    RedisConnector,
    _doc_id,
    _settings,
)
from app.ingestion.mongodb_id_migration import (
    MODE_MAINTENANCE,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_ISSUES,
    MigrationStateStore,
    migrate_legacy_mongodb_ids,
    migration_name,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_TENANT = "tid-oi4"
_SOURCE = "src-redis"
_CTX = TenantContext(tenant_id=_TENANT, plan=PlanTier.FREE, api_key_id="k")
_DIM = 768
_FILL = " — the cached value keeps enough words to pass the minimum text length." * 3


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=_SOURCE,
        tenant_id=_TENANT,
        name="r",
        family=SourceFamily.NOSQL_DATABASE,
        source_type="redis",
        connection_config=cc or {"host": "cache-old.example", "port": 6379, "db": 2},
        min_quality_score=0.0,
    )


def _legacy(key: str, *, display: str = "cache-old.example:6379", db: int = 2) -> tuple[str, str]:
    url = f"redis://{display}/{db}/{quote(key, safe='')}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_SOURCE}:{url}")), url


# ── ids ───────────────────────────────────────────────────────────────────────


def test_doc_id_ignores_the_host_and_is_scoped() -> None:
    old = _config(host="cache-old.example", db=2)
    new = _config(host="10.0.0.7", port=6380, db=2)
    assert _doc_id(old, 2, "user:1") == _doc_id(new, 2, "user:1")
    assert uuid.UUID(_doc_id(old, 2, "user:1")).version == 8
    assert _doc_id(old, 2, "user:1") != _doc_id(old, 3, "user:1")  # database matters
    assert _doc_id(old, 2, "user:1") != _doc_id(old, 2, "user:2")
    other = SourceConfig(
        source_id="src-other",
        tenant_id=_TENANT,
        name="o",
        family=SourceFamily.NOSQL_DATABASE,
        source_type="redis",
    )
    assert _doc_id(old, 2, "user:1") != _doc_id(other, 2, "user:1")


def test_only_current_ids_are_deletion_candidates() -> None:
    connector = RedisConnector()
    assert connector.manages_doc_id(_doc_id(_config(), 2, "k"))
    assert not connector.manages_doc_id(_legacy("k")[0])
    assert not connector.manages_doc_id("not-a-uuid")


@pytest.mark.parametrize(
    "display",
    ["cache-old.example:6379", "sentinel/mymaster", "cluster/10.0.0.1:7000"],
)
def test_legacy_ref_accepts_only_a_recomputable_v5_id(display: str) -> None:
    connector, config = RedisConnector(), _config()
    doc_id, url = _legacy("orders:a/b c", display=display)
    ref = connector.legacy_ref(doc_id, url, config)
    assert ref is not None
    assert (ref.database, ref.collection, ref.key) == ("2", "", "orders:a/b c")
    assert connector.legacy_ref(doc_id, url.replace("/2/", "/3/"), config) is None  # tampered
    assert connector.legacy_ref(_doc_id(config, 2, "k"), url, config) is None  # current id
    assert connector.legacy_ref(doc_id, "mongodb://h/db/c/k", config) is None
    assert connector.legacy_ref("not-a-uuid", url, config) is None


def test_scope_keeps_other_databases_and_unmatched_keys() -> None:
    connector = RedisConnector()
    config = _config(host="h", db=2, key_patterns="user:*, cart:*")
    ref = connector.legacy_ref(*_legacy("user:9"), config)
    assert connector.legacy_ref_in_scope(config, ref)
    assert not connector.legacy_ref_in_scope(
        config, connector.legacy_ref(*_legacy("session:1"), config)
    )
    assert not connector.legacy_ref_in_scope(
        config, connector.legacy_ref(*_legacy("user:9", db=5), config)
    )
    assert migration_name(connector) == "redis_v8_doc_ids"


# ── the migration ─────────────────────────────────────────────────────────────


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


class _Upstream(RedisConnector):
    """The real connector hooks; the Redis read answers from a dict.

    ``None`` value = the key exists but its type is filtered out now.
    """

    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values
        self.fail = False

    async def read_legacy_documents(
        self, config: SourceConfig, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[RawDocument] | None]:
        if self.fail:
            raise RuntimeError("redis: connection refused")
        settings = _settings(config.connection_config)
        out: dict[tuple[str, str], list[RawDocument] | None] = {}
        for ref in refs:
            key = ref[1]
            if key not in self.values:
                out[ref] = []
            elif self.values[key] is None:
                out[ref] = None
            else:
                from app.ingestion.connectors.redis_connector import _raw_document

                out[ref] = [
                    _raw_document(
                        config,
                        settings,
                        settings.db,
                        key,
                        "n",
                        ("string", str(self.values[key]) + _FILL, False),
                    )
                ]
        return out


def _world(**cc: Any) -> tuple[KnowledgeStore, SourceConfig, IngestionPipeline]:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="kb"), tenant_ctx=_CTX)
    config = _config(**cc)
    config.collection_id = cid
    return store, config, IngestionPipeline(knowledge_store=store, embedder=_Embedder())


def _seed(store: KnowledgeStore, cid: str, doc_id: str, text: str, url: str) -> None:
    store.ingest_chunk(
        Chunk(
            document_id=doc_id,
            content=text,
            embedding=[0.2] * _DIM,
            chunk_index=0,
            metadata={
                "source_id": _SOURCE,
                "source_url": url,
                "doc_content_hash": hashlib.sha256(text.encode()).hexdigest(),
            },
        ),
        collection_id=cid,
        tenant_ctx=_CTX,
    )


def _docs(store: KnowledgeStore, cid: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in store._data[(_TENANT, cid)].chunks:
        out[c.document_id] = out.get(c.document_id, "") + c.content
    return out


async def _run(
    store: KnowledgeStore,
    config: SourceConfig,
    pipeline: Any,
    connector: Any,
    states: MigrationStateStore,
    **kw: Any,
) -> dict[str, Any]:
    return await migrate_legacy_mongodb_ids(
        config=config,
        connector=connector,
        pipeline=pipeline,
        knowledge_store=store,
        state_store=states,
        **kw,
    )


async def test_each_legacy_key_ends_with_exactly_one_current_copy() -> None:
    store, config, pipeline = _world(host="cache-new.example", db=2, key_patterns="*")
    cid = config.collection_id
    for key, text in (
        ("upd", "old value before the edit"),
        ("gone", "deleted upstream"),
        ("typed", "a list key now filtered out"),
    ):
        _seed(store, cid, *_ids(key, text))
    same_id, same_url = _legacy("same")
    _seed(store, cid, same_id, f"# same\n\ntype: string\n\nunchanged value{_FILL}", same_url)
    other_id, other_url = _legacy("other", db=7)
    _seed(store, cid, other_id, "other database", other_url)
    connector = _Upstream(
        {"upd": "new value after the edit", "same": "unchanged value", "typed": None}
    )
    states = MigrationStateStore(migration=migration_name(connector))

    result = await _run(store, config, pipeline, connector, states)

    docs = _docs(store, cid)
    assert set(docs) == {
        _doc_id(config, 2, "upd"),
        _doc_id(config, 2, "same"),
        _ids("typed", "")[0],
        other_id,
    }
    assert "new value after the edit" in docs[_doc_id(config, 2, "upd")]
    assert "unchanged value" in docs[_doc_id(config, 2, "same")]
    assert (result["migrated"], result["deleted"], result["skipped"], result["failed"]) == (
        2,
        1,
        2,
        0,
    )
    assert result["status"] == STATUS_COMPLETED_WITH_ISSUES
    again = await _run(store, config, pipeline, connector, states)
    assert again["ran"] is False
    maint = await _run(store, config, pipeline, connector, states, mode=MODE_MAINTENANCE)
    assert maint["ran"] is True and maint["migrated"] == 0
    assert _docs(store, cid) == docs


def _ids(key: str, text: str) -> tuple[str, str, str]:
    doc_id, url = _legacy(key)
    return doc_id, text, url


async def test_a_failed_read_deletes_nothing_and_resumes() -> None:
    store, config, pipeline = _world(host="cache-new.example", db=2)
    cid = config.collection_id
    _seed(store, cid, *_ids("a", "value a"))
    connector = _Upstream({"a": "value a"})
    connector.fail = True
    states = MigrationStateStore(migration=migration_name(connector))

    with pytest.raises(RuntimeError, match="connection refused"):
        await _run(store, config, pipeline, connector, states)
    assert set(_docs(store, cid)) == {_legacy("a")[0]}

    connector.fail = False
    result = await _run(store, config, pipeline, connector, states)
    assert result["status"] == STATUS_COMPLETED
    assert set(_docs(store, cid)) == {_doc_id(config, 2, "a")}


async def test_mongodb_and_redis_migrations_keep_separate_state() -> None:
    from app.ingestion.connectors.mongodb_connector import MongoDBConnector

    assert migration_name(MongoDBConnector()) == "mongodb_v8_doc_ids"
    assert migration_name(RedisConnector()) == "redis_v8_doc_ids"


async def test_the_maintenance_dispatch_covers_redis_sources() -> None:
    """The idempotent maintenance task queues Redis Sources too (own migration name)."""
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    from app.ingestion.scheduler import _dispatch_mongodb_doc_id_migrations_async

    seen: list[dict[str, Any]] = []

    class _Result:
        def __init__(self, rows: list[tuple[str, str]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[tuple[str, str]]:
            return self._rows

    class _Session:
        async def execute(self, _stmt: Any, params: dict[str, Any] | None = None) -> Any:
            if not params:
                return _Result([])
            seen.append(params)
            return _Result([("t1", f"{params['st']}-source")])

        @asynccontextmanager
        async def _tx(self) -> Any:
            yield self

        def begin(self) -> Any:
            return self._tx()

    @asynccontextmanager
    async def _system_db() -> Any:
        yield _Session()

    with patch("app.ingestion.scheduler.migrate_mongodb_doc_ids_source_task") as per_source:
        out = await _dispatch_mongodb_doc_id_migrations_async(system_db=_system_db)

    queued = {c.kwargs["kwargs"]["source_id"] for c in per_source.apply_async.call_args_list}
    assert queued == {"mongodb-source", "redis-source"}
    assert {(p["st"], p["m"]) for p in seen} == {
        ("mongodb", "mongodb_v8_doc_ids"),
        ("redis", "redis_v8_doc_ids"),
    }
    assert out["queued"] == 2
