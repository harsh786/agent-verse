"""POST /tenants/me/export (GDPR data export) must be complete or fail loudly.

Regressions:
* goals: the endpoint called ``goal_service.list_goals()`` once with its
  default ``limit=50``, so a tenant with more than 50 goals got a silently
  truncated export.
* agents: it called ``agent_store.list(...)``, a method the real
  ``AgentStore`` does not have; the AttributeError was swallowed and every
  export reported ``agents: []``.
* any read failure was swallowed into an empty list, so an outage produced a
  plausible-looking but empty export.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.tenancy.context import PlanTier, TenantContext
from tests.api.test_tenants_uncovered import _CTX, H, _make_app

_OTHER = TenantContext(tenant_id="tid-other", plan=PlanTier.FREE, api_key_id="kid-other")


class _PagedGoalService:
    """Mimics GoalService.list_goals paging: limit clamped to [1, 200]."""

    def __init__(self, goals_by_tenant: dict[str, int]) -> None:
        self._goals = {
            tid: [{"id": f"{tid}-g{i}", "goal": f"goal {i}"} for i in range(n)]
            for tid, n in goals_by_tenant.items()
        }
        self.calls: list[tuple[int, int]] = []

    async def list_goals(
        self, tenant_ctx: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> dict[str, list[dict[str, Any]]]:
        limit = max(1, min(int(limit), 200))
        self.calls.append((limit, offset))
        rows = self._goals.get(tenant_ctx.tenant_id, [])
        return {"goals": rows[offset : offset + limit]}


def _client(app: Any) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


async def _agent_store_with(n_own: int) -> AgentStore:
    store = AgentStore()
    for i in range(n_own):
        await store.create({"name": f"agent {i}"}, tenant_ctx=_CTX)
    await store.create({"name": "someone else's"}, tenant_ctx=_OTHER)
    return store


async def test_export_includes_every_goal_beyond_the_default_page() -> None:
    app = _make_app()
    svc = _PagedGoalService({_CTX.tenant_id: 437, _OTHER.tenant_id: 3})
    app.state.goal_service = svc
    app.state.agent_store = await _agent_store_with(0)

    resp = _client(app).post("/tenants/me/export", headers=H)

    assert resp.status_code == 200
    body = resp.json()
    ids = [g["id"] for g in body["goals"]]
    assert len(ids) == 437
    assert len(set(ids)) == 437
    assert all(i.startswith(f"{_CTX.tenant_id}-") for i in ids)
    assert body["counts"]["goals"] == 437
    assert len(svc.calls) >= 3  # paged, not one capped call


async def test_export_includes_agents_from_the_real_agent_store() -> None:
    app = _make_app()
    app.state.goal_service = _PagedGoalService({})
    app.state.agent_store = await _agent_store_with(3)

    resp = _client(app).post("/tenants/me/export", headers=H)

    assert resp.status_code == 200
    body = resp.json()
    assert sorted(a["name"] for a in body["agents"]) == ["agent 0", "agent 1", "agent 2"]
    assert body["counts"]["agents"] == 3


async def test_export_goal_read_failure_is_503_not_an_empty_export() -> None:
    class _Down:
        async def list_goals(self, tenant_ctx: TenantContext, **_: Any) -> Any:
            raise RuntimeError("db down")

    app = _make_app()
    app.state.goal_service = _Down()
    app.state.agent_store = await _agent_store_with(1)

    resp = _client(app).post("/tenants/me/export", headers=H)

    assert resp.status_code == 503
    assert "goals" in resp.json()["detail"]


async def test_export_agent_read_failure_is_503_not_an_empty_export() -> None:
    class _Down:
        async def list_async(self, *, tenant_ctx: TenantContext, **_: Any) -> Any:
            raise RuntimeError("db down")

    app = _make_app()
    app.state.goal_service = _PagedGoalService({})
    app.state.agent_store = _Down()

    resp = _client(app).post("/tenants/me/export", headers=H)

    assert resp.status_code == 503
    assert "agents" in resp.json()["detail"]


def test_export_without_goal_service_is_503_not_an_empty_export() -> None:
    app = _make_app()
    app.state.agent_store = AgentStore()

    resp = _client(app).post("/tenants/me/export", headers=H)

    assert resp.status_code == 503
