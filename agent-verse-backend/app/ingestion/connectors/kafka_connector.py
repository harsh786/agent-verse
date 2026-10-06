"""KafkaConnector — Apache Kafka topic ingestion.

Streaming connector: subscribes to topics and yields messages as RawDocuments.
Cursor: committed consumer group offset (per partition).
Supports: any Kafka-compatible broker (MSK, Confluent Cloud, Redpanda).

Delivery is AT-LEAST-ONCE (DEF-4). The consumer group offset of a message is
committed only after the sync loop has acknowledged it — i.e. it was indexed,
skipped as unchanged, or written to the DLQ (:meth:`KafkaConnector.acknowledge`,
called by the scheduler). It used to be committed for the whole batch BEFORE a
single message was handed over, so a crash (or an indexing failure) mid-batch
lost every remaining message for good. A message that was indexed but whose
offset was not committed yet is redelivered on the next run; its document id is
derived from (source, topic, partition, offset), so the redelivery overwrites
the same document instead of creating a duplicate. A caller that does not
acknowledge (preview / dry run) never moves the group offset.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    stable_doc_id,
)
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

    def __init__(self) -> None:
        # (topic, partition) -> next offset to commit, for messages of the
        # current get_delta run the caller has acknowledged.
        self._acked: dict[tuple[str, int], int] = {}
        # Partitions where a handed-over message was NOT acknowledged: Kafka
        # commits a position, so nothing past that message may be committed.
        self._blocked: set[tuple[str, int]] = set()
        self._run_source_id: str | None = None

    async def acknowledge(self, raw_doc: RawDocument) -> None:
        """The sync loop durably handled ``raw_doc`` (indexed, skipped or DLQ'd):
        its offset may now be committed. Out-of-run or foreign docs are ignored."""
        meta = raw_doc.metadata or {}
        if raw_doc.source_id != self._run_source_id:
            return
        try:
            key = (str(meta["topic"]), int(meta["partition"]))
            nxt = int(meta["offset"]) + 1
        except (KeyError, TypeError, ValueError):
            return
        if key not in self._blocked and nxt > self._acked.get(key, -1):
            self._acked[key] = nxt

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
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "confluent-kafka is not installed on this server; the connector cannot run"
            ) from exc

        cc = config.connection_config
        topics = cc.get("topics") or []
        batch_size = int(cc.get("batch_size", 500))
        timeout_seconds = float(cc.get("poll_timeout_seconds", 5.0))
        group_id = cc.get("group_id") or f"agentverse-ingestor-{config.source_id[:8]}"
        bootstrap = await asyncio.to_thread(_require_bootstrap, cc)
        # The committed group offset is the resume position. A partition with no
        # committed offset starts at the beginning: "latest" (the old choice
        # whenever a cursor existed) silently skipped everything produced before
        # the consumer first joined — including messages whose indexing never
        # got committed.
        reset = str(cc.get("auto_offset_reset") or "earliest").lower()
        if reset not in ("earliest", "latest"):
            reset = "earliest"

        conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": reset,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            # The batch stays assigned while the loop indexes it (embedding can
            # be slow); past this the group rebalances and the uncommitted
            # messages are redelivered (at-least-once, idempotent ids).
            "max.poll.interval.ms": int(cc.get("max_poll_interval_ms", 900_000)),
        }
        if cc.get("security_protocol"):
            conf["security.protocol"] = cc["security_protocol"]
        if cc.get("sasl_mechanism"):
            conf["sasl.mechanism"] = cc["sasl_mechanism"]
            conf["sasl.username"] = cc.get("sasl_username", "")
            conf["sasl.password"] = cc.get("sasl_password", "")

        def _open_and_poll() -> tuple[Any, list[dict[str, Any]]]:
            # Vet the brokers the cluster advertises before consuming from them.
            admin_conf = {
                k: v
                for k, v in conf.items()
                if not k.startswith(("group.", "auto.", "enable.", "max.poll"))
            }
            _check_advertised_brokers(AdminClient(admin_conf).list_topics(timeout=10))
            consumer = Consumer(conf)
            try:
                consumer.subscribe(topics)
                msgs: list[dict[str, Any]] = []
                for _ in range(batch_size):
                    msg = consumer.poll(timeout=timeout_seconds)
                    if msg is None:
                        break
                    if msg.error():
                        if msg.error().code() != KafkaError._PARTITION_EOF:
                            # USR-1: a broker / auth / topic error fails the sync
                            # (nothing is committed) — it used to end the batch
                            # as an empty success.
                            _log.warning("kafka error: %s", msg.error())
                            raise ConnectorFetchError(f"kafka: consume failed: {msg.error()}")
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
            except BaseException:
                consumer.close()
                raise
            return consumer, msgs

        def _commit_and_close(consumer: Any, acked: dict[tuple[str, int], int]) -> None:
            try:
                if acked:
                    from confluent_kafka import TopicPartition

                    consumer.commit(
                        offsets=[TopicPartition(t, p, o) for (t, p), o in sorted(acked.items())],
                        asynchronous=False,
                    )
            except Exception as exc:
                # Not a data loss: the uncommitted messages are redelivered next
                # run and overwrite the same document ids.
                _log.warning("kafka offset commit failed (messages will be redelivered): %s", exc)
            finally:
                consumer.close()

        self._acked = {}
        self._blocked = set()
        self._run_source_id = config.source_id
        consumer, messages = await run_blocking(_open_and_poll)
        try:
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
                    # Deterministic per message: a redelivery (uncommitted
                    # offset) re-indexes the SAME document, never a duplicate.
                    doc_id=stable_doc_id(config, m["topic"], m["partition"], m["offset"]),
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
                # Resumed: the caller moved on. If it did not acknowledge this
                # message (e.g. its DLQ write failed), the partition's commit
                # position stops before it — a later ack must not skip it.
                key = (str(m["topic"]), int(m["partition"]))
                if self._acked.get(key, -1) < int(m["offset"]) + 1:
                    self._blocked.add(key)
        finally:
            # Commit exactly what the caller acknowledged — on a normal end, a
            # cancel (generator closed) or an error alike. A message handed over
            # but not acknowledged keeps its offset uncommitted.
            acked, self._acked, self._blocked = dict(self._acked), {}, set()
            await run_blocking(_commit_and_close, consumer, acked)
