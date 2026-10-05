"""TG-14: a manual MongoDB sync with failing documents lands them in the DLQ.

"Sync now" through the real Sources API, then the worker body the queued task
runs, against a MongoDB container and a migrated Postgres (the DLQ is durable,
so it needs the real table). One document fails the pipeline, one makes it
raise: both are in ``ingestion_dlq`` with a payload the retry job can rebuild,
and the job is ``partial``, not ``completed``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from pymongo import MongoClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.mongodb import MongoDbContainer

from app.ingestion.job_tracker import IngestionJobTracker, raw_document_from_dlq_json
from app.ingestion.source_config import PipelineResult
from app.ingestion.source_store import SourceConfigStore
from httpx import ASGITransport, AsyncClient

from tests.api.test_ingestion_api import _CTX, _auth, _make_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mongo() -> Iterator[tuple[str, int]]:
    with MongoDbContainer("mongo:7.0", username="root", password="pw", dbname="shop") as c:
        host, port = c.get_container_host_ip(), int(c.get_exposed_port(27017))
        client: MongoClient[dict[str, Any]] = MongoClient(
            host, port, username="root", password="pw", serverSelectionTimeoutMS=30000
        )
        try:
            client["shop"]["tickets"].insert_many(
                [{"n": i, "title": f"ticket {i}"} for i in range(5)]
            )
        finally:
            client.close()
        yield host, port


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, pool_size=4, max_overflow=0)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'professional', true) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _CTX.tenant_id, "email": f"{_CTX.tenant_id}@example.test"},
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _FailingPipeline:
    """Fails ticket 1 (pipeline failure) and raises on ticket 3."""

    async def ingest(self, raw_doc: Any, config: Any) -> PipelineResult:
        content = raw_doc.content.decode()
        if "title: ticket 3" in content:
            raise RuntimeError("embedder exploded")
        status = "failed" if "title: ticket 1" in content else "indexed"
        return PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            status=status,
            error="chunker rejected the document" if status == "failed" else None,
        )


async def test_a_manual_sync_puts_failing_documents_in_the_dlq(
    mongo: tuple[str, int], db: Any
) -> None:
    from app.ingestion.scheduler import _sync_source_async

    host, port = mongo
    tracker = IngestionJobTracker(db=db, system_db=db)
    store = SourceConfigStore(db=db)
    app = _make_app(
        ingestion_source_store=store, ingestion_job_tracker=tracker, ingestion_pipeline=MagicMock()
    )
    # Same event loop as the test's DB engine (TestClient would run its own).
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    created = await client.post(
        "/sources",
        headers=_auth(),
        json={
            "name": f"tickets-{uuid.uuid4().hex[:6]}",
            "family": "nosql_database",
            "source_type": "mongodb",
            "collection_id": "kb-tickets",
            "connection_config": {
                "uri": f"mongodb://{host}:{port}/shop",
                "username": "root",
                "password": "pw",
                "database": "shop",
                "collection": "tickets",
            },
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["source_id"]

    with patch("app.ingestion.scheduler.sync_source_task") as task_mod:
        queued_resp = await client.post(f"/sources/{source_id}/sync", headers=_auth())
        assert queued_resp.status_code == 202, queued_resp.text
        queued = task_mod.apply_async.call_args.kwargs["kwargs"]

    task = MagicMock()
    task.retry.side_effect = lambda exc, **_kw: exc
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, _FailingPipeline(), store),
        ),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
    ):
        result = await _sync_source_async(task=task, **queued)

    assert (result["docs_indexed"], result["docs_failed"]) == (3, 2), result
    entries = await tracker.list_dlq_entries(_CTX.tenant_id, source_id=source_id)
    assert len(entries) == 2
    errors = sorted(e["error_message"] for e in entries)
    assert errors[0] == "RuntimeError: embedder exploded"
    assert errors[1] == "chunker rejected the document"
    # Each entry carries the document itself, so the DLQ retry can re-run it.
    for entry in entries:
        full = await tracker.get_dlq_entry(str(entry["id"]), _CTX.tenant_id)
        assert full is not None
        rebuilt = raw_document_from_dlq_json(
            full["raw_doc_json"], source_id=source_id, tenant_id=_CTX.tenant_id
        )
        assert rebuilt is not None and rebuilt.doc_id == entry["doc_id"]
        assert b"title: ticket" in rebuilt.content

    status = (await client.get(f"/sources/{source_id}/sync/status", headers=_auth())).json()
    await client.aclose()
    assert status["status"] == "partial", status
    assert status["job_id"] == queued["job_id"]
