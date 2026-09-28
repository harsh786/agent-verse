"""Shared helpers for gateway webhook tests.

The channel webhooks now fail CLOSED (unconfigured → 503, bad credential → 401),
so tests that exercise the happy path must configure a secret and sign their
requests the way the real platform does. ``signed_channels`` configures every
channel secret; ``SignedClient`` signs each JSON POST for every channel at once
(explicit ``headers=`` still win, so negative tests can send bad credentials).
"""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

TG_SECRET = "tg-test-secret"
WA_SECRET = "wa-test-secret"
SLACK_SECRET = "slack-test-secret"
WEBHOOK_SECRET = "webhook-test-secret"


@pytest.fixture
def signed_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import router as gw

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", TG_SECRET)
    monkeypatch.setattr(gw._whatsapp, "_app_secret", WA_SECRET)
    monkeypatch.setattr(gw._slack, "_signing_secret", SLACK_SECRET)
    monkeypatch.setattr(gw._webhook, "_secret", WEBHOOK_SECRET)


def _hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class SignedClient(TestClient):
    def post(self, url: Any, *, json: Any = None, headers: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if json is None:
            return super().post(url, headers=headers, **kw)
        body = _json.dumps(json, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        signed = {
            "content-type": "application/json",
            "x-telegram-bot-api-secret-token": TG_SECRET,
            "x-hub-signature-256": "sha256=" + _hmac_hex(WA_SECRET, body),
            "x-webhook-signature": "sha256=" + _hmac_hex(WEBHOOK_SECRET, body),
            "x-slack-request-timestamp": ts,
            "x-slack-signature": "v0="
            + _hmac_hex(SLACK_SECRET, b"v0:" + ts.encode() + b":" + body),
            **dict(headers or {}),
        }
        return super().post(url, content=body, headers=signed, **kw)
