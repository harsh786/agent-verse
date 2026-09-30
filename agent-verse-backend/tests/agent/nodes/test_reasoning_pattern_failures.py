"""CORE-05: a reasoning-node failure leaves a pattern_failed record and event."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-pf", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Boom:
    def __init__(self, *a: Any, **k: Any) -> None:
        raise RuntimeError("pattern exploded")


CASES = [
    ("_node_self_consistency", "self_consistency",
     "app.agent.patterns.self_consistency.SelfConsistencyPattern"),
    ("_node_tree_of_thoughts", "tree_of_thoughts",
     "app.agent.patterns.tree_of_thoughts.TreeOfThoughtsPattern"),
    ("_node_peer_review", "peer_review", "app.agent.patterns.peer_review.PeerReviewPattern"),
    ("_node_supervisor_check", "supervisor", "app.agent.supervisor.SupervisorAgent"),
    ("_node_debate", "debate", "app.agent.debate.DebateOrchestrator"),
]


@pytest.mark.parametrize(("node", "pattern", "target"), CASES)
async def test_node_failure_records_pattern_failed(node: str, pattern: str, target: str) -> None:
    graph = AgentGraph(
        planner=FakeProvider(responses=["p"]),
        executor=FakeProvider(responses=["e"]),
        verifier=FakeProvider(responses=['{"success": true}']),
    )
    graph._goal_service = object()  # the supervisor node needs a wired service
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    graph._event_callback = _cb  # type: ignore[assignment]
    state = AgentState(goal="goal", tenant_ctx=T)
    state.steps.append(StepResult(description="s", output="an answer", status=StepStatus.COMPLETE))

    with patch(target, _Boom):
        await getattr(graph, node)({"agent_state": state, "tenant_ctx": T})

    failed = state.context.get("patterns_failed")
    assert failed and failed[-1]["pattern"] == pattern
    assert failed[-1]["error_type"] == "RuntimeError"
    assert any(
        e.get("type") == "pattern_failed" and e.get("pattern") == pattern for e in events
    )
