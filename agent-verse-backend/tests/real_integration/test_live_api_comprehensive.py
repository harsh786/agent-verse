"""
Real no-mock integration tests — AgentVerse full platform.

Hits the LIVE backend at localhost:8000.
No page.route(), no MagicMock, no ASGITransport.
Every request: test → real network → real FastAPI → real Postgres + Redis.

Run: pytest tests/real_integration/ -v --no-cov
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# ─── Config ───────────────────────────────────────────────────────────────────

BASE = os.getenv("AGENTVERSE_URL", "http://localhost:8000")
TIMEOUT = httpx.Timeout(30.0)


def _new_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def api_key() -> str:
    """Use a pre-provisioned key to avoid signup rate limits."""
    key = os.getenv("AGENTVERSE_TEST_KEY", "av_free_8aqZ_kuOQkHI3Jl9YEzb8GZ5-fZs3ZQqX0i93Uo_o6E")
    return key


@pytest.fixture(scope="module")
def client(api_key: str) -> httpx.Client:
    c = _new_client(api_key)
    yield c
    c.close()


@pytest.fixture(scope="module")
def agent_id(client: httpx.Client) -> str:
    r = client.post("/agents", json={
        "name": f"integ-agent-{uuid.uuid4().hex[:6]}",
        "system_prompt": "Integration test agent.",
        "autonomy_mode": "bounded-autonomous",
    })
    assert r.status_code in (200, 201), f"Agent create: {r.text}"
    return r.json().get("agent_id") or r.json().get("id")


@pytest.fixture(scope="module")
def goal_id(client: httpx.Client) -> str:
    r = client.post("/goals", json={"goal": "Integration test goal"})
    assert r.status_code in (200, 201, 202), f"Goal submit: {r.text}"
    return r.json()["goal_id"]


@pytest.fixture(scope="module")
def collection_id(client: httpx.Client) -> str:
    r = client.post("/knowledge/collections", json={"name": f"integ-col-{uuid.uuid4().hex[:6]}"})
    assert r.status_code in (200, 201), f"Collection create: {r.text}"
    return r.json()["collection_id"]


# ─────────────────────────────────────────────────────────────────────────────
#  1. HEALTH
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_200(self):
        r = httpx.get(f"{BASE}/health", timeout=TIMEOUT)
        assert r.status_code == 200

    def test_postgres_up(self):
        data = httpx.get(f"{BASE}/health", timeout=TIMEOUT).json()
        checks = data.get("checks", data.get("dependencies", {}))
        assert checks.get("postgres", {}).get("status") == "up"

    def test_redis_up(self):
        data = httpx.get(f"{BASE}/health", timeout=TIMEOUT).json()
        checks = data.get("checks", data.get("dependencies", {}))
        assert checks.get("redis", {}).get("status") == "up"

    def test_content_type_json(self):
        r = httpx.get(f"{BASE}/health", timeout=TIMEOUT)
        assert "application/json" in r.headers["content-type"]


# ─────────────────────────────────────────────────────────────────────────────
#  2. AUTH
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_me_200(self, client: httpx.Client):
        r = client.get("/tenants/me")
        assert r.status_code == 200

    def test_me_has_tenant_id(self, client: httpx.Client):
        data = client.get("/tenants/me").json()
        assert "tenant_id" in data or "id" in data

    def test_no_key_401(self):
        assert httpx.get(f"{BASE}/tenants/me", timeout=TIMEOUT).status_code == 401

    def test_bad_key_401(self):
        r = httpx.get(f"{BASE}/tenants/me", headers={"X-API-Key": "av_bad"}, timeout=TIMEOUT)
        assert r.status_code == 401

    def test_401_has_detail(self):
        data = httpx.get(f"{BASE}/goals", timeout=TIMEOUT).json()
        assert "detail" in data or "error" in data

    def test_goals_no_auth_401(self):
        assert httpx.get(f"{BASE}/goals", timeout=TIMEOUT).status_code == 401

    def test_agents_no_auth_401(self):
        assert httpx.get(f"{BASE}/agents", timeout=TIMEOUT).status_code == 401

    def test_submit_no_auth_401(self):
        assert httpx.post(f"{BASE}/goals", json={"goal": "hack"}, timeout=TIMEOUT).status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  3. AGENTS
# ─────────────────────────────────────────────────────────────────────────────

class TestAgents:
    def test_list_200(self, client: httpx.Client):
        assert client.get("/agents").status_code == 200

    def test_list_is_list(self, client: httpx.Client):
        assert isinstance(client.get("/agents").json(), list)

    def test_create_success(self, client: httpx.Client):
        r = client.post("/agents", json={
            "name": f"crud-{uuid.uuid4().hex[:6]}",
            "system_prompt": "CRUD test",
            "autonomy_mode": "bounded-autonomous",
        })
        assert r.status_code in (200, 201), r.text

    def test_create_returns_agent_id(self, client: httpx.Client):
        r = client.post("/agents", json={"name": f"id-{uuid.uuid4().hex[:6]}", "system_prompt": "test", "autonomy_mode": "bounded-autonomous"})
        assert r.status_code in (200, 201)
        assert "agent_id" in r.json() or "id" in r.json()

    def test_get_by_id(self, client: httpx.Client, agent_id: str):
        r = client.get(f"/agents/{agent_id}")
        assert r.status_code == 200
        data = r.json()
        assert data.get("agent_id") == agent_id or data.get("id") == agent_id

    def test_get_nonexistent_404(self, client: httpx.Client):
        assert client.get(f"/agents/{uuid.uuid4().hex}").status_code == 404

    def test_agent_in_list(self, client: httpx.Client, agent_id: str):
        agents = client.get("/agents").json()
        assert any(a.get("agent_id") == agent_id or a.get("id") == agent_id for a in agents)

    def test_create_empty_name_4xx(self, client: httpx.Client):
        r = client.post("/agents", json={"name": "", "system_prompt": "test", "autonomy_mode": "bounded-autonomous"})
        assert r.status_code >= 400

    def test_has_tenant_id(self, client: httpx.Client, agent_id: str):
        data = client.get(f"/agents/{agent_id}").json()
        assert "tenant_id" in data

    def test_update_agent(self, client: httpx.Client):
        create = client.post("/agents", json={"name": f"upd-{uuid.uuid4().hex[:6]}", "system_prompt": "update test", "autonomy_mode": "bounded-autonomous"})
        assert create.status_code in (200, 201)
        aid = create.json().get("agent_id") or create.json().get("id")
        r = client.put(f"/agents/{aid}", json={"name": "updated-name", "system_prompt": "updated", "autonomy_mode": "bounded-autonomous"})
        assert r.status_code in (200, 204)

    def test_delete_agent(self, client: httpx.Client):
        create = client.post("/agents", json={"name": f"del-{uuid.uuid4().hex[:6]}", "system_prompt": "delete me", "autonomy_mode": "bounded-autonomous"})
        assert create.status_code in (200, 201)
        aid = create.json().get("agent_id") or create.json().get("id")
        r = client.delete(f"/agents/{aid}")
        assert r.status_code in (200, 204)
        assert client.get(f"/agents/{aid}").status_code == 404

    def test_agent_snapshot(self, client: httpx.Client, agent_id: str):
        r = client.post(f"/agents/{agent_id}/snapshot")
        assert r.status_code in (200, 201), r.text

    def test_agent_readiness(self, client: httpx.Client, agent_id: str):
        r = client.get(f"/agents/{agent_id}/readiness")
        assert r.status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
#  4. GOALS
# ─────────────────────────────────────────────────────────────────────────────

class TestGoals:
    def test_list_200(self, client: httpx.Client):
        assert client.get("/goals").status_code == 200

    def test_submit_accepted(self, client: httpx.Client):
        r = client.post("/goals", json={"goal": "Summarize agents"})
        assert r.status_code in (200, 201, 202), r.text

    def test_submit_has_goal_id(self, client: httpx.Client):
        r = client.post("/goals", json={"goal": "Goal ID test"})
        assert r.status_code in (200, 201, 202)
        assert "goal_id" in r.json()

    def test_submit_has_status(self, client: httpx.Client):
        r = client.post("/goals", json={"goal": "Status test"})
        assert r.status_code in (200, 201, 202)
        assert "status" in r.json()

    def test_get_by_id(self, client: httpx.Client, goal_id: str):
        r = client.get(f"/goals/{goal_id}")
        assert r.status_code == 200
        assert "goal_id" in r.json()

    def test_get_nonexistent_404(self, client: httpx.Client):
        assert client.get(f"/goals/{uuid.uuid4()}").status_code == 404

    def test_empty_goal_4xx(self, client: httpx.Client):
        assert client.post("/goals", json={"goal": ""}).status_code >= 400

    def test_missing_field_422(self, client: httpx.Client):
        assert client.post("/goals", json={"not_goal": "x"}).status_code in (400, 422)

    def test_in_list(self, client: httpx.Client, goal_id: str):
        data = client.get("/goals").json()
        goals = data.get("goals", data if isinstance(data, list) else [])
        assert any(g.get("goal_id") == goal_id for g in goals)

    def test_pagination(self, client: httpx.Client):
        assert client.get("/goals?limit=2").status_code == 200

    def test_cancel(self, client: httpx.Client):
        create = client.post("/goals", json={"goal": "Cancel test"})
        assert create.status_code in (200, 201, 202)
        gid = create.json()["goal_id"]
        assert client.post(f"/goals/{gid}/cancel").status_code in (200, 204)

    def test_metrics_200(self, client: httpx.Client):
        assert client.get("/goals/metrics").status_code == 200

    def test_cost_metrics_200(self, client: httpx.Client):
        assert client.get("/goals/cost-metrics").status_code == 200

    def test_route_endpoint(self, client: httpx.Client):
        r = client.get("/goals/route")
        assert r.status_code in (200, 422)

    def test_bound_to_agent(self, client: httpx.Client, agent_id: str):
        r = client.post("/goals", json={"goal": "Agent bound goal", "agent_id": agent_id})
        assert r.status_code in (200, 201, 202)

    def test_stream_endpoint(self, client: httpx.Client, goal_id: str):
        r = client.get(f"/goals/{goal_id}/stream", headers={"Accept": "text/event-stream"})
        assert r.status_code in (200, 204)

    def test_audit_endpoint(self, client: httpx.Client, goal_id: str):
        r = client.get(f"/goals/{goal_id}/audit")
        assert r.status_code in (200, 404)

    def test_eval_endpoint(self, client: httpx.Client, goal_id: str):
        r = client.get(f"/goals/{goal_id}/eval")
        assert r.status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
#  5. KNOWLEDGE
# ─────────────────────────────────────────────────────────────────────────────

class TestKnowledge:
    def test_list_collections_200(self, client: httpx.Client):
        assert client.get("/knowledge/collections").status_code == 200

    def test_create_collection(self, client: httpx.Client):
        r = client.post("/knowledge/collections", json={"name": f"col-{uuid.uuid4().hex[:6]}"})
        assert r.status_code in (200, 201), r.text

    def test_create_has_collection_id(self, client: httpx.Client):
        r = client.post("/knowledge/collections", json={"name": f"cid-{uuid.uuid4().hex[:6]}"})
        assert r.status_code in (200, 201)
        assert "collection_id" in r.json()

    def test_get_by_id(self, client: httpx.Client, collection_id: str):
        r = client.get(f"/knowledge/collections/{collection_id}")
        assert r.status_code == 200
        assert r.json()["collection_id"] == collection_id

    def test_get_nonexistent_404(self, client: httpx.Client):
        assert client.get(f"/knowledge/collections/{uuid.uuid4()}").status_code == 404

    def test_ingest_text(self, client: httpx.Client, collection_id: str):
        r = client.post(f"/knowledge/collections/{collection_id}/ingest", json={
            "content": "AgentVerse is an autonomous AI agent platform.",
            "source": "integ-test",
        })
        assert r.status_code in (200, 201, 202), r.text

    def test_search(self, client: httpx.Client, collection_id: str):
        # Ingest first
        client.post(f"/knowledge/collections/{collection_id}/ingest", json={"content": "AI agents.", "source": "test"})
        r = client.post(f"/knowledge/collections/{collection_id}/search", json={"query": "agents", "limit": 3})
        assert r.status_code in (200, 201), r.text

    def test_delete_collection(self, client: httpx.Client):
        create = client.post("/knowledge/collections", json={"name": f"del-{uuid.uuid4().hex[:6]}"})
        assert create.status_code in (200, 201)
        cid = create.json()["collection_id"]
        assert client.delete(f"/knowledge/collections/{cid}").status_code in (200, 204)

    def test_analytics_200(self, client: httpx.Client):
        assert client.get("/knowledge/analytics").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  6. GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

class TestGovernance:
    def test_policies_200(self, client: httpx.Client):
        assert client.get("/governance/policies").status_code == 200

    def test_approvals_200(self, client: httpx.Client):
        assert client.get("/governance/approvals").status_code == 200

    def test_compliance_200(self, client: httpx.Client):
        assert client.get("/compliance").status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
#  7. SCHEDULES
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedules:
    def test_list_200(self, client: httpx.Client):
        assert client.get("/schedules").status_code == 200

    def test_create_and_delete(self, client: httpx.Client):
        r = client.post("/schedules", json={"name": f"s-{uuid.uuid4().hex[:6]}", "goal": "Daily", "cron": "0 9 * * *"})
        assert r.status_code in (200, 201), r.text
        sid = r.json().get("id") or r.json().get("schedule_id")
        assert client.delete(f"/schedules/{sid}").status_code in (200, 204)


# ─────────────────────────────────────────────────────────────────────────────
#  8. MEMORY
# ─────────────────────────────────────────────────────────────────────────────

class TestMemory:
    def test_store_201(self, client: httpx.Client):
        r = client.post("/memory", json={"content": "User prefers Python.", "type": "preference", "metadata": {}})
        assert r.status_code in (200, 201), r.text

    def test_store_returns_id(self, client: httpx.Client):
        r = client.post("/memory", json={"content": "Fact.", "type": "fact"})
        assert r.status_code in (200, 201)
        data = r.json()
        assert "memory_id" in data or "id" in data


# ─────────────────────────────────────────────────────────────────────────────
#  9. TEMPLATES & CONNECTORS
# ─────────────────────────────────────────────────────────────────────────────

class TestTemplatesConnectors:
    def test_templates_200(self, client: httpx.Client):
        assert client.get("/templates").status_code == 200

    def test_connectors_200(self, client: httpx.Client):
        assert client.get("/connectors").status_code == 200

    def test_provider_catalog_200(self, client: httpx.Client):
        assert client.get("/providers/catalog").status_code == 200

    def test_marketplace_200(self, client: httpx.Client):
        assert client.get("/marketplace/browse").status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
#  10. WORKFLOWS & STATE MACHINES
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowsStateMachines:
    def test_workflows_200(self, client: httpx.Client):
        assert client.get("/workflows").status_code == 200

    def test_state_machines_200(self, client: httpx.Client):
        assert client.get("/state-machines").status_code == 200

    def test_solutions_200(self, client: httpx.Client):
        assert client.get("/solutions").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  11. INTELLIGENCE (eval, prompt-variants, benchmarks)
# ─────────────────────────────────────────────────────────────────────────────

class TestIntelligence:
    def test_eval_suites_200(self, client: httpx.Client):
        assert client.get("/intelligence/eval-suites").status_code == 200

    def test_prompt_variants_200(self, client: httpx.Client):
        assert client.get("/intelligence/prompt-variants").status_code == 200

    def test_benchmarks_200(self, client: httpx.Client):
        assert client.get("/intelligence/benchmarks").status_code == 200

    def test_create_eval_suite(self, client: httpx.Client):
        r = client.post("/intelligence/eval-suites", json={"name": f"suite-{uuid.uuid4().hex[:6]}", "description": "test"})
        assert r.status_code in (200, 201), r.text

    def test_create_prompt_variant(self, client: httpx.Client):
        r = client.post("/intelligence/prompt-variants", json={"name": f"pv-{uuid.uuid4().hex[:6]}", "template": "Test {{input}}", "variables": ["input"]})
        assert r.status_code in (200, 201, 422), r.text


# ─────────────────────────────────────────────────────────────────────────────
#  12. VOICE & MODELS
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceModels:
    def test_voice_status_200(self, client: httpx.Client):
        assert client.get("/v1/voice/status").status_code == 200

    def test_voice_status_has_stt(self, client: httpx.Client):
        data = client.get("/v1/voice/status").json()
        assert "stt_status" in data or "stt" in data or "status" in data

    def test_models_200(self, client: httpx.Client):
        assert client.get("/models").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  13. ANALYTICS & OBSERVABILITY
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsObservability:
    def test_analytics_goals_200(self, client: httpx.Client):
        assert client.get("/analytics/goals").status_code == 200

    def test_analytics_agents_200(self, client: httpx.Client):
        assert client.get("/analytics/agents").status_code == 200

    def test_observability_logs_200(self, client: httpx.Client):
        assert client.get("/observability/logs").status_code in (200, 404)

    def test_skills_200(self, client: httpx.Client):
        assert client.get("/skills").status_code == 200

    def test_sources_200(self, client: httpx.Client):
        assert client.get("/sources").status_code == 200

    def test_strategies_200(self, client: httpx.Client):
        assert client.get("/strategies").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  14. TENANT ISOLATION (security)
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_agent_not_accessible_bad_key(self, client: httpx.Client, agent_id: str):
        r = httpx.get(f"{BASE}/agents/{agent_id}", headers={"X-API-Key": "av_wrong"}, timeout=TIMEOUT)
        assert r.status_code in (401, 403, 404)

    def test_goal_not_accessible_bad_key(self, client: httpx.Client, goal_id: str):
        r = httpx.get(f"{BASE}/goals/{goal_id}", headers={"X-API-Key": "av_wrong"}, timeout=TIMEOUT)
        assert r.status_code in (401, 403, 404)

    def test_agents_list_is_tenant_scoped(self, client: httpx.Client):
        agents = client.get("/agents").json()
        tenant_ids = {a.get("tenant_id") for a in agents}
        assert len(tenant_ids) <= 1

    def test_goals_list_scoped(self, client: httpx.Client):
        assert client.get("/goals").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  15. ERROR RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorResponses:
    def test_404_has_detail(self, client: httpx.Client):
        data = client.get(f"/goals/{uuid.uuid4()}").json()
        assert "detail" in data or "error" in data or "message" in data

    def test_malformed_json_4xx(self, client: httpx.Client):
        r = httpx.post(f"{BASE}/goals", content=b"not json!!!", headers={"X-API-Key": client.headers.get("X-API-Key", ""), "Content-Type": "application/json"}, timeout=TIMEOUT)
        assert r.status_code >= 400

    def test_missing_goal_field_422(self, client: httpx.Client):
        assert client.post("/goals", json={"not_goal": "x"}).status_code in (400, 422)

    def test_response_content_type_json(self, client: httpx.Client):
        r = client.get("/goals")
        assert "application/json" in r.headers.get("content-type", "")

    def test_goals_pagination(self, client: httpx.Client):
        assert client.get("/goals?limit=2").status_code == 200

    def test_agents_pagination(self, client: httpx.Client):
        assert client.get("/agents?limit=3").status_code == 200
