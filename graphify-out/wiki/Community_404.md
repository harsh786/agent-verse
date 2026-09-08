# Community 404

> 16 nodes · cohesion 0.15

## Key Concepts

- **Any** (10 connections)
- **AuditWriter** (6 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **compute_entry_hash()** (6 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **._verify_single_chain()** (5 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **HashChainVerifier** (5 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.verify_chain()** (4 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.write()** (3 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.verify()** (3 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Compute the hash for an audit entry — MUST be deterministic.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Verify the hash chain integrity for a tenant. Recomputes hashes and detects any…** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Internal: verify chain for a single tenant, always returns a dict.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Compat shim: v3 writes directly — no Redis WAL needed.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **No-op: v3 persists synchronously in AuditV3.append().** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Compat shim wrapping AuditV3.verify_chain() (in-memory) with a DB fallback that…** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`

## Relationships

- [Community 286](Community_286.md) (11 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 72](Community_72.md) (1 shared connections)
- [Community 280](Community_280.md) (1 shared connections)
- [Community 641](Community_641.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit_v3.py`

## Audit Trail

- EXTRACTED: 33 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*