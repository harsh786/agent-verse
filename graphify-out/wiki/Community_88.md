# Community 88

> 48 nodes · cohesion 0.08

## Key Concepts

- **execution_environment/models.py** (39 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **CodeWorkloadValidator** (18 connections) — `agent-verse-backend/app/execution_environment/code_validation.py`
- **CodeExecutionWorkload** (16 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **mcp/code_interpreter.py** (15 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **code_validation.py** (14 connections) — `agent-verse-backend/app/execution_environment/code_validation.py`
- **policy.py** (12 connections) — `agent-verse-backend/app/execution_environment/policy.py`
- **CodeExecutionObservation** (9 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **CodeInterpreterTool (governed boundary)** (9 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **.execute()** (9 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **ExecutionKind** (7 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **PythonPolicyValidator** (6 connections) — `agent-verse-backend/app/execution_environment/python_policy.py`
- **._to_observation()** (6 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **canonical_json()** (5 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **_FrozenCodeModel** (5 connections) — `agent-verse-backend/app/execution_environment/models.py`
- **CodePolicyViolation** (5 connections) — `agent-verse-backend/app/execution_environment/python_policy.py`
- **._record()** (5 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **GovernedToolInvocation** (5 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **.validate()** (4 connections) — `agent-verse-backend/app/execution_environment/code_validation.py`
- **observation_sanitizer.py** (4 connections) — `agent-verse-backend/app/execution_environment/observation_sanitizer.py`
- **sanitize_observation()** (4 connections) — `agent-verse-backend/app/execution_environment/observation_sanitizer.py`
- **python_policy.py** (4 connections) — `agent-verse-backend/app/execution_environment/python_policy.py`
- **CodeInterpreterDeniedError** (4 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **.__init__()** (4 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **._invoke()** (4 connections) — `agent-verse-backend/app/mcp/code_interpreter.py`
- **CodeWorkloadLimits** (3 connections) — `agent-verse-backend/app/execution_environment/code_validation.py`
- *... and 23 more nodes in this community*

## Relationships

- [Community 84](Community_84.md) (23 shared connections)
- [Community 165](Community_165.md) (13 shared connections)
- [Community 415](Community_415.md) (7 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (5 shared connections)
- [Community 527](Community_527.md) (4 shared connections)
- [Community 377](Community_377.md) (4 shared connections)
- [Community 338](Community_338.md) (3 shared connections)
- [Community 140](Community_140.md) (3 shared connections)
- [Community 376](Community_376.md) (2 shared connections)
- [Community 423](Community_423.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/execution_environment/code_validation.py`
- `agent-verse-backend/app/execution_environment/models.py`
- `agent-verse-backend/app/execution_environment/observation_sanitizer.py`
- `agent-verse-backend/app/execution_environment/policy.py`
- `agent-verse-backend/app/execution_environment/python_policy.py`
- `agent-verse-backend/app/mcp/code_interpreter.py`

## Audit Trail

- EXTRACTED: 152 (96%)
- INFERRED: 6 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*