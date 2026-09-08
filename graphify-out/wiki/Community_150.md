# Community 150

> 35 nodes · cohesion 0.11

## Key Concepts

- **StructuredPlanExecutor** (22 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **llm_compiler.py** (21 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **LLMCompilerRuntime** (18 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **structured_executor.py** (15 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **.execute()** (13 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **ExecutionCheckpoint** (11 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **.execute()** (10 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **CompiledTask** (7 connections) — `agent-verse-backend/app/agent/patterns/reasoning_contracts.py`
- **.validate_compilation()** (6 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **Any** (6 connections)
- **StepExecutionError** (6 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **validate_compiled_tasks()** (5 connections) — `agent-verse-backend/app/agent/patterns/reasoning_contracts.py`
- **ResumeMismatchError** (5 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **._validate_schema()** (4 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **LoopExhaustedError** (4 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **.create_runtime()** (3 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **._invoke()** (3 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **._matches_type()** (3 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- **StructuredExecutionResult** (3 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **.plan_hash()** (3 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **BaseModel** (2 connections)
- **RuntimeError** (2 connections)
- **Event** (1 connections)
- **Validated LLM Compiler adapter backed by the canonical DAG executor.** (1 connections) — `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- *... and 10 more nodes in this community*

## Relationships

- [Community 61](Community_61.md) (23 shared connections)
- [Community 53](Community_53.md) (15 shared connections)
- [Community 66](Community_66.md) (8 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (4 shared connections)
- [Agent Pattern Base](Agent_Pattern_Base.md) (2 shared connections)
- [Community 668](Community_668.md) (2 shared connections)
- [Community 294](Community_294.md) (2 shared connections)
- [Community 373](Community_373.md) (1 shared connections)
- [Community 528](Community_528.md) (1 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- `agent-verse-backend/app/agent/patterns/reasoning_contracts.py`
- `agent-verse-backend/app/agent/structured_executor.py`

## Audit Trail

- EXTRACTED: 108 (88%)
- INFERRED: 15 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*