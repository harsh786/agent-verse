"""A deduplicated goal submission must not consume a concurrent-goal slot.

Regression: submit_goal incremented the tenant's concurrency counter BEFORE the
dedup check and returned early on a hit without decrementing, so each duplicate
submission permanently leaked a slot until the tenant hit 429s.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


@pytest.mark.asyncio
async def test_dedup_hit_takes_no_concurrency_slot() -> None:
    svc = GoalService()
    inc = AsyncMock()
    with (
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", inc),
        patch("app.services.dedup._default_deduplicator.claim",
              AsyncMock(return_value="existing-goal")),
    ):
        result = await svc.submit_goal(goal="same goal", priority="normal", dry_run=False,
                                       tenant_ctx=T)
    assert result["deduplicated"] is True and result["goal_id"] == "existing-goal"
    inc.assert_not_awaited()
