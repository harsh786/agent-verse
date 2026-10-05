"""D3 — the public A2A agent directory (owner decision 2026-10-05).

Off by default. An agent is listed only when its tenant switched the directory
on AND the agent opted in (``a2a_public``) AND it is active (not deleted or
archived). A card is minimal: agent_id, name, description, skills, endpoint —
never tools, prompts, model, connector names or other internals. The
unauthenticated ``/.well-known/agents`` routes are rate-limited per IP and
keyset-paginated, and never list another tenant's private agents. Inbound A2A
tasks may target a specific agent, which must be public and active and belong to
the authenticated (signed) caller's tenant.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent_directory
from app.api.a2a import _tasks
from app.api.a2a import router as a2a_router
from app.api.agent_directory import router as directory_router
from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from tests.api._a2a_fakes import FakeGoalService

TENANT_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka",
                         roles=("admin",))
TENANT_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb",
                         roles=("admin",))
_INTERNALS = ("SECRET-PROMPT", "gpt-internal-model", "conn-salesforce", "internal goal template")


def _app() -> FastAPI:
    app = FastAPI()
    app.state.agent_store = AgentStore()
    app.state.goal_service = FakeGoalService()
    app.state.db_session_factory = None

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        who = request.headers.get("X-Test-Tenant")
        if who:
            request.state.tenant = {"a": TENANT_A, "b": TENANT_B}[who]
        return await call_next(request)

    for router in (agents_router, tenants_router, directory_router, a2a_router):
        app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    _tasks.clear()
    agent_directory._reset_local_rate_limits()


def _agent(client: TestClient, who: str, name: str) -> str:
    resp = client.post("/agents", headers={"X-Test-Tenant": who}, json={
        "name": name, "system_prompt": "SECRET-PROMPT", "model_override": "gpt-internal-model",
        "goal_template": "internal goal template"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["agent_id"])


def _publish(client: TestClient, who: str, agent_id: str, **extra: Any) -> Any:
    body = {"a2a_public": True, "a2a_description": f"Public face of {agent_id[:6]}",
            "a2a_skills": ["summarise contracts", "answer billing questions"], **extra}
    return client.put(f"/agents/{agent_id}", headers={"X-Test-Tenant": who}, json=body)


def _directory_on(client: TestClient, who: str, enabled: bool = True) -> None:
    resp = client.put("/tenants/me/a2a-directory", headers={"X-Test-Tenant": who},
                      json={"enabled": enabled})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": enabled}


def _listed(client: TestClient) -> list[str]:
    resp = client.get("/.well-known/agents", params={"limit": 100})
    assert resp.status_code == 200, resp.text
    return [card["agent_id"] for card in resp.json()["agents"]]


# ── off by default ────────────────────────────────────────────────────────────


def test_directory_is_off_by_default() -> None:
    client = TestClient(_app())
    agent = _agent(client, "a", "Billing bot")
    assert _publish(client, "a", agent).status_code == 200
    assert client.get("/tenants/me/a2a-directory", headers={"X-Test-Tenant": "a"}).json() == {
        "enabled": False}
    assert _listed(client) == []
    assert client.get(f"/.well-known/agents/{agent}.json").status_code == 404


def test_agents_are_private_by_default() -> None:
    client = TestClient(_app())
    agent = _agent(client, "a", "Billing bot")
    rec = client.get(f"/agents/{agent}", headers={"X-Test-Tenant": "a"}).json()
    assert rec["a2a_public"] is False
    _directory_on(client, "a")
    assert _listed(client) == []


def test_only_active_public_agents_of_enabled_tenants_are_listed() -> None:
    client = TestClient(_app())
    public_a = _agent(client, "a", "Public A")
    private_a = _agent(client, "a", "Private A")
    deleted_a = _agent(client, "a", "Deleted A")
    public_b = _agent(client, "b", "Public B (tenant B directory off)")
    for who, agent in (("a", public_a), ("a", deleted_a), ("b", public_b)):
        assert _publish(client, who, agent).status_code == 200
    assert client.delete(f"/agents/{deleted_a}", headers={"X-Test-Tenant": "a"}).status_code == 204
    _directory_on(client, "a")

    assert _listed(client) == [public_a]
    for hidden in (private_a, deleted_a, public_b):
        assert client.get(f"/.well-known/agents/{hidden}.json").status_code == 404
    # Withdrawing the opt-in or the tenant switch hides it again.
    _directory_on(client, "a", enabled=False)
    assert _listed(client) == []
    _directory_on(client, "a")
    assert client.put(f"/agents/{public_a}", headers={"X-Test-Tenant": "a"},
                      json={"a2a_public": False}).status_code == 200
    assert _listed(client) == []


def test_a_card_is_minimal_and_leaks_no_internals() -> None:
    client = TestClient(_app())
    agent = _agent(client, "a", "Billing bot")
    _publish(client, "a", agent)
    _directory_on(client, "a")
    card = client.get(f"/.well-known/agents/{agent}.json")
    assert card.status_code == 200, card.text
    body = card.json()
    assert set(body) == {"agent_id", "name", "description", "skills", "endpoint"}
    assert body["name"] == "Billing bot"
    assert body["skills"] == ["summarise contracts", "answer billing questions"]
    assert body["endpoint"].endswith("/a2a/tasks")
    listing = client.get("/.well-known/agents").text
    for secret in (*_INTERNALS, "tenant-a"):
        assert secret not in card.text and secret not in listing


def test_keyset_pagination_covers_every_public_agent_once() -> None:
    client = TestClient(_app())
    agents = [_agent(client, "a", f"Agent {i}") for i in range(5)]
    for agent in agents:
        _publish(client, "a", agent)
    _directory_on(client, "a")
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/.well-known/agents", params=params).json()
        assert len(page["agents"]) <= 2
        seen += [c["agent_id"] for c in page["agents"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(agents)
    assert client.get("/.well-known/agents", params={"limit": 101}).status_code == 422


def test_the_directory_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_directory, "_RATE_LIMIT", 3)
    client = TestClient(_app())
    statuses = [client.get("/.well-known/agents").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    assert client.get("/.well-known/agents/x.json").status_code == 429


def test_agent_a2a_fields_are_validated() -> None:
    client = TestClient(_app())
    agent = _agent(client, "a", "Bot")
    assert _publish(client, "a", agent, a2a_skills=["x" * 81]).status_code == 422
    assert _publish(client, "a", agent, a2a_skills=[f"s{i}" for i in range(21)]).status_code == 422
    assert _publish(client, "a", agent, a2a_description="d" * 501).status_code == 422


def test_the_tenant_switch_needs_an_admin() -> None:
    app = _app()

    @app.middleware("http")
    async def _viewer(request: Any, call_next: Any) -> Any:
        if request.headers.get("X-Viewer"):
            request.state.tenant = TenantContext(
                tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="kv",
                roles=("viewer",))
        return await call_next(request)

    resp = TestClient(app).put("/tenants/me/a2a-directory", headers={"X-Viewer": "1"},
                               json={"enabled": True})
    assert resp.status_code == 403


# ── inbound A2A tasks routed to one agent ─────────────────────────────────────


def _task(client: TestClient, who: str, agent_id: str | None) -> Any:
    body: dict[str, Any] = {"goal": "Summarise the latest invoice"}
    if agent_id is not None:
        body["agent_id"] = agent_id
    return client.post("/a2a/tasks", headers={"X-Test-Tenant": who}, json=body)


def test_an_inbound_task_runs_on_the_targeted_public_agent() -> None:
    app = _app()
    client = TestClient(app)
    agent = _agent(client, "a", "Billing bot")
    _publish(client, "a", agent)
    _directory_on(client, "a")
    resp = _task(client, "a", agent)
    assert resp.status_code == 202, resp.text
    assert resp.json()["agent_id"] == agent
    submitted = app.state.goal_service.submitted
    assert submitted[-1]["agent_id"] == agent and submitted[-1]["tenant_ctx"] is TENANT_A


@pytest.mark.parametrize("case", ["private", "deleted", "tenant_off", "other_tenant", "unknown"])
def test_an_inbound_task_for_a_non_public_agent_is_refused(case: str) -> None:
    app = _app()
    client = TestClient(app)
    agent = _agent(client, "b" if case == "other_tenant" else "a", "Bot")
    if case != "private":
        _publish(client, "b" if case == "other_tenant" else "a", agent)
    if case == "deleted":
        client.delete(f"/agents/{agent}", headers={"X-Test-Tenant": "a"})
    if case != "tenant_off":
        _directory_on(client, "a")
        _directory_on(client, "b")
    target = "nonexistent" if case == "unknown" else agent
    resp = _task(client, "a", target)
    assert resp.status_code == 404, resp.text
    assert app.state.goal_service.submitted == []
    assert _tasks == {}


def test_an_untargeted_inbound_task_is_unchanged() -> None:
    app = _app()
    resp = _task(TestClient(app), "a", None)
    assert resp.status_code == 202
    assert app.state.goal_service.submitted[-1].get("agent_id") is None
