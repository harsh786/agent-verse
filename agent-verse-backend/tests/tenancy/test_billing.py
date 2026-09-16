"""Tests for app/tenancy/billing.py — QA3 billing & payment integration."""
from __future__ import annotations

import pytest

from app.tenancy.billing import (
    OVERAGE_RATES,
    PLAN_CATALOG,
    BillingCycle,
    BillingPlan,
    BillingService,
    PaymentProvider,
    PlanLimits,
    TenantBilling,
    billing_service,
)


class TestPlanCatalog:
    def test_all_plans_present(self):
        for plan in BillingPlan:
            assert plan in PLAN_CATALOG

    def test_free_plan_limits(self):
        limits = PLAN_CATALOG[BillingPlan.FREE]
        assert limits.max_orgs == 1
        assert limits.max_agents_per_org == 5
        assert limits.max_missions_per_day == 2
        assert limits.channels_allowed == ["rest"]
        assert limits.mcp_enabled is False
        assert limits.sso_enabled is False
        assert limits.price_usd_monthly == 0.0

    def test_enterprise_plan_unlimited(self):
        limits = PLAN_CATALOG[BillingPlan.ENTERPRISE]
        assert limits.max_orgs == 999_999
        assert limits.sso_enabled is True
        assert limits.mcp_enabled is True

    def test_pro_plan_has_mcp(self):
        limits = PLAN_CATALOG[BillingPlan.PRO]
        assert limits.mcp_enabled is True
        assert "mcp" in limits.channels_allowed

    def test_overage_rates_present(self):
        assert OVERAGE_RATES["extra_agent_hour_usd"] == 0.05
        assert OVERAGE_RATES["extra_mission_usd"] == 0.10
        assert OVERAGE_RATES["extra_1k_tokens_usd"] == 0.002


class TestTenantBilling:
    def test_default_plan_is_free(self):
        billing = TenantBilling(tenant_id="t1")
        assert billing.plan == BillingPlan.FREE
        assert billing.billing_cycle == BillingCycle.MONTHLY
        assert billing.payment_provider == PaymentProvider.STRIPE

    def test_plan_limits_property(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.PRO)
        assert billing.plan_limits.max_agents_per_org == 100

    def test_plan_limits_falls_back_to_free_for_unknown_plan(self):
        billing = TenantBilling(tenant_id="t1")
        billing.plan = "not_a_real_plan"  # type: ignore[assignment]
        assert billing.plan_limits == PLAN_CATALOG[BillingPlan.FREE]

    def test_compute_overage_zero_when_under_limit(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.FREE)
        billing.usage_this_period = {"missions_used": 1, "tokens_1k": 0}
        assert billing.compute_overage() == 0.0

    def test_compute_overage_missions_above_limit(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.FREE)
        # FREE: max_missions_per_day=2 -> monthly approx = 60
        billing.usage_this_period = {"missions_used": 70, "tokens_1k": 0}
        overage = billing.compute_overage()
        assert overage == pytest.approx((70 - 60) * OVERAGE_RATES["extra_mission_usd"])

    def test_compute_overage_tokens_always_charged(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.PRO)
        billing.usage_this_period = {"tokens_1k": 100}
        overage = billing.compute_overage()
        assert overage == pytest.approx(100 * OVERAGE_RATES["extra_1k_tokens_usd"])

    def test_compute_overage_combines_missions_and_tokens(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.FREE)
        billing.usage_this_period = {"missions_used": 70, "tokens_1k": 10}
        overage = billing.compute_overage()
        expected = (70 - 60) * OVERAGE_RATES["extra_mission_usd"] + 10 * OVERAGE_RATES[
            "extra_1k_tokens_usd"
        ]
        assert overage == pytest.approx(expected)

    def test_compute_overage_rounds_to_4_places(self):
        billing = TenantBilling(tenant_id="t1", plan=BillingPlan.FREE)
        billing.usage_this_period = {"tokens_1k": 1}
        overage = billing.compute_overage()
        assert overage == round(overage, 4)


@pytest.fixture
def service() -> BillingService:
    return BillingService()


class TestBillingServiceGetOrCreate:
    @pytest.mark.asyncio
    async def test_creates_new_billing_record(self, service: BillingService):
        billing = service.get_or_create("tenant-1")
        assert billing.tenant_id == "tenant-1"
        assert billing.plan == BillingPlan.FREE

    def test_returns_same_instance_on_repeated_calls(self, service: BillingService):
        first = service.get_or_create("tenant-1")
        second = service.get_or_create("tenant-1")
        assert first is second


