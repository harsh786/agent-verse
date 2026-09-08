# Community 258

> 25 nodes · cohesion 0.13

## Key Concepts

- **NotificationService** (17 connections) — `agent-verse-backend/app/services/notification_service.py`
- **NotificationChannel** (8 connections) — `agent-verse-backend/app/services/notification_service.py`
- **notification_service.py** (7 connections) — `agent-verse-backend/app/services/notification_service.py`
- **._send()** (6 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.get_channels()** (5 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.notify_approval_required()** (5 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.notify_approval_timeout()** (5 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.notify_goal_complete()** (4 connections) — `agent-verse-backend/app/services/notification_service.py`
- **._persist_channel()** (4 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Any** (4 connections)
- **.add_channel()** (3 connections) — `agent-verse-backend/app/services/notification_service.py`
- **._delete_channel()** (3 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.set_db()** (3 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.sync_from_db()** (3 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.remove_channel()** (2 connections) — `agent-verse-backend/app/services/notification_service.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Notification service — sends alerts when HITL approval is required. Supports…** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Remove a channel from the DB (fire-and-forget).** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Send notification to all tenant channels.** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **G-12: Notify when an approval request has timed out. Called from…** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Notify when a goal reaches a terminal state.** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Dispatches notifications when approval is required or goals complete. Open…** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Wire in async SQLAlchemy session factory (called during lifespan).** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Load persisted channels from DB into the in-memory cache.** (1 connections) — `agent-verse-backend/app/services/notification_service.py`
- **Persist a channel to the DB (fire-and-forget).** (1 connections) — `agent-verse-backend/app/services/notification_service.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Community 72](Community_72.md) (2 shared connections)
- [Community 136](Community_136.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/services/notification_service.py`

## Audit Trail

- EXTRACTED: 47 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*