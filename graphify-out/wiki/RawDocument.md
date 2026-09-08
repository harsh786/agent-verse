# RawDocument

> God node · 132 connections · `agent-verse-backend/app/ingestion/source_config.py`

**Community:** [Ingestion Connectors](Ingestion_Connectors.md)

## Connections by Relation

### calls
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- ._fetch_single() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- ._build_raw_doc() `EXTRACTED`
- .on_webhook() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .get_delta() `EXTRACTED`
- *…and 20 more `calls` connection(s) not listed (lowest-degree first to go)*

### contains
- source_config.py `EXTRACTED`

### imports
- base_connector.py `EXTRACTED`
- ingestion/pipeline.py `EXTRACTED`
- email_imap_connector.py `EXTRACTED`
- postgresql_connector.py `EXTRACTED`
- github_connector.py `EXTRACTED`
- jira_connector.py `EXTRACTED`
- mongodb_connector.py `EXTRACTED`
- pdf_file_connector.py `EXTRACTED`
- s3_connector.py `EXTRACTED`
- slack_connector.py `EXTRACTED`
- snowflake_connector.py `EXTRACTED`
- agent_generated_connector.py `EXTRACTED`
- arxiv_connector.py `EXTRACTED`
- azure_blob_connector.py `EXTRACTED`
- bigquery_connector.py `EXTRACTED`
- clickhouse_connector.py `EXTRACTED`
- confluence_connector.py `EXTRACTED`
- discord_connector.py `EXTRACTED`
- duckdb_connector.py `EXTRACTED`
- elasticsearch_connector.py `EXTRACTED`
- *…and 20 more `imports` connection(s) not listed (lowest-degree first to go)*

### method
- .compute_hash() `EXTRACTED`

### rationale_for
- Normalised document yielded by any BaseConnector. LAW-17: correlation_id tracks… `EXTRACTED`

### references
- .ingest() `EXTRACTED`
- ._index() `EXTRACTED`
- .on_webhook() `EXTRACTED`
- ._emit() `EXTRACTED`
- ._enrich() `EXTRACTED`
- .get_delta() `EXTRACTED`
- .on_webhook() `EXTRACTED`
- .get_delta() `EXTRACTED`

### uses
- BaseConnector `INFERRED`
- IngestionPipeline `INFERRED`
- S3Connector `INFERRED`
- WebCrawlConnector `INFERRED`
- SalesforceConnector `INFERRED`
- SlackConnector `INFERRED`
- AgentGeneratedConnector `INFERRED`
- GitHubConnector `INFERRED`
- MySQLConnector `INFERRED`
- TeamsConnector `INFERRED`
- ArXivConnector `INFERRED`
- AzureBlobConnector `INFERRED`
- BigQueryConnector `INFERRED`
- ClickHouseConnector `INFERRED`
- ConfluenceConnector `INFERRED`
- DiscordConnector `INFERRED`
- DuckDBConnector `INFERRED`
- ElasticsearchConnector `INFERRED`
- EmailIMAPConnector `INFERRED`
- GCSConnector `INFERRED`
- *…and 21 more `uses` connection(s) not listed (lowest-degree first to go)*

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*