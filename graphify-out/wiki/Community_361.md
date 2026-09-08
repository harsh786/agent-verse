# Community 361

> 18 nodes · cohesion 0.13

## Key Concepts

- **GoalDeduplicator** (7 connections) — `agent-verse-backend/app/services/dedup.py`
- **GoalService.submit_goal()** (7 connections) — `agent-verse-backend/app/services/goal_service.py`
- **services/dedup.py** (5 connections) — `agent-verse-backend/app/services/dedup.py`
- **_dedup_key()** (4 connections) — `agent-verse-backend/app/services/dedup.py`
- **.get_existing()** (3 connections) — `agent-verse-backend/app/services/dedup.py`
- **.register()** (3 connections) — `agent-verse-backend/app/services/dedup.py`
- **.release()** (3 connections) — `agent-verse-backend/app/services/dedup.py`
- **_default_deduplicator singleton** (2 connections) — `agent-verse-backend/app/services/dedup.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/services/dedup.py`
- **Any** (1 connections)
- **Goal-level request deduplication. When two tenants submit identical goals…** (1 connections) — `agent-verse-backend/app/services/dedup.py`
- **Redis-backed goal-level deduplication. Usage: dedup =…** (1 connections) — `agent-verse-backend/app/services/dedup.py`
- **Return the in-flight goal_id for this (tenant, goal) pair, or None.** (1 connections) — `agent-verse-backend/app/services/dedup.py`
- **Register a new goal. Returns True if this is the first registration (i.e. no…** (1 connections) — `agent-verse-backend/app/services/dedup.py`
- **Delete the dedup key so future identical goals can be submitted.** (1 connections) — `agent-verse-backend/app/services/dedup.py`
- **GoalService._check_daily_goal_limit_redis()** (1 connections) — `agent-verse-backend/app/services/goal_service.py`
- **NotificationService.notify_goal_complete()** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **check_and_increment_concurrent_goals()** (1 connections) — `agent-verse-backend/app/tenancy/limits.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 530](Community_530.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/services/dedup.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/services/notification_service.py`
- `agent-verse-backend/app/tenancy/limits.py`

## Audit Trail

- EXTRACTED: 23 (92%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 1 (4%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*