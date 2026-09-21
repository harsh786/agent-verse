"""Tests for the A2A Agent Directory API (app/api/agent_directory.py).

No tenant auth is required for these endpoints — they are public,
agent-store-backed lookups keyed by agent id.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_directory import router as directory_router


def _make_app(agent_store: Any | None) -> FastAPI:
    app = FastAPI()
    app.include_router(directory_router)
    app.state.agent_store = agent_store
    return app


# ---------------------------------------------------------------------------
# GET /.well-known/agents/{agent_id}.json
# ---------------------------------------------------------------------------

def test_get_agent_card_no_store_returns_503() -> None:
    client = TestClient(_make_app(agent_store=None), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/agent-1.json")
    assert resp.status_code == 503


def test_get_agent_card_found_directly() -> None:
    store = MagicMock()
    store.get_agent = AsyncMock(
        return_value={"name": "PR Reviewer", "description": "Reviews PRs"}
    )
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/agent-1.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "agent-1"
    assert body["name"] == "PR Reviewer"
    assert body["description"] == "Reviews PRs"
    assert body["version"] == "1.0"
    assert body["capabilities"]["streaming"] is True
    assert body["endpoint"].endswith("/a2a/agents/agent-1")
    assert body["auth"]["type"] == "bearer"


def test_get_agent_card_falls_back_to_public_list_sync() -> None:
    """get_agent() raises, list_agents(public_only=True) is a *sync* call."""
    store = MagicMock()
    store.get_agent = AsyncMock(side_effect=RuntimeError("not found"))
    store.list_agents = MagicMock(
        return_value=[{"id": "agent-2", "name": "Scheduler", "description": "d"}]
    )
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/agent-2.json")
    assert resp.status_code == 200
    assert resp.json()["id"] == "agent-2"
    assert resp.json()["name"] == "Scheduler"


def test_get_agent_card_falls_back_to_public_list_async() -> None:
    """get_agent() raises, list_agents(public_only=True) returns an awaitable."""
    store = MagicMock()
    store.get_agent = AsyncMock(side_effect=RuntimeError("not found"))
    store.list_agents = AsyncMock(
        return_value=[{"id": "agent-3", "name": "Deployer", "description": "d"}]
    )
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/agent-3.json")
    assert resp.status_code == 200
    assert resp.json()["id"] == "agent-3"


def test_get_agent_card_not_found_404() -> None:
    store = MagicMock()
    store.get_agent = AsyncMock(return_value=None)
    store.list_agents = MagicMock(return_value=[])
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/ghost.json")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_get_agent_card_list_agents_raises_still_404() -> None:
    store = MagicMock()
    store.get_agent = AsyncMock(return_value=None)
    store.list_agents = MagicMock(side_effect=RuntimeError("boom"))
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/ghost.json")
    assert resp.status_code == 404


def test_get_agent_card_uses_agent_id_as_name_fallback() -> None:
    store = MagicMock()
    store.get_agent = AsyncMock(return_value={})  # no "name"/"description"
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents/agent-x.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "agent-x"
    assert body["description"] == ""


# ---------------------------------------------------------------------------
# GET /.well-known/agents
# ---------------------------------------------------------------------------

def test_list_public_agents_no_store() -> None:
    client = TestClient(_make_app(agent_store=None), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents")
    assert resp.status_code == 200
    assert resp.json() == {"agents": [], "total": 0}


def test_list_public_agents_success() -> None:
    store = MagicMock()
    store.list_agents = AsyncMock(
        return_value=[
            {"id": "a1", "name": "Agent One", "description": "d1"},
            {"id": "a2", "name": "Agent Two", "description": "d2"},
        ]
    )
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["agents"][0]["id"] == "a1"


def test_list_public_agents_respects_limit() -> None:
    store = MagicMock()
    store.list_agents = AsyncMock(
        return_value=[{"id": f"a{i}", "name": f"Agent {i}", "description": ""} for i in range(5)]
    )
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["agents"]) == 2


def test_list_public_agents_store_raises_returns_empty() -> None:
    store = MagicMock()
    store.list_agents = AsyncMock(side_effect=RuntimeError("boom"))
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents")
    assert resp.status_code == 200
    assert resp.json() == {"agents": [], "total": 0}


def test_list_public_agents_missing_fields_default_empty_strings() -> None:
    store = MagicMock()
    store.list_agents = AsyncMock(return_value=[{}])
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get("/.well-known/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agents"][0] == {"id": "", "name": "", "description": ""}
