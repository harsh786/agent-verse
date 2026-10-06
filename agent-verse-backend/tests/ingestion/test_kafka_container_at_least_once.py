"""DEF-4 on a real Kafka broker: a crash between indexing and the offset commit
loses nothing and duplicates nothing.

The "index" is a dict keyed by document id (what the pipeline's upsert does);
the sync loop acknowledges each document after indexing it, exactly as
``app.ingestion.scheduler`` does. Two crashes are simulated:

* soft: indexing of a message completes but the loop dies before acknowledging
  it (the generator is closed — the acknowledged prefix is committed);
* hard: the process dies after indexing everything but before any commit.

Every message must end up indexed exactly once by document id, and a final run
must find nothing left to consume.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_kafka_container_at_least_once.py -m integration
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.connectors import kafka_connector
from app.ingestion.connectors.kafka_connector import KafkaConnector
from app.ingestion.source_config import SourceConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("allow_unpinnable_drivers"),
]

_N = 20


@pytest.fixture(scope="module")
def kafka_bootstrap() -> Iterator[str]:
    try:
        from testcontainers.kafka import KafkaContainer

        container = KafkaContainer("confluentinc/cp-kafka:7.6.0").with_kraft()
        container.start(timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not start a Kafka testcontainer (Docker down?): {exc}")
    try:
        yield container.get_bootstrap_server()
    finally:
        container.stop()


@pytest.fixture
def topic(kafka_bootstrap: str) -> str:
    from confluent_kafka import Producer
    from confluent_kafka.admin import AdminClient, NewTopic

    name = f"orders-{secrets.token_hex(4)}"
    admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
    for fut in admin.create_topics([NewTopic(name, num_partitions=2, replication_factor=1)]).values():
        fut.result(timeout=30)
    producer = Producer({"bootstrap.servers": kafka_bootstrap})
    for n in range(_N):
        producer.produce(name, key=str(n % 2).encode(), value=json.dumps({"n": n}).encode())
    assert producer.flush(30) == 0
    return name


@pytest.fixture(autouse=True)
def _local_broker_allowed(monkeypatch: pytest.MonkeyPatch, kafka_bootstrap: str) -> None:
    # The SSRF guard (rightly) refuses a loopback broker; this test's broker is
    # the local container.
    monkeypatch.setattr(kafka_connector, "_require_bootstrap", lambda cc: kafka_bootstrap)
    monkeypatch.setattr(kafka_connector, "_check_advertised_brokers", lambda metadata: None)


def _config(topic: str, group: str) -> SourceConfig:
    return SourceConfig(
        source_id="src-kafka-it", tenant_id="t-kafka", name="k", family="database",
        source_type="kafka",
        connection_config={
            "topics": [topic], "group_id": group, "batch_size": 500,
            "poll_timeout_seconds": 15.0,
        },
    )


class _Index:
    """doc_id -> payload; an upsert, like the ingestion pipeline."""

    def __init__(self) -> None:
        self.docs: dict[str, int] = {}
        self.writes = 0

    def index(self, doc: Any) -> None:
        self.docs[doc.doc_id] = json.loads(doc.content)["n"]
        self.writes += 1


async def _sync(config: SourceConfig, index: _Index, *, crash_after: int | None = None) -> int:
    """One sync run; ``crash_after`` = die after indexing that many docs, before
    acknowledging the last one. Returns the number of docs handed over."""
    conn = KafkaConnector()
    gen = conn.get_delta(config, None)
    seen = 0
    try:
        async for doc, _cursor in gen:
            index.index(doc)
            seen += 1
            if crash_after is not None and seen == crash_after:
                raise RuntimeError("worker crashed between index and commit")
            await conn.acknowledge(doc)
    except RuntimeError:
        pass
    finally:
        await gen.aclose()
    return seen


async def test_soft_crash_between_index_and_commit_redelivers_without_duplicates(
    topic: str,
) -> None:
    config = _config(topic, f"g-{secrets.token_hex(4)}")
    index = _Index()

    assert await _sync(config, index, crash_after=7) == 7
    redelivered = await _sync(config, index)  # the crashed message comes back
    assert redelivered == _N - 6
    assert await _sync(config, index) == 0  # everything committed now

    assert sorted(index.docs.values()) == list(range(_N))  # nothing lost
    assert len(index.docs) == _N  # nothing duplicated (same doc ids)
    assert index.writes == _N + 1  # exactly the one in-flight message re-indexed


async def test_hard_crash_before_any_commit_redelivers_everything_idempotently(
    topic: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(topic, f"g-{secrets.token_hex(4)}")
    index = _Index()

    import confluent_kafka

    def _process_died(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("process killed before the offset commit")

    with monkeypatch.context() as m:
        # The commit never reaches the broker (as if the worker was killed).
        m.setattr(confluent_kafka, "TopicPartition", _process_died)
        assert await _sync(config, index) == _N
    first_ids = dict(index.docs)

    assert await _sync(config, index) == _N  # all redelivered
    assert await _sync(config, index) == 0
    assert index.docs == first_ids  # same ids, same payloads: no duplicates
    assert sorted(index.docs.values()) == list(range(_N))
