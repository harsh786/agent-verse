# Agent Store API

> 95 nodes · cohesion 0.06

## Key Concepts

- **agents.py** (57 connections) — `agent-verse-backend/app/api/agents.py`
- **Any** (36 connections)
- **Request** (26 connections)
- **_require_tenant()** (25 connections) — `agent-verse-backend/app/api/agents.py`
- **_agent_store()** (22 connections) — `agent-verse-backend/app/api/agents.py`
- **AgentStore** (22 connections) — `agent-verse-backend/app/api/agents.py`
- **create_agent_nl()** (12 connections) — `agent-verse-backend/app/api/agents.py`
- **create_agent()** (11 connections) — `agent-verse-backend/app/api/agents.py`
- **get** (11 connections)
- **clone_agent()** (10 connections) — `agent-verse-backend/app/api/agents.py`
- **snapshot_agent()** (9 connections) — `agent-verse-backend/app/api/agents.py`
- **.list_async()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **check_rollout_gate()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **list_agent_versions()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **rollback_agent()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **update_agent()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **update_knowledge_binding()** (8 connections) — `agent-verse-backend/app/api/agents.py`
- **Agent** (8 connections) — `agent-verse-backend/app/db/models/agent.py`
- **check_agent_limit()** (8 connections) — `agent-verse-backend/app/tenancy/limits.py`
- **.get_async()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- **.update_async()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- **check_readiness()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- **_create_agent_record()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- **export_agent()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- **issue_agent_credential()** (7 connections) — `agent-verse-backend/app/api/agents.py`
- *... and 70 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (14 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (10 shared connections)
- [Community 82](Community_82.md) (8 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Community 59](Community_59.md) (4 shared connections)
- [Community 210](Community_210.md) (4 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (3 shared connections)
- [Community 81](Community_81.md) (2 shared connections)
- [Community 91](Community_91.md) (2 shared connections)
- [Community 155](Community_155.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/api/agents.py`
- `agent-verse-backend/app/db/models/agent.py`
- `agent-verse-backend/app/intelligence/eval_suite.py`
- `agent-verse-backend/app/tenancy/limits.py`

## Audit Trail

- EXTRACTED: 303 (99%)
- INFERRED: 4 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*