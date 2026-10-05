"""STABLE-DOC-IDS / KB-44: documents deleted upstream are removed by reconciliation.

Connectors that can list what exists upstream let the worker delete the
Source's indexed documents that are gone — never for documents under legal hold
(or when the hold cannot be verified), and never for connectors that cannot
know. KB-44: reconciliation is its own task (``ingestion.reconcile_source``),
scheduled by a failure-free sync at most once per
``INGESTION_RECONCILE_INTERVAL_SECONDS``; the upstream listing is streamed and
staged in bounded batches (never a whole-bucket set in memory), legal holds are
checked one query per page and deletes are capped per run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.base_connector import BaseConnector, ConnectionHealth, stable_doc_id
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.scheduler import (
    _reconcile_source_async,
    _reconcile_upstream_deletions,
    _sync_source_async,
)
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t1", api_key_id="test", plan=PlanTier.FREE)


class _Upstream:
    """What exists upstream right now (key -> body)."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.listing: bool | None = True  # False: listing raises; None: cannot know
        self.fail_key: str | None = None


def _connector(upstream: _Upstream) -> type[BaseConnector]:
    class _Conn(BaseConnector):
        source_type = "fake-listing"

        async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
            return ConnectionHealth(ok=True)

        async def get_delta(
            self, config: SourceConfig, cursor: str | None
        ) -> AsyncIterator[tuple[RawDocument, str]]:
            for key, body in sorted(upstream.items.items()):
                yield (
                    RawDocument(
                        doc_id=stable_doc_id(config, key),
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        content=(body * 30).encode(),
                        content_type="text/plain",
                        metadata={"connector_failure": "boom"} if key == upstream.fail_key else {},
                    ),
                    key,
                )

        async def list_live_doc_ids(self, config: SourceConfig) -> set[str] | None:
            if upstream.listing is None:
                return None
            if upstream.listing is False:
                raise ConnectionError("listing failed")
            return {stable_doc_id(config, key) for key in upstream.items}

    return _Conn


class _Holds:
    """Stands in for the store's batched legal-hold query (one call per page)."""

    def __init__(self, held: set[str] | None = None, broken: bool = False) -> None:
        self.held = held or set()
        self.broken = broken
        self.calls = 0

    async def __call__(
        self, collection_id: str, document_ids: list[str], *, tenant_ctx: Any
    ) -> set[str]:
        self.calls += 1
        if self.broken:
            raise RuntimeError("hold store down")
        if collection_id in self.held:
            return set(document_ids)
        return {d for d in document_ids if d in self.held}


_PIPELINES: list[IngestionPipeline] = []


def _pipeline_of_world() -> IngestionPipeline:
    return _PIPELINES[-1]


@pytest.fixture
def world() -> Any:
    kb = KnowledgeStore()
    kb.create_collection(KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_CTX)
    pipeline = IngestionPipeline(knowledge_store=kb, embedder=FakeProvider(embed_dim=768))
    store = SourceConfigStore()
    tracker = IngestionJobTracker()
    config = SourceConfig(
        source_id="src-del", tenant_id="t1", name="s", family=SourceFamily.OBJECT_STORAGE,
        source_type="fake-listing", collection_id="c1", min_quality_score=0.0,
    )
    upstream = _Upstream()
    upstream.items = {"a.txt": "Alpha document body. ", "b.txt": "Bravo document body. "}

    async def sync(holds: _Holds | None = None) -> dict[str, Any]:
        """A sync, then — when the sync scheduled it — the reconcile task's body."""
        if await store.get(config.source_id, "t1") is None:
            await store.create(config)
        scheduled: list[str] = []

        async def _schedule(connector: Any, cfg: Any) -> bool:
            scheduled.append(cfg.source_id)
            return True

        with (
            patch(
                "app.ingestion.scheduler._build_worker_ingestion",
                return_value=(tracker, pipeline, store),
            ),
            patch(
                "app.ingestion.connector_registry.get_connector",
                return_value=_connector(upstream),
            ),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due", _schedule),
            patch("app.ingestion.scheduler._release_reconcile_queue", AsyncMock()),
            patch.object(kb, "held_document_ids_async", holds or _Holds()),
        ):
            result = await _sync_source_async(
                task=MagicMock(), source_id=config.source_id, tenant_id="t1",
                triggered_by="manual",
            )
            result["docs_deleted"] = 0
            if scheduled:
                counts = await _reconcile_source_async(
                    source_id=config.source_id, tenant_id="t1"
                )
                result["docs_deleted"] = int(counts.get("deleted", 0))
        return result

    def indexed() -> set[str]:
        return {c.document_id for c in kb._data[("t1", "c1")].chunks}

    _PIPELINES.append(pipeline)
    return config, upstream, sync, indexed


