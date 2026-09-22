"""e2e_full: deeper "AI Agent Team" coverage for ``app/org/service.py::OrgService``.

``tests/e2e_full/test_org_any_task_e2e.py`` already proves a mission forms a
team, decomposes into >=2 subtasks, completes, and emits the full event set —
but only checks that each event *type* fired at least once. It never checks:

  1. that a ``task.handoff`` genuinely links two DIFFERENT agents' subtasks
     (the actual collaboration hand-off), not just that the event exists
  2. what happens when the mission's underlying goal does NOT succeed
     (``OrgService.finalize_mission``'s terminal_fail branch, only unit-tested
     via ``tests/org/test_service_comprehensive.py`` today, never end-to-end)
  3. that the team activity feed ("team channel") is causally ordered and
     that per-task visibility filtering actually scopes to that task

All three run against the real booted app (``manage_pools=True``), real
Postgres + Redis, goals executed in-process (no Celery worker) — same
infrastructure as the sibling e2e_full org tests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def _in_process_goals(app: Any) -> Any:
    """Run dispatched goals in-process (no Celery) with a benign provider.

    Mirrors tests/e2e_full/test_org_any_task_e2e.py's fixture of the same name.
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


def _fake_two_agent_plan_factory() -> Any:
    """Deterministic OrchestrationPlan with a TWO-role team.

    ``decompose_and_assign`` round-robins subtasks across
    ``len(team_manifest.roles)`` viz agent ids, so pinning two roles here
    (rather than leaving team size to the real, non-deterministic
    TeamFormationEngine heuristic) makes the first ``task.handoff`` reliably
    span two DIFFERENT agents — the actual thing worth proving about
    "collaboration", as opposed to a single agent doing everything serially.
    Topology stays "sequential" (-> workflow_mode "single_agent") so the goal
    still runs through the real AgentGraph, not the stub multi_agent path
    (see tests/e2e_full/test_org_mission_hitl_e2e.py's docstring for why that
    path is avoided in these tests).
    """

    async def _fake_plan_mission(
        self: Any, goal: str, org: Any, tenant_id: str, mission: Any = None
    ) -> Any:
        from app.org.meta_orchestrator import OrchestrationPlan
        from app.org.team_formation import RoleAssignment, TeamManifest

        roles = [
            RoleAssignment(
                role_name="research_analyst",
                department_kind="research",
                seniority="mid",
                capabilities=["research"],
                model_profile="smart",
                estimated_cost_usd=0.1,
                estimated_hours=1.0,
                priority=1,
            ),
            RoleAssignment(
                role_name="operations_lead",
                department_kind="operations",
                seniority="senior",
                capabilities=["execution"],
                model_profile="smart",
                estimated_cost_usd=0.1,
                estimated_hours=1.0,
                priority=1,
            ),
        ]
        manifest = TeamManifest(
            mission_id=str(getattr(mission, "id", "m")),
            team_name="collab-team",
            roles=roles,
            departments=["research", "operations"],
            estimated_cost_usd=0.2,
            estimated_duration_hours=2.0,
            risk_level="low",
            success_probability=0.9,
            agent_count=2,
            requires_human_preview=False,
            formation_reasoning="forced deterministic 2-agent plan for e2e collaboration test",
        )
        return OrchestrationPlan(
            mission_id=str(getattr(mission, "id", "m")),
            topology="sequential",  # -> workflow_mode "single_agent"
            departments=["research", "operations"],
            team_manifest=manifest,
            model_gateway_profile="smart",
            autonomy_level=3,
            approval_gates=[],
            execution_phases=[],
            estimated_total_cost_usd=0.2,
            estimated_total_duration_hours=2.0,
        )

    return _fake_plan_mission


async def _wait_for_goal_terminal(
    tenant_client: Any, goal_id: str, *, timeout: float = 45.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"/goals/{goal_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in ("complete", "completed", "failed", "cancelled"):
                return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"goal {goal_id} never reached terminal; last={last.get('status')!r}")


async def _dispatch_mission(tenant_client: Any, *, title: str, objective: str) -> dict[str, Any]:
    org_resp = await tenant_client.post("/v1/org", json={"name": f"{title} Org"})
    assert org_resp.status_code == 201, f"org create failed: {org_resp.text}"
    org_id = org_resp.json()["id"]

    mission_resp = await tenant_client.post(
        f"/v1/org/{org_id}/missions/execute",
        json={"title": title, "objective": objective},
    )
    assert mission_resp.status_code == 201, f"mission dispatch failed: {mission_resp.text}"
    body = mission_resp.json()
    return {"org_id": org_id, **body}


# ── 1. Multi-agent collaboration: a handoff between two DIFFERENT agents ───────


