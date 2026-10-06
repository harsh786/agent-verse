"""A failed single-URL ingest goes to the ingestion DLQ and is replayed by the retry job.

``POST /knowledge/ingest/url`` was synchronous with no retry queue: a transient
failure (upstream down / timeout, embedder or screening unavailable) was lost
unless the caller retried. Now such a failure is dead-lettered as a source-less
entry (``{"kind": "url", ...}``, one open entry per (tenant, collection, URL)),
the caller still gets the honest error plus ``X-Ingestion-DLQ-Id``, and the
DLQ retry job re-runs the route's own ingest code. Permanent failures (blocked,
404, legal hold) are not queued.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.knowledge import (
    UrlIngestHTTPException,
    stable_url_document_id,
    url_ingest_failure_retryable,
)
from app.api.knowledge import router as knowledge_router
from app.ingestion.url_ingest_replay import (
    UrlReplayPermanentError,
    UrlReplayRetryableError,
    replay_url_ingest,
    url_ingest_dlq_payload,
)
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-urldlq", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_test_url_dlq"
_URL = "https://example.test/policy"


def _client(store: KnowledgeStore, tracker: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = FakeProvider(embed_dim=8)
    app.state.ingestion_job_tracker = tracker
    return TestClient(app, raise_server_exceptions=False)


def _store() -> tuple[KnowledgeStore, str]:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="web"), tenant_ctx=_CTX)
    return store, cid


def _tracker() -> MagicMock:
    tracker = MagicMock()
    tracker.dead_letter_sourceless = AsyncMock(return_value="dlq-123")
    tracker.resolve_sourceless_dlq = AsyncMock()
    return tracker


def _fail_fetch(exc: Exception) -> Any:
    async def _fake(url: str, source_type: str) -> Any:
        raise exc

    return patch("app.api.knowledge._fetch_url_resource", new=_fake)


def _ok_fetch(text: str) -> Any:
    from app.ingestion.web_fetch import WebResource

    async def _fake(url: str, source_type: str) -> WebResource:
        return WebResource(
            url=url, final_url=url, status=200,
            content_type="text/plain; charset=utf-8", data=text.encode(),
        )

    return patch("app.api.knowledge._fetch_url_resource", new=_fake)


def _post(client: TestClient, cid: str) -> Any:
    return client.post(
        "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL},
        headers={"X-API-Key": _KEY},
    )


def test_transient_failure_is_dead_lettered_with_replay_payload() -> None:
    store, cid = _store()
    tracker = _tracker()
    client = _client(store, tracker)
    with _fail_fetch(UrlIngestHTTPException(504, "upstream timed out", retryable=True)):
        resp = _post(client, cid)
    assert resp.status_code == 504
    assert resp.headers["X-Ingestion-DLQ-Id"] == "dlq-123"
    tracker.dead_letter_sourceless.assert_awaited_once()
    kwargs = tracker.dead_letter_sourceless.await_args.kwargs
    doc_id = stable_url_document_id(_CTX.tenant_id, cid, _URL)
    assert kwargs["tenant_id"] == _CTX.tenant_id
    assert kwargs["doc_id"] == doc_id  # one open entry per (tenant, collection, URL)
    assert kwargs["payload"] == {
        "kind": "url", "url": _URL, "collection_id": cid,
        "source_type": "web", "document_id": doc_id,
    }
    assert "504" in kwargs["error"]
    tracker.resolve_sourceless_dlq.assert_not_awaited()


def test_embedder_unavailable_is_dead_lettered() -> None:
    store, cid = _store()
    tracker = _tracker()
    client = _client(store, tracker)
    client.app.state.embedder = None  # type: ignore[attr-defined]
    with _ok_fetch("A real page about the refund policy. " * 10):
        resp = _post(client, cid)
    assert resp.status_code == 503
    tracker.dead_letter_sourceless.assert_awaited_once()


@pytest.mark.parametrize(
    "exc",
    [
        UrlIngestHTTPException(400, "URL blocked for security reasons", retryable=False),
        UrlIngestHTTPException(502, "upstream answered 404", retryable=False),
        UrlIngestHTTPException(413, "too large", retryable=False),
    ],
)
def test_permanent_failure_is_not_queued(exc: Exception) -> None:
    store, cid = _store()
    tracker = _tracker()
    client = _client(store, tracker)
    with _fail_fetch(exc):
        resp = _post(client, cid)
    assert resp.status_code >= 400
    assert "X-Ingestion-DLQ-Id" not in resp.headers
    tracker.dead_letter_sourceless.assert_not_awaited()


def test_success_resolves_the_open_entry() -> None:
    store, cid = _store()
    tracker = _tracker()
    client = _client(store, tracker)
    with _ok_fetch("Refunds are accepted within 30 days of purchase. " * 5):
        resp = _post(client, cid)
    assert resp.status_code == 201, resp.text
    tracker.resolve_sourceless_dlq.assert_awaited_once_with(
        tenant_id=_CTX.tenant_id, doc_id=stable_url_document_id(_CTX.tenant_id, cid, _URL)
    )
    tracker.dead_letter_sourceless.assert_not_awaited()


def test_dlq_error_never_masks_the_original_failure() -> None:
    store, cid = _store()
    tracker = _tracker()
    tracker.dead_letter_sourceless = AsyncMock(side_effect=RuntimeError("db down"))
    client = _client(store, tracker)
    with _fail_fetch(UrlIngestHTTPException(502, "connection refused", retryable=True)):
        resp = _post(client, cid)
    assert resp.status_code == 502
    assert "X-Ingestion-DLQ-Id" not in resp.headers


def test_retryable_classification() -> None:
    assert url_ingest_failure_retryable(UrlIngestHTTPException(502, "x", retryable=True))
    assert not url_ingest_failure_retryable(UrlIngestHTTPException(502, "x", retryable=False))
    assert url_ingest_failure_retryable(HTTPException(503, "embedder"))
    assert not url_ingest_failure_retryable(HTTPException(409, "legal hold"))
    assert not url_ingest_failure_retryable(HTTPException(422, "no content"))
    assert url_ingest_failure_retryable(RuntimeError("db blip"))


# ── replay (worker side) ──────────────────────────────────────────────────────


def _replay_request(store: KnowledgeStore) -> Any:
    state = SimpleNamespace(
        knowledge_store=store, embedder=FakeProvider(embed_dim=8), manage_pools=False
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state), state=SimpleNamespace(tenant=_CTX)
    )


async def test_replay_indexes_the_url_under_its_stable_id_idempotently() -> None:
    store, cid = _store()
    payload = {"kind": "url", "url": _URL, "collection_id": cid, "source_type": "web"}
    req = _replay_request(store)
    with _ok_fetch("Refunds are accepted within 30 days of purchase. " * 5):
        first = await replay_url_ingest(payload, tenant_id=_CTX.tenant_id, pipeline=None,
                                        request=req)
        second = await replay_url_ingest(payload, tenant_id=_CTX.tenant_id, pipeline=None,
                                         request=req)
    doc_id = stable_url_document_id(_CTX.tenant_id, cid, _URL)
    assert first["document_id"] == doc_id and first["chunks_ingested"] > 0
    assert second["deduplicated"] is True and second["document_id"] == doc_id
    chunks = store._data[(_CTX.tenant_id, cid)].chunks
    assert {c.document_id for c in chunks} == {doc_id}


async def test_replay_classifies_failures() -> None:
    store, cid = _store()
    payload = {"kind": "url", "url": _URL, "collection_id": cid}
    req = _replay_request(store)
    with (
        _fail_fetch(UrlIngestHTTPException(504, "timeout", retryable=True)),
        pytest.raises(UrlReplayRetryableError),
    ):
        await replay_url_ingest(payload, tenant_id=_CTX.tenant_id, pipeline=None, request=req)
    with (
        _fail_fetch(UrlIngestHTTPException(400, "blocked", retryable=False)),
        pytest.raises(UrlReplayPermanentError),
    ):
        await replay_url_ingest(payload, tenant_id=_CTX.tenant_id, pipeline=None, request=req)
    with pytest.raises(UrlReplayPermanentError):
        await replay_url_ingest({"kind": "url"}, tenant_id=_CTX.tenant_id, pipeline=None,
                                request=req)


def test_payload_detection() -> None:
    assert url_ingest_dlq_payload(json.dumps({"kind": "url", "url": "u"})) == {
        "kind": "url", "url": "u"
    }
    assert url_ingest_dlq_payload(json.dumps({"kind": "repository"})) is None
    assert url_ingest_dlq_payload("not json") is None
    assert url_ingest_dlq_payload(None) is None


async def _run_retry(entry: dict[str, Any], replay: Any) -> tuple[str, MagicMock]:
    from app.ingestion import scheduler

    tracker = MagicMock()
    for name in ("resolve_dlq_entry", "increment_dlq_retry", "mark_dlq_permanent_failure"):
        setattr(tracker, name, AsyncMock())
    with patch("app.ingestion.url_ingest_replay.replay_url_ingest", new=replay):
        outcome = await scheduler._retry_one_dlq_entry(entry, tracker, object(), MagicMock())
    return outcome, tracker


def _entry(retry_count: int = 0) -> dict[str, Any]:
    return {
        "dlq_id": "d1", "tenant_id": "t1", "source_id": None, "doc_id": "doc",
        "retry_count": retry_count,
        "raw_doc_json": json.dumps({"kind": "url", "url": _URL, "collection_id": "c"}),
    }


async def test_retry_job_resolves_a_replayed_url_entry() -> None:
    replay = AsyncMock(return_value={"chunks_ingested": 2})
    outcome, tracker = await _run_retry(_entry(), replay)
    assert outcome == "succeeded"
    assert replay.await_args.kwargs["tenant_id"] == "t1"
    tracker.resolve_dlq_entry.assert_awaited_once_with("d1", "t1")


async def test_retry_job_backs_off_a_still_failing_url_entry() -> None:
    replay = AsyncMock(side_effect=UrlReplayRetryableError("504: timeout"))
    outcome, tracker = await _run_retry(_entry(), replay)
    assert outcome == "still_failed"
    tracker.increment_dlq_retry.assert_awaited_once()
    tracker.mark_dlq_permanent_failure.assert_not_awaited()


async def test_retry_job_parks_a_permanent_url_failure() -> None:
    replay = AsyncMock(side_effect=UrlReplayPermanentError("400: blocked"))
    outcome, tracker = await _run_retry(_entry(), replay)
    assert outcome == "permanent"
    tracker.mark_dlq_permanent_failure.assert_awaited_once_with("d1", "t1")


async def test_retry_job_stops_at_the_retry_cap() -> None:
    replay = AsyncMock()
    outcome, tracker = await _run_retry(_entry(retry_count=99), replay)
    assert outcome == "permanent"
    replay.assert_not_awaited()
