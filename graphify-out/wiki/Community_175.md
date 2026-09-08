# Community 175

> 32 nodes · cohesion 0.10

## Key Concepts

- **ExperimentRegistry** (14 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **ABTestingEngine** (9 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **ExperimentType** (9 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **Any** (5 connections)
- **.load_from_db()** (5 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **experiment_registry.py** (4 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **._clear_active()** (4 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.propose()** (4 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **._update_stats()** (4 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **ab_testing.py** (4 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.record_result()** (4 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.record_result_async()** (4 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.evaluate()** (3 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.record_outcome()** (3 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.can_promote_variant()** (3 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.get_arm_stats()** (3 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.get_experiment_arm()** (3 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **.list_experiments()** (2 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.promote()** (2 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.rollback()** (2 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **ExperimentArm** (2 connections) — `agent-verse-backend/app/optimization/ab_testing.py`
- **Any** (2 connections)
- **.__init__()** (1 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- **Experiment Registry =================== Single control plane for ALL self-…** (1 connections) — `agent-verse-backend/app/intelligence/experiment_registry.py`
- *... and 7 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (1 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (1 shared connections)
- [Community 215](Community_215.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/experiment_registry.py`
- `agent-verse-backend/app/optimization/ab_testing.py`

## Audit Trail

- EXTRACTED: 52 (91%)
- INFERRED: 4 (7%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*