"""Trigger lifecycle changes must reach the shared stores, not one process's memory.

Regressions covered (unit level; the Postgres side is covered by
``test_trigger_persistence_integration.py``):

* POST /triggers/{id}/resume flipped ``rec["paused"]`` in memory only — the Redis
  copy the beat reads stayed ``paused: true``, so the trigger never fired again.
* PATCH /triggers/{id} changed only the in-memory record/spec, and wiped the
  webhook token when the replacement spec omitted it.
* POST /triggers/{id}/rotate-secret never called the store's persistence, and
  the secret was echoed on every GET/list.
* PLAN_MAX_TRIGGERS was defined but never enforced on create.
* Suppressed dispatches (dedup, condition_false, …) were never audited.
* NLTriggerResolver's fast path dropped the matched cron.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore


class _SyncRedis:
    """Records the JSON payload the beat would read for each schedule key."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


def _client(store: ScheduleStore, *, plan: PlanTier = PlanTier.PROFESSIONAL) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id="t1", plan=plan, api_key_id="k")
        return await call_next(request)

    app.include_router(triggers_router)
    app.state.schedule_store = store
    return TestClient(app)


def _beat_view(redis: _SyncRedis, schedule_id: str) -> dict[str, Any]:
    return json.loads(redis.data[f"schedule:t1:{schedule_id}"])  # type: ignore[no-any-return]


