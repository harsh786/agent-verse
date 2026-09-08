# Community 530

> 10 nodes · cohesion 0.22

## Key Concepts

- **get_breakdown()** (7 connections) — `agent-verse-backend/app/observability/cost_breakdown.py`
- **list_traces()** (5 connections) — `agent-verse-backend/app/api/analytics.py`
- **GET /goals/{goal_id}/cost-metrics** (5 connections) — `agent-verse-backend/app/observability/cost_breakdown_api.py`
- **get_goal_cost_metrics()** (5 connections) — `agent-verse-backend/app/observability/cost_breakdown_api.py`
- **cost_breakdown.py (per-goal per-role cost tracking)** (2 connections) — `agent-verse-backend/app/observability/cost_breakdown.py`
- **List agent execution traces. Integrates with OTel when configured.** (1 connections) — `agent-verse-backend/app/api/analytics.py`
- **get** (1 connections)
- **Request** (1 connections)
- **Cost breakdown API endpoint — per-role token/cost attribution per goal. Exposes…** (1 connections) — `agent-verse-backend/app/observability/cost_breakdown_api.py`
- **Return per-role (planner/executor/verifier) token and cost breakdown for a goal.** (1 connections) — `agent-verse-backend/app/observability/cost_breakdown_api.py`

## Relationships

- [Community 352](Community_352.md) (4 shared connections)
- [Community 545](Community_545.md) (2 shared connections)
- [Community 320](Community_320.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 361](Community_361.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/analytics.py`
- `agent-verse-backend/app/observability/cost_breakdown.py`
- `agent-verse-backend/app/observability/cost_breakdown_api.py`

## Audit Trail

- EXTRACTED: 18 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*