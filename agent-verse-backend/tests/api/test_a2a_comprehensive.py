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
    router as a2a_router,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = None
    app.state.goal_service = None
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
    _tasks[task_id] = {"status": "accepted", "result": ""}
    await _update_task_status(task_id, "complete", "done", db=None)
    assert _tasks[task_id]["status"] == "complete"
    assert _tasks[task_id]["result"] == "done"


async def test_update_task_status_missing_task_noop() -> None:
    """Update for a nonexistent task should not raise."""
    await _update_task_status("nonexistent", "failed", "error", db=None)


# ── _send_callback ────────────────────────────────────────────────────────────

async def test_send_callback_noop_empty_url() -> None:
    """No callback URL → no HTTP call, no error."""
    await _send_callback("", "task-1", "complete", "done")


async def test_send_callback_http_error_is_swallowed(respx_mock) -> None:
    import respx
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
    assert resp.json()["status"] == "accepted"


def test_receive_task_missing_tenant_id_returns_503(monkeypatch) -> None:
    monkeypatch.delenv("A2A_TENANT_ID", raising=False)
    client = TestClient(_make_app())
    resp = client.post("/a2a/tasks", json={"goal": "Do X"})
    assert resp.status_code == 503


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
    sig = "sha256=" + _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    resp = client.post(
        "/a2a/tasks",
        content=payload,
        headers={"X-A2A-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 202


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
    assert resp.json()["status"] == "accepted"


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
