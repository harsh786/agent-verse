"""Tests for A2A protocol endpoints — updated for DB-backed + HMAC implementation."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import _tasks
from app.api.a2a import router as a2a_router


def _make_app() -> FastAPI:
    from tests.api._a2a_fakes import FakeGoalService

    app = FastAPI()
    # Wire minimal state
    app.state.db_session_factory = None
    app.state.goal_service = FakeGoalService()
    # The real app puts /a2a behind TenantMiddleware; inbound tasks now run as
    # that authenticated caller (they used to run as the fixed A2A_TENANT_ID).
    @app.middleware("http")
    async def _inject_caller(request, call_next):  # type: ignore[no-untyped-def]
        import os as _os

        from app.tenancy.context import PlanTier, TenantContext

        request.state.tenant = TenantContext(
            tenant_id=_os.getenv("A2A_TENANT_ID") or "a2a-test-caller",
            plan=PlanTier.FREE,
            api_key_id="test-key",
        )
        return await call_next(request)

    app.include_router(a2a_router)
    return app


@pytest.fixture(autouse=True)
def _set_a2a_tenant(monkeypatch) -> None:
    """Set A2A_TENANT_ID for all tests in this module."""
    monkeypatch.setenv("A2A_TENANT_ID", "test-a2a-tenant")

def test_agent_card_returns_json() -> None:
    client = TestClient(_make_app())
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "AgentVerse Platform"
    assert data["agent_id"] == "agentverse-platform"
    # capabilities is a list
    assert isinstance(data["capabilities"], list)
    assert len(data["capabilities"]) > 0


def test_receive_task_returns_accepted() -> None:
    _tasks.clear()
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={
            "goal": "Summarize quarterly report",
            "context": {"tenant": "acme"},
        },
    )
    # New implementation returns 202 "accepted"
    assert resp.status_code in (200, 202)
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "accepted"


def test_get_task_status() -> None:
    _tasks.clear()
    client = TestClient(_make_app())

    # Create the task first
    create_resp = client.post(
        "/a2a/tasks",
        json={"goal": "Generate report"},
    )
    task_id = create_resp.json()["task_id"]

    # Fetch task status
    resp = client.get(f"/a2a/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert "status" in data


def test_get_unknown_task_returns_404() -> None:
    _tasks.clear()
    client = TestClient(_make_app())
    resp = client.get("/a2a/tasks/nonexistent-task-xyz")
    assert resp.status_code == 404


def test_hmac_verification_disabled_without_secret(monkeypatch) -> None:
    """When A2A_SHARED_SECRET not set, any request is accepted."""
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={"goal": "Test goal"},
        headers={"X-A2A-Signature": ""},
    )
    assert resp.status_code in (200, 202)


def test_hmac_verification_rejects_bad_signature(monkeypatch) -> None:
    """When A2A_SHARED_SECRET is set, bad signature returns 401."""
    monkeypatch.setenv("A2A_SHARED_SECRET", "real-secret")
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={"goal": "Test goal"},
        headers={"X-A2A-Signature": "sha256=badhash"},
    )
    assert resp.status_code == 401
