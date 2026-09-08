# Community 322

> 20 nodes · cohesion 0.19

## Key Concepts

- **_WorkflowStore** (18 connections) — `agent-verse-backend/app/api/workflows.py`
- **Any** (14 connections)
- **Workflow** (7 connections) — `agent-verse-backend/app/db/models/workflow.py`
- **_orm_to_dict()** (6 connections) — `agent-verse-backend/app/api/workflows.py`
- **._create_db()** (5 connections) — `agent-verse-backend/app/api/workflows.py`
- **._get_db()** (4 connections) — `agent-verse-backend/app/api/workflows.py`
- **._list_db()** (4 connections) — `agent-verse-backend/app/api/workflows.py`
- **._update_db()** (4 connections) — `agent-verse-backend/app/api/workflows.py`
- **.create()** (3 connections) — `agent-verse-backend/app/api/workflows.py`
- **.get()** (3 connections) — `agent-verse-backend/app/api/workflows.py`
- **.list()** (3 connections) — `agent-verse-backend/app/api/workflows.py`
- **.set_db()** (3 connections) — `agent-verse-backend/app/api/workflows.py`
- **.update()** (3 connections) — `agent-verse-backend/app/api/workflows.py`
- **.delete()** (2 connections) — `agent-verse-backend/app/api/workflows.py`
- **._delete_db()** (2 connections) — `agent-verse-backend/app/api/workflows.py`
- **Workflow persistence store. Uses an in-memory dict when no DB session factory…** (1 connections) — `agent-verse-backend/app/api/workflows.py`
- **Wire in the async SQLAlchemy session factory (called during lifespan).** (1 connections) — `agent-verse-backend/app/api/workflows.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/api/workflows.py`
- **Base** (1 connections)
- **Persisted visual workflow definition (nodes + edges from the workflow builder).…** (1 connections) — `agent-verse-backend/app/db/models/workflow.py`

## Relationships

- [Community 195](Community_195.md) (8 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/api/workflows.py`
- `agent-verse-backend/app/db/models/workflow.py`

## Audit Trail

- EXTRACTED: 48 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*