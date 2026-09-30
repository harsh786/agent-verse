"""api_poll default interval is plan-aware (owner decision, TRG-16 follow-up).

The spec default (300s) sits below the free plan's 15-min floor, so a free
tenant who simply omitted ``poll_interval_seconds`` got a 422. An omitted
interval now resolves to ``max(spec default, plan floor)``; an explicit interval
below the floor is still refused with the reason.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router
from app.triggers.models import (
    API_POLL_DEFAULT_INTERVAL_SECONDS,
    PLAN_MIN_SCHEDULE_INTERVAL_SECONDS,
    TriggerSpec,
    TriggerType,
    apply_plan_interval_defaults,
)
from app.triggers.store import ScheduleStore
from app.triggers.validation import creatable_error, validate_spec

_FREE_FLOOR = PLAN_MIN_SCHEDULE_INTERVAL_SECONDS["free"]


def _poll_spec(**kw: Any) -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.API_POLL, poll_url="https://x.test/s", **kw)


# ── unit ──────────────────────────────────────────────────────────────────────


def test_default_is_below_the_free_floor() -> None:
    # The premise of the fix: the raw spec default would be refused on free.
    assert API_POLL_DEFAULT_INTERVAL_SECONDS < _FREE_FLOOR
    with pytest.raises(ValueError, match="free plan allows"):
        validate_spec(_poll_spec(), plan="free")


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ("free", _FREE_FLOOR),
        ("starter", max(API_POLL_DEFAULT_INTERVAL_SECONDS, 300)),
        ("enterprise", API_POLL_DEFAULT_INTERVAL_SECONDS),
        ("unknown-plan", _FREE_FLOOR),
    ],
)
def test_omitted_interval_resolves_to_the_plan_aware_default(plan: str, expected: int) -> None:
    spec = _poll_spec()
    apply_plan_interval_defaults(spec, plan, explicit_fields=())
    assert spec.poll_interval_seconds == expected
    validate_spec(spec, plan=plan)


def test_explicit_interval_is_never_rewritten() -> None:
    spec = _poll_spec(poll_interval_seconds=300)
    apply_plan_interval_defaults(spec, "free", explicit_fields={"poll_interval_seconds"})
    assert spec.poll_interval_seconds == 300
    reason = creatable_error(spec, plan="free")
    assert reason is not None and "free plan allows" in reason


def test_other_types_are_untouched() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    apply_plan_interval_defaults(spec, "free", explicit_fields=())
    assert spec.interval_seconds == 3600
    assert spec.poll_interval_seconds == API_POLL_DEFAULT_INTERVAL_SECONDS


# ── API ───────────────────────────────────────────────────────────────────────


def _client(plan: str) -> TestClient:
    application = FastAPI()
    application.include_router(router)
    application.state.schedule_store = ScheduleStore()
    application.state.trigger_dispatcher = None

    @application.middleware("http")
    async def inject_tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id="t1", plan=plan, api_key="k")
        return await call_next(request)

    return TestClient(application)


def test_free_tenant_omitting_the_interval_gets_201_with_the_effective_interval() -> None:
    resp = _client("free").post(
        "/triggers",
        json={"spec": {"trigger_type": "api_poll", "poll_url": "https://x.test/s"}, "goal_id": "g"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["spec"]["poll_interval_seconds"] == _FREE_FLOOR


def test_enterprise_tenant_keeps_the_spec_default() -> None:
    resp = _client("enterprise").post(
        "/triggers",
        json={"spec": {"trigger_type": "api_poll", "poll_url": "https://x.test/s"}, "goal_id": "g"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["spec"]["poll_interval_seconds"] == API_POLL_DEFAULT_INTERVAL_SECONDS


def test_explicit_interval_below_the_floor_is_still_422() -> None:
    resp = _client("free").post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "api_poll",
                "poll_url": "https://x.test/s",
                "poll_interval_seconds": 300,
            },
            "goal_id": "g",
        },
    )
    assert resp.status_code == 422
    assert "free plan allows" in resp.json()["detail"]


def test_patch_replacing_the_spec_without_an_interval_uses_the_plan_default() -> None:
    client = _client("free")
    created = client.post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "api_poll",
                "poll_url": "https://x.test/s",
                "poll_interval_seconds": 1800,
            },
            "goal_id": "g",
        },
    ).json()
    resp = client.patch(
        f"/triggers/{created['schedule_id']}",
        json={"spec": {"trigger_type": "api_poll", "poll_url": "https://x.test/other"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["spec"]["poll_interval_seconds"] == _FREE_FLOOR


# ── NL parse ──────────────────────────────────────────────────────────────────


class _Provider:
    def __init__(self, answer: dict[str, Any]) -> None:
        self._answer = answer

    async def complete(self, request: Any) -> Any:
        from app.providers.base import CompletionResponse

        return CompletionResponse(content=json.dumps(self._answer), model="fake")


@pytest.mark.asyncio
async def test_nl_parse_resolves_an_omitted_interval_for_the_tenant_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.triggers import nl_scheduler

    async def fake_complete_decision(provider: Any, req: Any, **_: Any) -> Any:
        return await provider.complete(req)

    monkeypatch.setattr(
        "app.providers.guarded_completion.complete_decision", fake_complete_decision
    )
    omitted = nl_scheduler.NLScheduler(
        _Provider({"trigger_type": "api_poll", "poll_url": "https://x.test/s"})  # type: ignore[arg-type]
    )
    ctx = SimpleNamespace(tenant_id="t1", plan="free")
    [spec] = await omitted.parse("watch x.test", tenant_ctx=ctx)
    assert spec.poll_interval_seconds == _FREE_FLOOR

    explicit = nl_scheduler.NLScheduler(
        _Provider(  # type: ignore[arg-type]
            {
                "trigger_type": "api_poll",
                "poll_url": "https://x.test/s",
                "poll_interval_seconds": 300,
            }
        )
    )
    [spec] = await explicit.parse("watch x.test every 5 minutes", tenant_ctx=ctx)
    assert spec.poll_interval_seconds == 300
    assert creatable_error(spec, plan="free") is not None
