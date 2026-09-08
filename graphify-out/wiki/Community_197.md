# Community 197

> 30 nodes · cohesion 0.09

## Key Concepts

- **Goal** (13 connections) — `agent-verse-backend/app/db/models/goal.py`
- **event_store.py** (10 connections) — `agent-verse-backend/app/services/event_store.py`
- **goal.py** (8 connections) — `agent-verse-backend/app/db/models/goal.py`
- **GoalCheckpoint** (7 connections) — `agent-verse-backend/app/db/models/goal.py`
- **AuditLog** (7 connections) — `agent-verse-backend/app/db/models/governance.py`
- **services/persistence.py** (7 connections) — `agent-verse-backend/app/services/persistence.py`
- **GoalStep** (6 connections) — `agent-verse-backend/app/db/models/goal.py`
- **models/governance.py** (6 connections) — `agent-verse-backend/app/db/models/governance.py`
- **GoalEvent** (5 connections) — `agent-verse-backend/app/db/models/goal.py`
- **ApprovalRequest** (5 connections) — `agent-verse-backend/app/db/models/governance.py`
- **PolicyVersion** (5 connections) — `agent-verse-backend/app/db/models/governance.py`
- **Base** (4 connections)
- **._db_persist_step()** (4 connections) — `agent-verse-backend/app/services/goal_service.py`
- **persist_audit_event()** (4 connections) — `agent-verse-backend/app/services/persistence.py`
- **persist_goal()** (4 connections) — `agent-verse-backend/app/services/persistence.py`
- **persist_goal_status()** (4 connections) — `agent-verse-backend/app/services/persistence.py`
- **Base** (3 connections)
- **Any** (3 connections)
- **SQLAlchemy ORM models for goals and goal steps.** (1 connections) — `agent-verse-backend/app/db/models/goal.py`
- **Durable checkpoint payloads for future worker resume support.** (1 connections) — `agent-verse-backend/app/db/models/goal.py`
- **SQLAlchemy ORM models for governance: audit log, approval requests, policy…** (1 connections) — `agent-verse-backend/app/db/models/governance.py`
- **Append-only audit trail — immutability enforced by DB trigger.** (1 connections) — `agent-verse-backend/app/db/models/governance.py`
- **Human-in-the-loop approval gate for high-risk agent actions.** (1 connections) — `agent-verse-backend/app/db/models/governance.py`
- **Immutable snapshot of a policy at a given version number (migration 0056).** (1 connections) — `agent-verse-backend/app/db/models/governance.py`
- **Durable goal event storage.** (1 connections) — `agent-verse-backend/app/services/event_store.py`
- *... and 5 more nodes in this community*

## Relationships

- [Chat DB Models](Chat_DB_Models.md) (10 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (7 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (7 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Community 72](Community_72.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)
- [Community 49](Community_49.md) (1 shared connections)
- [Community 488](Community_488.md) (1 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/db/models/goal.py`
- `agent-verse-backend/app/db/models/governance.py`
- `agent-verse-backend/app/services/event_store.py`
- `agent-verse-backend/app/services/goal_service.py`
- `agent-verse-backend/app/services/persistence.py`

## Audit Trail

- EXTRACTED: 70 (91%)
- INFERRED: 7 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*