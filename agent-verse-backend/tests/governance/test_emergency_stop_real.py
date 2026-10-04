"""Regression: the tenant emergency stop reported success it did not deliver.

* It cancelled only the goals held in THIS replica's memory.
* It published to an ``emergency_stop`` channel nobody subscribes to.
* The ``emergency_stop:{tenant}`` flag expired after 300 s.
* Without Redis it still answered "All running goals cancelled".
* Nothing checked the flag at goal submission or between steps, so a goal
  already running (or started on another replica) kept going.
* The org stop/resume endpoints had no org-admin check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.core.errors import ConflictError, ServiceUnavailableError
from app.governance.emergency_stop import (
    TENANT_STOP_REASON,
    UNVERIFIABLE_REASON,
    activate_stop,
    tenant_stop_key,
)
from app.governance.hitl import HITLGateway
from app.reliability.goal_lifecycle import GoalCancelledError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TID = "t-estop"
ADMIN = TenantContext(tenant_id=TID, plan=PlanTier.ENTERPRISE, api_key_id="k-adm", roles=("admin",))
_KEYS = {"k-admin": ADMIN}
H = {"X-API-Key": "k-admin"}


class _GoalServiceStub:
    """Goals running on OTHER replicas: known only via the DB-backed listing."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.cancelled: list[str] = []
        self._goals: dict[str, Any] = {}  # this replica runs nothing

    async def active_goal_ids(
        self, tenant_ctx: TenantContext, *, limit: int | None = None
    ) -> list[str]:
        return list(self.ids)

    async def cancel_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        self.cancelled.append(goal_id)
        return {"goal_id": goal_id, "status": "cancelled"}


def _app(redis: Any = None, goal_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.hitl_gateway = HITLGateway()
    if redis is not None:
        app.state._redis = redis
        app.state._policy_pubsub_redis = redis
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def test_stop_without_redis_is_503_not_fake_success() -> None:
    resp = TestClient(_app(goal_service=_GoalServiceStub(["g1"]))).post(
        "/governance/emergency-stop", headers=H
    )
    assert resp.status_code == 503
    assert "cancelled" not in resp.text.lower() or "not" in resp.text.lower()


def test_stop_flag_has_no_ttl_and_is_reported_by_get() -> None:
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    client = TestClient(_app(redis=redis, goal_service=_GoalServiceStub([])))

    resp = client.post("/governance/emergency-stop", headers=H)
    assert resp.status_code == 200, resp.text

    sync_view = fakeredis.FakeRedis(server=server)
    assert sync_view.get(tenant_stop_key(TID))
    assert sync_view.ttl(tenant_stop_key(TID)) == -1  # persists until lifted

    state = client.get("/governance/emergency-stop", headers=H)
    assert state.status_code == 200
    assert state.json()["active"] is True
    assert state.json()["activated_by"] == "k-adm"

    cleared = client.delete("/governance/emergency-stop", headers=H)
    assert cleared.status_code == 200
    assert client.get("/governance/emergency-stop", headers=H).json()["active"] is False


def test_stop_cancels_goals_running_on_other_replicas() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    svc = _GoalServiceStub(["g-remote-1", "g-remote-2"])
    resp = TestClient(_app(redis=redis, goal_service=svc)).post(
        "/governance/emergency-stop", headers=H
    )
    assert resp.status_code == 200, resp.text
    assert sorted(svc.cancelled) == ["g-remote-1", "g-remote-2"]
    assert resp.json()["cancelled_goals"] == 2


def test_stop_flag_write_failure_is_503() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    redis.set = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    svc = _GoalServiceStub(["g1"])
    resp = TestClient(_app(redis=redis, goal_service=svc)).post(
        "/governance/emergency-stop", headers=H
    )
    assert resp.status_code == 503


def test_clear_without_redis_is_503() -> None:
    resp = TestClient(_app()).delete("/governance/emergency-stop", headers=H)
    assert resp.status_code == 503


def test_get_state_without_redis_is_503() -> None:
    resp = TestClient(_app()).get("/governance/emergency-stop", headers=H)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Enforcement: submission and step boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_refused_while_stop_active() -> None:
    from app.services.goal_service import GoalService

    svc = GoalService()
    svc._redis = fakeredis.aioredis.FakeRedis()
    await activate_stop(svc._redis, tenant_stop_key(TID), activated_by="k")

    with pytest.raises(ConflictError) as exc:
        await svc.submit_goal(goal="do x", priority="normal", dry_run=False, tenant_ctx=ADMIN)
    assert exc.value.code == "EMERGENCY_STOP_ACTIVE"


@pytest.mark.asyncio
async def test_submit_fails_closed_when_stop_state_unreadable() -> None:
    from app.services.goal_service import GoalService

    svc = GoalService()
    svc._redis = fakeredis.aioredis.FakeRedis()
    svc._redis.get = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]

    with pytest.raises(ServiceUnavailableError):
        await svc.submit_goal(goal="do x", priority="normal", dry_run=False, tenant_ctx=ADMIN)


