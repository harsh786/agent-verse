# Scaling & Autoscale Metrics

> 109 nodes · cohesion 0.04

## Key Concepts

- **tasks.py** (169 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Any** (37 connections)
- **task** (33 connections)
- **_run_async()** (23 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **fire_due_schedules (Celery beat task)** (15 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **system_session()** (14 connections) — `agent-verse-backend/app/db/rls.py`
- **civilization_tick()** (10 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **expire_hitl_approvals (maintenance task)** (10 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_load_db_schedules()** (9 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_do_check_email_goals()** (8 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **run_goal_dlq (Celery task)** (8 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **TenantScopedGraphCapabilityAdapter** (7 connections) — `agent-verse-backend/app/rag/gateway.py`
- **detect_stuck_goals (maintenance task)** (7 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **record_queue_depths()** (7 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **run_scheduled_goal (Celery task)** (7 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **beat_task_guard()** (6 connections) — `agent-verse-backend/app/scaling/beat_guard.py`
- **check_email_goals()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **execute_retention_policy()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **flush_audit_wal()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **process_feedback_batch()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **run_gdpr_export()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_update_db_schedule_last_fired_at()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_update_goal_dlq()** (6 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **record_schedule_fire()** (5 connections) — `agent-verse-backend/app/observability/metrics.py`
- **beat_guard.py** (5 connections) — `agent-verse-backend/app/scaling/beat_guard.py`
- *... and 84 more nodes in this community*

## Relationships

- [Community 102](Community_102.md) (26 shared connections)
- [Community 247](Community_247.md) (11 shared connections)
- [Community 206](Community_206.md) (10 shared connections)
- [Community 241](Community_241.md) (9 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (6 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (6 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (5 shared connections)
- [Community 141](Community_141.md) (5 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (5 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (5 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Community 156](Community_156.md) (4 shared connections)

## Source Files

- `agent-verse-backend/app/db/rls.py`
- `agent-verse-backend/app/observability/metrics.py`
- `agent-verse-backend/app/rag/gateway.py`
- `agent-verse-backend/app/scaling/beat_guard.py`
- `agent-verse-backend/app/scaling/celery_app.py`
- `agent-verse-backend/app/scaling/tasks.py`
- `agent-verse-backend/app/services/notification_service.py`

## Audit Trail

- EXTRACTED: 375 (94%)
- INFERRED: 23 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*