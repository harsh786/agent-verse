from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_get_goal_includes_result_artifact_for_completed_goal() -> None:
    tenant = TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="key-1")
    service = GoalService()
    service._goals["goal-1"] = GoalRecord(
        goal_id="goal-1",
        tenant_id="tenant-1",
        goal_text="fetch jira",
        status=GoalStatus.COMPLETE,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        events=[
            {
                "type": "tool_call_complete",
                "tool": "jira_search_issues",
                "success": True,
                "output": {
                    "issues": [{"key": "PCF-58608", "summary": "Deployment fix"}]
                },
            },
            {"type": "goal_complete"},
        ],
    )

    response = await service.get_goal("goal-1", tenant)

    assert response["result_artifact"]["kind"] == "table"
    assert response["result_artifact"]["tables"][0]["rows"][0]["key"] == "PCF-58608"
