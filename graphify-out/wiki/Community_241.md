# Community 241

> 26 nodes · cohesion 0.11

## Key Concepts

- **reliability/goal_lifecycle.py** (11 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **_get_sync_redis()** (8 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_run_with_signals()** (8 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Any** (7 connections)
- **check_pause_cancel()** (6 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **GoalCancelledError** (6 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **is_cancelled_sync()** (6 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **is_paused_sync()** (6 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **signal_cancel()** (5 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **signal_pause()** (5 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **signal_resume()** (4 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **_get_redis_pool()** (4 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **clear_signals()** (3 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Exception** (1 connections)
- **Cross-process goal lifecycle signals via Redis pub/sub + flag keys. Allows API…** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Check pause/cancel signals. Call between each wave step. - If cancelled: raises…** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Raised when a goal is cancelled by an operator signal.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Signal a running goal to pause. Works across process boundaries via Redis.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Signal a paused goal to resume.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Signal a running goal to cancel immediately.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Clear all signals for a completed/failed goal.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Synchronous check — use from Celery task context.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Synchronous check — use from Celery task context.** (1 connections) — `agent-verse-backend/app/reliability/goal_lifecycle.py`
- **Run agent_runner.run() while periodically polling pause/cancel signals. Polls…** (1 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Get or create a module-level Redis connection pool.** (1 connections) — `agent-verse-backend/app/scaling/tasks.py`
- *... and 1 more nodes in this community*

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (9 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (5 shared connections)
- [Community 154](Community_154.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/goal_lifecycle.py`
- `agent-verse-backend/app/scaling/tasks.py`

## Audit Trail

- EXTRACTED: 56 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*