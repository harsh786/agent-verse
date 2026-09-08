# Community 467

> 13 nodes · cohesion 0.19

## Key Concepts

- **ProceduralMemoryStore** (10 connections) — `agent-verse-backend/app/memory/procedural.py`
- **Skill (goal pattern → tool sequence)** (10 connections) — `agent-verse-backend/app/memory/procedural.py`
- **.recall()** (4 connections) — `agent-verse-backend/app/memory/procedural.py`
- **.format_for_context()** (3 connections) — `agent-verse-backend/app/memory/procedural.py`
- **._recall_from_db()** (3 connections) — `agent-verse-backend/app/memory/procedural.py`
- **ToolReliabilityStore (per-tenant per-tool success/latency memory)** (3 connections) — `agent-verse-backend/app/memory/tool_reliability.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/memory/procedural.py`
- **Any** (1 connections)
- **Recall skills relevant to a goal.** (1 connections) — `agent-verse-backend/app/memory/procedural.py`
- **Format skills as a context block for planner prompt.** (1 connections) — `agent-verse-backend/app/memory/procedural.py`
- **A learned tool-use pattern.** (1 connections) — `agent-verse-backend/app/memory/procedural.py`
- **DB-backed procedural memory for cross-session skill learning.** (1 connections) — `agent-verse-backend/app/memory/procedural.py`
- **.to_hint()** (1 connections) — `agent-verse-backend/app/memory/procedural.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 217](Community_217.md) (2 shared connections)
- [Community 278](Community_278.md) (1 shared connections)
- [Community 283](Community_283.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/memory/procedural.py`
- `agent-verse-backend/app/memory/tool_reliability.py`

## Audit Trail

- EXTRACTED: 21 (81%)
- INFERRED: 5 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*