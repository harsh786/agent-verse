"""Tests for plan limit enforcement."""

from __future__ import annotations

import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import (
    PlanLimitExceededError,
    check_agent_limit,
    check_api_key_limit,
    check_daily_goal_limit,
    check_knowledge_collection_limit,
)

FREE = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
ENTERPRISE = TenantContext(tenant_id="t2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


def test_goal_limit_not_exceeded():
    check_daily_goal_limit(FREE, 999)  # limit is 1000, should not raise


def test_goal_limit_exceeded():
    with pytest.raises(PlanLimitExceededError, match="Daily goal limit"):
        check_daily_goal_limit(FREE, 1000)


def test_goal_limit_enterprise_high():
    check_daily_goal_limit(ENTERPRISE, 9999)  # 10000 limit, should not raise


def test_agent_limit_not_exceeded():
    check_agent_limit(FREE, 1)  # limit is 2


def test_agent_limit_exceeded():
    with pytest.raises(PlanLimitExceededError, match="Agent limit"):
        check_agent_limit(FREE, 2)


def test_api_key_limit_exceeded():
    with pytest.raises(PlanLimitExceededError, match="API key limit"):
        check_api_key_limit(FREE, 2)


def test_knowledge_limit_exceeded():
    with pytest.raises(PlanLimitExceededError, match="Knowledge collection limit"):
        check_knowledge_collection_limit(FREE, 1)


@pytest.mark.asyncio
async def test_goal_service_enforces_daily_limit():
    """GoalService raises PlanLimitExceededError when daily limit hit."""
    from datetime import UTC, datetime

    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService

    svc = GoalService()
    # Manually inject 1000 goals for today
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(1000):
        rec = GoalRecord(
            goal_id=f"g{i}",
            goal_text="test",
            status=GoalStatus.COMPLETE,
            tenant_id=FREE.tenant_id,
            priority="normal",
            dry_run=False,
            created_at=(today.isoformat()),
        )
        svc._goals[rec.goal_id] = rec

    with pytest.raises(PlanLimitExceededError):
        await svc.submit_goal(
            goal="one more",
            priority="normal",
            dry_run=False,
            tenant_ctx=FREE,
        )
