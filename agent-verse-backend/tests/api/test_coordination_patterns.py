"""ORG-25: pattern runs over REST populate the read models the pattern routes serve."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_auction import router as auction_router
from app.api.coordination_camel import router as camel_router
from app.api.coordination_generative import router as generative_router
from app.api.coordination_magentic import router as magentic_router
from app.api.coordination_moa import router as moa_router
from app.api.coordination_patterns import router as patterns_router
from app.api.coordination_swarm import router as swarm_router
from app.coordination.pattern_runs.service import PatternRunService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.coordination.pattern_run_support import (
    ScriptedProvider,
    active_session,
    pattern_state,
)

_KEYS = {
    "op": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="op", roles=("operator",)
    ),
    "viewer": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="v", roles=("viewer",)
    ),
    "other": TenantContext(
        tenant_id="other", plan=PlanTier.PROFESSIONAL, api_key_id="x", roles=("operator",)
    ),
}


def _client(provider: Any | None = None) -> tuple[TestClient, str, Any]:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    for router in (
        patterns_router,
        swarm_router,
        auction_router,
        camel_router,
        generative_router,
        magentic_router,
        moa_router,
    ):
        app.include_router(router)
    state = pattern_state(provider if provider is not None else ScriptedProvider())
    for name, value in vars(state).items():
        setattr(app.state, name, value)
    app.state.pattern_run_service = PatternRunService(app.state)
    session_id = asyncio.run(active_session(state))
    return TestClient(app), session_id, app.state


def _run(client: TestClient, session_id: str, pattern: str, key: str = "op", **body: Any) -> Any:
    return client.post(
        f"/api/v1/coordination/sessions/{session_id}/patterns/{pattern}/runs",
        headers={"X-API-Key": key, "Idempotency-Key": f"{pattern}-1"},
        json={"objective": "Write a short market report", **body},
    )


def test_swarm_run_fills_topology_route() -> None:
    client, session_id, _ = _client()
    before = client.get(
        f"/api/v1/coordination/sessions/{session_id}/swarm", headers={"X-API-Key": "op"}
    ).json()
    assert before["edges"] == []
    run = _run(client, session_id, "decentralized_swarm")
    assert run.status_code == 200, run.text
    assert run.json()["phase"] == "completed"
    topology = client.get(
        f"/api/v1/coordination/sessions/{session_id}/swarm", headers={"X-API-Key": "op"}
    ).json()
    assert {node["agent_id"] for node in topology["nodes"]} == {
        "swarm-agent-1",
        "swarm-agent-2",
        "swarm-agent-3",
    }
    assert len(topology["edges"]) > 0
    foreign = client.get(
        f"/api/v1/coordination/sessions/{session_id}/swarm", headers={"X-API-Key": "other"}
    ).json()
    assert foreign["nodes"] == [] and foreign["edges"] == []


def test_auction_run_fills_allocation_route() -> None:
    client, session_id, _ = _client()
    assert _run(client, session_id, "market_auction").status_code == 200
    auction = client.get(
        f"/api/v1/coordination/sessions/{session_id}/auction", headers={"X-API-Key": "op"}
    ).json()
    assert auction["sealed_bid_count"] == 3
    assert auction["items"][0]["winner_id"] == "bidder-2"
    assert auction["items"][0]["state"] == "settled"


def test_camel_generative_and_moa_routes_show_runs() -> None:
    client, session_id, _ = _client()
    for pattern in ("camel", "generative_agents", "mixture_of_agents"):
        response = _run(client, session_id, pattern)
        assert response.status_code == 200, (pattern, response.text)
    headers = {"X-API-Key": "op"}
    base = f"/api/v1/coordination/sessions/{session_id}"
    camel = client.get(f"{base}/camel", headers=headers).json()["items"]
    generative = client.get(f"{base}/generative", headers=headers).json()["items"]
    layers = client.get(f"{base}/moa/layers", headers=headers).json()["items"]
    assert [item["phase"] for item in camel] == ["completed"]
    assert [item["phase"] for item in generative] == ["completed"]
    assert [layer["layer_index"] for layer in layers] == [0, 1]
    assert all(layer["quorum_met"] for layer in layers)


def test_magentic_human_review_route_continues_the_run() -> None:
    provider = ScriptedProvider(magentic_completes=False)
    client, session_id, _ = _client(provider)
    waiting = _run(client, session_id, "magentic").json()
    assert waiting["phase"] == "awaiting_human"
    provider.magentic_completes = True
    review = client.post(
        f"/api/v1/coordination/sessions/{session_id}/magentic/human-review",
        headers={"X-API-Key": "op"},
        json={"token": waiting["human_review"]["token"], "approved": True},
    )
    assert review.status_code == 200, review.text
    assert review.json()["run"]["phase"] == "completed"
    ledger = client.get(
        f"/api/v1/coordination/sessions/{session_id}/ledger", headers={"X-API-Key": "op"}
    ).json()
    assert ledger["reset_count"] >= 1


def test_run_route_fails_closed() -> None:
    client, session_id, state = _client()
    assert _run(client, session_id, "camel", key="viewer").status_code == 403
    assert _run(client, session_id, "nonsense").status_code == 404
    assert _run(client, "missing", "camel").status_code == 404
    assert _run(client, session_id, "camel", key="other").status_code == 404
    state.llm_provider = None
    assert _run(client, session_id, "camel").status_code == 503
