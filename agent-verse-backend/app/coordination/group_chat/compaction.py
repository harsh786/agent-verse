"""Append-only transcript compaction preserving evidence and dissent."""

from __future__ import annotations

from app.coordination.contracts import Classification
from app.coordination.transcript.models import TranscriptMessage
from app.coordination.transcript.service import TranscriptService


class TranscriptCompactor:
    def __init__(self, transcript: TranscriptService) -> None:
        self._transcript = transcript

    async def compact(
        self,
        *,
        tenant_id: str,
        session_id: str,
        sender_agent_id: str,
        messages: tuple[TranscriptMessage, ...],
        idempotency_key: str,
    ) -> TranscriptMessage:
        if not messages:
            raise ValueError("cannot compact an empty interval")
        sequences = [item.sequence for item in messages]
        if sequences != list(range(min(sequences), max(sequences) + 1)):
            raise ValueError("compaction interval must be contiguous")
        retained = [
            f"{item.message_type}:{item.safe_content or item.artifact_reference}"
            for item in messages
            if item.message_type in {"decision", "question", "evidence"}
            or item.trust_label == "quarantined"
        ]
        summary = " | ".join(retained) or f"Compacted {len(messages)} routine messages"
        return await self._transcript.append(
            tenant_id=tenant_id,
            session_id=session_id,
            sender_agent_id=sender_agent_id,
            message_type="summary",
            content=summary[:16_000],
            classification=max(
                (item.classification for item in messages),
                key=lambda item: list(Classification).index(item),
            ),
            idempotency_key=idempotency_key,
            provenance_chain=tuple(item.message_id for item in messages),
            compaction_interval=(min(sequences), max(sequences)),
        )


__all__ = ["TranscriptCompactor"]
