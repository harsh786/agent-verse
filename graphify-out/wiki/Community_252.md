# Community 252

> 25 nodes · cohesion 0.10

## Key Concepts

- **ChainTriggerConsumer** (8 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **.spec()** (4 connections) — `agent-verse-backend/app/orchestration/strategy_registry.py`
- **channels/gateway.py** (4 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **ChannelIngestionGateway** (4 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **.ingest()** (4 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **NLIntentClassifier** (4 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **._handle()** (4 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **Any** (3 connections)
- **chain.py** (3 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **._dispatch_matching()** (3 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **.start()** (3 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **.classify()** (2 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **._channel_to_type()** (2 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **Channel Ingestion Gateway — routes inbound channel events to the dispatcher.** (1 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **Routes channel events to matching trigger types and dispatches them.** (1 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **Process an inbound channel event and fire matching triggers. Returns list of…** (1 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **Classify natural-language messages to trigger types using embeddings + LLM…** (1 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **Return the most likely trigger_type string for the given message.** (1 connections) — `agent-verse-backend/app/triggers/channels/gateway.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **.stop()** (1 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **Goal chain trigger consumer — subscribes to Redis goal lifecycle events.** (1 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **Listens on Redis pub/sub for goal lifecycle events and fires chain triggers.** (1 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`
- **Subscribe to all goal lifecycle channels and process messages.** (1 connections) — `agent-verse-backend/app/triggers/consumers/chain.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 56](Community_56.md) (1 shared connections)
- [Community 66](Community_66.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/orchestration/strategy_registry.py`
- `agent-verse-backend/app/triggers/channels/gateway.py`
- `agent-verse-backend/app/triggers/consumers/chain.py`

## Audit Trail

- EXTRACTED: 31 (94%)
- INFERRED: 2 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*