def _create_cron(client: TestClient, cron: str = "0 9 * * *") -> str:
    resp = client.post(
        "/triggers",
        json={"spec": {"trigger_type": "cron", "cron_expression": cron}, "goal_template": "go"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["schedule_id"])


def test_resume_persists_unpaused_state_to_the_beat_store() -> None:
    redis = _SyncRedis()
    client = _client(ScheduleStore(redis=redis))
    sid = _create_cron(client)

    assert client.post(f"/triggers/{sid}/pause").status_code == 200
    assert _beat_view(redis, sid)["paused"] is True

    resp = client.post(f"/triggers/{sid}/resume")
    assert resp.status_code == 200
    assert resp.json()["paused"] is False
    # Old bug: Redis still said paused=True, so the beat skipped it forever.
    assert _beat_view(redis, sid)["paused"] is False


def test_patch_persists_to_the_beat_store_and_keeps_webhook_token() -> None:
    redis = _SyncRedis()
    store = ScheduleStore(redis=redis)
    client = _client(store)
    sid = _create_cron(client)

    resp = client.patch(
        f"/triggers/{sid}",
        json={
            "goal_template": "new goal",
            "spec": {"trigger_type": "cron", "cron_expression": "*/30 * * * *"},
        },
    )
    assert resp.status_code == 200, resp.text
    view = _beat_view(redis, sid)
    assert view["cron_expression"] == "*/30 * * * *"
    assert view["goal_template"] == "new goal"

    # A webhook trigger edited without restating its token keeps the token.
    wh = client.post(
        "/triggers", json={"spec": {"trigger_type": "webhook"}, "goal_template": "hook"}
    ).json()
    token = wh["spec"]["webhook_token"]
    client.patch(f"/triggers/{wh['schedule_id']}", json={"spec": {"trigger_type": "webhook"}})
    rec = store.get(wh["schedule_id"], tenant_ctx=TenantContext(
        tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k"
    ))
    assert rec is not None and rec["spec"].webhook_token == token


def test_rotate_secret_is_persisted_through_the_store_and_returned_once() -> None:
    store = ScheduleStore(redis=_SyncRedis())
    calls: list[dict[str, Any]] = []
    original = store.update_secret_async

    async def _spy(schedule_id: str, **kwargs: Any) -> bool:
        calls.append({"schedule_id": schedule_id, **kwargs})
        return await original(schedule_id, **kwargs)

    store.update_secret_async = _spy  # type: ignore[method-assign]
    client = _client(store)
    sid = client.post(
        "/triggers",
        json={
            "spec": {"trigger_type": "webhook", "webhook_signature_secret": "old-secret"},
            "goal_template": "hook",
        },
    ).json()["schedule_id"]

    resp = client.post(f"/triggers/{sid}/rotate-secret")
    assert resp.status_code == 200
    new_secret = resp.json()["new_secret"]
    assert calls and calls[0]["new_secret"] == new_secret  # old bug: never called
    got = client.get(f"/triggers/{sid}").json()["spec"]
    assert "webhook_signature_secret" not in got  # write-only
    assert got["has_webhook_signature_secret"] is True
    rec = store._data[("t1", sid)]
    assert rec["spec"].webhook_signature_secret == new_secret
    assert rec["previous_webhook_secret"] == "old-secret"


def test_create_enforces_plan_max_triggers() -> None:
    client = _client(ScheduleStore(), plan=PlanTier.FREE)
    for _ in range(5):  # PLAN_MAX_TRIGGERS["free"] == 5
        _create_cron(client)
    resp = client.post(
        "/triggers",
        json={"spec": {"trigger_type": "cron", "cron_expression": "0 9 * * *"}, "goal_template": "x"},
    )
    assert resp.status_code == 403
    assert "quota" in resp.json()["detail"].lower()


# ── Dispatcher: suppressed fires are audited without blocking real ones ──────


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _EventsDb:
    """Fake session factory holding trigger_events rows keyed like the UNIQUE."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def __call__(self) -> _EventsDb:
        return self

    async def __aenter__(self) -> _EventsDb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        params = params or {}
        if sql.startswith("INSERT INTO trigger_events"):
            key = (params["tenant_id"], params["idempotency_key"])
            self.rows.setdefault(key, dict(params))  # ON CONFLICT DO NOTHING
        elif "FROM trigger_events" in sql:
            hit = (params.get("t"), params.get("k")) in self.rows
            return _Result((1,) if hit else None)
        return _Result()

    async def commit(self) -> None:
        return None


class _Goals:
    def __init__(self) -> None:
        self.n = 0

    async def create_goal(self, **_: Any) -> Any:
        self.n += 1
        return SimpleNamespace(goal_id=f"g{self.n}")


@pytest.fixture
def _no_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop(session: Any, _tid: str) -> Any:
        yield session

    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", _noop)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_rls")
async def test_skip_outcomes_are_audited_and_do_not_block_the_real_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _EventsDb()
    goals = _Goals()
    dispatcher = TriggerDispatcher(goal_service=goals, db_session_factory=db)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, condition="payload.ok == 'yes'")
    spec.trigger_id = "trig1"  # type: ignore[attr-defined]
    ctx = SimpleNamespace(tenant_id="t1", plan="professional")

    # 1. condition false → suppressed, but audited with its reason.
    monkeypatch.setattr(dispatcher, "_evaluate_condition", lambda *_a: False)
    skipped = await dispatcher.dispatch(spec, {"x": 1}, ctx)
    assert skipped.skip_reason == "condition_false"
    skip_rows = [r for r in db.rows.values() if r["skip_reason"] == "condition_false"]
    assert len(skip_rows) == 1
    assert skip_rows[0]["idempotency_key"].startswith(f"{skipped.idempotency_key}:skip:")
    assert skip_rows[0]["trigger_type"] != "unknown"

    # 2. The same firing, once the condition holds, is NOT deduped by that audit row.
    monkeypatch.setattr(dispatcher, "_evaluate_condition", lambda *_a: True)
    fired = await dispatcher.dispatch(spec, {"x": 1}, ctx)
    assert fired.goal_created is True and fired.skip_reason is None

    # 3. A replay of the fired event is deduped — and that is audited too, without
    #    displacing the original row.
    replay = await dispatcher.dispatch(spec, {"x": 1}, ctx)
    assert replay.skip_reason == "dedup"
    assert goals.n == 1
    original = db.rows[("t1", fired.idempotency_key)]
    assert original["goal_created"] is True and original["skip_reason"] is None
    assert sum(r["skip_reason"] == "dedup" for r in db.rows.values()) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_rls")
async def test_simulation_skips_are_not_audited() -> None:
    db = _EventsDb()
    dispatcher = TriggerDispatcher(goal_service=_Goals(), db_session_factory=db)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK)
    spec.trigger_id = "trig2"  # type: ignore[attr-defined]
    ctx = SimpleNamespace(tenant_id="t1", plan="professional")
    await dispatcher.dispatch(spec, {"x": 1}, ctx)
    await dispatcher.dispatch(spec, {"x": 1}, ctx, simulation=True)
    assert not any(r["skip_reason"] for r in db.rows.values())


# ── Beat fires: bare trigger id, observed payload reaches the dispatcher ─────


@pytest.mark.asyncio
async def test_beat_dispatch_uses_bare_schedule_id_and_event_payload() -> None:
    from app.scaling.tasks import _dispatch_scheduled_via_dispatcher

    seen: dict[str, Any] = {}

    class _Disp:
        async def dispatch(self, spec: Any, payload: Any, ctx: Any, **kw: Any) -> Any:
            seen.update(spec=spec, payload=payload, kw=kw)
            return SimpleNamespace(goal_created=True)

    tenant = "a" * 32
    sid = "b" * 32
    await _dispatch_scheduled_via_dispatcher(
        f"schedule:{tenant}:{sid}",
        {
            "trigger_type": "rss_feed",
            "tenant_id": tenant,
            "goal_template": "summarise {{payload.title}}",
            "condition": "payload.title != ''",
            "event_payload": {"title": "Hello", "tenant_id": "attacker"},
        },
        fire_instance_id="rss:e1",
        dispatcher=_Disp(),
    )
    # trigger_events.trigger_id is VARCHAR(36): the 74-char Redis key never fit.
    assert seen["spec"].trigger_id == sid
    assert seen["spec"].trigger_type == TriggerType.RSS_FEED
    assert seen["spec"].condition == "payload.title != ''"
    assert seen["payload"]["title"] == "Hello"
    assert seen["payload"]["tenant_id"] == tenant  # schedule fields win
    assert seen["kw"]["txn_id"] == "rss:e1"


# ── NL trigger fast path keeps the cron ──────────────────────────────────────


@pytest.mark.asyncio
async def test_nl_fast_path_keeps_matched_cron() -> None:
    from app.workflow.nl_trigger import NLTriggerResolver
    from app.workflow.trigger_extract import schedule_cron

    t = await NLTriggerResolver().resolve("run this every weekday please")
    assert t.type == "schedule"
    assert t.schedule is not None and t.schedule.cron == "0 9 * * 1-5"
    # The workflow scheduler reads it back from the dumped definition.
    assert schedule_cron(t.model_dump())[0] == "0 9 * * 1-5"


@pytest.mark.asyncio
async def test_nl_llm_cron_answer_maps_to_schedule() -> None:
    from app.workflow.nl_trigger import NLTriggerResolver

    class _LLM:
        async def complete(self, _req: Any) -> Any:
            return SimpleNamespace(content='{"type": "cron", "cron": "15 3 * * *"}')

    t = await NLTriggerResolver(llm_provider=_LLM()).resolve("at quarter past three nightly")
    assert t.type == "schedule"
    assert t.schedule is not None and t.schedule.cron == "15 3 * * *"


def test_manual_fires_of_a_time_trigger_are_not_deduplicated_forever() -> None:
    """Regression: a cron/interval fire without a scheduled time keyed on the
    constant "now", so every manual fire after the first was a duplicate."""
    from app.triggers.dedup import derive_idempotency_key

    a = derive_idempotency_key("t1", "cron", {})
    b = derive_idempotency_key("t1", "cron", {})
    assert a != b
    at = derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="2026-01-01T00:00")
    assert at == derive_idempotency_key("t1", "cron", {}, scheduled_fire_time="2026-01-01T00:00")


def test_goal_chain_fire_without_ids_falls_back_to_the_payload() -> None:
    from app.triggers.dedup import derive_idempotency_key

    a = derive_idempotency_key("t1", "goal_completed", {"goal": "a"})
    b = derive_idempotency_key("t1", "goal_completed", {"goal": "b"})
    assert a != b
