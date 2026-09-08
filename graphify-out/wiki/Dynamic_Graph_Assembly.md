# Dynamic Graph Assembly

> 107 nodes · cohesion 0.04

## Key Concepts

- **runtime_profile.py** (56 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **PatternSelector** (25 connections) — `agent-verse-backend/app/orchestration/pattern_selector.py`
- **pattern_selector.py** (21 connections) — `agent-verse-backend/app/orchestration/pattern_selector.py`
- **runtime_profile_builder.py** (21 connections) — `agent-verse-backend/app/orchestration/runtime_profile_builder.py`
- **DynamicGraphAssembler** (20 connections) — `agent-verse-backend/app/agent/dynamic_graph.py`
- **RuntimeProfileBuilder** (18 connections) — `agent-verse-backend/app/orchestration/runtime_profile_builder.py`
- **dynamic_graph.py** (17 connections) — `agent-verse-backend/app/agent/dynamic_graph.py`
- **RiskLevel** (15 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **.assemble()** (14 connections) — `agent-verse-backend/app/agent/dynamic_graph.py`
- **orchestration/goal_classifier.py** (14 connections) — `agent-verse-backend/app/orchestration/goal_classifier.py`
- **GoalClassifier** (13 connections) — `agent-verse-backend/app/orchestration/goal_classifier.py`
- **compatibility.py** (12 connections) — `agent-verse-backend/app/orchestration/compatibility.py`
- **CompatibilityEvaluator** (11 connections) — `agent-verse-backend/app/orchestration/compatibility.py`
- **Complexity** (11 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **.classify_with_llm()** (10 connections) — `agent-verse-backend/app/orchestration/goal_classifier.py`
- **GoalProperties** (10 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **.build_with_trace()** (9 connections) — `agent-verse-backend/app/orchestration/runtime_profile_builder.py`
- **KnowledgeState** (9 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **StrategyRejection** (9 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **StrategySelection** (9 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **agent/model_router.py** (8 connections) — `agent-verse-backend/app/agent/model_router.py`
- **model_optimizer.py** (8 connections) — `agent-verse-backend/app/optimization/model_optimizer.py`
- **_ToDictMixin** (8 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **ModelOptimizer** (7 connections) — `agent-verse-backend/app/optimization/model_optimizer.py`
- **GoalProperties** (7 connections)
- *... and 82 more nodes in this community*

## Relationships

- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (24 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (12 shared connections)
- [Community 66](Community_66.md) (10 shared connections)
- [Community 168](Community_168.md) (9 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (7 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (7 shared connections)
- [Community 389](Community_389.md) (5 shared connections)
- [Community 367](Community_367.md) (5 shared connections)
- [Eval Scoring](Eval_Scoring.md) (4 shared connections)
- [Community 425](Community_425.md) (4 shared connections)
- [Community 277](Community_277.md) (3 shared connections)
- [Community 56](Community_56.md) (3 shared connections)

## Source Files

- `agent-verse-backend/app/agent/dynamic_graph.py`
- `agent-verse-backend/app/agent/model_router.py`
- `agent-verse-backend/app/agent/patterns/dynamic_graph_assembler.py`
- `agent-verse-backend/app/agent/prompts.py`
- `agent-verse-backend/app/intelligence/cost_optimizer.py`
- `agent-verse-backend/app/optimization/cost_optimizer.py`
- `agent-verse-backend/app/optimization/model_optimizer.py`
- `agent-verse-backend/app/orchestration/compatibility.py`
- `agent-verse-backend/app/orchestration/goal_classifier.py`
- `agent-verse-backend/app/orchestration/pattern_selector.py`
- `agent-verse-backend/app/orchestration/runtime_profile.py`
- `agent-verse-backend/app/orchestration/runtime_profile_builder.py`
- `agent-verse-backend/app/state_runtime/knowledge_policy.py`

## Audit Trail

- EXTRACTED: 296 (84%)
- INFERRED: 55 (16%)
- AMBIGUOUS: 1 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*