# Community 410

> 16 nodes · cohesion 0.16

## Key Concepts

- **tool_inverses.py** (11 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **get_inverse_fn** (8 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Any** (6 connections)
- **_inverse_confluence_create_page()** (4 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **_inverse_github_create_issue()** (4 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **_inverse_jira_create_issue()** (4 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **_inverse_slack_send_message()** (4 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **.rollback_all_async()** (3 connections) — `agent-verse-backend/app/reliability/rollback.py`
- **Execute all inverse operations in LIFO order, awaiting each one. Two modes:…** (1 connections) — `agent-verse-backend/app/reliability/rollback.py`
- **Tool inverse registry — maps tool names to their async undo functions. Each…** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Delete a Jira issue that was created by the forward tool call.** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Delete a Confluence page that was created by the forward tool call.** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Delete a Slack message that was sent by the forward tool call.** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Close/delete a GitHub issue that was created by the forward tool call.** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **Return a callable that undoes the named tool call. Two modes depending on…** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`
- **register_inverse()** (1 connections) — `agent-verse-backend/app/reliability/tool_inverses.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (5 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Community 57](Community_57.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/rollback.py`
- `agent-verse-backend/app/reliability/tool_inverses.py`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*