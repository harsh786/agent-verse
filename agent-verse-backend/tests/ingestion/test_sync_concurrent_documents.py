"""SYNC-CONC: a connector sync ingests its documents concurrently, bounded.

The sync loop awaited ``pipeline.ingest`` for one document before pulling the
next, so a sync of N scanned documents OCR'd them one after another (only the
pages within a document ran in parallel). Documents now run up to
INGESTION_SYNC_DOC_CONCURRENCY at once, while the cursor and the connector
acknowledgements still move in delta order and only past documents that
finished; failures stay per document and the job's counters are unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.scheduler import _sync_source_async
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily

_SRC, _TENANT = "src-c", "t-c"


@pytest.fixture(autouse=True)
def _concurrency(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_SYNC_DOC_CONCURRENCY", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _doc(i: int) -> RawDocument:
    return RawDocument(doc_id=f"d{i}", source_id=_SRC, tenant_id=_TENANT, content=b"x",
                       content_type="text/plain")


class _Connector:
    count = 10
    cancel_at: int | None = None
    tracker: Any = None
    acked: list[str]

    def __init__(self) -> None:
        self.acked = []
        _Connector.instance = self  # type: ignore[attr-defined]

    async def get_delta(self, config: SourceConfig, cursor: str | None) -> Any:
        for i in range(self.count):
            if self.cancel_at is not None and i == self.cancel_at:
                await self.tracker.request_cancel(_SRC, _TENANT)
            yield _doc(i), f"after:d{i}"

    async def acknowledge(self, raw_doc: RawDocument) -> None:
        self.acked.append(raw_doc.doc_id)


class _Pipeline:
    """Fake ingestion: per-document delays / outcomes; records overlap."""

    def __init__(self, delays: dict[str, float] | None = None, default: float = 0.02,
                 outcomes: dict[str, str] | None = None) -> None:
        self.delays = delays or {}
        self.default = default
        self.outcomes = outcomes or {}
        self.in_flight = self.peak = 0
        self.finished: set[str] = set()

    async def ingest(self, raw_doc: RawDocument, config: Any) -> PipelineResult:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(raw_doc.doc_id, self.default))
            outcome = self.outcomes.get(raw_doc.doc_id, "indexed")
            if outcome == "raise":
                raise RuntimeError(f"{raw_doc.doc_id} exploded")
            return PipelineResult(doc_id=raw_doc.doc_id, source_id=_SRC, tenant_id=_TENANT,
                                  status=outcome, chunks_created=2,
                                  error="bad" if outcome == "failed" else None)
        finally:
            self.in_flight -= 1
            self.finished.add(raw_doc.doc_id)


async def _sync(pipeline: _Pipeline, tracker: IngestionJobTracker | None = None,
                *, count: int = 10, cancel_at: int | None = None) -> tuple[dict, Any, Any]:
    tracker = tracker or IngestionJobTracker()
    job_id = await tracker.acquire_lock(_SRC, _TENANT)
    store = AsyncMock()
    store.get.return_value = SourceConfig(source_id=_SRC, tenant_id=_TENANT, name="s",
                                          family=SourceFamily.OBJECT_STORAGE,
                                          source_type="s3", collection_id="c")
    _Connector.count, _Connector.cancel_at, _Connector.tracker = count, cancel_at, tracker
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", AsyncMock(return_value=False)),
    ):
        result = await _sync_source_async(task=MagicMock(), source_id=_SRC, tenant_id=_TENANT,
                                          triggered_by="manual", job_id=job_id)
    job = tracker.get_job(str(job_id))
    return result, job, _Connector.instance  # type: ignore[attr-defined]


async def test_documents_are_ingested_concurrently_but_bounded() -> None:
    pipeline = _Pipeline(default=0.03)
    result, job, connector = await _sync(pipeline, count=10)
    assert pipeline.peak == 3  # INGESTION_SYNC_DOC_CONCURRENCY (was 1)
    assert result["docs_indexed"] == 10 and result["docs_failed"] == 0
    assert job.chunks_created == 20 and job.docs_discovered == 10
    assert job.cursor_after == "after:d9"
    assert connector.acked == [f"d{i}" for i in range(10)]


async def test_one_document_at_a_time_when_set_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_SYNC_DOC_CONCURRENCY", "1")
    get_settings.cache_clear()
    pipeline = _Pipeline(default=0.005)
    result, _job, connector = await _sync(pipeline, count=5)
    assert pipeline.peak == 1
    assert result["docs_indexed"] == 5
    assert connector.acked == [f"d{i}" for i in range(5)]


async def test_cursor_and_acks_never_pass_a_document_still_being_ingested() -> None:
    """d0 is slow, every later document is fast: no acknowledgement and no
    periodic cursor commit may get ahead of d0 while it is still running."""
    pipeline = _Pipeline(delays={"d0": 0.3}, default=0.001)
    tracker = IngestionJobTracker()
    commits: list[tuple[str, set[str]]] = []
    real_update = tracker.update_cursor

    async def _update_cursor(job: Any, cursor: str, config: Any, **kw: Any) -> Any:
        commits.append((cursor, set(pipeline.finished)))
        return await real_update(job, cursor, config, **kw)

    tracker.update_cursor = _update_cursor  # type: ignore[method-assign]
    result, job, connector = await _sync(pipeline, tracker, count=150)
    assert result["docs_indexed"] == 150
    assert connector.acked == [f"d{i}" for i in range(150)]
    assert [c for c, _ in commits] == ["after:d99", "after:d149"]
    for cursor, finished in commits:
        last = int(cursor.rsplit("d", 1)[1])
        assert {f"d{i}" for i in range(last + 1)} <= finished, cursor
    assert job.cursor_after == "after:d149"


async def test_a_cancel_waits_for_the_documents_in_flight_and_keeps_their_cursor() -> None:
    pipeline = _Pipeline(delays={"d0": 0.2}, default=0.001)
    result, job, connector = await _sync(pipeline, count=6, cancel_at=3)
    assert result["cancelled"] is True
    assert result["docs_indexed"] == 3
    assert connector.acked == ["d0", "d1", "d2"]
    assert job.cursor_after == "after:d2"


async def test_failures_stay_per_document_and_the_counters_add_up() -> None:
    pipeline = _Pipeline(
        delays={"d1": 0.05},
        outcomes={"d1": "raise", "d3": "failed", "d4": "skipped", "d6": "skipped"},
    )
    tracker = IngestionJobTracker()
    dlq: list[str] = []

    async def _add_to_dlq(**kw: Any) -> bool:
        dlq.append(kw["doc_id"])
        return True

    tracker.add_to_dlq = _add_to_dlq  # type: ignore[method-assign]
    result, job, connector = await _sync(pipeline, tracker, count=8)
    assert (result["docs_indexed"], result["docs_skipped"], result["docs_failed"]) == (4, 2, 2)
    assert sorted(dlq) == ["d1", "d3"]
    assert job.docs_discovered == 8
    assert connector.acked == [f"d{i}" for i in range(8)]  # both failures durably DLQ'd
    assert job.cursor_after == "after:d7"


async def test_a_lost_lock_stops_the_documents_in_flight_without_moving_the_cursor() -> None:
    from app.ingestion.job_tracker import SyncLockLostError

    pipeline = _Pipeline(delays={"d0": 5.0}, default=0.001)
    tracker = IngestionJobTracker()
    commits: list[str] = []

    async def _update_cursor(job: Any, cursor: str, config: Any, **kw: Any) -> Any:
        commits.append(cursor)

    tracker.update_cursor = _update_cursor  # type: ignore[method-assign]
    real_hold = tracker.hold

    async def _hold(*a: Any, **kw: Any) -> Any:
        lease = await real_hold(*a, **kw)
        calls = {"n": 0}
        real_check = lease.check

        def _check() -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise SyncLockLostError("another run owns the source")
            real_check()

        lease.check = _check
        return lease

    tracker.hold = _hold  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()
    started = loop.time()
    result, _job, connector = await _sync(pipeline, tracker, count=6)
    assert result["error"] == "lock_lost"
    assert loop.time() - started < 2.0  # the slow d0 was stopped, not awaited
    assert commits == [] and connector.acked == []
