# Community 860

> 5 nodes · cohesion 0.40

## Key Concepts

- **load_all_connectors (5-tier import list: gdrive/notion/slack/github/s3/confluence/jira/kafka/…)** (3 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **@register decorator + _REGISTRY (self-registering connectors)** (2 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **validate_connection health probe (LAW-21, 10s budget)** (1 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **resolve_repository_source (HTTPS-only, SSRF guard, DNS pinning, port 443)** (1 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **SourceFamily (18 families ≈230 source types)** (1 connections) — `agent-verse-backend/app/ingestion/source_config.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/connector_registry.py`
- `agent-verse-backend/app/ingestion/repository_security.py`
- `agent-verse-backend/app/ingestion/source_config.py`

## Audit Trail

- EXTRACTED: 1 (25%)
- INFERRED: 3 (75%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*