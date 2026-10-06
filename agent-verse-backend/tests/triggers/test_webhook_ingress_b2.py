"""B2-2 … B2-7: the generic signed webhook / REST trigger ingress, verified live first.

Live defects (2026-10-06, before the fixes):
* B2-2 a form or text body became ``{}`` (the payload was lost, and every form
  delivery deduplicated against every other);
* B2-3 the delivery lookup loaded every trigger of the type and compared tokens
  in Python;
* B2-4 the dedup key was the payload hash, kept forever by the durable gate: the
  same body sent as a NEW delivery an hour later was dropped as a replay, and a
  signed timestamp was ignored (a captured delivery replayed later still ran);
* B2-5 ``{{payload.ticket.id}}`` rendered empty;
* B2-6 a leaked webhook URL could not be rotated;
* B2-7 a throttled delivery / REST call answered 200 "accepted" (never retried),
  and the REST ``Idempotency-Key`` header was ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dedup import derive_idempotency_key
from app.triggers.dispatcher import TriggerDispatcher, _payload_path
from app.triggers.events import TriggerEvent
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from app.triggers.webhooks import ingress

TOKEN = "tokB2_" + "w" * 40
SECRET = "b2secret-" + "z" * 20
CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _sig(secret: str, data: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


# ── ingress helpers ───────────────────────────────────────────────────────────


def test_parse_body_json_form_and_text() -> None:
    assert ingress.parse_body(b'{"a": {"b": 1}}', "application/json") == {"a": {"b": 1}}
    assert ingress.parse_body(b"[1, 2]", "application/json") == {"data": [1, 2]}
    assert ingress.parse_body(
        b"ticket=TCK-9&priority=P2&tag=a&tag=b", "application/x-www-form-urlencoded"
    ) == {"ticket": "TCK-9", "priority": "P2", "tag": ["a", "b"]}
    assert ingress.parse_body(b"disk full on db-3", "text/plain; charset=utf-8") == {
        "text": "disk full on db-3"
    }
    assert ingress.parse_body(b"", "application/json") == {}
    with pytest.raises(HTTPException) as err:
        ingress.parse_body(b"{not json", "application/json")
    assert err.value.status_code == 400


def test_signature_with_timestamp_and_replay_window() -> None:
    body = b'{"x": 1}'
    now = 1_800_000_000
    ts = str(now)
    good = {"x-webhook-timestamp": ts, "x-signature": _sig(SECRET, f"{ts}.".encode() + body)}
    ingress.check_signature(good, body, [SECRET], now=now + 10)
    with pytest.raises(HTTPException) as err:  # captured and replayed 10 minutes later
        ingress.check_signature(good, body, [SECRET], now=now + 600)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException):  # a body-only signature cannot carry a timestamp
        ingress.check_signature(
            {"x-webhook-timestamp": ts, "x-signature": _sig(SECRET, body)}, body, [SECRET], now=now
        )
    ingress.check_signature({"x-signature": _sig(SECRET, body)}, body, [SECRET])  # legacy
    for headers in ({}, {"x-signature": _sig("wrong", body)}):
        with pytest.raises(HTTPException) as err:
            ingress.check_signature(headers, body, [SECRET])
        assert err.value.status_code == 401
    ingress.check_signature({"x-signature": _sig("old", body)}, body, [SECRET, "old"])


def test_firing_identity_is_the_delivery_id_else_the_body_in_its_window() -> None:
    body = b'{"kind": "daily-report"}'
    assert ingress.firing_message_id({"x-delivery-id": "d1"}, body) == "delivery:d1"
    assert ingress.firing_message_id({"idempotency-key": "k"}, body) == "delivery:k"
    same_window = ingress.firing_message_id({}, body, now=1000.0)
    assert same_window == ingress.firing_message_id({}, body, now=1100.0)
    assert same_window != ingress.firing_message_id({}, body, now=1000.0 + 3600)


def test_push_and_event_keys_follow_the_delivery_id() -> None:
    payload = {"kind": "daily-report"}
    for ttype in ("webhook", "rest", "event", "github_webhook"):
        day1 = derive_idempotency_key("tr", ttype, payload, message_id="delivery:day-1")
        day2 = derive_idempotency_key("tr", ttype, payload, message_id="delivery:day-2")
        retry = derive_idempotency_key("tr", ttype, {"other": 1}, message_id="delivery:day-1")
        assert day1 != day2  # an identical body as a new delivery runs again
        assert day1 == retry  # the same delivery runs once
    # Types without delivery semantics keep the payload key.
    assert derive_idempotency_key("tr", "condition", payload, message_id="a") == (
        derive_idempotency_key("tr", "condition", payload, message_id="b")
    )


def test_nested_payload_paths_render() -> None:
    payload = {"ticket": {"id": "TCK-1", "tags": ["vip", "eu"]}, "a.b": "flat", "n": 0}
    assert _payload_path(payload, "ticket.id") == "TCK-1"
    assert _payload_path(payload, "ticket.tags.1") == "eu"
    assert _payload_path(payload, "ticket.tags") == '["vip","eu"]'
    assert _payload_path(payload, "a.b") == "flat"
    assert _payload_path(payload, "n") == "0"
    assert _payload_path(payload, "ticket.missing") == ""
    rendered = TriggerDispatcher()._render_template(
        "Ticket {{payload.ticket.id}} ({{ payload.ticket.tags.0 }})", payload
    )
    assert rendered == "Ticket TCK-1 (vip)"


# ── the endpoint ──────────────────────────────────────────────────────────────


class _Dispatcher:
    def __init__(self, skip: str | None = None) -> None:
        self.skip = skip
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any,
                       **kwargs: Any) -> TriggerEvent:
        from datetime import UTC, datetime

        self.calls.append({"payload": payload, **kwargs})
        return TriggerEvent(
            event_id="e", tenant_id="t1", trigger_id="tr", trigger_type="webhook",
            idempotency_key="k", fired_at=datetime.now(UTC), payload=payload,
            goal_created=self.skip is None, goal_id=None if self.skip else "g1",
            skip_reason=self.skip,
        )


class _Store(ScheduleStore):
    def __init__(self) -> None:
        super().__init__()
        self.scanned = 0

    def find_by_type(self, trigger_type: str, *, tenant_id: str) -> list[dict[str, Any]]:
        self.scanned += 1
        return super().find_by_type(trigger_type, tenant_id=tenant_id)


def _client(dispatcher: _Dispatcher, *, secret: str = SECRET,
            ttype: TriggerType = TriggerType.WEBHOOK) -> tuple[TestClient, _Store, str]:
    store = _Store()
    sid = store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=ttype, webhook_token=TOKEN,
                         webhook_signature_secret=secret),
        tenant_ctx=CTX,
        goal_template="Ticket {{payload.ticket}}",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False), store, sid


def test_form_body_is_the_payload_and_the_delivery_id_its_identity() -> None:
    disp = _Dispatcher()
    client, _, _ = _client(disp)
    body = b"ticket=TCK-9&priority=P2"
    r = client.post(f"/triggers/webhooks/webhook/{TOKEN}", content=body, headers={
        "content-type": "application/x-www-form-urlencoded", "x-signature": _sig(SECRET, body),
        "x-delivery-id": "dlv-77"})
    assert r.status_code == 200, r.text
    call = disp.calls[0]
    assert call["payload"]["ticket"] == "TCK-9" and call["payload"]["priority"] == "P2"
    assert call["message_id"] == "delivery:dlv-77"
    assert call["dead_letter_throttled"] is False
    assert r.json()["goal_ids"] == ["g1"]


def test_stale_signed_timestamp_is_401_and_fires_nothing() -> None:
    disp = _Dispatcher()
    client, _, _ = _client(disp)
    body = b'{"ticket": "T"}'
    ts = str(int(time.time()) - 3600)
    r = client.post(f"/triggers/webhooks/webhook/{TOKEN}", content=body, headers={
        "content-type": "application/json", "x-webhook-timestamp": ts,
        "x-signature": _sig(SECRET, f"{ts}.".encode() + body)})
    assert r.status_code == 401
    assert disp.calls == []


@pytest.mark.parametrize(("skip", "status"), [
    ("rate_limit", 429), ("bulkhead_full", 429), ("rate_limit_unavailable", 503),
    ("PAYLOAD_TOO_LARGE", 413),
])
def test_throttled_delivery_is_not_acknowledged(skip: str, status: int) -> None:
    client, _, _ = _client(_Dispatcher(skip=skip), secret="")
    r = client.post(f"/triggers/webhooks/webhook/{TOKEN}", json={"ticket": "T"})
    assert r.status_code == status, r.text
    if status == 429:
        assert r.headers.get("retry-after")


@pytest.mark.parametrize("skip", ["dedup", "condition_false"])
def test_replay_and_filtered_deliveries_are_final_answers(skip: str) -> None:
    client, _, _ = _client(_Dispatcher(skip=skip), secret="")
    r = client.post(f"/triggers/webhooks/webhook/{TOKEN}", json={"ticket": "T"})
    assert r.status_code == 200
    assert r.json()["skipped"] == [skip]


def test_lookup_is_by_token_not_a_scan_of_every_trigger() -> None:
    client, store, _ = _client(_Dispatcher(), secret="")
    for i in range(20):  # the tenant's other webhook triggers
        store.create(goal_id="", spec=TriggerSpec(trigger_type=TriggerType.WEBHOOK,
                                                 webhook_token=f"other{i:02d}" + "q" * 30),
                     tenant_ctx=CTX, goal_template="x")
    calls: list[str] = []
    orig = store.find_by_webhook_token_async

    async def spy(token: str, **kw: Any) -> list[dict[str, Any]]:
        calls.append(token)
        return await orig(token, **kw)

    store.find_by_webhook_token_async = spy  # type: ignore[method-assign]
    store.find_by_type_async = None  # type: ignore[assignment,method-assign]
    r = client.post(f"/triggers/webhooks/webhook/{TOKEN}", json={"ticket": "T"})
    assert r.status_code == 200, r.text
    assert calls == [TOKEN]


def test_rotate_token_rejects_the_old_url() -> None:
    disp = _Dispatcher()
    client, _, sid = _client(disp, secret="")
    r = client.post(f"/triggers/{sid}/rotate-token")
    assert r.status_code == 200, r.text
    new = r.json()["webhook_token"]
    assert new != TOKEN and len(new) >= 32
    assert client.post(f"/triggers/webhooks/webhook/{TOKEN}", json={}).status_code == 404
    assert client.post(f"/triggers/webhooks/webhook/{new}", json={}).status_code == 200


def test_rest_fire_honours_idempotency_key_and_429() -> None:
    disp = _Dispatcher()
    client, _, sid = _client(disp, secret="", ttype=TriggerType.REST)
    r = client.post(f"/triggers/{sid}/fire", json={"payload": {"ref": "R-1"}},
                    headers={"Idempotency-Key": "order-42"})
    assert r.status_code == 200, r.text
    assert disp.calls[-1]["message_id"] == "delivery:order-42"
    client.post(f"/triggers/{sid}/fire", json={"payload": {"ref": "R-1"}})
    assert disp.calls[-1]["message_id"].startswith("call:")  # each call its own firing
    throttled, _, sid2 = _client(_Dispatcher(skip="rate_limit"), secret="",
                                 ttype=TriggerType.REST)
    assert throttled.post(f"/triggers/{sid2}/fire", json={"payload": {}}).status_code == 429
