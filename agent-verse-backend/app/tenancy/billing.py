"""QA3 — Billing & Payment Integration.

Supports pluggable payment providers per spec:
  stripe    — International (USD, EUR, GBP...)
  razorpay  — India (INR, UPI, cards, net banking)
  paddle    — Europe + global (tax handling)

Plan comparison:
  FREE:       1 org, 5 agents, 2 missions/day, no channels, no MCP
  STARTER:    3 orgs, 20 agents, 30 missions/day, Telegram + REST
  PRO:        10 orgs, 100 agents, unlimited missions, all channels, MCP
  ENTERPRISE: unlimited, SSO, on-premise option, SLA, dedicated support

Usage-based billing (additional to flat plan):
  Per token above plan limit
  Per mission above plan limit
  Per agent-hour above limit
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)


class BillingPlan(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class BillingCycle(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class PaymentProvider(StrEnum):
    STRIPE = "stripe"
    RAZORPAY = "razorpay"
    PADDLE = "paddle"


# ── Plan definitions ──────────────────────────────────────────────────────────


@dataclass
class PlanLimits:
    max_orgs: int
    max_agents_per_org: int
    max_missions_per_day: int
    channels_allowed: list[str]
    mcp_enabled: bool
    sso_enabled: bool
    support_tier: str
    price_usd_monthly: float
    price_usd_annual: float


PLAN_CATALOG: dict[str, PlanLimits] = {
    BillingPlan.FREE: PlanLimits(
        max_orgs=1,
        max_agents_per_org=5,
        max_missions_per_day=2,
        channels_allowed=["rest"],
        mcp_enabled=False,
        sso_enabled=False,
        support_tier="community",
        price_usd_monthly=0.0,
        price_usd_annual=0.0,
    ),
    BillingPlan.STARTER: PlanLimits(
        max_orgs=3,
        max_agents_per_org=20,
        max_missions_per_day=30,
        channels_allowed=["rest", "telegram"],
        mcp_enabled=False,
        sso_enabled=False,
        support_tier="email",
        price_usd_monthly=49.0,
        price_usd_annual=490.0,
    ),
    BillingPlan.PRO: PlanLimits(
        max_orgs=10,
        max_agents_per_org=100,
        max_missions_per_day=10_000,
        channels_allowed=[
            "rest",
            "telegram",
            "slack",
            "whatsapp",
            "discord",
            "email",
            "teams",
            "mcp",
        ],
        mcp_enabled=True,
        sso_enabled=False,
        support_tier="chat",
        price_usd_monthly=199.0,
        price_usd_annual=1990.0,
    ),
    BillingPlan.ENTERPRISE: PlanLimits(
        max_orgs=999_999,
        max_agents_per_org=999_999,
        max_missions_per_day=999_999,
        channels_allowed=["all"],
        mcp_enabled=True,
        sso_enabled=True,
        support_tier="dedicated",
        price_usd_monthly=0.0,
        price_usd_annual=0.0,
    ),
}

# Overage rates (per unit above plan)
OVERAGE_RATES: dict[str, float] = {
    "extra_agent_hour_usd": 0.05,
    "extra_mission_usd": 0.10,
    "extra_1k_tokens_usd": 0.002,
}


@dataclass
class TenantBilling:
    """Per spec QA3 — full billing record."""

    tenant_id: str
    plan: BillingPlan = BillingPlan.FREE
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    payment_provider: PaymentProvider = PaymentProvider.STRIPE
    customer_id: str | None = None  # provider customer ID
    subscription_id: str | None = None
    next_billing_date: datetime | None = None
    currency: str = "USD"
    # Usage-based tracking
    usage_this_period: dict[str, float] = field(default_factory=dict)
    # {missions_used: N, agent_hours: N, tokens_1k: N}

    @property
    def plan_limits(self) -> PlanLimits:
        return PLAN_CATALOG.get(self.plan, PLAN_CATALOG[BillingPlan.FREE])

    def compute_overage(self) -> float:
        """Calculate overage charges for current period."""
        limits = self.plan_limits
        total = 0.0
        missions_used = self.usage_this_period.get("missions_used", 0)
        daily_limit = limits.max_missions_per_day * 30  # monthly approximation
        if missions_used > daily_limit:
            total += (missions_used - daily_limit) * OVERAGE_RATES["extra_mission_usd"]
        tokens_1k = self.usage_this_period.get("tokens_1k", 0)
        total += tokens_1k * OVERAGE_RATES["extra_1k_tokens_usd"]
        return round(total, 4)


class BillingService:
    """
    QA3 — Billing and payment service.
    Manages subscriptions, usage tracking, and invoices.
    Pluggable provider backend (Stripe / Razorpay / Paddle).
    """

    def __init__(self, stripe_key: str | None = None, razorpay_key: str | None = None) -> None:
        self._stripe_key = stripe_key
        self._razorpay_key = razorpay_key
        self._billings: dict[str, TenantBilling] = {}
        self._invoices: dict[str, list[dict]] = {}  # tenant_id → invoices

    def get_or_create(self, tenant_id: str) -> TenantBilling:
        if tenant_id not in self._billings:
            self._billings[tenant_id] = TenantBilling(tenant_id=tenant_id)
        return self._billings[tenant_id]

    async def upgrade_plan(
        self,
        tenant_id: str,
        new_plan: BillingPlan,
        provider: PaymentProvider = PaymentProvider.STRIPE,
        payment_token: str | None = None,
        currency: str = "USD",
        cycle: BillingCycle = BillingCycle.MONTHLY,
    ) -> TenantBilling:
        """Upgrade tenant to a higher plan."""
        billing = self.get_or_create(tenant_id)
        billing.plan = new_plan
        billing.payment_provider = provider
        billing.currency = currency
        billing.billing_cycle = cycle

        # TODO: Integrate with Stripe/Razorpay/Paddle SDK when configured
        if payment_token:
            if provider == PaymentProvider.STRIPE and self._stripe_key:
                await self._create_stripe_subscription(billing, payment_token)
            elif provider == PaymentProvider.RAZORPAY and self._razorpay_key:
                await self._create_razorpay_subscription(billing, payment_token)

        billing.next_billing_date = datetime.now(UTC)
        _log.info("billing.plan_upgraded", tenant_id=tenant_id, plan=new_plan.value)
        return billing

    async def record_usage(
        self,
        tenant_id: str,
        missions_delta: int = 0,
        agent_hours_delta: float = 0.0,
        tokens_1k_delta: float = 0.0,
    ) -> None:
        billing = self.get_or_create(tenant_id)
        usage = billing.usage_this_period
        if missions_delta:
            usage["missions_used"] = usage.get("missions_used", 0) + missions_delta
        if agent_hours_delta:
            usage["agent_hours"] = usage.get("agent_hours", 0.0) + agent_hours_delta
        if tokens_1k_delta:
            usage["tokens_1k"] = usage.get("tokens_1k", 0.0) + tokens_1k_delta

    async def get_invoice_history(self, tenant_id: str) -> list[dict]:
        return self._invoices.get(tenant_id, [])

    async def create_invoice(self, tenant_id: str) -> dict:
        billing = self.get_or_create(tenant_id)
        limits = billing.plan_limits
        overage = billing.compute_overage()
        base = limits.price_usd_monthly
        invoice: dict[str, Any] = {
            "invoice_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "plan": billing.plan.value,
            "period_start": datetime.now(UTC).isoformat(),
            "base_charge": base,
            "overage_charge": overage,
            "total": base + overage,
            "currency": billing.currency,
            "status": "draft",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._invoices.setdefault(tenant_id, []).append(invoice)
        return invoice

    async def get_customer_portal_url(self, tenant_id: str) -> str:
        """Return payment provider portal URL."""
        billing = self.get_or_create(tenant_id)
        if billing.payment_provider == PaymentProvider.STRIPE and billing.customer_id:
            return f"https://billing.stripe.com/p/session/{billing.customer_id}"
        if billing.payment_provider == PaymentProvider.RAZORPAY:
            return "https://dashboard.razorpay.com"
        return "/settings/billing"

    def check_plan_limit(self, tenant_id: str, resource: str, current_value: int) -> bool:
        """Return True if within plan limits."""
        billing = self.get_or_create(tenant_id)
        limits = billing.plan_limits
        limit_map = {
            "orgs": limits.max_orgs,
            "agents": limits.max_agents_per_org,
            "missions_per_day": limits.max_missions_per_day,
        }
        limit = limit_map.get(resource, 999_999)
        return current_value < limit

    # ── Provider-specific (stubs — real impl needs SDK keys) ──────────────────

    async def _create_stripe_subscription(self, billing: TenantBilling, token: str) -> None:
        """Create Stripe subscription — requires STRIPE_SECRET_KEY."""
        import os

        if not os.getenv("STRIPE_SECRET_KEY"):
            _log.warning("billing.stripe_key_missing")
            return
        try:
            import stripe  # type: ignore[import]

            stripe.api_key = self._stripe_key
            # TODO: full Stripe subscription flow
            billing.customer_id = f"cus_stub_{billing.tenant_id[:8]}"
            billing.subscription_id = f"sub_stub_{billing.tenant_id[:8]}"
        except ImportError:
            _log.warning("billing.stripe_not_installed")

    async def _create_razorpay_subscription(self, billing: TenantBilling, token: str) -> None:
        """Create Razorpay subscription — requires RAZORPAY_KEY_ID."""
        _log.info("billing.razorpay_subscription_stub", tenant_id=billing.tenant_id)


# Global instance
billing_service = BillingService()
