from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.memory.contracts import MemoryFeedback, MemoryRecord


def test_memory_record_pins_embedding_profile_and_dimension() -> None:
    common = {
        "memory_id": "m",
        "tenant_id": "t",
        "memory_kind": "reflexion",
        "content_ref": "memory://m",
        "safe_summary": "lesson",
        "source_goal_id": "g",
        "source_execution_id": "e",
        "evidence_refs": ("evidence://1",),
        "classification": "internal",
        "confidence": 9000,
        "lifecycle_state": "active",
        "version": 1,
        "embedding_model": "memory-embedding-v1",
        "embedding_dimension": 1536,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "idempotency_key": "one",
    }
    assert MemoryRecord(**common).embedding_dimension == 1536
    with pytest.raises(ValidationError):
        MemoryRecord(**{**common, "embedding_dimension": 768})


def test_feedback_cannot_be_helpful_and_harmful() -> None:
    with pytest.raises(ValidationError):
        MemoryFeedback(
            memory_id="m",
            tenant_id="t",
            execution_id="e",
            was_used=True,
            was_helpful=True,
            was_harmful=True,
            outcome_score=0,
            feedback_reason="invalid",
            recorded_at=datetime.now(UTC),
        )
