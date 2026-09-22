"""Deeper endpoint-logic coverage for app.api.channels.ingestion: tenant
resolution via DB, HMAC-verified Slack signatures, per-channel webhook-secret
verification, chat-event publishing, and the channel-mappings CRUD DB paths.

tests/triggers/channels/test_ingestion.py already covers the gateway/
NLIntentClassifier unit behavior and the basic no-DB endpoint shapes; this
file exercises the branches that only fire when a DB or Redis backend is
wired onto app.state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels.ingestion import (
    _channel_verified,
    _emit_chat_event,
    _resolve_tenant_from_channel,
    router,
)


# ── _resolve_tenant_from_channel ────────────────────────────────────────


class _FakeRow:
    def __init__(self, value):
        self._value = value

    def __getitem__(self, idx):
        return self._value[idx]


def _fake_db(row_value):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def fake_execute(query, params=None):
        result = MagicMock()
        result.fetchone = MagicMock(
            return_value=_FakeRow(row_value) if row_value is not None else None
        )
        return result

    session.execute = AsyncMock(side_effect=fake_execute)

    def db():
        return session

    return db


@pytest.mark.asyncio
async def test_resolve_tenant_none_db_returns_none():
    assert await _resolve_tenant_from_channel("slack", "T1", None) is None


@pytest.mark.asyncio
async def test_resolve_tenant_found():
    db = _fake_db(("tenant-abc",))
    result = await _resolve_tenant_from_channel("slack", "T1", db)
    assert result == "tenant-abc"


@pytest.mark.asyncio
async def test_resolve_tenant_not_found_returns_none():
    db = _fake_db(None)
    result = await _resolve_tenant_from_channel("slack", "T1", db)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_tenant_db_error_returns_none():
    def db():
        raise RuntimeError("db down")

    result = await _resolve_tenant_from_channel("slack", "T1", db)
    assert result is None


# ── _channel_verified ────────────────────────────────────────────────────


def test_channel_verified_slack_uses_slack_ok_flag():
    request = MagicMock()
    assert _channel_verified(request, "slack", slack_ok=True) is True
    assert _channel_verified(request, "slack", slack_ok=False) is False


def test_channel_verified_no_secret_configured_is_unverified():
    request = MagicMock()
    request.app.state = SimpleNamespace(channel_webhook_secrets=None)
    request.headers = {}
    assert _channel_verified(request, "teams") is False


def test_channel_verified_matching_secret_is_verified():
    request = MagicMock()
    request.app.state = SimpleNamespace(channel_webhook_secrets={"teams": "s3cret"})
    request.headers = {"X-Webhook-Secret": "s3cret"}
    assert _channel_verified(request, "teams") is True


def test_channel_verified_mismatched_secret_is_unverified():
    request = MagicMock()
    request.app.state = SimpleNamespace(channel_webhook_secrets={"teams": "s3cret"})
    request.headers = {"X-Webhook-Secret": "wrong"}
    assert _channel_verified(request, "teams") is False


# ── _emit_chat_event ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_chat_event_no_redis_is_noop():
    request = MagicMock()
    request.app.state = SimpleNamespace(trigger_event_redis=None)
    # Must not raise even though verified=True — redis missing short-circuits.
    await _emit_chat_event(request, "slack", {}, "t1", verified=True)


@pytest.mark.asyncio
async def test_emit_chat_event_unverified_is_noop(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.triggers.consumers.conversational.publish_conversational_event", publish
    )
    request = MagicMock()
    request.app.state = SimpleNamespace(trigger_event_redis=MagicMock())
    await _emit_chat_event(request, "slack", {}, "t1", verified=False)
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_emit_chat_event_publishes_when_verified(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.triggers.consumers.conversational.publish_conversational_event", publish
    )
    redis = MagicMock()
    request = MagicMock()
    request.app.state = SimpleNamespace(trigger_event_redis=redis)
    await _emit_chat_event(
        request, "slack", {"text": "hi"}, "t1", verified=True
    )
    publish.assert_awaited_once()
    _, kwargs = publish.call_args
    assert kwargs["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_emit_chat_event_publish_failure_is_swallowed(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(
        "app.triggers.consumers.conversational.publish_conversational_event", boom
    )
    redis = MagicMock()
    request = MagicMock()
    request.app.state = SimpleNamespace(trigger_event_redis=redis)
    # Must not raise — ingestion never breaks on a publish failure.
    await _emit_chat_event(request, "slack", {}, "t1", verified=True)


# ── Slack HMAC signature verification (full app) ────────────────────────


@pytest.fixture
def app_with_secret():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None
    app.state.slack_signing_secret = "shhh"
    app.state.trigger_event_redis = None

    @app.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    return TestClient(app)


def test_slack_event_valid_signature_is_accepted(app_with_secret):
    body = json.dumps({"type": "event_callback", "team_id": "T1"}).encode()
    ts = "12345"
    basestring = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(b"shhh", basestring.encode(), hashlib.sha256).hexdigest()
    resp = app_with_secret.post(
        "/channels/slack/events",
        content=body,
        headers={
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_slack_event_invalid_signature_rejected(app_with_secret):
    body = json.dumps({"type": "event_callback", "team_id": "T1"}).encode()
    resp = app_with_secret.post(
        "/channels/slack/events",
        content=body,
        headers={
            "X-Slack-Signature": "v0=deadbeef",
            "X-Slack-Request-Timestamp": "12345",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ── gateway.ingest called when tenant resolves ──────────────────────────


@pytest.fixture
def app_with_gateway_and_db():
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    db = _fake_db(("tenant-xyz",))
    app.state.db = db

    @app.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="tenant-xyz", plan="free")
        return await call_next(req)

    return app, gateway


def test_teams_event_with_resolvable_tenant_calls_gateway(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/teams/events",
        json={"type": "message", "serviceUrl": "https://teams.microsoft.com"},
    )
    assert resp.status_code == 200
    gateway.ingest.assert_awaited_once()
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "tenant-xyz"


def test_discord_interaction_with_resolvable_tenant_calls_gateway(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/discord/events", json={"type": 2, "guild_id": "G1"}
    )
    assert resp.status_code == 200
    assert resp.json()["type"] == 5
    gateway.ingest.assert_awaited_once()


def test_email_inbound_full_flow(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/email/inbound",
        data={"to": "support@t1.example.com", "from": "a@b.com", "subject": "Hi", "text": "body"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    gateway.ingest.assert_awaited_once()
    channel, body = gateway.ingest.call_args.args
    assert channel == "email"
    assert body["subject"] == "Hi"


def test_sms_inbound_full_flow(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/sms/inbound",
        data={"To": "+15550001111", "From": "+15551112222", "Body": "hello", "MessageSid": "SM1"},
    )
    assert resp.status_code == 200
    assert "<Response>" in resp.text
    gateway.ingest.assert_awaited_once()


def test_meeting_ended_full_flow(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/meeting/ended",
        json={"account_id": "acct-1", "summary": "notes"},
    )
    assert resp.status_code == 200
    gateway.ingest.assert_awaited_once()


def test_slack_event_with_resolvable_tenant_calls_gateway(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/slack/events",
        json={"type": "event_callback", "team_id": "T1", "event": {"type": "message"}},
        headers={"X-Slack-Signature": "", "X-Slack-Request-Timestamp": ""},
    )
    assert resp.status_code == 200
    gateway.ingest.assert_awaited_once()
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "tenant-xyz"


def test_slack_event_unresolvable_tenant_logs_warning_and_skips_gateway():
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.db = None  # no mapping -> tenant unresolvable
    client = TestClient(app)
    resp = client.post(
        "/channels/slack/events",
        json={"type": "event_callback", "team_id": "unknown-team"},
        headers={"X-Slack-Signature": "", "X-Slack-Request-Timestamp": ""},
    )
    assert resp.status_code == 200
    gateway.ingest.assert_not_called()


def test_voice_transcript_calls_gateway_when_present(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/voice/transcript",
        json={"transcript": "hello"},
        headers={"X-Tenant-ID": "t1"},
    )
    assert resp.status_code == 200
    gateway.ingest.assert_awaited_once()
    channel, body = gateway.ingest.call_args.args
    assert channel == "voice"


def test_meeting_ended_unresolvable_tenant_401():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None
    app.state.trigger_event_redis = None
    client = TestClient(app)
    resp = client.post("/channels/meeting/ended", json={"account_id": "unknown"})
    assert resp.status_code == 401


def test_form_submission_resolves_tenant_from_db(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post("/channels/forms/form-1", json={"field": "value"})
    assert resp.status_code == 200
    gateway.ingest.assert_awaited_once()
    channel, body = gateway.ingest.call_args.args
    assert body["form_id"] == "form-1"


def test_form_submission_header_tenant_takes_priority(app_with_gateway_and_db):
    app, gateway = app_with_gateway_and_db
    client = TestClient(app)
    resp = client.post(
        "/channels/forms/form-1",
        json={"field": "value"},
        headers={"X-Tenant-ID": "header-tenant"},
    )
    assert resp.status_code == 200
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "header-tenant"


# ── channel mappings CRUD, DB present ────────────────────────────────────


def _mapping_app(db):
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = db

    @app.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    return TestClient(app)


def test_create_channel_mapping_no_db_falls_back_to_in_memory():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None

    @app.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    client = TestClient(app)
    resp = client.post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T1"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "mapped"


def test_create_channel_mapping_requires_auth():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None
    client = TestClient(app)
    resp = client.post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T1"}
    )
    assert resp.status_code == 401


def test_create_channel_mapping_persists_to_db():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(return_value=MagicMock())

    def db():
        return session

    client = _mapping_app(db)
    resp = client.post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T99"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "mapped"}
    session.execute.assert_awaited_once()


def test_create_channel_mapping_db_error_returns_500():
    def db():
        raise RuntimeError("db down")

    client = _mapping_app(db)
    resp = client.post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T99"}
    )
    assert resp.status_code == 500


def test_list_channel_mappings_requires_auth():
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = None
    client = TestClient(app)
    resp = client.get("/channels/mappings")
    assert resp.status_code == 401


def test_list_channel_mappings_returns_rows():
    row = MagicMock()
    row._mapping = {
        "id": "m1", "channel_type": "slack", "channel_id": "T1", "created_at": "2026-01-01",
    }
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=[row])

    def db():
        return session

    client = _mapping_app(db)
    resp = client.get("/channels/mappings")
    assert resp.status_code == 200
    assert resp.json() == [
        {"id": "m1", "channel_type": "slack", "channel_id": "T1", "created_at": "2026-01-01"}
    ]


def test_list_channel_mappings_db_error_returns_empty_list():
    def db():
        raise RuntimeError("db down")

    client = _mapping_app(db)
    resp = client.get("/channels/mappings")
    assert resp.status_code == 200
    assert resp.json() == []
