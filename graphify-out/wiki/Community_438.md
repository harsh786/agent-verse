# Community 438

> 15 nodes · cohesion 0.18

## Key Concepts

- **LLMConfigStore** (13 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **llm_config_store.py** (5 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **set_llm_config_store()** (5 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **.get_config()** (4 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **._key()** (4 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **.delete_config()** (3 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **.set_config()** (3 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Any** (2 connections)
- **Redis-backed LLM configuration store. Stores per-tenant LLM provider…** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Reads and writes per-tenant LLM provider config to/from Redis. Args:…** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Store the LLM config for *tenant_id* in Redis.** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Return the LLM config for *tenant_id*, or *None* if not configured.** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Remove the LLM config for *tenant_id* from Redis.** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`
- **Wire the process-wide singleton (called once from ``create_app``).** (1 connections) — `agent-verse-backend/app/services/llm_config_store.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (4 shared connections)
- [Community 102](Community_102.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 87](Community_87.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/services/llm_config_store.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*