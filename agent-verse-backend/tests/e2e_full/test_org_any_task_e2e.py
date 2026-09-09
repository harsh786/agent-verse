"""e2e_full (WS-2): an ARBITRARY NL org mission executes end-to-end.

Proves the any-task autonomous path against real Postgres + Redis:

  arbitrary objective
    -> a fit team forms dynamically (real MetaOrchestrator/TeamFormationEngine)
    -> a REAL goal is dispatched to the agent loop and reaches a terminal state
    -> the mission is finalized with a real aggregated deliverable/report
    -> the rich console events were published: task.decomposed, task.assigned,
       task.handoff, agent.working, mission.progress, mission.completed

No industry template is used — the objective is a one-off, so this exercises the
generalized composer + real decomposition, not a hardcoded blueprint.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def _in_process_goals(app: Any) -> Any:
    """Run dispatched goals in-process (no Celery) with a benign provider.

    Mirrors tests/e2e_full/test_org_mission_hitl_e2e.py's fixture: nulling the
    task queue makes GoalService.submit_goal execute the goal in-process so the
    test can poll it to a terminal state within the process.
    """
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = FakeProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def _wait_for_goal_terminal(
    tenant_client: Any, goal_id: str, *, timeout: float = 45.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"/goals/{goal_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in ("complete", "completed", "failed"):
                return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"goal {goal_id} never reached terminal; last={last.get('status')!r}")


async def _collect_event_types(tenant_client: Any, org_id: str) -> set[str]:
    resp = await tenant_client.get(f"/v1/org/{org_id}/events", params={"limit": 200})
    assert resp.status_code == 200, f"events list failed: {resp.status_code} {resp.text}"
    body = resp.json()
    rows = body.get("data", body) if isinstance(body, dict) else body
    return {str(r.get("event_type")) for r in rows}


async def test_arbitrary_mission_executes_forms_team_completes_and_emits_events(
    tenant_client: Any, _in_process_goals: Any
) -> None:
    # 1. A fresh org with no industry template.
    org_resp = await tenant_client.post(
        "/v1/org", json={"name": "Any-Task Org", "autonomy_level": 3}
    )
    assert org_resp.status_code == 201, f"org create failed: {org_resp.text}"
    org_id = org_resp.json()["id"]

    # 2. Dispatch an ARBITRARY objective (not a known blueprint domain).
    objective = (
        "Research emerging solid-state battery vendors, compare their cycle life "
        "and cost, and compile a shortlist recommendation for the product team"
    )
    mission_resp = await tenant_client.post(
        f"/v1/org/{org_id}/missions/execute",
        json={"title": "Battery vendor shortlist", "objective": objective},
    )
    assert mission_resp.status_code == 201, f"mission dispatch failed: {mission_resp.text}"
    body = mission_resp.json()
    mission_id = body["mission_id"]

    # A team formed dynamically for this arbitrary objective.
    assert body.get("team_id"), f"no team formed: {body}"

    # A REAL goal was dispatched to the agent loop.
    goal_id = body.get("goal_id")
    assert goal_id, f"mission dispatch produced no real goal: {body}"

    # 3. The real goal reaches a terminal state.
    goal = await _wait_for_goal_terminal(tenant_client, goal_id)
    assert str(goal.get("status")) in ("complete", "completed"), (
        f"goal did not complete: {goal.get('status')!r}"
    )

    # 4. Finalize the mission — reconcile subtasks + aggregate a real deliverable.
    fin_resp = await tenant_client.post(f"/v1/org/{org_id}/missions/{mission_id}/finalize")
    assert fin_resp.status_code == 200, f"finalize failed: {fin_resp.text}"
    fin = fin_resp.json()
    assert fin.get("finalized") is True, f"mission not finalized: {fin}"
    assert fin.get("status") == "completed", f"mission not completed: {fin}"
    # The deliverable is a REAL aggregated report tied to the goal, not a stub.
    result = fin.get("result") or {}
    assert result.get("goal_id") == goal_id
    assert result.get("subtasks"), "aggregated report has no subtasks"

    # 5. The mission itself is terminal-complete.
    mission_get = await tenant_client.get(f"/v1/org/{org_id}/missions/{mission_id}")
    assert mission_get.status_code == 200
    assert str(mission_get.json().get("status")) == "completed"

    # 6. The rich console events were all published for this mission.
    event_types = await _collect_event_types(tenant_client, org_id)
    for required in (
        "team.formed",
        "task.decomposed",
        "task.assigned",
        "agent.working",
        "task.handoff",
        "mission.progress",
        "mission.completed",
    ):
        assert required in event_types, (
            f"missing event {required!r}; published={sorted(event_types)}"
        )
