from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.coordination.contracts import Classification
from app.coordination.transcript.models import TranscriptMessage
from app.coordination.transcript.projections import TranscriptProjector


def _message(sequence: int, message_type: str = "message") -> TranscriptMessage:
    return TranscriptMessage(
        message_id=f"message-{sequence}",
        tenant_id="tenant",
        session_id="session",
        sequence=sequence,
        sender_agent_id="agent",
        message_type=message_type,
        safe_content="safe",
        classification=Classification.INTERNAL,
        trust_label="trusted",
        provenance_chain=(),
        source_digest="a" * 64,
        clearance_decision="allowed",
        idempotency_key=f"key-{sequence}",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_transcript_is_authority_for_ordered_idempotent_projections() -> None:
    board: list[int] = []
    bus: list[int] = []
    projector = TranscriptProjector(
        blackboard_writer=lambda **item: board.append(item["payload"]["sequence"]),
        bus_publisher=lambda **item: bus.append(item["payload"]["sequence"]),
    )
    assert await projector.project(_message(1, "decision")) is True
    assert await projector.project(_message(1, "decision")) is False
    assert await projector.project(_message(2)) is True
    assert board == [1]
    assert bus == [1, 2]
    with pytest.raises(ValueError, match="sequence gap"):
        await projector.project(_message(4))