@pytest.mark.asyncio
async def test_api_pause_gate_stops_running_goal_at_next_step() -> None:
    from app.services.goal_service import GoalService

    svc = GoalService()
    svc._redis = fakeredis.aioredis.FakeRedis()
    gate = svc._make_pause_gate("g-run", ADMIN)
    await gate()  # not stopped: passes

    await activate_stop(svc._redis, tenant_stop_key(TID), activated_by="k")
    with pytest.raises(GoalCancelledError, match=TENANT_STOP_REASON):
        await gate()


@pytest.mark.asyncio
async def test_worker_pause_gate_stops_running_goal_at_next_step() -> None:
    from app.scaling.tasks import _make_worker_pause_gate

    sync_r = fakeredis.FakeRedis()
    gate = _make_worker_pause_gate("g-w", sync_r, None, tenant_id=TID)
    await gate()

    sync_r.set(tenant_stop_key(TID), "1")
    with pytest.raises(GoalCancelledError, match=TENANT_STOP_REASON):
        await gate()


@pytest.mark.asyncio
async def test_worker_pause_gate_fails_closed_on_redis_error() -> None:
    from unittest.mock import MagicMock

    from app.scaling.tasks import _make_worker_pause_gate

    sync_r = MagicMock()
    sync_r.get = MagicMock(side_effect=ConnectionError("down"))
    gate = _make_worker_pause_gate("g-w", sync_r, None, tenant_id=TID)
    with pytest.raises(GoalCancelledError, match=UNVERIFIABLE_REASON):
        await gate()


# ---------------------------------------------------------------------------
# Org stop needs an org admin
# ---------------------------------------------------------------------------


def _org_app(ctx: TenantContext, server: Any = None) -> FastAPI:
    from app.org.router import get_org_service
    from app.org.router import router as org_router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(org_router)
    app.dependency_overrides[get_org_service] = lambda: AsyncMock()
    app.state._redis = fakeredis.aioredis.FakeRedis(server=server or fakeredis.FakeServer())
    return app


@pytest.mark.parametrize("path", ["emergency-stop", "emergency-stop/resume"])
def test_org_stop_and_resume_require_org_admin(path: str) -> None:
    viewer = TenantContext(tenant_id=TID, plan=PlanTier.ENTERPRISE, api_key_id="v", roles=("viewer",))
    app = _org_app(viewer)
    prefix = "/v1/org"
    resp = TestClient(app).post(f"{prefix}/org-1/{path}", headers={"X-API-Key": "k"})
    assert resp.status_code == 403


def test_org_stop_allowed_for_org_admin_and_has_no_ttl() -> None:
    server = fakeredis.FakeServer()
    app = _org_app(ADMIN, server)
    prefix = "/v1/org"
    client = TestClient(app)
    resp = client.post(f"{prefix}/org-1/emergency-stop", headers={"X-API-Key": "k"})
    assert resp.status_code == 200, resp.text
    sync_view = fakeredis.FakeRedis(server=server)
    assert sync_view.ttl(f"emergency_stop:{TID}:org-1") == -1
