# Community 224

> 27 nodes · cohesion 0.13

## Key Concepts

- **RedisCostController (cross-replica budget via Lua)** (18 connections) — `agent-verse-backend/app/governance/cost.py`
- **BudgetConfig** (12 connections) — `agent-verse-backend/app/governance/cost.py`
- **.check_and_record_async()** (12 connections) — `agent-verse-backend/app/governance/cost.py`
- **Any** (8 connections)
- **.get_budget_status()** (7 connections) — `agent-verse-backend/app/governance/cost.py`
- **._daily_key()** (6 connections) — `agent-verse-backend/app/governance/cost.py`
- **.refund_async()** (6 connections) — `agent-verse-backend/app/governance/cost.py`
- **_parse_float()** (5 connections) — `agent-verse-backend/app/governance/cost.py`
- **.check_and_record()** (4 connections) — `agent-verse-backend/app/governance/cost.py`
- **.get_cost_tier()** (4 connections) — `agent-verse-backend/app/governance/cost.py`
- **.get_tenant_cost_today()** (4 connections) — `agent-verse-backend/app/governance/cost.py`
- **._get_ttl_to_midnight()** (3 connections) — `agent-verse-backend/app/governance/cost.py`
- **._goal_key()** (3 connections) — `agent-verse-backend/app/governance/cost.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/governance/cost.py`
- **.try_record_and_check()** (3 connections) — `agent-verse-backend/app/governance/cost.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/cost.py`
- **.configure_tenant_budget()** (2 connections) — `agent-verse-backend/app/governance/cost.py`
- **Production CostController backed by Redis for cross-replica accuracy. Uses an…** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Seconds until next UTC midnight.** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Check budget atomically (check-then-increment). Returns True if within budget.…** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Refund a previously charged cost when the downstream operation failed. Uses…** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Get today's accumulated cost for this tenant from Redis.** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Pure READ operation — never modifies Redis counters (Amendment 6.4). Accepts…** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Decode a Redis GET value (bytes, str, int, or None) to float.** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- **Return cost tier based on budget consumption. Used by ModelRouter to auto-…** (1 connections) — `agent-verse-backend/app/governance/cost.py`
- *... and 2 more nodes in this community*

## Relationships

- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (4 shared connections)
- [Community 636](Community_636.md) (3 shared connections)
- [Community 51](Community_51.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Community 72](Community_72.md) (1 shared connections)
- [Community 49](Community_49.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)
- [Community 218](Community_218.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/cost.py`

## Audit Trail

- EXTRACTED: 59 (91%)
- INFERRED: 6 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*