class TestUpgradePlan:
    @pytest.mark.asyncio
    async def test_upgrade_updates_plan_fields(self, service: BillingService):
        billing = await service.upgrade_plan(
            "tenant-1",
            BillingPlan.PRO,
            provider=PaymentProvider.RAZORPAY,
            currency="INR",
            cycle=BillingCycle.ANNUAL,
        )
        assert billing.plan == BillingPlan.PRO
        assert billing.payment_provider == PaymentProvider.RAZORPAY
        assert billing.currency == "INR"
        assert billing.billing_cycle == BillingCycle.ANNUAL
        assert billing.next_billing_date is not None

    @pytest.mark.asyncio
    async def test_upgrade_without_payment_token_skips_provider_call(
        self, service: BillingService
    ):
        billing = await service.upgrade_plan("tenant-1", BillingPlan.STARTER)
        assert billing.customer_id is None
        assert billing.subscription_id is None

    @pytest.mark.asyncio
    async def test_upgrade_with_stripe_token_but_no_configured_key_noop(
        self, service: BillingService
    ):
        # BillingService created without stripe_key -> provider branch not entered
        billing = await service.upgrade_plan(
            "tenant-1",
            BillingPlan.PRO,
            provider=PaymentProvider.STRIPE,
            payment_token="tok_abc",
        )
        assert billing.customer_id is None

    @pytest.mark.asyncio
    async def test_upgrade_with_stripe_key_and_token_calls_stub(self, monkeypatch):
        import sys
        import types

        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        fake_stripe = types.ModuleType("stripe")
        fake_stripe.api_key = None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "stripe", fake_stripe)

        svc = BillingService(stripe_key="sk_test_123")
        billing = await svc.upgrade_plan(
            "tenant-1",
            BillingPlan.PRO,
            provider=PaymentProvider.STRIPE,
            payment_token="tok_abc",
        )
        assert billing.customer_id == "cus_stub_tenant-1"
        assert billing.subscription_id == "sub_stub_tenant-1"

    @pytest.mark.asyncio
    async def test_upgrade_stripe_package_not_installed_skips_gracefully(self, monkeypatch):
        # In this environment the real `stripe` package is not installed, so the
        # ImportError branch in _create_stripe_subscription is exercised here.
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        svc = BillingService(stripe_key="sk_test_123")
        billing = await svc.upgrade_plan(
            "tenant-1",
            BillingPlan.PRO,
            provider=PaymentProvider.STRIPE,
            payment_token="tok_abc",
        )
        assert billing.customer_id is None
        assert billing.subscription_id is None

    @pytest.mark.asyncio
    async def test_upgrade_stripe_without_env_key_warns_and_skips(
        self, monkeypatch
    ):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        svc = BillingService(stripe_key="sk_test_123")
        billing = await svc.upgrade_plan(
            "tenant-1",
            BillingPlan.PRO,
            provider=PaymentProvider.STRIPE,
            payment_token="tok_abc",
        )
        assert billing.customer_id is None

    @pytest.mark.asyncio
    async def test_upgrade_with_razorpay_key_and_token_calls_stub(self):
        svc = BillingService(razorpay_key="rzp_test_123")
        billing = await svc.upgrade_plan(
            "tenant-1",
            BillingPlan.STARTER,
            provider=PaymentProvider.RAZORPAY,
            payment_token="tok_abc",
        )
        # razorpay stub only logs, does not set customer_id
        assert billing.payment_provider == PaymentProvider.RAZORPAY


class TestRecordUsage:
    @pytest.mark.asyncio
    async def test_records_missions_delta(self, service: BillingService):
        await service.record_usage("tenant-1", missions_delta=5)
        billing = service.get_or_create("tenant-1")
        assert billing.usage_this_period["missions_used"] == 5

    @pytest.mark.asyncio
    async def test_accumulates_missions_delta(self, service: BillingService):
        await service.record_usage("tenant-1", missions_delta=5)
        await service.record_usage("tenant-1", missions_delta=3)
        billing = service.get_or_create("tenant-1")
        assert billing.usage_this_period["missions_used"] == 8

    @pytest.mark.asyncio
    async def test_records_agent_hours_delta(self, service: BillingService):
        await service.record_usage("tenant-1", agent_hours_delta=2.5)
        billing = service.get_or_create("tenant-1")
        assert billing.usage_this_period["agent_hours"] == 2.5

    @pytest.mark.asyncio
    async def test_records_tokens_delta(self, service: BillingService):
        await service.record_usage("tenant-1", tokens_1k_delta=10.0)
        billing = service.get_or_create("tenant-1")
        assert billing.usage_this_period["tokens_1k"] == 10.0

    @pytest.mark.asyncio
    async def test_zero_deltas_do_not_create_usage_keys(self, service: BillingService):
        await service.record_usage("tenant-1")
        billing = service.get_or_create("tenant-1")
        assert billing.usage_this_period == {}


