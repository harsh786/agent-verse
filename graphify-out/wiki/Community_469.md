# Community 469

> 13 nodes · cohesion 0.23

## Key Concepts

- **OrgEventPublisher** (11 connections) — `agent-verse-backend/app/org/events.py`
- **.publish()** (8 connections) — `agent-verse-backend/app/org/events.py`
- **OrgAuditRecord** (5 connections) — `agent-verse-backend/app/org/events.py`
- **Any** (5 connections)
- **._write_audit()** (4 connections) — `agent-verse-backend/app/org/events.py`
- **.to_dict()** (2 connections) — `agent-verse-backend/app/org/events.py`
- **._event_body()** (2 connections) — `agent-verse-backend/app/org/events.py`
- **._event_title()** (2 connections) — `agent-verse-backend/app/org/events.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/org/events.py`
- **Full audit record per spec PART 21. Written to audit trail on every org event.** (1 connections) — `agent-verse-backend/app/org/events.py`
- **Publishes org events to: 1. Existing audit trail (async DB write) 2. Redis…** (1 connections) — `agent-verse-backend/app/org/events.py`
- **Publish an org event to all downstream consumers. All 30+ event types are…** (1 connections) — `agent-verse-backend/app/org/events.py`
- **Write to audit trail (delegates to existing audit service).** (1 connections) — `agent-verse-backend/app/org/events.py`

## Relationships

- [Community 487](Community_487.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 285](Community_285.md) (2 shared connections)
- [Community 160](Community_160.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/org/events.py`

## Audit Trail

- EXTRACTED: 25 (93%)
- INFERRED: 2 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*