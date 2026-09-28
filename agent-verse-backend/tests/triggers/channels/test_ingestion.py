"""Tests for channel ingestion API and channel gateway."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels.ingestion import router
from app.triggers.channels.gateway import ChannelIngestionGateway, NLIntentClassifier

# ── NLIntentClassifier ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_command():
    c = NLIntentClassifier()
    assert await c.classify("/run now", "slack") == "chat_command"


@pytest.mark.asyncio
async def test_classify_urgent_keyword():
    c = NLIntentClassifier()
    assert await c.classify("urgent: server down", "teams") == "chat_keyword"


@pytest.mark.asyncio
async def test_classify_incident_keyword():
    c = NLIntentClassifier()
    result = await c.classify("P1 incident in production", "slack")
    assert result == "chat_keyword"


@pytest.mark.asyncio
async def test_classify_generic_fallback():
    c = NLIntentClassifier()
    result = await c.classify("Hello, how are you?", "slack")
    assert result == "chat_keyword"  # default fallback


# ── ChannelIngestionGateway ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gateway_no_store_returns_empty():
    gw = ChannelIngestionGateway()
    result = await gw.ingest("slack", {}, tenant_id="t1")
    assert result == []


@pytest.mark.asyncio
async def test_gateway_routes_slack():
    from unittest.mock import AsyncMock

    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.CHAT_COMMAND)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Run command")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))
    gw = ChannelIngestionGateway(trigger_store=store, dispatcher=mock_dispatcher)
    await gw.ingest("slack", {"command": "/run", "user_id": "U123"}, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_gateway_routes_email():
    from unittest.mock import AsyncMock

    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.EMAIL_INTENT)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Handle email")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    gw = ChannelIngestionGateway(trigger_store=store, dispatcher=mock_dispatcher)
    await gw.ingest("email", {"from": "user@example.com", "subject": "Urgent"}, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_gateway_unknown_channel_graceful():
    from app.triggers.store import ScheduleStore
    store = ScheduleStore()
    gw = ChannelIngestionGateway(trigger_store=store)
    # Unknown channel type — should not raise
    result = await gw.ingest("unknown_channel", {}, tenant_id="t1")
    assert isinstance(result, list)


# ── Channel Ingestion API ─────────────────────────────────────────────────────

@pytest.fixture
def channel_app():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None

    @app.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    return app


@pytest.fixture
def channel_client(channel_app):
    return TestClient(channel_app)


# The inbound routes authenticate the caller themselves (fail-closed); these
# used to accept unsigned Slack/Teams/Discord posts and a spoofed X-Tenant-ID.
# Positive signature paths are covered in tests/api/test_channel_webhook_auth.py.


def test_slack_url_verification_requires_signature(channel_client):
    resp = channel_client.post(
        "/channels/slack/events",
        json={"type": "url_verification", "challenge": "test_challenge_123"},
        headers={"X-Slack-Signature": "", "X-Slack-Request-Timestamp": ""},
    )
    assert resp.status_code in (401, 503)


def test_slack_url_verification_signed(channel_app):
    import hashlib
    import hmac
    import json
    import time

    channel_app.state.slack_signing_secret = "shhh"
    body = json.dumps({"type": "url_verification", "challenge": "test_challenge_123"}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"shhh", f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    resp = TestClient(channel_app).post(
        "/channels/slack/events",
        content=body,
        headers={
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "test_challenge_123"


def test_slack_event_unsigned_rejected(channel_app):
    channel_app.state.slack_signing_secret = "shhh"
    resp = TestClient(channel_app).post(
        "/channels/slack/events",
        json={"type": "event_callback", "team_id": "T123", "event": {"type": "message"}},
        headers={"X-Slack-Signature": "", "X-Slack-Request-Timestamp": ""},
    )
    assert resp.status_code == 401


def test_teams_event_unauthenticated_rejected(channel_client):
    resp = channel_client.post(
        "/channels/teams/events",
        json={"type": "message", "serviceUrl": "https://teams.microsoft.com"},
    )
    assert resp.status_code == 401


def test_discord_ping_unsigned_rejected(channel_client):
    resp = channel_client.post("/channels/discord/events", json={"type": 1})
    assert resp.status_code == 401


def test_discord_interaction_unsigned_rejected(channel_client):
    resp = channel_client.post("/channels/discord/events", json={"type": 2, "guild_id": "G123"})
    assert resp.status_code == 401


def test_voice_transcript_requires_tenant_header(channel_app):
    channel_app.state.channel_webhook_secrets = {"voice": "s"}
    resp = TestClient(channel_app).post(
        "/channels/voice/transcript",
        json={"transcript": "Meeting ended"},
        headers={"X-Webhook-Secret": "s"},
    )
    assert resp.status_code == 401


def test_voice_transcript_with_tenant(channel_app):
    channel_app.state.channel_webhook_secrets = {"voice": "s"}
    resp = TestClient(channel_app).post(
        "/channels/voice/transcript",
        json={"transcript": "Meeting ended"},
        headers={"X-Tenant-ID": "t1", "X-Webhook-Secret": "s"},
    )
    assert resp.status_code == 200


def test_voice_transcript_spoofed_tenant_without_secret_rejected(channel_client):
    resp = channel_client.post(
        "/channels/voice/transcript",
        json={"transcript": "Meeting ended"},
        headers={"X-Tenant-ID": "t1"},
    )
    assert resp.status_code in (401, 503)


def test_form_submission_requires_tenant(channel_app):
    channel_app.state.channel_webhook_secrets = {"form": "s"}
    resp = TestClient(channel_app).post(
        "/channels/forms/form-123",
        json={"field": "value"},
        headers={"X-Webhook-Secret": "s"},
    )
    assert resp.status_code == 401


def test_channel_mapping_list_empty(channel_client):
    resp = channel_client.get("/channels/mappings")
    assert resp.status_code == 200
    assert resp.json() == []
