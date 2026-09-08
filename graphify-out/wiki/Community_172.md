# Community 172

> 32 nodes · cohesion 0.10

## Key Concepts

- **AgentRouter** (18 connections) — `agent-verse-backend/app/agent/router.py`
- **.route()** (11 connections) — `agent-verse-backend/app/agent/router.py`
- **agent/router.py** (8 connections) — `agent-verse-backend/app/agent/router.py`
- **._score_by_keywords()** (7 connections) — `agent-verse-backend/app/agent/router.py`
- **._score_by_llm()** (7 connections) — `agent-verse-backend/app/agent/router.py`
- **Any** (7 connections)
- **._score_by_connector_match()** (5 connections) — `agent-verse-backend/app/agent/router.py`
- **AgentScore** (5 connections) — `agent-verse-backend/app/agent/router.py`
- **AgentRouter.route (weighted composite scoring)** (5 connections) — `agent-verse-backend/app/agent/router.py`
- **RoutingDecision** (5 connections) — `agent-verse-backend/app/agent/router.py`
- **._agent_primary_system()** (4 connections) — `agent-verse-backend/app/agent/router.py`
- **._score_by_history()** (4 connections) — `agent-verse-backend/app/agent/router.py`
- **._score_by_history_db()** (4 connections) — `agent-verse-backend/app/agent/router.py`
- **._tokenize()** (4 connections) — `agent-verse-backend/app/agent/router.py`
- **._named_system_in_goal()** (3 connections) — `agent-verse-backend/app/agent/router.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/router.py`
- **needs_human_choice routing mode (HITL disambiguation)** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Intent-based agent router — picks the best-fit agent for a goal.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Return the system explicitly named in the goal, or None.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Return the primary connector system for this agent, or None.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Jaccard-style overlap between goal words and agent name + goal_template. Anti-…** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Score breakdown for a single candidate agent.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Bidirectional connector relevance score. Old approach: checked if the raw…** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Return historical success rate from eval store, or 0.0 when unavailable.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- **Result of routing a goal to the best-fit agent.** (1 connections) — `agent-verse-backend/app/agent/router.py`
- *... and 7 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (6 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 151](Community_151.md) (1 shared connections)
- [Community 144](Community_144.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/router.py`

## Audit Trail

- EXTRACTED: 64 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*