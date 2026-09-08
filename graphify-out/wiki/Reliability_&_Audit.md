# Reliability & Audit

> 119 nodes · cohesion 0.02

## Key Concepts

- **FakeProvider** (34 connections) — `agent-verse-backend/app/providers/fake.py`
- **GuardrailChecker** (28 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- **RollbackEngine** (27 connections) — `agent-verse-backend/app/reliability/rollback.py`
- **DeduplicationCache** (23 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **ResultProcessor** (20 connections) — `agent-verse-backend/app/reliability/result_processor.py`
- **.__init__()** (18 connections) — `agent-verse-backend/app/agent/graph.py`
- **AgentTestHarness** (16 connections) — `agent-verse-backend/app/testing/harness.py`
- **intelligence/guardrails.py** (14 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- **harness.py** (14 connections) — `agent-verse-backend/app/testing/harness.py`
- **.run_goal()** (11 connections) — `agent-verse-backend/app/testing/harness.py`
- **red_team.py** (10 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **_make_agent_loop()** (10 connections) — `agent-verse-backend/app/services/goal_service.py`
- **RedTeamReport** (8 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **RedTeamRunner** (7 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **TestResult** (7 connections) — `agent-verse-backend/app/testing/harness.py`
- **.check_goal()** (6 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- **context_disclosure.py** (5 connections) — `agent-verse-backend/app/coordination/context_disclosure.py`
- **disclose_context** (5 connections) — `agent-verse-backend/app/coordination/context_disclosure.py`
- **BehavioralRedTeamRunner** (4 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **._analyze_events()** (4 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **.run_behavioral()** (4 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **.run()** (4 connections) — `agent-verse-backend/app/enterprise/red_team.py`
- **_detect_homoglyph_injection()** (4 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- **.check()** (4 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- **.check_tool_call()** (4 connections) — `agent-verse-backend/app/intelligence/guardrails.py`
- *... and 94 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (24 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (23 shared connections)
- [Community 49](Community_49.md) (9 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (8 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (6 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (5 shared connections)
- [Community 102](Community_102.md) (5 shared connections)
- [Community 129](Community_129.md) (3 shared connections)
- [Group Chat Coordination](Group_Chat_Coordination.md) (3 shared connections)
- [Community 250](Community_250.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 140](Community_140.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/agent/graph.py`
- `agent-verse-backend/app/coordination/context_disclosure.py`
- `agent-verse-backend/app/enterprise/red_team.py`
- `agent-verse-backend/app/intelligence/domain_policies.py`
- `agent-verse-backend/app/intelligence/encoding_attacks.py`
- `agent-verse-backend/app/intelligence/guardrails.py`
- `agent-verse-backend/app/intelligence/indirect_injection.py`
- `agent-verse-backend/app/providers/fake.py`
- `agent-verse-backend/app/reliability/dedup.py`
- `agent-verse-backend/app/reliability/result_processor.py`
- `agent-verse-backend/app/reliability/rollback.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/testing/harness.py`

## Audit Trail

- EXTRACTED: 245 (90%)
- INFERRED: 27 (10%)
- AMBIGUOUS: 1 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*