"""INGRESS-*: generic ingress triggers (webhook, rest, event) on the live stack (B2).

Every scenario drives the public HTTP surface like an external sender or
client and asserts what a user would check: the HTTP answer the sender acts on,
one goal per delivery (``GET /triggers/{id}/events``), the goal's rendered
text, the audit rows of suppressed deliveries, the trigger DLQ, and that the
other tenant can neither fire nor read anything.

* INGRESS-WEBHOOK-SIGNED: HMAC-SHA256 (constant time); unsigned / bad / stale
  signed timestamp -> 401; a delivery id runs once; an identical body sent as a
  NEW delivery runs again (B2-4); JSON / form / text bodies map into the goal
  text incl. nested paths (B2-2, B2-5); oversized body -> 413; tenant isolation.
* INGRESS-WEBHOOK-FILTER-RATE: a CEL filter fires only matching deliveries; a
  delivery over the trigger's hourly cap is 429 + Retry-After (never "accepted"),
  audited as rate_limit and not dedup-claimed (B2-1, B2-7).
* INGRESS-WEBHOOK-ROTATION: rotate-secret (old secret valid only in the grace
  window), rotate-token (old URL 404 at once, B2-6).
* INGRESS-WEBHOOK-LOOKUP: the pre-auth token lookups use the unique token index
  (EXPLAIN on the live database), never a scan (B2-3).
* INGRESS-REST: API-key REST trigger: Idempotency-Key runs once, distinct calls
  run, other tenant 404, no key 401, over the cap 429 (B2-7).
* INGRESS-EVENT: ``POST /triggers/events/{channel}`` fires the subscribed EVENT
  trigger once per event (filters, a redelivered event id once), the other
  tenant's event on the same channel fires nothing, a throttled event is
  dead-lettered (replayable) rather than lost (B2-1).
* INGRESS-EVENT-RECONNECT: the event-bus consumers' Redis connections are
  killed (``CLIENT KILL``); the next event still fires exactly once.
* INGRESS-EVENT-MULTI-REPLICA (``RW_SECOND_BACKEND_CONTAINER`` running): with
  two API replicas consuming the bus, every event fires exactly once.

Uses the session tenant (``api``) and ``RW_SECOND_TENANT_*`` for isolation.
Workflow webhooks (``/wf-hooks``) are covered by TRIGGER-CHAIN.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from typing import Any

import httpx
import pytest

from tests.real_world.helpers import (
    BASE_URL,
    LiveAPI,
    mask,
    register_secret,
    tag,
    wait_until,
)

ACK = "Reply with exactly the word ACK. Do not use any tools."
REDIS = os.getenv("RW_REDIS_CONTAINER", "agentverse-backend-redis-1")
PG = os.getenv("RW_PG_CONTAINER", "agentverse-backend-postgres-1")
SECOND_BACKEND = os.getenv("RW_SECOND_BACKEND_CONTAINER", "")


# ── helpers ───────────────────────────────────────────────────────────────────


def _sig(secret: str, data: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def _signed(secret: str, body: bytes, *, delivery: str = "", ts: int | None = None,
            ctype: str = "application/json") -> dict[str, str]:
    headers = {"Content-Type": ctype}
    if ts is not None:
        headers["X-Webhook-Timestamp"] = str(ts)
        headers["X-Signature"] = _sig(secret, f"{ts}.".encode() + body)
    else:
        headers["X-Signature"] = _sig(secret, body)
    if delivery:
        headers["X-Delivery-Id"] = delivery
    return headers


def _create(api: LiveAPI, cleanup: Any, spec: dict[str, Any], template: str) -> dict[str, Any]:
    resp = api.post("/triggers", json={"spec": spec, "goal_template": template})
    assert resp.status_code == 201, f"POST /triggers -> {resp.status_code}: {mask(resp.text[:300])}"
    rec: dict[str, Any] = resp.json()
    cleanup("DELETE", f"/triggers/{rec['schedule_id']}")
    token = str((rec.get("spec") or {}).get("webhook_token") or "")
    if token:
        register_secret(token)
    return rec


def _events(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    resp = api.get(f"/triggers/{sid}/events", params={"limit": 200})
    return resp.json() if resp.status_code == 200 else []


def _goals(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    return [e for e in _events(api, sid) if e.get("goal_id") and e.get("goal_created")]


def _skips(api: LiveAPI, sid: str) -> list[str]:
    return sorted(str(e.get("skip_reason")) for e in _events(api, sid) if e.get("skip_reason"))


def _goal_text(api: LiveAPI, goal_id: str) -> str:
    body = api.get(f"/goals/{goal_id}").json()
    return str(body.get("goal") or body.get("goal_text") or body.get("description") or "")


def _cancel(api: LiveAPI, cleanup: Any, sid: str) -> None:
    for e in _goals(api, sid):
        cleanup("POST", f"/goals/{e['goal_id']}/cancel")


def _wait_goals(api: LiveAPI, sid: str, n: int, *, settle: float = 6.0,
                timeout: float = 90.0) -> list[dict[str, Any]]:
    wait_until(lambda: _goals(api, sid), timeout=timeout, interval=3,
               desc=f"{n} goal(s) for trigger {sid}", done=lambda g: len(g) >= n)
    time.sleep(settle)  # anything extra (a duplicate) would show up now
    return _goals(api, sid)


@pytest.fixture
def other(second_tenant_api: LiveAPI | None) -> LiveAPI:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE (isolation)")
    return second_tenant_api


@pytest.fixture
def sender() -> Any:
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:  # no API key: a third party
        yield c


# ── webhook ───────────────────────────────────────────────────────────────────


@pytest.mark.scenario("INGRESS-WEBHOOK-SIGNED")
def test_signed_webhook_deliveries(api: LiveAPI, other: LiveAPI, sender: httpx.Client,
                                   cleanup: Any, evidence: dict[str, Any]) -> None:
    secret = secrets.token_hex(20)
    register_secret(secret)
    t = tag()
    rec = _create(api, cleanup, {"trigger_type": "webhook", "name": f"rw-in-signed-{t}",
                                 "webhook_secret": secret},
                  "Support ticket {{payload.ticket.id}} from {{payload.customer.name}} "
                  "priority {{payload.priority}} first tag {{payload.ticket.tags.0}}. " + ACK)
    sid, token = rec["schedule_id"], rec["spec"]["webhook_token"]
    url = f"/triggers/webhooks/webhook/{token}"
    evidence["trigger_id"] = sid
    body = json.dumps({"ticket": {"id": f"TCK-{t}", "tags": ["billing", "vip"]},
                       "customer": {"name": "Asha Rao"}, "priority": "P1"}).encode()
    now = int(time.time())
    status: dict[str, int] = {}
    status["unsigned"] = sender.post(url, content=body,
                                     headers={"Content-Type": "application/json"}).status_code
    status["bad_sig"] = sender.post(url, content=body,
                                    headers=_signed("wrong", body, delivery="x")).status_code
    status["stale_ts"] = sender.post(url, content=body, headers=_signed(
        secret, body, delivery="stale", ts=now - 3600)).status_code
    first = sender.post(url, content=body, headers=_signed(secret, body, delivery=f"d1-{t}",
                                                           ts=now))
    status["signed"] = first.status_code
    status["redelivery"] = sender.post(url, content=body, headers=_signed(
        secret, body, delivery=f"d1-{t}", ts=int(time.time()))).status_code
    evidence["status"] = status
    assert status["unsigned"] == 401 and status["bad_sig"] == 401, status
    assert status["stale_ts"] == 401, f"a 1 h-old signed timestamp must be refused: {status}"
    assert status["signed"] == 200 and first.json().get("goal_ids"), mask(first.text[:300])
    assert status["redelivery"] == 200, status
    goals = _wait_goals(api, sid, 1)
    assert len(goals) == 1, f"one delivery + its redelivery made {len(goals)} goals"
    text = _goal_text(api, goals[0]["goal_id"])
    evidence["goal_text"] = text[:200]
    for part in (f"TCK-{t}", "Asha Rao", "priority P1", "first tag billing"):
        assert part in text, f"payload mapping lost {part!r}: {text[:200]!r}"

    # The same body as a NEW delivery (a new delivery id) is a new firing.
    again = sender.post(url, content=body, headers=_signed(secret, body, delivery=f"d2-{t}"))
    assert again.status_code == 200, mask(again.text[:200])
    # Form and text bodies are payloads, not {}.
    form = f"ticket=FORM-{t}&priority=P2".encode()
    r_form = sender.post(url, content=form, headers=_signed(
        secret, form, delivery=f"f-{t}", ctype="application/x-www-form-urlencoded"))
    text_body = f"disk full on db-{t}".encode()
    r_text = sender.post(url, content=text_body, headers=_signed(
        secret, text_body, delivery=f"t-{t}", ctype="text/plain"))
    big = json.dumps({"blob": "x" * (2 * 1024 * 1024)}).encode()
    r_big = sender.post(url, content=big, headers=_signed(secret, big, delivery=f"b-{t}"))
    evidence["status"].update(new_delivery=again.status_code, form=r_form.status_code,
                              text=r_text.status_code, oversized=r_big.status_code)
    assert r_form.status_code == 200 and r_text.status_code == 200, evidence["status"]
    assert r_big.status_code == 413, evidence["status"]
    goals = _wait_goals(api, sid, 4)
    payloads = [json.dumps(e.get("payload") or {}) for e in goals]
    evidence["goals"] = len(goals)
    assert len(goals) == 4, f"want 4 goals (first, new delivery, form, text): {len(goals)}"
    assert any(f"FORM-{t}" in p for p in payloads), "the form body was not the payload"
    assert any(f"db-{t}" in p for p in payloads), "the text body was not the payload"
    assert "dedup" in _skips(api, sid), "the redelivery left no audit row"

    # Tenant isolation: the other tenant cannot use, read or fire this trigger.
    iso = {
        "other_key_on_token": other.post(f"/webhooks/{token}", content=body, headers=_signed(
            secret, body, delivery=f"iso-{t}")).status_code,
        "other_reads_events": other.get(f"/triggers/{sid}/events").json(),
        "other_fire": other.client.post(f"/triggers/{sid}/fire", json={}).status_code,
        "other_get": other.get(f"/triggers/{sid}").status_code,
    }
    evidence["isolation"] = {k: (v if not isinstance(v, list) else len(v)) for k, v in iso.items()}
    assert iso["other_key_on_token"] == 404 and iso["other_fire"] == 404, evidence["isolation"]
    assert iso["other_reads_events"] == [] and iso["other_get"] == 404, evidence["isolation"]
    _cancel(api, cleanup, sid)


@pytest.mark.scenario("INGRESS-WEBHOOK-FILTER-RATE")
def test_webhook_filters_and_rate_limit(api: LiveAPI, sender: httpx.Client, cleanup: Any,
                                         evidence: dict[str, Any]) -> None:
    secret = secrets.token_hex(20)
    register_secret(secret)
    t = tag()
    flt = _create(api, cleanup, {"trigger_type": "webhook", "name": f"rw-in-filter-{t}",
                                 "webhook_secret": secret,
                                 "condition_cel": 'payload.severity == "critical"'},
                  "Page on-call for alert {{payload.alert}}. " + ACK)
    url = f"/triggers/webhooks/webhook/{flt['spec']['webhook_token']}"
    answers = []
    for sev in ("warning", "critical", "info"):
        b = json.dumps({"severity": sev, "alert": f"{sev}-{t}"}).encode()
        r = sender.post(url, content=b, headers=_signed(secret, b, delivery=f"{sev}-{t}"))
        answers.append((sev, r.status_code, r.json().get("skipped")))
    evidence["filter_answers"] = answers
    goals = _wait_goals(api, flt["schedule_id"], 1)
    assert len(goals) == 1 and "critical" in json.dumps(goals[0].get("payload")), goals
    assert _skips(api, flt["schedule_id"]).count("condition_false") == 2
    assert all(code == 200 for _, code, _ in answers), answers
    _cancel(api, cleanup, flt["schedule_id"])

    capped = _create(api, cleanup, {"trigger_type": "webhook", "name": f"rw-in-rate-{t}",
                                    "webhook_secret": secret, "max_firings_per_hour": 2},
                     "Rate probe {{payload.i}}. " + ACK)
    url = f"/triggers/webhooks/webhook/{capped['spec']['webhook_token']}"
    codes: list[int] = []
    retry_after = ""
    for i in range(3):
        b = json.dumps({"i": i, "t": t}).encode()
        r = sender.post(url, content=b, headers=_signed(secret, b, delivery=f"r{i}-{t}"))
        codes.append(r.status_code)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "")
    evidence["rate_codes"] = codes
    assert codes == [200, 200, 429], f"over the cap must be 429 (sender retries): {codes}"
    assert retry_after, "429 without Retry-After"
    goals = _wait_goals(api, capped["schedule_id"], 2)
    assert len(goals) == 2
    assert "rate_limit" in _skips(api, capped["schedule_id"])
    _cancel(api, cleanup, capped["schedule_id"])


@pytest.mark.scenario("INGRESS-WEBHOOK-ROTATION")
def test_webhook_secret_and_token_rotation(api: LiveAPI, sender: httpx.Client, cleanup: Any,
                                           evidence: dict[str, Any]) -> None:
    secret = secrets.token_hex(20)
    register_secret(secret)
    t = tag()
    rec = _create(api, cleanup, {"trigger_type": "webhook", "name": f"rw-in-rot-{t}",
                                 "webhook_secret": secret}, "Rotation probe {{payload.n}}. " + ACK)
    sid, old_token = rec["schedule_id"], rec["spec"]["webhook_token"]
    rot = api.post(f"/triggers/{sid}/rotate-secret")
    assert rot.status_code == 200, rot.text[:200]
    new_secret = rot.json()["new_secret"]
    register_secret(new_secret)
    url = f"/triggers/webhooks/webhook/{old_token}"
    codes = {}
    for name, key in (("new_secret", new_secret), ("old_secret_in_grace", secret),
                      ("unrelated_secret", "nope")):
        b = json.dumps({"n": name, "t": t}).encode()
        codes[name] = sender.post(url, content=b, headers=_signed(key, b, delivery=name + t)
                                  ).status_code
    rt = api.post(f"/triggers/{sid}/rotate-token")
    assert rt.status_code == 200, rt.text[:200]
    new_token = rt.json()["webhook_token"]
    register_secret(new_token)
    b = json.dumps({"n": "after-rotate", "t": t}).encode()
    codes["old_token"] = sender.post(url, content=b, headers=_signed(
        new_secret, b, delivery="o" + t)).status_code
    codes["new_token"] = sender.post(f"/triggers/webhooks/webhook/{new_token}", content=b,
                                     headers=_signed(new_secret, b, delivery="n" + t)).status_code
    evidence["codes"] = codes
    assert codes == {"new_secret": 200, "old_secret_in_grace": 200, "unrelated_secret": 401,
                     "old_token": 404, "new_token": 200}, codes
    assert len(_wait_goals(api, sid, 3)) == 3
    _cancel(api, cleanup, sid)


@pytest.mark.scenario("INGRESS-WEBHOOK-LOOKUP")
def test_webhook_lookup_uses_the_token_index(evidence: dict[str, Any]) -> None:
    plans = {}
    for name, sql in {
        "pre_auth_tenant": "SELECT tenant_id, webhook_token FROM schedules "
                           "WHERE webhook_token = 'x' AND webhook_token <> '' LIMIT 1",
        "trigger_by_token": "SELECT * FROM schedules WHERE tenant_id = 't' "
                            "AND trigger_type = 'webhook' AND webhook_token = 'x'",
    }.items():
        out = subprocess.run(
            ["docker", "exec", PG, "psql", "-U", "agentverse", "-d", "agentverse", "-Atc",
             f"SET enable_seqscan = off; EXPLAIN {sql}"],
            capture_output=True, text=True, timeout=60, check=False)
        plans[name] = out.stdout.strip()
    evidence["plans"] = plans
    for name, plan in plans.items():
        assert "uq_schedules_webhook_token" in plan or "Index" in plan, f"{name}: {plan}"


# ── rest ──────────────────────────────────────────────────────────────────────


@pytest.mark.scenario("INGRESS-REST")
def test_rest_trigger(api: LiveAPI, other: LiveAPI, cleanup: Any,
                      evidence: dict[str, Any]) -> None:
    t = tag()
    rec = _create(api, cleanup, {"trigger_type": "rest", "name": f"rw-in-rest-{t}",
                                 "max_firings_per_hour": 3},
                  "Invoice {{payload.invoice.number}} for {{payload.invoice.amount}} EUR "
                  "needs review. " + ACK)
    sid = rec["schedule_id"]
    raw = api.client  # raw httpx: LiveAPI waits out 429s
    pay = {"payload": {"invoice": {"number": f"INV-{t}", "amount": 1830}}}
    codes = [
        raw.post(f"/triggers/{sid}/fire", json=pay, headers={"Idempotency-Key": f"k1-{t}"}),
        raw.post(f"/triggers/{sid}/fire", json=pay, headers={"Idempotency-Key": f"k1-{t}"}),
        raw.post(f"/triggers/{sid}/fire", json=pay, headers={"Idempotency-Key": f"k2-{t}"}),
        raw.post(f"/triggers/{sid}/fire", json=pay),
        raw.post(f"/triggers/{sid}/fire", json=pay),
    ]
    summary = [(r.status_code, (r.json() or {}).get("skip_reason") if r.status_code == 200
                else None) for r in codes]
    evidence["calls"] = summary
    assert summary[:4] == [(200, None), (200, "dedup"), (200, None), (200, None)], summary
    assert summary[4][0] == 429, f"over the 3/h cap must be 429: {summary}"
    iso = {"other_fire": other.client.post(f"/triggers/{sid}/fire", json=pay).status_code,
           "no_key": httpx.post(f"{BASE_URL}/triggers/{sid}/fire", json=pay,
                                timeout=30).status_code}
    evidence["isolation"] = iso
    assert iso == {"other_fire": 404, "no_key": 401}, iso
    goals = _wait_goals(api, sid, 3)
    assert len(goals) == 3, f"3 distinct calls -> {len(goals)} goals"
    text = _goal_text(api, goals[0]["goal_id"])
    assert f"INV-{t}" in text and "1830 EUR" in text, text[:200]
    _cancel(api, cleanup, sid)


# ── event ─────────────────────────────────────────────────────────────────────


def _publish(api: LiveAPI, channel: str, body: dict[str, Any]) -> int:
    return api.post(f"/triggers/events/{channel}", json=body).status_code


@pytest.mark.scenario("INGRESS-EVENT")
def test_event_triggers(api: LiveAPI, other: LiveAPI, cleanup: Any,
                        evidence: dict[str, Any]) -> None:
    t = tag()
    channel = f"rw.orders.{t}"
    rec = _create(api, cleanup, {"trigger_type": "event", "name": f"rw-in-event-{t}",
                                 "event_channel": channel,
                                 "condition_cel": "payload.total > 1000"},
                  "Review large order {{payload.order.id}} ({{payload.total}}). " + ACK)
    sid = rec["schedule_id"]
    codes = [
        _publish(api, channel, {"event_id": f"o1-{t}", "order": {"id": f"O1-{t}"}, "total": 4200}),
        _publish(api, channel, {"event_id": f"o1-{t}", "order": {"id": f"O1-{t}"}, "total": 4200}),
        _publish(api, channel, {"event_id": f"o2-{t}", "order": {"id": f"O2-{t}"}, "total": 80}),
        _publish(api, channel, {"event_id": f"o3-{t}", "order": {"id": f"O3-{t}"}, "total": 1500,
                                "tenant_plan": "enterprise", "tenant_id": "someone-else"}),
        _publish(other, channel, {"event_id": f"x-{t}", "order": {"id": f"X-{t}"},
                                  "total": 9999}),
    ]
    evidence["publish"] = codes
    assert codes == [202] * 5, codes
    goals = _wait_goals(api, sid, 2, settle=10)
    ids = sorted(json.dumps(g.get("payload")) for g in goals)
    evidence["goals"] = len(goals)
    assert len(goals) == 2, f"want O1 and O3 once each: {ids}"
    assert any(f"O1-{t}" in p for p in ids) and any(f"O3-{t}" in p for p in ids), ids
    assert not any(f"X-{t}" in p for p in ids), "the other tenant's event fired this trigger"
    assert _skips(api, sid).count("condition_false") == 1, _skips(api, sid)
    text = _goal_text(api, goals[0]["goal_id"])
    assert "Review large order O" in text, text[:200]
    _cancel(api, cleanup, sid)

    # A throttled event is dead-lettered (replayable), not lost (B2-1).
    capped_channel = f"rw.capped.{t}"
    capped = _create(api, cleanup, {"trigger_type": "event", "name": f"rw-in-capped-{t}",
                                    "event_channel": capped_channel, "max_firings_per_hour": 1},
                     "Capped event {{payload.n}}. " + ACK)
    for n in (1, 2):
        assert _publish(api, capped_channel, {"event_id": f"c{n}-{t}", "n": n}) == 202
    wait_until(lambda: _skips(api, capped["schedule_id"]), timeout=60, interval=3,
               desc="the throttled event's audit row", done=lambda s: "rate_limit" in s)

    def dlq_rows() -> list[dict[str, Any]]:
        return [d for d in api.get("/triggers/dlq").json()
                if d.get("trigger_id") == capped["schedule_id"]]

    rows = wait_until(dlq_rows, timeout=30, interval=3, desc="the throttled event in the DLQ")
    evidence["dlq"] = [r.get("failure_type") for r in rows]
    assert [r.get("failure_type") for r in rows] == ["RATE_LIMITED"], rows
    _cancel(api, cleanup, capped["schedule_id"])


def _kill_bus_consumers() -> int:
    """CLIENT KILL every connection blocked in XREADGROUP (the trigger consumers)."""
    out = subprocess.run(["docker", "exec", REDIS, "redis-cli", "CLIENT", "LIST"],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    ids = [part.split("=", 1)[1] for line in out.splitlines() if "cmd=xreadgroup" in line
           for part in line.split() if part.startswith("id=")]
    for cid in ids:
        subprocess.run(["docker", "exec", REDIS, "redis-cli", "CLIENT", "KILL", "ID", cid],
                       capture_output=True, text=True, timeout=30, check=False)
    return len(ids)


@pytest.mark.scenario("INGRESS-EVENT-RECONNECT")
def test_event_consumers_survive_redis_errors(api: LiveAPI, cleanup: Any,
                                              evidence: dict[str, Any]) -> None:
    t = tag()
    channel = f"rw.reconnect.{t}"
    rec = _create(api, cleanup, {"trigger_type": "event", "name": f"rw-in-reconn-{t}",
                                 "event_channel": channel}, "Reconnect probe {{payload.n}}. " + ACK)
    sid = rec["schedule_id"]
    kills = []
    for round_ in range(3):  # repeated connection resets, not just one
        kills.append(_kill_bus_consumers())
        time.sleep(2)
    evidence["killed_connections"] = kills
    assert kills[0] > 0, "no XREADGROUP consumer connection found to kill"
    started = time.monotonic()
    assert _publish(api, channel, {"event_id": f"r1-{t}", "n": 1}) == 202
    goals = _wait_goals(api, sid, 1, timeout=120)
    evidence["fire_after_kill_s"] = round(time.monotonic() - started, 1)
    assert len(goals) == 1, f"{len(goals)} goals after the reconnect (want 1)"
    # An event published while the consumers are down is delivered later (streams).
    _kill_bus_consumers()
    assert _publish(api, channel, {"event_id": f"r2-{t}", "n": 2}) == 202
    goals = _wait_goals(api, sid, 2, timeout=120)
    assert len(goals) == 2, f"{len(goals)} goals (want 2)"
    health = api.get("/health").json()
    evidence["health_consumers"] = mask(json.dumps(health)[:400])
    _cancel(api, cleanup, sid)


@pytest.mark.scenario("INGRESS-EVENT-MULTI-REPLICA")
def test_events_fire_once_with_two_api_replicas(api: LiveAPI, cleanup: Any,
                                                evidence: dict[str, Any]) -> None:
    if not SECOND_BACKEND:
        pytest.skip("needs RW_SECOND_BACKEND_CONTAINER (a second API replica on the bus)")
    with contextlib.suppress(Exception):
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", SECOND_BACKEND],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        if state != "true":
            pytest.skip(f"{SECOND_BACKEND} is not running")
    t = tag()
    channel = f"rw.replicas.{t}"
    rec = _create(api, cleanup, {"trigger_type": "event", "name": f"rw-in-replicas-{t}",
                                 "event_channel": channel}, "Replica probe {{payload.n}}. " + ACK)
    sid = rec["schedule_id"]
    n = int(os.getenv("RW_REPLICA_EVENTS", "8"))
    for i in range(n):
        assert _publish(api, channel, {"event_id": f"e{i}-{t}", "n": i}) == 202
    goals = _wait_goals(api, sid, n, settle=15, timeout=180)
    seen = sorted(int((g.get("payload") or {}).get("n", -1)) for g in goals)
    evidence["goals"] = len(goals)
    evidence["consumers"] = subprocess.run(
        ["docker", "exec", REDIS, "redis-cli", "XINFO", "CONSUMERS", "trigger:stream:event",
         "trigger-consumer:event"], capture_output=True, text=True, timeout=30).stdout.count("name")
    assert seen == list(range(n)), f"each event exactly once: {seen}"
    _cancel(api, cleanup, sid)


@pytest.mark.scenario("INGRESS-QUOTA")
def test_free_plan_trigger_quota(other: LiveAPI, evidence: dict[str, Any]) -> None:
    """The free tenant can hold at most PLAN_MAX_TRIGGERS['free'] (5) triggers."""
    cap = int(os.getenv("RW_FREE_TRIGGER_CAP", "5"))
    existing = other.get("/triggers").json()
    made: list[str] = []
    codes: list[int] = []
    try:
        for i in range(cap - len(existing) + 1):
            r = other.post("/triggers", json={
                "spec": {"trigger_type": "webhook", "name": f"rw-in-quota-{i}-{tag()}"},
                "goal_template": "quota probe " + ACK})
            codes.append(r.status_code)
            if r.status_code == 201:
                made.append(r.json()["schedule_id"])
                register_secret(str((r.json().get("spec") or {}).get("webhook_token") or ""))
    finally:
        for sid in made:
            other.delete(f"/triggers/{sid}")
    evidence.update(existing=len(existing), codes=codes)
    if len(existing) >= cap:
        assert codes == [403], codes
    else:
        assert codes[:-1] == [201] * (cap - len(existing)) and codes[-1] == 403, codes
