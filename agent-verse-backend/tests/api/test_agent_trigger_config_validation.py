"""TRG-50: POST /agents trigger_config goes through the same gate as POST /triggers.

The agent-create path built its schedule with the sync fire-and-forget
``create()``: no ``creatable_error`` (unsupported type / invalid spec / cron plan
floor), no PLAN_MAX_TRIGGERS quota, and any failure was swallowed — the API
answered 201 for an agent whose schedule did not exist.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, ScheduleStoreUnavailableError

_KEY = "av_test_trg50"


def _app(plan: PlanTier, schedule_store: Any) -> tuple[TestClient, AgentStore, TenantContext]:
    ctx = TenantContext(tenant_id="tid-trg50", plan=plan, api_key_id="k")
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    agents = AgentStore()
    app.include_router(agents_router)
    app.state.agent_store = agents
    app.state.schedule_store = schedule_store
    return TestClient(app, raise_server_exceptions=False), agents, ctx


def _post(client: TestClient, trigger_config: dict[str, Any]) -> Any:
    return client.post(
        "/agents",
        json={"name": "a", "goal_template": "do it", "trigger_config": trigger_config},
        headers={"X-API-Key": _KEY},
    )


def test_every_minute_cron_on_free_plan_is_422_and_creates_nothing() -> None:
    store = ScheduleStore()
    client, agents, ctx = _app(PlanTier.FREE, store)
    resp = _post(client, {"trigger_type": "cron", "cron_expression": "* * * * *"})
    assert resp.status_code == 422, resp.text
    assert agents.list_all(tenant_ctx=ctx) == []
    assert store.list_all(tenant_ctx=ctx) == []


def test_invalid_spec_is_422() -> None:
    client, agents, ctx = _app(PlanTier.ENTERPRISE, ScheduleStore())
    resp = _post(client, {"trigger_type": "cron", "cron_expression": "not a cron"})
    assert resp.status_code == 422
    assert agents.list_all(tenant_ctx=ctx) == []


def test_unknown_trigger_type_is_422() -> None:
    client, agents, ctx = _app(PlanTier.ENTERPRISE, ScheduleStore())
    resp = _post(client, {"trigger_type": "telepathy"})
    assert resp.status_code == 422
    assert agents.list_all(tenant_ctx=ctx) == []


def test_quota_exceeded_is_403_and_no_orphan_agent() -> None:
    store = ScheduleStore()
    client, agents, ctx = _app(PlanTier.FREE, store)
    for _ in range(5):  # PLAN_MAX_TRIGGERS["free"]
        store.create(
            goal_id="g",
            spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
            tenant_ctx=ctx,
        )
    resp = _post(client, {"trigger_type": "interval", "interval_seconds": 3600})
    assert resp.status_code == 403, resp.text
    assert agents.list_all(tenant_ctx=ctx) == []
    assert len(store.list_all(tenant_ctx=ctx)) == 5


def test_schedule_store_outage_is_503_and_no_orphan_agent() -> None:
    class _Down(ScheduleStore):
        async def create_async(self, **_: Any) -> str:  # type: ignore[override]
            raise ScheduleStoreUnavailableError("db down")

    client, agents, ctx = _app(PlanTier.ENTERPRISE, _Down())
    resp = _post(client, {"trigger_type": "interval", "interval_seconds": 3600})
    assert resp.status_code == 503
    assert agents.list_all(tenant_ctx=ctx) == []


def test_valid_trigger_config_creates_the_schedule_before_201() -> None:
    store = ScheduleStore()
    client, agents, ctx = _app(PlanTier.PROFESSIONAL, store)
    resp = _post(client, {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5"})
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["agent_id"] if "agent_id" in resp.json() else resp.json()["id"]
    [rec] = store.list_all(tenant_ctx=ctx)
    assert rec["agent_id"] == agent_id
    assert rec["spec"].cron_expression == "0 9 * * 1-5"
    assert resp.json().get("schedule_id") == rec["schedule_id"]


@pytest.mark.parametrize("cfg", [{}, {"trigger_type": "manual"}, {"trigger_type": ""}])
def test_manual_or_absent_trigger_creates_no_schedule(cfg: dict[str, Any]) -> None:
    store = ScheduleStore()
    client, _, ctx = _app(PlanTier.FREE, store)
    assert _post(client, cfg).status_code == 201
    assert store.list_all(tenant_ctx=ctx) == []
