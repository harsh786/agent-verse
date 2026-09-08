# Community 157

> 34 nodes · cohesion 0.09

## Key Concepts

- **CostTracker** (23 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **cost_tracker.py** (8 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **Any** (8 connections)
- **.get_budget_status()** (7 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.record_llm_usage()** (7 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._check_ewma_anomaly()** (5 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **calculate_cost()** (4 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._daily_key()** (4 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._load_budgets()** (4 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.predict_cost()** (4 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **CostAnomaly** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.detect_anomaly()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.get_cost_by_model()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.get_cost_trends_with_anomalies()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.get_per_agent_summary()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.get_projected_monthly_cost()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._goal_key()** (3 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **BudgetLimits** (2 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._ewma_key()** (2 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **._today()** (2 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **Cost tracking, budget enforcement, anomaly detection, and cost prediction. This…** (1 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **Central cost-tracking service wired with Redis + DB. Redis keys ----------…** (1 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **Load budget limits from DB; return defaults if unavailable.** (1 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- **Pure READ operation — never modifies Redis counters. This is the safe method to…** (1 connections) — `agent-verse-backend/app/intelligence/cost_tracker.py`
- *... and 9 more nodes in this community*

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Eval Scoring](Eval_Scoring.md) (1 shared connections)
- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/cost_tracker.py`

## Audit Trail

- EXTRACTED: 60 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*