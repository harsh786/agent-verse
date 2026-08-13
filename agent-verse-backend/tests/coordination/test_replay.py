from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.coordination.replay import InvalidCursorError, SequenceReplay


@dataclass
class Event:
    tenant_id: str
    session_id: str
    sequence: int
    event_id: str
    schema_version: int = 1
    event_type: str = "test.event"
    payload: dict[str, object] | None = None


class Repository:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.queries = 0

    async def page(
        self,
        *,
        tenant_id: str,
        session_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[Event]:
        self.queries += 1
        return [
            event
            for event in self.events
            if event.tenant_id == tenant_id
            and event.session_id == session_id
            and event.sequence > after_sequence
        ][:limit]


async def test_replay_is_exclusive_and_sequence_ordered() -> None:
    repository = Repository(
        [
            Event("tenant", "session", 3, "e3"),
            Event("tenant", "session", 1, "e1"),
            Event("tenant", "session", 2, "e2"),
        ]
    )
    replay = SequenceReplay(repository)

    events = await replay.page(
        tenant_id="tenant", session_id="session", after_sequence=1, limit=10
    )

    assert [event.sequence for event in events] == [2, 3]


async def test_replay_rejects_invalid_cursor_and_bounds_page_size() -> None:
    replay = SequenceReplay(Repository([]), max_page_size=100)
    with pytest.raises(InvalidCursorError):
        await replay.page(
            tenant_id="tenant", session_id="session", after_sequence=-1, limit=10
        )
    with pytest.raises(InvalidCursorError):
        await replay.page(
            tenant_id="tenant", session_id="session", after_sequence=0, limit=101
        )


async def test_replay_deduplicates_event_ids_and_upcasts_supported_schema() -> None:
    repository = Repository(
        [
            Event("tenant", "session", 1, "same", schema_version=0, payload={"v": 0}),
            Event("tenant", "session", 2, "same", payload={"v": 1}),
        ]
    )
    replay = SequenceReplay(
        repository,
        upcasters={0: lambda payload: {**payload, "upcasted": True}},
    )

    events = await replay.page(
        tenant_id="tenant", session_id="session", after_sequence=0, limit=10
    )

    assert len(events) == 1
    assert events[0].schema_version == 1
    assert events[0].payload == {"v": 0, "upcasted": True}


async def test_ten_thousand_event_replay_uses_bounded_pages() -> None:
    repository = Repository(
        [Event("tenant", "session", index, f"e{index}") for index in range(1, 10_001)]
    )
    replay = SequenceReplay(repository, max_page_size=500)

    events = await replay.all(
        tenant_id="tenant", session_id="session", page_size=500
    )

    assert len(events) == 10_000
    assert repository.queries == 21
