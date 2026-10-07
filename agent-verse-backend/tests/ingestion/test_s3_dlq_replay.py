"""S3-DLQ-REPLAY: retrying a retryable S3 webhook failure re-fetches the object.

A webhook fetch that failed transiently (throttled, 5xx, connection error) went
to the DLQ as an empty failure document; the retry loop replayed that document,
which can never succeed — the object was never fetched again. The failure
document now carries a replay reference (``connector_replay``: bucket + key),
stored with the DLQ entry, and the retry loop asks the connector to replay the
original event: the object is fetched again and the fresh document indexed.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from botocore.exceptions import ClientError

from app.ingestion.connectors.s3_connector import S3Connector
from app.ingestion.job_tracker import _json_default
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_FAILURE_RETRYABLE_KEY,
    CONNECTOR_REPLAY_KEY,
    RawDocument,
    SourceConfig,
    SourceFamily,
)


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="src-s3",
        tenant_id="t-s3",
        name="s3",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="s3",
        connection_config={"bucket": "b"},
    )


def _event(key: str) -> bytes:
    record = {
        "eventName": "ObjectCreated:Put",
        "s3": {"bucket": {"name": "b"}, "object": {"key": key}},
    }
    return json.dumps({"Records": [record]}).encode()


def _throttled() -> ClientError:
    return ClientError(
        {"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "GetObject"
    )


def _ok(body: bytes) -> dict[str, Any]:
    return {"Body": MagicMock(read=MagicMock(return_value=body)), "ContentLength": len(body)}


def dlq_entry_for(doc: RawDocument) -> dict[str, Any]:
    """The DLQ row add_to_dlq writes for ``doc`` (same serialization)."""
    return {
        "dlq_id": "d1",
        "tenant_id": doc.tenant_id,
        "source_id": doc.source_id,
        "doc_id": doc.doc_id,
        "retry_count": 0,
        "raw_doc_json": json.dumps(dataclasses.asdict(doc), default=_json_default),
    }


class _Pipeline:
    def __init__(self) -> None:
        self.docs: list[RawDocument] = []

    async def run(self, doc: RawDocument, *, source_config: SourceConfig) -> Any:
        self.docs.append(doc)
        failed = CONNECTOR_FAILURE_KEY in (doc.metadata or {})
        return SimpleNamespace(
            success=not failed,
            skipped=False,
            skip_reason="",
            error="x" if failed else "",
            status="failed" if failed else "indexed",
        )


def _tracker() -> MagicMock:
    tracker = MagicMock()
    for name in ("resolve_dlq_entry", "increment_dlq_retry", "mark_dlq_permanent_failure"):
        setattr(tracker, name, AsyncMock())
    return tracker


def _store(config: SourceConfig) -> MagicMock:
    store = MagicMock()
    store.get = AsyncMock(return_value=config)
    return store


async def _failed_webhook_doc(error: Exception) -> RawDocument:
    s3 = MagicMock()
    s3.get_object.side_effect = error
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        docs = [d async for d in S3Connector().on_webhook(_config(), _event("late.txt"), {})]
    assert len(docs) == 1
    return docs[0]


async def test_a_failed_webhook_fetch_carries_a_replay_reference() -> None:
    doc = await _failed_webhook_doc(_throttled())
    assert doc.metadata[CONNECTOR_REPLAY_KEY] == {
        "kind": "s3_object",
        "bucket": "b",
        "key": "late.txt",
    }


async def test_dlq_retry_refetches_the_object_and_resolves() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = await _failed_webhook_doc(_throttled())
    pipeline, tracker = _Pipeline(), _tracker()
    s3 = MagicMock()
    s3.get_object.return_value = _ok(b"fresh content")
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        outcome = await _retry_one_dlq_entry(
            dlq_entry_for(doc), tracker, pipeline, _store(_config())
        )
    assert outcome == "succeeded"
    assert [d.content for d in pipeline.docs] == [b"fresh content"]
    s3.get_object.assert_called_once_with(Bucket="b", Key="late.txt")
    tracker.resolve_dlq_entry.assert_awaited_once_with("d1", "t-s3")


async def test_a_replay_that_fails_transiently_again_stays_in_the_dlq() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = await _failed_webhook_doc(_throttled())
    pipeline, tracker = _Pipeline(), _tracker()
    s3 = MagicMock()
    s3.get_object.side_effect = _throttled()
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        outcome = await _retry_one_dlq_entry(
            dlq_entry_for(doc), tracker, pipeline, _store(_config())
        )
    assert outcome == "still_failed"
    assert pipeline.docs == []  # the failure stand-in is never indexed
    assert "SlowDown" in tracker.increment_dlq_retry.await_args.kwargs["error"]
    tracker.resolve_dlq_entry.assert_not_called()


async def test_a_replay_that_now_fails_permanently_is_marked_permanent() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = await _failed_webhook_doc(_throttled())
    pipeline, tracker = _Pipeline(), _tracker()
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject"
    )
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        outcome = await _retry_one_dlq_entry(
            dlq_entry_for(doc), tracker, pipeline, _store(_config())
        )
    assert outcome == "permanent"
    tracker.mark_dlq_permanent_failure.assert_awaited_once_with("d1", "t-s3")


async def test_an_entry_without_a_replay_reference_is_replayed_as_before() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = RawDocument(
        doc_id="x",
        source_id="src-s3",
        tenant_id="t-s3",
        content=b"body",
        content_type="text/plain",
    )
    pipeline, tracker = _Pipeline(), _tracker()
    outcome = await _retry_one_dlq_entry(dlq_entry_for(doc), tracker, pipeline, _store(_config()))
    assert outcome == "succeeded"
    assert [d.content for d in pipeline.docs] == [b"body"]


async def test_a_permanent_failure_reference_is_not_replayed() -> None:
    from app.ingestion.scheduler import _retry_one_dlq_entry

    doc = await _failed_webhook_doc(
        ClientError(
            {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
            "GetObject",
        )
    )
    assert doc.metadata[CONNECTOR_FAILURE_RETRYABLE_KEY] is False
    pipeline, tracker = _Pipeline(), _tracker()
    with patch("boto3.Session") as client:
        outcome = await _retry_one_dlq_entry(
            dlq_entry_for(doc), tracker, pipeline, _store(_config())
        )
    assert outcome == "permanent"
    client.assert_not_called()
