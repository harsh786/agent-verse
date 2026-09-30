"""TRG-28: vendor-format signed deliveries for the typed webhook triggers.

Only GitHub and Stripe had tests proving a real, vendor-signed delivery is
accepted. Each case below signs a sample payload exactly the way the vendor
documents (header name + signature format) and proves the correctly signed
delivery fires the trigger and a tampered body is rejected.

Slack / Teams / CloudWatch (SNS) / Salesforce deliveries depend on the
TRG-24/25/26 verifier work (scheduling package) and are covered alongside it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

TOKEN = "vendor_" + "v" * 40
SECRET = "whsec-vendor-test-secret"


def _hex(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# (webhook path type, trigger type, header name, header value builder, sample payload)
VENDORS: list[tuple[str, TriggerType, str, Callable[[bytes], str], dict[str, Any]]] = [
    (
        # Jira Cloud: "X-Hub-Signature: sha256=<hex HMAC-SHA256 of the body>".
        "jira", TriggerType.JIRA_WEBHOOK, "X-Hub-Signature",
        lambda b: "sha256=" + _hex(b),
        {"timestamp": 1727690000000, "webhookEvent": "jira:issue_created",
         "issue": {"id": "10002", "key": "OPS-42",
                   "fields": {"summary": "Checkout 500s", "project": {"key": "OPS"}}}},
    ),
    (
        # Linear: "Linear-Signature: <hex HMAC-SHA256 of the body>".
        "linear", TriggerType.LINEAR_WEBHOOK, "Linear-Signature",
        _hex,
        {"action": "create", "type": "Issue", "createdAt": "2026-09-30T10:00:00.000Z",
         "data": {"id": "9f1c", "identifier": "ENG-7", "title": "Flaky deploy",
                  "team": {"id": "t1", "key": "ENG"}},
         "webhookTimestamp": 1727690000000},
    ),
    (
        # Sentry integration platform: "Sentry-Hook-Signature: <hex HMAC-SHA256>".
        "sentry", TriggerType.SENTRY_ISSUE, "Sentry-Hook-Signature",
        _hex,
        {"action": "created", "installation": {"uuid": "a8e5d37a"},
         "data": {"issue": {"id": "1170820242", "title": "ZeroDivisionError",
                            "project": {"slug": "checkout"}, "level": "error"}},
         "actor": {"type": "application", "id": "sentry", "name": "Sentry"}},
    ),
    (
        # PagerDuty v3 webhooks: "X-PagerDuty-Signature: v1=<hex HMAC-SHA256>".
        "pagerduty", TriggerType.PAGERDUTY, "X-PagerDuty-Signature",
        lambda b: "v1=" + _hex(b),
        {"event": {"id": "01DEN4HPBQAJ9J", "event_type": "incident.triggered",
                   "resource_type": "incident", "occurred_at": "2026-09-30T10:00:00Z",
                   "data": {"id": "PGR0VU2", "type": "incident", "title": "DB down",
                            "urgency": "high", "status": "triggered"}}},
    ),
]


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[dict[str, Any]] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any) -> None:
        self.fired.append(payload)


def _client(ttype: TriggerType) -> tuple[TestClient, _Dispatcher]:
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=ttype, webhook_token=TOKEN, webhook_signature_secret=SECRET),
        tenant_ctx=TenantContext(tenant_id="t-vendor", plan=PlanTier.FREE, api_key_id="k"),
        goal_template="triage it",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    dispatcher = _Dispatcher()
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    return TestClient(app, raise_server_exceptions=False), dispatcher


@pytest.mark.parametrize(("path_type", "ttype", "header", "sign", "payload"), VENDORS,
                         ids=[v[0] for v in VENDORS])
def test_vendor_signed_delivery_is_accepted(path_type, ttype, header, sign, payload) -> None:
    client, dispatcher = _client(ttype)
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = client.post(f"/triggers/webhooks/{path_type}/{TOKEN}", content=body,
                    headers={"content-type": "application/json", header: sign(body)})
    assert r.status_code == 200, r.text
    assert r.json()["dispatched"] == 1
    assert dispatcher.fired and dispatcher.fired[0]["webhook_type"] == path_type


@pytest.mark.parametrize(("path_type", "ttype", "header", "sign", "payload"), VENDORS,
                         ids=[v[0] for v in VENDORS])
def test_tampered_vendor_delivery_is_rejected(path_type, ttype, header, sign, payload) -> None:
    client, dispatcher = _client(ttype)
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = sign(body)
    tampered = body.replace(b"}", b',"injected":true}', 1)
    r = client.post(f"/triggers/webhooks/{path_type}/{TOKEN}", content=tampered,
                    headers={"content-type": "application/json", header: signature})
    assert r.status_code == 401
    assert dispatcher.fired == []
