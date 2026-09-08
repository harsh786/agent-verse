# Community 105

> 43 nodes · cohesion 0.10

## Key Concepts

- **agent_runtime.py** (13 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **AgentExecutionPlan** (11 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **AgentRole (planner/executor/verifier/critic/judge/reflector/synthesizer/subagent)** (10 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **AgentRunTrace** (10 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **create_execution_plan()** (10 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **agent_runtime/models.py** (9 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **parse_verifier_verdict (JSON + heuristic fallback)** (9 connections) — `agent-verse-backend/app/agent/schemas.py`
- **VerifierVerdict** (9 connections) — `agent-verse-backend/app/agent/schemas.py`
- **_require_tenant()** (9 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **PlanStep (agent_runtime)** (8 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **agent/schemas.py** (7 connections) — `agent-verse-backend/app/agent/schemas.py`
- **create_run_trace()** (7 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **Any** (7 connections)
- **Request** (7 connections)
- **RiskLevel (low/medium/high/critical)** (6 connections) — `agent-verse-backend/app/agent_runtime/models.py`
- **planner_schema()** (6 connections) — `agent-verse-backend/app/agent/schemas.py`
- **verifier_schema()** (6 connections) — `agent-verse-backend/app/agent/schemas.py`
- **get_execution_plan()** (6 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **list_agent_roles()** (6 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **list_strategies()** (6 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **PlannerPlan** (5 connections) — `agent-verse-backend/app/agent/schemas.py`
- **get_run_trace()** (5 connections) — `agent-verse-backend/app/api/agent_runtime.py`
- **StructuredPlan.from_llm_response (planner output parser)** (4 connections) — `agent-verse-backend/app/agent/structured_plan.py`
- **get** (4 connections)
- **StrEnum** (3 connections)
- *... and 18 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (11 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (6 shared connections)
- [Community 151](Community_151.md) (3 shared connections)
- [Community 61](Community_61.md) (2 shared connections)
- [Community 99](Community_99.md) (2 shared connections)
- [Community 292](Community_292.md) (2 shared connections)
- [Community 215](Community_215.md) (2 shared connections)
- [Community 82](Community_82.md) (2 shared connections)
- [Community 169](Community_169.md) (1 shared connections)
- [Community 528](Community_528.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/schemas.py`
- `agent-verse-backend/app/agent/structured_plan.py`
- `agent-verse-backend/app/agent_runtime/models.py`
- `agent-verse-backend/app/api/agent_runtime.py`

## Audit Trail

- EXTRACTED: 98 (82%)
- INFERRED: 22 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*