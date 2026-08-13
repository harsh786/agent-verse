from __future__ import annotations

from dataclasses import dataclass

from app.coordination.outbox import OutboxDelivery, OutboxRecord, retry_delay_seconds


@dataclass
class Repository:
    records: list[OutboxRecord]

    def __post_init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.retried: list[tuple[str, int, float, str]] = []
        self.dead: list[tuple[str, str]] = []

    async def claim(self, *, owner: str, limit: int) -> list[OutboxRecord]:
        return self.records[:limit]

    async def mark_published(self, record_id: str, message_id: str) -> None:
        self.published.append((record_id, message_id))

    async def mark_retry(
        self, record_id: str, attempt: int, delay: float, error: str
    ) -> None:
        self.retried.append((record_id, attempt, delay, error))

    async def dead_letter(self, record_id: str, error: str) -> None:
        self.dead.append((record_id, error))


class Streams:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.envelopes: list[dict[str, object]] = []

    async def publish(
        self, tenant_id: str, session_id: str, envelope: dict[str, object]
    ) -> str:
        del tenant_id, session_id
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.envelopes.append(envelope)
        return "1-0"


def record(*, attempt: int = 0) -> OutboxRecord:
    return OutboxRecord(
        record_id="o1",
        event_id="e1",
        tenant_id="tenant",
        session_id="session",
        envelope={"event_id": "e1", "sequence": 1},
        attempt_count=attempt,
    )


def test_retry_backoff_is_bounded_and_deterministic() -> None:
    first = retry_delay_seconds("e1", 3, base_seconds=2, max_seconds=60)
    second = retry_delay_seconds("e1", 3, base_seconds=2, max_seconds=60)

    assert first == second
    assert 8 <= first <= 16


async def test_delivery_confirms_only_after_stream_accepts_envelope() -> None:
    repository = Repository([record()])
    streams = Streams()

    delivered = await OutboxDelivery(repository, streams).deliver(owner="worker", limit=10)

    assert delivered == 1
    assert streams.envelopes == [{"event_id": "e1", "sequence": 1}]
    assert repository.published == [("o1", "1-0")]


async def test_delivery_retries_redis_failure_then_dead_letters_at_limit() -> None:
    retry_repository = Repository([record(attempt=1)])
    await OutboxDelivery(retry_repository, Streams(fail=True), max_attempts=3).deliver(
        owner="worker", limit=10
    )
    assert retry_repository.published == []
    assert retry_repository.retried[0][1] == 2

    dead_repository = Repository([record(attempt=2)])
    await OutboxDelivery(dead_repository, Streams(fail=True), max_attempts=3).deliver(
        owner="worker", limit=10
    )
    assert dead_repository.dead == [("o1", "ConnectionError: redis unavailable")]
