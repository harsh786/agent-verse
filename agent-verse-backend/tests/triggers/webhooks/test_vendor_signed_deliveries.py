"""TRG-28: vendor-format signed deliveries for the typed webhook triggers.

Only GitHub and Stripe had tests proving a real, vendor-signed delivery is
accepted. Each case below signs a sample payload exactly the way the vendor
documents (header name + signature format) and proves that

* the correctly signed delivery fires the trigger,
* a tampered body is rejected (401), and
* a replay does not fire twice: vendors that sign a timestamp (Slack, Grafana
  with a timestamp header) reject a stale one; vendors that sign only the body
  (Teams, Salesforce, Confluence, PagerDuty, Jira, ...) produce the SAME
  dispatcher idempotency key for a replayed delivery, so the dispatcher's
  Redis + ``trigger_events`` dedup collapses it onto the original firing.

CloudWatch (AWS SNS) has the same three properties in
``tests/triggers/test_typed_webhook_cloudwatch_sns.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dedup import derive_idempotency_key
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from app.triggers.webhooks.verifier import WebhookSignatureVerifier

TOKEN = "vendor_" + "v" * 40
SECRET = "whsec-vendor-test-secret"
# Teams shows the outgoing-webhook security token base64-encoded.
TEAMS_SECRET = base64.b64encode(b"teams-outgoing-webhook-key-012345").decode()
# DEF-5: a Teams activity's signed timestamp must be fresh (replay window).
_NOW_ISO = time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime())

Signer = Callable[[bytes, str], dict[str, str]]


def _mac(secret: str, data: bytes) -> bytes:
    return hmac.new(secret.encode(), data, hashlib.sha256).digest()


def _hex(body: bytes, secret: str = SECRET) -> str:
    return _mac(secret, body).hex()


def _slack(ts_offset: int = 0) -> Signer:
    def sign(body: bytes, secret: str) -> dict[str, str]:
        ts = str(int(time.time()) + ts_offset)
        return {
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": "v0=" + _mac(secret, f"v0:{ts}:".encode() + body).hex(),
        }

    return sign


def _grafana_ts(ts_offset: int = 0) -> Signer:
    def sign(body: bytes, secret: str) -> dict[str, str]:
        ts = str(int(time.time()) + ts_offset)
        return {
            "X-Grafana-Alerting-Timestamp": ts,
            "X-Grafana-Alerting-Signature": _mac(secret, ts.encode() + b":" + body).hex(),
        }

    return sign


def _teams(body: bytes, secret: str) -> dict[str, str]:
    mac = hmac.new(base64.b64decode(secret), body, hashlib.sha256).digest()
    return {"Authorization": "HMAC " + base64.b64encode(mac).decode()}


_GRAFANA_ALERT = {
    "receiver": "agentverse", "status": "firing", "groupKey": '{}:{alertname="HighLatency"}',
    "alerts": [{"status": "firing", "labels": {"alertname": "HighLatency"},
                "startsAt": "2026-09-30T10:00:00Z", "fingerprint": "c6eadffa33f0e1a0"}],
    "version": "1",
}

# (id, webhook path type, trigger type, secret, header builder, sample payload)
VENDORS: list[tuple[str, str, TriggerType, str, Signer, dict[str, Any]]] = [
    (
        # Jira Cloud: "X-Hub-Signature: sha256=<hex HMAC-SHA256 of the body>".
        "jira", "jira", TriggerType.JIRA_WEBHOOK, SECRET,
        lambda b, s: {"X-Hub-Signature": "sha256=" + _hex(b, s)},
        {"timestamp": 1727690000000, "webhookEvent": "jira:issue_created",
         "issue": {"id": "10002", "key": "OPS-42",
                   "fields": {"summary": "Checkout 500s", "project": {"key": "OPS"}}}},
    ),
    (
        # Linear: "Linear-Signature: <hex HMAC-SHA256 of the body>".
        "linear", "linear", TriggerType.LINEAR_WEBHOOK, SECRET,
        lambda b, s: {"Linear-Signature": _hex(b, s)},
        {"action": "create", "type": "Issue", "createdAt": "2026-09-30T10:00:00.000Z",
         "data": {"id": "9f1c", "identifier": "ENG-7", "title": "Flaky deploy",
                  "team": {"id": "t1", "key": "ENG"}},
         "webhookTimestamp": 1727690000000},
    ),
    (
        # Sentry integration platform: "Sentry-Hook-Signature: <hex HMAC-SHA256>".
        "sentry", "sentry", TriggerType.SENTRY_ISSUE, SECRET,
        lambda b, s: {"Sentry-Hook-Signature": _hex(b, s)},
        {"action": "created", "installation": {"uuid": "a8e5d37a"},
         "data": {"issue": {"id": "1170820242", "title": "ZeroDivisionError",
                            "project": {"slug": "checkout"}, "level": "error"}},
         "actor": {"type": "application", "id": "sentry", "name": "Sentry"}},
    ),
    (
        # PagerDuty v3 webhooks: "X-PagerDuty-Signature: v1=<hex HMAC-SHA256>".
        "pagerduty", "pagerduty", TriggerType.PAGERDUTY, SECRET,
        lambda b, s: {"X-PagerDuty-Signature": "v1=" + _hex(b, s)},
        {"event": {"id": "01DEN4HPBQAJ9J", "event_type": "incident.triggered",
                   "resource_type": "incident", "occurred_at": "2026-09-30T10:00:00Z",
                   "data": {"id": "PGR0VU2", "type": "incident", "title": "DB down",
                            "urgency": "high", "status": "triggered"}}},
    ),
    (
        # PagerDuty during a secret roll: one v1 per active secret, comma-separated;
        # the valid one is NOT the last entry.
        "pagerduty-multi", "pagerduty", TriggerType.PAGERDUTY, SECRET,
        lambda b, s: {"X-PagerDuty-Signature": f"v1={_hex(b, s)},v1={_hex(b, 'old-secret')}"},
        {"event": {"id": "01DEN4HPBQAJ9K", "event_type": "incident.acknowledged",
                   "resource_type": "incident", "occurred_at": "2026-09-30T10:05:00Z",
                   "data": {"id": "PGR0VU2", "type": "incident"}}},
    ),
    (
        # Slack Events API: "X-Slack-Signature: v0=<hex HMAC of v0:{ts}:{body}>".
        "slack", "slack", TriggerType.SLACK_EVENT, SECRET, _slack(),
        {"type": "event_callback", "team_id": "T024BE7LD", "event_id": "Ev08MFMKH6",
         "event": {"type": "app_mention", "text": "<@U0LAN0Z89> deploy?", "user": "U2147483697"}},
    ),
    (
        # Teams outgoing webhook: "Authorization: HMAC <base64 HMAC-SHA256>" keyed
        # by the base64-decoded security token.
        "teams", "teams", TriggerType.TEAMS_WEBHOOK, TEAMS_SECRET, _teams,
        {"type": "message", "id": "1485983408511", "timestamp": _NOW_ISO,
         "text": "<at>AgentVerse</at> status", "from": {"id": "29:1abc", "name": "Ada"},
         "conversation": {"id": "19:abc@thread.skype"}, "channelData": {"tenant": {"id": "t"}}},
    ),
    (
        # Salesforce (Data Cloud webhook data action target / signed Apex callout):
        # "x-signature: <base64 HMAC-SHA256 of the body>".
        "salesforce", "salesforce", TriggerType.SALESFORCE_EVENT, SECRET,
        lambda b, s: {"x-signature": base64.b64encode(_mac(s, b)).decode()},
        {"events": [{"ObjectName": "Opportunity", "Id": "006xx000001Sv6", "Stage": "Closed Won",
                     "EventId": "e-001"}]},
    ),
    (
        # Grafana contact point with an HMAC secret:
        # "X-Grafana-Alerting-Signature: <hex HMAC-SHA256 of the body>".
        "grafana", "grafana", TriggerType.GRAFANA_ALERT, SECRET,
        lambda b, s: {"X-Grafana-Alerting-Signature": _hex(b, s)},
        _GRAFANA_ALERT,
    ),
    (
        # Grafana with a timestamp header configured: HMAC over "{ts}:{body}".
        "grafana-ts", "grafana", TriggerType.GRAFANA_ALERT, SECRET, _grafana_ts(),
        _GRAFANA_ALERT,
    ),
    (
        # Confluence Data Center webhook with a secret:
        # "X-Hub-Signature: sha256=<hex HMAC-SHA256 of the body>".
        "confluence", "confluence", TriggerType.CONFLUENCE_WEBHOOK, SECRET,
        lambda b, s: {"X-Hub-Signature": "sha256=" + _hex(b, s)},
        {"timestamp": 1727690000000, "event": "page_created", "userAccountId": "5b10ac8d",
         "page": {"id": 98311, "title": "Runbook", "spaceKey": "OPS", "version": 1}},
    ),
]
IDS = [v[0] for v in VENDORS]

# Vendors whose signature covers only the body: replay protection is dedup.
BODY_ONLY = [v for v in VENDORS if v[0] not in {"slack", "grafana-ts"}]


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[dict[str, Any]] = []
        self.keys: list[str] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any, **kw: Any) -> None:
        self.fired.append(payload)
        # The key the real TriggerDispatcher dedups on (Redis SET NX + the
        # trigger_events UNIQUE (tenant_id, idempotency_key) row).
        self.keys.append(
            derive_idempotency_key(
                str(getattr(spec, "trigger_id", "") or "trigger"),
                spec.trigger_type.value,
                payload,
                message_id=kw.get("message_id"),
            )
        )


def _client(ttype: TriggerType, secret: str) -> tuple[TestClient, _Dispatcher]:
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=ttype, webhook_token=TOKEN, webhook_signature_secret=secret),
        tenant_ctx=TenantContext(tenant_id="t-vendor", plan=PlanTier.FREE, api_key_id="k"),
        goal_template="triage it",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    dispatcher = _Dispatcher()
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    return TestClient(app, raise_server_exceptions=False), dispatcher


def _body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _post(client: TestClient, path_type: str, body: bytes, headers: dict[str, str]) -> Any:
    return client.post(
        f"/triggers/webhooks/{path_type}/{TOKEN}",
        content=body,
        headers={"content-type": "application/json", **headers},
    )


@pytest.mark.parametrize(("vid", "path_type", "ttype", "secret", "sign", "payload"), VENDORS,
                         ids=IDS)
def test_vendor_signed_delivery_is_accepted(vid, path_type, ttype, secret, sign, payload) -> None:
    client, dispatcher = _client(ttype, secret)
    body = _body(payload)
    r = _post(client, path_type, body, sign(body, secret))
    assert r.status_code == 200, r.text
    assert r.json()["dispatched"] == 1
    assert dispatcher.fired and dispatcher.fired[0]["webhook_type"] == path_type


@pytest.mark.parametrize(("vid", "path_type", "ttype", "secret", "sign", "payload"), VENDORS,
                         ids=IDS)
def test_tampered_vendor_delivery_is_rejected(vid, path_type, ttype, secret, sign, payload) -> None:
    client, dispatcher = _client(ttype, secret)
    body = _body(payload)
    headers = sign(body, secret)
    tampered = body.replace(b"}", b',"injected":true}', 1)
    assert _post(client, path_type, tampered, headers).status_code == 401
    assert dispatcher.fired == []


@pytest.mark.parametrize(("vid", "path_type", "ttype", "secret", "sign", "payload"), VENDORS,
                         ids=IDS)
def test_delivery_signed_with_another_secret_is_rejected(
    vid, path_type, ttype, secret, sign, payload
) -> None:
    client, dispatcher = _client(ttype, secret)
    body = _body(payload)
    other = base64.b64encode(b"a-different-shared-key-000000000").decode()
    assert _post(client, path_type, body, sign(body, other)).status_code == 401
    assert dispatcher.fired == []


@pytest.mark.parametrize(
    ("path_type", "ttype", "stale_sign", "payload"),
    [
        ("slack", TriggerType.SLACK_EVENT, _slack(ts_offset=-600), VENDORS[5][5]),
        ("grafana", TriggerType.GRAFANA_ALERT, _grafana_ts(ts_offset=-600), _GRAFANA_ALERT),
    ],
    ids=["slack", "grafana-ts"],
)
def test_replayed_timestamped_delivery_is_rejected_when_stale(
    path_type, ttype, stale_sign, payload
) -> None:
    client, dispatcher = _client(ttype, SECRET)
    body = _body(payload)
    assert _post(client, path_type, body, stale_sign(body, SECRET)).status_code == 401
    assert dispatcher.fired == []


def test_grafana_timestamp_cannot_be_swapped_for_a_fresh_one() -> None:
    client, dispatcher = _client(TriggerType.GRAFANA_ALERT, SECRET)
    body = _body(_GRAFANA_ALERT)
    headers = _grafana_ts(ts_offset=-600)(body, SECRET)
    headers["X-Grafana-Alerting-Timestamp"] = str(int(time.time()))
    assert _post(client, "grafana", body, headers).status_code == 401
    assert dispatcher.fired == []


@pytest.mark.parametrize(("vid", "path_type", "ttype", "secret", "sign", "payload"), BODY_ONLY,
                         ids=[v[0] for v in BODY_ONLY])
def test_replayed_body_signed_delivery_collapses_onto_one_firing(
    vid, path_type, ttype, secret, sign, payload
) -> None:
    client, dispatcher = _client(ttype, secret)
    body = _body(payload)
    headers = sign(body, secret)
    assert _post(client, path_type, body, headers).status_code == 200
    assert _post(client, path_type, body, headers).status_code == 200
    first, replay = dispatcher.keys
    assert first == replay  # the dispatcher dedups the replay as the same firing

    # ... while a genuinely different (signed) delivery is a separate firing.
    other = _body({**payload, "delivery": "second"})
    assert _post(client, path_type, other, sign(other, secret)).status_code == 200
    assert dispatcher.keys[2] != first


def test_pagerduty_signature_header_without_v1_is_rejected() -> None:
    v = WebhookSignatureVerifier()
    body = b'{"event":{}}'
    good = _hex(body)
    assert v.verify_pagerduty(body, f"v1={good}", SECRET)
    assert v.verify_pagerduty(body, f"v1=deadbeef, v1={good}", SECRET)
    assert not v.verify_pagerduty(body, good, SECRET)
    assert not v.verify_pagerduty(body, f"v2={good}", SECRET)
    assert not v.verify_pagerduty(body, "", SECRET)


def test_salesforce_accepts_hex_encoding_too() -> None:
    v = WebhookSignatureVerifier()
    body = b'{"events":[]}'
    assert v.verify_salesforce(body, _hex(body), SECRET)
    assert v.verify_salesforce(body, base64.b64encode(_mac(SECRET, body)).decode(), SECRET)
    assert not v.verify_salesforce(body, "not-a-signature", SECRET)


def test_confluence_requires_the_sha256_prefix() -> None:
    v = WebhookSignatureVerifier()
    body = b'{"event":"page_created"}'
    assert v.verify_hub_signature(body, "sha256=" + _hex(body), SECRET)
    assert not v.verify_hub_signature(body, _hex(body), SECRET)
    assert not v.verify_hub_signature(body, "sha1=" + _hex(body), SECRET)


def test_salesforce_soap_outbound_message_cannot_satisfy_a_signing_secret() -> None:
    """Outbound (SOAP) messages carry no signature; a trigger that requires one
    must not accept them unsigned (fail closed)."""
    client, dispatcher = _client(TriggerType.SALESFORCE_EVENT, SECRET)
    soap = (
        b'<?xml version="1.0"?><soapenv:Envelope '
        b'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>'
        b'<notifications xmlns="http://soap.sforce.com/2005/09/outbound">'
        b"<OrganizationId>00Dxx</OrganizationId><Notification><Id>04l1</Id></Notification>"
        b"</notifications></soapenv:Body></soapenv:Envelope>"
    )
    r = client.post(f"/triggers/webhooks/salesforce/{TOKEN}", content=soap,
                    headers={"content-type": "text/xml"})
    assert r.status_code == 401
    assert dispatcher.fired == []
