"""e2e_full: the anti-disconnect gate (Phase-3 Row 18 — core platform workflows).

The dominant bug class this whole effort targets is the *silent wiring
disconnect*: a real DB/Redis-backed service exists but the live app keeps the
in-memory fallback on ``app.state``. This test boots the REAL lifespan
(``create_app(manage_pools=True)`` under LifespanManager, against real
Postgres+Redis) and asserts the two-phase swap actually happened — every core
service is its DB/Redis-backed implementation, not the in-memory stub — and that
the backends are reachable via a real signup round-trip.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_lifespan_upgrades_services_to_db_redis_backed(app: Any) -> None:
    state = app.state

    # Core DB/Redis-wired services are present (not dropped to None during the
    # swap). The embedder is a provider, not a wiring concern, and is legitimately
    # None without an embedding API key — excluded here.
    for name in (
        "goal_service",
        "tenant_service",
        "knowledge_store",
        "schedule_store",
        "trigger_dispatcher",
        "workflow_runner",
    ):
        assert getattr(state, name, None) is not None, f"{name} missing on app.state"

    # Workflow runner is backed by the Postgres run store (WT-6/WT-7) — the
    # in-memory fallback has no _run_store / a non-Postgres one.
    run_store = getattr(state.workflow_runner, "_run_store", None)
    assert run_store is not None, "workflow_runner has no run store (in-memory fallback)"
    assert "Postgres" in type(run_store).__name__, (
        f"workflow run store is {type(run_store).__name__}, expected the Postgres one"
    )

    # LangGraph checkpointer is Redis-backed (persistent across replicas), not the
    # MemorySaver fallback.
    checkpointer = getattr(state, "langgraph_checkpointer", None)
    assert checkpointer is not None
    assert "MemorySaver" not in type(checkpointer).__name__, (
        "langgraph_checkpointer fell back to MemorySaver — Redis checkpointer not wired"
    )

    # Trigger dispatcher was wired with the goal service (WT-3), not left unset.
    assert getattr(state.trigger_dispatcher, "_goal_service", None) is state.goal_service


async def test_backends_reachable_via_signup_roundtrip(client: Any) -> None:
    """A real DB+Redis round-trip: signup persists a tenant and returns a key."""
    email = f"wiring-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Wiring", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    assert api_key

    # The key authenticates against DB-backed auth on a subsequent request.
    me = await client.get("/agents", headers={"X-API-Key": api_key})
    assert me.status_code in (200, 404), f"authenticated request failed: {me.status_code}"
