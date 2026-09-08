# Persistence & Retry Services

> 180 nodes · cohesion 0.02

## Key Concepts

- **TenantContext (immutable per-request identity)** (482 connections) — `agent-verse-backend/app/tenancy/context.py`
- **GoalService** (110 connections) — `agent-verse-backend/app/services/goal_service.py`
- **goal_service.py** (99 connections) — `agent-verse-backend/app/services/goal_service.py`
- **Any** (36 connections)
- **._make_agent_loop_for_tenant()** (28 connections) — `agent-verse-backend/app/services/goal_service.py`
- **.submit_goal()** (24 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._dispatch_event()** (21 connections) — `agent-verse-backend/app/services/goal_service.py`
- **SelfOptimizer** (19 connections) — `agent-verse-backend/app/intelligence/self_optimization.py`
- **GoalRecord dataclass** (16 connections) — `agent-verse-backend/app/services/goal_service.py`
- **NotFoundError** (14 connections) — `agent-verse-backend/app/core/errors.py`
- **._get_record()** (13 connections) — `agent-verse-backend/app/services/goal_service.py`
- **.subscribe_events()** (13 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._db_get_goal_record()** (11 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._run_agent_loop()** (11 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._events_for_replay()** (10 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._run_agent_loop_isolated()** (10 connections) — `agent-verse-backend/app/services/goal_service.py`
- **PersistenceConfig** (9 connections) — `agent-verse-backend/app/agent/persistence.py`
- **._build_tool_context()** (9 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._get_agent_store()** (9 connections) — `agent-verse-backend/app/services/goal_service.py`
- **.get_goal()** (9 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._run_agent_loop_persistent()** (9 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._run_workflow()** (9 connections) — `agent-verse-backend/app/services/goal_service.py`
- **.persist_suggestion()** (8 connections) — `agent-verse-backend/app/intelligence/self_optimization.py`
- **._build_runtime_profile()** (8 connections) — `agent-verse-backend/app/services/goal_service.py`
- **._refresh_goal_from_db_if_needed()** (8 connections) — `agent-verse-backend/app/services/goal_service.py`
- *... and 155 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (47 shared connections)
- [Community 49](Community_49.md) (29 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (29 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (23 shared connections)
- [Community 102](Community_102.md) (20 shared connections)
- [Community 61](Community_61.md) (20 shared connections)
- [Coordination Contracts](Coordination_Contracts.md) (19 shared connections)
- [Tenant API Keys/IP Allowlist](Tenant_API_Keys-IP_Allowlist.md) (17 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (17 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (15 shared connections)
- [Agent Store API](Agent_Store_API.md) (14 shared connections)
- [Community 97](Community_97.md) (14 shared connections)

## Source Files

- `agent-verse-backend/app/agent/persistence.py`
- `agent-verse-backend/app/api/agents.py`
- `agent-verse-backend/app/core/errors.py`
- `agent-verse-backend/app/intelligence/self_optimization.py`
- `agent-verse-backend/app/orchestration/strategy_certification.py`
- `agent-verse-backend/app/qos/queue_policy.py`
- `agent-verse-backend/app/rpa/credential_injector.py`
- `agent-verse-backend/app/services/event_store.py`
- `agent-verse-backend/app/services/goal_events.py`
- `agent-verse-backend/app/services/goal_lifecycle.py`
- `agent-verse-backend/app/services/goal_queue.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/tenancy/context.py`
- `agent-verse-backend/app/tenancy/limits.py`

## Audit Trail

- EXTRACTED: 992 (95%)
- INFERRED: 48 (5%)
- AMBIGUOUS: 1 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*