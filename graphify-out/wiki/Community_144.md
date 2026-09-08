# Community 144

> 36 nodes · cohesion 0.08

## Key Concepts

- **SkillSelector** (15 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **ToolSelector** (15 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **to_tiered_prompt (3-tier tool rendering)** (7 connections) — `agent-verse-backend/app/agent/tool_context.py`
- **tool_selector.py** (7 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **.select()** (7 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **skill_selector.py** (6 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **SelectedSkill** (6 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **ToolSelection (3-tier)** (6 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **Any** (5 connections)
- **._boost_by_reliability()** (5 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **_render_signature()** (4 connections) — `agent-verse-backend/app/agent/tool_context.py`
- **_needs_rpa (RPA capability gate)** (4 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **._score_by_capability()** (4 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **.build_skills_context()** (3 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **.select()** (3 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **AgentRouter._score_by_history_db** (2 connections) — `agent-verse-backend/app/agent/router.py`
- **SkillSelector.build_skills_context (prompt block)** (2 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **ToolContext.to_prompt_block (planner prompt tools)** (2 connections) — `agent-verse-backend/app/agent/tool_context.py`
- **ToolSelector._boost_by_reliability** (2 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **.all_tools()** (2 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/agent/tool_selector.py`
- **Any** (1 connections)
- **Skill Selector ============== Selects the top 1-3 most relevant skills for a…** (1 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- **Selects relevant skills for a goal using keyword matching on trigger_hints.…** (1 connections) — `agent-verse-backend/app/agent/skill_selector.py`
- *... and 11 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Community 61](Community_61.md) (5 shared connections)
- [Community 294](Community_294.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 398](Community_398.md) (2 shared connections)
- [Community 164](Community_164.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 172](Community_172.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/router.py`
- `agent-verse-backend/app/agent/skill_selector.py`
- `agent-verse-backend/app/agent/tool_context.py`
- `agent-verse-backend/app/agent/tool_selector.py`

## Audit Trail

- EXTRACTED: 67 (89%)
- INFERRED: 8 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*