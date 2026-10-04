"""Emergency stop / clear are admin-only and report per-goal/approval failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from tests.governance._router_app import make_app, tenant


@dataclass
class _Rec:
    tenant_id: str
    status: str = "running"


class _GoalSvc:
    def __init__(self) -> None:
        self._goals = {"g-ok": _Rec("t-gov"), "g-bad": _Rec("t-gov"), "g-other": _Rec("t-x")}
        self.cancelled: list[str] = []

    async def active_goal_ids(self, tenant_ctx: Any, *, limit: int | None = None) -> list[str]:
        # DB-backed fleet-wide listing (stubbed): the tenant's non-terminal goals.
        return [g for g, r in self._goals.items() if r.tenant_id == tenant_ctx.tenant_id]

    async def cancel_goal(self, *, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        if goal_id == "g-bad":
            raise RuntimeError("cancel exploded")
        self.cancelled.append(goal_id)
        return {}


@dataclass
class _Req:
    request_id: str


class _Hitl:
    async def alist_pending(self, *, tenant_ctx: Any) -> list[_Req]:
        return [_Req("r-ok"), _Req("r-bad"), _Req("r-lost")]

    async def reject(self, request_id: str, **_: Any) -> bool:
        if request_id == "r-bad":
            raise RuntimeError("db down")
        return request_id != "r-lost"


async def test_emergency_stop_rejects_non_admin() -> None:
    app = make_app(router, ctx=tenant(roles=("operator",)))
    app.state.goal_service = _GoalSvc()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/governance/emergency-stop")
    assert r.status_code == 403
    assert app.state.goal_service.cancelled == []


async def test_clear_emergency_stop_rejects_non_admin() -> None:
    app = make_app(router, ctx=tenant(roles=("operator",)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete("/governance/emergency-stop")
    assert r.status_code == 403


async def test_emergency_stop_reports_cancel_and_reject_failures() -> None:
    app = make_app(router)
    app.state._redis = fakeredis.aioredis.FakeRedis()
    app.state.goal_service = _GoalSvc()
    app.state.hitl_gateway = _Hitl()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/governance/emergency-stop")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial"] is True
    assert body["status"] == "emergency_stop_partial"
    assert body["cancelled_goal_ids"] == ["g-ok"]
    assert body["failed_goals"] == [{"goal_id": "g-bad", "error": "RuntimeError"}]
    assert body["rejected_approvals"] == 1
    assert {f["request_id"] for f in body["failed_approvals"]} == {"r-bad", "r-lost"}


async def test_clear_emergency_stop_503_when_flag_delete_fails() -> None:
    class _Redis:
        async def delete(self, key: str) -> int:
            raise ConnectionError("redis down")

    app = make_app(router)
    app.state._policy_pubsub_redis = _Redis()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete("/governance/emergency-stop")
    assert r.status_code == 503
