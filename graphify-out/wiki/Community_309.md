# Community 309

> 21 nodes · cohesion 0.20

## Key Concepts

- **auth/mfa.py** (12 connections) — `agent-verse-backend/app/auth/mfa.py`
- **confirm_mfa()** (8 connections) — `agent-verse-backend/app/auth/mfa.py`
- **complete_mfa_login()** (7 connections) — `agent-verse-backend/app/auth/mfa.py`
- **enroll_mfa()** (7 connections) — `agent-verse-backend/app/auth/mfa.py`
- **validate_mfa()** (7 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Any** (6 connections)
- **Request** (6 connections)
- **verify_mfa()** (6 connections) — `agent-verse-backend/app/auth/mfa.py`
- **_get_pyotp()** (5 connections) — `agent-verse-backend/app/auth/mfa.py`
- **_req_tenant()** (5 connections) — `agent-verse-backend/app/auth/mfa.py`
- **BaseModel** (4 connections)
- **EnrollConfirmRequest** (3 connections) — `agent-verse-backend/app/auth/mfa.py`
- **MFACompleteRequest** (3 connections) — `agent-verse-backend/app/auth/mfa.py`
- **ValidateRequest** (3 connections) — `agent-verse-backend/app/auth/mfa.py`
- **VerifyRequest** (3 connections) — `agent-verse-backend/app/auth/mfa.py`
- **TOTP-based MFA using pyotp. Secrets sent in request body, stored in DB.** (1 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Verify an MFA token (TOTP). Lightweight verification endpoint for use in…** (1 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Complete MFA-gated login: validate TOTP code and issue full session. Flow: 1.…** (1 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Generate a new TOTP secret. The secret is NOT stored yet — call /confirm to…** (1 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Verify the code, then store the encrypted secret and mark MFA as enabled.** (1 connections) — `agent-verse-backend/app/auth/mfa.py`
- **Validate a TOTP code at login time. Loads secret from DB.** (1 connections) — `agent-verse-backend/app/auth/mfa.py`

## Relationships

- [Community 82](Community_82.md) (5 shared connections)

## Source Files

- `agent-verse-backend/app/auth/mfa.py`

## Audit Trail

- EXTRACTED: 48 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*