"""DEF-4: Kafka offsets are committed only for messages the sync loop durably
handled (indexed, skipped, or written to the DLQ) — at-least-once.

The old connector committed the whole batch before yielding a single message,
so a crash or an indexing failure mid-batch lost the rest. Fake
``confluent_kafka`` here; the real-broker crash test is
test_kafka_container_at_least_once.py.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors import kafka_connector
from app.ingestion.connectors.kafka_connector import KafkaConnector
from app.ingestion.source_config import SourceConfig

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("allow_unpinnable_drivers")]


class _TP:
    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self.topic, self.partition, self.offset = topic, partition, offset


def _msg(topic: str, partition: int, offset: int) -> Any:
    m = MagicMock()
    m.topic.return_value = topic
    m.partition.return_value = partition
    m.offset.return_value = offset
    m.key.return_value = None
    m.value.return_value = f'{{"n": {offset}}}'.encode()
    m.timestamp.return_value = (1, 0)
    m.error.return_value = None
    return m


@contextmanager
def _fake_kafka(messages: list[Any]) -> Any:
    pkg = ModuleType("confluent_kafka")
    admin = ModuleType("confluent_kafka.admin")

    class _KafkaError:
        _PARTITION_EOF = -191

    consumer = MagicMock()
    consumer.poll.side_effect = [*messages, None]
    pkg.KafkaError = _KafkaError  # type: ignore[attr-defined]
    pkg.TopicPartition = _TP  # type: ignore[attr-defined]
    pkg.Consumer = MagicMock(return_value=consumer)  # type: ignore[attr-defined]
    admin.AdminClient = MagicMock()  # type: ignore[attr-defined]
    admin.AdminClient.return_value.list_topics.return_value = SimpleNamespace(brokers={})
    with (
        patch.dict(sys.modules, {"confluent_kafka": pkg, "confluent_kafka.admin": admin}),
        patch.object(kafka_connector, "_require_bootstrap", lambda cc: "broker.test:9092"),
    ):
        yield pkg, consumer


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="src-kafka", tenant_id="t1", name="k", family="database",
        source_type="kafka", connection_config={"topics": ["orders"], "batch_size": 50},
    )


def _committed(consumer: Any) -> dict[tuple[str, int], int]:
    assert consumer.commit.call_count <= 1
    if not consumer.commit.call_count:
        return {}
    kwargs = consumer.commit.call_args.kwargs
    assert kwargs["asynchronous"] is False
    return {(tp.topic, tp.partition): tp.offset for tp in kwargs["offsets"]}


async def test_unacknowledged_messages_are_never_committed() -> None:
    """A preview / dry run iterates without acknowledging: offsets stay put."""
    with _fake_kafka([_msg("orders", 0, 10), _msg("orders", 0, 11)]) as (_, consumer):
        conn = KafkaConnector()
        docs = [d async for d, _ in conn.get_delta(_config(), None)]
    assert len(docs) == 2
    assert _committed(consumer) == {}
    consumer.close.assert_called_once()


async def test_acknowledged_messages_are_committed_after_handling() -> None:
    msgs = [_msg("orders", 0, 10), _msg("orders", 1, 4), _msg("orders", 0, 11)]
    with _fake_kafka(msgs) as (pkg, consumer):
        conn = KafkaConnector()
        async for doc, _ in conn.get_delta(_config(), "orders:0:9"):
            assert consumer.commit.call_count == 0  # never before handling
            await conn.acknowledge(doc)
    assert _committed(consumer) == {("orders", 0): 12, ("orders", 1): 5}
    # The group starts from the earliest offset when it has no commit, even with
    # a cursor ("latest" skipped never-committed messages).
    assert pkg.Consumer.call_args.args[0]["auto.offset.reset"] == "earliest"
    assert pkg.Consumer.call_args.args[0]["enable.auto.commit"] is False


async def test_crash_mid_batch_commits_only_the_handled_prefix() -> None:
    msgs = [_msg("orders", 0, 10), _msg("orders", 0, 11), _msg("orders", 0, 12)]
    with _fake_kafka(msgs) as (_, consumer):
        conn = KafkaConnector()
        gen = conn.get_delta(_config(), None)
        first, _ = await gen.__anext__()
        await conn.acknowledge(first)
        await gen.__anext__()  # handed over, indexing "crashes" before the ack
        await gen.aclose()
    assert _committed(consumer) == {("orders", 0): 11}


async def test_an_unacknowledged_message_blocks_later_commits_in_its_partition() -> None:
    """Kafka commits a position: once message 11 is not durably handled (its DLQ
    write failed), acknowledging 12 must not skip it. Other partitions move on."""
    msgs = [_msg("orders", 0, 10), _msg("orders", 0, 11), _msg("orders", 1, 7),
            _msg("orders", 0, 12)]
    with _fake_kafka(msgs) as (_, consumer):
        conn = KafkaConnector()
        async for doc, _ in conn.get_delta(_config(), None):
            if doc.metadata["offset"] != 11:
                await conn.acknowledge(doc)
    assert _committed(consumer) == {("orders", 0): 11, ("orders", 1): 8}


async def test_redelivered_message_keeps_its_document_id() -> None:
    with _fake_kafka([_msg("orders", 0, 10)]):
        a = [d async for d, _ in KafkaConnector().get_delta(_config(), None)]
    with _fake_kafka([_msg("orders", 0, 10)]):
        b = [d async for d, _ in KafkaConnector().get_delta(_config(), None)]
    assert a[0].doc_id == b[0].doc_id


async def test_commit_failure_is_logged_not_a_fake_loss() -> None:
    with _fake_kafka([_msg("orders", 0, 10)]) as (_, consumer):
        consumer.commit.side_effect = RuntimeError("rebalance in progress")
        conn = KafkaConnector()
        async for doc, _ in conn.get_delta(_config(), None):
            await conn.acknowledge(doc)
    consumer.close.assert_called_once()


# ── the sync loop acknowledges only durable outcomes ─────────────────────────


class _Pipeline:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def ingest(self, raw_doc: Any, cfg: Any) -> Any:
        status = self.statuses[raw_doc.doc_id]
        if status == "raise":
            raise RuntimeError("embedder down")
        return SimpleNamespace(status=status, error="bad doc", tokens_consumed=0,
                               chunks_created=0)


@pytest.mark.parametrize("dlq_ok", [True, False])
async def test_scheduler_acknowledges_indexed_skipped_and_durably_dlqd_docs(
    dlq_ok: bool,
) -> None:
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.scheduler import _sync_source_async
    from app.ingestion.source_config import RawDocument, SourceFamily

    config = SourceConfig(
        source_id="src-k", tenant_id="t-k", name="n", family=SourceFamily.WEB,
        source_type="http", connection_config={"url": "https://93.184.216.34/x"},
        collection_id="col",
    )
    docs = [
        RawDocument(doc_id=d, source_id="src-k", tenant_id="t-k", content=b"x",
                    content_type="text/plain")
        for d in ("indexed", "skipped", "failed", "raised")
    ]
    acked: list[str] = []

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> Any:
            for i, doc in enumerate(docs):
                yield doc, f"c{i}"

        async def acknowledge(self, raw_doc: Any) -> None:
            acked.append(raw_doc.doc_id)

    tracker = IngestionJobTracker()
    tracker.add_to_dlq = AsyncMock(return_value=dlq_ok)  # type: ignore[method-assign]
    store = AsyncMock()
    store.get = AsyncMock(return_value=config)
    pipeline = _Pipeline(
        {"indexed": "indexed", "skipped": "skipped", "failed": "failed", "raised": "raise"}
    )
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
    ):
        try:
            await _sync_source_async(
                task=None, source_id="src-k", tenant_id="t-k", triggered_by="manual"
            )
        except Exception:
            pass
    expected = ["indexed", "skipped"] + (["failed", "raised"] if dlq_ok else [])
    assert acked == expected
