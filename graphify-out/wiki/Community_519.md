# Community 519

> 11 nodes · cohesion 0.22

## Key Concepts

- **entitlements.py** (8 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **has_feature / assert_feature (plan feature gate)** (5 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **assert_feature()** (4 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **assert_limit()** (4 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **check_limit()** (4 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **PLAN_CATALOG (billing plan limits)** (2 connections) — `agent-verse-backend/app/tenancy/billing.py`
- **Single entitlement check module. Answers: "Can tenant T use feature F at volume…** (1 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **Raise PermissionError if tenant's plan doesn't include the feature.** (1 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **Raise PermissionError if adding one more resource would exceed plan limits.** (1 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **Return True if the tenant's plan includes the given feature.** (1 connections) — `agent-verse-backend/app/tenancy/entitlements.py`
- **Check if adding one more resource is within plan limits. Returns (allowed:…** (1 connections) — `agent-verse-backend/app/tenancy/entitlements.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (5 shared connections)
- [Community 57](Community_57.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/billing.py`
- `agent-verse-backend/app/tenancy/entitlements.py`

## Audit Trail

- EXTRACTED: 18 (90%)
- INFERRED: 2 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*