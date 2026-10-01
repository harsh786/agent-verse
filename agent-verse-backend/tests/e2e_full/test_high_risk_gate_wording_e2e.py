"""e2e_full (HIGH-RISK-GATE-WORDING / RW-20): a rephrased destructive step stops
for approval.

The real-world run planned "Step 3: Remove those identified stale staging
records from the list." for a goal that asked to delete them; the keyword gate
only knew "delete", so no approval was requested and the step ran. Here the same
plan runs through the booted app (manage_pools=True, real Postgres + Redis) with
a supervised agent: the read-only steps run, the "Remove" step must park the goal
on a durable approval, and a rejection must keep it from ever executing.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_GOAL = (
    "Demo list (in-memory sample data, no external systems or tools needed): rec-101 "
    "env=staging last_used=2025-01-03; rec-102 env=production last_used=2026-09-28; "
    "rec-103 env=staging last_used=2025-02-11. Delete the stale staging records "
    "(env=staging and last_used before 2026) from the demo list and report exactly "
    "which record IDs were removed and which remain."
)
_IDENTIFY = "Step 1: Identify records where env equals staging and last_used is before 2026."
_REMOVE = "Step 2: Remove those identified stale staging records from the list."


class _Rw20PlanProvider(FakeProvider):
    """Plans the RW-20 wording; answers every other call deterministically."""

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        text = " ".join(str(getattr(m, "content", "")) for m in request.messages)
        if "steps" in props:
            content = json.dumps({"steps": [_IDENTIFY, _REMOVE]})
        elif "success" in props:
            content = '{"success": true, "reason": "step output matches the step"}'
        elif f"Step: {_REMOVE}" in text:
            content = "Removed rec-101 and rec-103; rec-102 remains."
        else:
            content = "Stale staging records: rec-101, rec-103."
        return CompletionResponse(
            content=content,
            model=getattr(request, "model", "fake"),
            input_tokens=10,
            output_tokens=len(content.split()),
        )


@pytest.fixture
def rw20_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    provider = _Rw20PlanProvider()
    app.state._llm_provider_override = provider
    gs._task_queue = None  # run inline: this tier has no Celery worker
    try:
        yield provider
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def _supervised_agent(tenant_client: Any) -> str:
    resp = await tenant_client.post(
        "/agents", json={"name": "rw20-gate", "autonomy_mode": "supervised"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("agent_id") or body.get("id"))


async def _pending_approval(tenant_client: Any, goal_id: str) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + 30.0
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get("/governance/approvals")
        if resp.status_code == 200:
            for req in resp.json():
                if req.get("goal_id") == goal_id and str(req.get("status")).lower() == "pending":
                    return dict(req)
        goal = (await tenant_client.get(f"/goals/{goal_id}")).json()
        assert str(goal.get("status")) not in ("complete", "failed"), (
            f"goal finished without asking for approval: {goal}"
        )
        await asyncio.sleep(0.25)
    raise AssertionError(f"no pending approval for goal {goal_id}")


async def _terminal(tenant_client: Any, goal_id: str) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + 60.0
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            last = got.json()
            if str(last.get("status")) in ("complete", "failed", "cancelled"):
                return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"goal {goal_id} did not finish: {last}")


def _executed_steps(provider: _Rw20PlanProvider) -> list[str]:
    seen: list[str] = []
    for req in provider.call_history:
        text = " ".join(str(getattr(m, "content", "")) for m in req.messages)
        for step in (_IDENTIFY, _REMOVE):
            if f"Step: {step}" in text and step not in seen:
                seen.append(step)
    return seen


async def test_remove_wording_stops_for_approval_and_rejection_blocks_it(
    app: Any, tenant_client: Any, rw20_provider: _Rw20PlanProvider
) -> None:
    agent_id = await _supervised_agent(tenant_client)
    submit = await tenant_client.post("/goals", json={"goal": _GOAL, "agent_id": agent_id})
    assert submit.status_code == 202, submit.text
    goal_id = submit.json()["goal_id"]

    pending = await _pending_approval(tenant_client, goal_id)
    assert "Remove those identified stale staging records" in str(pending.get("action")), pending
    assert str(pending.get("risk_level")).lower() == "high"
    # The read-only step ran; the removal did not.
    assert _executed_steps(rw20_provider) == [_IDENTIFY]
    paused = (await tenant_client.get(f"/goals/{goal_id}")).json()
    assert str(paused.get("status")) == "waiting_human", paused

    from app.tenancy.context import PlanTier, TenantContext

    me = (await tenant_client.get("/tenants/me")).json()
    ctx = TenantContext(tenant_id=str(me["tenant_id"]), plan=PlanTier.FREE, api_key_id="")
    events = await app.state.goal_service.get_events(goal_id=goal_id, tenant_ctx=ctx)
    waiting = [e for e in events if e.get("type") == "waiting_approval"]
    assert waiting, [e.get("type") for e in events]
    assert any("remove" in str(r) for r in waiting[-1].get("risk_reasons", [])), waiting[-1]

    reject = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": pending["request_id"], "action": "reject", "approver": "e2e"},
    )
    assert reject.status_code == 200, reject.text
    final = await _terminal(tenant_client, goal_id)
    assert str(final.get("status")) == "failed", final
    assert _REMOVE not in _executed_steps(rw20_provider), "a rejected step must never run"


async def test_remove_wording_runs_after_approval(
    app: Any, tenant_client: Any, rw20_provider: _Rw20PlanProvider
) -> None:
    agent_id = await _supervised_agent(tenant_client)
    submit = await tenant_client.post("/goals", json={"goal": _GOAL, "agent_id": agent_id})
    assert submit.status_code == 202, submit.text
    goal_id = submit.json()["goal_id"]

    pending = await _pending_approval(tenant_client, goal_id)
    approve = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": pending["request_id"], "action": "approve", "approver": "e2e"},
    )
    assert approve.status_code == 200, approve.text
    final = await _terminal(tenant_client, goal_id)
    assert str(final.get("status")) == "complete", final
    assert _executed_steps(rw20_provider) == [_IDENTIFY, _REMOVE]
