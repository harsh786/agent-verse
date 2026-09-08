# Community 345

> 19 nodes · cohesion 0.14

## Key Concepts

- **UsageService** (12 connections) — `agent-verse-backend/app/services/usage_service.py`
- **usage_service.py** (6 connections) — `agent-verse-backend/app/services/usage_service.py`
- **.record()** (6 connections) — `agent-verse-backend/app/services/usage_service.py`
- **._flush()** (5 connections) — `agent-verse-backend/app/services/usage_service.py`
- **._get_lock()** (4 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Any** (3 connections)
- **.get_usage_summary()** (3 connections) — `agent-verse-backend/app/services/usage_service.py`
- **.record_goal_completion()** (3 connections) — `agent-verse-backend/app/services/usage_service.py`
- **.record_tool_call()** (3 connections) — `agent-verse-backend/app/services/usage_service.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Lock** (1 connections)
- **Usage Metering Service ====================== Emits usage_records for goals,…** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Record a single tool call.** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Return usage summary for the last N days.** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Flush buffered records to DB. Serialised by an asyncio.Lock so concurrent fire-…** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Records usage metrics to DB and/or in-memory buffer. DB writes are batched /…** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Return the asyncio.Lock, creating it lazily on first use.** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Record a usage event. Non-blocking — buffers and flushes async.** (1 connections) — `agent-verse-backend/app/services/usage_service.py`
- **Record metrics at goal completion — goals + llm_tokens.** (1 connections) — `agent-verse-backend/app/services/usage_service.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 152](Community_152.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/services/usage_service.py`

## Audit Trail

- EXTRACTED: 31 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*