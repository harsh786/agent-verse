# Community 63

> 62 nodes · cohesion 0.06

## Key Concepts

- **api/mfa.py** (26 connections) — `agent-verse-backend/app/api/mfa.py`
- **Any** (14 connections)
- **complete_enrollment()** (11 connections) — `agent-verse-backend/app/api/mfa.py`
- **regenerate_recovery_codes()** (11 connections) — `agent-verse-backend/app/api/mfa.py`
- **verify_mfa()** (11 connections) — `agent-verse-backend/app/api/mfa.py`
- **_require_tenant()** (10 connections) — `agent-verse-backend/app/api/mfa.py`
- **_check_rate_limit_global()** (9 connections) — `agent-verse-backend/app/api/mfa.py`
- **disable_mfa()** (9 connections) — `agent-verse-backend/app/api/mfa.py`
- **Request** (8 connections)
- **begin_enrollment()** (7 connections) — `agent-verse-backend/app/api/mfa.py`
- **_is_totp_replayed()** (7 connections) — `agent-verse-backend/app/api/mfa.py`
- **MFAStore** (7 connections) — `agent-verse-backend/app/api/mfa.py`
- **TenantMFA** (7 connections) — `agent-verse-backend/app/db/models/mfa.py`
- **get_mfa_status()** (6 connections) — `agent-verse-backend/app/api/mfa.py`
- **get_recovery_codes_count()** (6 connections) — `agent-verse-backend/app/api/mfa.py`
- **decrypt_secret()** (5 connections) — `agent-verse-backend/app/api/mfa_crypto.py`
- **encrypt_secret()** (5 connections) — `agent-verse-backend/app/api/mfa_crypto.py`
- **_hash_recovery_code()** (5 connections) — `agent-verse-backend/app/api/mfa.py`
- **.get()** (5 connections) — `agent-verse-backend/app/api/mfa.py`
- **.save()** (5 connections) — `agent-verse-backend/app/api/mfa.py`
- **VerifyRequest** (5 connections) — `agent-verse-backend/app/api/mfa.py`
- **_check_totp_replay()** (4 connections) — `agent-verse-backend/app/api/mfa.py`
- **mfa_crypto.py** (4 connections) — `agent-verse-backend/app/api/mfa_crypto.py`
- **_get_fernet_key()** (4 connections) — `agent-verse-backend/app/api/mfa_crypto.py`
- **_generate_recovery_codes()** (4 connections) — `agent-verse-backend/app/api/mfa.py`
- *... and 37 more nodes in this community*

## Relationships

- [Community 82](Community_82.md) (5 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 248](Community_248.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/mfa.py`
- `agent-verse-backend/app/api/mfa_crypto.py`
- `agent-verse-backend/app/db/models/mfa.py`

## Audit Trail

- EXTRACTED: 129 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*