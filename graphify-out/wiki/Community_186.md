# Community 186

> 31 nodes · cohesion 0.10

## Key Concepts

- **OAuthFlowManager** (18 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **oauth.py** (9 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **OAuthToken** (8 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.exchange_code()** (6 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **._persist_token_to_db()** (6 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.refresh_token()** (6 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **OAuthState (PKCE)** (6 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.get_pending_flow()** (5 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.get_token()** (5 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.load_tokens_from_db()** (5 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.start_flow()** (4 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **._cleanup_expired_flows()** (3 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **._decrypt_token()** (3 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **._encrypt_token()** (3 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Any** (3 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.code_challenge()** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **.is_expired()** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **OAuth flow manager — handles authorization code + PKCE flows for MCP connectors.** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Initiate a PKCE OAuth flow. Returns the PKCE parameters and state token.** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Exchange authorization code for tokens (PKCE flow).** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Flexible token lookup. Supports two call styles: - Keyword:…** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Ephemeral state for an in-progress OAuth flow.** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Refresh an expired access token.** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- **Persist an OAuth token to the database for cross-restart recovery.** (1 connections) — `agent-verse-backend/app/mcp/oauth.py`
- *... and 6 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (2 shared connections)
- [Community 248](Community_248.md) (1 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/mcp/oauth.py`

## Audit Trail

- EXTRACTED: 59 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*