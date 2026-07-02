"""Comprehensive tests for /agents API endpoints — targets 19% → 55%+ coverage."""

from __future__ import annotations

from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore, router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-agents-comp", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_agents_comp"


def _make_app(
    agent_store: AgentStore | None = None,
    meta_agent: Any | None = None,
    mcp_registry: Any | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = agent_store or AgentStore()
    app.state.meta_agent = meta_agent or AsyncMock()
    if mcp_registry is not None:
        app.state.mcp_registry = mcp_registry
    return app


def _sample_agent(agent_id: str = "agent-1") -> dict:
    return {
        "agent_id": agent_id,
        "tenant_id": _CTX.tenant_id,
        "name": "test-agent",
        "goal_template": "Solve {task}",
        "autonomy_mode": "supervised",
        "connector_ids": ["github"],
        "trigger_config": {},
        "allowed_collection_ids": [],
        "permissions": {},
        "eval_suite_id": None,
        "policy_ids": [],
        "system_prompt": "",
        "model_override": "",
        "max_iterations": 15,
        "timeout_seconds": 300,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_agents_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents")
    assert resp.status_code == 401


def test_create_agent_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/agents", json={"name": "test"})
    assert resp.status_code == 401


def test_get_agent_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/agent-1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

def test_list_agents_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_agents_after_create() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    client.post(
        "/agents",
        json={"name": "my-agent", "goal_template": "Do {task}", "autonomy_mode": "supervised"},
        headers={"X-API-Key": _VALID_KEY},
    )
    resp = client.get("/agents", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert agents[0]["name"] == "my-agent"


# ---------------------------------------------------------------------------
# create_agent
# ---------------------------------------------------------------------------

def test_create_agent_returns_201() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={
            "name": "pr-reviewer",
            "goal_template": "Review PR {pr_url}",
            "autonomy_mode": "supervised",
            "connector_ids": ["github"],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "pr-reviewer"
    assert body["autonomy_mode"] == "supervised"
    assert "agent_id" in body


def test_create_agent_fully_autonomous_without_eval_suite_fails() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={"name": "risky-agent", "autonomy_mode": "fully-autonomous"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_agent_fully_autonomous_with_eval_suite_succeeds() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={
            "name": "safe-autonomous",
            "autonomy_mode": "fully-autonomous",
            "eval_suite_id": "suite-001",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["autonomy_mode"] == "fully-autonomous"


def test_create_agent_legal_context_requires_bar_number() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={"name": "legal-agent", "domain_context": "legal"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_agent_legal_context_with_bar_number() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={
            "name": "legal-agent",
            "domain_context": "legal",
            "domain_metadata": {"bar_number": "CA12345"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------

def test_get_agent_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    # Create first
    cr = client.post(
        "/agents",
        json={"name": "my-agent", "goal_template": "Do {t}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == agent_id


def test_get_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# update_agent (PUT)
# ---------------------------------------------------------------------------

def test_update_agent_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "original", "goal_template": "Original {t}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.put(
        f"/agents/{agent_id}",
        json={"name": "updated"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated"


def test_update_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/agents/nonexistent",
        json={"name": "new-name"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_update_agent_upgrade_to_fully_autonomous_without_eval_suite_fails() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "agent", "autonomy_mode": "supervised"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.put(
        f"/agents/{agent_id}",
        json={"autonomy_mode": "fully-autonomous"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# delete_agent
# ---------------------------------------------------------------------------

def test_delete_agent_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "to-delete"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.delete(f"/agents/{agent_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_delete_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/agents/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------

def test_get_permissions_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "perm-agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/permissions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert "permissions" in resp.json()


def test_get_permissions_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent/permissions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_update_permissions_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "perm-agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.put(
        f"/agents/{agent_id}/permissions",
        json={"permissions": {"github.read": "allow", "github.write": "deny"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"


def test_update_permissions_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/agents/nonexistent/permissions",
        json={"permissions": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# knowledge binding
# ---------------------------------------------------------------------------

def test_update_knowledge_binding_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "kb-agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.put(
        f"/agents/{agent_id}/knowledge",
        json={"collection_ids": ["col-1", "col-2"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed_collection_ids"] == ["col-1", "col-2"]


def test_update_knowledge_binding_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/agents/nonexistent/knowledge",
        json={"collection_ids": []},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_assign_knowledge_collection() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "kb-agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.post(
        f"/agents/{agent_id}/knowledge/col-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204


def test_remove_knowledge_collection() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "kb-agent", "allowed_collection_ids": ["col-1"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.delete(
        f"/agents/{agent_id}/knowledge/col-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# snapshot / rollback
# ---------------------------------------------------------------------------

def test_list_agent_versions_empty() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "versioned"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/versions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_snapshot_and_list_versions() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "versioned"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    # Take snapshot
    snap_resp = client.post(f"/agents/{agent_id}/snapshot", headers={"X-API-Key": _VALID_KEY})
    assert snap_resp.status_code == 200
    assert snap_resp.json()["version"] == 1
    # List versions
    resp = client.get(f"/agents/{agent_id}/versions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_snapshot_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/agents/nonexistent/snapshot", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_rollback_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "rollback-test"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    snap_resp = client.post(f"/agents/{agent_id}/snapshot", headers={"X-API-Key": _VALID_KEY})
    snapshot_id = snap_resp.json()["snapshot_id"]
    resp = client.post(
        f"/agents/{agent_id}/rollback/{snapshot_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rolled_back"


def test_rollback_snapshot_not_found() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.post(
        f"/agents/{agent_id}/rollback/nonexistent-snapshot",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def test_export_agent_openai_format() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "export-agent", "system_prompt": "You are helpful."},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(
        f"/agents/{agent_id}/export",
        params={"format": "openai"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "assistant"
    assert "model" in body


def test_export_agent_anthropic_format() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "export-agent-2"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(
        f"/agents/{agent_id}/export",
        params={"format": "anthropic"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "system" in body
    assert "model" in body


def test_export_agent_unknown_format() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.get(
        f"/agents/{agent_id}/export",
        params={"format": "unknown"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 400


def test_export_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/agents/nonexistent/export",
        params={"format": "openai"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

def test_clone_agent_success() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "original-agent", "goal_template": "Do {t}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.post(
        f"/agents/{agent_id}/clone",
        json={"name": "cloned-agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "cloned-agent"
    assert body["cloned_from"] == agent_id


def test_clone_agent_default_name() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "original"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.post(
        f"/agents/{agent_id}/clone",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert "(copy)" in resp.json()["name"]


def test_clone_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents/nonexistent/clone",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# credentials (no agent_identity_service configured)
# ---------------------------------------------------------------------------

def test_list_credentials_no_service() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "cred-agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/credentials", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_issue_credential_no_service() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "cred-agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.post(
        f"/agents/{agent_id}/credentials",
        json={"scopes": ["read"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_revoke_credential_no_service() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "cred-agent"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.delete(
        f"/agents/{agent_id}/credentials/key-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# rollout gate (no DB)
# ---------------------------------------------------------------------------

def test_rollout_gate_no_db() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "prod-agent", "eval_suite_id": "suite-1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/rollout-gate", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["gate_passed"] is False


# ---------------------------------------------------------------------------
# readiness check
# ---------------------------------------------------------------------------

def test_readiness_check_no_connectors() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post("/agents", json={"name": "no-conn"}, headers={"X-API-Key": _VALID_KEY})
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/readiness", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    fail_checks = [c for c in body["checks"] if c["status"] == "fail"]
    assert any(c["check"] == "connectors" for c in fail_checks)


def test_readiness_check_with_connectors() -> None:
    store = AgentStore()
    client = TestClient(_make_app(store), raise_server_exceptions=False)
    cr = client.post(
        "/agents",
        json={"name": "well-configured", "connector_ids": ["github"], "goal_template": "Do {t}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = cr.json()["agent_id"]
    resp = client.get(f"/agents/{agent_id}/readiness", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    pass_checks = [c for c in body["checks"] if c["check"] == "connectors" and c["status"] == "pass"]
    assert len(pass_checks) == 1


# ---------------------------------------------------------------------------
# meta-agent NL creation
# ---------------------------------------------------------------------------

def test_create_agent_nl_success() -> None:
    from app.intelligence.meta_agent import MetaAgentConfig

    store = AgentStore()
    meta = AsyncMock()
    meta.plan = AsyncMock(
        return_value=MetaAgentConfig(
            name="auto-agent",
            goal_template="Process {task}",
            connectors=["slack"],
            autonomy_mode="supervised",
        )
    )
    client = TestClient(_make_app(store, meta), raise_server_exceptions=False)
    resp = client.post(
        "/agents/create",
        json={"command": "Create an agent to process Slack messages"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "agent" in body
    assert "meta_agent_config" in body


def test_create_agent_nl_resolves_connector_names_to_registered_ids() -> None:
    from app.intelligence.meta_agent import MetaAgentConfig

    class Registry:
        async def list_server_records(self, *, tenant_ctx: TenantContext):
            return [
                (
                    "server-jira",
                    SimpleNamespace(
                        name="jira",
                        server_id="server-jira",
                    ),
                ),
                (
                    "server-slack",
                    SimpleNamespace(
                        name="Slack",
                        server_id="server-slack",
                    ),
                ),
            ]

    store = AgentStore()
    meta = AsyncMock()
    meta.plan = AsyncMock(
        return_value=MetaAgentConfig(
            name="jira-agent",
            goal_template="Find assigned Jira issues",
            connectors=["jira"],
            autonomy_mode="supervised",
        )
    )
    client = TestClient(
        _make_app(store, meta, Registry()),
        raise_server_exceptions=False,
    )

    resp = client.post(
        "/agents/create",
        json={"command": "Create a Jira agent"},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["agent"]["connector_ids"] == ["server-jira"]
    assert body["meta_agent_config"]["connectors"] == ["server-jira"]


def test_create_agent_nl_blocks_fully_autonomous() -> None:
    from app.intelligence.meta_agent import MetaAgentConfig

    store = AgentStore()
    meta = AsyncMock()
    meta.plan = AsyncMock(
        return_value=MetaAgentConfig(
            name="risky-auto",
            goal_template="Do anything",
            connectors=[],
            autonomy_mode="fully-autonomous",
        )
    )
    client = TestClient(_make_app(store, meta), raise_server_exceptions=False)
    resp = client.post(
        "/agents/create",
        json={"command": "Create fully-autonomous agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422
