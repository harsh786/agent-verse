# Community 171

> 32 nodes · cohesion 0.12

## Key Concepts

- **_ingest_repo_background()** (18 connections) — `agent-verse-backend/app/api/knowledge.py`
- **repository_security.py** (17 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **ingest_repository()** (16 connections) — `agent-verse-backend/app/api/knowledge.py`
- **read_repository_files()** (13 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **RepositorySecurityError** (12 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **resolve_repository_source()** (10 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **check_tool_args_for_exfil** (9 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **validate_patterns()** (8 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **repository_usage()** (7 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **RepositoryLimits** (5 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **validate_branch()** (5 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **_assert_no_symlink_components()** (4 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **PurePosixPath** (4 connections)
- **_contains_secret()** (3 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **_is_secret_or_disallowed()** (3 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **Path** (3 connections)
- **validate_repository_url()** (3 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **RepositoryFile** (2 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **RepositorySource** (2 connections) — `agent-verse-backend/app/ingestion/repository_security.py`
- **Any** (1 connections)
- **Return True if text appears to contain credentials or secrets.** (1 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Check tool arguments for potential data exfiltration. Returns: (blocked: bool,…** (1 connections) — `agent-verse-backend/app/agent/exfil_guard.py`
- **Clone a git repository and ingest all matching files. Uses git (open source)…** (1 connections) — `agent-verse-backend/app/api/knowledge.py`
- **Clone and atomically ingest under a disk/file quota and durable lease.…** (1 connections) — `agent-verse-backend/app/api/knowledge.py`
- **ValueError** (1 connections)
- *... and 7 more nodes in this community*

## Relationships

- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (21 shared connections)
- [Community 95](Community_95.md) (5 shared connections)
- [Community 170](Community_170.md) (2 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (2 shared connections)
- [Community 136](Community_136.md) (2 shared connections)
- [Community 667](Community_667.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (1 shared connections)
- [Community 82](Community_82.md) (1 shared connections)
- [Community 376](Community_376.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/exfil_guard.py`
- `agent-verse-backend/app/api/knowledge.py`
- `agent-verse-backend/app/ingestion/repository_security.py`

## Audit Trail

- EXTRACTED: 93 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*