from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.coordination.contracts import Classification
from app.coordination.group_chat.compaction import TranscriptCompactor
from app.coordination.transcript.models import TranscriptMessage
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService


def _message(
    sequence: int,
    *,
    message_type: str = "message",
    content: str = "routine",
    trust: str = "trusted",
    classification: Classification = Classification.INTERNAL,
) -> TranscriptMessage:
    return TranscriptMessage(
        message_id=f"message-{sequence}",
        tenant_id="tenant",
        session_id="session",
        sequence=sequence,
        sender_agent_id="agent",
        message_type=message_type,
        safe_content=content,
        classification=classification,
        trust_label=trust,
        provenance_chain=(),
        source_digest=f"{sequence:064x}",
        clearance_decision="allowed",
        idempotency_key=f"source-{sequence}",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_compaction_preserves_decisions_evidence_taint_and_classification() -> None:
    service = TranscriptService(InMemoryTranscriptRepository())
    source = (
        _message(1),
        _message(2, message_type="decision", content="choose A"),
        _message(
            3,
            message_type="evidence",
            content="artifact://proof",
            classification=Classification.CONFIDENTIAL,
        ),
        _message(4, content="[REDACTED]", trust="quarantined"),
    )
    summary = await TranscriptCompactor(service).compact(
        tenant_id="tenant",
        session_id="session",
        sender_agent_id="compactor",
        messages=source,
        idempotency_key="compact-1",
    )
    assert summary.message_type == "summary"
    assert summary.compacts_from_sequence == 1 and summary.compacts_to_sequence == 4
    assert summary.classification is Classification.CONFIDENTIAL
    assert "choose A" in (summary.safe_content or "")
    assert "artifact://proof" in (summary.safe_content or "")
    assert "[REDACTED]" in (summary.safe_content or "")
    assert summary.provenance_chain == tuple(item.message_id for item in source)


@pytest.mark.asyncio
async def test_compaction_rejects_sequence_gaps() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        await TranscriptCompactor(
            TranscriptService(InMemoryTranscriptRepository())
        ).compact(
            tenant_id="tenant",
            session_id="session",
            sender_agent_id="compactor",
            messages=(_message(1), _message(3)),
            idempotency_key="gap",
        )
