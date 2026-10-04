"""e2e_full: POST /goals without agent_id routes to the tenant's DB agent (RV-02).

create_app built the AgentRouter over the in-memory AgentStore and the lifespan
swapped only ``app.state.agent_store`` to the DB-backed store, so the router
scored an empty cache and every replica answered ``no_agents``. Against the
booted app (real lifespan, Postgres + Redis; run with E2E_LEAST_PRIVILEGE=1 to
serve as the NOBYPASSRLS app role): create an agent, drop this replica's cached
copy (another replica never had it), and route a goal with no agent_id.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true E2E_LEAST_PRIVILEGE=1 \\
        uv run pytest tests/e2e_full/test_agent_auto_routing_e2e.py -q -m e2e_full --no-cov
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

GOAL = "invoice reconciler: reconcile vendor invoices"


async def test_goal_without_agent_id_routes_to_the_tenants_db_agent(
    app: Any, tenant_client: Any
) -> None:
    state = app.state
    assert state.agent_router._agent_store is state.agent_store, (
        "agent_router still holds the pre-lifespan in-memory AgentStore"
    )

    created = await tenant_client.post(
        "/agents",
        json={"name": "Invoice reconciler", "goal_template": "reconcile vendor invoices"},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    tenant_id = created.json()["tenant_id"]
    # Another replica has no cached copy: routing must read Postgres.
    state.agent_store._data.pop((tenant_id, agent_id), None)

    preview = await tenant_client.get("/goals/route", params={"goal": GOAL})
    assert preview.status_code == 200, preview.text
    assert preview.json()["agent_id"] == agent_id, preview.json()

    state.agent_store._data.pop((tenant_id, agent_id), None)
    submitted = await tenant_client.post("/goals", json={"goal": GOAL, "dry_run": True})
    assert submitted.status_code == 202, submitted.text
    body = submitted.json()
    assert body["agent_id"] == agent_id, body
    ctx = state.goal_service._goals[body["goal_id"]].execution_context
    assert ctx["routing_decision"]["agent_id"] == agent_id
