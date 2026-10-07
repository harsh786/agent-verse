"""S3-WEBHOOK-ERRORS: a failed S3 event fetch is reported, never swallowed.

``S3Connector._fetch_single`` caught every exception, logged it and yielded
nothing, so an S3 event whose object could not be fetched (deleted, access
denied, throttled, endpoint down) vanished: no document, no failure, no DLQ
entry. Each failed record now yields a failure document carrying the reason and
whether a retry can help; the pipeline fails it with that reason (→ DLQ), and the
DLQ retry loop gives up at once on a failure that no retry can fix.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from app.ingestion.connectors.s3_connector import S3Connector, s3_document_id
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_FAILURE_RETRYABLE_KEY,
    RawDocument,
    SourceConfig,
    SourceFamily,
)


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-s3",
        tenant_id="t-s3",
        name="s3",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="s3",
        connection_config={"bucket": "b", **cc},
        max_doc_size_bytes=100,
    )


def _event(*keys: str) -> bytes:
    records = [
        {"eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": "b"}, "object": {"key": k}}}
        for k in keys
    ]
    return json.dumps({"Records": records}).encode()


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


def _ok_object(body: bytes = b"hello") -> dict[str, Any]:
    return {
        "Body": MagicMock(read=MagicMock(return_value=body)),
        "ContentType": "text/plain",
        "ContentLength": len(body),
    }


async def _webhook(s3: MagicMock, *keys: str) -> list[RawDocument]:
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        return [d async for d in S3Connector().on_webhook(_config(), _event(*keys), {})]


@pytest.mark.parametrize(
    ("error", "retryable", "reason"),
    [
        (_client_error("NoSuchKey", 404), False, "NoSuchKey"),
        (_client_error("AccessDenied", 403), False, "AccessDenied"),
        (_client_error("SlowDown", 503), True, "SlowDown"),
        (_client_error("InternalError", 500), True, "InternalError"),
        (_client_error("RequestTimeout", 400), True, "RequestTimeout"),
        (EndpointConnectionError(endpoint_url="https://s3.example"), True, "connect"),
        (NoCredentialsError(), False, "credentials"),
        (RuntimeError("socket closed"), True, "socket closed"),
    ],
)
async def test_a_failed_fetch_is_reported_with_reason_and_retryability(
    error: Exception, retryable: bool, reason: str
) -> None:
    s3 = MagicMock()
    s3.get_object.side_effect = error
    docs = await _webhook(s3, "gone.txt")
    assert len(docs) == 1
    meta = docs[0].metadata
    assert reason.lower() in meta[CONNECTOR_FAILURE_KEY].lower()
    assert meta[CONNECTOR_FAILURE_RETRYABLE_KEY] is retryable
    assert docs[0].doc_id == s3_document_id(_config(), "b", "gone.txt")
    assert docs[0].source_url == "s3://b/gone.txt"
    assert docs[0].content == b""


async def test_an_oversized_object_is_a_permanent_failure_and_is_not_read() -> None:
    s3 = MagicMock()
    body = MagicMock()
    s3.get_object.return_value = {"Body": body, "ContentLength": 10_000}
    docs = await _webhook(s3, "big.bin")
    assert docs[0].metadata[CONNECTOR_FAILURE_RETRYABLE_KEY] is False
    assert "size cap" in docs[0].metadata[CONNECTOR_FAILURE_KEY]
    body.read.assert_not_called()


async def test_one_failed_record_does_not_hide_the_others() -> None:
    s3 = MagicMock()
    s3.get_object.side_effect = [_client_error("NoSuchKey", 404), _ok_object(b"fine")]
    docs = await _webhook(s3, "gone.txt", "ok.txt")
    assert [d.doc_id for d in docs] == [
        s3_document_id(_config(), "b", "gone.txt"), s3_document_id(_config(), "b", "ok.txt")
    ]
    assert CONNECTOR_FAILURE_KEY in docs[0].metadata
    assert docs[1].content == b"fine"
    assert CONNECTOR_FAILURE_KEY not in (docs[1].metadata or {})


async def test_a_client_construction_failure_is_reported_too() -> None:
    with patch("boto3.Session", side_effect=RuntimeError("no creds")):
        docs = [d async for d in S3Connector().on_webhook(_config(), _event("a.txt"), {})]
    assert len(docs) == 1
    assert "no creds" in docs[0].metadata[CONNECTOR_FAILURE_KEY]


async def test_the_pipeline_fails_the_event_with_the_reason_and_retryability() -> None:
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(knowledge_store=MagicMock(), embedder=object())
    doc = RawDocument(
        doc_id="s3://b/gone.txt",
        source_id="src-s3",
        tenant_id="t-s3",
        content=b"",
        content_type="application/octet-stream",
        metadata={
            CONNECTOR_FAILURE_KEY: "s3 GetObject failed: NoSuchKey",
            CONNECTOR_FAILURE_RETRYABLE_KEY: False,
        },
    )
    result = await pipeline.ingest(doc, _config())
    assert result.status == "failed"
    assert "NoSuchKey" in result.error
    assert "permanent" in result.error


async def test_dlq_retry_gives_up_at_once_on_a_permanent_connector_failure() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = {
        "doc_id": "s3://b/gone.txt",
        "source_id": "src-s3",
        "tenant_id": "t-s3",
        "content": "",
        "content_type": "application/octet-stream",
        "metadata": {
            CONNECTOR_FAILURE_KEY: "s3 GetObject failed: NoSuchKey",
            CONNECTOR_FAILURE_RETRYABLE_KEY: False,
        },
    }
    entry = {
        "dlq_id": "d1",
        "tenant_id": "t-s3",
        "source_id": "src-s3",
        "doc_id": "s3://b/gone.txt",
        "retry_count": 0,
        "raw_doc_json": json.dumps(doc),
    }
    tracker = MagicMock()
    tracker.mark_dlq_permanent_failure = AsyncMock()
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
    outcome = await _retry_one_dlq_entry(entry, tracker, pipeline, MagicMock())
    assert outcome == "permanent"
    tracker.mark_dlq_permanent_failure.assert_awaited_once_with("d1", "t-s3")
    pipeline.run.assert_not_called()
