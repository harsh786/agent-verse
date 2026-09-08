# Community 97

> 45 nodes · cohesion 0.06

## Key Concepts

- **EvalRunner** (24 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **BenchmarkStore** (10 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **EvalScorecard** (10 connections) — `agent-verse-backend/app/intelligence/eval.py`
- **.score()** (10 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **.score_async()** (10 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **self_optimization.py** (10 connections) — `agent-verse-backend/app/intelligence/self_optimization.py`
- **.score_and_persist()** (9 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **benchmarking.py** (8 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **intelligence/eval.py** (6 connections) — `agent-verse-backend/app/intelligence/eval.py`
- **._score_accuracy()** (6 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **AgentBenchmark** (5 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **.compare_agents()** (5 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **.record_eval()** (5 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **Any** (5 connections)
- **.get_benchmark()** (4 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **._score_tool_relevance()** (4 connections) — `agent-verse-backend/app/intelligence/eval_runner.py`
- **.list_benchmarks()** (3 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **Any** (3 connections)
- **EvalScorecard** (3 connections)
- **.to_dict()** (2 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **.load_history_from_db()** (2 connections) — `agent-verse-backend/app/intelligence/benchmarking.py`
- **EvalResult** (2 connections) — `agent-verse-backend/app/intelligence/eval.py`
- **.average_score()** (2 connections) — `agent-verse-backend/app/intelligence/eval.py`
- **.dimension_results()** (2 connections) — `agent-verse-backend/app/intelligence/eval.py`
- *... and 20 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (14 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (14 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (5 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Community 92](Community_92.md) (2 shared connections)
- [Enterprise API Surface](Enterprise_API_Surface.md) (2 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Community 158](Community_158.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/benchmarking.py`
- `agent-verse-backend/app/intelligence/eval.py`
- `agent-verse-backend/app/intelligence/eval_runner.py`
- `agent-verse-backend/app/intelligence/self_optimization.py`
- `agent-verse-backend/app/orchestration/strategy_adapters.py`
- `agent-verse-backend/app/orchestration/strategy_registry.py`

## Audit Trail

- EXTRACTED: 103 (92%)
- INFERRED: 9 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*