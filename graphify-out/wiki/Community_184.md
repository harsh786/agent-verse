# Community 184

> 31 nodes · cohesion 0.09

## Key Concepts

- **google_oauth.py** (12 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **google_callback()** (7 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **google_login()** (7 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **user_service.py** (7 connections) — `agent-verse-backend/app/auth/user_service.py`
- **upsert_google_user()** (7 connections) — `agent-verse-backend/app/auth/user_service.py`
- **user.py** (6 connections) — `agent-verse-backend/app/db/models/user.py`
- **User** (6 connections) — `agent-verse-backend/app/db/models/user.py`
- **_pkce_store_pop()** (5 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **_pkce_store_set()** (5 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **TenantMembership** (5 connections) — `agent-verse-backend/app/db/models/user.py`
- **_generate_pkce()** (3 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **_pkce_redis_key()** (3 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Any** (2 connections)
- **get** (2 connections)
- **Request** (2 connections)
- **Base** (2 connections)
- **JSONResponse** (1 connections)
- **RedirectResponse** (1 connections)
- **Google OIDC login — Authorization Code + PKCE flow. Endpoints: GET…** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Exchange auth code for tokens, upsert user, mint AgentVerse JWT.** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Persist PKCE state to Redis (with 5-min TTL) or in-memory fallback.** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Retrieve-and-delete PKCE state from Redis or in-memory fallback.** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Generate code_verifier and code_challenge for PKCE.** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Redirect user to Google consent screen.** (1 connections) — `agent-verse-backend/app/auth/google_oauth.py`
- **Any** (1 connections)
- *... and 6 more nodes in this community*

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Chat DB Models](Chat_DB_Models.md) (2 shared connections)
- [Community 248](Community_248.md) (1 shared connections)
- [Community 320](Community_320.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/auth/google_oauth.py`
- `agent-verse-backend/app/auth/user_service.py`
- `agent-verse-backend/app/db/models/user.py`

## Audit Trail

- EXTRACTED: 52 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*