# Community 405

> 16 nodes · cohesion 0.15

## Key Concepts

- **SlackConnector** (12 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **SlackIngestor** (9 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`
- **.get_delta()** (5 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **slack_ingestor.py** (4 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`
- **.validate_connection()** (3 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **.ingest_channel()** (3 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`
- **SourceConfig** (2 connections)
- **._headers()** (2 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`
- **BaseConnector** (1 connections)
- **ConnectionHealth** (1 connections)
- **Slack workspace message ingestion.** (1 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **Yield messages from configured channels since cursor timestamp.** (1 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **.supports_streaming()** (1 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **Any** (1 connections)
- **Slack channel message ingestor via Web API.** (1 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`

## Relationships

- [Ingestion Connectors](Ingestion_Connectors.md) (8 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- `agent-verse-backend/app/knowledge/ingestors/slack_ingestor.py`

## Audit Trail

- EXTRACTED: 24 (80%)
- INFERRED: 6 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*