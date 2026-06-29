"""Comprehensive tests for app/api/integrations.py — targets the 20% baseline."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router as integrations_router


def _make_app(goal_service: Any = None, hitl_gateway: Any = None) -> FastAPI:
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(integrations_router)
    # Provide a minimal settings object so that app.state.settings attribute lookups
    # don't raise AttributeError (e.g. when checking slack_tenant_id fallback)
    app.state.settings = SimpleNamespace()
    if goal_service is not None:
        app.state.goal_service = goal_service
    if hitl_gateway is not None:
        app.state.hitl_gateway = hitl_gateway
    return app


# ── Slack signature helper ─────────────────────────────────────────────────────

def _slack_sig(body: bytes, secret: str, timestamp: str | None = None) -> tuple[str, str]:
    ts = timestamp or str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return ts, sig


# ── POST /integrations/slack/commands ────────────────────────────────────────

def test_slack_command_no_secret_dev_mode(monkeypatch) -> None:
    """Without SLACK_SIGNING_SECRET, dev mode allows all requests."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    body = urllib.parse.urlencode({"text": "Deploy my app", "user_id": "U123"}).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # No goal service configured → returns service unavailable message
    data = resp.json()
    assert "response_type" in data


def test_slack_command_no_text_returns_usage_hint(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    body = urllib.parse.urlencode({"text": "", "user_id": "U123"}).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    assert "Usage" in data["text"]


def test_slack_command_invalid_signature_returns_403(monkeypatch) -> None:
    secret = "real-secret"
    monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
    monkeypatch.setenv("ENVIRONMENT", "development")

    body = urllib.parse.urlencode({"text": "test", "user_id": "U1"}).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Signature": "v0=badsignature",
            "X-Slack-Request-Timestamp": str(int(time.time())),
        },
    )
    assert resp.status_code == 403


def test_slack_command_valid_signature_but_no_tenant_id(monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
    monkeypatch.delenv("SLACK_TENANT_ID", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    body = urllib.parse.urlencode({"text": "run daily report", "user_id": "U1"}).encode()
    ts, sig = _slack_sig(body, secret)

    # Provide a goal_service so we get past the "service unavailable" check
    # and reach the SLACK_TENANT_ID validation
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
        },
    )
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    # Message mentions SLACK_TENANT_ID is not configured
    assert "SLACK_TENANT_ID" in data["text"] or "not configured" in data["text"].lower()


def test_slack_command_with_tenant_and_goal_service(monkeypatch) -> None:
    secret = "my-secret"
    monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
    monkeypatch.setenv("SLACK_TENANT_ID", "slack-tenant-1")

    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "g123"})

    body = urllib.parse.urlencode({"text": "summarize daily report", "user_id": "U2"}).encode()
    ts, sig = _slack_sig(body, secret)

    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
        },
    )
    data = resp.json()
    assert data["response_type"] == "in_channel"
    assert "g123" in data["text"]


