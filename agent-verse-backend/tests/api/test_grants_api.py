"""Grantex issuance API — issue / list / revoke over HTTP."""
from __future__ import annotations


async def _agent(c) -> str:
    r = await c.post("/agents", json={"name": "Grantee"})
    assert r.status_code in (200, 201), r.text
    return str(r.json()["agent_id"])


async def test_issue_list_revoke_grant(signed_up_client) -> None:
    c = signed_up_client
    agent_id = await _agent(c)

    # issue
    r = await c.post(
        "/grants",
        json={
            "grantee_agent_id": agent_id,
            "scopes": ["jira.*", "github.read"],
            "ttl_seconds": 3600,
            "max_cost_usd": 5.0,
        },
    )
    assert r.status_code == 201, r.text
    grant = r.json()
    gid = grant["grant_id"]
    assert grant["scopes"] == ["jira.*", "github.read"]
    assert grant["revoked"] is False

    # list
    r = await c.get("/grants", params={"agent_id": agent_id})
    assert r.status_code == 200
    assert any(g["grant_id"] == gid for g in r.json()["grants"])

    # get one (spent_usd is exposed)
    r = await c.get(f"/grants/{gid}")
    assert r.status_code == 200
    assert r.json()["spent_usd"] == 0.0

    # revoke
    r = await c.post(f"/grants/{gid}/revoke")
    assert r.status_code == 200
    assert r.json()["revoked"] is True

    # revoking a missing grant → 404
    r = await c.post("/grants/does-not-exist/revoke")
    assert r.status_code == 404


async def test_issue_requires_scopes(signed_up_client) -> None:
    r = await signed_up_client.post(
        "/grants",
        json={"grantee_agent_id": "agent-1", "scopes": [], "ttl_seconds": 60},
    )
    assert r.status_code == 422  # min_length=1 on scopes


async def test_issue_rejects_an_unknown_grantee_agent(signed_up_client) -> None:
    """The grantee was never validated: grants could target non-existent agents."""
    r = await signed_up_client.post(
        "/grants",
        json={"grantee_agent_id": "no-such-agent", "scopes": ["jira.*"], "ttl_seconds": 60},
    )
    assert r.status_code == 404


async def test_issue_bounds_cost_cap_and_window(signed_up_client) -> None:
    c = signed_up_client
    agent_id = await _agent(c)
    base = {"grantee_agent_id": agent_id, "scopes": ["jira.*"], "ttl_seconds": 60}
    assert (await c.post("/grants", json={**base, "max_cost_usd": -1})).status_code == 422
    assert (await c.post("/grants", json={**base, "max_cost_usd": 1e12})).status_code == 422
    assert (await c.post("/grants", json={**base, "not_before_seconds": 60})).status_code == 422
    assert (await c.get("/grants/does-not-exist")).status_code == 404
