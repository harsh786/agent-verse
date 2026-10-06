"""Regression: gateway chat-platform webhooks must fail CLOSED.

These routes sit in TenantMiddleware's public bypass list, so the channel secret
is their only authentication. Telegram accepted ANY request when
TELEGRAM_WEBHOOK_SECRET was unset (``verify_auth`` returned True), and WhatsApp /
Slack / the generic webhook did the same when their secret was empty — anyone
who knew an org id could inject commands. Now: unconfigured → 503, bad or
missing credential → 401 (mirrors app/api/channels/ingestion.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import router as gw
from app.gateway.channel_registry import ChannelRegistry

_TG_MSG = {"message": {"chat": {"id": "1"}, "from": {"id": "2"}, "text": "hi"}}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
    monkeypatch.setattr(gw._whatsapp, "_app_secret", "")
    monkeypatch.setattr(gw._slack, "_signing_secret", "")
    monkeypatch.setattr(gw._webhook, "_secret", "")
    monkeypatch.setattr(gw, "_process_command", AsyncMock())
    app = FastAPI()
    app.include_router(gw.router)
    app.state.chat_service = object()
    reg = ChannelRegistry()
    reg.register("telegram", "bot-1", "tenant-a")
    app.state.channel_registry = reg
    return TestClient(app)


# ── Telegram ─────────────────────────────────────────────────────────────────


def test_telegram_unset_secret_is_503(client: TestClient) -> None:
    r = client.post("/v1/gateway/org1/telegram/webhook", json=_TG_MSG)
    assert r.status_code == 503
    gw._process_command.assert_not_awaited()  # type: ignore[attr-defined]


def test_telegram_wrong_or_missing_token_is_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    bad = client.post(
        "/v1/gateway/org1/telegram/webhook",
        json=_TG_MSG,
        headers={"x-telegram-bot-api-secret-token": "nope"},
    )
    missing = client.post("/v1/gateway/org1/telegram/webhook", json=_TG_MSG)
    assert bad.status_code == 401
    assert missing.status_code == 401


def test_telegram_correct_token_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    r = client.post(
        "/v1/gateway/org1/telegram/webhook",
        json=_TG_MSG,
        headers={"x-telegram-bot-api-secret-token": "s3cret"},
    )
    assert r.status_code == 200


def test_telegram_chat_route_unset_secret_is_503(client: TestClient) -> None:
    r = client.post("/v1/gateway/telegram/chat", json={"addressee": "bot-1", **_TG_MSG})
    assert r.status_code == 503


# ── WhatsApp / Slack / generic ───────────────────────────────────────────────


def test_whatsapp_unset_secret_is_503(client: TestClient) -> None:
    r = client.post("/v1/gateway/org1/whatsapp/webhook", json={"entry": []})
    assert r.status_code == 503


def test_whatsapp_bad_signature_is_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gw._whatsapp, "_app_secret", "shh")
    r = client.post(
        "/v1/gateway/org1/whatsapp/webhook",
        json={"entry": []},
        headers={"x-hub-signature-256": "sha256=bad"},
    )
    assert r.status_code == 401


def test_whatsapp_verify_unset_token_is_503(client: TestClient) -> None:
    # An unset verify token used to match an empty hub_verify_token ("" == "").
    r = client.get(
        "/v1/gateway/org1/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "5"},
    )
    assert r.status_code == 503


def test_slack_unset_secret_is_503(client: TestClient) -> None:
    r = client.post("/v1/gateway/org1/slack/events", json={"event": {"text": "hi"}})
    assert r.status_code == 503


def test_generic_webhook_unset_secret_is_503(client: TestClient) -> None:
    r = client.post("/v1/gateway/org1/webhook", json={"command": "do it"})
    assert r.status_code == 503
    gw._process_command.assert_not_awaited()  # type: ignore[attr-defined]


def test_generic_webhook_bad_signature_is_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gw._webhook, "_secret", "shh")
    r = client.post(
        "/v1/gateway/org1/webhook",
        json={"command": "do it"},
        headers={"x-webhook-signature": "sha256=bad"},
    )
    assert r.status_code == 401


def test_generic_webhook_valid_signature_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gw._webhook, "_secret", "shh")
    body = json.dumps({"command": "do it"}).encode()
    sig = "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/v1/gateway/org1/webhook",
        content=body,
        headers={"content-type": "application/json", "x-webhook-signature": sig},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_adapters_verify_auth_fail_closed_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(gw._whatsapp, "_app_secret", "")
    monkeypatch.setattr(gw._slack, "_signing_secret", "")
    monkeypatch.setattr(gw._webhook, "_secret", "")
    for adapter in (gw._telegram, gw._whatsapp, gw._slack, gw._webhook):
        assert adapter.is_configured is False
        assert await adapter.verify_auth({}, {}, raw_body=b"{}") is False
