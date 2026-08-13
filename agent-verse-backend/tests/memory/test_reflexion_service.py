from __future__ import annotations

import pytest

from app.memory.reflexion import ReflexionService
from app.memory.repository import InMemoryMemoryRepository


@pytest.mark.asyncio
async def test_reflexion_learning_recall_and_effectiveness_are_awaited_and_idempotent() -> None:
    service = ReflexionService(repository=InMemoryMemoryRepository())
    kwargs = {
        "tenant_id": "tenant",
        "goal_id": "goal",
        "execution_id": "execution",
        "safe_lesson": "validate evidence before completion",
        "evidence_refs": ("evidence://failure",),
        "classification": "internal",
        "confidence": 9000,
        "idempotency_key": "lesson",
    }
    record = await service.learn(**kwargs)
    assert await service.learn(**kwargs) == record
    recalled = await service.recall(
        tenant_id="tenant",
        query="validate evidence",
        allowed_data_classes=frozenset({"internal"}),
    )
    assert recalled == (record,)
    helpful = await service.record_effectiveness(
        tenant_id="tenant",
        memory_id=record.memory_id,
        execution_id="next",
        used=True,
        helpful=True,
        harmful=False,
        outcome_score=8000,
        reason="prevented failure",
    )
    assert helpful.helpful_count == 1 and helpful.effectiveness_score > 0


@pytest.mark.asyncio
async def test_reflexion_without_evidence_is_quarantined_and_not_recalled() -> None:
    service = ReflexionService(repository=InMemoryMemoryRepository())
    record = await service.learn(
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        safe_lesson="unsupported",
        evidence_refs=(),
        classification="internal",
        confidence=9000,
        idempotency_key="unsupported",
    )
    assert record.lifecycle_state == "quarantined"
    assert (
        await service.recall(
            tenant_id="tenant",
            query="unsupported",
            allowed_data_classes=frozenset({"internal"}),
        )
        == ()
    )
