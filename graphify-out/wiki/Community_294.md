# Community 294

> 22 nodes · cohesion 0.13

## Key Concepts

- **workflow_nodes.py** (17 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **execute_decision_node** (12 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **execute_rag_node** (9 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **WorkflowExecutor._execute_step (node-type dispatch)** (8 connections) — `agent-verse-backend/app/agent/workflow_executor.py`
- **execute_skill_node** (8 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **execute_loop_node** (7 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **WorkflowExecutor.execute (parallel DAG waves)** (6 connections) — `agent-verse-backend/app/agent/workflow_executor.py`
- **execute_delay_node** (6 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **WorkflowExecutor._run_step (connector/HITL path)** (5 connections) — `agent-verse-backend/app/agent/workflow_executor.py`
- **Any** (5 connections)
- **PLATFORM_SKILLS registry** (2 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **Bounded cancellable wave execution pattern** (2 connections) — `agent-verse-backend/app/agent/structured_executor.py`
- **ToolContext.find_tool (fuzzy/alias lookup)** (2 connections) — `agent-verse-backend/app/agent/tool_context.py`
- **WorkflowExecutor._emit (sanitized event emission)** (2 connections) — `agent-verse-backend/app/agent/workflow_executor.py`
- **PlanVerifier.verify_pre_execution (permissions/budget/HITL gate)** (2 connections) — `agent-verse-backend/app/plan_runtime/plan_verifier.py`
- **WorkflowExecutor.run (legacy sequential API)** (1 connections) — `agent-verse-backend/app/agent/workflow_executor.py`
- **Workflow Node Executors ======================= Real execution semantics for…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **Execute a loop node. Returns list of per-iteration outputs. Node config:…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **Execute a delay node. Waits for the specified duration. Node config: seconds:…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **Execute a RAG retrieval node. Node config: collection_id: str — knowledge…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **Execute a skill node — injects skill instructions into workflow context. Node…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`
- **Execute a decision node. Returns the edge label to follow. Node config:…** (1 connections) — `agent-verse-backend/app/agent/workflow_nodes.py`

## Relationships

- [Community 61](Community_61.md) (13 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Community 144](Community_144.md) (3 shared connections)
- [Community 150](Community_150.md) (2 shared connections)
- [Community 169](Community_169.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 368](Community_368.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Federated RAG Search](Federated_RAG_Search.md) (1 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/skill_selector.py`
- `agent-verse-backend/app/agent/structured_executor.py`
- `agent-verse-backend/app/agent/tool_context.py`
- `agent-verse-backend/app/agent/workflow_executor.py`
- `agent-verse-backend/app/agent/workflow_nodes.py`
- `agent-verse-backend/app/plan_runtime/plan_verifier.py`

## Audit Trail

- EXTRACTED: 62 (90%)
- INFERRED: 6 (9%)
- AMBIGUOUS: 1 (1%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*