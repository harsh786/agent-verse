from __future__ import annotations

import asyncio

import pytest

from app.coordination.contracts import Classification
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService


@pytest.mark.asyncio
async def test_concurrent_append_has_contiguous_order_and_idempotency() -> None:
    service = TranscriptService(InMemoryTranscriptRepository())

    async def append(index: int):
        return await service.append(
            tenant_id="tenant", session_id="session", sender_agent_id="agent",
            message_type="message", content=f"message {index}",
            classification=Classification.INTERNAL, idempotency_key=f"key-{index}"
        )

    await asyncio.gather(*(append(index) for index in range(100)))
    duplicate = await append(5)
    messages = await service.page("tenant", "session", after_sequence=0, limit=200)
    assert [item.sequence for item in messages] == list(range(1, 101))
    assert duplicate.message_id == next(
        item.message_id for item in messages if item.idempotency_key == "key-5"
    )
    assert await service.page("other", "session", after_sequence=0, limit=200) == ()
