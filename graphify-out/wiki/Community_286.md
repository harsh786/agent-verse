# Community 286

> 23 nodes · cohesion 0.14

## Key Concepts

- **AuditV3 (hash-chained append-only audit)** (20 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **audit_v3.py** (11 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.append()** (10 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.record()** (8 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **_hash_dict()** (7 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **AuditRecord** (5 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.append_security_event()** (5 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.export_worm_bundle()** (4 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.export_records()** (3 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **._get_previous_hash()** (3 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **._next_sequence()** (3 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **.authorize_break_glass()** (2 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Audit v3 — World-Class Immutable Audit System…** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Immutable append-only audit log with complete hash chain. Every record…** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Append a complete, privacy-preserving governance event to the existing chain.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Require dual control before break-glass authority can be exercised.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Append an audit record to the chain. Returns the new record.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Synchronous record() — convenience wrapper for non-async callers.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Export audit records as JSON or CSV.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Export chain evidence with a deterministic manifest for immutable retention.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **A single audit record with full context for tamper-proof chain.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **Deterministic SHA-256 hash of any dict/value.** (1 connections) — `agent-verse-backend/app/governance/audit_v3.py`
- **LegalHoldManager.is_under_hold (O(1) Redis-cached deletion gate)** (1 connections) — `agent-verse-backend/app/governance/legal_holds.py`

## Relationships

- [Community 404](Community_404.md) (11 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 236](Community_236.md) (2 shared connections)
- [Community 641](Community_641.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 49](Community_49.md) (1 shared connections)
- [Community 427](Community_427.md) (1 shared connections)
- [Community 166](Community_166.md) (1 shared connections)
- [Community 488](Community_488.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit_v3.py`
- `agent-verse-backend/app/governance/legal_holds.py`

## Audit Trail

- EXTRACTED: 53 (93%)
- INFERRED: 4 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*