"""Regression: GET /schedules/analytics was shadowed by GET /schedules/{schedule_id}.

FastAPI matches routes in declaration order, and ``/{schedule_id}`` was declared
first, so ``/schedules/analytics`` was handled by ``get_schedule`` with
``schedule_id="analytics"`` → 404 "Schedule analytics not found".
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.schedules import router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

_CTX = TenantContext(tenant_id="tid-analytics", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _client(store: ScheduleStore) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = _CTX
        return await call_next(request)

    app.include_router(router)
    app.state.schedule_store = store
    return TestClient(app)


def test_schedules_analytics_is_reachable() -> None:
    store = ScheduleStore()
    store.create(
        goal_id="g1",
        spec=TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *"),
        tenant_ctx=_CTX,
    )
    r = _client(store).get("/schedules/analytics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["active"] == 1
    assert body["by_trigger_type"] == {"cron": 1}
    assert "fired_last_7_days" in body


def test_static_routes_precede_parameterized_ones() -> None:
    """Every static GET under /schedules must be declared before /{schedule_id}."""
    paths = [
        (getattr(r, "path", ""), sorted(getattr(r, "methods", set()) or []))
        for r in router.routes
    ]
    get_paths = [p for p, methods in paths if "GET" in methods]
    param_idx = get_paths.index("/schedules/{schedule_id}")
    static = [p for p in get_paths if p.count("/") == 2 and "{" not in p]
    assert static, "expected at least /schedules/analytics"
    for p in static:
        assert get_paths.index(p) < param_idx, f"{p} is shadowed by /schedules/{{schedule_id}}"
