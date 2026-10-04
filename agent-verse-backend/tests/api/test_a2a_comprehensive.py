"""Comprehensive tests for app/api/a2a.py — supplements test_a2a.py."""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import (
    _get_a2a_secret,
    _persist_task,
    _send_callback,
    _tasks,
    _update_task_status,
    _verify_hmac,
)
from app.api.a2a import (
    router as a2a_router,
)


_A2A_TID = "a2a-test-caller"


def _make_app() -> FastAPI:
    from tests.api._a2a_fakes import FakeGoalService

    app = FastAPI()
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
def _set_a2a_tenant(monkeypatch):
    monkeypatch.setenv("A2A_TENANT_ID", "a2a-comprehensive-tenant")
    _tasks.clear()


# ── _verify_hmac ───────────────────────────────────────────────────────────────

def test_verify_hmac_no_secret_returns_true() -> None:
    """Dev mode: no secret → all requests accepted."""
    assert _verify_hmac(b"payload", "anything", "") is True


def test_verify_hmac_no_signature_with_secret_returns_false() -> None:
    assert _verify_hmac(b"payload", "", "my-secret") is False


def test_verify_hmac_valid_signature() -> None:
    secret = "test-secret-123"
    payload = b'{"goal": "test"}'
    expected_hex = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    signature = f"sha256={expected_hex}"
    assert _verify_hmac(payload, signature, secret) is True


def test_verify_hmac_wrong_signature() -> None:
    assert _verify_hmac(b"payload", "sha256=wronghash", "secret") is False


def test_verify_hmac_bad_prefix_without_sha256() -> None:
    secret = "s"
    payload = b"data"
    hex_val = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    # Without "sha256=" prefix, comparison fails
    assert _verify_hmac(payload, hex_val, secret) is False


# ── _persist_task (in-memory) ──────────────────────────────────────────────────

async def test_persist_task_db_none_stores_in_memory() -> None:
    task_id = "task-persist-001"
    data = {"task_id": task_id, "goal": "test", "status": "accepted"}
    await _persist_task(task_id, data, db=None)
    assert _tasks[task_id] == data


# ── _update_task_status (in-memory) ───────────────────────────────────────────

async def test_update_task_status_db_none_updates_in_memory() -> None:
    task_id = "task-upd-001"
    _tasks[task_id] = {"status": "accepted", "result": "", "tenant_id": _A2A_TID}
    await _update_task_status(task_id, _A2A_TID, "complete", "done", db=None)
    assert _tasks[task_id]["status"] == "complete"
    assert _tasks[task_id]["result"] == "done"


async def test_update_task_status_ignores_another_tenants_task() -> None:
    """Status updates are scoped to the owning tenant."""
    _tasks["task-foreign"] = {"status": "accepted", "result": "", "tenant_id": "someone-else"}
    await _update_task_status("task-foreign", _A2A_TID, "complete", "hijacked", db=None)
    assert _tasks["task-foreign"]["status"] == "accepted"


async def test_update_task_status_missing_task_noop() -> None:
    """Update for a nonexistent task should not raise."""
    await _update_task_status("nonexistent", _A2A_TID, "failed", "error", db=None)


# ── _send_callback ────────────────────────────────────────────────────────────

async def test_send_callback_noop_empty_url() -> None:
    """No callback URL → no HTTP call, no error."""
    await _send_callback("", "task-1", "complete", "done")


async def test_send_callback_http_error_is_swallowed(respx_mock) -> None:
    import httpx

    respx_mock.post("https://callback.example.com/done").mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )
    # Should not raise
    await _send_callback("https://callback.example.com/done", "t1", "complete", "r")


# ── agent_card ────────────────────────────────────────────────────────────────

def test_agent_card_structure() -> None:
    client = TestClient(_make_app())
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agentverse-platform"
    assert "endpoint" in data
    assert "authentication" in data
    assert "hmac-sha256" in data["authentication"]["scheme"]
    assert len(data["capabilities"]) >= 5
    assert len(data["supported_task_types"]) >= 1


# ── receive_a2a_task ──────────────────────────────────────────────────────────

