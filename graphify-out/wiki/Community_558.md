# Community 558

> 10 nodes · cohesion 0.20

## Key Concepts

- **WebhookSignatureVerifier** (7 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **verifier.py** (3 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **.verify()** (2 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **.verify_github()** (2 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **.verify_stripe()** (2 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **WebhookSignatureVerifier — HMAC verification for all webhook families.** (1 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **Verify incoming webhook payloads using HMAC-SHA256.** (1 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **Return True if the signature is valid, False otherwise.** (1 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **Synchronous verify for GitHub X-Hub-Signature-256.** (1 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`
- **Stripe uses t=timestamp,v1=... format — verify the v1 component.** (1 connections) — `agent-verse-backend/app/triggers/webhooks/verifier.py`

## Relationships

- [Community 83](Community_83.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/webhooks/verifier.py`

## Audit Trail

- EXTRACTED: 11 (92%)
- INFERRED: 1 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*