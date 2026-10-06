"""QA-15: editing an agent's trigger replaces its schedule (never leaves the old one).

PUT /agents/{id} stored a new ``trigger_config`` without validating it (no
supported-type / spec / cron plan floor check) and never touched schedules: the
old schedule kept firing with the old cron and goal template, and a newly
configured cron never got a schedule at all.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, ScheduleStoreUnavailableError

_KEY = "av_test_qa15"
_H = {"X-API-Key": _KEY}
_CRON_A = {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5"}
_CRON_B = {"trigger_type": "cron", "cron_expression": "30 18 * * *"}


def _app(
    plan: PlanTier = PlanTier.PROFESSIONAL, schedule_store: Any = None
) -> tuple[TestClient, AgentStore, ScheduleStore, TenantContext]:
    ctx = TenantContext(tenant_id="tid-qa15", plan=plan, api_key_id="k")
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    agents = AgentStore()
    store = schedule_store if schedule_store is not None else ScheduleStore()
    app.include_router(agents_router)
    app.state.agent_store = agents
    app.state.schedule_store = store
    return TestClient(app, raise_server_exceptions=False), agents, store, ctx


def _create(client: TestClient, trigger_config: dict[str, Any], goal: str = "report A") -> str:
    resp = client.post(
        "/agents",
        json={"name": "a", "goal_template": goal, "trigger_config": trigger_config},
        headers=_H,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["agent_id"])


def _schedules(store: ScheduleStore, ctx: TenantContext, agent_id: str) -> list[dict[str, Any]]:
    return [r for r in store.list_all(tenant_ctx=ctx) if r.get("agent_id") == agent_id]


def test_cron_to_manual_removes_the_schedule() -> None:
    client, agents, store, ctx = _app()
    agent_id = _create(client, _CRON_A)
    assert len(_schedules(store, ctx, agent_id)) == 1

    resp = client.put(
        f"/agents/{agent_id}", json={"trigger_config": {"trigger_type": "manual"}}, headers=_H
    )
    assert resp.status_code == 200, resp.text
    assert _schedules(store, ctx, agent_id) == []
    assert agents.get(agent_id, tenant_ctx=ctx)["trigger_config"] == {"trigger_type": "manual"}  # type: ignore[index]


def test_cron_a_to_cron_b_replaces_the_schedule_with_new_cron_and_template() -> None:
    client, _, store, ctx = _app()
    agent_id = _create(client, _CRON_A)
    [old] = _schedules(store, ctx, agent_id)

    resp = client.put(
        f"/agents/{agent_id}",
        json={"trigger_config": _CRON_B, "goal_template": "report B"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    [new] = _schedules(store, ctx, agent_id)
    assert new["schedule_id"] != old["schedule_id"]
    assert new["spec"].cron_expression == "30 18 * * *"
    assert new["goal_template"] == "report B"
    assert resp.json().get("schedule_id") == new["schedule_id"]


def test_goal_template_change_alone_rebuilds_the_schedule() -> None:
    client, _, store, ctx = _app()
    agent_id = _create(client, _CRON_A)
    resp = client.put(f"/agents/{agent_id}", json={"goal_template": "report C"}, headers=_H)
    assert resp.status_code == 200, resp.text
    [new] = _schedules(store, ctx, agent_id)
    assert new["goal_template"] == "report C"
    assert new["spec"].cron_expression == "0 9 * * 1-5"


def test_manual_to_cron_creates_a_schedule() -> None:
    client, _, store, ctx = _app()
    agent_id = _create(client, {})
    assert _schedules(store, ctx, agent_id) == []
    resp = client.put(f"/agents/{agent_id}", json={"trigger_config": _CRON_B}, headers=_H)
    assert resp.status_code == 200, resp.text
    [new] = _schedules(store, ctx, agent_id)
    assert new["spec"].cron_expression == "30 18 * * *"
    assert new["goal_template"] == "report A"


def test_unrelated_update_keeps_the_schedule() -> None:
    client, _, store, ctx = _app()
    agent_id = _create(client, _CRON_A)
    [old] = _schedules(store, ctx, agent_id)
    resp = client.put(
        f"/agents/{agent_id}",
        json={"name": "renamed", "trigger_config": dict(_CRON_A), "goal_template": "report A"},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    [same] = _schedules(store, ctx, agent_id)
    assert same["schedule_id"] == old["schedule_id"]


def _assert_unchanged(
    client: TestClient, store: ScheduleStore, ctx: TenantContext, agent_id: str, old_id: str
) -> None:
    got = client.get(f"/agents/{agent_id}", headers=_H).json()
    assert got["trigger_config"] == _CRON_A
    assert got["goal_template"] == "report A"
    [kept] = _schedules(store, ctx, agent_id)
    assert kept["spec"].cron_expression == "0 9 * * 1-5"
    assert kept["goal_template"] == "report A"
    assert kept["schedule_id"] == old_id


def test_invalid_trigger_is_422_and_nothing_changes() -> None:
    client, _, store, ctx = _app()
    agent_id = _create(client, _CRON_A)
    [old] = _schedules(store, ctx, agent_id)
    for bad in (
        {"trigger_type": "cron", "cron_expression": "not a cron"},
        {"trigger_type": "telepathy"},
    ):
        resp = client.put(
            f"/agents/{agent_id}",
            json={"trigger_config": bad, "goal_template": "changed"},
            headers=_H,
        )
        assert resp.status_code == 422, resp.text
        _assert_unchanged(client, store, ctx, agent_id, old["schedule_id"])


def test_plan_floor_violation_is_422_and_nothing_changes() -> None:
    client, _, store, ctx = _app(PlanTier.FREE)
    agent_id = _create(client, _CRON_A)
    [old] = _schedules(store, ctx, agent_id)
    resp = client.put(
        f"/agents/{agent_id}",
        json={"trigger_config": {"trigger_type": "cron", "cron_expression": "* * * * *"}},
        headers=_H,
    )
    assert resp.status_code == 422, resp.text
    _assert_unchanged(client, store, ctx, agent_id, old["schedule_id"])


def test_replacing_at_the_quota_cap_succeeds() -> None:
    client, _, store, ctx = _app(PlanTier.FREE)
    agent_id = _create(client, _CRON_A)
    for _ in range(4):  # fill PLAN_MAX_TRIGGERS["free"] (5) with the agent's one
        store.create(
            goal_id="g",
            spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
            tenant_ctx=ctx,
        )
    resp = client.put(f"/agents/{agent_id}", json={"trigger_config": _CRON_B}, headers=_H)
    assert resp.status_code == 200, resp.text
    [new] = _schedules(store, ctx, agent_id)
    assert new["spec"].cron_expression == "30 18 * * *"


class _CreateDown(ScheduleStore):
    """Creates work until ``down`` is set; then the store is unavailable."""

    down = False

    async def create_async(self, **kwargs: Any) -> str:  # type: ignore[override]
        if self.down and kwargs.get("goal_template") != "report A":
            raise ScheduleStoreUnavailableError("db down")
        return await super().create_async(**kwargs)


def test_schedule_create_failure_is_503_and_restores_the_old_schedule() -> None:
    store = _CreateDown()
    client, _, _, ctx = _app(schedule_store=store)
    agent_id = _create(client, _CRON_A)
    store.down = True
    resp = client.put(
        f"/agents/{agent_id}",
        json={"trigger_config": _CRON_B, "goal_template": "report B"},
        headers=_H,
    )
    assert resp.status_code == 503, resp.text
    got = client.get(f"/agents/{agent_id}", headers=_H).json()
    assert got["trigger_config"] == _CRON_A
    assert got["goal_template"] == "report A"
    [restored] = _schedules(store, ctx, agent_id)
    assert restored["spec"].cron_expression == "0 9 * * 1-5"
    assert restored["goal_template"] == "report A"


class _DeleteDown(ScheduleStore):
    async def delete_for_agent_async(  # type: ignore[override]
        self, agent_id: str, *, tenant_ctx: TenantContext
    ) -> list[str]:
        raise ScheduleStoreUnavailableError("db down")


def test_schedule_delete_failure_is_503_and_nothing_changes() -> None:
    store = _DeleteDown()
    client, _, _, ctx = _app(schedule_store=store)
    agent_id = _create(client, _CRON_A)
    [old] = _schedules(store, ctx, agent_id)
    resp = client.put(
        f"/agents/{agent_id}", json={"trigger_config": {"trigger_type": "manual"}}, headers=_H
    )
    assert resp.status_code == 503, resp.text
    _assert_unchanged(client, store, ctx, agent_id, old["schedule_id"])


def test_agent_write_failure_after_reschedule_restores_the_old_schedule() -> None:
    client, agents, store, ctx = _app()
    agent_id = _create(client, _CRON_A)

    async def _fail(*_: Any, **__: Any) -> bool:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Agent store unavailable")

    agents.update_async = _fail  # type: ignore[method-assign]
    resp = client.put(
        f"/agents/{agent_id}",
        json={"trigger_config": _CRON_B, "goal_template": "report B"},
        headers=_H,
    )
    assert resp.status_code == 503, resp.text
    [restored] = _schedules(store, ctx, agent_id)
    assert restored["spec"].cron_expression == "0 9 * * 1-5"
    assert restored["goal_template"] == "report A"
