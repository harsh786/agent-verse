# Community 517

> 11 nodes · cohesion 0.22

## Key Concepts

- **IdentityResolver** (8 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **identity_profile.py** (7 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **IdentityProfile (tenant/agent/delegated scope)** (7 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **.resolve()** (3 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **.has_permission()** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **.is_delegated()** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **.to_dict()** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **IdentityScope** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **IdentityProfile — tenant/agent/delegated identity resolution (spec §Layer 1).…** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **Resolved identity for a single request/goal execution.** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`
- **Resolves IdentityProfile from TenantContext and optional agent_id.** (1 connections) — `agent-verse-backend/app/security_runtime/identity_profile.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)

## Source Files

- `agent-verse-backend/app/security_runtime/identity_profile.py`

## Audit Trail

- EXTRACTED: 19 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*