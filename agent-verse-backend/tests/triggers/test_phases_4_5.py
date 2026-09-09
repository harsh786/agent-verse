"""Tests for Phase 4 (typed webhook parsers + rotation) and Phase 5 (IoT/geofence)."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest

from app.triggers.iot.geofence import (
    GeofenceRegion,
    GeofenceTriggerEvaluator,
    LatLng,
    haversine_meters,
    point_in_polygon,
)
from app.triggers.iot.mqtt import MQTTTriggerConsumer
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
    assert v.verify_stripe(payload, header, secret) is True


# ── Geofence ──────────────────────────────────────────────────────────────────

def test_point_in_polygon_inside():
    # Simple square: (0,0), (0,1), (1,1), (1,0)
    polygon = [LatLng(0, 0), LatLng(0, 1), LatLng(1, 1), LatLng(1, 0)]
    assert point_in_polygon(LatLng(0.5, 0.5), polygon) is True


def test_point_in_polygon_outside():
    polygon = [LatLng(0, 0), LatLng(0, 1), LatLng(1, 1), LatLng(1, 0)]
    assert point_in_polygon(LatLng(2.0, 2.0), polygon) is False


def test_haversine_zero_distance():
    p = LatLng(51.5, -0.1)
    assert haversine_meters(p, p) == pytest.approx(0.0, abs=1e-5)


def test_haversine_known_distance():
    # London to Paris ≈ 340km
    london = LatLng(51.5074, -0.1278)
    paris = LatLng(48.8566, 2.3522)
    dist = haversine_meters(london, paris)
    assert 330_000 < dist < 360_000


def test_geofence_polygon_inside():
    evaluator = GeofenceTriggerEvaluator()
    region = GeofenceRegion(
        region_id="r1",
        name="Office",
        polygon=[LatLng(0, 0), LatLng(0, 1), LatLng(1, 1), LatLng(1, 0)],
    )
    assert evaluator.is_inside(region, LatLng(0.5, 0.5)) is True


def test_geofence_circle_inside():
    evaluator = GeofenceTriggerEvaluator()
    region = GeofenceRegion(
        region_id="r2",
        name="Home",
        radius_meters=500,
        center=LatLng(51.5, -0.1),
    )
    # 10m away
    assert evaluator.is_inside(region, LatLng(51.5001, -0.1)) is True


def test_geofence_circle_outside():
    evaluator = GeofenceTriggerEvaluator()
    region = GeofenceRegion(
        region_id="r3",
        name="Small Zone",
        radius_meters=100,
        center=LatLng(51.5, -0.1),
    )
    # 10km away
    assert evaluator.is_inside(region, LatLng(51.6, -0.1)) is False


@pytest.mark.asyncio
async def test_geofence_dispatcher_called():
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value={"goal_created": True})

    from types import SimpleNamespace

    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.GEOFENCE)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Device entered region")

    evaluator = GeofenceTriggerEvaluator(trigger_store=store, dispatcher=mock_dispatcher)
    region = GeofenceRegion(
        region_id="r1",
        name="Office",
        polygon=[LatLng(0, 0), LatLng(0, 2), LatLng(2, 2), LatLng(2, 0)],
    )
    results = await evaluator.evaluate("device-001", LatLng(1, 1), [region], tenant_id="t1")
    assert mock_dispatcher.dispatch.called


# ── MQTT Consumer ─────────────────────────────────────────────────────────────

def test_mqtt_topic_match_exact():
    consumer = MQTTTriggerConsumer()
    assert consumer._topic_matches("sensors/temp", "sensors/temp") is True


def test_mqtt_topic_match_wildcard_plus():
    consumer = MQTTTriggerConsumer()
    assert consumer._topic_matches("sensors/+/temp", "sensors/device1/temp") is True


def test_mqtt_topic_match_wildcard_hash():
    consumer = MQTTTriggerConsumer()
    assert consumer._topic_matches("sensors/#", "sensors/device1/temp/celsius") is True


def test_mqtt_topic_no_match():
    consumer = MQTTTriggerConsumer()
    assert consumer._topic_matches("sensors/temp", "actuators/fan") is False


@pytest.mark.asyncio
async def test_mqtt_consumer_no_client():
    consumer = MQTTTriggerConsumer()
    # Should not raise
    await consumer.start()
    await consumer.stop()


@pytest.mark.asyncio
async def test_mqtt_handle_message_fires_trigger():
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value={"goal_created": True})

    from types import SimpleNamespace

    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.MQTT)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="MQTT data received")

    consumer = MQTTTriggerConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    payload = json.dumps({"temperature": 25.5}).encode()
    await consumer.handle_message("sensors/temp", payload, tenant_id="t1")
    assert mock_dispatcher.dispatch.called
