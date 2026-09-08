# Community 233

> 26 nodes · cohesion 0.14

## Key Concepts

- **GoalAnalyticsAggregator** (15 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **analytics/aggregator.py** (11 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **._get_all_goals()** (9 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Any** (9 connections)
- **.goal_metrics()** (8 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **.cost_trends()** (6 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **._parse_created_at()** (6 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **_goal_status_completed()** (5 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **.agent_metrics()** (5 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **.cost_trends_db()** (4 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **._get_goals_from_db()** (4 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **_goal_status_cancelled()** (3 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **_goal_status_failed()** (3 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **datetime** (3 connections)
- **AgentMetrics** (2 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **GoalAnalyticsAggregator — computes behavioural metrics from goal event history.** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Return a timezone-aware datetime for goal.created_at regardless of type.** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Get all goal states, optionally filtered.** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Compute success/failure breakdown for goals. Uses PostgreSQL when ``tenant_id``…** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Return daily/weekly cost aggregates.** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Query cost trends from goals.cost_usd via DATE_TRUNC in PostgreSQL. Returns…** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Per-agent goal performance.** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Check if a goal status represents completion (tolerates StrEnum variants).** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- **Computes analytics from the GoalService in-memory goal states. When ``db`` is…** (1 connections) — `agent-verse-backend/app/analytics/aggregator.py`
- *... and 1 more nodes in this community*

## Relationships

- [Community 352](Community_352.md) (4 shared connections)
- [Community 752](Community_752.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 1033](Community_1033.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/analytics/aggregator.py`

## Audit Trail

- EXTRACTED: 58 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*