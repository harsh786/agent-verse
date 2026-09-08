# Step Execution & Semantic Cache

> 77 nodes · cohesion 0.04

## Key Concepts

- **._execute_step()** (38 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **observability/metrics.py** (35 connections) — `agent-verse-backend/app/observability/metrics.py`
- **cost.py** (15 connections) — `agent-verse-backend/app/governance/cost.py`
- **record_cost_usd()** (11 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_goal_duration()** (11 connections) — `agent-verse-backend/app/observability/metrics.py`
- **_normalize_status_label()** (9 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_llm_tokens()** (9 connections) — `agent-verse-backend/app/observability/metrics.py`
- **._node_execute()** (8 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **_non_negative()** (8 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_goal_completed()** (8 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_goal_failed()** (8 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_tool_call()** (8 connections) — `agent-verse-backend/app/observability/metrics.py`
- **.complete()** (8 connections) — `agent-verse-backend/app/providers/anthropic_provider.py`
- **.complete()** (8 connections) — `agent-verse-backend/app/providers/openai_compatible.py`
- **._execute_step_with_cache()** (7 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **._execute_step_with_loop()** (7 connections) — `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- **estimate_cost (deprecated per-1k pricing table)** (7 connections) — `agent-verse-backend/app/governance/pricing.py`
- **record_goal_started()** (7 connections) — `agent-verse-backend/app/observability/metrics.py`
- **SearchDirectiveParser** (7 connections) — `agent-verse-backend/app/rag/agentic/search_directive_parser.py`
- **_normalize_exact_label()** (6 connections) — `agent-verse-backend/app/observability/metrics.py`
- **_normalize_priority_label()** (6 connections) — `agent-verse-backend/app/observability/metrics.py`
- **record_queue_depth()** (6 connections) — `agent-verse-backend/app/observability/metrics.py`
- **goal_metrics.py** (6 connections) — `agent-verse-backend/app/services/goal_metrics.py`
- **spawn_tool.py** (5 connections) — `agent-verse-backend/app/civilization/spawn_tool.py`
- **execute_spawn_tool()** (5 connections) — `agent-verse-backend/app/civilization/spawn_tool.py`
- *... and 52 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (40 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (18 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (9 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (6 shared connections)
- [Community 49](Community_49.md) (5 shared connections)
- [Community 169](Community_169.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (4 shared connections)
- [Community 224](Community_224.md) (4 shared connections)
- [Community 170](Community_170.md) (3 shared connections)
- [Community 65](Community_65.md) (3 shared connections)
- [Community 100](Community_100.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/agent/nodes/executor_mixin.py`
- `agent-verse-backend/app/civilization/spawn_tool.py`
- `agent-verse-backend/app/context/tool_prompt_builder.py`
- `agent-verse-backend/app/governance/cost.py`
- `agent-verse-backend/app/governance/pricing.py`
- `agent-verse-backend/app/intelligence/explainability.py`
- `agent-verse-backend/app/observability/metrics.py`
- `agent-verse-backend/app/providers/anthropic_provider.py`
- `agent-verse-backend/app/providers/openai_compatible.py`
- `agent-verse-backend/app/rag/agentic/search_directive_parser.py`
- `agent-verse-backend/app/services/goal_metrics.py`

## Audit Trail

- EXTRACTED: 223 (97%)
- INFERRED: 7 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*