"""AGKEY-01/02: agent-scoped API keys authenticate and are enforced end to end.

An ``av_agent_*`` key used to be minted with 200 while no auth path accepted it
and no tool gate read its allowed/denied tools. Now:

* TenantMiddleware resolves it to a TenantContext bound to its agent, with
  ``roles=("agent",)`` and goal-only scopes;
* goal submission is bound to the key's agent and carries the restriction on
  the goal (``goals.execution_context``) for the worker;
* the executor's tool gate and the policy engine deny tools outside it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.tenancy.context import AgentKeyRestriction, PlanTier, TenantContext


def _signup(client: TestClient, email: str = "agk@example.com") -> dict[str, str]:
    r = client.post("/tenants/signup", json={"name": "AgentKey Co", "email": email})
    assert r.status_code in (200, 201), r.text
    return {"X-API-Key": r.json()["api_key"]}


def _agent(client: TestClient, headers: dict[str, str], name: str) -> str:
    r = client.post("/agents", json={"name": name}, headers=headers)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    return str(body.get("id") or body.get("agent_id"))


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_agent_key_authenticates_and_is_bound_to_its_agent(client: TestClient) -> None:
    owner = _signup(client)
    agent_a = _agent(client, owner, "Agent A")
    agent_b = _agent(client, owner, "Agent B")
    r = client.post(
        f"/agents/{agent_a}/keys",
        json={"name": "ci", "allowed_tools": ["web_search"], "denied_tools": ["shell*"]},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    raw = r.json()["raw_key"]
    assert raw.startswith("av_agent_")
    agent_hdr = {"X-API-Key": raw}

    # Authenticates: a goal for its own agent is accepted (agent_id defaulted).
    r = client.post("/goals", json={"goal": "summarise news", "dry_run": True}, headers=agent_hdr)
    assert r.status_code in (200, 202), r.text
    goal_id = r.json()["goal_id"]
    g = client.get(f"/goals/{goal_id}", headers=owner).json()
    assert g.get("agent_id") == agent_a

    # Bound: another agent's goal is refused.
    r = client.post(
        "/goals",
        json={"goal": "x", "dry_run": True, "agent_id": agent_b},
        headers=agent_hdr,
    )
    assert r.status_code == 403, r.text

    # Scoped: an agent key cannot administer the tenant or mint more keys.
    assert client.get("/agents", headers=agent_hdr).status_code == 403
    assert (
        client.post(f"/agents/{agent_a}/keys", json={"name": "x"}, headers=agent_hdr).status_code
        == 403
    )

    # Revoked: the key stops authenticating at once.
    key_id = client.get(f"/agents/{agent_a}/keys", headers=owner).json()["keys"][0]["key_id"]
    assert client.delete(f"/agents/{agent_a}/keys/{key_id}", headers=owner).status_code == 200
    r = client.post("/goals", json={"goal": "again", "dry_run": True}, headers=agent_hdr)
    assert r.status_code == 401


def test_unknown_agent_key_is_401(client: TestClient) -> None:
    r = client.get("/goals", headers={"X-API-Key": "av_agent_deadbeef_notarealkey"})
    assert r.status_code == 401


def test_restriction_tool_denial_rules() -> None:
    r = AgentKeyRestriction(
        key_id="k", agent_id="a", allowed_tools=("web_*",), denied_tools=("web_delete",)
    )
    assert r.tool_denial("web_search") is None
    assert r.tool_denial("web_delete") is not None
    assert r.tool_denial("shell_exec") is not None
    assert AgentKeyRestriction(key_id="k", agent_id="a").tool_denial("anything") is None
    assert AgentKeyRestriction(key_id="k", agent_id="a", allowed_tools=()).tool_denial("x")


def test_restriction_connector_rule_uses_governance_names() -> None:
    from app.mcp.tool_naming import governed_tool_name

    r = AgentKeyRestriction(key_id="k", agent_id="a", allowed_connectors=("conn-1",))
    assert r.tool_denial(governed_tool_name("search", server_id="conn-1")) is None
    assert r.tool_denial(governed_tool_name("search", server_id="conn-2")) is not None
    # A tool that carries no connection at all is outside every connector.
    assert r.tool_denial("search") is not None


def test_restriction_round_trip_and_malformed_fails_closed() -> None:
    r = AgentKeyRestriction(key_id="k", agent_id="a", allowed_tools=("t",))
    assert AgentKeyRestriction.from_dict(r.to_dict()) == r
    bad = AgentKeyRestriction.from_dict({"agent_id": "a"})
    assert bad.deny_all and bad.tool_denial("t") is not None


def _ctx(restriction: AgentKeyRestriction | None) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k", agent_key=restriction)


def test_policy_engine_denies_tools_outside_the_agent_key() -> None:
    from app.governance.policies import PolicyEngine, PolicyResult

    engine = PolicyEngine()
    ctx = _ctx(AgentKeyRestriction(key_id="k", agent_id="a", allowed_tools=("web_search",)))
    assert engine.evaluate("web_search", tenant_ctx=ctx) == PolicyResult.ALLOW
    assert engine.evaluate("shell_exec", tenant_ctx=ctx) == PolicyResult.DENY


async def test_executor_tool_gate_denies_without_a_policy_engine() -> None:
    """The dispatch gate enforces the key even when no policy engine is wired."""
    from app.agent.nodes.executor_mixin import ExecutorMixin

    gate = SimpleNamespace(_policy_engine=None, _hitl_gateway=None)
    ctx = _ctx(AgentKeyRestriction(key_id="k", agent_id="a", denied_tools=("rm_*",)))
    denial = await ExecutorMixin._tool_policy_gate(
        gate,  # type: ignore[arg-type]
        tool_name="rm_rf",
        step="clean",
        state=SimpleNamespace(),  # type: ignore[arg-type]
        tenant_ctx=ctx,
    )
    assert denial is not None and "agent key" in denial
    allowed = await ExecutorMixin._tool_policy_gate(
        gate,  # type: ignore[arg-type]
        tool_name="ls",
        step="list",
        state=SimpleNamespace(),  # type: ignore[arg-type]
        tenant_ctx=ctx,
    )
    assert allowed is None


def test_goal_binding_forces_agent_and_records_restriction() -> None:
    from app.auth.agent_credentials import AGENT_KEY_CONTEXT_KEY, bind_goal_to_agent_key
    from app.core.errors import AuthorizationError

    r = AgentKeyRestriction(key_id="k", agent_id="a1", allowed_tools=("t",))
    agent_id, ctx = bind_goal_to_agent_key(_ctx(r), None, {"x": 1})
    assert agent_id == "a1"
    assert ctx is not None and ctx["x"] == 1 and ctx[AGENT_KEY_CONTEXT_KEY] == r.to_dict()
    with pytest.raises(AuthorizationError):
        bind_goal_to_agent_key(_ctx(r), "other", None)
    # A plain key is untouched.
    assert bind_goal_to_agent_key(_ctx(None), "z", None) == ("z", None)


def test_worker_context_carries_or_fails_closed() -> None:
    from app.auth.agent_credentials import AGENT_KEY_CONTEXT_KEY, apply_goal_agent_key

    base = _ctx(None)
    r = AgentKeyRestriction(key_id="k", agent_id="a1", denied_tools=("x",))
    out = apply_goal_agent_key(base, {AGENT_KEY_CONTEXT_KEY: r.to_dict()}, unreadable=False)
    assert out.agent_key == r
    assert apply_goal_agent_key(base, {}, unreadable=False).agent_key is None
    # Unreadable context: whoever submitted it is unknown, so no tool may run.
    failed: Any = apply_goal_agent_key(base, {}, unreadable=True).agent_key
    assert failed is not None and failed.deny_all
