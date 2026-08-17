"""KafkaConnector — Apache Kafka topic ingestion.

Streaming connector: subscribes to topics and yields messages as RawDocuments.
Cursor: committed consumer group offset (per partition).
Supports: any Kafka-compatible broker (MSK, Confluent Cloud, Redpanda).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("kafka", feature_flag="ingestion_connector_kafka_enabled")
class KafkaConnector(BaseConnector):
    """Kafka connector — consumes messages from one or more topics."""

    source_type = "kafka"
    supports_streaming = True

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            from confluent_kafka.admin import AdminClient  # type: ignore[import-not-found]
            cc = config.connection_config
            admin = AdminClient({"bootstrap.servers": cc.get("bootstrap_servers", "localhost:9092")})
            metadata = admin.list_topics(timeout=10)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"brokers": len(metadata.brokers), "topics": len(metadata.topics)},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="confluent-kafka not installed — pip install confluent-kafka")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        try:
            from confluent_kafka import Consumer, KafkaError  # type: ignore[import-not-found]
        except ImportError:
            _log.error("confluent-kafka not installed"); return

        import asyncio
        cc = config.connection_config
        topics = cc.get("topics") or []
        batch_size = int(cc.get("batch_size", 500))
        timeout_seconds = float(cc.get("poll_timeout_seconds", 5.0))
        group_id = cc.get("group_id") or f"agentverse-ingestor-{config.source_id[:8]}"
        bootstrap = cc.get("bootstrap_servers", "localhost:9092")

        conf = {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest" if not cursor else "latest",
            "enable.auto.commit": False,
        }
        if cc.get("security_protocol"):
            conf["security.protocol"] = cc["security_protocol"]
        if cc.get("sasl_mechanism"):
            conf["sasl.mechanism"] = cc["sasl_mechanism"]
            conf["sasl.username"] = cc.get("sasl_username", "")
            conf["sasl.password"] = cc.get("sasl_password", "")

        def _consume_batch():
            consumer = Consumer(conf)
            consumer.subscribe(topics)
            msgs = []
            try:
                for _ in range(batch_size):
                    msg = consumer.poll(timeout=timeout_seconds)
                    if msg is None:
                        break
                    if msg.error():
                        if msg.error().code() != KafkaError._PARTITION_EOF:
                            _log.warning("kafka error: %s", msg.error())
                        break
                    msgs.append({
                        "topic": msg.topic(),
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                        "key": msg.key().decode("utf-8", errors="replace") if msg.key() else "",
                        "value": msg.value(),
                        "timestamp": msg.timestamp()[1] if msg.timestamp() else 0,
                    })
                consumer.commit()
            finally:
                consumer.close()
            return msgs

        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, _consume_batch)

        new_cursor = cursor or ""
        for m in messages:
            raw_value = m["value"]
            try:
                text = json.dumps(json.loads(raw_value), ensure_ascii=False)
            except Exception:
                text = raw_value.decode("utf-8", errors="replace") if isinstance(raw_value, bytes) else str(raw_value)

            offset_cursor = f"{m['topic']}:{m['partition']}:{m['offset']}"
            new_cursor = offset_cursor
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"kafka://{bootstrap}/{m['topic']}/{m['partition']}/{m['offset']}",
                content=text.encode(),
                content_type="application/json" if text.startswith("{") else "text/plain",
                metadata={"topic": m["topic"], "partition": m["partition"], "offset": m["offset"], "key": m["key"]},
            )
            yield doc, new_cursor
