# Community 279

> 23 nodes · cohesion 0.11

## Key Concepts

- **vault.py** (22 connections) — `agent-verse-backend/app/providers/vault.py`
- **get_vault()** (17 connections) — `agent-verse-backend/app/providers/vault.py`
- **is_connector_secret_ref()** (13 connections) — `agent-verse-backend/app/providers/vault.py`
- **resolve_connector_secret_ref_for_tenant()** (10 connections) — `agent-verse-backend/app/providers/vault.py`
- **resolve_connector_secret_ref()** (7 connections) — `agent-verse-backend/app/providers/vault.py`
- **_secret_resolver()** (6 connections) — `agent-verse-backend/app/api/connectors.py`
- **read_secret() (file/env secret resolution)** (6 connections) — `agent-verse-backend/app/core/secrets.py`
- **SecretNotFoundError** (6 connections) — `agent-verse-backend/app/core/secrets.py`
- **store_connector_secret_for_tenant()** (6 connections) — `agent-verse-backend/app/providers/vault.py`
- **_get_master_key()** (3 connections) — `agent-verse-backend/app/providers/vault.py`
- **store_connector_secret()** (3 connections) — `agent-verse-backend/app/providers/vault.py`
- **_connector_secret_ref_parts()** (2 connections) — `agent-verse-backend/app/providers/vault.py`
- **RuntimeError** (1 connections)
- **Raised when a required secret cannot be resolved from file or env.** (1 connections) — `agent-verse-backend/app/core/secrets.py`
- **Resolve a secret by name, preferring a mounted ``*_FILE`` over a plain env var.** (1 connections) — `agent-verse-backend/app/core/secrets.py`
- **Credential vault — AES-256-GCM encryption via Fernet. All LLM API keys and MCP…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Store a connector secret in either a tenant-aware store or mapping fallback.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Resolve a connector secret from a tenant-aware store or mapping fallback.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Return the vault master key from the environment. - Raises ``RuntimeError`` if…** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Create a vault from the environment master key.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Return True when *value* is a connector secret reference.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Store a connector secret in the provided store or process fallback store.** (1 connections) — `agent-verse-backend/app/providers/vault.py`
- **Resolve a connector secret reference without exposing secrets in configs.** (1 connections) — `agent-verse-backend/app/providers/vault.py`

## Relationships

- [Community 145](Community_145.md) (15 shared connections)
- [Community 302](Community_302.md) (7 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (6 shared connections)
- [Community 182](Community_182.md) (5 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (5 shared connections)
- [Tenant API Keys/IP Allowlist](Tenant_API_Keys-IP_Allowlist.md) (3 shared connections)
- [Community 248](Community_248.md) (2 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (2 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 53](Community_53.md) (1 shared connections)
- [Community 87](Community_87.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/connectors.py`
- `agent-verse-backend/app/core/secrets.py`
- `agent-verse-backend/app/providers/vault.py`

## Audit Trail

- EXTRACTED: 81 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*