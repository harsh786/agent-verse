"""Tests for KinesisConnector — shard-iterator streaming ingestion.

boto3 is a real installed dependency; we patch ``boto3.Session`` so no AWS
calls are made. Shard reads run through ``loop.run_in_executor``, which
works transparently with plain (synchronous) MagicMock methods.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.ingestion.connectors.kinesis_connector import KinesisConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-kinesis",
        tenant_id="t1",
        name="Test Kinesis",
        family="streaming",
        source_type="kinesis",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


def _fake_session(kinesis_client):
    session = MagicMock()
    session.client = MagicMock(return_value=kinesis_client)
    return MagicMock(return_value=session)


class TestValidateConnection:
    async def test_success(self):
        kinesis_client = MagicMock()
        kinesis_client.describe_stream_summary = MagicMock(
            return_value={"StreamDescriptionSummary": {"OpenShardCount": 4}}
        )
        config = _make_config({"stream_name": "orders-stream"})
        with patch("boto3.Session", _fake_session(kinesis_client)):
            result = await KinesisConnector().validate_connection(config)
        assert result.ok is True
        assert result.metadata == {"stream": "orders-stream", "open_shards": 4}

    async def test_import_error(self):
        with patch.dict("sys.modules", {"boto3": None}):
            result = await KinesisConnector().validate_connection(_make_config())
        assert result.ok is False
        assert "boto3" in result.error

    async def test_exception(self):
        kinesis_client = MagicMock()
        kinesis_client.describe_stream_summary = MagicMock(
            side_effect=Exception("stream not found")
        )
        with patch("boto3.Session", _fake_session(kinesis_client)):
            result = await KinesisConnector().validate_connection(_make_config())
        assert result.ok is False
        assert "stream not found" in result.error


class TestGetDelta:
    async def test_no_boto3_yields_nothing(self):
        with patch.dict("sys.modules", {"boto3": None}):
            docs = await _collect(KinesisConnector().get_delta(_make_config(), None))
        assert docs == []

    async def test_yields_records_from_all_shards(self):
        kinesis_client = MagicMock()
        kinesis_client.list_shards = MagicMock(
            return_value={"Shards": [{"ShardId": "shard-0"}, {"ShardId": "shard-1"}]}
        )
        kinesis_client.get_shard_iterator = MagicMock(return_value={"ShardIterator": "iter-1"})
        kinesis_client.get_records = MagicMock(
            return_value={
                "Records": [
                    {"SequenceNumber": "seq-1", "Data": json.dumps({"event": "click"}).encode()}
                ]
            }
        )
        config = _make_config({"stream_name": "events"})
        with patch("boto3.Session", _fake_session(kinesis_client)):
            docs = await _collect(KinesisConnector().get_delta(config, None))

        assert len(docs) == 2  # one record per shard
        doc0, cursor0 = docs[0]
        assert doc0.content_type == "application/json"
        assert json.loads(cursor0) == {"shard-0": "seq-1"}
        assert doc0.metadata["shard"] == "shard-0"

    async def test_non_json_payload_falls_back_to_text(self):
        kinesis_client = MagicMock()
        kinesis_client.list_shards = MagicMock(return_value={"Shards": [{"ShardId": "shard-0"}]})
        kinesis_client.get_shard_iterator = MagicMock(return_value={"ShardIterator": "iter-1"})
        kinesis_client.get_records = MagicMock(
            return_value={"Records": [{"SequenceNumber": "seq-1", "Data": b"plain text payload"}]}
        )
        config = _make_config({"stream_name": "events"})
        with patch("boto3.Session", _fake_session(kinesis_client)):
            docs = await _collect(KinesisConnector().get_delta(config, None))
        doc, _cursor = docs[0]
        assert doc.content_type == "text/plain"
        assert doc.content == b"plain text payload"

    async def test_resumes_from_cursor_map_with_after_sequence(self):
        kinesis_client = MagicMock()
        kinesis_client.list_shards = MagicMock(return_value={"Shards": [{"ShardId": "shard-0"}]})
        kinesis_client.get_shard_iterator = MagicMock(return_value={"ShardIterator": "iter-2"})
        kinesis_client.get_records = MagicMock(return_value={"Records": []})
        cursor = json.dumps({"shard-0": "seq-old"})
        config = _make_config({"stream_name": "events"})
        with patch("boto3.Session", _fake_session(kinesis_client)):
            docs = await _collect(KinesisConnector().get_delta(config, cursor))
        assert docs == []
        kinesis_client.get_shard_iterator.assert_called_once_with(
            StreamName="events",
            ShardId="shard-0",
            ShardIteratorType="AFTER_SEQUENCE_NUMBER",
            StartingSequenceNumber="seq-old",
        )

    async def test_no_cursor_uses_trim_horizon(self):
        kinesis_client = MagicMock()
        kinesis_client.list_shards = MagicMock(return_value={"Shards": [{"ShardId": "shard-0"}]})
        kinesis_client.get_shard_iterator = MagicMock(return_value={"ShardIterator": "iter-3"})
        kinesis_client.get_records = MagicMock(return_value={"Records": []})
        config = _make_config({"stream_name": "events"})
        with patch("boto3.Session", _fake_session(kinesis_client)):
            await _collect(KinesisConnector().get_delta(config, None))
        kinesis_client.get_shard_iterator.assert_called_once_with(
            StreamName="events", ShardId="shard-0", ShardIteratorType="TRIM_HORIZON"
        )
