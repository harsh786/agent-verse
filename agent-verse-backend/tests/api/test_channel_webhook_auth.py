"""Inbound ``/channels/*`` webhooks authenticate the caller before doing anything.

Regression: these endpoints are called by Slack / Teams / Discord / SendGrid /
Twilio / form + meeting providers, so they sit in TenantMiddleware's bypass list
and must carry their own auth. They did not:

* Slack verified its HMAC only when a signing secret was configured AND the
  request carried a signature header — omit the header and anything was
  ingested. The ``url_verification`` challenge was answered before any check.
* Teams, Discord, email, SMS, voice, forms and meeting-ended verified nothing.
  Teams / voice / forms / meeting even took the tenant from a caller-supplied
  ``X-Tenant-ID`` header, so anyone could inject messages into any tenant.

Every channel now fails closed: unconfigured → 503, bad/missing credential → 401,
and the tenant comes from the verified channel mapping (or, for the shared-
secret relay channels, from ``X-Tenant-ID`` only after the secret matched).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels.ingestion import router

_SECRET_CHANNELS = ["email", "sms", "voice", "form", "meeting"]


def _app(**state: Any) -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.db = None
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False), gateway


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SLACK_SIGNING_SECRET",
        "DISCORD_PUBLIC_KEY",
        "TEAMS_APP_ID",
        *(f"CHANNEL_WEBHOOK_SECRET_{c.upper()}" for c in _SECRET_CHANNELS),
    ):
        monkeypatch.delenv(var, raising=False)


# ── Slack ────────────────────────────────────────────────────────────────


def _slack_headers(secret: str, body: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Slack-Signature": sig,
        "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/json",
    }


def test_slack_unconfigured_is_503() -> None:
    client, gateway = _app()
    r = client.post("/channels/slack/events", json={"type": "event_callback"})
    assert r.status_code == 503
    gateway.ingest.assert_not_called()


def test_slack_missing_signature_is_401_not_ingested() -> None:
    client, gateway = _app(slack_signing_secret="shhh")
    r = client.post("/channels/slack/events", json={"type": "event_callback", "team_id": "T1"})
    assert r.status_code == 401
    gateway.ingest.assert_not_called()


def test_slack_url_verification_requires_signature() -> None:
    client, _ = _app(slack_signing_secret="shhh")
    r = client.post("/channels/slack/events", json={"type": "url_verification", "challenge": "c"})
    assert r.status_code == 401
    body = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
    ok = client.post("/channels/slack/events", content=body, headers=_slack_headers("shhh", body))
    assert ok.status_code == 200 and ok.json() == {"challenge": "c"}


def test_slack_stale_timestamp_is_rejected() -> None:
    client, _ = _app(slack_signing_secret="shhh")
    body = json.dumps({"type": "event_callback"}).encode()
    stale = str(int(time.time()) - 3600)
    r = client.post(
        "/channels/slack/events", content=body, headers=_slack_headers("shhh", body, stale)
    )
    assert r.status_code == 401


def test_slack_signing_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "envsecret")
    client, _ = _app()
    body = json.dumps({"type": "event_callback", "team_id": "T1"}).encode()
    r = client.post(
        "/channels/slack/events", content=body, headers=_slack_headers("envsecret", body)
    )
    assert r.status_code == 200


# ── Teams (Bot Framework JWT) ────────────────────────────────────────────


def test_teams_without_valid_bot_framework_token_is_401() -> None:
    client, gateway = _app()
    r = client.post(
        "/channels/teams/events",
        json={"type": "message", "serviceUrl": "https://x"},
        headers={"X-Tenant-ID": "victim", "Authorization": "Bearer " + "a" * 40},
    )
    assert r.status_code == 401
    gateway.ingest.assert_not_called()


def test_teams_ignores_x_tenant_id_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.channels.teams import MicrosoftTeamsAdapter as TeamsAdapter

    monkeypatch.setattr(TeamsAdapter, "verify_auth", AsyncMock(return_value=True))
    client, gateway = _app()
    r = client.post(
        "/channels/teams/events",
        json={"type": "message", "serviceUrl": "https://x"},
        headers={"X-Tenant-ID": "victim", "Authorization": "Bearer tok"},
    )
    assert r.status_code == 200
    # No channel mapping resolves → the header must NOT pick the tenant.
    gateway.ingest.assert_not_called()


# ── Discord (Ed25519) ────────────────────────────────────────────────────


def test_discord_unsigned_is_401() -> None:
    client, gateway = _app()
    assert client.post("/channels/discord/events", json={"type": 1}).status_code == 401
    r = client.post("/channels/discord/events", json={"type": 2, "guild_id": "G1"})
    assert r.status_code == 401
    gateway.ingest.assert_not_called()


def test_discord_valid_signature_answers_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", pub)
    client, _ = _app()
    body = json.dumps({"type": 1}).encode()
    ts = str(int(time.time()))
    sig = key.sign(ts.encode() + body).hex()
    r = client.post(
        "/channels/discord/events",
        content=body,
        headers={
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200 and r.json() == {"type": 1}


# ── Shared-secret relay channels (email / sms / voice / forms / meeting) ─


_SECRET_ROUTES = [
    ("email", "/channels/email/inbound", {"data": {"to": "a@b.c", "from": "x@y.z"}}),
    ("sms", "/channels/sms/inbound", {"data": {"To": "+1", "From": "+2", "Body": "hi"}}),
    ("voice", "/channels/voice/transcript", {"json": {"transcript": "hi"}}),
    ("form", "/channels/forms/form-1", {"json": {"field": "v"}}),
    ("meeting", "/channels/meeting/ended", {"json": {"account_id": "acct"}}),
]


@pytest.mark.parametrize(("channel", "path", "kwargs"), _SECRET_ROUTES)
def test_secret_channel_unconfigured_is_503(channel: str, path: str, kwargs: Any) -> None:
    client, gateway = _app()
    r = client.post(path, headers={"X-Tenant-ID": "victim"}, **kwargs)
    assert r.status_code == 503, r.text
    gateway.ingest.assert_not_called()


@pytest.mark.parametrize(("channel", "path", "kwargs"), _SECRET_ROUTES)
def test_secret_channel_wrong_secret_is_401(
    channel: str, path: str, kwargs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"CHANNEL_WEBHOOK_SECRET_{channel.upper()}", "right")
    client, gateway = _app()
    for headers in ({"X-Tenant-ID": "victim"}, {"X-Tenant-ID": "victim", "X-Webhook-Secret": "x"}):
        r = client.post(path, headers=headers, **kwargs)
        assert r.status_code == 401, r.text
    gateway.ingest.assert_not_called()


@pytest.mark.parametrize(("channel", "path", "kwargs"), _SECRET_ROUTES)
def test_secret_channel_correct_secret_is_accepted(
    channel: str, path: str, kwargs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"CHANNEL_WEBHOOK_SECRET_{channel.upper()}", "right")
    client, gateway = _app()
    r = client.post(path, headers={"X-Tenant-ID": "t1", "X-Webhook-Secret": "right"}, **kwargs)
    assert r.status_code == 200, r.text
    gateway.ingest.assert_awaited_once()
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "t1"


def test_secret_channel_accepts_basic_auth_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twilio / SendGrid cannot add headers but can put credentials in the URL."""
    monkeypatch.setenv("CHANNEL_WEBHOOK_SECRET_SMS", "right")
    client, _ = _app()
    r = client.post(
        "/channels/sms/inbound",
        data={"To": "+1", "From": "+2", "Body": "hi"},
        auth=("twilio", "right"),
    )
    assert r.status_code == 200
