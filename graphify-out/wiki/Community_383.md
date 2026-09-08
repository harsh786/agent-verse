# Community 383

> 17 nodes · cohesion 0.15

## Key Concepts

- **LimitsV2Checker (steps/tokens/connector-rpm/burst limits)** (10 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.get_config()** (6 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **limits_v2.py** (5 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.check_burst_rate()** (3 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.check_connector_rate()** (3 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.check_step_limit()** (3 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.check_token_limit()** (3 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **LimitsV2Config** (3 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Any** (1 connections)
- **Limits v2 — Comprehensive Resource Quotas…** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Checks all v2 limits with in-process counters + Redis when available.** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Check if step count is within plan limit.** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Check if token count is within plan limit.** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Check per-connector rate limit (in-process fallback).** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Check 10-second burst rate limit.** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`
- **Complete limits configuration for a plan tier.** (1 connections) — `agent-verse-backend/app/tenancy/limits_v2.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 57](Community_57.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/limits_v2.py`

## Audit Trail

- EXTRACTED: 23 (92%)
- INFERRED: 2 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*