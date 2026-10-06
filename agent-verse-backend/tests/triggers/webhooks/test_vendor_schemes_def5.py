"""DEF-5: GitHub, Stripe, Jira and Teams typed webhooks verify each vendor's
signature scheme EXACTLY, enforce the replay window where the vendor signs a
time, and collapse a replayed delivery onto one firing.

Replay protection per vendor:

* Stripe — ``t=`` is signed; a timestamp outside 5 minutes is refused.
* Teams outgoing webhook — the body (incl. the activity ``timestamp``) is
  signed; a stale activity is refused.
* Jira Connect — the JWT ``exp`` (short-lived) bounds replays; ``qsh`` binds
  the token to this request.
* GitHub / Jira Cloud — only the body is signed. The delivery id headers
  (``X-GitHub-Delivery``, ``X-Atlassian-Webhook-Identifier``) are NOT signed,
  so dedup keys on the signed body: a replay with a forged new delivery id still
  produces the original idempotency key (Redis + ``trigger_events`` dedup).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import pytest
from jose import jwt as jose_jwt

from app.triggers.models import TriggerType
from app.triggers.webhooks.verifier import WebhookSignatureVerifier, atlassian_qsh
from tests.triggers.webhooks.test_vendor_signed_deliveries import (
    _NOW_MS,
    TOKEN,
    _body,
    _client,
    _post,
)

# Built from parts: no provider-key-shaped literal in the repo.
_SECRET = "-".join(("vendor", "shared", "secret", "def5"))
_STRIPE_SECRET = "_".join(("whsec", "def5", "test", "only"))
_TEAMS_KEY = base64.b64encode(b"teams-outgoing-webhook-key-def5!").decode()


def _hex(secret: str, data: bytes) -> str:
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


# ── GitHub ───────────────────────────────────────────────────────────────────

_PUSH = {
    "ref": "refs/heads/main",
    "before": "6113728f27ae82c7b1a177c8d03f9e96e0adf246",
    "after": "59b20b8d5c6ff8d09518454d4dd8b7b30f095ab5",
    "repository": {"id": 1296269, "full_name": "octo/checkout", "private": True},
    "pusher": {"name": "ada"},
    "sender": {"login": "ada", "id": 1},
    "head_commit": {"id": "59b20b8", "message": "fix: retry payments"},
}


def _github_headers(body: bytes, secret: str = _SECRET, delivery: str | None = None) -> dict:
    return {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "X-GitHub-Hook-ID": "292430182",
        "X-Hub-Signature-256": "sha256=" + _hex(secret, body),
    }


def test_github_signed_push_is_accepted() -> None:
    client, dispatcher = _client(TriggerType.GITHUB_WEBHOOK, _SECRET)
    body = _body(_PUSH)
    r = _post(client, "github", body, _github_headers(body))
    assert r.status_code == 200, r.text
    assert dispatcher.fired[0]["event_type"] == "push"
    assert dispatcher.fired[0]["repo_full_name"] == "octo/checkout"


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(lambda b: "sha1=" + hmac.new(_SECRET.encode(), b, hashlib.sha1).hexdigest(),
                     id="legacy-sha1"),
        pytest.param(lambda b: _hex(_SECRET, b), id="missing-sha256-prefix"),
        pytest.param(lambda b: "sha256=" + _hex("other", b), id="other-secret"),
        pytest.param(lambda b: "", id="unsigned"),
    ],
)
def test_github_signature_must_be_exact(header: Any) -> None:
    client, dispatcher = _client(TriggerType.GITHUB_WEBHOOK, _SECRET)
    body = _body(_PUSH)
    headers = {**_github_headers(body), "X-Hub-Signature-256": header(body)}
    assert _post(client, "github", body, headers).status_code == 401
    assert dispatcher.fired == []


def test_github_replay_with_a_forged_delivery_id_collapses_onto_one_firing() -> None:
    """X-GitHub-Delivery is not signed: keying dedup on it would let a captured
    delivery be replayed under a fresh id. The signed body is the key."""
    client, dispatcher = _client(TriggerType.GITHUB_WEBHOOK, _SECRET)
    body = _body(_PUSH)
    assert _post(client, "github", body, _github_headers(body)).status_code == 200
    assert _post(client, "github", body, _github_headers(body)).status_code == 200
    forged = {**_github_headers(body), "Idempotency-Key": "fresh-1", "X-Request-Id": "r-2"}
    assert _post(client, "github", body, forged).status_code == 200
    assert dispatcher.keys[0] == dispatcher.keys[1] == dispatcher.keys[2]


def test_github_replay_after_the_body_hash_window_still_collapses(monkeypatch) -> None:
    """The generic no-delivery-id identity is windowed (5 min); a vendor-signed
    body has no signed time, so its identity must not expire."""
    from app.triggers.webhooks import ingress

    client, dispatcher = _client(TriggerType.GITHUB_WEBHOOK, _SECRET)
    body = _body(_PUSH)
    headers = {k: v for k, v in _github_headers(body).items() if k != "X-GitHub-Delivery"}
    assert _post(client, "github", body, headers).status_code == 200
    later = time.time() + 3 * ingress.REPLAY_WINDOW_SECONDS
    monkeypatch.setattr(ingress.time, "time", lambda: later)
    assert _post(client, "github", body, headers).status_code == 200
    assert dispatcher.keys[0] == dispatcher.keys[1]


def test_distinct_signed_github_deliveries_are_distinct_firings() -> None:
    client, dispatcher = _client(TriggerType.GITHUB_WEBHOOK, _SECRET)
    one, two = _body(_PUSH), _body({**_PUSH, "after": "0" * 40})
    assert _post(client, "github", one, _github_headers(one)).status_code == 200
    assert _post(client, "github", two, _github_headers(two)).status_code == 200
    assert dispatcher.keys[0] != dispatcher.keys[1]


# ── Stripe ───────────────────────────────────────────────────────────────────

_EVENT = {
    "id": "evt_1PdJ2KLkdIwHu7ix",
    "object": "event",
    "api_version": "2024-06-20",
    "created": 1727690000,
    "type": "payment_intent.payment_failed",
    "livemode": False,
    "data": {"object": {"id": "pi_3PdJ2K", "object": "payment_intent", "amount": 4999,
                        "currency": "usd", "status": "requires_payment_method"}},
}


def _stripe_header(body: bytes, *, secret: str = _STRIPE_SECRET, t: int | None = None,
                   extra_v1: str = "") -> dict:
    ts = int(time.time()) if t is None else t
    sig = _hex(secret, f"{ts}.".encode() + body)
    v1 = f"v1={sig}" + (f",v1={extra_v1}" if extra_v1 else "")
    return {"Stripe-Signature": f"t={ts},{v1},v0=deadbeef"}


def test_stripe_signed_event_is_accepted_including_during_a_secret_roll() -> None:
    client, dispatcher = _client(TriggerType.STRIPE_WEBHOOK, _STRIPE_SECRET)
    body = _body(_EVENT)
    assert _post(client, "stripe", body, _stripe_header(body)).status_code == 200
    rolled = _stripe_header(body, extra_v1=_hex("old-secret", b"x"))
    assert _post(client, "stripe", body, rolled).status_code == 200
    assert dispatcher.fired[0]["event_id"] == "evt_1PdJ2KLkdIwHu7ix"


def test_stripe_outside_the_tolerance_is_a_replay_and_refused() -> None:
    client, dispatcher = _client(TriggerType.STRIPE_WEBHOOK, _STRIPE_SECRET)
    body = _body(_EVENT)
    stale = _stripe_header(body, t=int(time.time()) - 301)
    assert _post(client, "stripe", body, stale).status_code == 401
    assert dispatcher.fired == []


def test_stripe_timestamp_cannot_be_refreshed_without_resigning() -> None:
    client, dispatcher = _client(TriggerType.STRIPE_WEBHOOK, _STRIPE_SECRET)
    body = _body(_EVENT)
    old = _stripe_header(body, t=int(time.time()) - 3600)["Stripe-Signature"]
    fresh_t = old.replace(old.split(",")[0], f"t={int(time.time())}")
    assert _post(client, "stripe", body, {"Stripe-Signature": fresh_t}).status_code == 401
    assert dispatcher.fired == []


# ── Jira ─────────────────────────────────────────────────────────────────────

_ISSUE_CREATED = {
    "timestamp": _NOW_MS,  # stamped by _body: a stale one is refused (DEF-NEW-3)
    "webhookEvent": "jira:issue_created",
    "issue_event_type_name": "issue_created",
    "user": {"accountId": "5b10ac8d82e05b22cc7d4ef5", "displayName": "Ada"},
    "issue": {"id": "10002", "key": "OPS-42",
              "fields": {"summary": "Checkout 500s", "status": {"name": "To Do"},
                         "project": {"key": "OPS"}}},
}


def _jira_hub(body: bytes, secret: str = _SECRET) -> dict:
    return {
        "X-Hub-Signature": "sha256=" + _hex(secret, body),
        "X-Atlassian-Webhook-Identifier": str(uuid.uuid4()),
    }


def test_jira_cloud_signed_webhook_is_accepted() -> None:
    client, dispatcher = _client(TriggerType.JIRA_WEBHOOK, _SECRET)
    body = _body(_ISSUE_CREATED)
    r = _post(client, "jira", body, _jira_hub(body))
    assert r.status_code == 200, r.text
    assert dispatcher.fired[0]["issue_key"] == "OPS-42"


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param(lambda b: _hex(_SECRET, b), id="bare-hex-without-sha256-prefix"),
        pytest.param(lambda b: "md5=" + _hex(_SECRET, b), id="wrong-algorithm-label"),
        pytest.param(lambda b: "sha256=" + _hex("other", b), id="other-secret"),
    ],
)
def test_jira_signature_must_be_exact(signature: Any) -> None:
    client, dispatcher = _client(TriggerType.JIRA_WEBHOOK, _SECRET)
    body = _body(_ISSUE_CREATED)
    headers = {**_jira_hub(body), "X-Hub-Signature": signature(body)}
    assert _post(client, "jira", body, headers).status_code == 401
    assert dispatcher.fired == []


def test_jira_replay_with_a_new_webhook_identifier_collapses_onto_one_firing() -> None:
    client, dispatcher = _client(TriggerType.JIRA_WEBHOOK, _SECRET)
    body = _body(_ISSUE_CREATED)
    assert _post(client, "jira", body, _jira_hub(body)).status_code == 200
    assert _post(client, "jira", body, _jira_hub(body)).status_code == 200
    assert dispatcher.keys[0] == dispatcher.keys[1]


def _connect_jwt(*, secret: str = _SECRET, path: str | None = None, exp_delta: int = 180,
                 alg: str = "HS256") -> str:
    now = int(time.time())
    qsh = atlassian_qsh("POST", path or f"/triggers/webhooks/jira/{TOKEN}", "")
    claims = {"iss": "jira:client-key-1234", "iat": now, "exp": now + exp_delta, "qsh": qsh}
    return jose_jwt.encode(claims, secret, algorithm=alg)


def test_jira_connect_jwt_webhook_is_accepted() -> None:
    client, dispatcher = _client(TriggerType.JIRA_WEBHOOK, _SECRET)
    body = _body(_ISSUE_CREATED)
    r = _post(client, "jira", body, {"Authorization": f"JWT {_connect_jwt()}"})
    assert r.status_code == 200, r.text
    assert dispatcher.fired


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: _connect_jwt(path="/triggers/webhooks/jira/other-token"),
                     id="qsh-for-another-request"),
        pytest.param(lambda: _connect_jwt(exp_delta=-600), id="expired"),
        pytest.param(lambda: _connect_jwt(secret="another-shared-secret"), id="other-secret"),
        pytest.param(lambda: _connect_jwt(alg="HS512"), id="unexpected-alg"),
    ],
)
def test_jira_connect_jwt_refusals(token: Any) -> None:
    client, dispatcher = _client(TriggerType.JIRA_WEBHOOK, _SECRET)
    body = _body(_ISSUE_CREATED)
    assert _post(client, "jira", body, {"Authorization": f"JWT {token()}"}).status_code == 401
    assert dispatcher.fired == []


def test_atlassian_qsh_canonicalisation() -> None:
    # Documented example shape: sorted keys, jwt dropped, RFC 3986 encoding,
    # repeated values sorted and comma-joined, no trailing slash.
    expected = hashlib.sha256(b"GET&/rest/api&a=x%20y&b=1,2").hexdigest()
    assert atlassian_qsh("get", "/rest/api/", "b=2&jwt=abc&a=x+y&b=1") == expected
    assert atlassian_qsh("POST", "", "") == hashlib.sha256(b"POST&/&").hexdigest()


# ── Teams outgoing webhook ───────────────────────────────────────────────────


def _activity(ts: float | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "message",
        "id": "1485983408511",
        "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "from": {"id": "29:1abc", "name": "Ada", "aadObjectId": "a1"},
        "conversation": {"id": "19:abc@thread.skype", "conversationType": "channel"},
        "text": "<at>AgentVerse</at> status of checkout",
        "channelData": {"tenant": {"id": "72f988bf-86f1-41af-91ab-2d7cd011db47"}},
    }
    if ts is not None:
        body["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".3460532Z"
    return body


def _teams_auth(body: bytes, key: str = _TEAMS_KEY) -> dict:
    mac = hmac.new(base64.b64decode(key), body, hashlib.sha256).digest()
    return {"Authorization": "HMAC " + base64.b64encode(mac).decode()}


def test_teams_outgoing_webhook_is_accepted() -> None:
    client, dispatcher = _client(TriggerType.TEAMS_WEBHOOK, _TEAMS_KEY)
    body = _body(_activity(time.time()))
    r = _post(client, "teams", body, _teams_auth(body))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "ts", [pytest.param(time.time() - 3600, id="stale"), pytest.param(None, id="no-timestamp"),
           pytest.param(time.time() + 3600, id="future")],
)
def test_teams_replay_window(ts: float | None) -> None:
    client, dispatcher = _client(TriggerType.TEAMS_WEBHOOK, _TEAMS_KEY)
    body = _body(_activity(ts))
    assert _post(client, "teams", body, _teams_auth(body)).status_code == 401
    assert dispatcher.fired == []


def test_teams_hmac_keyed_by_the_raw_token_text_is_refused() -> None:
    """Teams keys the HMAC with the BASE64-DECODED security token."""
    client, dispatcher = _client(TriggerType.TEAMS_WEBHOOK, _TEAMS_KEY)
    body = _body(_activity(time.time()))
    mac = hmac.new(_TEAMS_KEY.encode(), body, hashlib.sha256).digest()
    headers = {"Authorization": "HMAC " + base64.b64encode(mac).decode()}
    assert _post(client, "teams", body, headers).status_code == 401
    assert dispatcher.fired == []


def test_verifier_parses_teams_seven_digit_fractions() -> None:
    from app.triggers.webhooks.verifier import _parse_iso_timestamp

    assert _parse_iso_timestamp("2026-10-06T10:00:00.3460532Z") == pytest.approx(
        _parse_iso_timestamp("2026-10-06T10:00:00.346053Z")
    )
    assert _parse_iso_timestamp("2026-10-06T10:00:00") is None  # no zone: refused
    assert _parse_iso_timestamp("yesterday") is None
    assert WebhookSignatureVerifier().verify_teams(b"{}", "HMAC x", "") is False
    _ = json  # keep the import for readers of vendor payloads
