"""B2-GAP-1: signed generic ``webhook`` deliveries cannot be replayed under a fresh id.

The legacy generic signature (no ``X-Webhook-Timestamp``) covers only the body,
and the delivery id (``X-Delivery-Id`` / ``Idempotency-Key`` / ...) is an
unsigned header. The firing identity was that header (or the body hash inside a
300 s window), so a captured delivery replayed under a fresh header — or after
the window — verified and fired again. Same hole GitHub had (DEF-NEW-3), same
fix: identity from the signed bytes, and the signed key of every delivery that
ran kept in ``vendor_webhook_replay_guard``. A timestamped delivery is guarded on
its exact signed bytes for the replay window.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.schedules import webhooks_router
from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from app.triggers.webhooks import ingress, replay

TOKEN = "genreplay_" + "g" * 40
SECRET = "generic-replay-test-secret"
TENANT = "t-generic-replay"
CTX = TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="k")
BODY = b'{"ticket":"TCK-1","action":"refund","amount":40}'


def _sig(data: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def _legacy_headers(body: bytes, delivery: str) -> dict[str, str]:
    return {"content-type": "application/json", "x-signature": _sig(body),
            "x-delivery-id": delivery}


def _ts_headers(body: bytes, delivery: str, ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    return {"content-type": "application/json", "x-webhook-timestamp": stamp,
            "x-signature": _sig(f"{stamp}.".encode() + body), "x-delivery-id": delivery}


# ── pure rules ────────────────────────────────────────────────────────────────


def test_replay_key_comes_only_from_signed_bytes() -> None:
    a = ingress.signed_replay_key({"x-delivery-id": "d-1"}, BODY)
    b = ingress.signed_replay_key({"x-delivery-id": "d-attacker"}, BODY)
    assert a == b and a.startswith("signed-body:")
    assert ingress.signed_replay_key({}, BODY + b" ") != a
    t1 = ingress.signed_replay_key({"x-webhook-timestamp": "1700000000"}, BODY)
    t2 = ingress.signed_replay_key({"x-webhook-timestamp": "1700000001"}, BODY)
    assert t1.startswith("signed-ts:") and t1 != t2


def test_purge_drops_timestamped_rows_after_the_window_only() -> None:
    sql = replay.purge_sql()
    assert "left(g.signed_key, 10) = 'signed-ts:'" in sql
    assert f"secs => {replay.TIMESTAMPED_GUARD_TTL_SECONDS}" in sql
    assert replay.TIMESTAMPED_GUARD_TTL_SECONDS > 2 * ingress.REPLAY_WINDOW_SECONDS
    assert "s.id IS NULL" in sql  # the vendor rules are unchanged


# ── fakes ─────────────────────────────────────────────────────────────────────


class _Dispatcher:
    """Fires every delivery: as if its trigger_events dedup row had been purged."""

    def __init__(self, outcome: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome

    async def dispatch(self, spec: Any, payload: dict[str, Any], ctx: Any, **kw: Any) -> Any:
        self.calls.append({"payload": payload, **kw})
        if self.outcome:
            return SimpleNamespace(skip_reason=self.outcome, goal_id=None, goal_created=False,
                                   fired_at=None)
        return SimpleNamespace(skip_reason=None, goal_id=f"g{len(self.calls)}",
                               goal_created=True, fired_at=None)


class _GuardDb:
    """vendor_webhook_replay_guard in memory, behind a session-factory shape."""

    def __init__(self) -> None:
        self.rows: set[tuple[str, str, str]] = set()
        self.fail_reads = False

    def __call__(self) -> _GuardDb:
        return self

    async def __aenter__(self) -> _GuardDb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> contextlib.AbstractAsyncContextManager[None]:
        return contextlib.nullcontext()  # type: ignore[return-value]

    async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        sql = str(stmt)
        assert "vendor_webhook_replay_guard" in sql
        row = (params["t"], params["tr"], params["k"])
        if sql.lstrip().startswith("SELECT"):
            if self.fail_reads:
                raise RuntimeError("db down")
            return SimpleNamespace(first=lambda: (1,) if row in self.rows else None)
        self.rows.add(row)
        return SimpleNamespace()


@pytest.fixture(autouse=True)
def _no_rls() -> Any:
    with patch(
        "app.db.rls.sqlalchemy_rls_context", lambda session, tenant_id: contextlib.nullcontext()
    ):
        yield


def _client(dispatcher: _Dispatcher, db: _GuardDb | None, *, secret: str = SECRET) -> TestClient:
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token=TOKEN,
                         webhook_signature_secret=secret),
        tenant_ctx=CTX,
        goal_template="Ticket {{payload.ticket}}",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    if db is not None:
        app.state.db_session_factory = db
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: bytes, headers: dict[str, str]) -> Any:
    return client.post(f"/triggers/webhooks/webhook/{TOKEN}", content=body, headers=headers)


# ── POST /triggers/webhooks/webhook/{token} ───────────────────────────────────


def test_legacy_replay_under_a_fresh_delivery_id_does_not_fire_again() -> None:
    db, disp = _GuardDb(), _Dispatcher()
    client = _client(disp, db)

    first = _post(client, BODY, _legacy_headers(BODY, "d-1"))
    assert first.status_code == 200, first.text
    assert len(disp.calls) == 1 and len(db.rows) == 1
    (row,) = db.rows
    assert row[2].startswith("signed-body:")
    # The dispatcher dedups on the signed body too, not on the unsigned header.
    assert disp.calls[0]["message_id"] == row[2]

    # Later (window passed, trigger_events purged), the captured delivery is
    # replayed under a fresh, unsigned delivery id.
    replayed = _post(client, BODY, _legacy_headers(BODY, "d-attacker"))
    assert replayed.status_code == 200
    assert replayed.json()["skipped"] == ["dedup"] and replayed.json()["goal_ids"] == []
    assert len(disp.calls) == 1  # never reached the dispatcher

    # A genuinely new delivery still fires.
    other = BODY.replace(b"TCK-1", b"TCK-2")
    assert _post(client, other, _legacy_headers(other, "d-2")).status_code == 200
    assert len(disp.calls) == 2


def test_legacy_identity_ignores_the_unsigned_header_even_without_the_guard_db() -> None:
    disp = _Dispatcher()
    client = _client(disp, None)
    assert _post(client, BODY, _legacy_headers(BODY, "d-1")).status_code == 200
    assert _post(client, BODY, _legacy_headers(BODY, "d-attacker")).status_code == 200
    ids = {c["message_id"] for c in disp.calls}
    # One durable identity: the dispatcher's trigger_events gate dedups the replay.
    assert len(ids) == 1 and next(iter(ids)).startswith("signed-body:")


def test_timestamped_replay_inside_the_window_under_a_fresh_id_is_refused() -> None:
    db, disp = _GuardDb(), _Dispatcher()
    client = _client(disp, db)
    ts = int(time.time())
    first = _post(client, BODY, _ts_headers(BODY, "d-1", ts))
    assert first.status_code == 200, first.text
    # The sender's delivery id stays the dispatcher identity (its retries re-sign
    # with a fresh timestamp but repeat the id).
    assert disp.calls[0]["message_id"] == "delivery:d-1"
    (row,) = db.rows
    assert row[2].startswith("signed-ts:")

    replayed = _post(client, BODY, _ts_headers(BODY, "d-attacker", ts))
    assert replayed.json()["skipped"] == ["dedup"]
    assert len(disp.calls) == 1

    # A new delivery of the same body (new signed timestamp) still fires.
    assert _post(client, BODY, _ts_headers(BODY, "d-2", ts + 1)).status_code == 200
    assert len(disp.calls) == 2


def test_throttled_legacy_delivery_is_not_remembered() -> None:
    db = _GuardDb()
    throttled = _Dispatcher(outcome="rate_limit")
    assert _post(_client(throttled, db), BODY, _legacy_headers(BODY, "d-1")).status_code == 429
    assert db.rows == set()  # the sender's retry must still land
    ok = _Dispatcher()
    assert _post(_client(ok, db), BODY, _legacy_headers(BODY, "d-1")).status_code == 200
    assert len(ok.calls) == 1 and len(db.rows) == 1


def test_guard_read_failure_fails_closed_with_a_retryable_503() -> None:
    db, disp = _GuardDb(), _Dispatcher()
    db.fail_reads = True
    assert _post(_client(disp, db), BODY, _legacy_headers(BODY, "d-1")).status_code == 503
    assert disp.calls == []


def test_unsigned_trigger_is_unaffected() -> None:
    db, disp = _GuardDb(), _Dispatcher()
    client = _client(disp, db, secret="")
    r = _post(client, BODY, {"content-type": "application/json", "x-delivery-id": "d-1"})
    assert r.status_code == 200
    assert db.rows == set() and disp.calls[0]["message_id"] == "delivery:d-1"


def test_forged_signature_never_touches_the_guard() -> None:
    db, disp = _GuardDb(), _Dispatcher()
    headers = _legacy_headers(BODY, "d-1") | {"x-signature": _sig(BODY, "not-the-secret")}
    assert _post(_client(disp, db), BODY, headers).status_code == 401
    assert db.rows == set() and disp.calls == []


# ── POST /webhooks/{token} (API-key) ──────────────────────────────────────────


def test_api_key_webhook_endpoint_applies_the_same_guard() -> None:
    _keys = {"key-a": CTX}

    async def _resolve(key: str) -> TenantContext | None:
        return _keys.get(key)

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(webhooks_router)
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token=TOKEN,
                         webhook_signature_secret=SECRET),
        tenant_ctx=CTX,
        goal_template="x",
    )
    db, disp = _GuardDb(), _Dispatcher()
    app.state.schedule_store = store
    app.state.trigger_dispatcher = disp
    app.state.db_session_factory = db
    client = TestClient(app, raise_server_exceptions=False)

    def _send(delivery: str) -> Any:
        return client.post(f"/webhooks/{TOKEN}", content=BODY,
                           headers={"X-API-Key": "key-a", **_legacy_headers(BODY, delivery)})

    first = _send("d-1")
    assert first.status_code == 202, first.text
    assert first.json()["goal_id"] == "g1"
    replayed = _send("d-attacker")
    assert replayed.status_code == 202
    assert replayed.json()["skip_reason"] == "dedup" and replayed.json()["goal_id"] is None
    assert len(disp.calls) == 1
    assert disp.calls[0]["message_id"].startswith("signed-body:")

    db.fail_reads = True
    assert _send("d-3").status_code == 503
    assert len(disp.calls) == 1
