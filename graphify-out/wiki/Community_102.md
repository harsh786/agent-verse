# Community 102

> 44 nodes · cohesion 0.05

## Key Concepts

- **run_goal (Celery task)** (70 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **AnthropicProvider** (26 connections) — `agent-verse-backend/app/providers/anthropic_provider.py`
- **EventStore** (13 connections) — `agent-verse-backend/app/services/event_store.py`
- **get_provider_env()** (10 connections) — `agent-verse-backend/app/core/config.py`
- **build_safe_web_search_capability()** (9 connections) — `agent-verse-backend/app/rag/agentic/patterns/web_augmented.py`
- **get_llm_config_store()** (9 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **decrement_concurrent_goals()** (8 connections) — `agent-verse-backend/app/tenancy/limits.py`
- **_build_verifier_provider()** (7 connections) — `agent-verse-backend/app/main.py`
- **_get_llm_provider()** (7 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **parse_allowed_domains()** (5 connections) — `agent-verse-backend/app/rag/agentic/patterns/web_augmented.py`
- **_load_worker_policy_engine()** (5 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_build_worker_retrieval_gateway()** (4 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_decrement_after_completion()** (4 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_record_goal_duration_metric()** (4 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **.embed()** (3 connections) — `agent-verse-backend/app/providers/anthropic_provider.py`
- **.stream_complete()** (3 connections) — `agent-verse-backend/app/providers/anthropic_provider.py`
- **append_submitted_goal_event() (Redis publish + EventStore persist)** (3 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_make_worker_goal_bridge() (fresh GoalService per task)** (3 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **_monotonic()** (3 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **CeleryGoalTaskQueue.enqueue_goal()** (3 connections) — `agent-verse-backend/app/services/goal_queue.py`
- **GoalService._subscribe_celery_goal_events()** (3 connections) — `agent-verse-backend/app/services/goal_service.py`
- **GoalService._dispatch_event()** (3 connections) — `agent-verse-backend/app/services/goal_service.py`
- **PLAN_QUEUE_MAP (per-plan queue routing)** (2 connections) — `agent-verse-backend/app/scaling/celery_app.py`
- **EventStore.append_event()** (2 connections) — `agent-verse-backend/app/services/event_store.py`
- **GoalService.start_celery_event_bridge()** (2 connections) — `agent-verse-backend/app/services/goal_service.py`
- *... and 19 more nodes in this community*

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (26 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (20 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (12 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (9 shared connections)
- [Community 167](Community_167.md) (5 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (5 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (4 shared connections)
- [Community 316](Community_316.md) (3 shared connections)
- [Community 438](Community_438.md) (3 shared connections)
- [Community 87](Community_87.md) (2 shared connections)
- [Community 136](Community_136.md) (2 shared connections)
- [Community 84](Community_84.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/core/config.py`
- `agent-verse-backend/app/main.py`
- `agent-verse-backend/app/providers/anthropic_provider.py`
- `agent-verse-backend/app/rag/agentic/patterns/web_augmented.py`
- `agent-verse-backend/app/scaling/celery_app.py`
- `agent-verse-backend/app/scaling/tasks.py`
- `agent-verse-backend/app/services/event_store.py`
- `agent-verse-backend/app/services/goal_queue.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/services/llm_config_store.py`
- `agent-verse-backend/app/tenancy/limits.py`

## Audit Trail

- EXTRACTED: 141 (77%)
- INFERRED: 42 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*