def test_slack_command_goal_service_exception(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SLACK_TENANT_ID", "t1")

    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(side_effect=RuntimeError("service down"))

    body = urllib.parse.urlencode({"text": "failing goal", "user_id": "U3"}).encode()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/slack/commands",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    assert "Error" in data["text"]


# ── POST /integrations/slack/events ──────────────────────────────────────────

def test_slack_events_url_verification() -> None:
    body = b'{"type": "url_verification", "challenge": "test-challenge-xyz"}'
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "test-challenge-xyz"


def test_slack_events_returns_ok_for_unknown_type() -> None:
    body = b'{"type": "message", "event": {"text": "hello"}}'
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_slack_events_block_actions_approve_hitl(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("SLACK_TENANT_ID", "evt-tenant")

    mock_hitl = MagicMock()
    mock_hitl.approve = MagicMock()
    mock_hitl.reject = AsyncMock()

    body_data = {
        "type": "block_actions",
        "actions": [{"action_id": "approve_hitl", "value": "req-123"}],
        "user": {"name": "alice"},
    }
    import json
    body = json.dumps(body_data).encode()
    client = TestClient(_make_app(hitl_gateway=mock_hitl))
    resp = client.post(
        "/integrations/slack/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    mock_hitl.approve.assert_called_once()


def test_slack_events_block_actions_reject_hitl(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("SLACK_TENANT_ID", "evt-tenant-2")

    mock_hitl = MagicMock()
    mock_hitl.approve = MagicMock()
    mock_hitl.reject = AsyncMock()

    import json
    body_data = {
        "type": "block_actions",
        "actions": [{"action_id": "reject_hitl", "value": "req-456"}],
        "user": {"name": "bob"},
    }
    body = json.dumps(body_data).encode()
    client = TestClient(_make_app(hitl_gateway=mock_hitl))
    resp = client.post(
        "/integrations/slack/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_slack_events_invalid_json_body_parsed_as_form(monkeypatch) -> None:
    import json
    payload = {"type": "url_verification", "challenge": "form-challenge"}
    body = ("payload=" + urllib.parse.quote(json.dumps(payload))).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/events",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200


# ── POST /integrations/slack/interactive ─────────────────────────────────────

def test_slack_interactive_invalid_signature_returns_401(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    import json
    payload = json.dumps({"type": "block_actions", "actions": []})
    body = ("payload=" + urllib.parse.quote(payload)).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/interactive",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Signature": "v0=badsig",
            "X-Slack-Request-Timestamp": str(int(time.time())),
        },
    )
    assert resp.status_code == 401


def test_slack_interactive_non_block_actions_returns_ok(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    import json
    payload = json.dumps({"type": "shortcut"})
    body = ("payload=" + urllib.parse.quote(payload)).encode()
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/slack/interactive",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_slack_interactive_approve_action_resumes_goal(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("SLACK_TENANT_ID", "interactive-tenant")

    import json
    payload = json.dumps({
        "type": "block_actions",
        "actions": [{"action_id": "approve_hitl", "value": "goal-789"}],
        "user": {"name": "approver"},
    })
    body = ("payload=" + urllib.parse.quote(payload)).encode()

    mock_svc = MagicMock()
    mock_svc.resume_goal = AsyncMock(return_value={"goal_id": "goal-789"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/slack/interactive",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ── POST /integrations/zapier/trigger ────────────────────────────────────────

def test_zapier_trigger_no_secret_dev_mode(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zapier-t1")

    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "z1", "status": "planning"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/zapier/trigger",
        json={"goal": "Run daily sync", "priority": "high"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == "z1"
    assert "track_url" in data


def test_zapier_trigger_invalid_secret_returns_403(monkeypatch) -> None:
    monkeypatch.setenv("ZAPIER_WEBHOOK_SECRET", "real-secret")
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/zapier/trigger",
        json={"goal": "test"},
        headers={"X-Zapier-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403


def test_zapier_trigger_no_goal_text_returns_422(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    client = TestClient(_make_app())
    resp = client.post("/integrations/zapier/trigger", json={})
    assert resp.status_code == 422


def test_zapier_trigger_no_goal_service_returns_503(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zt1")
    client = TestClient(_make_app())  # no goal_service
    resp = client.post("/integrations/zapier/trigger", json={"goal": "test"})
    assert resp.status_code == 503


def test_zapier_trigger_no_tenant_id_returns_503(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ZAPIER_TENANT_ID", raising=False)
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post("/integrations/zapier/trigger", json={"goal": "test"})
    assert resp.status_code == 503


def test_zapier_trigger_uses_text_field(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zt2")

    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "z2", "status": "planning"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post("/integrations/zapier/trigger", json={"text": "From Zapier text field"})
    assert resp.status_code == 200
    assert resp.json()["goal_id"] == "z2"


def test_zapier_trigger_uses_message_field(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zt3")

    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "z3", "status": "planning"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post("/integrations/zapier/trigger", json={"message": "message-based goal"})
    assert resp.status_code == 200


# ── GET /integrations/zapier/goals ────────────────────────────────────────────

def test_zapier_poll_no_goal_service_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_TENANT_ID", raising=False)
    client = TestClient(_make_app())
    resp = client.get("/integrations/zapier/goals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_zapier_poll_no_tenant_id_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("ZAPIER_TENANT_ID", raising=False)
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/integrations/zapier/goals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_zapier_poll_returns_completed_goals(monkeypatch) -> None:
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zpoll-tenant")
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={
        "goals": [
            {"goal_id": "g1", "status": "complete"},
            {"goal_id": "g2", "status": "failed"},
            {"goal_id": "g3", "status": "complete"},
        ]
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/integrations/zapier/goals")
    assert resp.status_code == 200
    results = resp.json()
    assert all(g["status"] == "complete" for g in results)
    assert len(results) == 2


def test_zapier_poll_limits_to_10(monkeypatch) -> None:
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zpoll-limit")
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={
        "goals": [{"status": "complete"} for _ in range(20)]
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/integrations/zapier/goals")
    assert len(resp.json()) <= 10


def test_zapier_poll_service_exception_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("ZAPIER_TENANT_ID", "zpoll-error")
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(side_effect=RuntimeError("error"))
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/integrations/zapier/goals")
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST /integrations/events/alertmanager ────────────────────────────────────

def test_alertmanager_no_alerts_returns_zero() -> None:
    client = TestClient(_make_app())
    resp = client.post("/integrations/events/alertmanager", json={"alerts": []})
    assert resp.status_code == 200
    assert resp.json()["received"] == 0
    assert resp.json()["goals_created"] == 0


def test_alertmanager_resolved_alert_ignored(monkeypatch) -> None:
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "am-tenant")
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "ag1"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/alertmanager",
        json={"alerts": [{"status": "resolved", "labels": {"alertname": "Test"}}]},
    )
    assert resp.status_code == 200
    assert resp.json()["goals_created"] == 0


def test_alertmanager_firing_alert_no_tenant_id_ignored(monkeypatch) -> None:
    monkeypatch.delenv("ALERTMANAGER_TENANT_ID", raising=False)
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/alertmanager",
        json={"alerts": [{"status": "firing", "labels": {"alertname": "HighCPU", "severity": "critical"}}]},
    )
    assert resp.status_code == 200
    assert resp.json()["goals_created"] == 0


def test_alertmanager_firing_alert_creates_goal(monkeypatch) -> None:
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "am-tenant-2")
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "ag2"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/alertmanager",
        json={
            "alerts": [{
                "status": "firing",
                "labels": {"alertname": "HighCPU", "severity": "warning"},
                "annotations": {"summary": "CPU over 90%"},
            }]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["goals_created"] == 1
    assert "ag2" in resp.json()["goal_ids"]


# ── POST /integrations/events/datadog ────────────────────────────────────────

def test_datadog_non_critical_alert_ignored() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/integrations/events/datadog",
        json={"title": "Info alert", "text": "just informational", "alert_type": "info"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_datadog_critical_alert_no_tenant_id_no_goal(monkeypatch) -> None:
    monkeypatch.delenv("DATADOG_TENANT_ID", raising=False)
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/datadog",
        json={"title": "Critical DB", "text": "DB down", "alert_type": "critical"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"
    assert resp.json()["goal_id"] is None


def test_datadog_critical_alert_creates_goal(monkeypatch) -> None:
    monkeypatch.setenv("DATADOG_TENANT_ID", "dd-tenant")
    monkeypatch.delenv("DATADOG_WEBHOOK_SECRET", raising=False)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "dd-goal-1"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/datadog",
        json={"title": "DB Failure", "text": "connection refused", "alert_type": "error"},
    )
    assert resp.status_code == 200
    assert resp.json()["goal_id"] == "dd-goal-1"


def test_datadog_warning_alert_creates_goal(monkeypatch) -> None:
    monkeypatch.setenv("DATADOG_TENANT_ID", "dd-tenant-2")
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "dd-goal-2"})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/integrations/events/datadog",
        json={"title": "Warning", "text": "memory high", "alert_type": "warning"},
    )
    assert resp.status_code == 200


def test_datadog_no_goal_service_returns_processed(monkeypatch) -> None:
    monkeypatch.delenv("DATADOG_WEBHOOK_SECRET", raising=False)
    client = TestClient(_make_app())  # no goal service
    resp = client.post(
        "/integrations/events/datadog",
        json={"title": "X", "text": "y", "alert_type": "critical"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"
