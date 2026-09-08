# Agent LangGraph Kernel

> 237 nodes · cohesion 0.02

## Key Concepts

- **get_logger()** (575 connections) — `agent-verse-backend/app/observability/logging.py`
- **tenancy/context.py** (97 connections) — `agent-verse-backend/app/tenancy/context.py`
- **Enum** (87 connections)
- **executor_mixin.py** (84 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **AgentGraph (LangGraph kernel)** (83 connections) — `agent-verse-backend/app/agent/graph.py`
- **AgentState** (83 connections) — `agent-verse-backend/app/agent/state.py`
- **agent/graph.py** (67 connections) — `agent-verse-backend/app/agent/graph.py`
- **GoalStatus** (40 connections) — `agent-verse-backend/app/agent/state.py`
- **GraphState** (39 connections) — `agent-verse-backend/app/agent/graph_types.py`
- **verifier_mixin.py** (37 connections) — `agent-verse-backend/app/agent/nodes/verifier_mixin.py`
- **ExecutorMixin** (33 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **planner_mixin.py** (33 connections) — `agent-verse-backend/app/agent/nodes/planner_mixin.py`
- **agent/state.py** (33 connections) — `agent-verse-backend/app/agent/state.py`
- **reasoning_mixin.py** (23 connections) — `agent-verse-backend/app/agent/nodes/reasoning_mixin.py`
- **initialize_mixin.py** (22 connections) — `agent-verse-backend/app/agent/nodes/initialize_mixin.py`
- **rag_mixin.py** (22 connections) — `agent-verse-backend/app/agent/nodes/rag_mixin.py`
- **guardrails_v2/engine.py** (20 connections) — `agent-verse-backend/app/guardrails_v2/engine.py`
- **GuardrailLayer** (19 connections) — `agent-verse-backend/app/guardrails_v2/models.py`
- **ReasoningMixin** (18 connections) — `agent-verse-backend/app/agent/nodes/reasoning_mixin.py`
- **._node_verify()** (18 connections) — `agent-verse-backend/app/agent/nodes/verifier_mixin.py`
- **policies.py** (18 connections) — `agent-verse-backend/app/governance/policies.py`
- **guardrails_v2/models.py** (18 connections) — `agent-verse-backend/app/guardrails_v2/models.py`
- **._node_plan()** (17 connections) — `agent-verse-backend/app/agent/nodes/planner_mixin.py`
- **routing_mixin.py** (17 connections) — `agent-verse-backend/app/agent/nodes/routing_mixin.py`
- **governance/hitl.py** (17 connections) — `agent-verse-backend/app/governance/hitl.py`
- *... and 212 more nodes in this community*

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (134 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (47 shared connections)
- [Eval Scoring](Eval_Scoring.md) (46 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (40 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (35 shared connections)
- [Community 49](Community_49.md) (29 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (24 shared connections)
- [Community 99](Community_99.md) (24 shared connections)
- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (17 shared connections)
- [Community 78](Community_78.md) (16 shared connections)
- [Community 96](Community_96.md) (14 shared connections)
- [Community 97](Community_97.md) (14 shared connections)

## Source Files

- `agent-verse-backend/app/agent/__init__.py`
- `agent-verse-backend/app/agent/graph.py`
- `agent-verse-backend/app/agent/graph_types.py`
- `agent-verse-backend/app/agent/loop.py`
- `agent-verse-backend/app/agent/nodes/_helpers.py`
- `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- `agent-verse-backend/app/agent/nodes/initialize_mixin.py`
- `agent-verse-backend/app/agent/nodes/planner_mixin.py`
- `agent-verse-backend/app/agent/nodes/rag_mixin.py`
- `agent-verse-backend/app/agent/nodes/reasoning_mixin.py`
- `agent-verse-backend/app/agent/nodes/routing_mixin.py`
- `agent-verse-backend/app/agent/nodes/verifier_mixin.py`
- `agent-verse-backend/app/agent/prompts.py`
- `agent-verse-backend/app/agent/state.py`
- `agent-verse-backend/app/context/output_contract_builder.py`
- `agent-verse-backend/app/evals/agent_score.py`
- `agent-verse-backend/app/evals/dataset_builder.py`
- `agent-verse-backend/app/evals/goal_score.py`
- `agent-verse-backend/app/governance/cost.py`
- `agent-verse-backend/app/governance/hitl.py`

## Audit Trail

- EXTRACTED: 1577 (91%)
- INFERRED: 152 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*