"""TRG-31: deleting an agent deletes its schedules everywhere, or fails loudly.

The cleanup walked ``schedule_store.list_all`` (this replica's cache), so a
schedule created on another replica survived and kept firing goals for the
deleted agent; any failure was swallowed. (The cross-replica case against real
Postgres is in tests/triggers/test_trigger_persistence_integration.py.)
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.triggers.store import ScheduleStoreUnavailableError
from tests.api.test_agents_api import _CTX, _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}


class _Schedules:
    def __init__(self, *, fail: bool = False) -> None:
        self.deleted_for: list[str] = []
        self._fail = fail

    async def delete_for_agent_async(self, agent_id: str, *, tenant_ctx: Any) -> list[str]:
        assert tenant_ctx.tenant_id == _CTX.tenant_id
        if self._fail:
            raise ScheduleStoreUnavailableError("db down")
        self.deleted_for.append(agent_id)
        return ["s-1"]

    def list_all(self, **_k: Any) -> list[Any]:
        raise AssertionError("must not use the per-process cache")


def _agent(client: TestClient) -> str:
    r = client.post("/agents", json={"name": "a", "goal_template": "g"}, headers=_H)
    return str(r.json()["agent_id"])


def test_agent_delete_removes_its_schedules_through_the_store() -> None:
    app = _make_app()
    app.state.schedule_store = schedules = _Schedules()
    client = TestClient(app, raise_server_exceptions=False)
    agent_id = _agent(client)

    assert client.delete(f"/agents/{agent_id}", headers=_H).status_code == 204
    assert schedules.deleted_for == [agent_id]


def test_schedule_store_outage_is_a_503_and_keeps_the_agent() -> None:
    app = _make_app()
    app.state.schedule_store = _Schedules(fail=True)
    client = TestClient(app, raise_server_exceptions=False)
    agent_id = _agent(client)

    assert client.delete(f"/agents/{agent_id}", headers=_H).status_code == 503
    assert client.get(f"/agents/{agent_id}", headers=_H).status_code == 200  # retryable


def test_unknown_agent_is_a_404_and_touches_no_schedule() -> None:
    app = _make_app()
    app.state.schedule_store = schedules = _Schedules()
    client = TestClient(app, raise_server_exceptions=False)

    assert client.delete("/agents/nope", headers=_H).status_code == 404
    assert schedules.deleted_for == []


async def test_in_memory_store_deletes_only_that_agents_schedules() -> None:
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    mine = await store.create_async(goal_id="g", spec=spec, tenant_ctx=_CTX, agent_id="a1")
    other = await store.create_async(
        goal_id="g",
        spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
        tenant_ctx=_CTX,
        agent_id="a2",
    )

    assert await store.delete_for_agent_async("a1", tenant_ctx=_CTX) == [mine]
    assert [r["schedule_id"] for r in store.list_all(tenant_ctx=_CTX)] == [other]
