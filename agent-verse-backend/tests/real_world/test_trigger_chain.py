"""TRIGGER-CHAIN and TRIGGER-SIGNED-WEBHOOK.

TRIGGER-CHAIN: an HMAC-signed delivery to a published workflow's webhook fires the
producer workflow; its completion announces an event that resumes the consumer
workflow (parked on a wait-for-event step), which books the dispatch with the
producer's data. A replayed delivery id yields a single run; a bad signature is
refused with 401 and starts no run; a forged token is refused with 401.

TRIGGER-SIGNED-WEBHOOK: the generic trigger engine's GitHub-style webhook
(``POST /triggers/webhooks/github/{token}``, X-Hub-Signature-256) fires a goal for a
valid signature, de-duplicates a replayed X-GitHub-Delivery and refuses a bad
signature with 401 without firing anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import httpx
import pytest

from tests.real_world import wf_complex as wfc
from tests.real_world import workflows as wfx
from tests.real_world.helpers import BASE_URL, LiveAPI, mask, register_secret, tag, wait_until


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _runs(api: LiveAPI, wf_id: str) -> list[dict[str, Any]]:
    body = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": wf_id, "per_page": 50})
    return list(body.get("items", []) if isinstance(body, dict) else body)


@pytest.mark.scenario("TRIGGER-CHAIN")
def test_signed_webhook_chains_two_workflows(api: LiveAPI, cleanup: Any,
                                             evidence: dict[str, Any]) -> None:
    k = tag()
    channel = f"rw-batch-ready-{k}"
    secret = secrets.token_hex(24)
    register_secret(secret)
    consumer = wfx.import_yaml(api, cleanup, wfc.chain_consumer_yaml(f"rw-chain-consumer-{k}",
                                                                channel))
    producer = wfx.import_yaml(api, cleanup, wfc.chain_producer_yaml(f"rw-chain-producer-{k}",
                                                                channel, secret))
    evidence.update(consumer_id=consumer, producer_id=producer, channel=channel)
    consumer_run = wfx.trigger_run(api, cleanup, consumer)
    evidence["consumer_run_id"] = consumer_run
    parked = wfx.wait_status(api, consumer_run, {"waiting_timer", "waiting_event"}, 120)
    evidence["consumer_parked_status"] = parked.get("status")
    assert parked.get("status") in ("waiting_timer", "waiting_event"), (
        f"consumer did not park on the event: {parked.get('status')} {mask(parked.get('error'))}"
    )

    cleanup("POST", f"{wfx.V1}/workflows/{producer}/unpublish")
    pub = api.json_ok("POST", f"{wfx.V1}/workflows/{producer}/publish")
    path = str(pub.get("webhook_path") or "")
    assert path.startswith("/wf-hooks/"), f"publish returned no webhook path: {mask(pub)}"
    register_secret(path.rsplit("/", 1)[-1])
    url = f"{BASE_URL}{path}"
    body = json.dumps({"batch": "BATCH-2026-10-02-A", "gross": 82859.0}).encode()
    delivery = f"rw-dlv-{k}"
    headers = {"Content-Type": "application/json", "X-Delivery-Id": delivery,
               "X-AgentVerse-Signature": _sign(secret, body)}
    soft: list[str] = []
    with httpx.Client(timeout=60) as c:
        started = time.monotonic()
        first = c.post(url, content=body, headers=headers)
        evidence["delivery_http"] = first.status_code
        assert first.status_code in (200, 202), f"signed delivery -> {first.status_code}: " \
            f"{mask(first.text[:200])}"
        producer_run = str(first.json().get("run_id"))
        evidence["producer_run_id"] = producer_run
        cleanup("POST", f"{wfx.V1}/runs/{producer_run}/cancel")
        replay = c.post(url, content=body, headers=headers)
        evidence["replay_http"] = replay.status_code
        evidence["replay_body"] = mask(replay.text[:200])
        if replay.status_code in (200, 202) and replay.json().get("run_id") not in (
                None, producer_run):
            soft.append(f"replayed delivery id started a second run {replay.json()['run_id']}")
        bad_body = json.dumps({"batch": "FORGED", "gross": 1}).encode()
        bad = c.post(url, content=bad_body, headers={
            "Content-Type": "application/json", "X-Delivery-Id": f"rw-dlv-bad-{k}",
            "X-AgentVerse-Signature": _sign("not-the-secret", bad_body)})
        evidence["bad_signature_http"] = bad.status_code
        if bad.status_code != 401:
            soft.append(f"a delivery with a bad HMAC signature answered {bad.status_code}, not "
                        "401 (the workflow declares auth: hmac)")
        unsigned = c.post(url, content=bad_body, headers={
            "Content-Type": "application/json", "X-Delivery-Id": f"rw-dlv-unsigned-{k}"})
        evidence["unsigned_http"] = unsigned.status_code
        if unsigned.status_code != 401:
            soft.append(f"an unsigned delivery answered {unsigned.status_code}, not 401")
        forged = c.post(f"{BASE_URL}/wf-hooks/{secrets.token_urlsafe(32)}", content=body,
                        headers=headers)
        evidence["forged_token_http"] = forged.status_code
        if forged.status_code != 401:
            soft.append(f"a forged webhook token answered {forged.status_code}, not 401")

    done = wfx.wait_status(api, producer_run, {"complete"}, wfx.FINISH_TIMEOUT)
    assert done.get("status") == "complete", f"producer {done.get('status')}: " \
        f"{mask(done.get('error'))}"
    announced = wfx.output_of(wfx.get_steps(api, producer_run), "announce")
    evidence["announce_output"] = announced
    time.sleep(3)
    runs = _runs(api, producer)
    evidence["producer_runs"] = [{"id": r.get("run_id"), "status": r.get("status"),
                                  "batch": (r.get("inputs") or {}).get("batch")} for r in runs]
    if len(runs) != 1:
        soft.append(f"{len(runs)} producer runs exist; one signed delivery (+ its replay) and "
                    "two refused deliveries must yield exactly 1")
    if any((r.get("inputs") or {}).get("batch") == "FORGED" for r in runs):
        soft.append("a run was started from the badly signed / unsigned payload")

    finished = wfx.wait_status(api, consumer_run, {"complete"}, wfx.FINISH_TIMEOUT + 60)
    chain_s = time.monotonic() - started
    evidence["consumer_final"] = finished.get("status")
    assert finished.get("status") == "complete", (
        f"the producer's completion event did not resume the consumer: "
        f"{finished.get('status')} {mask(finished.get('error'))}"
    )
    booked = wfx.output_of(wfx.get_steps(api, consumer_run), "book_dispatch")
    evidence["consumer_output"] = booked
    evidence.setdefault("metrics", {})["chain_seconds"] = round(chain_s, 1)
    assert booked.get("received") is True and booked.get("timed_out") is False, booked
    assert booked.get("batch") == "BATCH-2026-10-02-A", booked
    assert float(booked.get("gross") or 0) == 82859.0, booked
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("TRIGGER-SIGNED-WEBHOOK")
def test_trigger_engine_signed_webhook(api: LiveAPI, cleanup: Any,
                                       evidence: dict[str, Any]) -> None:
    secret = secrets.token_hex(20)
    register_secret(secret)
    created = api.post("/triggers", json={
        "spec": {"trigger_type": "github_webhook", "name": f"rw-gh-push-{tag()}",
                 "webhook_secret": secret, "max_firings": 5},
        "goal_template": "A push landed on {{payload.repository}}; reply with the single word "
                         "ACK and nothing else."})
    assert created.status_code == 201, f"POST /triggers -> {created.status_code}: " \
        f"{mask(created.text[:300])}"
    rec = created.json()
    sid = rec["schedule_id"]
    cleanup("DELETE", f"/triggers/{sid}")
    token = str((rec.get("spec") or {}).get("webhook_token") or "")
    register_secret(token)
    evidence["trigger_id"] = sid
    assert token, "the GitHub trigger was issued no webhook token"
    url = f"{BASE_URL}/triggers/webhooks/github/{token}"
    body = json.dumps({"ref": "refs/heads/main", "repository": {"full_name": "larkspur/ops"},
                       "after": "9f1c2e7"}).encode()
    delivery = f"rw-gh-{tag()}"
    good = {"Content-Type": "application/json", "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": _sign(secret, body)}
    soft: list[str] = []
    with httpx.Client(timeout=60) as c:
        bad = c.post(url, content=body, headers={**good, "X-GitHub-Delivery": f"{delivery}-x",
                                                 "X-Hub-Signature-256": _sign("wrong", body)})
        evidence["bad_signature_http"] = bad.status_code
        assert bad.status_code == 401, f"bad signature -> {bad.status_code}"
        first = c.post(url, content=body, headers=good)
        evidence["delivery_http"] = first.status_code
        evidence["delivery_body"] = mask(first.text[:200])
        assert first.status_code in (200, 202) and first.json().get("dispatched") == 1, (
            f"signed delivery -> {first.status_code}: {mask(first.text[:200])}"
        )
        replay = c.post(url, content=body, headers=good)
        evidence["replay_http"] = replay.status_code

    def fired() -> list[dict[str, Any]]:
        resp = api.get(f"/triggers/{sid}/events")
        return [e for e in (resp.json() if resp.status_code == 200 else [])
                if e.get("goal_id") or e.get("goal_created")]

    events = wait_until(fired, timeout=60, interval=3, desc="the trigger to record its firing")
    time.sleep(5)
    events = fired()
    evidence["fired_events"] = [{k: e.get(k) for k in ("goal_id", "skip_reason", "fired_at")}
                                for e in events]
    for e in events:
        if e.get("goal_id"):
            cleanup("POST", f"/goals/{e['goal_id']}/cancel")
    if len(events) != 1:
        soft.append(f"{len(events)} goals fired for one signed delivery + its replay (want 1); "
                    "the bad-signature delivery must fire none")
    assert not soft, "; ".join(soft)
