# Community 338

> 19 nodes · cohesion 0.11

## Key Concepts

- **.run()** (11 connections) — `agent-verse-backend/app/execution_environment/fake_runner.py`
- **evaluate_policy()** (9 connections) — `agent-verse-backend/app/execution_environment/policy.py`
- **.schedule()** (9 connections) — `agent-verse-backend/app/execution_environment/scheduler.py`
- **.from_flags()** (8 connections) — `agent-verse-backend/app/execution_environment/scheduler.py`
- **._build_loop()** (6 connections) — `agent-verse-backend/app/execution_environment/fake_runner.py`
- **make_forwarding_callback()** (5 connections) — `agent-verse-backend/app/execution_environment/events.py`
- **Any** (3 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/execution_environment/fake_runner.py`
- **PolicyDecision** (2 connections) — `agent-verse-backend/app/execution_environment/policy.py`
- **Any** (2 connections)
- **Return an async callback that wraps events and forwards them downstream. The…** (1 connections) — `agent-verse-backend/app/execution_environment/events.py`
- **ExecutionResult** (1 connections)
- **Return an agent runner for this envelope.** (1 connections) — `agent-verse-backend/app/execution_environment/fake_runner.py`
- **Execute the goal and return a structured result. Never raises — all errors…** (1 connections) — `agent-verse-backend/app/execution_environment/fake_runner.py`
- **Evaluate isolation-layer policy rules against the envelope.** (1 connections) — `agent-verse-backend/app/execution_environment/policy.py`
- **ExecutionResult** (1 connections)
- **Validate, route, and execute the envelope. Fail-closed: unhealthy runner,…** (1 connections) — `agent-verse-backend/app/execution_environment/scheduler.py`
- **Construct the scheduler with the appropriate runner from flags.** (1 connections) — `agent-verse-backend/app/execution_environment/scheduler.py`
- **GoalEventCallback** (1 connections)

## Relationships

- [Community 140](Community_140.md) (6 shared connections)
- [Community 84](Community_84.md) (4 shared connections)
- [Community 165](Community_165.md) (4 shared connections)
- [Community 423](Community_423.md) (3 shared connections)
- [Community 88](Community_88.md) (3 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 377](Community_377.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (1 shared connections)
- [Community 57](Community_57.md) (1 shared connections)
- [Community 424](Community_424.md) (1 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/execution_environment/events.py`
- `agent-verse-backend/app/execution_environment/fake_runner.py`
- `agent-verse-backend/app/execution_environment/policy.py`
- `agent-verse-backend/app/execution_environment/scheduler.py`

## Audit Trail

- EXTRACTED: 47 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*