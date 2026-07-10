# RAG, Knowledge, Ingestion, Retrieval, and Chunking

**Date:** 2026-07-08

## Purpose

This document covers every RAG pattern, ingestion path, retrieval strategy, and chunking strategy in AgentVerse.

## RAG System Layers

```text
Raw content
  -> classifier
  -> parser
  -> chunker
  -> embedder
  -> vector/lexical index
  -> retrieval strategy
  -> reranking
  -> context manager
  -> agent prompt
```

## Knowledge Model

Core data structures in `app/rag/models.py`:

```python
KnowledgeCollection
  - collection_id
  - name
  - description
  - document_count
  - embedder

Document
  - document_id
  - collection_id
  - source
  - content
  - content_hash
  - metadata

Chunk
  - chunk_id
  - document_id
  - content
  - embedding
  - chunk_index
  - metadata
  - parent_chunk_id
  - chunk_level
  - window_start/window_end
```

## KnowledgeStore

Implemented in `app/rag/store.py`.

It supports:

- in-memory fallback storage
- Postgres persistence
- pgvector similarity
- trigram fuzzy search
- hybrid scoring

Hybrid score:

```text
score = 0.7 * cosine_similarity(query_vec, chunk_vec)
      + 0.3 * trigram_overlap(query_text, chunk_text)
```

## Ingestion Sources

Source-specific ingestors live in `app/knowledge/ingestors/`:

| Ingestor | Source | Output |
|---|---|---|
| `pdf_ingestor.py` | PDFs | page text chunks |
| `docx_ingestor.py` | DOCX | heading/paragraph chunks |
| `github_ingestor.py` | GitHub repos | code/function chunks |
| `jira_ingestor.py` | Jira issues | issue chunks with key, summary, status |
| `confluence_ingestor.py` | Confluence pages | page/section chunks |
| `slack_ingestor.py` | Slack exports | thread/message chunks |

## Content Classification

`app/ingestion/content_classifier.py` detects:

```text
TEXT, PDF, DOCX, HTML, MARKDOWN, CODE, IMAGE, AUDIO, VIDEO, CSV, JSON, WEB_PAGE, MIXED
```

Detection uses both filename extensions and content inspection.

## Modality Pipeline

`app/ingestion/modality_pipeline.py` maps content type to parser, chunker, embedding modality, and requirements:

```text
PDF   -> layout chunking, text embeddings
Audio -> timestamp chunking, transcription required
Video -> scene chunking, multimodal/text embeddings
Image -> region chunking, vision required
Code  -> AST/code chunking
DOCX  -> heading chunking
CSV   -> row-group chunking
Text  -> semantic chunking
```

## Chunking Strategies

### 1. Token-Aware Chunking

Implemented in `app/knowledge/chunker_v2.py`.

```text
max_tokens=512
overlap_tokens=64
encoding=cl100k_base
```

Best for PDFs, DOCX, text, markdown.

### 2. Semantic Chunking

Implemented in `app/rag/chunker.py`.

Strategies:

- sentence-boundary chunking
- markdown heading chunking
- code boundary chunking
- fixed-size fallback

Best for mixed text, markdown docs, source code.

### 3. Parent-Child Chunking

Implemented in `app/rag/parent_child_chunker.py`.

Workflow:

```text
Large parent chunk -> smaller child chunks
Index child chunks
Retrieve child
Return parent context
```

This gives small-chunk precision with large-context recall.

Best for contracts, manuals, architecture docs.

### 4. Sentence Window Retrieval

Implemented in `app/rag/sentence_window.py`.

Workflow:

```text
Index single sentence
Store surrounding ±N sentence window in metadata
At retrieval, return window instead of isolated sentence
```

Best for legal, policy, and FAQ retrieval.

### 5. Agentic Chunking

Implemented in `app/rag/agentic/patterns/agentic_chunking.py`.

Workflow:

```text
Chunk -> LLM extracts atomic propositions -> each proposition becomes searchable unit
```

Best for dense policy documents where every sentence contains a separate fact.

## Retrieval Legs

### Vector Retrieval

Uses pgvector HNSW cosine search.

Best for semantic similarity:

```text
"payment reversal issue" -> finds "refund settlement failed"
```

### PostgreSQL FTS

Uses `tsvector` and `ts_rank_cd`.

Best for exact keyword and phrase matching.

### Trigram Retrieval

Uses `pg_trgm` fuzzy similarity.

Best for typo tolerance:

```text
"authntication" -> "authentication"
```

### BM25

Implemented in `app/rag/bm25.py`.

Uses true Okapi BM25 when `rank_bm25` is installed; falls back to term-frequency scoring.

Best for structured identifiers and keyword-heavy search.

