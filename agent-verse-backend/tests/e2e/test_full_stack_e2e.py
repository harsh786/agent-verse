"""Full-stack E2E tests — exercises the complete request→agent→response cycle.

Uses the real FastAPI app with FakeProvider so no external LLM calls needed.
Tests the complete vertical slice: HTTP → middleware → service → AgentGraph → SSE.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


# ── Shared test app ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_app():
    """A fully wired app instance shared across tests in this module."""
    import os
    from app.providers.fake import FakeProvider

    # Ensure A2A_TENANT_ID is set so the a2a endpoint works in tests
    os.environ.setdefault("A2A_TENANT_ID", "e2e-test-tenant")
    app = create_app()
    # Set up a fake embedder so knowledge base search works without a real provider.
    # FakeProvider generates deterministic sin-wave vectors for testing.
    if app.state.embedder is None:
        app.state.embedder = FakeProvider()
    return app


@pytest.fixture
async def client_and_key(real_app):
    """Creates a tenant, returns (AsyncClient, api_key)."""
    transport = ASGITransport(app=real_app)
    unique = uuid.uuid4().hex[:16]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/tenants/signup",
            json={"name": "E2E Corp", "email": f"e2e_{unique}@test.com"},
        )
        assert resp.status_code == 201
        api_key = resp.json()["api_key"]
        yield c, api_key


# ── Health & metrics ───────────────────────────────────────────────────────────


async def test_health_returns_healthy(client_and_key):
    c, _ = client_and_key
    resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"healthy", "degraded"}


async def test_metrics_returns_prometheus_text(client_and_key):
    c, _ = client_and_key
    resp = await c.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


# ── Tenant lifecycle ───────────────────────────────────────────────────────────


async def test_signup_returns_api_key(real_app):
    transport = ASGITransport(app=real_app)
    unique = uuid.uuid4().hex[:16]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/tenants/signup",
            json={"name": "New Co", "email": f"newco_{unique}@test.com"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "api_key" in data
        assert data["api_key"].startswith("av_")


async def test_get_me_returns_profile(client_and_key):
    c, key = client_and_key
    resp = await c.get("/tenants/me", headers={"X-API-Key": key})
    assert resp.status_code == 200
    data = resp.json()
    assert "tenant_id" in data
    assert data["name"] == "E2E Corp"


async def test_create_and_list_api_keys(client_and_key):
    c, key = client_and_key
    # Create a new key
    resp = await c.post(
        "/tenants/me/keys",
        json={"name": "Test Key", "scopes": ["goals:read"]},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 201
    new_key_id = resp.json()["key_id"]
    # List keys
    resp2 = await c.get("/tenants/me/keys", headers={"X-API-Key": key})
    assert resp2.status_code == 200
    key_ids = [k["key_id"] for k in resp2.json()]
    assert new_key_id in key_ids


async def test_revoke_api_key(client_and_key):
    c, key = client_and_key
    # Create then revoke
    resp = await c.post(
        "/tenants/me/keys",
        json={"name": "ToRevoke"},
        headers={"X-API-Key": key},
    )
    kid = resp.json()["key_id"]
    resp2 = await c.delete(f"/tenants/me/keys/{kid}", headers={"X-API-Key": key})
    assert resp2.status_code == 204


async def test_rotate_api_key(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/tenants/me/keys", json={"name": "ToRotate"}, headers={"X-API-Key": key}
    )
    kid = resp.json()["key_id"]
    resp2 = await c.post(
        f"/tenants/me/keys/{kid}/rotate",
        json={"name": "Rotated", "revoke_old": True},
        headers={"X-API-Key": key},
    )
    assert resp2.status_code == 201
    assert "new_key" in resp2.json()


# ── Goal submission & SSE ──────────────────────────────────────────────────────


async def test_submit_goal_returns_202(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/goals",
        json={"goal": "list all repos", "priority": "normal"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "goal_id" in data
    assert data["status"] in {"planning", "complete"}


async def test_dry_run_goal_completes_immediately(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/goals",
        json={"goal": "dry run test", "dry_run": True},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 202
    assert resp.json()["dry_run"] is True


async def test_get_goal_after_submit(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/goals", json={"goal": "get goal test"}, headers={"X-API-Key": key}
    )
    gid = r.json()["goal_id"]
    # Poll a few times waiting for completion
    for _ in range(5):
        resp = await c.get(f"/goals/{gid}", headers={"X-API-Key": key})
        assert resp.status_code == 200
        if resp.json()["status"] in {"complete", "failed"}:
            break
        await asyncio.sleep(0.1)


async def test_cancel_goal(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/goals", json={"goal": "cancel me"}, headers={"X-API-Key": key}
    )
    gid = r.json()["goal_id"]
    resp = await c.post(f"/goals/{gid}/cancel", headers={"X-API-Key": key})
    assert resp.status_code == 200


async def test_get_nonexistent_goal_returns_404(client_and_key):
    c, key = client_and_key
    resp = await c.get("/goals/does-not-exist", headers={"X-API-Key": key})
    assert resp.status_code == 404


# ── Connectors ─────────────────────────────────────────────────────────────────


async def test_connectors_catalog_returns_9_entries(client_and_key):
    c, key = client_and_key
    resp = await c.get("/connectors/catalog", headers={"X-API-Key": key})
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()}
    assert "github" in names
    assert "slack" in names
    assert len(resp.json()) >= 9


async def test_register_and_unregister_connector(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/connectors",
        json={
            "name": "test-mcp",
            "url": "http://localhost:9999",
            "auth_type": "bearer",
            "auth_config": {"token": "tok"},
        },
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 201
    sid = resp.json()["server_id"]
    # List
    r2 = await c.get("/connectors", headers={"X-API-Key": key})
    assert any(s.get("name") == "test-mcp" for s in r2.json())
    # Unregister
    r3 = await c.delete(f"/connectors/{sid}", headers={"X-API-Key": key})
    assert r3.status_code == 204


# ── Agents ─────────────────────────────────────────────────────────────────────


async def test_create_agent_and_list(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/agents",
        json={
            "name": "E2E Agent",
            "goal_template": "fix {issue}",
            "autonomy_mode": "bounded-autonomous",
        },
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 201
    aid = resp.json()["agent_id"]
    r2 = await c.get("/agents", headers={"X-API-Key": key})
    ids = [a["agent_id"] for a in r2.json()]
    assert aid in ids


async def test_create_agent_via_nl_command(client_and_key):
    c, key = client_and_key
    resp = await c.post(
        "/agents/create",
        json={
            "command": (
                "Create an agent that monitors GitHub issues and creates JIRA tickets"
            )
        },
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 201
    assert "agent" in resp.json()


async def test_agent_permissions_crud(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/agents",
        json={"name": "PermAgent", "goal_template": "test"},
        headers={"X-API-Key": key},
    )
    aid = r.json()["agent_id"]
    # Get permissions
    r2 = await c.get(f"/agents/{aid}/permissions", headers={"X-API-Key": key})
    assert r2.status_code == 200
    # Update permissions
    r3 = await c.put(
        f"/agents/{aid}/permissions",
        json={"permissions": {"github.push": "allow", "github.delete": "deny"}},
        headers={"X-API-Key": key},
    )
    assert r3.status_code == 200
    assert r3.json()["permissions"]["github.delete"] == "deny"


# ── Knowledge ──────────────────────────────────────────────────────────────────


async def test_create_collection_and_ingest_and_search(client_and_key):
    c, key = client_and_key
    # Create collection
    r = await c.post(
        "/knowledge/collections",
        json={"name": "E2E Docs", "embedder_type": "voyage"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    col_id = r.json()["collection_id"]
    # Ingest
    r2 = await c.post(
        "/knowledge/ingest",
        json={
            "collection_id": col_id,
            "source_type": "text",
            "content": (
                "Python is a high-level programming language known for readability"
            ),
        },
        headers={"X-API-Key": key},
    )
    assert r2.status_code == 201
    assert r2.json()["chunks_created"] >= 1
    # Search
    r3 = await c.get(
        f"/knowledge/search?q=python+programming&collection_id={col_id}",
        headers={"X-API-Key": key},
    )
    assert r3.status_code == 200
    assert isinstance(r3.json(), list)


# ── Governance ─────────────────────────────────────────────────────────────────


async def test_governance_policy_lifecycle(client_and_key):
    c, key = client_and_key
    # Create policy
    r = await c.post(
        "/governance/policies",
        json={"name": "No Deletes", "tools_pattern": "*.delete*", "action": "deny"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    pid = r.json()["policy_id"]
    # List
    r2 = await c.get("/governance/policies", headers={"X-API-Key": key})
    assert any(p["policy_id"] == pid for p in r2.json())
    # Delete
    r3 = await c.delete(f"/governance/policies/{pid}", headers={"X-API-Key": key})
    assert r3.status_code == 204


async def test_governance_budget_get_and_set(client_and_key):
    c, key = client_and_key
    r = await c.get("/governance/budget", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert "per_goal_usd" in r.json()
    r2 = await c.put(
        "/governance/budget",
        json={"per_goal_usd": 5.0, "per_tenant_daily_usd": 100.0},
        headers={"X-API-Key": key},
    )
    assert r2.status_code == 200
    assert r2.json()["per_goal_usd"] == 5.0


# ── Schedules ─────────────────────────────────────────────────────────────────


async def test_schedule_lifecycle(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/schedules",
        json={
            "name": "Daily Run",
            "trigger_type": "cron",
            "cron_expr": "0 9 * * *",
            "goal_template": "run daily",
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    sid = r.json()["schedule_id"]
    # Pause
    await c.post(f"/schedules/{sid}/pause", headers={"X-API-Key": key})
    # Resume
    await c.post(f"/schedules/{sid}/resume", headers={"X-API-Key": key})
    # Delete
    r2 = await c.delete(f"/schedules/{sid}", headers={"X-API-Key": key})
    assert r2.status_code == 204


async def test_nl_schedule_creates_schedule(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/nl/schedule",
        json={"command": "every day at 9 AM UTC"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


# ── Enterprise ─────────────────────────────────────────────────────────────────


async def test_compliance_export(client_and_key):
    c, key = client_and_key
    r = await c.get("/enterprise/compliance/export", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert "request_id" in r.json()


async def test_simulation_run(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/enterprise/simulation",
        json={
            "goal": "Fix the bug in app.py",
            "mock_tools": {"github": {"list_prs": []}},
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "completed"


async def test_red_team_run(client_and_key):
    c, key = client_and_key
    r = await c.post("/enterprise/red-team", json={}, headers={"X-API-Key": key})
    assert r.status_code == 201
    data = r.json()
    assert data["cases_run"] == 5
    assert "results" in data


async def test_marketplace_browse_and_deploy(client_and_key):
    c, key = client_and_key
    r = await c.get("/marketplace/browse", headers={"X-API-Key": key})
    assert r.status_code == 200
    templates = r.json()
    assert len(templates) >= 6
    tpl_id = templates[0]["template_id"]
    r2 = await c.post(
        f"/marketplace/{tpl_id}/deploy",
        json={"params": {"repo": "acme/app", "label": "prod-down"}},
        headers={"X-API-Key": key},
    )
    assert r2.status_code == 201
    assert "deployment_id" in r2.json()


# ── A2A ────────────────────────────────────────────────────────────────────────


async def test_agent_card_endpoint(real_app):
    transport = ASGITransport(app=real_app)
    unique = uuid.uuid4().hex[:12]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r_signup = await c.post(
            "/tenants/signup",
            json={"name": "A2A Test", "email": f"a2a_{unique}@test.com"},
        )
        api_key = r_signup.json()["api_key"]
        r = await c.get("/.well-known/agent.json", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        data = r.json()
        assert "name" in data
        assert "capabilities" in data


async def test_a2a_send_and_get_task(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/a2a/tasks",
        json={"goal": "analyze the data", "context": {}},
        headers={"X-API-Key": key},
    )
    assert r.status_code in (200, 202)
    # Use task_id returned by server (new impl generates its own UUID)
    returned_task_id = r.json().get("task_id")
    assert returned_task_id, f"Expected task_id in response, got: {r.json()}"
    r2 = await c.get(f"/a2a/tasks/{returned_task_id}", headers={"X-API-Key": key})
    assert r2.status_code == 200


# ── Collab ─────────────────────────────────────────────────────────────────────


async def test_collab_session_lifecycle(client_and_key):
    c, key = client_and_key
    r = await c.post(
        "/collab/sessions",
        json={"name": "E2E Collab", "mode": "co-write"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    sid = r.json()["session_id"]
    r2 = await c.get(f"/collab/sessions/{sid}", headers={"X-API-Key": key})
    assert r2.status_code == 200
    assert r2.json()["name"] == "E2E Collab"


# ── Tenant isolation ───────────────────────────────────────────────────────────


async def test_tenant_cannot_see_other_tenants_goals(real_app):
    """Core security: tenant A's goals are invisible to tenant B."""
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        uid_a = uuid.uuid4().hex[:12]
        uid_b = uuid.uuid4().hex[:12]
        r1 = await c.post(
            "/tenants/signup",
            json={"name": "A Corp", "email": f"isol_a_{uid_a}@test.com"},
        )
        r2 = await c.post(
            "/tenants/signup",
            json={"name": "B Corp", "email": f"isol_b_{uid_b}@test.com"},
        )
        key_a = r1.json()["api_key"]
        key_b = r2.json()["api_key"]
        # A submits a goal
        gr = await c.post(
            "/goals",
            json={"goal": "tenant A secret goal"},
            headers={"X-API-Key": key_a},
        )
        goal_id = gr.json()["goal_id"]
        # B tries to access A's goal — must get 404
        resp = await c.get(f"/goals/{goal_id}", headers={"X-API-Key": key_b})
        assert resp.status_code == 404, (
            f"Tenant B accessed Tenant A's goal! Status: {resp.status_code}"
        )


async def test_tenant_cannot_see_other_tenants_agents(real_app):
    """Tenant isolation: agents are tenant-scoped."""
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        uid_a = uuid.uuid4().hex[:12]
        uid_b = uuid.uuid4().hex[:12]
        r1 = await c.post(
            "/tenants/signup",
            json={"name": "AgA", "email": f"ag_a_{uid_a}@test.com"},
        )
        r2 = await c.post(
            "/tenants/signup",
            json={"name": "AgB", "email": f"ag_b_{uid_b}@test.com"},
        )
        key_a, key_b = r1.json()["api_key"], r2.json()["api_key"]
        # A creates an agent
        await c.post(
            "/agents",
            json={"name": "A-Agent", "goal_template": "t"},
            headers={"X-API-Key": key_a},
        )
        # B lists agents — should be empty
        resp = await c.get("/agents", headers={"X-API-Key": key_b})
        assert resp.json() == []