class TestInvoices:
    @pytest.mark.asyncio
    async def test_get_invoice_history_empty_for_new_tenant(self, service: BillingService):
        history = await service.get_invoice_history("tenant-1")
        assert history == []

    @pytest.mark.asyncio
    async def test_create_invoice_returns_expected_fields(self, service: BillingService):
        invoice = await service.create_invoice("tenant-1")
        assert invoice["tenant_id"] == "tenant-1"
        assert invoice["plan"] == BillingPlan.FREE.value
        assert invoice["status"] == "draft"
        assert invoice["total"] == invoice["base_charge"] + invoice["overage_charge"]
        assert "invoice_id" in invoice

    @pytest.mark.asyncio
    async def test_create_invoice_appends_to_history(self, service: BillingService):
        await service.create_invoice("tenant-1")
        await service.create_invoice("tenant-1")
        history = await service.get_invoice_history("tenant-1")
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_create_invoice_includes_overage(self, service: BillingService):
        await service.upgrade_plan("tenant-1", BillingPlan.FREE)
        await service.record_usage("tenant-1", missions_delta=1000)
        invoice = await service.create_invoice("tenant-1")
        assert invoice["overage_charge"] > 0


class TestCustomerPortalUrl:
    @pytest.mark.asyncio
    async def test_stripe_with_customer_id(self, service: BillingService):
        billing = service.get_or_create("tenant-1")
        billing.payment_provider = PaymentProvider.STRIPE
        billing.customer_id = "cus_123"
        url = await service.get_customer_portal_url("tenant-1")
        assert url == "https://billing.stripe.com/p/session/cus_123"

    @pytest.mark.asyncio
    async def test_stripe_without_customer_id_falls_back(self, service: BillingService):
        billing = service.get_or_create("tenant-1")
        billing.payment_provider = PaymentProvider.STRIPE
        billing.customer_id = None
        url = await service.get_customer_portal_url("tenant-1")
        assert url == "/settings/billing"

    @pytest.mark.asyncio
    async def test_razorpay_returns_dashboard(self, service: BillingService):
        billing = service.get_or_create("tenant-1")
        billing.payment_provider = PaymentProvider.RAZORPAY
        url = await service.get_customer_portal_url("tenant-1")
        assert url == "https://dashboard.razorpay.com"

    @pytest.mark.asyncio
    async def test_paddle_falls_back_to_settings(self, service: BillingService):
        billing = service.get_or_create("tenant-1")
        billing.payment_provider = PaymentProvider.PADDLE
        url = await service.get_customer_portal_url("tenant-1")
        assert url == "/settings/billing"


class TestCheckPlanLimit:
    def test_within_orgs_limit(self, service: BillingService):
        assert service.check_plan_limit("tenant-1", "orgs", 0) is True

    def test_at_orgs_limit_fails(self, service: BillingService):
        # FREE plan max_orgs = 1
        assert service.check_plan_limit("tenant-1", "orgs", 1) is False

    def test_within_agents_limit(self, service: BillingService):
        assert service.check_plan_limit("tenant-1", "agents", 4) is True

    def test_missions_per_day_limit(self, service: BillingService):
        assert service.check_plan_limit("tenant-1", "missions_per_day", 2) is False
        assert service.check_plan_limit("tenant-1", "missions_per_day", 1) is True

    def test_unknown_resource_defaults_to_generous_limit(self, service: BillingService):
        assert service.check_plan_limit("tenant-1", "unknown_resource", 999_998) is True


class TestGlobalInstance:
    def test_global_billing_service_exists(self):
        assert isinstance(billing_service, BillingService)


class TestPlanLimitsDataclass:
    def test_plan_limits_is_dataclass_with_expected_fields(self):
        limits = PlanLimits(
            max_orgs=1,
            max_agents_per_org=1,
            max_missions_per_day=1,
            channels_allowed=["rest"],
            mcp_enabled=False,
            sso_enabled=False,
            support_tier="community",
            price_usd_monthly=0.0,
            price_usd_annual=0.0,
        )
        assert limits.max_orgs == 1
        assert limits.channels_allowed == ["rest"]
