"""e2e_full: full goal lifecycle through a real HITL approval gate.

The Raccoon-plan flagship: submit a goal → the agent plans a high-risk step →
the executor gates it through the wired ``HITLGateway`` → a pending approval is
visible over the HTTP API → a human approves (or rejects) → the goal reaches a
terminal state. Proven against the booted app (manage_pools=True) with real
Postgres + Redis.

Determinism: the app's default FakeProvider plans a fixed low-risk step, so we
pin an ``_llm_provider_override`` on ``app.state`` (the production seam used by
the simulation/replay/eval tiers) that plans an explicit high-risk step
("deploy … to production"). The agent runs supervised so the executor blocks on
``wait_for_approval`` until the API approval resolves it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _HighRiskPlanProvider(FakeProvider):
    """Deterministic provider that plans one explicit high-risk step.

    Branches on the request's response schema so the answer is correct
    regardless of how many auxiliary LLM calls the loop makes:

    * planning call (schema exposes ``steps``) → a single high-risk step
    * verification call (schema exposes ``success``) → success verdict
    * anything else (execution / free-form) → a plausible step output
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


async def _create_supervised_agent(tenant_client: Any) -> str:
    resp = await tenant_client.post(
        "/agents",
        json={
            "name": "e2e-hitl-supervised",
            "autonomy_mode": "supervised",
            "goal_template": "",
        },
    )
    assert resp.status_code == 201, f"agent create failed: {resp.status_code} {resp.text}"
    body = resp.json()
    return str(body.get("agent_id") or body.get("id"))


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
                    return req
        await asyncio.sleep(0.25)
    raise AssertionError(f"no pending HITL approval for goal {goal_id} within {timeout}s")


@pytest.fixture
def _pinned_high_risk_provider(app: Any) -> Any:
    """Pin the high-risk planner and run goals in-process for one test.

    ``goal_service._app_state`` is the FastAPI app object, so the provider
    override is set there (that is what ``_make_agent_loop_for_tenant`` reads).
    The e2e harness has no Celery worker, so we also null the goal service's
    task queue for the duration so goals run inline via the real agent loop
    instead of enqueuing to an absent worker. Both are restored afterwards.
    """
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    # The graph builder normalises app_state → app.state, so the override lives
    # on app.state.
    app.state._llm_provider_override = _HighRiskPlanProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_high_risk_goal_gates_then_completes_on_approval(
    tenant_client: Any, _pinned_high_risk_provider: Any
) -> None:
    agent_id = await _create_supervised_agent(tenant_client)

    submit = await tenant_client.post(
        "/goals",
        json={"goal": "Ship the release", "agent_id": agent_id},
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]
    assert goal_id

    # The executor must gate the high-risk step: a pending approval appears.
    pending = await _wait_for_pending_approval(tenant_client, goal_id)
    assert pending["risk_level"] in ("high", "HIGH")
    request_id = pending["request_id"]

    # Approve over the HTTP API — unblocks the waiting executor.
    approve = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": request_id, "action": "approve", "approver": "e2e-operator"},
    )
    assert approve.status_code == 200, f"approve failed: {approve.status_code} {approve.text}"
    assert approve.json().get("accepted") in (True, 1)

    # The goal now runs to a terminal state.
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
        f"approved goal should complete, last={last.get('status')!r}"
    )


async def test_high_risk_goal_fails_on_rejection(
    tenant_client: Any, _pinned_high_risk_provider: Any
) -> None:
    agent_id = await _create_supervised_agent(tenant_client)

    submit = await tenant_client.post(
        "/goals",
        json={"goal": "Ship the risky release", "agent_id": agent_id},
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    pending = await _wait_for_pending_approval(tenant_client, goal_id)
    request_id = pending["request_id"]

    reject = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": request_id, "action": "reject", "approver": "e2e-operator"},
    )
    assert reject.status_code == 200, f"reject failed: {reject.status_code} {reject.text}"

    # A rejected high-risk step must not let the goal complete successfully.
    deadline = asyncio.get_event_loop().time() + 30.0
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            last = got.json()
            if str(last.get("status")) in ("complete", "failed"):
                break
        await asyncio.sleep(0.3)
    assert str(last.get("status")) == "failed", (
        f"rejected goal must not complete, last={last.get('status')!r}"
    )
