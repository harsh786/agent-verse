# Community 401

> 16 nodes · cohesion 0.16

## Key Concepts

- **A2ATaskRecord** (7 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **a2a_dispatch.py** (6 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **InMemoryA2ARepository** (5 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **dispatch_internal_task()** (4 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **a2a_repository.py** (3 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **_sign_payload()** (2 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **.create()** (2 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **.get()** (2 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **.update()** (2 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **Any** (1 connections)
- **Internal agent-to-agent dispatch for civilization members. Uses A2A data model…** (1 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **Produce HMAC-SHA256 signature for an A2A payload.** (1 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **Dispatch an A2A task internally via GoalService (not public HTTP ingress). This…** (1 connections) — `agent-verse-backend/app/civilization/a2a_dispatch.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`
- **BaseModel** (1 connections)
- **Idempotent durable-intent repository for A2A tasks and callbacks.** (1 connections) — `agent-verse-backend/app/civilization/a2a_repository.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/civilization/a2a_dispatch.py`
- `agent-verse-backend/app/civilization/a2a_repository.py`

## Audit Trail

- EXTRACTED: 21 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*