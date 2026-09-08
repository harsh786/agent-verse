# Community 210

> 29 nodes · cohesion 0.10

## Key Concepts

- **nl_scheduler.py** (13 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **triggers/store.py** (13 connections) — `agent-verse-backend/app/triggers/store.py`
- **TriggerSpec** (12 connections) — `agent-verse-backend/app/triggers/models.py`
- **TriggerType** (12 connections) — `agent-verse-backend/app/triggers/models.py`
- **triggers/models.py** (11 connections) — `agent-verse-backend/app/triggers/models.py`
- **NLScheduler** (9 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **triggers.simulation** (7 connections) — `agent-verse-backend/app/triggers/simulation.py`
- **_keyword_route() fallback** (6 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **.parse()** (6 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **TriggerChaosHarness** (6 connections) — `agent-verse-backend/app/triggers/simulation.py`
- **_parse_single()** (5 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **TriggerSpec** (3 connections)
- **validate_cron()** (2 connections) — `agent-verse-backend/app/triggers/models.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **Trigger type definitions — 58 trigger types across 9 families. Families: A.…** (1 connections) — `agent-verse-backend/app/triggers/models.py`
- **Complete configuration for any of the 58 trigger types.** (1 connections) — `agent-verse-backend/app/triggers/models.py`
- **Validate a cron expression and check plan-tier minimum interval. Raises…** (1 connections) — `agent-verse-backend/app/triggers/models.py`
- **NLScheduler.parse()** (1 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **NL Scheduler — parses NL schedule/trigger descriptions into TriggerSpecs.…** (1 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **Fast path: return TriggerSpec if a keyword rule matches.** (1 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **Converts NL trigger descriptions to TriggerSpecs. Primary path: LLM provider…** (1 connections) — `agent-verse-backend/app/triggers/nl_scheduler.py`
- **ChaosStats** (1 connections) — `agent-verse-backend/app/triggers/simulation.py`
- **Trigger simulation mode and chaos testing harness.** (1 connections) — `agent-verse-backend/app/triggers/simulation.py`
- **Injects controlled failures for testing the dispatch pipeline.** (1 connections) — `agent-verse-backend/app/triggers/simulation.py`
- **.__enter__()** (1 connections) — `agent-verse-backend/app/triggers/simulation.py`
- *... and 4 more nodes in this community*

## Relationships

- [Community 112](Community_112.md) (8 shared connections)
- [Community 83](Community_83.md) (7 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (7 shared connections)
- [Community 156](Community_156.md) (5 shared connections)
- [Agent Store API](Agent_Store_API.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (4 shared connections)
- [Community 79](Community_79.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/models.py`
- `agent-verse-backend/app/triggers/nl_scheduler.py`
- `agent-verse-backend/app/triggers/simulation.py`
- `agent-verse-backend/app/triggers/store.py`

## Audit Trail

- EXTRACTED: 79 (96%)
- INFERRED: 3 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*