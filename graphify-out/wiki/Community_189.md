# Community 189

> 31 nodes · cohesion 0.10

## Key Concepts

- **tenancy/billing.py** (12 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **BillingService (plan upgrade/invoice/overage)** (12 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.upgrade_plan()** (9 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **TenantBilling** (8 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.get_or_create()** (7 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **._create_razorpay_subscription()** (4 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **._create_stripe_subscription()** (4 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **BillingCycle** (3 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **BillingPlan** (3 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.check_plan_limit()** (3 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.get_customer_portal_url()** (3 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **PaymentProvider** (3 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **StrEnum** (3 connections)
- **.create_invoice()** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.record_usage()** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **PlanLimits** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.compute_overage()** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.plan_limits()** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.get_invoice_history()** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **QA3 — Billing & Payment Integration. Supports pluggable payment providers per…** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **Per spec QA3 — full billing record.** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **Calculate overage charges for current period.** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **QA3 — Billing and payment service. Manages subscriptions, usage tracking, and…** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **Upgrade tenant to a higher plan.** (1 connections) — `agent-verse-backend/app/tenancy/billing.py`
- *... and 6 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/billing.py`

## Audit Trail

- EXTRACTED: 50 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*