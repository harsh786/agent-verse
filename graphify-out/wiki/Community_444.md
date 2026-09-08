# Community 444

> 14 nodes · cohesion 0.19

## Key Concepts

- **TimePolicyEngine (fail-closed overnight/weekend rules)** (9 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **time_policy.py** (6 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **.check_tool()** (4 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **datetime** (3 connections)
- **.add_blackout()** (3 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **TimeRule** (3 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **._tool_matches_patterns()** (2 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **DataClassifier.classify_or_safe_fallback (fail-closed classification)** (1 connections) — `agent-verse-backend/app/data_classification/classifier.py`
- **Time-Based Governance Rules ============================== Prevents destructive…** (1 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **Check if a tool call is allowed at the current time. Returns (allowed: bool,…** (1 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **A time-based governance rule.** (1 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **Evaluates time-based governance rules for tool calls.** (1 connections) — `agent-verse-backend/app/governance/time_policy.py`
- **Add a blackout window (no operations allowed).** (1 connections) — `agent-verse-backend/app/governance/time_policy.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/data_classification/classifier.py`
- `agent-verse-backend/app/governance/time_policy.py`

## Audit Trail

- EXTRACTED: 18 (86%)
- INFERRED: 3 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*