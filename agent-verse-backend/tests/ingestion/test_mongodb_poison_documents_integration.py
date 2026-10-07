"""MONGO-FAIL-POISON on real infrastructure: poison documents, the DLQ and the operator fix.

A real MongoDB (``mongo:7.0`` as a single-node replica set, so updates also come
from the change stream) holds 50 normal tickets, five valid documents that sort
AFTER the poison ones, and the poison documents of the live scenario
(``tests/real_world/commerce_seed.py``): an 11 MiB document, 95-level nesting, a
20,000-item array, odd BSON (control characters, binary, UUIDs, regex,
timestamp, Min/MaxKey, code, Decimal128, DBRef, a date outside Python's range)
and a string that is not valid UTF-8 (planted as raw BSON — drivers refuse to
encode it).

The real sync task runs the real connector and the real ingestion pipeline (an
in-memory knowledge store and a fake embedder are the only stand-ins) against a
migrated Postgres for the job and the durable DLQ. Asserted:

* every valid document is indexed exactly once — including those after the
  undecodable one (it used to fail the whole sync after 38 documents);
* the poison documents are indexed with their notes (depth, array, odd types)
  or dead-lettered with a reason (undecodable: ``_id`` + error id; over-size:
  the cap) — the job is ``partial`` with ``docs_failed == 2``;
* after the operator fixes them upstream, an operator DLQ retry and a re-sync
  index both, the DLQ ends empty, and the store holds exactly every document.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from bson import Binary, Code, Decimal128, MaxKey, MinKey, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
from bson.dbref import DBRef
from bson.raw_bson import RawBSONDocument
from pymongo import MongoClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer

import app.ingestion.connectors.mongodb_connector as mc
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

from tests.real_world.commerce_seed import invalid_utf8_raw, nested

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_DB, _COLL = "shop", "poison"
_DIM = 768
_OVERSIZE = 11 * 1024 * 1024  # above the pipeline's 10 MiB per-document cap
_NORMAL = [f"OK-{i:03d}" for i in range(50)]
_AFTER = [f"ZZ-{i}" for i in range(5)]  # sort after every POISON-* _id
_INDEXED_POISON = ["POISON-ARRAY", "POISON-DEEP", "POISON-ODD"]
_DEAD_LETTERED = ["POISON-OVERSIZE", "POISON-UTF8"]


def _ticket(key: str) -> dict[str, Any]:
    return {"_id": key, "kind": "normal",
            "notes": f"Routine merchant ticket {key} about a delayed settlement of the "
                     "weekly payout batch, raised by the merchant's finance desk."}


def _poison() -> list[dict[str, Any]]:
    return [
        {"_id": "POISON-OVERSIZE", "kind": "oversize", "blob": "x" * _OVERSIZE,
         "notes": "Archived raw gateway dump, far larger than any document cap."},
        {"_id": "POISON-DEEP", "kind": "deep", "notes": "Pathological nesting.",
         "tree": nested(95, "Bottom of the 95-level settlement tree")},
        {"_id": "POISON-ARRAY", "kind": "huge_array",
         "notes": "Click-stream array with 20000 events.",
         "events": [{"seq": k, "page": f"/p/{k % 97}"} for k in range(20000)]},
        {"_id": "POISON-ODD", "kind": "odd_bson",
         "notes": "Control characters \x00\x01\x07 and lone ​ zero-width "
                  "spaces in a merchant note.",
         "empty": {}, "empty_list": [], "neg_zero": -0.0, "nan": float("nan"),
         "blob": Binary(b"\x00\x01binary-payload", 0),
         "uuid4": Binary(uuid.UUID("12345678-1234-5678-1234-567812345678").bytes, 4),
         "uuid3": Binary(b"\x02" * 16, 3),
         "pattern": Regex("^refund-[0-9]+$", "i"), "ts": Timestamp(1_700_000_000, 3),
         "low": MinKey(), "high": MaxKey(), "js": Code("return amount * 2;", {"amount": 5}),
         "price": Decimal128("1999.9900"),
         "merchant": DBRef("merchants", ObjectId("64f000000000000000000009"), _DB),
         "far_future": DatetimeMS(2**60)},
    ]


@pytest.fixture(scope="module")
def mongo() -> Iterator[tuple[str, int]]:
    container = (
        DockerContainer("mongo:7.0")
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
    )
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(27017))
        client: MongoClient[dict[str, Any]] = MongoClient(
            host, port, directConnection=True, serverSelectionTimeoutMS=2000
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    client.admin.command("ping")
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            client.admin.command(
                "replSetInitiate",
                {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]},
            )
            while not client.admin.command("hello").get("isWritablePrimary"):
                if time.monotonic() > deadline:
                    raise TimeoutError("the replica set elected no primary")
                time.sleep(0.5)
        finally:
            client.close()
        yield host, port


@pytest.fixture
def seeded(mongo: tuple[str, int]) -> Iterator[MongoClient[dict[str, Any]]]:
    host, port = mongo
    client: MongoClient[dict[str, Any]] = MongoClient(host, port, directConnection=True)
    coll = client[_DB][_COLL]
    coll.drop()
    coll.insert_many([_ticket(k) for k in _NORMAL])
    for doc in _poison():
        coll.insert_one(doc)
    coll.insert_one(RawBSONDocument(invalid_utf8_raw("POISON-UTF8")))
    coll.insert_many([_ticket(k) for k in _AFTER])
    yield client
    coll.drop()
    client.close()


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


class _RetriedError(Exception):
    pass


class _Task:
    def retry(self, **_kwargs: Any) -> _RetriedError:
        return _RetriedError()


@pytest_asyncio.fixture
async def world(pg_url: str, mongo: tuple[str, int]) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = f"poison-{uuid.uuid4().hex[:8]}"
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :e, 'professional', true)"
            ),
            {"id": tenant, "e": f"{tenant}@example.test"},
        )
    kb = KnowledgeStore()
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    cid = kb.create_collection(KnowledgeCollection(name="poison-kb"), tenant_ctx=ctx)
    store = SourceConfigStore(db=factory)
    host, port = mongo
    config = SourceConfig(
        source_id=uuid.uuid4().hex,
        tenant_id=tenant,
        name="poison tickets",
        family=SourceFamily.NOSQL_DATABASE,
        source_type="mongodb",
        connection_config={
            "uri": f"mongodb://{host}:{port}/",
            "database": _DB,
            "collection": _COLL,
            "direct_connection": True,
            "batch_size": 20,
        },
        collection_id=cid,
        min_quality_score=0.0,
    )
    await store.create(config)
    yield {
        "factory": factory,
        "tracker": IngestionJobTracker(db=factory, system_db=factory),
        "store": store,
        "pipeline": IngestionPipeline(knowledge_store=kb, embedder=_Embedder()),
        "kb": kb,
        "cid": cid,
        "config": config,
    }
    await engine.dispose()


def _patched(world: dict[str, Any]) -> Any:
    return patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(world["tracker"], world["pipeline"], world["store"]),
    )


async def _sync(world: dict[str, Any]) -> dict[str, Any]:
    from app.ingestion.scheduler import _sync_source_async

    config = world["config"]
    # "Sync now" (POST /sources/{id}/sync): the API takes the lock, its token is
    # the job id, and an operator run is never backed off after a partial sync.
    job_id = await world["tracker"].acquire_lock(config.source_id, config.tenant_id)
    assert job_id
    with (
        _patched(world),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
    ):
        try:
            return await _sync_source_async(
                task=_Task(), source_id=config.source_id, tenant_id=config.tenant_id,
                triggered_by="manual", job_id=job_id,
            )
        except _RetriedError:  # the sync failed and asked Celery to retry it
            return {"failed": True}


async def _operator_retry(world: dict[str, Any], dlq_id: str) -> str:
    from app.ingestion.scheduler import _retry_dlq_entry_async

    with _patched(world):
        result = await _retry_dlq_entry_async(dlq_id=dlq_id, tenant_id=world["config"].tenant_id)
    return str(result["outcome"])


async def _job(world: dict[str, Any], job_id: str) -> dict[str, Any]:
    async with world["factory"]() as s:
        row = (await s.execute(
            text("SELECT status, docs_indexed, docs_skipped, docs_failed, error_message "
                 "FROM ingestion_jobs WHERE id = :id"), {"id": job_id})).mappings().one()
    return dict(row)


async def _dlq(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The Source's DLQ rows keyed by the MongoDB ``_id`` they name."""
    async with world["factory"]() as s:
        rows = (await s.execute(
            text("SELECT id, doc_id, error_message, last_error, retry_count, resolved_at "
                 "FROM ingestion_dlq WHERE source_id = :sid"),
            {"sid": world["config"].source_id})).mappings().all()
    by_doc = {_doc_id(world, k): k for k in _all_keys()}
    return {by_doc[str(r["doc_id"])]: dict(r) for r in rows}


