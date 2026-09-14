"""Grantex issuance API — issue / list / revoke over HTTP."""
from __future__ import annotations


async def test_issue_list_revoke_grant(signed_up_client) -> None:
    c = signed_up_client

    # issue
    r = await c.post(
        "/grants",
        json={
            "grantee_agent_id": "agent-1",
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
    r = await c.get("/grants", params={"agent_id": "agent-1"})
    assert r.status_code == 200
    assert any(g["grant_id"] == gid for g in r.json()["grants"])

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
