"""Tests for Phase 6: SelfImprovementEngine.process_feedback_batch."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_process_feedback_batch_no_db_returns_zeros() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    engine = SelfImprovementEngine()
    result = await engine.process_feedback_batch(
        db_session_factory=None,
        tenant_id="tenant-1",
    )
    assert result == {"processed": 0, "actions_derived": 0}


@pytest.mark.asyncio
async def test_process_feedback_batch_tolerates_bad_db() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    async def bad_factory() -> None:
        raise RuntimeError("DB unavailable")

    engine = SelfImprovementEngine()
    result = await engine.process_feedback_batch(
        db_session_factory=bad_factory,
        tenant_id="tenant-1",
    )
    # Should return zeros rather than raising
    assert isinstance(result, dict)
    assert "processed" in result