## Reranking Methods

### RRF

Reciprocal Rank Fusion merges ranked result lists:

```text
score = sum(1 / (60 + rank_i))
```

Used for hybrid retrieval and fusion RAG.

### Cross-Encoder

Implemented in `app/rag/cross_encoder.py`.

Uses `cross-encoder/ms-marco-MiniLM-L-6-v2` when available; TF-IDF fallback otherwise.

Best for high-precision reranking of top 50 results.

### ColBERT Late Interaction

Implemented in `app/rag/agentic/patterns/colbert.py`.

Uses token-level MaxSim scoring:

```text
Each query token finds its best matching document token.
```

Best for dense technical/legal queries where individual token matches matter.

## Retrieval Strategies

Implemented in `app/rag/engine.py`.

### Direct Hybrid

Default path:

```text
pgvector + FTS + trigram -> RRF -> top K
```

### Lexical

Selected for IDs and ticket-like queries:

```text
JIRA-123, PR-42, ticket BI-7439
```

### HyDE

Generates a hypothetical answer and searches with that synthetic document.

Best for short abstract questions.

### Multi-Hop

Decomposes comparative/analytical queries into sub-queries.

Best for compare/analyze/relationship questions.

### Fusion RAG

Expands query into multiple variants, retrieves in parallel, RRF-merges results.

### Corrective RAG

Runs retrieval, detects low confidence, falls back through:

```text
hybrid -> graph -> hyde -> web -> ltm -> parametric
```

### FLARE

Generate first, detect uncertainty, retrieve for uncertain claim, regenerate.

### Self-RAG

Decides whether retrieval is needed, retrieves, critiques relevance/support/usefulness.

### Speculative RAG

Generates multiple candidate answers, retrieves evidence for each, returns best-supported candidate.

## Agentic RAG Supporting Components

| Component | File | Purpose |
|---|---|---|
| QueryExpander | `app/rag/agentic/query_expander.py` | multi-query variants for Fusion RAG |
| QueryReformulator | `app/rag/agentic/query_reformulator.py` | rewrites when retrieval is empty |
| ContextGapDetector | `app/rag/agentic/context_gap_detector.py` | detects "cannot determine", "not found", etc. |
| FallbackChain | `app/rag/agentic/fallback_chain.py` | tracks fallback order and final source |
| SearchDirectiveParser | `app/rag/agentic/search_directive_parser.py` | parses `[SEARCH:kb:"..."]` directives |
| SourceInventory | `app/rag/agentic/source_inventory.py` | snapshots available KB/KG/LTM/web sources |
| CitationThreader | `app/rag/agentic/citation_threader.py` | assigns citation indexes |
| RetrieverTool | `app/rag/agentic/retriever_tool.py` | unified structured retrieval interface |

## Federated Search

Implemented in `app/knowledge/federated_search.py`.

Runs searches across multiple collections in parallel, normalizes scores per collection, deduplicates by content hash, and returns global top K.

Use case:

```text
Search across: finance policy, compliance policy, engineering docs, Jira issue history.
```

## Knowledge Graph

Knowledge graph support is exposed through `app/api/knowledge_graph.py` and backed by migration `0085_add_knowledge_graph.py`. It is a separate retrieval source from vector/lexical search.

Where vector RAG answers "which chunks are semantically similar to this query?", graph retrieval answers "which entities and relationships connect this concept to that concept?"

Conceptual model:

```text
Node: Entity
  - id
  - tenant_id
  - label/type
  - properties

Edge: Relationship
  - source_node_id
  - target_node_id
  - relationship_type
  - confidence
  - provenance/source document
```

Example graph:

```text
PCI_DSS
  -> requires -> AccessControl
  -> requires -> AuditLogging
  -> applies_to -> PaymentData

SOX
  -> requires -> ChangeApproval
  -> requires -> AuditTrail
  -> applies_to -> FinancialReporting
```

How it fits retrieval:

```text
Query: "What controls overlap between PCI and SOX?"
  -> RetrievalPolicy sees relationship/impact/dependency query
  -> select GRAPH when kg_available=true
  -> graph traversal finds shared controls
  -> vector RAG retrieves supporting policy text
  -> answer includes relationship chain + citations
```

Best for:

- compliance dependency mapping
- impact analysis
- entity relationship exploration
- lineage and provenance
- root-cause reasoning
- policy-to-control mapping

Hybrid usage:

```text
Graph says: PCI -> requires -> AccessControl
Vector RAG retrieves the access control policy section
Verifier checks answer is grounded in retrieved text
```

The graph should not replace RAG. It complements RAG by providing structural relationships, while vector/lexical retrieval provides the evidence text.
