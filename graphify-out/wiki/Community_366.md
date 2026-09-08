# Community 366

> 17 nodes · cohesion 0.19

## Key Concepts

- **agent/goal_tree.py** (16 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **execute_goal_tree (DAG waves)** (14 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **SubGoal (goal-tree decomposition unit)** (12 connections) — `agent-verse-backend/app/agent/state.py`
- **decompose_goal** (10 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **execute_sub_goal (spawned agent)** (10 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **_synthesize_goal_tree_results** (7 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **DecompositionResult** (3 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **Any** (3 connections)
- **Semaphore** (1 connections)
- **Goal-tree decomposition and parallel sub-agent execution. The GoalTreeExecutor:…** (1 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **Synthesize sub-goal results into a coherent final answer using LLM. Falls back…** (1 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **Decompose goal → build dependency DAG → execute with parallelism. Returns list…** (1 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **Ask the planner LLM whether to decompose and how.** (1 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **Execute a single sub-goal using a spawned AgentGraph instance.** (1 connections) — `agent-verse-backend/app/agent/goal_tree.py`
- **GOAL_TREE_SYSTEM prompt** (1 connections) — `agent-verse-backend/app/agent/prompts.py`
- **SYNTHESIS_SYSTEM prompt** (1 connections) — `agent-verse-backend/app/agent/prompts.py`
- **A decomposed sub-goal produced by the goal-tree planner.** (1 connections) — `agent-verse-backend/app/agent/state.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (14 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (10 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (4 shared connections)
- [Community 151](Community_151.md) (2 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (1 shared connections)
- [Community 203](Community_203.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/goal_tree.py`
- `agent-verse-backend/app/agent/prompts.py`
- `agent-verse-backend/app/agent/state.py`

## Audit Trail

- EXTRACTED: 47 (81%)
- INFERRED: 11 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*