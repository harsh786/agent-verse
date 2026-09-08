# Community 367

> 17 nodes · cohesion 0.15

## Key Concepts

- **ModelRouter (per-task-type model)** (19 connections) — `agent-verse-backend/app/agent/model_router.py`
- **get_router_for_tenant** (7 connections) — `agent-verse-backend/app/agent/model_router.py`
- **ModelRouterConfig** (6 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.complexity_tier()** (4 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.model_for_goal()** (4 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.model_for()** (3 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.with_override()** (3 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/model_router.py`
- **.from_provider_name()** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Any** (1 connections)
- **Compatibility facade over the canonical orchestration classifier.** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Like model_for() but downgrades to a cheaper model for simple goals. For…** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Return a NEW ModelRouter (copy-on-write) with all task types overridden to…** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Build a ModelRouter from a tenant's LLM config dict.** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Per-tenant model routing configuration.** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Routes task types to optimal models for a given provider.** (1 connections) — `agent-verse-backend/app/agent/model_router.py`
- **Return the optimal model name for the given task type. task_type: "planning" |…** (1 connections) — `agent-verse-backend/app/agent/model_router.py`

## Relationships

- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (5 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 277](Community_277.md) (1 shared connections)
- [Community 389](Community_389.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/model_router.py`

## Audit Trail

- EXTRACTED: 32 (89%)
- INFERRED: 4 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*