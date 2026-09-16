"""Tests for S3Connector — validate_connection, get_delta pagination/filtering,
on_webhook, estimate_doc_count, and the pattern-matching helper. boto3 is
installed in this environment, so we patch boto3.Session/boto3.client rather
than injecting a fake module; the ImportError branch is exercised by setting
sys.modules["boto3"] = None (mirrors the pypdf pattern used elsewhere)."""
from __future__ import annotations

import datetime
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.s3_connector import S3Connector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None, **overrides) -> SourceConfig:
    return SourceConfig(
        source_id="src-s3",
        tenant_id="t1",
        name="Test S3",
        family="object_storage",
        source_type="s3",
        enabled=True,
        connection_config=conn_config or {"bucket": "my-bucket", "region": "us-east-1"},
        **overrides,
    )


@pytest.fixture(autouse=True)
def _restore_boto3():
    saved = sys.modules.get("boto3")
    yield
    if saved is not None:
        sys.modules["boto3"] = saved
    else:
        sys.modules.pop("boto3", None)


class TestValidateConnection:
    async def test_no_library_installed(self):
        sys.modules["boto3"] = None
        connector = S3Connector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "boto3" in health.error

    async def test_success(self):
        mock_s3 = MagicMock()
        mock_s3.head_bucket.return_value = {}
        mock_s3.list_objects_v2.return_value = {"KeyCount": 5}
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["bucket"] == "my-bucket"
        assert health.metadata["accessible_objects"] == 5

    async def test_failure(self):
        mock_session = MagicMock()
        mock_session.client.side_effect = RuntimeError("access denied")
        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "access denied" in health.error


def _obj(key, last_modified, size=100):
    return {"Key": key, "LastModified": last_modified, "Size": size}


class TestGetDelta:
    async def test_no_library_yields_nothing(self):
        sys.modules["boto3"] = None
        connector = S3Connector()
        docs = [d async for d in connector.get_delta(_make_config(), None)]
        assert docs == []

    async def test_yields_objects_and_advances_cursor(self):
        t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        t2 = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
        page = {"Contents": [_obj("a.txt", t1), _obj("b.txt", t2)]}

        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"content")),
            "ContentType": "text/plain",
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            config = _make_config()
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        doc0, cursor0 = results[0]
        assert doc0.content == b"content"
        assert doc0.title == "a.txt"
        doc1, cursor1 = results[1]
        assert cursor1 == t2.isoformat()

    async def test_skips_objects_older_than_cursor(self):
        t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        page = {"Contents": [_obj("old.txt", t1)]}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            results = [d async for d in connector.get_delta(_make_config(), t1.isoformat())]
        assert results == []
        mock_s3.get_object.assert_not_called()

    async def test_exclude_pattern_filters_object(self):
        t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        page = {"Contents": [_obj("skip.log", t1), _obj("keep.txt", t1)]}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"x")),
            "ContentType": "text/plain",
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            config = _make_config()
            config.exclude_patterns = ["*.log"]
            results = [d async for d in connector.get_delta(config, None)]
        assert len(results) == 1
        assert results[0][0].title == "keep.txt"

    async def test_skips_oversized_objects(self):
        t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        page = {"Contents": [_obj("huge.bin", t1, size=999_999_999)]}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            config = _make_config()
            config.max_doc_size_bytes = 10
            results = [d async for d in connector.get_delta(config, None)]
        assert results == []

    async def test_download_error_is_skipped_not_raised(self):
        t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        page = {"Contents": [_obj("broken.txt", t1)]}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.get_object.side_effect = RuntimeError("network blip")
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            results = [d async for d in connector.get_delta(_make_config(), None)]
        assert results == []

    async def test_unexpected_error_reraises(self):
        """The error must surface while iterating pages (inside the try block),
        not merely from calling paginate() itself, to exercise the log+re-raise."""

        class _BoomPages:
            def __iter__(self):
                return self

            def __next__(self):
                raise RuntimeError("boom")

        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = _BoomPages()
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch("boto3.Session", return_value=mock_session):
            connector = S3Connector()
            with pytest.raises(RuntimeError, match="boom"):
                [d async for d in connector.get_delta(_make_config(), None)]


class TestOnWebhook:
    async def test_object_created_event_yields_document(self):
        payload = (
            b'{"Records": [{"eventName": "ObjectCreated:Put", '
            b'"s3": {"bucket": {"name": "my-bucket"}, "object": {"key": "new.txt"}}}]}'
        )
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"webhook body")),
            "ContentType": "text/plain",
        }
        with patch("boto3.client", return_value=mock_s3):
            connector = S3Connector()
            docs = [d async for d in connector.on_webhook(_make_config(), payload, {})]
        assert len(docs) == 1
        assert docs[0].content == b"webhook body"

    async def test_invalid_json_yields_nothing(self):
        connector = S3Connector()
        docs = [d async for d in connector.on_webhook(_make_config(), b"not json", {})]
        assert docs == []

    async def test_non_creation_event_ignored(self):
        payload = b'{"Records": [{"eventName": "ObjectRemoved:Delete", "s3": {}}]}'
        connector = S3Connector()
        docs = [d async for d in connector.on_webhook(_make_config(), payload, {})]
        assert docs == []

    async def test_fetch_single_error_is_swallowed(self):
        payload = (
            b'{"Records": [{"eventName": "ObjectCreated:Put", '
            b'"s3": {"bucket": {"name": "my-bucket"}, "object": {"key": "bad.txt"}}}]}'
        )
        with patch("boto3.client", side_effect=RuntimeError("no creds")):
            connector = S3Connector()
            docs = [d async for d in connector.on_webhook(_make_config(), payload, {})]
        assert docs == []


class TestEstimateDocCount:
    def test_success(self):
        mock_s3 = MagicMock()
        mock_s3.list_objects_v2.return_value = {"KeyCount": 42}
        with patch("boto3.client", return_value=mock_s3):
            connector = S3Connector()
            assert connector.estimate_doc_count(_make_config()) == 42

    def test_exception_returns_none(self):
        with patch("boto3.client", side_effect=RuntimeError("fail")):
            connector = S3Connector()
            assert connector.estimate_doc_count(_make_config()) is None


class TestMatchesPatterns:
    def test_no_patterns_matches_everything(self):
        assert S3Connector._matches_patterns("any/key.txt", [], []) is True

    def test_include_pattern_required(self):
        assert S3Connector._matches_patterns("a.txt", ["*.txt"], []) is True
        assert S3Connector._matches_patterns("a.bin", ["*.txt"], []) is False

    def test_exclude_pattern_takes_precedence(self):
        assert S3Connector._matches_patterns("a.txt", ["*.txt"], ["*.txt"]) is False


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert S3Connector().source_type == "s3"
    assert get_connector("s3") is S3Connector
    assert S3Connector.supports_streaming is True
    assert S3Connector.supports_deletion_tracking is True