async def test_deleted_upstream_item_is_removed_after_a_clean_sync(world: Any) -> None:
    config, upstream, sync, indexed = world
    first = await sync(_Holds())
    assert first["docs_indexed"] == 2
    assert indexed() == {stable_doc_id(config, "a.txt"), stable_doc_id(config, "b.txt")}

    del upstream.items["b.txt"]
    second = await sync(_Holds())

    assert second["docs_deleted"] == 1
    assert indexed() == {stable_doc_id(config, "a.txt")}


async def test_documents_under_legal_hold_are_kept(world: Any) -> None:
    config, upstream, sync, indexed = world
    await sync(_Holds())
    del upstream.items["b.txt"]

    result = await sync(_Holds(held={stable_doc_id(config, "b.txt")}))
    assert result["docs_deleted"] == 0
    assert stable_doc_id(config, "b.txt") in indexed()

    # Collection-wide hold, and an unverifiable hold state: kept too.
    assert (await sync(_Holds(held={"c1"})))["docs_deleted"] == 0
    assert (await sync(_Holds(broken=True)))["docs_deleted"] == 0
    assert stable_doc_id(config, "b.txt") in indexed()


async def test_nothing_is_deleted_after_a_run_with_failures(world: Any) -> None:
    config, upstream, sync, indexed = world
    await sync(_Holds())
    del upstream.items["b.txt"]
    upstream.fail_key = "a.txt"
    upstream.items["a.txt"] = "Alpha edited so it is re-ingested. "

    result = await sync(_Holds())
    assert result["docs_failed"] == 1
    assert result["docs_deleted"] == 0
    assert stable_doc_id(config, "b.txt") in indexed()


async def test_connectors_that_cannot_know_never_delete(world: Any) -> None:
    config, upstream, sync, indexed = world
    await sync(_Holds())
    del upstream.items["b.txt"]

    upstream.listing = None
    assert (await sync(_Holds()))["docs_deleted"] == 0
    upstream.listing = False  # listing error: sync stands, nothing deleted
    result = await sync(_Holds())
    assert result["docs_deleted"] == 0 and result["docs_failed"] == 0
    assert stable_doc_id(config, "b.txt") in indexed()


async def test_other_sources_documents_are_never_candidates(world: Any) -> None:
    config, upstream, sync, indexed = world
    await sync(_Holds())
    other_cfg = SourceConfig(
        source_id="src-other", tenant_id="t1", name="o", family=SourceFamily.WEB,
        source_type="http", collection_id="c1", min_quality_score=0.0,
    )
    other_id = stable_doc_id(other_cfg, "x")
    other = await _pipeline_of_world().ingest(
        RawDocument(
            doc_id=other_id, source_id="src-other", tenant_id="t1",
            content=("Other source document. " * 30).encode(), content_type="text/plain",
        ),
        other_cfg,
    )
    assert other.status == "indexed"

    upstream.items.clear()
    result = await sync(_Holds())

    assert result["docs_deleted"] == 2
    assert indexed() == {other_id}


async def test_documents_indexed_before_stable_ids_are_never_deleted(world: Any) -> None:
    """A random-id (uuid4) document of this Source can never be in the live set;
    its unchanged upstream item is not re-fetched by an incremental sync, so
    deleting it would silently drop content."""
    import uuid

    config, upstream, sync, indexed = world
    await sync(_Holds())
    legacy_id = str(uuid.uuid4())
    legacy = await _pipeline_of_world().ingest(
        RawDocument(
            doc_id=legacy_id, source_id=config.source_id, tenant_id="t1",
            content=("Legacy random-id document. " * 30).encode(), content_type="text/plain",
        ),
        config,
    )
    assert legacy.status == "indexed"

    result = await sync(_Holds())

    assert result["docs_deleted"] == 0
    assert legacy_id in indexed()


