"""TRG-12: the trigger dispatcher's role check is fed the caller's real role.

``caller_role`` defaulted to "operator" and no caller passed it, so the RBAC step
never blocked anything: a viewer could manually fire triggers. Manual fires now
map the caller's roles onto the trigger permission matrix and are refused with
403 before anything is dispatched; automated fires use the explicit "system"
role.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.triggers import fire_trigger_now
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.rbac import TriggerPermissionDenied, check_permission, trigger_role


def _ctx(*roles: str) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=roles)


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (("admin",), "admin"),
        (("operator",), "operator"),
        (("viewer", "operator"), "operator"),
        (("viewer",), "viewer"),
        (("approver",), "viewer"),
        (("some-custom-role",), "viewer"),
        ((), "api_key"),  # legacy role-less key (writes gated by the middleware)
    ],
)
def test_trigger_role_mapping(roles: tuple[str, ...], expected: str) -> None:
    assert trigger_role(_ctx(*roles)) == expected


def test_system_role_may_fire_but_do_nothing_else() -> None:
    assert check_permission("system", "fire") is True
    with pytest.raises(TriggerPermissionDenied):
        check_permission("system", "delete")


def _request(ctx: TenantContext, dispatcher: Any) -> Any:
    class _Store:
        def get(self, sid: str, tenant_ctx: Any = None) -> dict[str, Any]:
            spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK)
            return {"schedule_id": sid, "spec": spec, "paused": False}

    state = SimpleNamespace(trigger_dispatcher=dispatcher, schedule_store=_Store())
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(tenant=ctx))


async def test_viewer_cannot_fire_a_trigger() -> None:
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await fire_trigger_now("tr1", _request(_ctx("viewer"), dispatcher),
                               SimpleNamespace(payload={"x": 1}))
    assert exc.value.status_code == 403
    dispatcher.dispatch.assert_not_awaited()


async def test_operator_fires_with_its_real_role() -> None:
    dispatcher = SimpleNamespace(dispatch=AsyncMock(return_value=SimpleNamespace(
        goal_id="g", goal_created=True, skip_reason=None, fired_at=None)))
    resp = await fire_trigger_now("tr1", _request(_ctx("operator"), dispatcher),
                                  SimpleNamespace(payload={"x": 1}))
    assert resp["goal_id"] == "g"
    assert dispatcher.dispatch.await_args.kwargs["caller_role"] == "operator"


async def test_viewer_cannot_fire_a_schedule() -> None:
    from app.api.schedules import fire_schedule_now

    class _Store:
        async def get_async(self, sid: str, tenant_ctx: Any = None, strict: bool = False) -> Any:
            return {"schedule_id": sid, "paused": False,
                    "spec": TriggerSpec(trigger_type=TriggerType.WEBHOOK)}

    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    state = SimpleNamespace(trigger_dispatcher=dispatcher, schedule_store=_Store())
    request = SimpleNamespace(app=SimpleNamespace(state=state),
                              state=SimpleNamespace(tenant=_ctx("viewer")))
    with pytest.raises(HTTPException) as exc:
        await fire_schedule_now(request, "s1")  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    dispatcher.dispatch.assert_not_awaited()


async def test_dispatcher_denies_a_viewer_role_and_defaults_to_system() -> None:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, goal_template="x")
    spec.trigger_id = "tr1"  # type: ignore[attr-defined]
    denied = await d.dispatch(spec, {"a": 1}, _ctx(), caller_role="viewer")
    assert denied.skip_reason == "RBAC_DENIED"
    automated = await d.dispatch(spec, {"a": 2}, _ctx())
    assert automated.skip_reason is None and automated.goal_created is True
