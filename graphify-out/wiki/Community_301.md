# Community 301

> 22 nodes · cohesion 0.14

## Key Concepts

- **get_runtime_flags()** (13 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **ReadinessGate** (9 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **DependencyHealth** (7 connections) — `agent-verse-backend/app/runtime_readiness/dependency_health.py`
- **readiness_gate.py** (7 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **runtime_flags.py** (6 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **._check_readiness()** (6 connections) — `agent-verse-backend/app/services/goal_service.py`
- **dependency_health.py** (4 connections) — `agent-verse-backend/app/runtime_readiness/dependency_health.py`
- **_env_set()** (3 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **RuntimeFlags** (3 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **.from_env()** (3 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **DepStatus** (3 connections) — `agent-verse-backend/app/runtime_readiness/dependency_health.py`
- **.check()** (3 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **ReadinessResult** (3 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **_bool_env()** (2 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **_env_bool()** (2 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **.all_healthy()** (2 connections) — `agent-verse-backend/app/runtime_readiness/dependency_health.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **.to_dict()** (2 connections) — `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- **Runtime feature flags loaded from environment variables. All new orchestration…** (1 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **Return cached RuntimeFlags loaded from env once at startup.** (1 connections) — `agent-verse-backend/app/core/runtime_flags.py`
- **Any** (1 connections)
- **Check the exact runtime profile selected for this goal. A readiness…** (1 connections) — `agent-verse-backend/app/services/goal_service.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (8 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/core/runtime_flags.py`
- `agent-verse-backend/app/runtime_readiness/dependency_health.py`
- `agent-verse-backend/app/runtime_readiness/readiness_gate.py`
- `agent-verse-backend/app/services/goal_service.py`

## Audit Trail

- EXTRACTED: 46 (90%)
- INFERRED: 5 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*