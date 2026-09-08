# Community 277

> 23 nodes · cohesion 0.19

## Key Concepts

- **model_orchestrator.py** (18 connections) — `agent-verse-backend/app/ai_router/model_orchestrator.py`
- **pattern_config.py** (13 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **GoalClassifier (two-tier)** (11 connections) — `agent-verse-backend/app/agent/goal_classifier.py`
- **PatternAssembler** (11 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **agent/goal_classifier.py** (10 connections) — `agent-verse-backend/app/agent/goal_classifier.py`
- **pattern_assembler.py** (10 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **RiskLevel** (10 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **Complexity** (9 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **GoalProperties** (8 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **CostLatencyQualityPolicy** (8 connections) — `agent-verse-backend/app/ai_router/cost_latency_quality_policy.py`
- **cost_latency_quality_policy.py** (6 connections) — `agent-verse-backend/app/ai_router/cost_latency_quality_policy.py`
- **Domain** (5 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **Rule (pattern selection rule)** (4 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **GoalClassifier — two-tier goal classification (doc-4 exact implementation).…** (1 connections) — `agent-verse-backend/app/agent/goal_classifier.py`
- **Two-tier goal classifier.** (1 connections) — `agent-verse-backend/app/agent/goal_classifier.py`
- **PatternAssembler — assembles PatternConfig from GoalProperties + agent config.…** (1 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **Assembles PatternConfig by applying ordered rules to GoalProperties.** (1 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **# NOTE: force_no_hitl is intentionally IGNORED — CRITICAL rules win** (1 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **.__post_init__()** (1 connections) — `agent-verse-backend/app/agent/pattern_assembler.py`
- **PatternConfig and GoalProperties — exact doc-4 dataclass contracts. These live…** (1 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **Classified properties of a goal — drives PatternAssembler decisions.** (1 connections) — `agent-verse-backend/app/agent/pattern_config.py`
- **CostLatencyQualityPolicy — selects a model quality tier based on complexity and…** (1 connections) — `agent-verse-backend/app/ai_router/cost_latency_quality_policy.py`
- **Canonical live model assignment owner for every agent role. Runtime profiles…** (1 connections) — `agent-verse-backend/app/ai_router/model_orchestrator.py`

## Relationships

- [Community 389](Community_389.md) (9 shared connections)
- [Community 393](Community_393.md) (7 shared connections)
- [Community 597](Community_597.md) (6 shared connections)
- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (3 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (2 shared connections)
- [Community 599](Community_599.md) (2 shared connections)
- [Content Classification & Embedding Policy](Content_Classification_&_Embedding_Policy.md) (2 shared connections)
- [Community 367](Community_367.md) (1 shared connections)
- [Community 169](Community_169.md) (1 shared connections)
- [Community 215](Community_215.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 529](Community_529.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/goal_classifier.py`
- `agent-verse-backend/app/agent/pattern_assembler.py`
- `agent-verse-backend/app/agent/pattern_config.py`
- `agent-verse-backend/app/ai_router/cost_latency_quality_policy.py`
- `agent-verse-backend/app/ai_router/model_orchestrator.py`

## Audit Trail

- EXTRACTED: 69 (81%)
- INFERRED: 16 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*