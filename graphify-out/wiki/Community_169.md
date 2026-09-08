# Community 169

> 32 nodes · cohesion 0.09

## Key Concepts

- **tool_calls.py** (15 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **classify_tool_risk** (12 connections) — `agent-verse-backend/app/agent/tool_risk.py`
- **repair_tool_call_arguments (argument self-repair)** (11 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **requires_consensus** (7 connections) — `agent-verse-backend/app/agent/consensus.py`
- **extract_tool_call (ReAct tool-call parser)** (7 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **ToolCall** (7 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **validate_tool_name (hallucinated-tool guard)** (6 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_canonical_tool_name (Jira alias canonicalization)** (5 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_resolve_jira_account_id (cached identity resolution)** (4 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_resolve_jira_display_names_in_jql()** (4 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **validate_tool_arguments (JSON-Schema check)** (4 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **tool_risk.py** (4 connections) — `agent-verse-backend/app/agent/tool_risk.py`
- **_jql_from_goal_or_step()** (3 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_try_parse_json()** (3 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_name_tokens (camel/snake tokenizer)** (3 connections) — `agent-verse-backend/app/agent/tool_risk.py`
- **ToolRisk (read|write_low|write_high|destructive)** (3 connections) — `agent-verse-backend/app/agent/tool_risk.py`
- **_looks_like_placeholder_jql()** (2 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_named_assignee_from_text()** (2 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **_tool_name_key()** (2 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **Return True when this goal warrants 3-way consensus verification. Two calling…** (1 connections) — `agent-verse-backend/app/agent/consensus.py`
- **Structured tool-call parsing for executor output.** (1 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **Attempt to parse JSON with several repair strategies.** (1 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **Look up a Jira user account ID by display name (async, non-blocking). Calls GET…** (1 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **Replace display-name strings in JQL assignee clauses with account IDs (async).…** (1 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- **Fill obvious missing arguments from the planner step text.** (1 connections) — `agent-verse-backend/app/agent/tool_calls.py`
- *... and 7 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (12 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (4 shared connections)
- [Community 215](Community_215.md) (3 shared connections)
- [Community 61](Community_61.md) (3 shared connections)
- [Community 294](Community_294.md) (2 shared connections)
- [Community 277](Community_277.md) (1 shared connections)
- [Community 292](Community_292.md) (1 shared connections)
- [Community 105](Community_105.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/consensus.py`
- `agent-verse-backend/app/agent/tool_calls.py`
- `agent-verse-backend/app/agent/tool_risk.py`

## Audit Trail

- EXTRACTED: 60 (83%)
- INFERRED: 11 (15%)
- AMBIGUOUS: 1 (1%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*