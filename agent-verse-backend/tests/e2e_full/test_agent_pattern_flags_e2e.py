"""e2e_full: an agent's reasoning-pattern flags persist in Postgres and reach the goal.

CORE-04. Against the booted app (real Postgres + Redis, migrations applied):
create an agent with ``enable_cot`` / ``enable_debate`` through the API, drop
this replica's cached copy (as another replica would not have it), read the
agent back from the DB, then submit a dry-run goal bound to it and check the
flags were snapshotted onto the goal's execution context — the snapshot the
Celery worker compiles its graph from.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def flags_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"flags-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Flags", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://e2e-full",
        headers={"X-API-Key": resp.json()["api_key"]},
    ) as c:
        yield c


async def test_pattern_flags_round_trip_through_postgres_onto_the_goal(
    app: Any, flags_client: Any
) -> None:
    created = await flags_client.post(
        "/agents",
        json={"name": "deliberate", "enable_cot": True, "enable_debate": True},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    tenant_id = created.json()["tenant_id"]

    # Another replica has no cached copy: forget ours so reads hit Postgres.
    app.state.agent_store._data.pop((tenant_id, agent_id), None)

    got = await flags_client.get(f"/agents/{agent_id}")
    assert got.status_code == 200, got.text
    assert got.json()["pattern_flags"]["enable_cot"] is True
    assert got.json()["pattern_flags"]["enable_debate"] is True
    assert got.json()["enable_self_refine"] is False

    app.state.agent_store._data.pop((tenant_id, agent_id), None)
    submitted = await flags_client.post(
        "/goals", json={"goal": "Weigh the two options", "agent_id": agent_id, "dry_run": True}
    )
    assert submitted.status_code == 202, submitted.text
    goal_id = submitted.json()["goal_id"]

    ctx = app.state.goal_service._goals[goal_id].execution_context
    assert ctx["agent_pattern_flags"]["enable_cot"] is True
    assert ctx["agent_pattern_flags"]["enable_debate"] is True
