# Community 284

> 23 nodes · cohesion 0.15

## Key Concepts

- **decision_store.py** (12 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **PostgresDecisionStore** (11 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **InMemoryDecisionStore** (9 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **OptimizationOutcome** (7 connections) — `agent-verse-backend/app/routing_runtime/contracts.py`
- **routing.py** (6 connections) — `agent-verse-backend/app/db/models/routing.py`
- **RoutingDecisionRow** (5 connections) — `agent-verse-backend/app/db/models/routing.py`
- **RoutingOutcomeRow** (5 connections) — `agent-verse-backend/app/db/models/routing.py`
- **_decision()** (5 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **RoutingDecision** (5 connections)
- **.get_decision()** (4 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **.save_decision()** (4 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **.save_outcome()** (3 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **Base** (2 connections)
- **.get_decision()** (2 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **.save_decision()** (2 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **.save_outcome()** (2 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **ORM rows for canonical routing decisions and outcomes.** (1 connections) — `agent-verse-backend/app/db/models/routing.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`
- **Any** (1 connections)
- **async_sessionmaker** (1 connections)
- **AsyncSession** (1 connections)
- **Idempotent tenant-scoped routing decision and outcome persistence.** (1 connections) — `agent-verse-backend/app/routing_runtime/decision_store.py`

## Relationships

- [Community 98](Community_98.md) (5 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (5 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (4 shared connections)
- [Community 585](Community_585.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/db/models/routing.py`
- `agent-verse-backend/app/routing_runtime/contracts.py`
- `agent-verse-backend/app/routing_runtime/decision_store.py`

## Audit Trail

- EXTRACTED: 52 (93%)
- INFERRED: 4 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*