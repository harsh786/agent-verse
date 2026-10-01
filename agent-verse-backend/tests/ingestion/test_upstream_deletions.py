"""STABLE-DOC-IDS: documents deleted upstream are removed after a complete sync.

Connectors that can list what exists upstream (``list_live_doc_ids``) let the
worker delete the Source's indexed documents that are gone — only after a
failure-free run, never for documents under legal hold (or when the hold cannot
be verified), and never for connectors that cannot know (``None``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.base_connector import BaseConnector, ConnectionHealth, stable_doc_id
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.scheduler import _sync_source_async
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
    def __init__(self, held: set[str] | None = None, broken: bool = False) -> None:
        self.held = held or set()
        self.broken = broken

    async def is_under_hold(self, *, tenant_id: str, resource_id: str) -> bool:
        if self.broken:
            raise RuntimeError("hold store down")
        return resource_id in self.held


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
        if await store.get(config.source_id, "t1") is None:
            await store.create(config)
        with (
            patch(
                "app.ingestion.scheduler._build_worker_ingestion",
                return_value=(tracker, pipeline, store),
            ),
            patch(
                "app.ingestion.connector_registry.get_connector",
                return_value=_connector(upstream),
            ),
            patch("app.ingestion.scheduler._build_worker_legal_holds", return_value=holds),
        ):
            return await _sync_source_async(
                task=MagicMock(), source_id=config.source_id, tenant_id="t1",
                triggered_by="manual",
            )

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
