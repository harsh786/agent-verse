# Community 246

> 25 nodes · cohesion 0.14

## Key Concepts

- **observability.py** (13 connections) — `agent-verse-backend/app/api/observability.py`
- **Any** (8 connections)
- **list_logs()** (7 connections) — `agent-verse-backend/app/api/observability.py`
- **_require_tenant()** (7 connections) — `agent-verse-backend/app/api/observability.py`
- **StructuredLogStore** (7 connections) — `agent-verse-backend/app/api/observability.py`
- **get_structured_metrics()** (6 connections) — `agent-verse-backend/app/api/observability.py`
- **get_timeseries()** (6 connections) — `agent-verse-backend/app/api/observability.py`
- **stream_logs()** (6 connections) — `agent-verse-backend/app/api/observability.py`
- **Request** (5 connections)
- **get** (4 connections)
- **_evt_to_message()** (3 connections) — `agent-verse-backend/app/api/observability.py`
- **.query()** (3 connections) — `agent-verse-backend/app/api/observability.py`
- **.set_redis()** (3 connections) — `agent-verse-backend/app/api/observability.py`
- **.stream_new_since()** (3 connections) — `agent-verse-backend/app/api/observability.py`
- **StreamingResponse** (1 connections)
- **Observability API — real-time logs, SSE log stream, and structured metrics.** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Fetch log entries for a tenant (newest-first).** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Non-blocking read of entries newer than *last_id* (for SSE). Uses ``XREAD BLOCK…** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Return recent log entries from the Redis Streams store (falls back to goal…** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **SSE stream of real-time log entries via Redis XREAD (event-driven).** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Return structured observability metrics including DB-backed latency percentiles.** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Redis Streams-backed structured log store. Falls back to an in-memory ring…** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Return time-series data for goals, cost, and latency bucketed by time.** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **Wire a real (or fake) Redis client. Called from main.py lifespan.** (1 connections) — `agent-verse-backend/app/api/observability.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/api/observability.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 215](Community_215.md) (2 shared connections)
- [Community 136](Community_136.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 320](Community_320.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/observability.py`

## Audit Trail

- EXTRACTED: 50 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*