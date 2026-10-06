"""a01-F013-02: GET /goals/{id}/pattern-selection says what the runtime actually ran.

``pattern_selection`` is computed from the goal text for every goal (the
selector's recommendation), whatever the runtime executes: an agent's pattern
flags, a workflow_mode, a downgraded strategy override or the legacy kernel can
all run something else. The endpoint used to present that recommendation as
"the pattern this goal was routed to". It now carries the runtime's own record
(``strategy_execution``: driver, patterns, downgrades) next to it and says
whether they match.
"""

from __future__ import annotations

from typing import Any

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("tenant-1", PlanTier.PROFESSIONAL, "key-1")


def _service(goal_id: str, ctx: dict[str, Any], status: GoalStatus) -> GoalService:
    svc = GoalService()
    svc._goals[goal_id] = GoalRecord(
        goal_id=goal_id,
        goal_text="Compare three vendors' SLAs and recommend one, with sources",
        status=status,
        tenant_id=TENANT.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-10-06T00:00:00Z",
        execution_context=ctx,
    )
    return svc


async def test_a_goal_that_ran_something_else_says_so() -> None:
    svc = _service(
        "g-mismatch",
        {
            "pattern_selection": {
                "source": "auto",
                "primary_pattern": "tree_of_thoughts",
                "primary_pattern_name": "Tree of Thoughts",
                "reasoning_patterns": ["tree_of_thoughts"],
                "multi_agent_patterns": ["single_agent"],
            },
            "strategy_execution": {"driver": "agent_graph", "patterns": ["react", "supervisor"]},
        },
        GoalStatus.COMPLETE,
    )
    body = await svc.get_pattern_selection("g-mismatch", TENANT)

    assert body["selection_kind"] == "recommendation"
    assert body["execution"]["state"] == "recorded"
    assert body["execution"]["driver"] == "agent_graph"
    assert body["executed_patterns"] == ["react", "supervisor"]
    assert body["matches_execution"] is False
    # The recommendation itself is still reported unchanged.
    assert body["primary_pattern"] == "tree_of_thoughts"


async def test_a_goal_that_ran_its_selection_matches() -> None:
    svc = _service(
        "g-match",
        {
            "pattern_selection": {"source": "auto", "primary_pattern": "react"},
            "strategy_execution": {"driver": "agent_graph", "patterns": ["react"]},
        },
        GoalStatus.COMPLETE,
    )
    body = await svc.get_pattern_selection("g-match", TENANT)
    assert body["matches_execution"] is True


async def test_a_downgraded_override_reports_the_downgrade() -> None:
    svc = _service(
        "g-down",
        {
            "strategy_runtime": {"primary_strategy": "debate"},
            "strategy_downgraded": True,
            "strategy_downgrade": {"reason": "tenant_not_on_strategy_runtime_v2_allowlist"},
            "strategy_execution": {
                "driver": "agent_graph",
                "patterns": ["react"],
                "downgrades": [{"strategy_id": "debate", "to": "react", "reason": "x"}],
            },
        },
        GoalStatus.COMPLETE,
    )
    body = await svc.get_pattern_selection("g-down", TENANT)
    assert body["source"] == "override"
    assert body["primary_pattern"] == "debate"
    assert body["matches_execution"] is False
    assert body["strategy_downgraded"] is True
    assert body["execution"]["downgrades"][0]["strategy_id"] == "debate"


async def test_a_goal_not_started_yet_has_no_execution_verdict() -> None:
    svc = _service(
        "g-pending",
        {"pattern_selection": {"source": "auto", "primary_pattern": "react"}},
        GoalStatus.PLANNING,
    )
    body = await svc.get_pattern_selection("g-pending", TENANT)
    assert body["execution"] == {"state": "pending"}
    assert body["executed_patterns"] == []
    assert body["matches_execution"] is None
