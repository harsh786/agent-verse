# Community 473

> 13 nodes · cohesion 0.23

## Key Concepts

- **LargePayloadStore** (9 connections) — `agent-verse-backend/app/workflow/storage.py`
- **.store_if_large()** (5 connections) — `agent-verse-backend/app/workflow/storage.py`
- **storage.py** (4 connections) — `agent-verse-backend/app/workflow/storage.py`
- **.resolve_ref()** (4 connections) — `agent-verse-backend/app/workflow/storage.py`
- **Any** (4 connections)
- **._serialize()** (3 connections) — `agent-verse-backend/app/workflow/storage.py`
- **._get()** (2 connections) — `agent-verse-backend/app/workflow/storage.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/workflow/storage.py`
- **._put()** (2 connections) — `agent-verse-backend/app/workflow/storage.py`
- **LargePayloadStore — offloads step outputs > threshold to object storage. Keeps…** (1 connections) — `agent-verse-backend/app/workflow/storage.py`
- **Store large step outputs outside of LangGraph state.** (1 connections) — `agent-verse-backend/app/workflow/storage.py`
- **Return *value* unchanged if small, else store and return a ref pointer.** (1 connections) — `agent-verse-backend/app/workflow/storage.py`
- **If *value* is a ref pointer, fetch and deserialise; else return as-is.** (1 connections) — `agent-verse-backend/app/workflow/storage.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 110](Community_110.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/workflow/storage.py`

## Audit Trail

- EXTRACTED: 20 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*