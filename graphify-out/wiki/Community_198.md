# Community 198

> 30 nodes · cohesion 0.15

## Key Concepts

- **OrgResponse** (34 connections) — `agent-verse-backend/app/gateway/command.py`
- **ChannelAdapter** (24 connections) — `agent-verse-backend/app/gateway/channels/base.py`
- **OrgCommand** (22 connections) — `agent-verse-backend/app/gateway/command.py`
- **command.py** (16 connections) — `agent-verse-backend/app/gateway/command.py`
- **channels/base.py** (14 connections) — `agent-verse-backend/app/gateway/channels/base.py`
- **email.py** (10 connections) — `agent-verse-backend/app/gateway/channels/email.py`
- **telegram.py** (9 connections) — `agent-verse-backend/app/gateway/channels/telegram.py`
- **webhook.py** (9 connections) — `agent-verse-backend/app/gateway/channels/webhook.py`
- **discord.py** (8 connections) — `agent-verse-backend/app/gateway/channels/discord.py`
- **slack.py** (8 connections) — `agent-verse-backend/app/gateway/channels/slack.py`
- **teams.py** (8 connections) — `agent-verse-backend/app/gateway/channels/teams.py`
- **whatsapp.py** (8 connections) — `agent-verse-backend/app/gateway/channels/whatsapp.py`
- **voice_webhook.py** (7 connections) — `agent-verse-backend/app/gateway/channels/voice_webhook.py`
- **ABC** (2 connections)
- **Base ChannelAdapter — abstract interface for all channel adapters.** (1 connections) — `agent-verse-backend/app/gateway/channels/base.py`
- **Abstract base for all channel adapters.** (1 connections) — `agent-verse-backend/app/gateway/channels/base.py`
- **Discord channel adapter — Q6 of spec. Handles: - Slash commands: /org ask, /org…** (1 connections) — `agent-verse-backend/app/gateway/channels/discord.py`
- **# TODO: implement full Ed25519 verify (requires PyNaCl)** (1 connections) — `agent-verse-backend/app/gateway/channels/discord.py`
- **Email channel adapter — Q6 of spec. Handles: - Inbound commands via email…** (1 connections) — `agent-verse-backend/app/gateway/channels/email.py`
- **Slack channel adapter — Bolt-compatible event handling. Handles: slash…** (1 connections) — `agent-verse-backend/app/gateway/channels/slack.py`
- **Microsoft Teams Bot Framework adapter (via Azure Bot Service). Setup:…** (1 connections) — `agent-verse-backend/app/gateway/channels/teams.py`
- **Telegram channel adapter — full Bot API integration. Handles: - Text commands →…** (1 connections) — `agent-verse-backend/app/gateway/channels/telegram.py`
- **Voice webhook adapter — Q6 of spec. Handles inbound voice webhook events from…** (1 connections) — `agent-verse-backend/app/gateway/channels/voice_webhook.py`
- **Generic HMAC-signed webhook adapter. Any external system can POST to…** (1 connections) — `agent-verse-backend/app/gateway/channels/webhook.py`
- **WhatsApp Business Cloud API adapter. Setup: WHATSAPP_ACCESS_TOKEN +…** (1 connections) — `agent-verse-backend/app/gateway/channels/whatsapp.py`
- *... and 5 more nodes in this community*

## Relationships

- [Community 265](Community_265.md) (10 shared connections)
- [Community 426](Community_426.md) (8 shared connections)
- [Community 537](Community_537.md) (6 shared connections)
- [Community 538](Community_538.md) (6 shared connections)
- [Community 569](Community_569.md) (6 shared connections)
- [Community 536](Community_536.md) (5 shared connections)
- [Community 446](Community_446.md) (5 shared connections)
- [Community 568](Community_568.md) (5 shared connections)
- [Community 485](Community_485.md) (5 shared connections)
- [Community 606](Community_606.md) (4 shared connections)
- [Ingestion Connectors](Ingestion_Connectors.md) (1 shared connections)
- [Community 572](Community_572.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/channels/base.py`
- `agent-verse-backend/app/gateway/channels/discord.py`
- `agent-verse-backend/app/gateway/channels/email.py`
- `agent-verse-backend/app/gateway/channels/slack.py`
- `agent-verse-backend/app/gateway/channels/teams.py`
- `agent-verse-backend/app/gateway/channels/telegram.py`
- `agent-verse-backend/app/gateway/channels/voice_webhook.py`
- `agent-verse-backend/app/gateway/channels/webhook.py`
- `agent-verse-backend/app/gateway/channels/whatsapp.py`
- `agent-verse-backend/app/gateway/command.py`

## Audit Trail

- EXTRACTED: 110 (85%)
- INFERRED: 19 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*