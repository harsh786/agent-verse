# Ingestion Connectors

> 385 nodes · cohesion 0.01

## Key Concepts

- **RawDocument** (132 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **BaseConnector** (95 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **SourceConfig** (90 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **register()** (80 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **ConnectionHealth** (78 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **source_config.py** (49 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **connector_registry.py** (47 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **base_connector.py** (45 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **S3Connector** (16 connections) — `agent-verse-backend/app/ingestion/connectors/s3_connector.py`
- **ingestion/pipeline.py** (15 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **email_imap_connector.py** (14 connections) — `agent-verse-backend/app/ingestion/connectors/email_imap_connector.py`
- **postgresql_connector.py** (13 connections) — `agent-verse-backend/app/ingestion/connectors/postgresql_connector.py`
- **github_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/github_connector.py`
- **jira_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/jira_connector.py`
- **mongodb_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/mongodb_connector.py`
- **pdf_file_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/pdf_file_connector.py`
- **s3_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/s3_connector.py`
- **slack_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/slack_connector.py`
- **snowflake_connector.py** (12 connections) — `agent-verse-backend/app/ingestion/connectors/snowflake_connector.py`
- **agent_generated_connector.py** (11 connections) — `agent-verse-backend/app/ingestion/connectors/agent_generated_connector.py`
- **AgentGeneratedConnector** (11 connections) — `agent-verse-backend/app/ingestion/connectors/agent_generated_connector.py`
- **arxiv_connector.py** (11 connections) — `agent-verse-backend/app/ingestion/connectors/arxiv_connector.py`
- **azure_blob_connector.py** (11 connections) — `agent-verse-backend/app/ingestion/connectors/azure_blob_connector.py`
- **bigquery_connector.py** (11 connections) — `agent-verse-backend/app/ingestion/connectors/bigquery_connector.py`
- **clickhouse_connector.py** (11 connections) — `agent-verse-backend/app/ingestion/connectors/clickhouse_connector.py`
- *... and 360 more nodes in this community*

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (40 shared connections)
- [Ingestion API](Ingestion_API.md) (18 shared connections)
- [Community 405](Community_405.md) (8 shared connections)
- [Community 225](Community_225.md) (8 shared connections)
- [Community 571](Community_571.md) (7 shared connections)
- [Community 510](Community_510.md) (7 shared connections)
- [Community 607](Community_607.md) (5 shared connections)
- [Community 681](Community_681.md) (4 shared connections)
- [Community 543](Community_543.md) (3 shared connections)
- [Community 58](Community_58.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (1 shared connections)
- [Community 1012](Community_1012.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/connector_registry.py`
- `agent-verse-backend/app/ingestion/connectors/agent_generated_connector.py`
- `agent-verse-backend/app/ingestion/connectors/arxiv_connector.py`
- `agent-verse-backend/app/ingestion/connectors/azure_blob_connector.py`
- `agent-verse-backend/app/ingestion/connectors/bigquery_connector.py`
- `agent-verse-backend/app/ingestion/connectors/clickhouse_connector.py`
- `agent-verse-backend/app/ingestion/connectors/confluence_connector.py`
- `agent-verse-backend/app/ingestion/connectors/discord_connector.py`
- `agent-verse-backend/app/ingestion/connectors/duckdb_connector.py`
- `agent-verse-backend/app/ingestion/connectors/elasticsearch_connector.py`
- `agent-verse-backend/app/ingestion/connectors/email_imap_connector.py`
- `agent-verse-backend/app/ingestion/connectors/gcs_connector.py`
- `agent-verse-backend/app/ingestion/connectors/github_connector.py`
- `agent-verse-backend/app/ingestion/connectors/gitlab_connector.py`
- `agent-verse-backend/app/ingestion/connectors/hubspot_connector.py`
- `agent-verse-backend/app/ingestion/connectors/influxdb_connector.py`
- `agent-verse-backend/app/ingestion/connectors/jira_connector.py`
- `agent-verse-backend/app/ingestion/connectors/kafka_connector.py`
- `agent-verse-backend/app/ingestion/connectors/kinesis_connector.py`

## Audit Trail

- EXTRACTED: 876 (84%)
- INFERRED: 168 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*