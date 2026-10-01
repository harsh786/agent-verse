"""e2e_full (HITL-DISTRIBUTED-GATES): supervisor/goal_tree and debate goals wait for a human.

Through the booted app (real Postgres + Redis, goals inline, strategy runtime v2
on): a high-risk goal selecting a supervisor-family strategy or debate creates
a persisted approval visible at ``GET /governance/approvals``; approving it via
``POST /goals/{id}/approve`` resumes the run to ``complete``; rejecting it fails
the goal with the rejection as the reason.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.coordination.pattern_run_support import ScriptedProvider

from .conftest import collect_sse

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def _inline_v2(app: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from types import SimpleNamespace

    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = ScriptedProvider()
    gs._task_queue = None
    monkeypatch.setattr(
        "app.orchestration.strategy_certification.RolloutController.choose",
        lambda *_a, **_k: SimpleNamespace(path="v2", shadow_comparison=None),
    )
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def _pending(client: Any, goal_id: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/governance/approvals")
        if resp.status_code == 200:
            for req in resp.json():
                if req.get("goal_id") == goal_id and str(req.get("status")).lower() == "pending":
                    return dict(req)
        await asyncio.sleep(0.25)
    raise AssertionError(f"no pending approval for goal {goal_id}")


async def _terminal(client: Any, goal_id: str, timeout: float = 40.0) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            last = got.json()
            if str(last.get("status")) in ("complete", "failed"):
                return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"goal {goal_id} did not finish: {last.get('status')!r}")


async def _submit(client: Any, strategy_id: str) -> str:
    resp = await client.post(
        "/goals",
        json={
            "goal": f"Deploy the release to prod ({uuid.uuid4().hex[:6]})",
            "strategy_override": strategy_id,
        },
    )
    assert resp.status_code == 202, resp.text
    return str(resp.json()["goal_id"])


@pytest.mark.parametrize("strategy_id", ["supervisor", "debate"])
async def test_high_risk_goal_waits_then_completes_on_approval(
    tenant_client: Any, _inline_v2: None, strategy_id: str
) -> None:
    goal_id = await _submit(tenant_client, strategy_id)
    pending = await _pending(tenant_client, goal_id)
    approve = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": pending["request_id"], "action": "approve", "approver": "e2e"},
    )
    assert approve.status_code == 200, approve.text
    final = await _terminal(tenant_client, goal_id)
    assert final["status"] == "complete", final
    raw = await collect_sse(tenant_client, goal_id, until="goal_complete", timeout=15.0)
    types = [json.loads(r).get("type") for r in raw if r.startswith("{")]
    assert "waiting_approval" in types and "approval_granted" in types, types


@pytest.mark.parametrize("strategy_id", ["goal_tree", "debate"])
async def test_high_risk_goal_fails_with_the_rejection(
    tenant_client: Any, _inline_v2: None, strategy_id: str
) -> None:
    goal_id = await _submit(tenant_client, strategy_id)
    pending = await _pending(tenant_client, goal_id)
    reject = await tenant_client.post(
        f"/goals/{goal_id}/approve",
        json={"request_id": pending["request_id"], "action": "reject", "approver": "e2e"},
    )
    assert reject.status_code == 200, reject.text
    final = await _terminal(tenant_client, goal_id)
    assert final["status"] == "failed", final
    raw = await collect_sse(tenant_client, goal_id, until="goal_failed", timeout=15.0)
    failed = [json.loads(r) for r in raw if '"goal_failed"' in r]
    assert failed and "approval_rejected" in failed[-1].get("reason", ""), failed