async def test_mission_handoff_links_two_different_agents_in_order(
    tenant_client: Any, _in_process_goals: Any, monkeypatch: Any
) -> None:
    from app.org.meta_orchestrator import MetaOrchestrator

    monkeypatch.setattr(MetaOrchestrator, "plan_mission", _fake_two_agent_plan_factory())

    dispatch = await _dispatch_mission(
        tenant_client,
        title="Vendor shortlist",
        objective="Research emerging vendors and compile a shortlist recommendation",
    )
    org_id = dispatch["org_id"]
    goal_id = dispatch.get("goal_id")
    assert goal_id, f"mission dispatch produced no real goal: {dispatch}"

    await _wait_for_goal_terminal(tenant_client, goal_id)

    # Pull every task.assigned + task.handoff event for this org's activity feed.
    resp = await tenant_client.get(f"/v1/org/{org_id}/events", params={"limit": 200})
    assert resp.status_code == 200
    body = resp.json()
    rows = body.get("data", body) if isinstance(body, dict) else body

    # OrgService.update_task_status ALSO emits a generic, empty-payload
    # "task.assigned" audit event for every status transition (source
    # "system"); decompose_and_assign separately emits the rich,
    # domain-specific one used for the constellation/handoff wiring (source
    # "orchestrator", carries agent_id/order/department). Only the latter is
    # relevant to "which agent got which subtask".
    assigned = [
        r
        for r in rows
        if r.get("event_type") == "task.assigned" and (r.get("payload") or {}).get("agent_id")
    ]
    handoffs = [r for r in rows if r.get("event_type") == "task.handoff"]
    assert len(assigned) >= 2, f"expected >=2 assigned subtasks: {assigned}"
    assert handoffs, f"no task.handoff emitted: {rows}"

    # The two round-robin-assigned agents for the first two subtasks are
    # genuinely different (agent-1 vs agent-2), and the FIRST handoff's
    # from_agent/to_agent match those two real, different agent ids in order —
    # not just "a handoff event of some kind fired".
    assigned_by_order = sorted(assigned, key=lambda r: (r.get("payload") or {}).get("order", 0))
    first_agent = (assigned_by_order[0].get("payload") or {}).get("agent_id")
    second_agent = (assigned_by_order[1].get("payload") or {}).get("agent_id")
    assert first_agent and second_agent and first_agent != second_agent, (
        f"round-robin across 2 roles should assign different agents: "
        f"{first_agent!r} vs {second_agent!r}"
    )

    handoff_payload = handoffs[0].get("payload") or {}
    assert handoff_payload.get("from_agent") == first_agent
    assert handoff_payload.get("to_agent") == second_agent
    # The handoff's task ids are real, distinct subtask ids — the second
    # agent's input is genuinely the first agent's completed task, not a
    # generic/placeholder link.
    assert handoff_payload.get("from_task") != handoff_payload.get("to_task")
    assigned_task_ids = {(r.get("payload") or {}).get("task_id") for r in assigned}
    assert handoff_payload.get("from_task") in assigned_task_ids
    assert handoff_payload.get("to_task") in assigned_task_ids


# ── 2. Mission failure / partial-completion handling ────────────────────────────


async def test_mission_finalize_marks_failed_when_underlying_goal_is_cancelled(
    tenant_client: Any, _in_process_goals: Any
) -> None:
    """When the mission's dispatched goal ends up in a terminal-FAIL state
    (cancelled counts, per OrgService.finalize_mission's terminal_fail set),
    finalize must mark every open task + the mission itself "failed" and
    persist a failure report — proven end-to-end via the real cancel endpoint,
    not by poking internal state."""
    dispatch = await _dispatch_mission(
        tenant_client,
        title="Doomed mission",
        objective="Investigate a topic that will be cancelled before it finishes",
    )
    org_id = dispatch["org_id"]
    mission_id = dispatch["mission_id"]
    goal_id = dispatch.get("goal_id")
    assert goal_id, f"mission dispatch produced no real goal: {dispatch}"

    # Cancel the underlying goal through the SAME real endpoint an operator
    # would use — genuinely reaches a terminal-fail status, no mocking.
    cancel_resp = await tenant_client.post(f"/goals/{goal_id}/cancel")
    assert cancel_resp.status_code == 200, f"cancel failed: {cancel_resp.text}"

    goal = await _wait_for_goal_terminal(tenant_client, goal_id)
    assert str(goal.get("status")) == "cancelled", goal.get("status")

    fin_resp = await tenant_client.post(f"/v1/org/{org_id}/missions/{mission_id}/finalize")
    assert fin_resp.status_code == 200, f"finalize failed: {fin_resp.text}"
    fin = fin_resp.json()
    assert fin.get("finalized") is True, fin
    assert fin.get("status") == "failed", f"mission should be marked failed: {fin}"
    result = fin.get("result") or {}
    assert result.get("goal_status") == "cancelled"
    # Every subtask the report carries is marked failed to match, not left
    # dangling as "assigned"/"in_progress" (the partial-completion case).
    report_subtasks = result.get("subtasks") or []
    assert report_subtasks, f"failure report under-decomposed: {result}"
    assert all(s.get("status") == "failed" for s in report_subtasks), report_subtasks

    # The mission itself, read back fresh, is terminal-failed — not stuck
    # "in_progress" or silently reported as completed.
    mission_get = await tenant_client.get(f"/v1/org/{org_id}/missions/{mission_id}")
    assert mission_get.status_code == 200
    mission_body = mission_get.json()
    assert str(mission_body.get("status")) == "failed", mission_body

    # No open (non-terminal) tasks are left behind on a failed mission.
    tasks_resp = await tenant_client.get(
        f"/v1/org/{org_id}/tasks", params={"mission_id": mission_id, "limit": 200}
    )
    assert tasks_resp.status_code == 200
    tasks_body = tasks_resp.json()
    task_rows = tasks_body.get("data", tasks_body) if isinstance(tasks_body, dict) else tasks_body
    assert task_rows, "expected persisted subtasks for the failed mission"
    assert all(
        str(t.get("status")) in ("completed", "failed", "cancelled", "expired") for t in task_rows
    ), task_rows

    # A mission.failed event was published to the team's activity feed.
    events_resp = await tenant_client.get(f"/v1/org/{org_id}/events", params={"limit": 200})
    assert events_resp.status_code == 200
    events_body = events_resp.json()
    event_rows = (
        events_body.get("data", events_body) if isinstance(events_body, dict) else events_body
    )
    event_types = {str(r.get("event_type")) for r in event_rows}
    assert "mission.failed" in event_types, sorted(event_types)


