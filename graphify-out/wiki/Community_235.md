# Community 235

> 26 nodes · cohesion 0.11

## Key Concepts

- **WebhookDeliverySystem** (13 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **WebhookDelivery** (9 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **._attempt_delivery()** (7 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **webhook_delivery.py** (6 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.deliver()** (5 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **._delayed_retry()** (4 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **._schedule_retry()** (4 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **WebhookConfig** (3 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.register_webhook()** (3 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Any** (2 connections)
- **.sign_payload()** (2 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.list_dlq()** (2 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.retry_from_dlq()** (2 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **QA7 — Webhook Delivery Guarantees (at-least-once delivery). Guarantees: - At-…** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Register a new outbound webhook.** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Queue a webhook delivery. Returns a WebhookDelivery that will be retried until…** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Attempt one delivery. Returns True on success.** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **A registered outbound webhook.** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Per spec QA7 — a single webhook delivery attempt.** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **Compute HMAC-SHA256 signature for payload.** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **QA7 — At-least-once webhook delivery with exponential backoff. Usage: system =…** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.is_dead()** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.close()** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- **.dismiss_dlq()** (1 connections) — `agent-verse-backend/app/gateway/webhook_delivery.py`
- *... and 1 more nodes in this community*

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/webhook_delivery.py`

## Audit Trail

- EXTRACTED: 39 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*