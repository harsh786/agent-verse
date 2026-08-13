"""Classification-aware transcript append service."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from app.coordination.contracts import Classification
from app.coordination.transcript.models import TranscriptMessage
from app.coordination.transcript.repository import TranscriptRepository
from app.intelligence.guardrails import GuardrailChecker


class TranscriptService:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repository = repository
        self._guardrail = GuardrailChecker()

    async def append(
        self,
        *,
        tenant_id: str,
        session_id: str,
        sender_agent_id: str,
        message_type: str,
        content: str,
        classification: Classification,
        idempotency_key: str,
        recipient_agent_ids: tuple[str, ...] = (),
        provenance_chain: tuple[str, ...] = (),
        clearance_allowed: bool = True,
        compaction_interval: tuple[int, int] | None = None,
    ) -> TranscriptMessage:
        injection = bool(self._guardrail.check_goal(content))
        trust = "quarantined" if injection else "trusted"
        clearance = "allowed" if clearance_allowed else "denied"
        safe_content = content if clearance_allowed and not injection else "[REDACTED]"
        message = TranscriptMessage(
            message_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"{tenant_id}:{session_id}:{idempotency_key}"
            ).hex,
            tenant_id=tenant_id,
            session_id=session_id,
            sequence=1,
            sender_agent_id=sender_agent_id,
            recipient_agent_ids=recipient_agent_ids,
            message_type=message_type,
            safe_content=safe_content,
            classification=classification,
            trust_label=trust,
            provenance_chain=provenance_chain,
            source_digest=hashlib.sha256(content.encode()).hexdigest(),
            clearance_decision=clearance,
            compacts_from_sequence=(compaction_interval or (None, None))[0],
            compacts_to_sequence=(compaction_interval or (None, None))[1],
            idempotency_key=idempotency_key,
            created_at=datetime.now(UTC),
        )
        return await self._repository.append(message)

    async def page(
        self, tenant_id: str, session_id: str, **kwargs: int
    ) -> tuple[TranscriptMessage, ...]:
        return await self._repository.page(tenant_id, session_id, **kwargs)


__all__ = ["TranscriptService"]
