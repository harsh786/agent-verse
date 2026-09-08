# Community 299

> 22 nodes · cohesion 0.14

## Key Concepts

- **CivilizationBus** (17 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Any** (7 connections)
- **bus.py** (6 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.publish()** (6 connections) — `agent-verse-backend/app/civilization/bus.py`
- **_nullctx** (6 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.subscribe()** (5 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.get_messages()** (4 connections) — `agent-verse-backend/app/civilization/bus.py`
- **._channel()** (3 connections) — `agent-verse-backend/app/civilization/bus.py`
- **._emit_civilization_event()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **._persist_message()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.__aenter__()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.__aexit__()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/civilization/bus.py`
- **datetime** (2 connections)
- **._all_channel()** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **CivilizationBus — Redis pub/sub event bus with PostgreSQL persistence. Topics:…** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Fetch persisted messages from DB for replay.** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Null async context manager to handle redis clients directly.** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Redis pub/sub bus for civilization events with durable persistence.** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Publish a message to the bus. Persists to DB and publishes to Redis.** (1 connections) — `agent-verse-backend/app/civilization/bus.py`
- **Subscribe to topics. Yields messages as dicts.** (1 connections) — `agent-verse-backend/app/civilization/bus.py`

## Relationships

- [Community 67](Community_67.md) (5 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/civilization/bus.py`

## Audit Trail

- EXTRACTED: 38 (90%)
- INFERRED: 4 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*