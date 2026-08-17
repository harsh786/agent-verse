"""KinesisConnector — AWS Kinesis Data Streams ingestion.

Streaming: ShardIterator-based consumption with checkpoint cursor.
Cursor: JSON-encoded {shard_id: sequence_number} map for all shards.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("kinesis", feature_flag="ingestion_connector_kinesis_enabled")
class KinesisConnector(BaseConnector):
    """AWS Kinesis Data Streams connector."""

    source_type = "kinesis"
    supports_streaming = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import boto3  # type: ignore[import-not-found]
            cc = config.connection_config
            session = boto3.Session(
                aws_access_key_id=cc.get("access_key_id"),
                aws_secret_access_key=cc.get("secret_access_key"),
                region_name=cc.get("region", "us-east-1"),
            )
            kinesis = session.client("kinesis")
            stream = cc.get("stream_name", "")
            resp = kinesis.describe_stream_summary(StreamName=stream)
            shards = resp["StreamDescriptionSummary"]["OpenShardCount"]
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"stream": stream, "open_shards": shards},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="boto3 not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError:
            _log.error("boto3 not installed"); return

        import asyncio
        cc = config.connection_config
        stream = cc.get("stream_name", "")
        region = cc.get("region", "us-east-1")
        batch_size = int(cc.get("batch_size", 500))
        cursor_map: dict = json.loads(cursor) if cursor else {}

        session = boto3.Session(
            aws_access_key_id=cc.get("access_key_id"),
            aws_secret_access_key=cc.get("secret_access_key"),
            region_name=region,
        )
        kinesis = session.client("kinesis")

        def _fetch_shard(shard_id: str, seq: str | None) -> list[dict]:
            if seq:
                it_resp = kinesis.get_shard_iterator(
                    StreamName=stream, ShardId=shard_id,
                    ShardIteratorType="AFTER_SEQUENCE_NUMBER",
                    StartingSequenceNumber=seq,
                )
            else:
                it_resp = kinesis.get_shard_iterator(
                    StreamName=stream, ShardId=shard_id,
                    ShardIteratorType="TRIM_HORIZON",
                )
            iterator = it_resp["ShardIterator"]
            records_resp = kinesis.get_records(ShardIterator=iterator, Limit=batch_size)
            return records_resp.get("Records", [])

        # List all shards
        resp = kinesis.list_shards(StreamName=stream)
        shards = [s["ShardId"] for s in resp.get("Shards", [])]

        new_cursor_map = dict(cursor_map)
        loop = asyncio.get_event_loop()

        for shard_id in shards:
            seq = cursor_map.get(shard_id)
            records = await loop.run_in_executor(None, _fetch_shard, shard_id, seq)
            for rec in records:
                seq_num = rec["SequenceNumber"]
                new_cursor_map[shard_id] = seq_num
                data = rec.get("Data", b"")
                try:
                    text = json.dumps(json.loads(data))
                except Exception:
                    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)

                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id, tenant_id=config.tenant_id,
                    source_url=f"kinesis://{region}/{stream}/{shard_id}/{seq_num}",
                    content=text.encode(), content_type="application/json" if text.startswith("{") else "text/plain",
                    metadata={"stream": stream, "shard": shard_id, "sequence": seq_num},
                )
                yield doc, json.dumps(new_cursor_map)