# ── KB-44: bounded, capped, scheduled ────────────────────────────────────────


def _cfg(**overrides: Any) -> SourceConfig:
    values: dict[str, Any] = {
        "source_id": "src-m",
        "tenant_id": "t1",
        "name": "m",
        "family": SourceFamily.OBJECT_STORAGE,
        "source_type": "fake-million",
        "collection_id": "c1",
    }
    values.update(overrides)
    return SourceConfig(**values)


class _MillionBucket(BaseConnector):
    """1,000,000 live ids, generated lazily (as a paginated listing streams them)."""

    source_type = "fake-million"

    def __init__(self, total: int = 1_000_000) -> None:
        self.total = total
        self.yielded = 0

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        return ConnectionHealth(ok=True)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        return
        yield  # pragma: no cover

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        for i in range(self.total):
            self.yielded += 1
            yield f"s3://b/key-{i:07d}"

    def manages_doc_id(self, doc_id: str) -> bool:
        return doc_id.startswith("s3://")


class _CountingStore:
    """Stages by counting (never keeping) ids; ``stale`` indexed ids are gone upstream."""

    def __init__(self, connector: _MillionBucket | None = None, stale: int = 5000) -> None:
        self.connector = connector
        self.staged = 0
        self.max_batch = 0
        self.max_unstaged = 0
        self.deleted: list[str] = []
        self.cleared: list[str] = []
        self.begun: list[str] = []
        self.hold_calls = 0
        self.stale = [f"s3://b/gone-{i:05d}" for i in range(stale)]
        self.indexed_before: Any = None

    async def begin_live_listing_async(self, run_id: str, **_: Any) -> Any:
        import datetime

        self.begun.append(run_id)
        return datetime.datetime.now(datetime.UTC)

    async def stage_live_doc_ids_async(self, run_id: str, ids: list[str], **_: Any) -> None:
        self.staged += len(ids)
        self.max_batch = max(self.max_batch, len(ids))
        if self.connector is not None:
            self.max_unstaged = max(self.max_unstaged, self.connector.yielded - self.staged)

    async def list_unlisted_source_documents_async(
        self, run_id: str, *, after: str | None, limit: int, indexed_before: Any, **_: Any
    ) -> list[str]:
        self.indexed_before = indexed_before
        rest = [d for d in self.stale if after is None or d > after]
        return rest[:limit]

    async def held_document_ids_async(self, cid: str, ids: list[str], **_: Any) -> set[str]:
        self.hold_calls += 1
        return {ids[0]}  # one held per page

    async def delete_document_async(self, doc_id: str, **_: Any) -> int:
        self.deleted.append(doc_id)
        return 1

    async def clear_live_listing_async(self, run_id: str, **_: Any) -> None:
        self.cleared.append(run_id)


