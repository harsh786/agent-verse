"""TRG-24: Slack and Teams typed webhooks verify the vendors' real signatures.

The endpoint read ``x-signature`` as hex HMAC(body). Slack signs
``v0:{timestamp}:{body}`` into ``X-Slack-Signature`` (with
``X-Slack-Request-Timestamp``) and Teams outgoing webhooks send
``Authorization: HMAC <base64 HMAC-SHA256(base64-decoded secret, body)>``, so every
signed delivery was a 401 — and Slack's ``url_verification`` challenge was never
echoed, so the Events API URL could not even be saved.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.triggers.webhooks.verifier import WebhookSignatureVerifier
from tests.triggers.test_typed_webhook_tenant_boundary import (
    TOK,
    _app,
    _Dispatcher,
    _spec,
    _Store,
)

SLACK_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
TEAMS_SECRET = base64.b64encode(b"teams-shared-secret-bytes-0123456").decode()


def _slack_headers(body: bytes, *, secret: str = SLACK_SECRET, ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    base = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": str(ts),
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }


def _now_iso() -> str:
    # Teams activities carry a signed timestamp; DEF-5 requires it to be fresh.
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _teams_headers(body: bytes, *, secret: str = TEAMS_SECRET) -> dict:
    mac = hmac.new(base64.b64decode(secret), body, hashlib.sha256).digest()
    return {"Authorization": "HMAC " + base64.b64encode(mac).decode()}


# ── Slack ──────────────────────────────────────────────────────────────────────


def test_signed_slack_event_is_accepted() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK, SLACK_SECRET)]}), disp, caller=None)
    body = json.dumps({"type": "event_callback", "event": {"type": "message"}}).encode()

    r = client.post(f"/triggers/webhooks/slack/{TOK}", content=body, headers=_slack_headers(body))

    assert r.status_code == 200, r.text
    assert disp.fired == [("t1", TOK)]


def test_tampered_or_stale_slack_event_is_rejected() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK, SLACK_SECRET)]}), disp, caller=None)
    body = json.dumps({"type": "event_callback"}).encode()

    tampered = client.post(
        f"/triggers/webhooks/slack/{TOK}",
        content=body + b" ",
        headers=_slack_headers(body),
    )
    stale = client.post(
        f"/triggers/webhooks/slack/{TOK}",
        content=body,
        headers=_slack_headers(body, ts=int(time.time()) - 3600),
    )

    assert tampered.status_code == 401
    assert stale.status_code == 401
    assert disp.fired == []


def test_slack_url_verification_challenge_is_echoed_without_firing() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK, SLACK_SECRET)]}), disp, caller=None)
    body = json.dumps({"type": "url_verification", "challenge": "3eZbrw1aB"}).encode()

    r = client.post(f"/triggers/webhooks/slack/{TOK}", content=body, headers=_slack_headers(body))

    assert r.status_code == 200, r.text
    assert r.json() == {"challenge": "3eZbrw1aB"}
    assert disp.fired == []


def test_unsigned_slack_challenge_is_rejected_when_a_secret_is_set() -> None:
    client = _app(_Store({"t1": [_spec(TOK, SLACK_SECRET)]}), _Dispatcher(), caller=None)
    r = client.post(
        f"/triggers/webhooks/slack/{TOK}",
        json={"type": "url_verification", "challenge": "x"},
    )
    assert r.status_code == 401


# ── Teams ──────────────────────────────────────────────────────────────────────


def test_signed_teams_message_is_accepted() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK, TEAMS_SECRET)]}), disp, caller=None)
    body = json.dumps(
        {"type": "message", "text": "<at>Bot</at> deploy", "timestamp": _now_iso()}
    ).encode()

    r = client.post(
        f"/triggers/webhooks/teams/{TOK}",
        content=body,
        headers={**_teams_headers(body), "Content-Type": "application/json"},
    )

    assert r.status_code == 200, r.text
    assert disp.fired == [("t1", TOK)]


def test_tampered_teams_message_is_rejected() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK, TEAMS_SECRET)]}), disp, caller=None)
    body = json.dumps({"type": "message", "text": "hi"}).encode()

    r = client.post(
        f"/triggers/webhooks/teams/{TOK}",
        content=b'{"type": "message", "text": "rm -rf"}',
        headers=_teams_headers(body),
    )

    assert r.status_code == 401
    assert disp.fired == []


# ── Verifier units ─────────────────────────────────────────────────────────────


def test_verifier_units() -> None:
    v = WebhookSignatureVerifier()
    body = json.dumps({"a": 1, "timestamp": _now_iso()}).encode()
    h: Any = _slack_headers(body)
    assert v.verify_slack(
        body, h["X-Slack-Signature"], h["X-Slack-Request-Timestamp"], SLACK_SECRET
    )
    assert not v.verify_slack(body, h["X-Slack-Signature"], "not-a-ts", SLACK_SECRET)
    assert not v.verify_slack(body, h["X-Slack-Signature"], h["X-Slack-Request-Timestamp"], "")
    auth = _teams_headers(body)["Authorization"]
    assert v.verify_teams(body, auth, TEAMS_SECRET)
    assert not v.verify_teams(body, auth.replace("HMAC ", ""), TEAMS_SECRET)
    assert not v.verify_teams(body, auth, "not base64 !!")
