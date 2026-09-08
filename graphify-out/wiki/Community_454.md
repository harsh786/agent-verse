# Community 454

> 14 nodes · cohesion 0.20

## Key Concepts

- **OutboundWebhookService** (9 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **webhook_service.py** (5 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **.deliver()** (5 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **WebhookDelivery** (5 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **._evict_old_deliveries()** (3 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **.get_deliveries()** (2 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **.get_delivery()** (2 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **.list_dlq()** (2 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **Any** (1 connections)
- **Outbound webhook delivery with retry and dead-letter queue.** (1 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **Delivers outbound webhooks with exponential backoff retry.** (1 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **Remove delivery records older than TTL or beyond size limit.** (1 connections) — `agent-verse-backend/app/services/webhook_service.py`
- **Deliver a webhook with retry. Returns delivery record.** (1 connections) — `agent-verse-backend/app/services/webhook_service.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 79](Community_79.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/services/webhook_service.py`

## Audit Trail

- EXTRACTED: 20 (95%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (5%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*