# Community 412

> 16 nodes · cohesion 0.21

## Key Concepts

- **celery_tasks.py** (10 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **execute_workflow_run** (9 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **_run_async()** (7 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **check_hitl_escalations()** (4 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **cleanup_expired_runs()** (4 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **_get_runner()** (4 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **task** (4 connections)
- **retry_dead_letter_webhooks()** (4 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Any** (3 connections)
- **Workflow Celery tasks — execution, HITL resume, periodic maintenance.** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Run every 5 minutes to retry failed webhook deliveries.** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Run daily to delete runs older than workflow's run_retention_days.** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Lazily import runner from app.state to avoid circular imports.** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Run an async coroutine from a Celery task (sync context).** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Execute a workflow run (or resume after HITL).** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`
- **Run every 15 minutes to auto-escalate overdue HITL requests.** (1 connections) — `agent-verse-backend/app/workflow/celery_tasks.py`

## Relationships

- [Community 75](Community_75.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 903](Community_903.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/workflow/celery_tasks.py`

## Audit Trail

- EXTRACTED: 31 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*