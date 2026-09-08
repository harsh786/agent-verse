# Community 259

> 24 nodes · cohesion 0.11

## Key Concepts

- **Society** (20 connections) — `agent-verse-backend/app/civilization/society.py`
- **.load_members()** (5 connections) — `agent-verse-backend/app/civilization/society.py`
- **society.py** (4 connections) — `agent-verse-backend/app/civilization/society.py`
- **.route_goal()** (4 connections) — `agent-verse-backend/app/civilization/society.py`
- **.update_reputation()** (4 connections) — `agent-verse-backend/app/civilization/society.py`
- **.get_lineage_graph()** (3 connections) — `agent-verse-backend/app/civilization/society.py`
- **.get_metrics()** (3 connections) — `agent-verse-backend/app/civilization/society.py`
- **Any** (2 connections)
- **._get_current_reputation()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **.get_member()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **._persist_reputation()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **.update_budget_spent()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **.update_member_status()** (2 connections) — `agent-verse-backend/app/civilization/society.py`
- **Society — civilization membership, reputation tracking, goal routing.…** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Update reputation via EWMA. Returns new reputation value. EWMA formula: new_rep…** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Update member status (active/idle/debating/spawning/failed).** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Record additional spend for a member (for cost rollup).** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Manages civilization membership, reputation, and goal routing. The Society…** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Route an incoming goal to the best society member. Returns routing decision:…** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Get the spawn lineage graph for visualization.** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Get live society metrics.** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Load active members from DB into memory cache.** (1 connections) — `agent-verse-backend/app/civilization/society.py`
- **Get a single member by agent_id.** (1 connections) — `agent-verse-backend/app/civilization/society.py`

## Relationships

- [Community 67](Community_67.md) (5 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/civilization/society.py`

## Audit Trail

- EXTRACTED: 34 (89%)
- INFERRED: 4 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*