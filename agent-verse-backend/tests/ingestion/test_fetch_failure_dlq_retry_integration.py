"""USR-4 on real Postgres: a document that fails to fetch is retried from the DLQ.

A URL the sync could not download used to be logged and dropped — nothing
reached ``ingestion_dlq``, so the document was lost until someone synced the
Source by hand. Now the sync writes it to the durable DLQ with a replay
reference; the DLQ retry job fetches it again (``replay_event``) with
exponential backoff between attempts, resolves the entry once the fetch
succeeds, and gives up (permanent) after the retry cap or at once for a failure
no retry can fix (404).

Runs the real sync task and the real DLQ retry job against a migrated
testcontainer Postgres (``pg_url``); HTTP is answered in-process.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import PipelineResult
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_REPLAY_KEY,
    SourceConfig,
    SourceFamily,
)
from app.ingestion.source_store import SourceConfigStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PDF = b"%PDF-1.4 recovered handbook"


class _RetriedError(Exception):
    pass


class _Task:
    def retry(self, **_kwargs: Any) -> _RetriedError:
        return _RetriedError()


class _RecordingPipeline:
    """Stands in for the indexing stages: fails failure documents exactly like
    the real pipeline (connector_failure → failed) and records the rest."""

    def __init__(self) -> None:
        self.indexed: list[Any] = []

    async def ingest(self, raw_doc: Any, source_config: Any, **_: Any) -> PipelineResult:
        result = PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=source_config.source_id,
            tenant_id=source_config.tenant_id,
            status="pending",
        )
        reason = (raw_doc.metadata or {}).get(CONNECTOR_FAILURE_KEY)
        if reason:
            result.status, result.error = "failed", f"connector: {reason}"
        else:
            self.indexed.append(raw_doc)
            result.status = "indexed"
        return result

    async def run(self, raw_doc: Any, *, source_config: Any) -> PipelineResult:
        return await self.ingest(raw_doc, source_config)


def _site(status: Callable[[str], int]) -> Any:
    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        code = status(request.url.path)
        return httpx.Response(code, content=_PDF if code == 200 else b"down", request=request)

    return _send


@pytest_asyncio.fixture
async def world(pg_url: str) -> Any:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = f"usr4-{uuid.uuid4().hex[:8]}"
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :e, 'free', true)"
            ),
            {"id": tenant, "e": f"{tenant}@example.test"},
        )
    store = SourceConfigStore(db=factory)
    tracker = IngestionJobTracker(db=factory, system_db=factory)
    yield factory, store, tracker, tenant
    await engine.dispose()


async def _source(store: SourceConfigStore, tenant: str, urls: list[str]) -> SourceConfig:
    config = SourceConfig(
        source_id=uuid.uuid4().hex,
        tenant_id=tenant,
        name="handbooks",
        family=SourceFamily.FILE_SYSTEM,
        source_type="pdf_file",
        connection_config={"urls": urls},
        collection_id="col-usr4",
    )
    await store.create(config)
    return config


async def _sync(world: Any, config: SourceConfig, pipeline: Any) -> dict[str, Any]:
    from app.ingestion.scheduler import _sync_source_async

    _factory, store, tracker, _tenant = world
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        contextlib.suppress(_RetriedError),
    ):
        return await _sync_source_async(
            task=_Task(), source_id=config.source_id, tenant_id=config.tenant_id,
            triggered_by="scheduler",
        )
    return {}


async def _retry_job(world: Any, pipeline: Any) -> dict[str, Any]:
    from app.ingestion.scheduler import _retry_dlq_async

    _factory, store, tracker, _tenant = world
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, pipeline, store),
    ):
        return await _retry_dlq_async()


async def _rows(factory: Any, source_id: str) -> list[dict[str, Any]]:
    async with factory() as s:
        result = await s.execute(
            text(
                "SELECT id, doc_id, retry_count, next_retry_at > now() AS backing_off, "
                "COALESCE(permanent_failure, false) AS permanent, resolved_at, raw_doc_json, "
                "error_message, last_error FROM ingestion_dlq WHERE source_id = :sid"
            ),
            {"sid": source_id},
        )
        return [dict(r) for r in result.mappings()]


async def _backoff_elapsed(factory: Any, source_id: str) -> None:
    async with factory() as s, s.begin():
        await s.execute(
            text("UPDATE ingestion_dlq SET next_retry_at = now() WHERE source_id = :sid"),
            {"sid": source_id},
        )


async def test_failed_fetch_is_queued_retried_with_backoff_and_recovered(world: Any) -> None:
    factory, store, _tracker, tenant = world
    url = "https://example.com/handbook.pdf"
    config = await _source(store, tenant, [url])
    up = {"ok": False}

    with patch.object(httpx.AsyncClient, "send", _site(lambda _p: 200 if up["ok"] else 503)):
        # 1. The sync cannot download it: it lands in the durable DLQ.
        await _sync(world, config, _RecordingPipeline())
        (row,) = await _rows(factory, config.source_id)
        assert row["retry_count"] == 0 and row["resolved_at"] is None
        assert "HTTP 503" in row["error_message"]
        payload = json.loads(row["raw_doc_json"])
        assert payload["metadata"][CONNECTOR_REPLAY_KEY] == {"kind": "url", "url": url}

        # 2. Retried while still down: still failed, and backed off (not rescanned).
        pipeline = _RecordingPipeline()
        assert (await _retry_job(world, pipeline))["still_failed"] == 1
        (row,) = await _rows(factory, config.source_id)
        assert row["retry_count"] == 1 and row["backing_off"] is True
        assert (await _retry_job(world, pipeline))["retried"] == 0  # waits out the backoff

        # 3. The URL is back once the backoff elapsed: the retry re-fetches it.
        up["ok"] = True
        await _backoff_elapsed(factory, config.source_id)
        assert (await _retry_job(world, pipeline))["succeeded"] == 1
    (row,) = await _rows(factory, config.source_id)
    assert row["resolved_at"] is not None
    (doc,) = pipeline.indexed
    assert doc.content == _PDF and doc.source_url == url


async def test_retries_stop_at_the_cap_and_a_404_is_permanent_at_once(world: Any) -> None:
    factory, store, _tracker, tenant = world
    flaky, gone = "https://example.com/flaky.pdf", "https://example.com/gone.pdf"
    config = await _source(store, tenant, [flaky, gone])
    status = {"/flaky.pdf": 503, "/gone.pdf": 404}

    with patch.object(httpx.AsyncClient, "send", _site(lambda p: status[p])):
        await _sync(world, config, _RecordingPipeline())
        rows = {r["doc_id"]: r for r in await _rows(factory, config.source_id)}
        assert len(rows) == 2

        # The 404 can never succeed: the first retry marks it permanent.
        await _retry_job(world, _RecordingPipeline())
        rows = {json.loads(r["raw_doc_json"])["source_url"]: r
                for r in await _rows(factory, config.source_id)}
        assert rows[gone]["permanent"] is True
        assert rows[flaky]["permanent"] is False and rows[flaky]["retry_count"] == 1

        # The 503 keeps failing: after the cap (5 attempts) it is given up on.
        for _ in range(5):
            await _backoff_elapsed(factory, config.source_id)
            await _retry_job(world, _RecordingPipeline())
        rows = {json.loads(r["raw_doc_json"])["source_url"]: r
                for r in await _rows(factory, config.source_id)}
        assert rows[flaky]["permanent"] is True
        assert rows[flaky]["retry_count"] == 5
