"""e2e_full: a goal's agent pattern is auto-selected, recorded, and retrievable.

Proves the pattern-selection seam end-to-end against the booted app
(manage_pools=True) with real Postgres + Redis: submitting a goal makes the ONE
PatternSelector choose an agent pattern, the choice (+ why) is persisted to the
goal's execution_context, and it is retrievable via
GET /goals/{id}/pattern-selection — the exact contract the frontend selection
surface consumes. An explicit strategy override is honored and reported as such.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def selection_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"pattern-select-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "PatternSelect", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


async def test_complex_goal_pattern_selection_recorded_and_retrievable(
    selection_client: Any,
) -> None:
    submit = await selection_client.post(
        "/goals",
        json={
            "goal": (
                "Design, implement and rigorously verify a fault-tolerant distributed "
                "rate limiter across all microservices, analyzing every tradeoff"
            )
        },
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]
    assert goal_id

    resp = await selection_client.get(f"/goals/{goal_id}/pattern-selection")
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    body = resp.json()

    # A real pattern was chosen, with a plain-language rationale.
    assert body["goal_id"] == goal_id
    assert body["primary_pattern"], "no primary pattern selected"
    assert body["source"] == "auto"
    assert body["reasoning_patterns"], "no reasoning patterns recorded"
    assert isinstance(body["multi_agent_patterns"], list) and body["multi_agent_patterns"]
    assert body["rationale"], "no rationale recorded"
    for entry in body["rationale"]:
        assert entry["why"], f"pattern {entry['pattern']} has no reason"

    # The override catalog is registry-driven (adding a pattern = registering it).
    catalog_ids = {p["id"] for p in body["available_patterns"]}
    assert {"react", "plan_execute", "supervisor", "debate", "consensus"} <= catalog_ids


async def test_explicit_override_is_honored_and_reported(selection_client: Any) -> None:
    submit = await selection_client.post(
        "/goals",
        json={"goal": "Summarize the onboarding guide", "strategy_override": "plan_execute"},
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    resp = await selection_client.get(f"/goals/{goal_id}/pattern-selection")
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    body = resp.json()
    assert body["source"] == "override"
    assert body["primary_pattern"] == "plan_execute"
    assert body["override"] == "plan_execute"
