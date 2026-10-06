"""a03-F055-02: grants can be delegated and their chain read over REST.

The router exposed only create/list/get/revoke; delegation happened only inside
the sub-agent spawn path, and no endpoint showed a delegation chain.
"""

from __future__ import annotations


async def _agent(c, name: str) -> str:
    r = await c.post("/agents", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return str(r.json()["agent_id"])


async def _root_grant(c, agent_id: str) -> dict:
    r = await c.post(
        "/grants",
        json={
            "grantee_agent_id": agent_id,
            "scopes": ["jira.*"],
            "ttl_seconds": 3600,
            "max_cost_usd": 5.0,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_delegate_narrows_and_the_chain_is_readable(signed_up_client) -> None:
    c = signed_up_client
    parent_agent = await _agent(c, "Supervisor")
    child_agent = await _agent(c, "Worker")
    root = await _root_grant(c, parent_agent)

    r = await c.post(
        f"/grants/{root['grant_id']}/delegate",
        json={"grantee_agent_id": child_agent, "scopes": ["jira.search"], "max_cost_usd": 2.0},
    )
    assert r.status_code == 201, r.text
    child = r.json()
    assert child["parent_grant_id"] == root["grant_id"]
    assert child["scopes"] == ["jira.search"]
    assert child["max_cost_usd"] == 2.0
    assert child["grantee_agent_id"] == child_agent
    assert child["expires_at"] <= root["expires_at"]
    assert child["grantor"].startswith(f"agent:{parent_agent}")

    # Defaults inherit the parent's scopes and remaining budget.
    r = await c.post(f"/grants/{root['grant_id']}/delegate", json={"grantee_agent_id": child_agent})
    assert r.status_code == 201, r.text
    assert r.json()["scopes"] == ["jira.*"]
    assert r.json()["max_cost_usd"] == 5.0

    chain = (await c.get(f"/grants/{child['grant_id']}/chain")).json()
    assert chain["grant"]["grant_id"] == child["grant_id"]
    assert [g["grant_id"] for g in chain["ancestors"]] == [root["grant_id"]]
    root_chain = (await c.get(f"/grants/{root['grant_id']}/chain")).json()
    assert root_chain["ancestors"] == []
    assert child["grant_id"] in {g["grant_id"] for g in root_chain["delegations"]}
    assert len(root_chain["delegations"]) == 2

    # Revoking the parent revokes the delegation too.
    assert (await c.post(f"/grants/{root['grant_id']}/revoke")).status_code == 200
    assert (await c.get(f"/grants/{child['grant_id']}")).json()["revoked"] is True


async def test_delegation_cannot_widen_the_parent(signed_up_client) -> None:
    c = signed_up_client
    parent_agent = await _agent(c, "Supervisor")
    child_agent = await _agent(c, "Worker")
    root = await _root_grant(c, parent_agent)
    gid = root["grant_id"]

    wider_scope = await c.post(
        f"/grants/{gid}/delegate",
        json={"grantee_agent_id": child_agent, "scopes": ["github.*"]},
    )
    assert wider_scope.status_code == 400
    wider_cap = await c.post(
        f"/grants/{gid}/delegate", json={"grantee_agent_id": child_agent, "max_cost_usd": 50.0}
    )
    assert wider_cap.status_code == 400
    longer = await c.post(
        f"/grants/{gid}/delegate",
        json={"grantee_agent_id": child_agent, "ttl_seconds": 7 * 24 * 3600},
    )
    assert longer.status_code == 400
    unknown_agent = await c.post(
        f"/grants/{gid}/delegate", json={"grantee_agent_id": "no-such-agent"}
    )
    assert unknown_agent.status_code == 404
    missing = await c.post("/grants/nope/delegate", json={"grantee_agent_id": child_agent})
    assert missing.status_code == 404
    assert (await c.get("/grants/nope/chain")).status_code == 404


async def test_delegation_from_a_revoked_grant_is_refused(signed_up_client) -> None:
    c = signed_up_client
    parent_agent = await _agent(c, "Supervisor")
    child_agent = await _agent(c, "Worker")
    root = await _root_grant(c, parent_agent)
    await c.post(f"/grants/{root['grant_id']}/revoke")
    r = await c.post(f"/grants/{root['grant_id']}/delegate", json={"grantee_agent_id": child_agent})
    assert r.status_code == 400
