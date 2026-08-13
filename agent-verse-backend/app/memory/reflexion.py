"""Canonical awaited Reflexion extraction, recall, and effectiveness service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from app.memory.contracts import (
    Classification,
    MemoryFeedback,
    MemoryRecallRequest,
    MemoryRecord,
    MemoryWriteRequest,
)


class ReflexionService:
    def __init__(self, *, repository: Any) -> None:
        self._repository = repository

    async def learn(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        execution_id: str,
        safe_lesson: str,
        evidence_refs: tuple[str, ...],
        classification: Classification,
        confidence: int,
        idempotency_key: str,
    ) -> MemoryRecord:
        return cast(
            MemoryRecord,
            await self._repository.write(
                MemoryWriteRequest(
                    tenant_id=tenant_id,
                    memory_kind="reflexion",
                    content=safe_lesson,
                    source_goal_id=goal_id,
                    source_execution_id=execution_id,
                    evidence_refs=evidence_refs,
                    classification=classification,
                    confidence=confidence,
                    idempotency_key=idempotency_key,
                    retention_policy_id="reflexion-standard",
                )
            ),
        )

    async def recall(
        self,
        *,
        tenant_id: str,
        query: str,
        allowed_data_classes: frozenset[Classification],
        top_k: int = 5,
        token_budget: int = 1_000,
    ) -> tuple[MemoryRecord, ...]:
        hits = await self._repository.recall(
            MemoryRecallRequest(
                tenant_id=tenant_id,
                query=query,
                memory_kinds=frozenset({"reflexion"}),
                top_k=top_k,
                min_confidence=1,
                allowed_data_classes=allowed_data_classes,
                as_of=datetime.now(UTC),
                token_budget=token_budget,
            )
        )
        return tuple(hit.record for hit in hits)

    async def record_effectiveness(
        self,
        *,
        tenant_id: str,
        memory_id: str,
        execution_id: str,
        used: bool,
        helpful: bool,
        harmful: bool,
        outcome_score: int,
        reason: str,
    ) -> MemoryRecord:
        return cast(
            MemoryRecord,
            await self._repository.feedback(
                MemoryFeedback(
                    memory_id=memory_id,
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    was_used=used,
                    was_helpful=helpful,
                    was_harmful=harmful,
                    outcome_score=outcome_score,
                    feedback_reason=reason,
                    recorded_at=datetime.now(UTC),
                )
            ),
        )


__all__ = ["ReflexionService"]
