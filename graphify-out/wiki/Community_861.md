# Community 861

> 5 nodes · cohesion 0.40

## Key Concepts

- **chunk_by_tokens (tiktoken cl100k_base token-bounded chunking)** (3 connections) — `agent-verse-backend/app/knowledge/chunker_v2.py`
- **ParentWindow / build_parent_windows (citation window per chunk)** (2 connections) — `agent-verse-backend/app/knowledge/chunker_v2.py`
- **_ADVANCED_STRATEGIES (parent_child/sentence_window/fixed/agentic) collection-level override** (2 connections) — `agent-verse-backend/app/ingestion/chunking_strategy_selector.py`
- **orchestrator._chunk advanced dispatch (parent_child / sentence_window / fixed → rag chunkers)** (2 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **_chunk_by_chars (char fallback chunker, ~4 chars/token)** (1 connections) — `agent-verse-backend/app/knowledge/chunker_v2.py`

## Relationships

- [Community 526](Community_526.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/chunking_strategy_selector.py`
- `agent-verse-backend/app/ingestion/orchestrator.py`
- `agent-verse-backend/app/knowledge/chunker_v2.py`

## Audit Trail

- EXTRACTED: 2 (33%)
- INFERRED: 3 (50%)
- AMBIGUOUS: 1 (17%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*