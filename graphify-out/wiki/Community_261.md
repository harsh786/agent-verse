# Community 261

> 24 nodes · cohesion 0.13

## Key Concepts

- **ComplianceController** (16 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **compliance.py** (7 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.request_data_export()** (6 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **Any** (6 connections)
- **.get_export_payload()** (5 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.get_export_status()** (5 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.request_data_deletion()** (5 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **DataExportRequest** (5 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.execute_data_deletion_async()** (4 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.configure_services()** (3 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **._db_load_request()** (3 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **._db_save_request()** (3 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.get_data_residency()** (3 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.retention_sweep()** (3 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **._db_save_deletion()** (2 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **GDPR/SOC2/PCI-DSS compliance controls. Provides: - GDPR right-to-erasure:…** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **GDPR right-of-access — collect and return all tenant data.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **GDPR right-to-erasure. Records intent and schedules DB deletion in 30 days.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **Sweep and mark records older than retention_days for deletion.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **Return the raw export payload dict for a ready export request.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **Execute GDPR erasure — actual DB deletion. Called 30 days after request.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **GDPR/SOC2/PCI-DSS compliance controller. Export requests and deletion records…** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`
- **Inject service references for comprehensive data export.** (1 connections) — `agent-verse-backend/app/enterprise/compliance.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (7 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/enterprise/compliance.py`

## Audit Trail

- EXTRACTED: 48 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*