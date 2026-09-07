"""WT-2 (G3/G4): fire returns the real goal_id; simulate passes simulation=True."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.triggers import fire_trigger_now, simulate_trigger
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.events import TriggerEvent


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _spec():
    return SimpleNamespace(trigger_type=SimpleNamespace(value="webhook"), simulation_mode=False)


def _request(*, dispatcher, store):
    state = SimpleNamespace(trigger_dispatcher=dispatcher, schedule_store=store)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app, state=SimpleNamespace(tenant=_tenant()))


async def test_fire_returns_real_goal_id():
    """FAILS TODAY: reads event.goal_id_created (does not exist) -> always None."""
    import datetime

    event = TriggerEvent(
        event_id="e1",
        tenant_id="t1",
        trigger_id="tr1",
        trigger_type="webhook",
        idempotency_key="idem",
        fired_at=datetime.datetime.now(datetime.UTC),
        goal_created=True,
        goal_id="g-9",
    )

    class _Dispatcher:
        async def dispatch(self, spec, payload, tenant_ctx, **kw):
            return event

    class _Store:
        def get(self, sid, tenant_ctx=None):
            return {"spec": _spec(), "paused": False}

    resp = await fire_trigger_now(
        "tr1", _request(dispatcher=_Dispatcher(), store=_Store()), SimpleNamespace(payload={"x": 1})
    )
    assert resp["goal_id"] == "g-9"
    assert resp["goal_created"] is True


async def test_simulate_uses_simulation_kwarg():
    """FAILS TODAY: passes simulate=True; the dispatcher kwarg is simulation=."""
    seen: dict[str, object] = {}

    class _Dispatcher:
        async def dispatch(self, spec, payload, tenant_ctx, **kw):
            seen.update(kw)
            return SimpleNamespace(would_fire=True)

    class _Store:
        def get(self, sid, tenant_ctx=None):
            return {"spec": _spec(), "paused": False}

    await simulate_trigger(
        "tr1", _request(dispatcher=_Dispatcher(), store=_Store()), SimpleNamespace(payload={"x": 1})
    )
    assert seen.get("simulation") is True, "simulate must call dispatch(simulation=True)"
    assert "simulate" not in seen, "the wrong 'simulate=' kwarg must not be used"
