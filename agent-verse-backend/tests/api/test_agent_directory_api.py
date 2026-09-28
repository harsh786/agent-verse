"""Tests for the A2A Agent Directory API (app/api/agent_directory.py).

The per-agent directory is NOT IMPLEMENTED: it previously called store methods
that do not exist (``get_agent`` / ``list_agents``) under a blanket
``suppress(Exception)``, so it silently answered 404 / an empty list, and it
advertised a non-existent ``/a2a/agents/{id}`` endpoint. There is no public-agent
visibility model, so wiring it to the real store would expose every tenant's
agents on an unauthenticated path. It now answers an honest 501.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_directory import router as directory_router


def _make_app(agent_store: Any | None) -> FastAPI:
    app = FastAPI()
    app.include_router(directory_router)
    app.state.agent_store = agent_store
    return app


def _store_with_private_agent() -> MagicMock:
    store = MagicMock()
    store.get_agent = AsyncMock(return_value={"id": "agent-1", "name": "Tenant A secret"})
    store.list_agents = AsyncMock(return_value=[{"id": "agent-1", "name": "Tenant A secret"}])
    store.get_async = AsyncMock(return_value={"id": "agent-1", "name": "Tenant A secret"})
    return store


@pytest.mark.parametrize("path", ["/.well-known/agents/agent-1.json", "/.well-known/agents"])
@pytest.mark.parametrize("store", [None, "private"])
def test_directory_is_not_implemented_and_leaks_nothing(path: str, store: Any) -> None:
    agent_store = _store_with_private_agent() if store == "private" else None
    client = TestClient(_make_app(agent_store))
    resp = client.get(path)
    assert resp.status_code == 501
    assert "NOT IMPLEMENTED" in resp.json()["detail"]
    assert "Tenant A secret" not in resp.text
    if agent_store is not None:
        agent_store.get_agent.assert_not_called()
        agent_store.list_agents.assert_not_called()
        agent_store.get_async.assert_not_called()
