# Community 406

> 16 nodes · cohesion 0.15

## Key Concepts

- **ExecutionMemory** (17 connections) — `agent-verse-backend/app/memory/execution.py`
- **.load_from_db()** (4 connections) — `agent-verse-backend/app/memory/execution.py`
- **.record_async()** (4 connections) — `agent-verse-backend/app/memory/execution.py`
- **Any** (4 connections)
- **.recall_async()** (3 connections) — `agent-verse-backend/app/memory/execution.py`
- **.record_failure_async()** (3 connections) — `agent-verse-backend/app/memory/execution.py`
- **.recall()** (2 connections) — `agent-verse-backend/app/memory/execution.py`
- **.recall_failures()** (2 connections) — `agent-verse-backend/app/memory/execution.py`
- **.record()** (2 connections) — `agent-verse-backend/app/memory/execution.py`
- **.record_failure()** (2 connections) — `agent-verse-backend/app/memory/execution.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/memory/execution.py`
- **Persist failed attempt to DB for cross-session pattern learning.** (1 connections) — `agent-verse-backend/app/memory/execution.py`
- **Per-tenant store of past executions (successful plans and failures).** (1 connections) — `agent-verse-backend/app/memory/execution.py`
- **Seed in-memory _plans from DB on startup. Returns count loaded.** (1 connections) — `agent-verse-backend/app/memory/execution.py`
- **Recall relevant execution plans from DB for a given goal. Falls back to in-…** (1 connections) — `agent-verse-backend/app/memory/execution.py`
- **Record to both in-memory dict and PostgreSQL. Uses ``tenant_id`` (str) directly…** (1 connections) — `agent-verse-backend/app/memory/execution.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 49](Community_49.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/memory/execution.py`

## Audit Trail

- EXTRACTED: 31 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*