def _doc_id(world: dict[str, Any], key: str) -> str:
    return mc._doc_id(world["config"], _COLL, key)


def _all_keys() -> list[str]:
    return _NORMAL + _INDEXED_POISON + _DEAD_LETTERED + _AFTER


def _indexed(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """document id -> {"content": joined chunk text, "urls": distinct source URLs}."""
    out: dict[str, dict[str, Any]] = {}
    tenant = world["config"].tenant_id
    for chunk in world["kb"]._data[(tenant, world["cid"])].chunks:
        entry = out.setdefault(chunk.document_id, {"content": "", "urls": set()})
        entry["content"] += chunk.content
        entry["urls"].add((chunk.metadata or {}).get("source_url"))
    return out


async def test_poison_documents_are_isolated_dead_lettered_and_recovered(
    world: dict[str, Any], seeded: MongoClient[dict[str, Any]]
) -> None:
    # ── 1. The first sync: valid documents all indexed, poison isolated ─────
    first = await _sync(world)
    assert "failed" not in first, "the sync failed instead of isolating the poison documents"
    assert first["docs_failed"] == 2, first
    assert first["docs_indexed"] == len(_NORMAL) + len(_AFTER) + len(_INDEXED_POISON), first
    job = await _job(world, first["job_id"])
    assert job["status"] == "partial" and job["docs_failed"] == 2, job
    assert "ingestion DLQ" in job["error_message"]

    indexed = _indexed(world)
    expected_now = _NORMAL + _AFTER + _INDEXED_POISON
    assert set(indexed) == {_doc_id(world, k) for k in expected_now}  # exactly these
    for key in expected_now:
        entry = indexed[_doc_id(world, key)]
        assert len(entry["urls"]) == 1, (key, entry["urls"])  # one copy each
    for key in _NORMAL + _AFTER:  # including the ones AFTER the undecodable document
        assert f"Routine merchant ticket {key}" in indexed[_doc_id(world, key)]["content"]
    deep = indexed[_doc_id(world, "POISON-DEEP")]["content"]
    assert "[nested deeper than 5 levels, as JSON]" in deep
    assert "Bottom of the 95-level settlement tree" in deep
    assert "19900 more item(s) of 20000 not indexed" in (
        indexed[_doc_id(world, "POISON-ARRAY")]["content"])
    odd = indexed[_doc_id(world, "POISON-ODD")]["content"]
    for rendered in ("Control characters \\u0000\\u0001\\u0007", "<binary 16 bytes>",
                     "<uuid 12345678-1234-5678-1234-567812345678>", "/^refund-[0-9]+$/i",
                     "(BSON timestamp, increment 3)", "<MinKey>", "<MaxKey>",
                     "<javascript code> return amount * 2;", "1999.9900", '"$ref": "merchants"',
                     "ms since the epoch, outside the representable range"):
        assert rendered in odd, rendered
    assert "\x00" not in odd

    dlq = await _dlq(world)
    assert set(dlq) == set(_DEAD_LETTERED)
    utf8 = dlq["POISON-UTF8"]["error_message"]
    assert "_id=POISON-UTF8 in shop.poison" in utf8 and "not valid UTF-8" in utf8
    assert "(error id " in utf8 and "codec can't decode" not in utf8
    oversize = dlq["POISON-OVERSIZE"]["error_message"]
    assert "_id=POISON-OVERSIZE in shop.poison" in oversize
    assert "over this Source's 10485760-byte per-document cap" in oversize

    # ── 2. An operator retry before the fix: still failed, with the reason ──
    assert await _operator_retry(world, str(dlq["POISON-UTF8"]["id"])) == "still_failed"
    row = (await _dlq(world))["POISON-UTF8"]
    assert row["resolved_at"] is None and row["retry_count"] == 1
    assert "not valid UTF-8" in row["last_error"]

    # ── 3. The operator fixes both upstream ─────────────────────────────────
    coll = seeded[_DB][_COLL]
    coll.replace_one({"_id": "POISON-UTF8"}, {
        "kind": "utf8-fixed",
        "notes": "Merchant note re-exported as valid UTF-8 from the legacy system."})
    coll.replace_one({"_id": "POISON-OVERSIZE"}, {
        "kind": "oversize-fixed",
        "notes": "Archived gateway dump trimmed to a summary: 1,204 settlement callbacks, "
                 "3 retries."})

    # The operator retry re-reads the fixed document and indexes it.
    assert await _operator_retry(world, str(dlq["POISON-UTF8"]["id"])) == "succeeded"
    assert (await _dlq(world))["POISON-UTF8"]["resolved_at"] is not None
    assert "re-exported as valid UTF-8" in _indexed(world)[_doc_id(world, "POISON-UTF8")][
        "content"]

    # ── 4. The re-sync reads the fix from the change stream ─────────────────
    second = await _sync(world)
    assert "failed" not in second and second["docs_failed"] == 0, second
    assert (await _job(world, second["job_id"]))["status"] == "completed"
    final = _indexed(world)
    assert set(final) == {_doc_id(world, k) for k in _all_keys()}  # exactly every document
    assert all(len(e["urls"]) == 1 for e in final.values())
    assert "1,204 settlement callbacks" in final[_doc_id(world, "POISON-OVERSIZE")]["content"]
    assert all(r["resolved_at"] is not None for r in (await _dlq(world)).values())
