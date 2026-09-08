# Community 402

> 16 nodes · cohesion 0.12

## Key Concepts

- **ChannelAuthGuard** (9 connections) — `agent-verse-backend/app/gateway/auth.py`
- **gateway/auth.py** (5 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.check_scope()** (2 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.verify_api_key()** (2 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.verify_hmac()** (2 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.verify_email_sender()** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.verify_telegram_user()** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **.verify_whatsapp_phone()** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **AsyncSession** (1 connections)
- **Per-channel authentication guard — Q9 of spec. Each channel has its own auth…** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **Verifies channel-specific authentication for every inbound command. All…** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **Verify a tenant API key (hash comparison).** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **# TODO: Look up hashed key in DB** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **Verify HMAC-SHA256 signature.** (1 connections) — `agent-verse-backend/app/gateway/auth.py`
- **Check if any of the provided scopes grants the required action.** (1 connections) — `agent-verse-backend/app/gateway/auth.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/auth.py`

## Audit Trail

- EXTRACTED: 17 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*