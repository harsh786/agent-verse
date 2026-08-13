from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.routing_runtime.contracts import OptimizationOutcome
from app.routing_runtime.decision_store import InMemoryDecisionStore
from tests.routing_runtime.test_contracts import decision


@pytest.mark.asyncio
async def test_store_is_idempotent_and_tenant_scoped() -> None:
    store = InMemoryDecisionStore()
    item = decision()
    assert await store.save_decision(item) == await store.save_decision(item)
    assert await store.get_decision("other", item.decision_id) is None
    outcome = OptimizationOutcome(
        outcome_id="outcome",
        tenant_id="tenant",
        decision_id=item.decision_id,
        attempt=1,
        evaluator_version="v1",
        success=True,
        quality_score=9000,
        actual_cost_usd=0.1,
        actual_latency_ms=10,
        prompt_tokens=5,
        completion_tokens=5,
        fallback_used=False,
        recorded_at=datetime.now(UTC),
    )
    assert await store.save_outcome(outcome) == await store.save_outcome(outcome)
