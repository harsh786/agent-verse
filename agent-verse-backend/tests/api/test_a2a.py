"""Tests for A2A protocol endpoints."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import _received_tasks, _task_results, router as a2a_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def test_agent_card_returns_json() -> None:
    client = TestClient(_make_app())
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "AgentVerse Platform"
    assert data["agent_id"] == "agentverse-platform"
    assert "goals" in data["capabilities"]
    assert data["auth_required"] is True


def test_receive_task() -> None:
    _received_tasks.clear()
    _task_results.clear()
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={
            "task_id": "t-001",
            "goal": "Summarize quarterly report",
            "context": {"tenant": "acme"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t-001"
    assert data["status"] == "received"
    assert "t-001" in _received_tasks


def test_get_task_status() -> None:
    _received_tasks.clear()
    _task_results.clear()
    client = TestClient(_make_app())

    # Create the task first
    client.post(
        "/a2a/tasks",
        json={"task_id": "t-002", "goal": "Generate report"},
    )

    # Pending task returns status pending
    resp = client.get("/a2a/tasks/t-002")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t-002"
    assert data["status"] == "pending"


def test_get_task_not_found_returns_404() -> None:
    _received_tasks.clear()
    _task_results.clear()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/a2a/tasks/ghost-task")
    assert resp.status_code == 404
