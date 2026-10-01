"""KafkaConnector — Apache Kafka topic ingestion.

Streaming connector: subscribes to topics and yields messages as RawDocuments.
Cursor: committed consumer group offset (per partition).
Supports: any Kafka-compatible broker (MSK, Confluent Cloud, Redpanda).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    assert_source_host,
    require_pinnable_driver,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _split_host(entry: str) -> str:
    """Return the host of a ``[PROTO://]host[:port]`` bootstrap entry."""
    entry = entry.strip()
    if "://" in entry:
        entry = entry.split("://", 1)[1]
    if entry.startswith("["):  # [v6]:port
        return entry[1 : entry.find("]")] if "]" in entry else entry
    return entry.rsplit(":", 1)[0] if entry.count(":") == 1 else entry


def _require_bootstrap(cc: dict[str, Any]) -> str:
    """Return the tenant-supplied bootstrap list after SSRF-vetting every broker.

    There is deliberately no ``localhost`` default: an unset value used to make
    the API/worker host dial its own loopback on the tenant's behalf.
    """
    bootstrap = str(cc.get("bootstrap_servers") or "").strip()
    if not bootstrap:
        raise ConnectorEgressBlockedError("kafka: bootstrap_servers is required")
    for entry in bootstrap.split(","):
        if entry.strip():
            assert_source_host(_split_host(entry), context="kafka_connector")
    return bootstrap


def _check_advertised_brokers(metadata: Any) -> None:
    """Brokers advertise their own listener addresses; a hostile broker could
    point the client at internal hosts after the bootstrap check. Vet them too."""
    brokers = getattr(metadata, "brokers", None) or {}
    for broker in brokers.values():
        host = getattr(broker, "host", None)
        if isinstance(host, str) and host:
            assert_source_host(host, context="kafka_connector.advertised_broker")


@register("kafka", feature_flag="ingestion_connector_kafka_enabled")
class KafkaConnector(BaseConnector):
    """Kafka connector — consumes messages from one or more topics."""

    source_type = "kafka"
    supports_streaming = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            # librdkafka resolves bootstrap and advertised brokers itself, so its
            # connections cannot be pinned to the checked addresses.
            require_pinnable_driver("confluent-kafka (librdkafka)", context="kafka_connector")
            from confluent_kafka.admin import AdminClient  # type: ignore[import-not-found]

            cc = config.connection_config
            bootstrap = await asyncio.to_thread(_require_bootstrap, cc)
            def _metadata() -> Any:
                metadata = AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=10)
                _check_advertised_brokers(metadata)
                return metadata

            # librdkafka calls block (up to the 10s timeout): keep them off the loop.
            metadata = await run_blocking(_metadata)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"brokers": len(metadata.brokers), "topics": len(metadata.topics)},
            )
        except ImportError:
            return ConnectionHealth(
                ok=False, error="confluent-kafka not installed — pip install confluent-kafka"
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        require_pinnable_driver("confluent-kafka (librdkafka)", context="kafka_connector")
        try:
            from confluent_kafka import Consumer, KafkaError
            from confluent_kafka.admin import AdminClient
        except ImportError:
            _log.error("confluent-kafka not installed")
            return

        cc = config.connection_config
        topics = cc.get("topics") or []
        batch_size = int(cc.get("batch_size", 500))
        timeout_seconds = float(cc.get("poll_timeout_seconds", 5.0))
        group_id = cc.get("group_id") or f"agentverse-ingestor-{config.source_id[:8]}"
        bootstrap = await asyncio.to_thread(_require_bootstrap, cc)

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
            # Vet the brokers the cluster advertises before consuming from them.
            admin_conf = {
                k: v
                for k, v in conf.items()
                if not k.startswith(("group.", "auto.", "enable."))
            }
            _check_advertised_brokers(AdminClient(admin_conf).list_topics(timeout=10))
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
                    msgs.append(
                        {
                            "topic": msg.topic(),
                            "partition": msg.partition(),
                            "offset": msg.offset(),
                            "key": msg.key().decode("utf-8", errors="replace") if msg.key() else "",
                            "value": msg.value(),
                            "timestamp": msg.timestamp()[1] if msg.timestamp() else 0,
                        }
                    )
                consumer.commit()
            finally:
                consumer.close()
            return msgs

        messages = await run_blocking(_consume_batch)

        new_cursor = cursor or ""
        for m in messages:
            raw_value = m["value"]
            try:
                text = json.dumps(json.loads(raw_value), ensure_ascii=False)
            except Exception:
                text = (
                    raw_value.decode("utf-8", errors="replace")
                    if isinstance(raw_value, bytes)
                    else str(raw_value)
                )

            offset_cursor = f"{m['topic']}:{m['partition']}:{m['offset']}"
            new_cursor = offset_cursor
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"kafka://{bootstrap}/{m['topic']}/{m['partition']}/{m['offset']}",
                content=text.encode(),
                content_type="application/json" if text.startswith("{") else "text/plain",
                metadata={
                    "topic": m["topic"],
                    "partition": m["partition"],
                    "offset": m["offset"],
                    "key": m["key"],
                },
            )
            yield doc, new_cursor
