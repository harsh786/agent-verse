# Community 141

> 37 nodes · cohesion 0.08

## Key Concepts

- **NotionConnector** (15 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **GDriveConnector** (12 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **._build_service()** (7 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **ingestion/connectors/__init__.py** (7 connections) — `agent-verse-backend/app/ingestion/connectors/__init__.py`
- **delta_reingest_files()** (7 connections) — `agent-verse-backend/app/scaling/tasks.py`
- **Any** (6 connections)
- **.get_file_metadata()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **.list_files()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **Any** (4 connections)
- **.fetch_page_content()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **.fetch_page_metadata()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **._get()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **.list_all_pages_in_workspace()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **.list_pages()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **._post()** (4 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **gdrive_connector.py** (3 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **.download_file()** (3 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **notion_connector.py** (3 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **._blocks_to_text()** (3 connections) — `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- **sharepoint_connector.py** (3 connections) — `agent-verse-backend/app/ingestion/connectors/sharepoint_connector.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **GDriveConnector — lists and downloads files from Google Drive for ingestion.…** (1 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **Download or export a Drive file and return its content as a string.** (1 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **Return metadata for a single Drive file.** (1 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- **Thin wrapper around the Google Drive REST API v3.** (1 connections) — `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- *... and 12 more nodes in this community*

## Relationships

- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (5 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (4 shared connections)
- [Community 287](Community_287.md) (2 shared connections)
- [Content Classification & Embedding Policy](Content_Classification_&_Embedding_Policy.md) (1 shared connections)
- [Community 82](Community_82.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/connectors/__init__.py`
- `agent-verse-backend/app/ingestion/connectors/gdrive_connector.py`
- `agent-verse-backend/app/ingestion/connectors/notion_connector.py`
- `agent-verse-backend/app/ingestion/connectors/sharepoint_connector.py`
- `agent-verse-backend/app/scaling/tasks.py`

## Audit Trail

- EXTRACTED: 63 (93%)
- INFERRED: 5 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*