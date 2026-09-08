# Community 641

> 7 nodes · cohesion 0.29

## Key Concepts

- **AuditFlusher** (9 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.flush()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.run()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Compat shim: v3 has no WAL to flush — records are written directly.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Long-running no-op so the background task doesn't crash.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **No-op — v3 does not buffer in Redis WAL.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 286](Community_286.md) (1 shared connections)
- [Community 404](Community_404.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit_v3.py`

## Audit Trail

- EXTRACTED: 11 (92%)
- INFERRED: 1 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*