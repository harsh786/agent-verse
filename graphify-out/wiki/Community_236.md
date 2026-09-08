# Community 236

> 26 nodes · cohesion 0.11

## Key Concepts

- **audit_v2.py** (10 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **AuditEvent** (10 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Any** (9 connections)
- **AuditWriter (Redis WAL, deprecated)** (6 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **audit_admin_action decorator** (4 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **HashChainVerifier (v2, deprecated)** (4 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.verify()** (4 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **_redact_pii (recursive PII field redaction)** (4 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.to_dict()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.write()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.write_batch()** (3 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.compute_hash()** (2 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.to_json()** (2 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **datetime** (2 connections)
- **Production-grade audit system with WAL, hash chaining, and SIEM integration.…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Return SHA-256 of the canonical JSON representation of this event. The…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Writes audit events to a Redis list (WAL). Guarantees: - Never raises — a Redis…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Push one event to the WAL. Never raises.** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Push multiple events via a single pipelined command.** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Verifies the cryptographic hash chain for a tenant's audit events. Reads events…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Decorator that emits an AuditEvent for every admin route handler call. Captures…** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- **Recursively redact PII values in nested dicts/lists (max depth 5).** (1 connections) — `agent-verse-backend/app/governance/audit_v2.py`
- *... and 1 more nodes in this community*

## Relationships

- [Community 427](Community_427.md) (5 shared connections)
- [Community 286](Community_286.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/audit_v2.py`

## Audit Trail

- EXTRACTED: 44 (98%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*