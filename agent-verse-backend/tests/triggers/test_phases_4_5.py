"""Tests for Phase 4 (typed webhook parsers + rotation)."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.triggers.webhooks.parsers import (
    GitHubWebhookPayload,
    JiraWebhookPayload,
    LinearWebhookPayload,
    PagerDutyWebhookPayload,
    SlackEventPayload,
    StripeWebhookPayload,
)
from app.triggers.webhooks.rotation import WebhookSecretRotation
from app.triggers.webhooks.verifier import WebhookSignatureVerifier

# ── GitHub Webhook Parser ─────────────────────────────────────────────────────

def test_github_parse_push():
    body = {
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "alice"},
        "action": "",
        "ref": "refs/heads/main",
        "head_commit": {"message": "fix: bug"},
    }
    p = GitHubWebhookPayload.parse({"X-GitHub-Event": "push"}, body)
    assert p.event_type == "push"
    assert p.repo_full_name == "org/repo"
    assert p.sender_login == "alice"
    assert p.ref == "refs/heads/main"
    assert p.head_commit_message == "fix: bug"


def test_github_parse_pr():
    body = {
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "bob"},
        "action": "opened",
    }
    p = GitHubWebhookPayload.parse({"x-github-event": "pull_request"}, body)
    assert p.event_type == "pull_request"
    assert p.action == "opened"


# ── Stripe Webhook Parser ─────────────────────────────────────────────────────

def test_stripe_parse_payment_intent():
    body = {
        "id": "evt_001",
        "type": "payment_intent.succeeded",
        "livemode": True,
        "data": {
            "object": {
                "object": "payment_intent",
                "amount": 5000,
                "currency": "usd",
            }
        },
    }
    p = StripeWebhookPayload.parse(body)
    assert p.event_type == "payment_intent.succeeded"
    assert p.event_id == "evt_001"
    assert p.livemode is True
    assert p.amount == 5000
    assert p.currency == "usd"


def test_stripe_parse_missing_data():
    p = StripeWebhookPayload.parse({})
    assert p.event_type == ""
    assert p.amount == 0


# ── Jira Webhook Parser ───────────────────────────────────────────────────────

def test_jira_parse_issue_updated():
    body = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": "PROJ-42",
            "fields": {
                "status": {"name": "In Progress"},
                "project": {"key": "PROJ"},
            },
        },
        "user": {"displayName": "Charlie"},
    }
    p = JiraWebhookPayload.parse(body)
    assert p.event_type == "jira:issue_updated"
    assert p.issue_key == "PROJ-42"
    assert p.issue_status == "In Progress"
    assert p.project_key == "PROJ"
    assert p.user_display_name == "Charlie"


# ── Slack Event Parser ────────────────────────────────────────────────────────

def test_slack_parse_message():
    body = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "message",
            "channel": "C456",
            "user": "U789",
            "text": "hello world",
            "ts": "1234567890.000001",
        },
    }
    p = SlackEventPayload.parse(body)
    assert p.event_type == "event_callback"
    assert p.team_id == "T123"
    assert p.channel == "C456"
    assert p.user == "U789"
    assert p.text == "hello world"


# ── PagerDuty Webhook Parser ──────────────────────────────────────────────────

def test_pagerduty_parse_incident():
    body = {
        "messages": [{
            "event": "incident.trigger",
            "incident": {
                "id": "Q1",
                "service": {"name": "API Gateway"},
                "severity": "critical",
                "urgency": "high",
            },
        }]
    }
    p = PagerDutyWebhookPayload.parse(body)
    assert p.event_type == "incident.trigger"
    assert p.service_name == "API Gateway"
    assert p.incident_id == "Q1"
    assert p.severity == "critical"


# ── Linear Webhook Parser ─────────────────────────────────────────────────────

def test_linear_parse_issue():
    body = {
        "type": "Issue",
        "action": "update",
        "data": {
            "id": "issue-abc",
            "title": "Fix login bug",
            "state": {"name": "In Review"},
            "team": {"name": "Backend"},
        },
    }
    p = LinearWebhookPayload.parse(body)
    assert p.event_type == "Issue"
    assert p.action == "update"
    assert p.issue_title == "Fix login bug"
    assert p.state_name == "In Review"
    assert p.team_name == "Backend"


# ── WebhookSecretRotation ─────────────────────────────────────────────────────

def test_rotation_generates_hex_secret():
    r = WebhookSecretRotation()
    s1 = r.generate_secret()
    s2 = r.generate_secret()
    assert len(s1) == 64  # 32 bytes hex = 64 chars
    assert s1 != s2  # should be unique


@pytest.mark.asyncio
async def test_rotation_rotate_no_store():
    r = WebhookSecretRotation()
    result = await r.rotate("trigger-1")
    assert result["trigger_id"] == "trigger-1"
    assert len(result["new_secret"]) == 64
    assert result["grace_period_seconds"] == 300


@pytest.mark.asyncio
async def test_rotation_schedule():
    r = WebhookSecretRotation()
    ids = await r.schedule_rotation(["t1", "t2", "t3"])
    assert set(ids) == {"t1", "t2", "t3"}


# ── WebhookSignatureVerifier (platform helpers) ───────────────────────────────

def test_github_signature_helper():
    secret = "my-secret"
    payload = b'{"ref": "main"}'
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    v = WebhookSignatureVerifier()
    assert v.verify_github(payload, sig, secret) is True


def test_stripe_signature_helper():
    secret = "whsec_test"
    payload = b'{"type": "payment_intent.succeeded"}'
    timestamp = "1700000000"
    signed_payload = f"{timestamp}.".encode() + payload
    sig_hash = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={sig_hash}"
    v = WebhookSignatureVerifier()
    assert v.verify_stripe(payload, header, secret, now=float(timestamp) + 10) is True
    # Replay protection: outside the 5-minute tolerance the same header fails.
    assert v.verify_stripe(payload, header, secret, now=float(timestamp) + 301) is False
    # During a secret roll Stripe sends several v1 values; any valid one passes.
    rolled = f"t={timestamp},v1={'0' * 64},v1={sig_hash}"
    assert v.verify_stripe(payload, rolled, secret, now=float(timestamp)) is True


@pytest.mark.asyncio
async def test_typed_verify_uses_the_stripe_scheme():
    """Regression: the typed webhook route called the generic verify(), which
    HMACs the bare body and compares the text after the last '=' — a genuine
    Stripe t=,v1= signature could never verify."""
    import time as _time

    secret = "whsec_test"
    payload = b'{"type": "invoice.paid"}'
    ts = str(int(_time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    v = WebhookSignatureVerifier()
    assert await v.verify(payload, header, secret) is False  # the old code path
    assert await v.verify_for_type("stripe", payload, header, secret) is True
    assert await v.verify_for_type("stripe", payload, header, "wrong") is False
