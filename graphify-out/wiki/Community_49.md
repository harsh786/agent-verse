# Community 49

> 71 nodes · cohesion 0.04

## Key Concepts

- **steps.py** (37 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **AuditLog (append-only, DB-backed)** (22 connections) — `agent-verse-backend/app/governance/audit.py`
- **ActionLevel enum (allow/allow_log/approval/deny)** (21 connections) — `agent-verse-backend/app/governance/permissions.py`
- **AuditEvent** (20 connections) — `agent-verse-backend/app/governance/audit.py`
- **CostController (in-memory budget enforcement)** (20 connections) — `agent-verse-backend/app/governance/cost.py`
- **audit.py** (17 connections) — `agent-verse-backend/app/governance/audit.py`
- **PermissionMatrix** (14 connections) — `agent-verse-backend/app/governance/permissions.py`
- **permissions.py** (12 connections) — `agent-verse-backend/app/governance/permissions.py`
- **.query_db()** (7 connections) — `agent-verse-backend/app/governance/audit.py`
- **record_usage()** (7 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **smart_context_fetch()** (7 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **.check_and_record()** (6 connections) — `agent-verse-backend/app/governance/cost.py`
- **.check()** (6 connections) — `agent-verse-backend/app/governance/permissions.py`
- **.check_with_limits()** (6 connections) — `agent-verse-backend/app/governance/permissions.py`
- **.record()** (5 connections) — `agent-verse-backend/app/governance/audit.py`
- **.get_rule()** (5 connections) — `agent-verse-backend/app/governance/permissions.py`
- **exec_memory_lookup()** (5 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **governance_check()** (5 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **result_processor_step()** (5 connections) — `agent-verse-backend/app/pipeline/steps.py`
- **._db_record()** (4 connections) — `agent-verse-backend/app/governance/audit.py`
- **.query()** (4 connections) — `agent-verse-backend/app/governance/audit.py`
- **.sync_from_db()** (4 connections) — `agent-verse-backend/app/governance/audit.py`
- **.get_tenant_cost_today()** (4 connections) — `agent-verse-backend/app/governance/cost.py`
- **._reset_if_new_day()** (4 connections) — `agent-verse-backend/app/governance/cost.py`
- **PermissionRule** (4 connections) — `agent-verse-backend/app/governance/permissions.py`
- *... and 46 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (29 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (29 shared connections)
- [Community 72](Community_72.md) (9 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (9 shared connections)
- [Community 110](Community_110.md) (6 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (5 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (5 shared connections)
- [Community 65](Community_65.md) (5 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Community 96](Community_96.md) (3 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit.py`
- `agent-verse-backend/app/governance/cost.py`
- `agent-verse-backend/app/governance/permissions.py`
- `agent-verse-backend/app/pipeline/steps.py`
- `agent-verse-backend/app/reliability/result_processor.py`

## Audit Trail

- EXTRACTED: 192 (86%)
- INFERRED: 31 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*