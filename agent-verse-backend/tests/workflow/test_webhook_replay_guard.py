"""WF-REPLAY-1: a signed workflow webhook delivery cannot be replayed.

``POST /wf-hooks/{token}`` with ``auth: hmac`` deduplicated only on the unsigned
delivery-id header, and not at all without one, so a captured signed delivery
replayed (with a fresh header, or none) started another run. The identity now
comes from the signed bytes and an accepted delivery is kept in
``workflow_webhook_replay_guard`` (real-Postgres coverage:
``test_webhook_replay_guard_pg.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import pytest

from app.workflow import webhook_replay
from tests.workflow.test_webhook_hardening import _T, env  # noqa: F401  (fixture)

SECRET = "wf-hmac-" + "r" * 24
CT = {"Content-Type": "application/json"}


def _sig(data: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


async def _published(svc: Any, webhook: dict[str, Any]) -> str:
    wf = await svc.create(
        tenant_id=_T,
        name="signed",
        definition={"name": "signed", "steps": [],
                    "trigger": {"type": "webhook", "webhook": webhook}},
    )
    wid = str(wf["id"])
    await svc.publish(tenant_id=_T, workflow_id=wid)
    return wid


class _Guard:
    """In-memory stand-in for the guard table (keyed like its primary key)."""

    def __init__(self, *, read_error: bool = False) -> None:
        self.rows: dict[tuple[str, str, str], str] = {}
        self.read_error = read_error

    async def already(self, _db: Any, t: str, w: str, k: str, r: str) -> bool:
        if self.read_error:
            raise ConnectionError("pg down")
        return self.rows.get((t, w, k)) == r

    async def record(self, _db: Any, t: str, w: str, k: str, r: str) -> None:
        self.rows[(t, w, k)] = r


@pytest.fixture
def guard(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> _Guard:  # noqa: F811
    g = _Guard()
    monkeypatch.setattr(webhook_replay, "already_accepted", g.already)
    monkeypatch.setattr(webhook_replay, "record_accepted", g.record)
    env["client"].app.state.db_session_factory = object()
    return g


async def _path(env: dict[str, Any], webhook: dict[str, Any]) -> str:  # noqa: F811
    wid = await _published(env["svc"], webhook)
    return str(env["client"].get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"])


@pytest.mark.asyncio
async def test_replay_under_a_fresh_delivery_id_is_refused(
    env: dict[str, Any], guard: _Guard  # noqa: F811
) -> None:
    client, runner = env["client"], env["runner"]
    path = await _path(env, {"auth": "hmac", "hmac_secret": SECRET})
    body = b'{"invoice": "INV-7"}'
    first = client.post(path, content=body,
                        headers={**CT, "X-Signature": _sig(body), "X-Delivery-Id": "d-1"})
    replay = client.post(path, content=body,
                         headers={**CT, "X-Signature": _sig(body), "X-Delivery-Id": "forged-2"})
    bare = client.post(path, content=body, headers={**CT, "X-Signature": _sig(body)})
    assert first.status_code == 200 and first.json()["status"] == "accepted"
    assert replay.status_code == 200 and replay.json()["status"] == "duplicate"
    assert bare.json()["status"] == "duplicate"
    assert len(runner.runs) == 1
    # The run's identity is the signed body, not the unsigned header.
    assert runner.runs[0]["idempotency_key"].startswith("signed-body:")
    assert len(guard.rows) == 1
    assert SECRET not in next(iter(guard.rows.values()))  # a fingerprint, never the secret


@pytest.mark.asyncio
async def test_without_a_guard_db_the_signed_identity_still_dedupes(
    env: dict[str, Any],  # noqa: F811
) -> None:
    """No header at all used to mean no dedup: the run store index now keys on
    the signed body."""
    client, runner = env["client"], env["runner"]
    path = await _path(env, {"auth": "hmac", "hmac_secret": SECRET})
    body = b'{"n": 1}'
    a = client.post(path, content=body, headers={**CT, "X-Signature": _sig(body)})
    b = client.post(path, content=body, headers={**CT, "X-Signature": _sig(body)})
    assert a.json()["run_id"] == b.json()["run_id"]
    assert len(runner.runs) == 1


@pytest.mark.asyncio
async def test_timestamped_deliveries_key_on_the_signed_bytes(
    env: dict[str, Any], guard: _Guard  # noqa: F811
) -> None:
    client, runner = env["client"], env["runner"]
    path = await _path(env, {"auth": "hmac", "hmac_secret": SECRET})
    body = b'{"n": 1}'
    ts = str(int(time.time()))
    signed = {**CT, "X-Webhook-Timestamp": ts, "X-Signature": _sig(f"{ts}.".encode() + body)}
    first = client.post(path, content=body, headers={**signed, "X-Delivery-Id": "d-9"})
    # Replayed inside the window under a fresh delivery id: same signed bytes.
    again = client.post(path, content=body, headers={**signed, "X-Delivery-Id": "fresh"})
    assert first.json()["status"] == "accepted"
    assert again.json()["status"] == "duplicate"
    # A sender's own retry re-signed later is the same run (delivery id identity).
    ts2 = str(int(time.time()) + 1)
    resigned = {**CT, "X-Webhook-Timestamp": ts2,
                "X-Signature": _sig(f"{ts2}.".encode() + body), "X-Delivery-Id": "d-9"}
    retry = client.post(path, content=body, headers=resigned)
    assert retry.json()["run_id"] == first.json()["run_id"]
    assert len(runner.runs) == 1
    assert runner.runs[0]["idempotency_key"] == "webhook:d-9"
    assert all(k[2].startswith("signed-ts:") for k in guard.rows)


@pytest.mark.asyncio
async def test_unreadable_guard_refuses_with_503(
    env: dict[str, Any], guard: _Guard  # noqa: F811
) -> None:
    client, runner = env["client"], env["runner"]
    path = await _path(env, {"auth": "hmac", "hmac_secret": SECRET})
    guard.read_error = True
    body = b"{}"
    r = client.post(path, content=body, headers={**CT, "X-Signature": _sig(body)})
    assert r.status_code == 503
    assert runner.runs == []


@pytest.mark.asyncio
async def test_a_refused_delivery_is_not_remembered(
    env: dict[str, Any], guard: _Guard  # noqa: F811
) -> None:
    """Only accepted deliveries are recorded: a bad signature leaves no row."""
    client = env["client"]
    path = await _path(env, {"auth": "hmac", "hmac_secret": SECRET})
    body = b"{}"
    assert client.post(path, content=body,
                       headers={**CT, "X-Signature": _sig(body, "wrong")}).status_code == 401
    assert guard.rows == {}


@pytest.mark.asyncio
async def test_unsigned_workflow_keeps_the_delivery_id_identity(
    env: dict[str, Any], guard: _Guard  # noqa: F811
) -> None:
    client, runner = env["client"], env["runner"]
    path = await _path(env, {"auth": "none"})
    client.post(path, json={"n": 1}, headers={"X-Delivery-Id": "d-1"})
    client.post(path, json={"n": 1}, headers={"X-Delivery-Id": "d-2"})
    assert len(runner.runs) == 2
    assert runner.runs[0]["idempotency_key"] == "webhook:d-1"
    assert guard.rows == {}


def test_signed_replay_key_and_secret_ref() -> None:
    body = b'{"a": 1}'
    assert webhook_replay.signed_replay_key({}, body).startswith("signed-body:")
    ts_key = webhook_replay.signed_replay_key({"x-webhook-timestamp": "1700000000"}, body)
    assert ts_key.startswith("signed-ts:")
    assert ts_key != webhook_replay.signed_replay_key({"x-webhook-timestamp": "1700000001"}, body)
    assert webhook_replay.secret_ref("a") != webhook_replay.secret_ref("b")
    assert len(webhook_replay.secret_ref("a")) == 32
