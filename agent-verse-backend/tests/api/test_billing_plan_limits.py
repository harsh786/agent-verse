"""QA-17: the limits the billing API shows are the limits the platform enforces.

``/billing/plans`` and the paid-order response used a hard-coded PLAN_FEATURES
table (starter 50 goals/day + 5 agents, professional 500, enterprise
"unlimited") while enforcement reads ``PLAN_LIMITS`` (starter 100/10,
professional 1000/50, enterprise 50000/1000), listed limits nothing enforces
(tokens/month, tool calls/day, connectors) and had no free plan at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.billing import _Order, _upgrade_result, plan_limits
from app.api.billing import router as billing_router
from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext
from app.triggers.quota import PLAN_MAX_TRIGGERS


def _client(plan: PlanTier = PlanTier.STARTER) -> TestClient:
    app = FastAPI()
    app.include_router(billing_router)

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id="t-qa17", plan=plan, api_key_id="k")
        return await call_next(request)

    return TestClient(app)


def _expected(tier: PlanTier) -> dict[str, int]:
    lim = PLAN_LIMITS[tier]
    return {
        "goals_per_day": lim.goals_per_day,
        "agents": lim.max_agents,
        "knowledge_collections": lim.max_knowledge_collections,
        "triggers": PLAN_MAX_TRIGGERS[tier.value],
        "api_keys": lim.max_api_keys,
        "requests_per_minute": lim.requests_per_minute,
        "goal_timeout_seconds": lim.goal_timeout_seconds,
    }


@pytest.mark.parametrize("tier", list(PlanTier))
def test_plan_limits_are_derived_from_the_enforced_tables(tier: PlanTier) -> None:
    assert plan_limits(tier) == _expected(tier)
    assert plan_limits(tier.value) == _expected(tier)


def test_unknown_plan_has_no_limits() -> None:
    assert plan_limits("platinum") == {}


def test_list_plans_shows_every_tier_with_enforced_limits() -> None:
    resp = _client().get("/billing/plans")
    assert resp.status_code == 200, resp.text
    plans = {p["plan_id"]: p for p in resp.json()}
    assert list(plans) == ["free", "starter", "professional", "enterprise"]
    for tier in PlanTier:
        assert plans[tier.value]["limits"] == _expected(tier)
    # The QA-17 example: what enforcement actually allows a starter tenant.
    assert plans["starter"]["limits"]["goals_per_day"] == 100
    assert plans["starter"]["limits"]["agents"] == 10
    for unenforced in ("tokens_per_month", "tool_calls_per_day", "connectors"):
        assert unenforced not in plans["professional"]["limits"]


def test_free_plan_is_listed_at_zero_price_and_paid_prices_are_unchanged() -> None:
    plans = {p["plan_id"]: p for p in _client().get("/billing/plans").json()}
    assert plans["free"]["prices"] == {
        "monthly_inr": 0.0,
        "annual_inr": 0.0,
        "monthly_paise": 0,
        "annual_paise": 0,
    }
    assert plans["starter"]["prices"]["monthly_inr"] == 29.0
    assert plans["enterprise"]["prices"]["annual_paise"] == 479040


@pytest.mark.parametrize("tier", [PlanTier.FREE, PlanTier.PROFESSIONAL])
def test_subscription_reports_the_tenants_enforced_limits(tier: PlanTier) -> None:
    body = _client(tier).get("/billing/subscription").json()
    assert body["plan"] == tier.value
    assert body["limits"] == _expected(tier)


def test_paid_order_result_reports_the_enforced_limits() -> None:
    order = _Order(
        order_id="o1",
        tenant_id="t",
        plan="professional",
        cycle="monthly",
        amount=9900,
        currency="INR",
        is_mock=True,
    )
    assert _upgrade_result(order, "pay_1")["limits"] == _expected(PlanTier.PROFESSIONAL)
