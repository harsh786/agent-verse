"""e2e_full: AI-org mission HITL — a mission's agent hits a gated action,
pauses, surfaces to the approvals inbox, and resumes to completion on approval.

WS-3 path 2. Before this fix, ``OrgService.create_mission_and_execute`` never
forced supervised autonomy for the dispatched goal, so even a mission
MetaOrchestrator flagged as needing an approval gate (high risk / legal /
finance / over budget) would run "bounded-autonomous" — the executor logs the
HITL request but does not block (see app/agent/nodes/executor_mixin.py) — and
the pre-dispatch "approval_gates" task/ApprovalChainEngine record it also
creates is purely informational and never gated the actual dispatch. This test
proves the SAME HITLGateway used by direct goal submission (WS-3 path 1) is
now actually exercised for a mission-dispatched goal too: no parallel
mechanism, one reachable implementation.

Reuses the exact ``_HighRiskPlanProvider`` / polling pattern from
tests/e2e_full/test_goal_hitl_lifecycle_e2e.py for the underlying goal
execution; drives mission creation through the real
``POST /v1/org/{org_id}/missions/execute`` endpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _HighRiskPlanProvider(FakeProvider):
    """Deterministic provider that plans one explicit high-risk step.

    Identical in spirit to test_goal_hitl_lifecycle_e2e.py's provider — kept
    local so this file has no cross-file test-module import dependency.
    """

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["deploy the payment service to production"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "deployment verified"}'
        else:
            content = "Deployed the payment service to production successfully."
        from app.providers.base import CompletionResponse

        return CompletionResponse(
            content=content,
            model=getattr(request, "model", "fake"),
            input_tokens=10,
            output_tokens=len(content.split()),
        )


async def _wait_for_pending_approval(
    tenant_client: Any, goal_id: str, *, timeout: float = 20.0
) -> dict[str, Any]:
    """Poll GET /governance/approvals until a pending request for goal_id shows."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get("/governance/approvals")
        if resp.status_code == 200:
            for req in resp.json():
                if req.get("goal_id") == goal_id and str(req.get("status")) in (
                    "pending",
                    "PENDING",
                ):
                    return dict(req)
        await asyncio.sleep(0.25)
    raise AssertionError(f"no pending HITL approval for goal {goal_id} within {timeout}s")


@pytest.fixture
def _pinned_high_risk_provider(app: Any) -> Any:
    """Pin the high-risk planner and run goals in-process for one test.

    See test_goal_hitl_lifecycle_e2e.py's fixture of the same name for the
    full rationale — duplicated here to keep this file self-contained.
    """
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _HighRiskPlanProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


def _fake_plan_mission_factory() -> Any:
    """Deterministic OrchestrationPlan: single_agent topology + a high-risk
    approval gate.

    The real MetaOrchestrator's capability heuristic maps almost any goal text
    to several departments (its generic "web_search"/"knowledge_synthesis"
    fallback capabilities alone span 5+ departments in DEPT_CAPABILITY_MAP),
    which always selects the "swarm" topology -> "multi_agent" workflow_mode
    — a separate, stub-like execution path that does not run the single-agent
    AgentGraph/executor pipeline at all (confirmed: goals dispatched this way
    complete in 3 events with no LLM calls). That topology-selection heuristic
    is unrelated to HITL and out of scope here, so this test pins a
    deterministic plan instead of fighting it, to exercise exactly the piece
    WS-3 fixed: approval_gates -> forced supervised autonomy -> the real
    executor_mixin HITL gate -> HITLGateway -> resume.
    """

    async def _fake_plan_mission(
        self: Any, goal: str, org: Any, tenant_id: str, mission: Any = None
    ) -> Any:
        from app.org.meta_orchestrator import OrchestrationPlan
        from app.org.team_formation import TeamManifest

        manifest = TeamManifest(
            mission_id=str(getattr(mission, "id", "m")),
            team_name="ws3-ops-team",
            roles=[],
            departments=["engineering"],
            estimated_cost_usd=0.5,
            estimated_duration_hours=1.0,
            risk_level="high",
            success_probability=0.9,
            agent_count=1,
            requires_human_preview=True,
            formation_reasoning="forced deterministic plan for WS-3 e2e test",
        )
        return OrchestrationPlan(
            mission_id=str(getattr(mission, "id", "m")),
            topology="sequential",  # -> workflow_mode "single_agent"
            departments=["engineering"],
            team_manifest=manifest,
            model_gateway_profile="smart",
            autonomy_level=2,
            approval_gates=["high_risk_action"],
            execution_phases=[],
            estimated_total_cost_usd=0.5,
            estimated_total_duration_hours=1.0,
        )

    return _fake_plan_mission


async def test_org_mission_gated_action_pauses_then_completes_on_approval(
    tenant_client: Any, _pinned_high_risk_provider: Any, monkeypatch: Any
) -> None:
    from app.org.meta_orchestrator import MetaOrchestrator

    monkeypatch.setattr(MetaOrchestrator, "plan_mission", _fake_plan_mission_factory())

    # 1. Create an org.
    org_resp = await tenant_client.post("/v1/org", json={"name": "WS-3 HITL Org"})
    assert org_resp.status_code == 201, f"org create failed: {org_resp.status_code} {org_resp.text}"
    org_id = org_resp.json()["id"]

    # 2. Dispatch a mission. MetaOrchestrator.plan_mission is pinned (above) to
    #    a plan with a "high_risk_action" approval gate, which forces the
    #    dispatched goal into supervised autonomy (WS-3 fix in
    #    app/org/service.py::create_mission_and_execute).
    mission_resp = await tenant_client.post(
        f"/v1/org/{org_id}/missions/execute",
        json={
            "title": "Ship the release",
            "objective": "Deploy the payment service to production",
        },
    )
    assert mission_resp.status_code == 201, (
        f"mission dispatch failed: {mission_resp.status_code} {mission_resp.text}"
    )
    mission_body = mission_resp.json()
    goal_id = mission_body.get("goal_id")
    assert goal_id, f"mission dispatch returned no goal_id: {mission_body}"

    # 3. The mission's agent must gate the high-risk step: a pending approval
    #    appears in the SAME approvals inbox used by direct goal submission.
    pending = await _wait_for_pending_approval(tenant_client, goal_id)
    assert pending["risk_level"] in ("high", "HIGH")

    # The goal's own status reflects the pause too (WS-3 path 1 fix).
    goal_while_paused = await tenant_client.get(f"/goals/{goal_id}")
    assert goal_while_paused.status_code == 200
    assert str(goal_while_paused.json().get("status")) == "waiting_human"

    # 4. Approve — over the exact same /goals/{id}/approve endpoint path 1 uses.
    approve = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={
            "request_id": pending["request_id"],
            "action": "approve",
            "approver": "e2e-org-operator",
        },
    )
    assert approve.status_code == 200, f"approve failed: {approve.status_code} {approve.text}"
    assert approve.json().get("accepted") in (True, 1)

    # 5. The mission's underlying goal resumes and runs to completion.
    deadline = asyncio.get_event_loop().time() + 30.0
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            last = got.json()
            if str(last.get("status")) in ("complete", "failed"):
                break
        await asyncio.sleep(0.3)
    assert str(last.get("status")) == "complete", (
        f"approved mission goal should complete, last={last.get('status')!r}"
    )