async def test_a_million_object_listing_is_streamed_in_bounded_batches_and_capped() -> None:
    import tracemalloc

    bucket = _MillionBucket()
    store = _CountingStore(bucket)
    tracemalloc.start()
    try:
        counts = await _reconcile_upstream_deletions(
            bucket, _cfg(), MagicMock(_kb=store), max_deletes=1500
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert store.staged == 1_000_000
    assert store.max_batch <= 1000
    # Never more than one staging batch of listed ids held in process memory.
    assert store.max_unstaged <= 1000
    assert peak < 16 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"
    assert counts["deleted"] == 1500 and len(store.deleted) == 1500
    assert counts["truncated"] == 1
    assert counts["kept_held"] == 2  # one per page compared (pages 1 and 2)
    assert store.hold_calls == 2  # ONE hold query per page, never one per document
    assert store.cleared == store.begun  # the run's staging is dropped
    assert store.indexed_before is not None  # only documents indexed before the run


async def test_a_listing_error_mid_stream_deletes_nothing() -> None:
    class _Broken(_MillionBucket):
        async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
            yield "s3://b/a"
            raise ConnectionError("page 2 failed")

    store = _CountingStore()
    counts = await _reconcile_upstream_deletions(_Broken(), _cfg(), MagicMock(_kb=store))
    assert counts["deleted"] == 0 and store.deleted == []
    assert counts["listing_failed"] == 1
    assert store.cleared == store.begun  # the partial staging is dropped


async def test_an_unverifiable_hold_stops_the_run_with_nothing_deleted() -> None:
    store = _CountingStore()

    async def _down(cid: str, ids: list[str], **_: Any) -> set[str]:
        raise RuntimeError("legal_holds unreachable")

    store.held_document_ids_async = _down  # type: ignore[method-assign]
    counts = await _reconcile_upstream_deletions(
        _MillionBucket(total=10), _cfg(), MagicMock(_kb=store)
    )
    assert store.deleted == []
    assert counts["hold_check_failed"] == 1 and counts["deleted"] == 0


async def test_a_collection_hold_keeps_every_stale_document() -> None:
    store = _CountingStore(stale=2500)

    async def _all_held(cid: str, ids: list[str], **_: Any) -> set[str]:
        return set(ids)

    store.held_document_ids_async = _all_held  # type: ignore[method-assign]
    counts = await _reconcile_upstream_deletions(
        _MillionBucket(total=10), _cfg(), MagicMock(_kb=store)
    )
    assert store.deleted == []
    assert counts["kept_held"] == 2500


class _GateRedis:
    def __init__(self) -> None:
        self.keys: dict[str, int] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if key in self.keys:
            return False
        self.keys[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.keys.pop(k, None) is not None)

    async def aclose(self) -> None:
        return None


async def test_a_sync_schedules_reconciliation_at_most_once_per_interval() -> None:
    from app.core.config import get_settings
    from app.ingestion import scheduler

    gate = _GateRedis()
    with (
        patch.object(get_settings(), "ingestion_reconcile_interval_seconds", 7200),
        patch.object(scheduler, "_reconcile_redis", return_value=gate),
        patch.object(scheduler.reconcile_source_task, "apply_async") as enqueue,
    ):
        assert await scheduler._schedule_reconcile_if_due(_MillionBucket(), _cfg()) is True
        assert await scheduler._schedule_reconcile_if_due(_MillionBucket(), _cfg()) is False
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["queue"] == "ingestion"
    assert enqueue.call_args.kwargs["kwargs"] == {"source_id": "src-m", "tenant_id": "t1"}
    assert gate.keys["ingestion:reconcile:due:t1:src-m"] == 7200


def test_the_reconcile_interval_defaults_to_a_day_and_has_a_floor() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    assert Settings().ingestion_reconcile_interval_seconds == 86400
    with pytest.raises(ValidationError):
        Settings(ingestion_reconcile_interval_seconds=10)


async def test_a_failed_enqueue_reopens_the_gate() -> None:
    from app.ingestion import scheduler

    gate = _GateRedis()
    with (
        patch.object(scheduler, "_reconcile_redis", return_value=gate),
        patch.object(
            scheduler.reconcile_source_task, "apply_async", side_effect=OSError("broker down")
        ),
    ):
        assert await scheduler._schedule_reconcile_if_due(_MillionBucket(), _cfg()) is False
    assert gate.keys == {}  # the next sync tries again (never a silent 24 h gap)


async def test_no_reconciliation_without_redis_or_without_a_listing() -> None:
    from app.ingestion import scheduler

    class _NoListing(_MillionBucket):
        iter_live_doc_ids = BaseConnector.iter_live_doc_ids  # type: ignore[assignment]

    with patch.object(scheduler.reconcile_source_task, "apply_async") as enqueue:
        with patch.object(scheduler, "_reconcile_redis", return_value=None):
            assert await scheduler._schedule_reconcile_if_due(_MillionBucket(), _cfg()) is False
        with patch.object(scheduler, "_reconcile_redis", return_value=_GateRedis()):
            assert await scheduler._schedule_reconcile_if_due(_NoListing(), _cfg()) is False
    enqueue.assert_not_called()


def test_a_truncated_run_continues_without_waiting_for_the_interval() -> None:
    from app.ingestion import scheduler

    async def _truncated(**_: Any) -> dict[str, int]:
        return {"deleted": 10_000, "truncated": 1}

    with (
        patch.object(scheduler, "_reconcile_source_async", _truncated),
        patch.object(scheduler.reconcile_source_task, "apply_async") as enqueue,
    ):
        result = scheduler.reconcile_source_task.run(source_id="src-m", tenant_id="t1")
    assert result["truncated"] == 1
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["kwargs"] == {"source_id": "src-m", "tenant_id": "t1"}
