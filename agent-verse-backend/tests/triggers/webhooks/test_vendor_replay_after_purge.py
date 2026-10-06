"""DEF-NEW-3: body-signed vendor webhooks stay unreplayable after the dedup purge.

GitHub / Jira (and the other body-only vendors) sign nothing but the body, so a
delivery is deduplicated on its signed body through the dispatcher's durable
``trigger_events`` row — purged after ``DATA_RETENTION_DAYS``. A captured
delivery replayed after the purge verified again and fired again.

* Jira carries the event time in the signed body: a delivery older than the
  acceptance window (72 h, never longer than the retention) is refused, so every
  accepted one is still covered by its dedup row.
* GitHub has no signed age: the signed-body key of a delivery that ran is kept in
  ``vendor_webhook_replay_guard`` while the secret that signed it is accepted,
  and a replay found there is a dedup no-op (no goal), even after the purge.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from app.triggers.webhooks import replay

TOKEN = "replay_" + "r" * 40
SECRET = "whsec-replay-test-secret"
TENANT = "t-replay"


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# ── pure rules ────────────────────────────────────────────────────────────────


def test_jira_event_time_is_read_from_the_signed_body() -> None:
    assert replay.signed_event_time("jira", {"timestamp": 1_727_690_000_000}) == 1_727_690_000.0
    assert replay.signed_event_time("jira", {"timestamp": "1727690000000"}) == 1_727_690_000.0
    assert replay.signed_event_time("jira", {"timestamp": 1_727_690_000}) == 1_727_690_000.0
    for missing in ({}, {"timestamp": None}, {"timestamp": True}, {"timestamp": "x"}):
        assert replay.signed_event_time("jira", missing) is None
    # GitHub signs no event time.
    assert replay.signed_event_time("github", {"timestamp": 1_727_690_000_000}) is None


def test_freshness_window() -> None:
    now = 2_000_000_000.0
    replay.check_signed_freshness(now - 60, now=now)
    replay.check_signed_freshness(now - replay.SIGNED_MAX_AGE_SECONDS + 5, now=now)
    with pytest.raises(HTTPException) as stale:
        replay.check_signed_freshness(now - replay.SIGNED_MAX_AGE_SECONDS - 5, now=now)
    assert stale.value.status_code == 401
    with pytest.raises(HTTPException):
        replay.check_signed_freshness(now + replay.FUTURE_SKEW_SECONDS + 60, now=now)


def test_window_never_outlives_the_dedup_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_RETENTION_DAYS", "1")
    assert replay.max_signed_age_seconds() == 86400
    now = 2_000_000_000.0
    with pytest.raises(HTTPException):
        replay.check_signed_freshness(now - 2 * 86400, now=now)
    monkeypatch.setenv("DATA_RETENTION_DAYS", "90")
    assert replay.max_signed_age_seconds() == replay.SIGNED_MAX_AGE_SECONDS


def test_which_deliveries_need_the_durable_guard() -> None:
    assert replay.needs_durable_guard("github", {"action": "opened"})
    assert replay.needs_durable_guard("jira", {"webhookEvent": "x"})  # no timestamp
    assert not replay.needs_durable_guard("jira", {"timestamp": 1_727_690_000_000})
    assert not replay.needs_durable_guard("stripe", {"id": "evt_1"})  # signs a timestamp


def test_purge_keeps_live_secrets_rows_and_exempts_filters() -> None:
    sql = replay.purge_sql(" AND tenant_id NOT IN (SELECT 'held')")
    assert "s.id IS NULL" in sql  # trigger deleted
    assert "webhook_secret_grace_until < NOW()" in sql  # rotation completed
    assert sql.rstrip().endswith("AND tenant_id NOT IN (SELECT 'held') LIMIT :lim)")


# ── the typed webhook endpoint ────────────────────────────────────────────────


class _Dispatcher:
    """Fires every delivery: as if its trigger_events dedup row had been purged."""

    def __init__(self, outcome: str | None = None) -> None:
        self.fired: list[dict[str, Any]] = []
        self.outcome = outcome

    async def dispatch(self, spec: Any, payload: dict[str, Any], ctx: Any, **kw: Any) -> Any:
        self.fired.append(payload)
        if self.outcome:
            return SimpleNamespace(skip_reason=self.outcome, goal_id=None, goal_created=False)
        return SimpleNamespace(skip_reason=None, goal_id=f"g{len(self.fired)}", goal_created=True)


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


def _client(
    ttype: TriggerType, dispatcher: _Dispatcher, db: _GuardDb | None
) -> TestClient:
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=ttype, webhook_token=TOKEN, webhook_signature_secret=SECRET),
        tenant_ctx=TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="k"),
        goal_template="triage it",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    if db is not None:
        app.state.db_session_factory = db
    return TestClient(app, raise_server_exceptions=False)


def _github(client: TestClient, body: bytes, delivery: str) -> Any:
    return client.post(
        f"/triggers/webhooks/github/{TOKEN}",
        content=body,
        headers={
            "content-type": "application/json",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sig(body),
        },
    )


def _jira(client: TestClient, body: bytes) -> Any:
    return client.post(
        f"/triggers/webhooks/jira/{TOKEN}",
        content=body,
        headers={"content-type": "application/json", "X-Hub-Signature": _sig(body)},
    )


_GH_BODY = json.dumps(
    {"action": "opened", "issue": {"number": 7, "title": "Checkout 500s"},
     "repository": {"full_name": "octo/shop"}},
    separators=(",", ":"),
).encode()


def test_github_replay_after_the_dedup_purge_does_not_fire_again() -> None:
    db, dispatcher = _GuardDb(), _Dispatcher()
    client = _client(TriggerType.GITHUB_WEBHOOK, dispatcher, db)

    first = _github(client, _GH_BODY, "d-1")
    assert first.status_code == 200, first.text
    assert len(dispatcher.fired) == 1 and len(db.rows) == 1

    # Months later (trigger_events purged), the captured delivery is replayed
    # under a fresh, unsigned X-GitHub-Delivery header.
    replayed = _github(client, _GH_BODY, "d-attacker")
    assert replayed.status_code == 200
    assert replayed.json()["skipped"] == ["dedup"] and replayed.json()["goal_ids"] == []
    assert len(dispatcher.fired) == 1  # never reached the dispatcher

    # A genuinely new delivery still fires.
    other = _GH_BODY.replace(b'"number":7', b'"number":8')
    assert _github(client, other, "d-2").status_code == 200
    assert len(dispatcher.fired) == 2


def test_throttled_github_delivery_is_not_remembered() -> None:
    db = _GuardDb()
    throttled = _Dispatcher(outcome="rate_limit")
    assert _github(_client(TriggerType.GITHUB_WEBHOOK, throttled, db), _GH_BODY, "d-1").status_code == 429
    assert db.rows == set()  # the sender's retry must still be deliverable
    ok = _Dispatcher()
    assert _github(_client(TriggerType.GITHUB_WEBHOOK, ok, db), _GH_BODY, "d-1").status_code == 200
    assert len(ok.fired) == 1 and len(db.rows) == 1


def test_guard_read_failure_fails_closed_with_a_retryable_503() -> None:
    db, dispatcher = _GuardDb(), _Dispatcher()
    db.fail_reads = True
    r = _github(_client(TriggerType.GITHUB_WEBHOOK, dispatcher, db), _GH_BODY, "d-1")
    assert r.status_code == 503
    assert dispatcher.fired == []


def _jira_body(ts_ms: int | None) -> bytes:
    payload: dict[str, Any] = {"webhookEvent": "jira:issue_created",
                               "issue": {"id": "10002", "key": "OPS-42"}}
    if ts_ms is not None:
        payload["timestamp"] = ts_ms
    return json.dumps(payload, separators=(",", ":")).encode()


def test_stale_jira_delivery_is_refused() -> None:
    db, dispatcher = _GuardDb(), _Dispatcher()
    client = _client(TriggerType.JIRA_WEBHOOK, dispatcher, db)
    old_ms = int((time.time() - replay.SIGNED_MAX_AGE_SECONDS - 3600) * 1000)
    r = _jira(client, _jira_body(old_ms))
    assert r.status_code == 401 and "Stale" in r.json()["detail"]
    assert dispatcher.fired == []


def test_fresh_jira_delivery_fires_without_a_guard_row() -> None:
    db, dispatcher = _GuardDb(), _Dispatcher()
    client = _client(TriggerType.JIRA_WEBHOOK, dispatcher, db)
    r = _jira(client, _jira_body(int(time.time() * 1000)))
    assert r.status_code == 200, r.text
    assert len(dispatcher.fired) == 1
    assert db.rows == set()  # covered by its trigger_events row for the whole window


def test_jira_delivery_without_a_timestamp_uses_the_durable_guard() -> None:
    db, dispatcher = _GuardDb(), _Dispatcher()
    client = _client(TriggerType.JIRA_WEBHOOK, dispatcher, db)
    body = _jira_body(None)
    assert _jira(client, body).status_code == 200
    assert _jira(client, body).json()["skipped"] == ["dedup"]
    assert len(dispatcher.fired) == 1


def test_trigger_without_a_secret_is_unaffected() -> None:
    store = ScheduleStore()
    store.create(
        goal_id="",
        spec=TriggerSpec(trigger_type=TriggerType.GITHUB_WEBHOOK, webhook_token=TOKEN),
        tenant_ctx=TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="k"),
        goal_template="triage it",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    db, dispatcher = _GuardDb(), _Dispatcher()
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    app.state.db_session_factory = db
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"/triggers/webhooks/github/{TOKEN}", json={"a": 1}).status_code == 200
    assert db.rows == set()