def test_receive_task_no_hmac_secret_accepted(monkeypatch) -> None:
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    client = TestClient(_make_app())
    resp = client.post("/a2a/tasks", json={"goal": "Do X"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "working"


def test_receive_task_no_longer_depends_on_a2a_tenant_id(monkeypatch) -> None:
    """Tasks run as the authenticated caller, so A2A_TENANT_ID is irrelevant.

    It used to be *required* (503 without it) because every inbound goal ran as
    that one fixed tenant, regardless of who authenticated.
    """
    monkeypatch.delenv("A2A_TENANT_ID", raising=False)
    client = TestClient(_make_app())
    resp = client.post("/a2a/tasks", json={"goal": "Do X"})
    assert resp.status_code == 202


def test_receive_task_without_a_goal_service_is_503_not_accepted() -> None:
    """A2A-05: the task was stored and 202 'accepted' but could never run."""
    app = _make_app()
    app.state.goal_service = None
    resp = TestClient(app).post("/a2a/tasks", json={"goal": "Do X"})
    assert resp.status_code == 503
    assert _tasks == {}  # nothing stranded as 'accepted'


def test_receive_task_without_an_authenticated_caller_is_401() -> None:
    app = FastAPI()
    app.state.db_session_factory = None
    app.state.goal_service = None
    app.include_router(a2a_router)
    resp = TestClient(app).post("/a2a/tasks", json={"goal": "Do X"})
    assert resp.status_code == 401


def test_receive_task_bad_hmac_returns_401(monkeypatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", "real-secret")
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={"goal": "Test goal"},
        headers={"X-A2A-Signature": "sha256=badhash"},
    )
    assert resp.status_code == 401


def test_receive_task_valid_hmac(monkeypatch) -> None:
    secret = "my-test-secret"
    monkeypatch.setenv("A2A_SHARED_SECRET", secret)
    client = TestClient(_make_app())
    payload = json.dumps({"goal": "HMAC task", "context": {}}).encode()
    import time

    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + payload
    sig = "sha256=" + _hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    headers = {"X-A2A-Signature": sig, "X-A2A-Timestamp": ts, "Content-Type": "application/json"}
    resp = client.post("/a2a/tasks", content=payload, headers=headers)
    assert resp.status_code == 202
    # The same signed request again is a replay (body-only HMAC had no nonce).
    assert client.post("/a2a/tasks", content=payload, headers=headers).status_code == 401


def test_receive_task_body_only_or_stale_signature_is_rejected(monkeypatch) -> None:
    import time

    secret = "my-test-secret"
    monkeypatch.setenv("A2A_SHARED_SECRET", secret)
    client = TestClient(_make_app())
    payload = json.dumps({"goal": "HMAC task 2", "context": {}}).encode()
    body_only = "sha256=" + _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    h = {"X-A2A-Signature": body_only, "Content-Type": "application/json"}
    assert client.post("/a2a/tasks", content=payload, headers=h).status_code == 401
    old = str(int(time.time()) - 3600)
    stale = "sha256=" + _hmac.new(
        secret.encode(), f"{old}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    h = {"X-A2A-Signature": stale, "X-A2A-Timestamp": old, "Content-Type": "application/json"}
    assert client.post("/a2a/tasks", content=payload, headers=h).status_code == 401


def test_receive_task_stores_in_memory() -> None:
    client = TestClient(_make_app())
    resp = client.post("/a2a/tasks", json={"goal": "Stored task"})
    task_id = resp.json()["task_id"]
    assert task_id in _tasks


def test_receive_task_with_callback_url() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={
            "goal": "With callback",
            "callback_url": "https://example.com/cb",
            "requester_agent_id": "agent-xyz",
        },
    )
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    assert _tasks[task_id]["callback_url"] == "https://example.com/cb"
    assert _tasks[task_id]["requester_agent_id"] == "agent-xyz"


def test_receive_task_with_priority() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/a2a/tasks",
        json={"goal": "Priority task", "priority": "high"},
    )
    assert resp.status_code == 202


def test_receive_task_returns_tracking_message() -> None:
    client = TestClient(_make_app())
    resp = client.post("/a2a/tasks", json={"goal": "Track me"})
    data = resp.json()
    # Message is like "Task accepted. Track at /a2a/tasks/{task_id}"
    assert "task_id" in data
    assert "/a2a/tasks/" in data["message"]


# ── get_a2a_task ──────────────────────────────────────────────────────────────

def test_get_task_returns_status() -> None:
    client = TestClient(_make_app())
    create = client.post("/a2a/tasks", json={"goal": "Test"})
    task_id = create.json()["task_id"]
    resp = client.get(f"/a2a/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("working", "complete")


def test_get_task_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.get("/a2a/tasks/absolutely-nonexistent-xyz")
    assert resp.status_code == 404


def test_get_task_contains_expected_fields() -> None:
    client = TestClient(_make_app())
    create = client.post("/a2a/tasks", json={"goal": "Fields test"})
    task_id = create.json()["task_id"]
    resp = client.get(f"/a2a/tasks/{task_id}")
    data = resp.json()
    assert "task_id" in data
    assert "status" in data
    assert "goal" in data


# ── _get_a2a_secret ────────────────────────────────────────────────────────────

def test_get_a2a_secret_from_env(monkeypatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", "env-secret")
    assert _get_a2a_secret() == "env-secret"


def test_get_a2a_secret_empty_by_default(monkeypatch) -> None:
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    assert _get_a2a_secret() == ""
