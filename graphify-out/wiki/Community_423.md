# Community 423

> 15 nodes · cohesion 0.17

## Key Concepts

- **ExecutionRequest** (15 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **KubernetesRunner** (12 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **.run()** (7 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **build_workload_manifests()** (5 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **._failure()** (5 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **.run()** (5 connections) — `agent-verse-backend/app/execution_environment/runner_client.py`
- **.__init__()** (4 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **.health_check()** (2 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **ExecutionResult** (2 connections)
- **.runner_type()** (1 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **Build a digest-pinned Job and matching default-deny NetworkPolicy.** (1 connections) — `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- **Thin wrapper sent from the scheduler to a concrete runner.** (1 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **Any** (1 connections)
- **ExecutionResult** (1 connections)
- **Execute the goal described in ``request`` and return a structured result. Args:…** (1 connections) — `agent-verse-backend/app/execution_environment/runner_client.py`

## Relationships

- [Community 165](Community_165.md) (10 shared connections)
- [Community 140](Community_140.md) (7 shared connections)
- [Community 339](Community_339.md) (4 shared connections)
- [Community 338](Community_338.md) (3 shared connections)
- [Community 84](Community_84.md) (1 shared connections)
- [Community 424](Community_424.md) (1 shared connections)
- [Community 88](Community_88.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/execution_environment/kubernetes_runner.py`
- `agent-verse-backend/app/execution_environment/models.py`
- `agent-verse-backend/app/execution_environment/runner_client.py`

## Audit Trail

- EXTRACTED: 43 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*