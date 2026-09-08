# Community 430

> 15 nodes · cohesion 0.17

## Key Concepts

- **EpisodicMemoryStore** (11 connections) — `agent-verse-backend/app/memory/episodic.py`
- **Episode (goal/actions/outcome/lessons)** (9 connections) — `agent-verse-backend/app/memory/episodic.py`
- **.record()** (7 connections) — `agent-verse-backend/app/memory/episodic.py`
- **.recall()** (4 connections) — `agent-verse-backend/app/memory/episodic.py`
- **.format_for_context()** (3 connections) — `agent-verse-backend/app/memory/episodic.py`
- **._recall_from_db()** (3 connections) — `agent-verse-backend/app/memory/episodic.py`
- **.to_context_snippet()** (2 connections) — `agent-verse-backend/app/memory/episodic.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/memory/episodic.py`
- **Any** (1 connections)
- **Recall similar past episodes for a given goal.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`
- **Format episodes as a context block for planner prompt.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`
- **A single past experience.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`
- **Format for injection into planner context.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`
- **DB-backed episodic memory for cross-session experience recall.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`
- **Record a goal execution as an episode. Called on goal completion.** (1 connections) — `agent-verse-backend/app/memory/episodic.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (6 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 283](Community_283.md) (1 shared connections)
- [Community 217](Community_217.md) (1 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/memory/episodic.py`

## Audit Trail

- EXTRACTED: 27 (90%)
- INFERRED: 3 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*