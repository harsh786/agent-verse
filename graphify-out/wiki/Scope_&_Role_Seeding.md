# Scope & Role Seeding

> 91 nodes · cohesion 0.04

## Key Concepts

- **app/main.py** (245 connections) — `agent-verse-backend/app/main.py`
- **create_app()** (183 connections) — `agent-verse-backend/app/main.py`
- **PostgresPatternStateRepository** (17 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **InMemoryPatternStateRepository** (13 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **PatternRecord** (12 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **start_policy_subscriber()** (8 connections) — `agent-verse-backend/app/governance/policies.py`
- **register_builtin_servers()** (8 connections) — `agent-verse-backend/app/mcp/servers/registry_wiring.py`
- **RetrievalDependencies** (8 connections) — `agent-verse-backend/app/rag/gateway.py`
- **_resolve_provider_for_app()** (7 connections) — `agent-verse-backend/app/main.py`
- **get_approval_engine()** (7 connections) — `agent-verse-backend/app/org/approval_chain.py`
- **select_cache_backend()** (7 connections) — `agent-verse-backend/app/rag/vector_cache_backend.py`
- **seed_builtin_scopes()** (6 connections) — `agent-verse-backend/app/auth/scope_seeder.py`
- **camel/repository.py** (6 connections) — `agent-verse-backend/app/coordination/camel/repository.py`
- **generative/repository.py** (6 connections) — `agent-verse-backend/app/coordination/generative/repository.py`
- **._list()** (6 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **.save()** (6 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **swarm/repository.py** (6 connections) — `agent-verse-backend/app/coordination/swarm/repository.py`
- **make_async_redis()** (6 connections) — `agent-verse-backend/app/net/redis_factory.py`
- **goal_queue.py** (6 connections) — `agent-verse-backend/app/services/goal_queue.py`
- **CeleryGoalTaskQueue** (6 connections) — `agent-verse-backend/app/services/goal_queue.py`
- **warmup_providers()** (6 connections) — `agent-verse-backend/app/voice/providers/__init__.py`
- **scope_seeder.py** (5 connections) — `agent-verse-backend/app/auth/scope_seeder.py`
- **._decode()** (5 connections) — `agent-verse-backend/app/coordination/state_repository.py`
- **_register_error_handlers()** (5 connections) — `agent-verse-backend/app/main.py`
- **redis_factory.py** (5 connections) — `agent-verse-backend/app/net/redis_factory.py`
- *... and 66 more nodes in this community*

## Relationships

- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (26 shared connections)
- [Community 102](Community_102.md) (12 shared connections)
- [Community 87](Community_87.md) (11 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (11 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (10 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (10 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (9 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (8 shared connections)
- [Community 68](Community_68.md) (8 shared connections)
- [Community 57](Community_57.md) (8 shared connections)
- [Coordination Contracts](Coordination_Contracts.md) (8 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (8 shared connections)

## Source Files

- `agent-verse-backend/app/auth/scope_seeder.py`
- `agent-verse-backend/app/coordination/auction/repository.py`
- `agent-verse-backend/app/coordination/camel/repository.py`
- `agent-verse-backend/app/coordination/generative/repository.py`
- `agent-verse-backend/app/coordination/state_repository.py`
- `agent-verse-backend/app/coordination/swarm/repository.py`
- `agent-verse-backend/app/core/errors.py`
- `agent-verse-backend/app/governance/policies.py`
- `agent-verse-backend/app/ingestion/connector_registry.py`
- `agent-verse-backend/app/main.py`
- `agent-verse-backend/app/mcp/servers/registry_wiring.py`
- `agent-verse-backend/app/net/redis_factory.py`
- `agent-verse-backend/app/observability/logging.py`
- `agent-verse-backend/app/org/approval_chain.py`
- `agent-verse-backend/app/org/events.py`
- `agent-verse-backend/app/rag/cross_encoder.py`
- `agent-verse-backend/app/rag/gateway.py`
- `agent-verse-backend/app/rag/vector_cache_backend.py`
- `agent-verse-backend/app/reliability/tool_inverses.py`
- `agent-verse-backend/app/services/goal_queue.py`

## Audit Trail

- EXTRACTED: 570 (99%)
- INFERRED: 8 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*