# ── 3. Team channel: causal ordering + per-task visibility scoping ─────────────


async def test_team_channel_events_are_causally_ordered_and_task_scoped(
    tenant_client: Any, _in_process_goals: Any, monkeypatch: Any
) -> None:
    from app.org.meta_orchestrator import MetaOrchestrator

    monkeypatch.setattr(MetaOrchestrator, "plan_mission", _fake_two_agent_plan_factory())

    dispatch = await _dispatch_mission(
        tenant_client,
        title="Ordering check",
        objective="Compile a short report so the team channel has real events to order",
    )
    org_id = dispatch["org_id"]
    goal_id = dispatch.get("goal_id")
    assert goal_id
    await _wait_for_goal_terminal(tenant_client, goal_id)

    resp = await tenant_client.get(f"/v1/org/{org_id}/events", params={"limit": 200})
    assert resp.status_code == 200
    body = resp.json()
    rows = body.get("data", body) if isinstance(body, dict) else body
    assert rows, "no events published for this mission"

    # `id` is a UUIDv7 (time-sortable, generated in application code at
    # insert time) — a reliable causal-order key independent of the API's own
    # `created_at DESC` sort (which can tie under one DB transaction's frozen
    # `now()`). Reconstruct chronological order from it.
    chronological = sorted(rows, key=lambda r: UUID(str(r["id"])).int)

    def _first_index(event_type: str) -> int:
        for i, r in enumerate(chronological):
            if r.get("event_type") == event_type:
                return i
        raise AssertionError(f"event {event_type!r} never published: {chronological}")

    def _last_index(event_type: str) -> int:
        for i in range(len(chronological) - 1, -1, -1):
            if chronological[i].get("event_type") == event_type:
                return i
        raise AssertionError(f"event {event_type!r} never published: {chronological}")

    # Causal order the team channel must preserve, regardless of API sort:
    # decomposition -> (assign, work) per subtask -> handoff -> dispatched.
    idx_decomposed = _first_index("task.decomposed")
    idx_first_assigned = _first_index("task.assigned")
    idx_first_working = _first_index("agent.working")
    idx_handoff = _first_index("task.handoff")
    idx_dispatched_progress = _last_index("mission.progress")

    assert idx_decomposed < idx_first_assigned, "decomposition must precede assignment"
    assert idx_first_assigned < idx_first_working, "a task is assigned before an agent works it"
    assert idx_first_working < idx_handoff, "work happens before the handoff to the next agent"
    assert idx_handoff < idx_dispatched_progress, (
        "the handoff must precede the mission-dispatched progress event"
    )

    # Visibility scoping: filtering the channel by one specific task's
    # entity_id returns only events tied to THAT task — not the whole mission
    # feed leaking into a per-task view. Every task.assigned event (rich or
    # the generic update_task_status audit one) carries the task id as the
    # row-level entity_id, so use that rather than the payload.
    assigned_events = [r for r in rows if r.get("event_type") == "task.assigned"]
    distinct_task_ids = list(dict.fromkeys(r.get("entity_id") for r in assigned_events))
    assert len(distinct_task_ids) >= 2, distinct_task_ids
    target_task_id = distinct_task_ids[0]
    assert target_task_id

    scoped_resp = await tenant_client.get(
        f"/v1/org/{org_id}/events", params={"limit": 200, "entity_id": target_task_id}
    )
    assert scoped_resp.status_code == 200
    scoped_body = scoped_resp.json()
    scoped_rows = (
        scoped_body.get("data", scoped_body) if isinstance(scoped_body, dict) else scoped_body
    )
    assert scoped_rows, "task-scoped channel view returned nothing"
    assert all(r.get("entity_id") == target_task_id for r in scoped_rows), scoped_rows
    # It must NOT include events scoped to the mission or to the other task.
    other_task_id = distinct_task_ids[1]
    assert other_task_id not in {r.get("entity_id") for r in scoped_rows}
