# Community 302

> 22 nodes · cohesion 0.13

## Key Concepts

- **CredentialVault** (13 connections) — `agent-verse-backend/app/providers/vault.py`
- **RedisConnectorSecretStore** (11 connections) — `agent-verse-backend/app/providers/vault.py`
- **Any** (7 connections)
- **._redis_key()** (6 connections) — `agent-verse-backend/app/providers/vault.py`
- **.rotate_key()** (5 connections) — `agent-verse-backend/app/providers/vault.py`
- **_derive_fernet_key()** (4 connections) — `agent-verse-backend/app/providers/vault.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/providers/vault.py`
- **.resolve()** (3 connections) — `agent-verse-backend/app/providers/vault.py`
- **.store()** (3 connections) — `agent-verse-backend/app/providers/vault.py`
- **.decrypt()** (2 connections) — `agent-verse-backend/app/providers/vault.py`
- **.encrypt()** (2 connections) — `agent-verse-backend/app/providers/vault.py`
- **.from_byok()** (2 connections) — `agent-verse-backend/app/providers/vault.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/providers/vault.py`
- **.__repr__()** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **.__str__()** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Derive a 32-byte key from *master_key* using PBKDF2 with a fixed salt. The salt…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Encrypt and decrypt credential strings using Fernet (AES-256-GCM). The master…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Encrypt *plaintext* and return a URL-safe ciphertext string.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Decrypt *ciphertext* back to plaintext. Raises…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Re-encrypt all stored secrets with a new master key. Process (transactional):…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Create a vault instance using a customer-provided encryption key (BYOK). The…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Encrypted Redis-backed connector secret store scoped by tenant and server.** (1 connections) — `agent-verse-backend/app/providers/vault.py`

## Relationships

- [Community 279](Community_279.md) (7 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/providers/vault.py`

## Audit Trail

- EXTRACTED: 41 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*