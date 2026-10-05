"""MongoDB ingestion against a hostile / broken server (a fake mongod, no Docker).

* C1 / MDB-12 / TG-10: a server that accepts and then stalls must fail the sync
  within the configured bound — recorded as a failed job with the error — instead
  of blocking the worker (and ``GET /sources/{id}/health``) forever.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors import mongodb_connector as mc
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import PipelineResult, SourceConfig
from app.ingestion.source_store import SourceConfigStore
from tests.ingestion.fake_mongod import FakeMongod


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    # 127.0.0.1 only — NOT "localhost" (the member tests rely on that).
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "127.0.0.1")
    monkeypatch.setenv("INGESTION_MONGODB_SOCKET_TIMEOUT_MS", "1000")
    monkeypatch.setenv("INGESTION_MONGODB_MAX_TIME_MS", "700")
    monkeypatch.setenv("INGESTION_MONGODB_SERVER_SELECTION_TIMEOUT_MS", "2000")
    monkeypatch.setenv("INGESTION_MONGODB_CONNECT_TIMEOUT_MS", "2000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def stalled() -> Iterator[FakeMongod]:
    srv = FakeMongod(stall=True)
    yield srv
    srv.close()


def _config(uri: str, **extra: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-stall",
        tenant_id="t1",
        name="stalled mongo",
        family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb",
        enabled=True,
        collection_id="kb-1",
        connection_config={"uri": uri, "database": "db", "collection": "c", **extra},
    )


class _Pipeline:
    def __init__(self) -> None:
        self.docs: list[Any] = []

    async def ingest(self, raw_doc: Any, config: Any) -> PipelineResult:
        self.docs.append(raw_doc)
        return PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            status="indexed",
        )


# ── Settings: every bound is set, from operator settings ──────────────────────


def test_driver_timeouts_come_from_settings() -> None:
    s = mc._settings({"uri": "mongodb://h/", "database": "d"})
    assert s.kwargs["socketTimeoutMS"] == 1000
    assert s.kwargs["connectTimeoutMS"] == 2000
    assert s.kwargs["serverSelectionTimeoutMS"] == 2000
    assert s.max_time_ms == 700


def test_defaults_bound_every_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    defaults = Settings()
    for name in (
        "ingestion_mongodb_socket_timeout_ms",
        "ingestion_mongodb_connect_timeout_ms",
        "ingestion_mongodb_server_selection_timeout_ms",
        "ingestion_mongodb_max_time_ms",
    ):
        assert 0 < getattr(defaults, name) <= 120_000, name
    # The server gives up on a query before the socket does, so a slow query
    # fails with an honest MaxTimeMSExpired rather than a dropped connection.
    assert defaults.ingestion_mongodb_max_time_ms < defaults.ingestion_mongodb_socket_timeout_ms


def test_a_tenant_cannot_unbound_the_waits() -> None:
    s = mc._settings(
        {
            "uri": "mongodb://h/?socketTimeoutMS=0&maxTimeMS=0&connectTimeoutMS=0",
            "database": "d",
            "timeout_ms": 10_000_000,
        }
    )
    # Driver kwargs override URI options; the tenant's timeout_ms may only lower.
    assert s.kwargs["socketTimeoutMS"] == 1000
    assert s.kwargs["connectTimeoutMS"] == 2000
    assert s.kwargs["serverSelectionTimeoutMS"] == 2000
    lower = mc._settings({"uri": "mongodb://h/", "database": "d", "timeout_ms": 500})
    assert lower.kwargs["connectTimeoutMS"] == 500
    assert lower.kwargs["serverSelectionTimeoutMS"] == 500


# ── A stalled server fails within the bound ───────────────────────────────────


async def test_stalled_find_fails_within_the_bound(stalled: FakeMongod) -> None:
    settings = mc._settings({"uri": f"mongodb://{stalled.me}/", "database": "db"})
    t0 = time.monotonic()
    with pytest.raises(Exception, match=r"(?i)timed out"):
        async with mc._connected(settings) as (client, s):
            await asyncio.wait_for(
                asyncio.to_thread(mc._fetch_page, client, s, "c", None, 10), timeout=30
            )
    # One retryable-read retry at most: ~2 x socketTimeoutMS, nowhere near forever.
    assert time.monotonic() - t0 < 15
    finds = [d for d in stalled.documents if next(iter(d)).lower() == "find"]
    assert finds and all(d.get("maxTimeMS") == 700 for d in finds), finds


async def test_stalled_health_check_reports_the_timeout(stalled: FakeMongod) -> None:
    t0 = time.monotonic()
    health = await asyncio.wait_for(
        mc.MongoDBConnector().validate_connection(_config(f"mongodb://{stalled.me}/")),
        timeout=30,
    )
    assert health.ok is False
    assert "timed out" in (health.error or "").lower()
    assert time.monotonic() - t0 < 15
    lists = [d for d in stalled.documents if next(iter(d)).lower() == "listcollections"]
    assert all(d.get("maxTimeMS") == 700 for d in lists), lists


async def test_stalled_server_fails_the_sync_with_an_error(stalled: FakeMongod) -> None:
    from app.ingestion.scheduler import _sync_source_async

    store = SourceConfigStore()
    tracker = IngestionJobTracker()
    config = _config(f"mongodb://{stalled.me}/")
    await store.create(config)
    pipeline = _Pipeline()
    task = MagicMock()
    task.retry.side_effect = lambda exc, **_kw: exc  # surface the real error

    t0 = time.monotonic()
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        pytest.raises(Exception, match=r"(?i)timed out"),
    ):
        await asyncio.wait_for(
            _sync_source_async(
                task=task, source_id=config.source_id, tenant_id="t1", triggered_by="manual"
            ),
            timeout=30,
        )
    assert time.monotonic() - t0 < 15

    (job,) = tracker.list_jobs_for_source(config.source_id)
    assert job.status == "failed"
    assert "timed out" in job.error_message.lower()
    assert job.docs_failed >= 1  # USR-1: the source-level failure is counted
    assert pipeline.docs == []
