# Community 751

> 6 nodes · cohesion 0.33

## Key Concepts

- **read_repository_files (O_NOFOLLOW, symlink refusal, byte/file quotas, strict UTF-8)** (3 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **SourceConfig (sync policy, filters, chunking, PII, ACL, cursor, backoff stats)** (3 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **BaseConnector._matches include/exclude fnmatch filter** (2 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **Stage 6 PII_DETECT (Presidio analyze + redact/reject/allow)** (2 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **validate_patterns (allowlist .py/.md/.ts/.js, block secret-looking names, no traversal)** (2 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **RepositoryLimits (max_files/max_file_bytes/max_total_bytes/max_repository_bytes)** (1 connections) — `agent-verse-backend/app/ingestion/repository_security.py`

## Relationships

- [Community 525](Community_525.md) (1 shared connections)
- [Community 526](Community_526.md) (1 shared connections)
- [Community 634](Community_634.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/pipeline.py`
- `agent-verse-backend/app/ingestion/repository_security.py`
- `agent-verse-backend/app/ingestion/source_config.py`

## Audit Trail

- EXTRACTED: 5 (62%)
- INFERRED: 3 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*