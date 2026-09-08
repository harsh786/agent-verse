# Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)

> 100 nodes · cohesion 0.04

## Key Concepts

- **strategy_adapters.py** (73 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **ExecutionTier** (62 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **RAGRuntimeAdapter (Protocol)** (22 connections) — `agent-verse-backend/app/rag/contracts.py`
- **GraphFactory** (19 connections) — `agent-verse-backend/app/orchestration/graph_factory.py`
- **build_default_registry** (19 connections) — `agent-verse-backend/app/orchestration/strategy_registry.py`
- **AdapterDescriptor** (15 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **program_of_thought.py** (13 connections) — `agent-verse-backend/app/agent/patterns/program_of_thought.py`
- **durable_coordination_descriptor()** (12 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **durable_coordination_descriptor** (12 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **local_reasoning_descriptor()** (11 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- **lats.py** (10 connections) — `agent-verse-backend/app/agent/patterns/lats.py`
- **voyager.py** (10 connections) — `agent-verse-backend/app/agent/patterns/voyager.py`
- **consensus_adapter.py** (10 connections) — `agent-verse-backend/app/coordination/patterns/consensus_adapter.py`
- **is_rag_runtime_adapter()** (10 connections) — `agent-verse-backend/app/rag/contracts.py`
- **core_execution.py** (9 connections) — `agent-verse-backend/app/agent/patterns/core_execution.py`
- **graph_factory.py** (9 connections) — `agent-verse-backend/app/orchestration/graph_factory.py`
- **autogpt.py** (8 connections) — `agent-verse-backend/app/agent/patterns/autogpt.py`
- **.execute()** (8 connections) — `agent-verse-backend/app/coordination/patterns/consensus_adapter.py`
- **PlanExecuteStrategyAdapter** (7 connections) — `agent-verse-backend/app/agent/patterns/core_execution.py`
- **MarketAuctionAdapter** (7 connections) — `agent-verse-backend/app/coordination/auction/adapter.py`
- **DurableDebateAdapter** (7 connections) — `agent-verse-backend/app/coordination/patterns/debate_adapter.py`
- **DurableGoalTreeAdapter** (7 connections) — `agent-verse-backend/app/coordination/patterns/goal_tree_adapter.py`
- **coordination/patterns/__init__.py** (7 connections) — `agent-verse-backend/app/coordination/patterns/__init__.py`
- **DurableSupervisorAdapter** (7 connections) — `agent-verse-backend/app/coordination/patterns/supervisor_adapter.py`
- **rag_adapter_descriptor()** (7 connections) — `agent-verse-backend/app/orchestration/strategy_adapters.py`
- *... and 75 more nodes in this community*

## Relationships

- [Community 94](Community_94.md) (26 shared connections)
- [Community 53](Community_53.md) (18 shared connections)
- [Community 66](Community_66.md) (14 shared connections)
- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (12 shared connections)
- [Federated RAG Search](Federated_RAG_Search.md) (11 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (11 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (8 shared connections)
- [Agent Pattern Base](Agent_Pattern_Base.md) (7 shared connections)
- [Community 61](Community_61.md) (6 shared connections)
- [Community 168](Community_168.md) (6 shared connections)
- [Community 88](Community_88.md) (5 shared connections)
- [Community 278](Community_278.md) (5 shared connections)

## Source Files

- `agent-verse-backend/app/agent/patterns/autogpt.py`
- `agent-verse-backend/app/agent/patterns/babyagi.py`
- `agent-verse-backend/app/agent/patterns/codeact.py`
- `agent-verse-backend/app/agent/patterns/constitutional_ai.py`
- `agent-verse-backend/app/agent/patterns/core_execution.py`
- `agent-verse-backend/app/agent/patterns/few_shot_cot.py`
- `agent-verse-backend/app/agent/patterns/graph_of_thoughts.py`
- `agent-verse-backend/app/agent/patterns/lats.py`
- `agent-verse-backend/app/agent/patterns/least_to_most.py`
- `agent-verse-backend/app/agent/patterns/llm_compiler.py`
- `agent-verse-backend/app/agent/patterns/program_of_thought.py`
- `agent-verse-backend/app/agent/patterns/rewoo.py`
- `agent-verse-backend/app/agent/patterns/voyager.py`
- `agent-verse-backend/app/coordination/auction/adapter.py`
- `agent-verse-backend/app/coordination/camel/adapter.py`
- `agent-verse-backend/app/coordination/generative/adapter.py`
- `agent-verse-backend/app/coordination/group_chat/adapter.py`
- `agent-verse-backend/app/coordination/magentic/adapter.py`
- `agent-verse-backend/app/coordination/moa/adapter.py`
- `agent-verse-backend/app/coordination/patterns/__init__.py`

## Audit Trail

- EXTRACTED: 344 (84%)
- INFERRED: 64 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*