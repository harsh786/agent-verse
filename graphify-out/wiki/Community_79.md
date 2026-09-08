# Community 79

> 52 nodes · cohesion 0.06

## Key Concepts

- **dispatcher.py** (22 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **TriggerDispatcher** (20 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **.dispatch()** (17 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **derive_idempotency_key()** (6 connections) — `agent-verse-backend/app/triggers/dedup.py`
- **TriggerQuotaEnforcer** (6 connections) — `agent-verse-backend/app/triggers/quota.py`
- **triggers.dedup (idempotency check)** (5 connections) — `agent-verse-backend/app/triggers/dedup.py`
- **.__init__()** (5 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **._skip_event()** (5 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **triggers/events.py** (5 connections) — `agent-verse-backend/app/triggers/events.py`
- **triggers.quota** (5 connections) — `agent-verse-backend/app/triggers/quota.py`
- **triggers.rbac** (5 connections) — `agent-verse-backend/app/triggers/rbac.py`
- **check_permission()** (5 connections) — `agent-verse-backend/app/triggers/rbac.py`
- **._make_skip_event()** (4 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **dlq.py** (4 connections) — `agent-verse-backend/app/triggers/dlq.py`
- **write_to_dlq()** (4 connections) — `agent-verse-backend/app/triggers/dlq.py`
- **SimulatedTriggerResult** (4 connections) — `agent-verse-backend/app/triggers/events.py`
- **TriggerEvent** (4 connections)
- **_payload_hash()** (3 connections) — `agent-verse-backend/app/triggers/dedup.py`
- **._create_goal()** (3 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **._evaluate_condition()** (3 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **._persist_event()** (3 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **._render_template()** (3 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **._write_dlq()** (3 connections) — `agent-verse-backend/app/triggers/dispatcher.py`
- **TriggerEvent** (3 connections) — `agent-verse-backend/app/triggers/events.py`
- **.check_create()** (3 connections) — `agent-verse-backend/app/triggers/quota.py`
- *... and 27 more nodes in this community*

## Relationships

- [Community 472](Community_472.md) (4 shared connections)
- [Community 557](Community_557.md) (4 shared connections)
- [Community 471](Community_471.md) (4 shared connections)
- [Community 210](Community_210.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 454](Community_454.md) (1 shared connections)
- [Community 130](Community_130.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/dedup.py`
- `agent-verse-backend/app/triggers/dispatcher.py`
- `agent-verse-backend/app/triggers/dlq.py`
- `agent-verse-backend/app/triggers/events.py`
- `agent-verse-backend/app/triggers/quota.py`
- `agent-verse-backend/app/triggers/rbac.py`

## Audit Trail

- EXTRACTED: 93 (91%)
- INFERRED: 3 (3%)
- AMBIGUOUS: 6 (6%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*