# Community 280

> 23 nodes · cohesion 0.14

## Key Concepts

- **Any** (29 connections)
- **get** (14 connections)
- **_get_db()** (11 connections) — `agent-verse-backend/app/api/governance.py`
- **email_approve_link()** (8 connections) — `agent-verse-backend/app/api/governance.py`
- **email_reject_link()** (8 connections) — `agent-verse-backend/app/api/governance.py`
- **verify_audit_chain()** (8 connections) — `agent-verse-backend/app/api/governance.py`
- **get_policy_versions()** (7 connections) — `agent-verse-backend/app/api/governance.py`
- **get_sla_stats()** (7 connections) — `agent-verse-backend/app/api/governance.py`
- **list_approval_history()** (7 connections) — `agent-verse-backend/app/api/governance.py`
- **list_legal_holds()** (7 connections) — `agent-verse-backend/app/api/governance.py`
- **list_policies()** (7 connections) — `agent-verse-backend/app/api/governance.py`
- **_db_list_policies()** (6 connections) — `agent-verse-backend/app/api/governance.py`
- **list_notification_channels()** (5 connections) — `agent-verse-backend/app/api/governance.py`
- **_pending_snapshot()** (4 connections) — `agent-verse-backend/app/api/governance.py`
- **_tail_redis_channel()** (3 connections) — `agent-verse-backend/app/api/governance.py`
- **List active legal holds for this tenant (empty when DB unavailable).** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Return the full version history for a policy.** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Verify the cryptographic hash chain of audit events.** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Return resolved approval requests from the DB (approved / rejected / timed_out).** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Return SLA compliance stats for HITL approvals.** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Yield SSE frames from a Redis pub/sub channel. Closes cleanly on cancel.** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Handle one-click approve link from HITL approval email. Validates HMAC…** (1 connections) — `agent-verse-backend/app/api/governance.py`
- **Handle one-click reject link from HITL approval email. Validates HMAC signature…** (1 connections) — `agent-verse-backend/app/api/governance.py`

## Relationships

- [Community 72](Community_72.md) (53 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Community 636](Community_636.md) (3 shared connections)
- [Community 610](Community_610.md) (2 shared connections)
- [Community 57](Community_57.md) (2 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Community 247](Community_247.md) (1 shared connections)
- [Community 96](Community_96.md) (1 shared connections)
- [Community 404](Community_404.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/governance.py`

## Audit Trail

- EXTRACTED: 101 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*