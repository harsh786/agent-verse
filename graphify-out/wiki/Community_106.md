# Community 106

> 43 nodes · cohesion 0.11

## Key Concepts

- **keycloak.py** (20 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **api/auth.py** (19 connections) — `agent-verse-backend/app/api/auth.py`
- **resolve_tenant_from_jwt()** (11 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **exchange_token()** (10 connections) — `agent-verse-backend/app/api/auth.py`
- **get_sso_config()** (10 connections) — `agent-verse-backend/app/api/auth.py`
- **validate_jwt()** (10 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **refresh_token()** (9 connections) — `agent-verse-backend/app/api/auth.py`
- **_client_id()** (9 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **_keycloak_url()** (9 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **_realm()** (9 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **get_userinfo()** (8 connections) — `agent-verse-backend/app/api/auth.py`
- **sso_login()** (7 connections) — `agent-verse-backend/app/api/auth.py`
- **token_endpoint()** (7 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **_check_auth_rate_limit()** (6 connections) — `agent-verse-backend/app/api/auth.py`
- **authorization_endpoint()** (6 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **extract_roles()** (6 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **_sso_enabled()** (6 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **get_jwks()** (5 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **Any** (5 connections)
- **_default_redirect_uri()** (4 connections) — `agent-verse-backend/app/api/auth.py`
- **Any** (4 connections)
- **Request** (4 connections)
- **_get_or_provision_tenant()** (4 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **jwks_uri()** (4 connections) — `agent-verse-backend/app/auth/keycloak.py`
- **get** (3 connections)
- *... and 18 more nodes in this community*

## Relationships

- [Community 136](Community_136.md) (7 shared connections)
- [Community 57](Community_57.md) (6 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [Community 82](Community_82.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 320](Community_320.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 601](Community_601.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/auth.py`
- `agent-verse-backend/app/auth/keycloak.py`

## Audit Trail

- EXTRACTED: 119 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*