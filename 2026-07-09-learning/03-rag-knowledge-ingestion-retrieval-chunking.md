# AgentVerse RAG, Knowledge, Ingestion, Retrieval & Chunking — Complete Reference

This document is the authoritative guide to how AgentVerse acquires, stores, searches, and presents knowledge. It covers the data model, the ingestion pipeline for every supported content type, all nine RAG patterns, every retrieval strategy in the engine, every chunking algorithm, and the full reranking + citation pipeline. Read alongside `app/rag/`, `app/ingestion/`, and `app/context/`.

---

## Part A: The Knowledge Model

### Core data structures (`app/rag/models.py`)

Three dataclasses form the entire knowledge hierarchy:

**`KnowledgeCollection`** — a logical container for related documents. Every tenant can have multiple collections, each with its own embedding model.

```python
@dataclass
class KnowledgeCollection:
    name: str
    description: str = ""
    collection_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    document_count: int = 0
    embedder: str = "voyage"   # voyage | openai | google | local
```

**`Document`** — a single ingested source before chunking. Stores the raw content and a SHA-256 hash for deduplication.

```python
@dataclass
class Document:
    collection_id: str
    source: str           # URL, file path, or connector reference
    content: str          # raw extracted text
    content_hash: str     # dedup key
    document_id: str = ...
    metadata: dict[str, str] = ...
```

**`Chunk`** — a sub-document fragment with its embedding vector. The primary unit of retrieval.

```python
@dataclass
class Chunk:
    document_id: str
    content: str
    embedding: list[float]
    chunk_index: int
    chunk_id: str = ...
    metadata: dict[str, str] = ...
    parent_chunk_id: str | None = None    # for parent-child chunking
    chunk_level: str = "leaf"             # "parent" | "child" | "leaf"
    window_start: int | None = None       # for sentence-window chunking
    window_end: int | None = None
```

### Storage: dual-path architecture

`KnowledgeStore` (`app/rag/store.py`) uses a dual-path strategy:

**In-memory path** (used in tests, dev, and as hot cache): A pure-Python dict of `collection_id → list[Chunk]`. Hybrid search uses cosine similarity (70% weight) + character trigram overlap (30% weight). Exact formula: `score = 0.7 * cosine(query_vec, chunk_vec) + 0.3 * trigram_score(query_text, chunk_text)`.

**Postgres path** (production): Writes are fire-and-forget asyncio tasks that persist to Postgres. The `hybrid_search_db()` method dispatches to `app/rag/engine.py:hybrid_search()` which runs server-side SQL with pgvector HNSW + pg_trgm + FTS.

When a `db_session_factory` is supplied to `KnowledgeStore.__init__`, the Postgres path is active. Without it, the store operates entirely in memory — this is the default for tests (no Docker required).

### Dynamic table routing

Chunk vectors are stored in dimension-specific tables to avoid wasting space on padding and ensure HNSW index efficiency:

| Table | Embedding dimension | Default provider |
|-------|---------------------|-----------------|
| `knowledge_chunks_1536` | 1536-dim | OpenAI text-embedding-3-large |
| `knowledge_chunks_1024` | 1024-dim | Voyage voyage-3 |
| `knowledge_chunks_768` | 768-dim | Local sentence-transformers |

The table is selected at query time by looking up the collection's `embedding_dim` column:

```sql
SELECT embedding_dim FROM knowledge_collections WHERE id = :cid LIMIT 1
```

(`app/rag/engine.py:79–89`). Falls back to 1536 if the column is null.

### Chunk flow: ingestion to retrieval

```
Raw content
    └─→ ContentClassifier.classify()     → ContentType
    └─→ ParserRegistry.get_parser()      → parser instance
    └─→ parser.parse()                   → text / metadata
    └─→ chunker.chunk()                  → list[Chunk (no embedding)]
    └─→ EmbeddingPolicySelector          → model choice
    └─→ embedder.embed()                 → list[float]
    └─→ KnowledgeStore.add_chunk()       → in-memory + Postgres fire-and-forget
    └─→ knowledge_chunks_{dim} table     → HNSW index, FTS, trgm index
    └─→ RetrievalEngine.retrieve()       → ranked list[RetrievalResult]
    └─→ ContextPipeline.run()            → deduplicated, reranked, token-budgeted context
    └─→ LLM prompt                       → cited answer
```

---

## Part B: The Ingestion Pipeline

### ContentClassifier (`app/ingestion/content_classifier.py`)

`ContentClassifier.classify(content: str)` detects content type from text using regex heuristics (no external library):

| Signal | Detection method |
|--------|-----------------|
| HTML | `<html|body|div|span|...>` pattern in first 500 chars |
| JSON | Leading `[` or `{` in first 20 chars |
| Code | `def `, `class `, `import `, `function `, `const `, `let `, `var `, `public class ` at line start |
| Markdown | `## `, `**`, `- `, numbered lists in first 500 chars |
| Text | Default fallback |

`classify_by_filename(filename)` maps extensions via `_EXT_MAP` — supports `.pdf`, `.docx`, `.doc`, `.html`, `.md`, `.py`, `.js`, `.ts`, `.java`, `.go`, `.rs`, `.cpp`, `.c`, `.rb`, `.sh`, `.sql`, `.png`, `.jpg`, `.mp3`, `.wav`, `.mp4`, `.mov`, `.csv`, `.tsv`, `.json`, `.jsonl`.

### 1. Text / Markdown

**Parser**: `TextParser` — reads the string directly; strips null bytes and normalizes line endings.  
**Chunker**: `SemanticChunker` (default) or `HeadingChunker` for markdown.  
**Metadata**: `{"source": url, "content_type": "text"}`.

`SemanticChunker` splits at paragraph boundaries (`\n\n`), accumulating until `max_chars=2048` (512 tokens × 4 chars/token). Oversize paragraphs fall back to sentence splitting (`(?<=[.!?])\s+`), then word splitting if a single sentence exceeds the limit. This preserves semantic units at every level.

`HeadingChunker` (`app/ingestion/chunkers/heading.py`) splits at `##` and deeper heading markers — ensuring each chunk maps to exactly one documentation section, preserving heading hierarchy in metadata.

### 2. PDF

**Parser**: `PDFParser` — three-tier fallback chain:
1. `pymupdf` (MuPDF) — fastest, preserves layout.
2. `pdfminer.six` — better for scanned PDFs with OCR text layer.
3. Plain text extraction — last resort for corrupt files.

Metadata preserved: `{"page_number": N, "source_pdf": filename}`.

**Chunker**: `PDFLayoutChunker` (`app/ingestion/chunkers/pdf_layout.py`) — inserts `[PAGE N]` markers at page boundaries, then splits by layout regions. This ensures citations can reference specific pages.

### 3. DOCX

**Parser**: `DOCXParser` — uses `python-docx` to iterate `document.paragraphs`. Preserves heading levels (`paragraph.style.name` → `Heading 1`, `Heading 2`), tables (serialized as markdown), and image alt-text.  
**Chunker**: `HeadingChunker` by default — aligns chunks with Word heading structure.

### 4. HTML

**Parser**: strips tags using `html.parser` from stdlib. Preserves `<title>`, `<meta description>`, and `<h1..h6>` tags as metadata.  
**Chunker**: `SemanticChunker` on cleaned text, with optional DOM-structure chunking (splits at block-level elements: `<article>`, `<section>`, `<div>`).

### 5. Code

**Parser**: `CodeParser` — normalizes indentation, removes copyright headers, preserves the raw code as-is.  
**Chunker**: `ASTChunker` (`app/ingestion/chunkers/ast_chunker.py`) — the primary chunker for Python; regex fallback for all other languages.

`ASTChunker._python_chunk()` walks the AST via `ast.parse()`, extracting every `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` as a separate chunk. Metadata includes `{"symbol_type": "function"|"class", "name": "<symbol_name>"}`. This guarantees that a chunk never splits a function body across boundaries.

`_regex_chunk()` fallback for non-Python: splits at `def |class |function |const |let |var |public class ` at line start using `_SYMBOL_PATTERN`. Each matched block becomes a chunk.

### 6. Images

**Parser**: `VisionParser` — encodes the image as base64 and sends to:
1. GPT-4o vision endpoint (primary).
2. Claude Vision (Anthropic) as fallback.

The response is a text description + embedded OCR output. Metadata: `{"original_format": "png|jpg|...", "vision_model": "gpt-4o"}`.

**Chunker**: `SemanticChunker` on the extracted description text.

### 7. Audio

**Parser**: `AudioParser` — transcribes via OpenAI Whisper API. Returns a transcript with optional word-level timestamps when the `verbose_json` response format is used.  
**Chunker**: `TimestampChunker` (`app/ingestion/chunkers/timestamp.py`) — splits at `[HH:MM:SS]` markers in the transcript. Each chunk covers a time window. Metadata: `{"timestamp_start": "00:01:23", "timestamp_end": "00:02:45"}`.

### 8. Video

**Parser**: `VideoParser` — two-phase:
1. `ffmpeg` extracts the audio track → `AudioParser` transcribes it.
2. `ffmpeg` extracts frames at regular intervals → `VisionParser` generates scene descriptions.

Output: interleaved transcript and scene descriptions.  
**Chunker**: `SceneChunker` (`app/ingestion/chunkers/scene.py`) — splits at `[SCENE N:]` markers inserted by the video parser. Metadata: `{"scene_number": N, "timestamp": "..."}`.

### 9. CSV / JSON

**CSV**: Each row group (configurable N rows per chunk) becomes one chunk. Column headers are repeated in each chunk for context.  
**JSON**: Record-level chunking — each top-level object or array element is a chunk. Nested structures are serialized with `json.dumps(indent=2)`.  
**Chunker**: `TableChunker` (`app/ingestion/chunkers/table.py`) — row-group strategy with header repetition.

### 10. GitHub

The GitHub MCP connector (`registry.py`) fetches repository content:
- File tree → code files dispatched to `CodeParser` + `ASTChunker`.
- Issues / PRs → text dispatched to `SemanticChunker`.
- README → `HeadingChunker`.

Collection is tagged with `{"source_type": "github", "repo": "owner/repo", "branch": "main"}`.

### 11. Jira

The Jira MCP connector fetches ticket content: summary, description, comments, attachments. Each ticket becomes one or more chunks via `SemanticChunker`. Metadata: `{"jira_key": "PROJ-123", "status": "Open", "priority": "High"}`.

### 12. Confluence

The Confluence MCP connector fetches page content (with full body including macros expanded to text) via the Confluence REST API. Chunked with `HeadingChunker`, preserving the page hierarchy in metadata. Metadata: `{"confluence_page_id": "...", "space_key": "...", "title": "..."}`.

### 13. Slack

The Slack MCP connector fetches message history from configured channels. Messages are grouped into time windows (1-hour blocks by default) and chunked via `TimestampChunker`. Metadata: `{"channel": "#general", "window_start": "...", "window_end": "..."}`.

### 14. Browser / RPA

The RPA module (`app/rpa/`) uses Playwright to:
1. Navigate to the target URL.
2. Extract DOM text via `page.content()` → `TextParser` → `SemanticChunker`.
3. Take a full-page screenshot → `VisionParser` for visual content.

Metadata: `{"source_url": url, "captured_at": ISO8601}`.

### Pipeline orchestration

`IngestionOrchestrator` (`app/ingestion/orchestrator.py`) is the unified entry point, wired to `POST /collections/{id}/documents` in the API router. Its pipeline:

1. **QualityChecker**: rejects documents with `len(content) < 20` or high noise ratio (excessive special characters, NUL bytes).
2. **ContentClassifier**: detects type.
3. **ParserRegistry**: routes type → parser instance.
4. **EmbeddingPolicySelector**: selects embedding model based on content type and collection size (larger collections use cheaper models to control cost).
5. **Chunker**: produces raw chunks.
6. **Embedder**: batch-embeds all chunks.
7. **KnowledgeStore.add_chunk()**: in-memory write + Postgres fire-and-forget.

**Failure handling**: individual chunk embedding failures are logged at WARNING level and skipped; the rest of the document proceeds. Parser errors are retried once; on second failure the document is marked `ingestion_failed` and a DB task records the error for async retry.

---

## Part C: The Nine RAG Patterns

### Pattern 1: Naive RAG

**Where**: `RetrieverTool.retrieve()` in `app/pipeline/retriever_tool.py`

The baseline: query → retrieve top-K chunks → inject as context → generate.

**Problem solved**: Zero-shot LLM hallucination on domain-specific knowledge.  
**When to use**: Well-structured knowledge base with high-quality, non-overlapping chunks.  
**When not to use**: When the knowledge base has coverage gaps, conflicting information, or requires synthesis across many sources.

**Flow**:
1. Embed the query.
2. Call `KnowledgeStore.hybrid_search()` (in-memory) or `engine.hybrid_search()` (Postgres).
3. Top-K chunks are formatted into a `[CONTEXT]` block and prepended to the executor system prompt.

---

### Pattern 2: Hybrid RAG

**Where**: `app/rag/engine.py:hybrid_search()` — the default for every retrieval call.

**Problem solved**: Pure vector search misses exact-match queries (IDs, names, codes); pure keyword search misses semantic meaning. Hybrid combines both.

**Implementation**: Three-leg Reciprocal Rank Fusion:

**Leg 1 — pgvector ANN**: HNSW index cosine search. `SET LOCAL hnsw.ef_search = 200` for high recall. Returns `top_k * 3` candidates.

**Leg 2 — PostgreSQL FTS**: `to_tsvector('english', content) @@ plainto_tsquery('english', :q)`. Scores via `ts_rank_cd` (BM25-like coverage-normalized ranking). Returns `top_k * 3` candidates.

**Leg 3 — pg_trgm fuzzy**: `similarity(content, :q)` with `content % :q` filter (similarity > 0.3 threshold). Returns `top_k * 2` candidates.

**RRF fusion**: For each unique chunk, the final score is:
```python
score = sum(1.0 / (60 + rank) for rank in ranks_this_chunk_appeared_in)
```
RRF constant `k=60` (standard value from Cormack et al. 2009). Final results sorted by RRF score descending.

**BM25 leg**: `app/rag/bm25.py` implements `BM25Retriever` using `rank_bm25.BM25Okapi` with `k1=1.5, b=0.75`. This is an optional fourth leg used for in-memory hybrid search when `rank_bm25` is installed. Falls back to simple TF scoring when the library is absent.

**Degradation**: When no embedding is available (`query_embedding=None`), engine drops the vector leg and runs FTS+trigram only (`retrieval_mode="lexical"`).

---

### Pattern 3: Fusion RAG

**Where**: `app/rag/engine.py:retrieve_fusion()` | Pattern adapter: `app/rag/agentic/patterns/fusion.py`

**Problem solved**: A single query formulation often misses relevant documents that use different terminology for the same concept. Fusion RAG expands the query into N variants and merges results.

**Implementation**:

1. `QueryExpander.expand_for_fusion_async()` generates N query variants (default 3) via LLM.
2. `asyncio.gather` runs `hybrid_search()` for each variant in parallel.
3. All results are merged with `rrf_fuse()` from `app/context/rerank_policy.py`.

Each variant gets its own embedding (when an embedder is available) or reuses the original query embedding as a best-effort fallback.

**When to activate**: PatternAssembler adds `fusion_rag` for `domain=ANALYTICAL and complexity!=SIMPLE` (MEDIUM priority). High-research, multi-angle questions benefit most.

---

### Pattern 4: Corrective RAG (CRAG)

**Where**: `app/rag/agentic/patterns/corrective.py` → `RetrieverTool.retrieve_corrective()`

**Problem solved**: Hybrid RAG retrieves chunks that are technically relevant but insufficiently authoritative or confident. CRAG evaluates retrieval quality and falls back to web search when the knowledge base cannot answer.

**Implementation**:

1. Hybrid retrieval produces an initial result set.
2. Each result is tagged with a confidence score (computed from RRF score distribution).
3. If `max_score < confidence_threshold` (default `0.5`) OR if the query contains gap phrases ("I don't know about X", "not found in context"): web search fallback fires.
4. Web results are merged with KB results using RRF.

**When to activate**: PatternAssembler adds `corrective_rag` for `domain=ANALYTICAL and risk in (HIGH, CRITICAL)` (MEDIUM). Also used when FLARE detects uncertainty (see below).

---

### Pattern 5: Self-RAG

**Where**: `app/rag/agentic/patterns/self_rag.py:SelfRAGPattern`

**Problem solved**: RAG always retrieves, even when the LLM already knows the answer. Self-RAG makes retrieval on-demand, reducing unnecessary context injection and latency.

**Based on**: Asai et al. 2023 "Self-RAG: Learning to Retrieve, Generate and Critique through Self-Reflection."

**Implementation** — four critique token steps (simulated via LLM prompts):

1. **`[Retrieve]`** — `_should_retrieve()`: calls the LLM with `_SHOULD_RETRIEVE_SYSTEM` to decide `{"should_retrieve": true/false, "reason": "..."}`. Factual questions, current events, and domain-specific data → retrieve. Math, simple reasoning, confident general knowledge → skip retrieval.

2. **`[ISREL]`** — after retrieval, evaluates whether the retrieved docs are relevant to the query.

3. **`[ISSUP]`** — evaluates whether the generated response is supported by the retrieved context.

4. **`[ISUSE]`** — evaluates whether the final response is useful for answering the query.

Returns `SelfRAGResult(answer, retrieved, is_relevant, is_supported, is_useful, confidence)`.

**When to activate**: Gated by `settings.enable_self_rag`. PatternSelector chooses `self_rag` strategy for queries that show mixed retrieval need signals.

---

### Pattern 6: Speculative RAG

**Where**: `app/rag/agentic/patterns/speculative.py:SpeculativeRAGPattern`

**Problem solved**: A single retrieval pass may miss the best framing. Speculative RAG generates multiple candidate answers in parallel with different retrieval framings and returns the one with the highest retrieval verification score.

**Implementation**:

1. Generate N candidate answers (default 2) via parallel LLM calls with varied framings.
2. For each candidate, retrieve context using the candidate as a query.
3. Score each (candidate, context) pair for mutual support.
4. Return the best-scoring candidate.

In `app/rag/engine.py:659`, `SpeculativeRAGPattern(n_candidates=2)` is used. The best candidate is returned as an enriched `RetrievalResult` with `score=0.9`.

---

### Pattern 7: FLARE (Forward-Looking Active Retrieval)

**Where**: `app/rag/agentic/patterns/flare.py:FLAREPattern`

**Problem solved**: Static RAG retrieves once at the start; FLARE retrieves *on demand* as the LLM generates, targeted at specific uncertain claims.

**Based on**: Jiang et al. 2023 "Active Retrieval Augmented Generation."

**Uncertainty signals** (18 phrases in `_UNCERTAINTY_SIGNALS`):
```python
{"i think", "i'm not sure", "might be", "could be", "possibly",
 "i believe", "unclear", "uncertain", "not certain", "may be",
 "perhaps", "i don't know", "i do not know", "[uncertain]",
 "i'm unsure", "hard to say", "not clear", "i am not sure"}
```

**Algorithm**:
1. Generate an initial response using `_FLARE_GENERATE_SYSTEM` (explicitly instructs the LLM to express uncertainty).
2. `_detect_uncertainty(text)` scans for signal phrases.
3. If uncertain: `_extract_uncertain_claim()` isolates the uncertain sentence.
4. Retrieve targeted context for that claim.
5. Re-generate using `_FLARE_REFINE_SYSTEM` with the retrieved evidence.
6. Repeat up to `max_iterations=2` (configurable).

**When to activate**: PatternAssembler adds `flare` for `requires_web=True or time_sensitivity in ("realtime", "recent")` (MEDIUM). Gated by `settings.enable_flare`.

---

### Pattern 8: RAPTOR

**Where**: `app/rag/agentic/patterns/raptor.py:RAPTORPattern`

**Problem solved**: For long documents (books, reports, codebases), individual chunk retrieval misses the high-level narrative. RAPTOR builds a hierarchical summary tree and retrieves from all levels.

**Based on**: Sarthi et al. 2024 "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval."

**Implementation**:

```
Leaf chunks (level 0)
  → cluster in groups of cluster_size (default 4)
  → LLM-summarize each cluster → level-1 summary nodes
  → cluster level-1 nodes → LLM-summarize → level-2 nodes
  → repeat until 1 node remains (root)
```

All clustering is done by simple grouping (no external clustering library required). LLM summaries use `_SUMMARIZE_SYSTEM`: "Summarize these chunks into one paragraph. Preserve key facts and relationships."

Summaries are built via `asyncio.gather` — all clusters at a given level are summarized in parallel.

At query time, retrieval is from *all* tree levels. In `app/rag/engine.py:696`, this is implemented by:
1. `hybrid_search()` on the leaf chunks for the original query.
2. `RAPTORPattern.execute()` builds the summary tree from those chunks.
3. The root-level summary is returned as a single `RetrievalResult(chunk_id="raptor_summary", score=0.95)` prepended to the leaf results.

**When to activate**: PatternAssembler adds `raptor` for `domain=ANALYTICAL and complexity in (COMPLEX, EXPERT)` (MEDIUM). Gated by `settings.enable_raptor`.

---

### Pattern 9: Agentic RAG

**Where**: `RetrieverTool` with `FallbackChain` strategy | `SearchDirectiveParser`

**Problem solved**: No single retrieval strategy is best for all queries. Agentic RAG gives the agent control over retrieval: it can choose KB search, web search, graph traversal, long-term memory recall, or parametric (LLM-only) responses.

**Implementation — FallbackChain**:

When the primary retrieval strategy returns below-threshold results, the chain tries alternatives in order:
```
hybrid → graph → hyde → web → long_term_memory → parametric
```

If `hybrid_search` returns `max_score < 0.4`, the next strategy fires automatically.

**SearchDirectiveParser**:

The agent can embed directives in its tool call arguments:
```
[SEARCH:kb:how to configure OAuth]
[SEARCH:web:latest LangGraph release notes]
[SEARCH:graph:dependencies of service X]
[SEARCH:memory:previous analysis of dataset Y]
```

`SearchDirectiveParser` extracts these tokens and routes to the corresponding retrieval backend.

**When to activate**: PatternAssembler adds `agentic_rag` for `complexity in (COMPLEX, EXPERT)` (MEDIUM). All COMPLEX/EXPERT goals get agent-controlled retrieval.

---

## Part D: Retrieval Strategies

All strategies are dispatched via the `retrieve()` function in `app/rag/engine.py`. The `RetrievalPlanner.select_strategy()` method (line 269) auto-selects based on query heuristics when no strategy is specified.

### RetrievalPlanner heuristics

| Query pattern | Strategy selected |
|---------------|------------------|
| `[A-Z][A-Z0-9]+-\d+` (Jira/GitHub IDs) | `lexical` |
| `\bticket\b|\bissue\b|\bpr\b` | `lexical` |
| `compare`, `analyze`, `contrast`, `difference between`, `across all`, `summarize all` | `multi_hop` |
| Starts with `what is`, `explain`, `how does`, `describe`, `tell me about`, `overview of` and `len < 80` | `hyde` |
| Everything else | `direct` (→ hybrid) |

### Strategy reference

| Strategy | Implementation | Description |
|----------|---------------|-------------|
| `hybrid` | `hybrid_search()` | 3-leg RRF (pgvector + FTS + trigram) — default |
| `lexical` | `hybrid_search(retrieval_mode="lexical")` | FTS + trigram only; skips vector leg |
| `vector` | `hybrid_search(retrieval_mode="vector")` | ANN only; no text legs |
| `direct` | `hybrid_search()` | Alias for hybrid |
| `hyde` | `retrieve_hyde()` | LLM generates hypothetical doc, searches with it |
| `multi_hop` | `retrieve_multi_hop()` | LLM decomposes query → parallel sub-queries → merge |
| `fusion` | `retrieve_fusion()` | Query expansion → N variants → parallel hybrid → RRF |
| `corrective` | hybrid + confidence tagging | Falls back to web when score < threshold |
| `flare` | `FLAREPattern` | Uncertainty-driven targeted retrieval |
| `self_rag` | `SelfRAGPattern` | On-demand retrieval with critique tokens |
| `speculative` | `SpeculativeRAGPattern` | N candidates + retrieval verification |
| `raptor` | `RAPTORPattern` | Hierarchical summary tree, multi-level context |
| `colbert` | `ColBERTPattern.rerank()` | MaxSim token-level reranking |
| `agentic_chunking` | `AgenticChunkingPattern` | LLM proposition extraction post-retrieval |
| `parametric` | Returns `[]` | LLM-only; no retrieval |
| `memory` | `LTM.recall_async()` | pgvector cosine on long-term memory store |
| `graph` | KG entity + path traversal | Falls back to hybrid if graph unavailable |

### HyDE (`retrieve_hyde`)

Generates a 2–3 sentence hypothetical document that would answer the query:
```
"Write a 2-3 sentence hypothetical document that would perfectly answer the following question."
```
Then runs `hybrid_search()` with the hypothetical document as the query string. This bridges the vocabulary gap between abstract questions and concrete document language. Falls back to standard hybrid if the LLM call fails.

### Multi-hop (`retrieve_multi_hop`)

Decomposes the query into 2–3 sub-queries via LLM (`"Return ONLY a JSON array: ["sub-query 1", ...]"`), runs `hybrid_search()` for each sub-query in series (to avoid redundant DB connections), deduplicates by `chunk_id`, and returns merged results sorted by score. Budget per hop: `top_k // max(num_sub_queries, 1)`, minimum 3.

### Fusion (`retrieve_fusion`)

Uses `QueryExpander.expand_for_fusion_async()` to generate up to `max_variants=3` alternative query formulations. All variants are searched in parallel via `asyncio.gather`. Results are merged using `rrf_fuse()` from `app/context/rerank_policy.py` (k=60, 1-indexed ranks).

---

## Part E: Chunking Strategies

### Strategy comparison

| Strategy | Class | File | Optimal for | Key size |
|----------|-------|------|-------------|----------|
| `semantic` | `SemanticChunker` | `chunkers/semantic.py` | Narrative text, articles | max 2048 chars (512 tokens) |
| `heading` | `HeadingChunker` | `chunkers/heading.py` | Documentation, markdown | 1 section per chunk |
| `ast` | `ASTChunker` | `chunkers/ast_chunker.py` | Python code | 1 function/class per chunk |
| `layout` | `PDFLayoutChunker` | `chunkers/pdf_layout.py` | PDFs with tables/columns | page-boundary aware |
| `timestamp` | `TimestampChunker` | `chunkers/timestamp.py` | Audio transcripts | `[HH:MM:SS]` window |
| `scene` | `SceneChunker` | `chunkers/scene.py` | Video content | `[SCENE N:]` window |
| `table` | `TableChunker` | `chunkers/table.py` | CSV, tabular data | N rows per chunk |
| `parent-child` | `ParentChildChunker` | `rag/parent_child_chunker.py` | High-precision KB | parent=1500, child=400 chars |
| `sentence-window` | `SentenceWindowChunker` | `rag/sentence_window.py` | Dense factual docs | ±2 sentence window |
| `agentic` | `AgenticChunkingPattern` | `rag/agentic/patterns/agentic_chunking.py` | High-precision extraction | LLM proposition |

---

### SemanticChunker (`app/ingestion/chunkers/semantic.py`)

**Algorithm**: Greedy paragraph accumulation. Split text at `\n\n`. Accumulate paragraphs into a chunk until adding the next paragraph would exceed `max_chars` (2048 default). At overflow, start a new chunk.

**Oversize handling** (3-level cascade):
1. Split oversized paragraph at sentence boundaries (`(?<=[.!?])\s+`).
2. If a single sentence exceeds `max_chars`, split by words.

**Why it works**: `\n\n` is a reliable semantic boundary in most text — it separates thoughts. The cascade ensures no chunk ever exceeds the token budget, which prevents silent truncation in the LLM context window.

**Metadata preserved**: `chunk_index`, nothing else (source metadata added by the orchestrator).

---

### HeadingChunker (`app/ingestion/chunkers/heading.py`)

**Algorithm**: Split at markdown heading markers (`^#{1,6}\s`). Each section (from one heading to the next) becomes a chunk. The heading itself is included in the chunk for context.

**Why it works**: Documentation and wikis are already structured by headings. Chunk-per-section ensures semantic coherence and enables exact-section retrieval.

**Metadata preserved**: `{"heading": "<heading text>", "heading_level": N}`.

---

### ASTChunker (`app/ingestion/chunkers/ast_chunker.py`)

**Python path**: `ast.parse(content)` → walk the AST → extract every `FunctionDef`, `AsyncFunctionDef`, `ClassDef` node. Uses `node.lineno` and `node.end_lineno` for precise extraction. Metadata: `{"symbol_type": "function"|"class", "name": "<symbol_name>"}`.

**Regex fallback** (non-Python): `_SYMBOL_PATTERN` splits at `def |class |function |const |let |var |public class ` at line start. Less precise but handles JavaScript, TypeScript, Java, Go, Ruby, and shell scripts.

**Why it works**: Code should never be chunked mid-function — the context window would contain incomplete logic. AST-level chunking preserves the minimum meaningful unit (a function or class).

---

### PDFLayoutChunker (`app/ingestion/chunkers/pdf_layout.py`)

**Algorithm**: Inserts `[PAGE N]` markers at page boundaries. Within each page, splits at layout regions (detected by pymupdf bounding box analysis: text blocks with vertical gaps > threshold). Each layout region becomes a candidate chunk; adjacent small regions are merged up to the size limit.

**Why it works**: PDFs with multi-column layouts, tables, and figures break naive paragraph splitting. Layout-aware chunking keeps table rows together and separates columns correctly.

**Metadata preserved**: `{"page_number": N, "region_type": "text"|"table"|"figure"}`.

---

### TimestampChunker (`app/ingestion/chunkers/timestamp.py`)

**Algorithm**: Splits the transcript at `[HH:MM:SS]` markers. Each time window becomes a chunk. The time range is stored in metadata.

**Why it works**: Retrieval for audio/video content typically needs to be jumped-to. Timestamp chunks enable "go to 12:34" style citations.

**Metadata preserved**: `{"timestamp_start": "HH:MM:SS", "timestamp_end": "HH:MM:SS"}`.

---

### SceneChunker (`app/ingestion/chunkers/scene.py`)

**Algorithm**: Splits at `[SCENE N:]` markers inserted by `VideoParser`. Each scene (visual + transcript) becomes one chunk.

**Metadata preserved**: `{"scene_number": N, "timestamp": "..."}`.

---

### ParentChildChunker (`app/rag/parent_child_chunker.py`)

**The "small-to-big" retrieval pattern**.

**Ingestion**:
1. Split document into large parent chunks (`parent_chunk_size=1500` chars, ~500 tokens).
2. Split each parent into small child chunks (`child_chunk_size=400` chars, ~130 tokens) with `child_overlap=50` chars.
3. Index **only child chunks** with embeddings (precise matching).
4. Store parent-child linkage via `parent_chunk_id` on each child.

**Retrieval**: When a child chunk is retrieved, the pipeline looks up its `parent_chunk_id` and returns the full parent content as context. This gives:
- **Precision of small chunks** during the ANN search.
- **Context richness of large chunks** during generation.

`ParentChunk.children` list enables reverse lookup without a join.

---

### SentenceWindowChunker (`app/rag/sentence_window.py`)

**Algorithm**:
1. Split text into individual sentences (`(?<=[.!?])\s+`).
2. Each sentence is a chunk.
3. The surrounding ±`window_size` sentences (default 2) are stored in `metadata["window_context"]`.

**Retrieval**: The precise sentence is matched; the window context is passed to the LLM for generation. This enables focused matching on the exact claim while preserving contextual flow.

**Why it works**: Dense factual documents (scientific papers, legal text) have precise claims in individual sentences. Sentence-level indexing maximizes recall for exact-fact queries.

---

### AgenticChunkingPattern (`app/rag/agentic/patterns/agentic_chunking.py`)

**Post-retrieval LLM proposition extraction**. Instead of splitting text at boundaries, an LLM decomposes each retrieved chunk into atomic, self-contained propositions (claims that are independently verifiable).

For each base chunk, the LLM generates up to `max_propositions=5` propositions. Each proposition becomes a new chunk. This yields retrieval units that map exactly to answerable questions.

**When to use**: High-precision KB queries where chunk boundaries are unpredictable (e.g., mixed-content enterprise documents). The LLM overhead is significant — only activate for the highest-value collections.

---

## Part F: Reranking and Citations

### ContextPipeline

Every retrieval result passes through `ContextPipeline.run()` before being injected into the LLM prompt. The pipeline has 7 sequential steps:

1. **Dedup** — removes chunks with identical content (exact string match on the first 200 chars).
2. **Rerank** — applies the configured `RerankPolicy` (see below).
3. **Filter** — drops chunks below `min_score` threshold.
4. **Diversity** — applies MMR if `RerankStrategy.DIVERSITY` is active.
5. **Token budget** — trims the result list so total token count fits the context window.
6. **CitationThreader** — attaches `[1]`, `[2]`, ... citation indices to each chunk and builds a `references` list.
7. **PromptBuilder** — formats the context block for the LLM.

### RerankPolicy (`app/context/rerank_policy.py`)

Five strategies:

| Strategy | How it works |
|----------|-------------|
| `SCORE` | Sort descending by raw RRF score. Default. |
| `RRF` | Re-apply RRF fusion to a single list (normalizes scores). |
| `CROSS_ENCODER` | Real cross-encoder scoring (see below). |
| `LLM` | Asks the LLM to score each passage 0–10 (batch prompt). |
| `DIVERSITY` | True MMR via pairwise cosine (see below). |

All strategies first: filter by `min_score`, deduplicate by content, then apply the strategy, then cap at `max_per_source=5` chunks per source URL to prevent single-source dominance.

### True MMR (`_diversity_rerank`)

**Maximal Marginal Relevance** maximizes the combination of relevance to the query and diversity from already-selected documents:

```
argmax_d [ λ · sim(d, q) − (1−λ) · max_{s ∈ S} sim(d, s) ]
```

`lambda_=0.5` by default (equal weight to relevance and diversity).

**With embeddings** (`_mmr_with_embeddings`): uses `_cosine()` to compute pairwise similarities using the real chunk embedding vectors. This is the correct implementation. Requires chunks to carry their `embedding` field.

**Without embeddings** (`_mmr_with_tokens`): falls back to Jaccard similarity over tokenized words. Less accurate but never fails.

### Real cross-encoder (`app/rag/cross_encoder.py`)

`cross-encoder/ms-marco-MiniLM-L-6-v2` — a 22MB model trained on MS MARCO passage ranking. Scores (query, passage) pairs using a bidirectional attention mechanism (correct cross-attention between query and passage tokens, not independent embeddings).

```python
model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
scores = model.predict([(query, doc[:512]) for doc in documents], batch_size=32)
```

Loaded lazily as a module-level singleton. Falls back to TF-IDF weighted token overlap when `sentence-transformers` is not installed or the model cannot load (network restrictions, etc.).

Called in `engine.py:rerank_results()` when `provider` is available — LLM scoring is used in that path; `cross_encode()` is called from `RerankPolicy._cross_encoder_rerank()`.

### ColBERT reranking (`app/rag/agentic/patterns/colbert.py`)

ColBERT (Khattab & Zaharia 2020) uses late interaction: instead of a single embedding per document, it produces **one embedding per token**. The final score is MaxSim: for each query token embedding, find the highest-similarity document token embedding, then sum across query tokens.

```python
def _maxsim_with_embeddings(query_emb, doc_emb) -> float:
    total = 0.0
    for q_tok_emb in query_emb:
        max_sim = max(cosine(q_tok_emb, d) for d in doc_emb)
        total += max_sim
    return total / len(query_emb)
```

**Production path**: `all-MiniLM-L6-v2` from sentence-transformers generates 384-dim token embeddings. Loaded as a module-level singleton.

**Fallback**: TF-IDF weighted token overlap with stopword filtering (`_STOPWORDS` set of 60 words).

Final score: `alpha * colbert_score + (1 - alpha) * base_score` where `alpha=0.5` (configurable). This blends the fast RRF base ranking with the precise ColBERT re-ranking.

In `engine.py:730`, ColBERT retrieves `top_k * 2` candidates from hybrid search and then ColBERT-reranks to `top_k` final results.

### Citations

**CitationManager** attaches inline citation indices (`[1]`, `[2]`, ...) to each retrieved chunk. These are carried through to the LLM prompt:

```
[CONTEXT]
[1] <chunk content>   Source: app/agent/graph.py
[2] <chunk content>   Source: README.md
...
```

The LLM is instructed to use `[N]` references inline in its response. The `AgentState.cited_answer` field stores the final response with inline citations. `AgentState.provenance` is a `list[dict]` of source attribution records: `[{"index": 1, "source": url, "chunk_id": "...", "page": N}, ...]`.

This provenance chain enables full audit of every claim in the agent's output back to its source document and chunk.

---

## Putting It All Together: A Complete Retrieval Flow

For a goal classified as `domain=ANALYTICAL, complexity=EXPERT, requires_web=False`:

1. **PatternAssembler** selects: `hybrid_rag`, `fusion_rag`, `raptor`, `corrective_rag`, `agentic_rag`.
2. **rag_retrieval node** fires: runs `agentic_rag` strategy (complex goal), which invokes `FallbackChain`.
3. **FallbackChain** tries `hybrid_search()` first → score threshold met → continues.
4. **RetrievalPlanner** sees `"compare"` in the query → selects `multi_hop` strategy.
5. **multi_hop** decomposes into 3 sub-queries → 3 parallel `hybrid_search()` calls.
6. Results merged, deduplicated.
7. **Fusion RAG** also runs in parallel (MEDIUM priority) → 3 query variants → 3 more `hybrid_search()` calls → `rrf_fuse()`.
8. **RAPTOR** builds a summary tree from the top-20 results → root summary prepended.
9. **ContextPipeline.run()**: dedup → SCORE rerank → filter(min_score=0.1) → diversity(MMR) → token budget (8192 tokens) → CitationThreader → PromptBuilder.
10. Final context block with `[N]` citations injected into the planner system prompt.
11. After generation, `AgentState.cited_answer` and `AgentState.provenance` populated.

---

## Operational Reference

### Key collection management APIs

| Endpoint | Action |
|----------|--------|
| `POST /collections` | Create a collection |
| `POST /collections/{id}/documents` | Ingest a document (triggers full pipeline) |
| `GET /collections/{id}/search?q=...` | Direct hybrid search |
| `DELETE /collections/{id}/documents/{doc_id}` | Remove document + all chunks |

### Performance tuning

| Parameter | Default | Guidance |
|-----------|---------|----------|
| `hnsw.ef_search` | 200 | Increase to 400 for higher recall at cost of latency |
| `top_k` | 10 | Increase for synthesis tasks, decrease for Q&A |
| `RRF_K` | 60 | Standard; increase to 120 to weight tail results less |
| `parent_chunk_size` | 1500 | Increase for long-form content; decrease for precise factual KB |
| `child_chunk_size` | 400 | Match your typical query length |
| `cluster_size` (RAPTOR) | 4 | Increase for very long docs; decrease for tight summaries |
| `n_thoughts` (ToT) | 3 | Increase for harder problems; decrease for latency |
| `n_samples` (Self-Consistency) | 3 | Odd numbers prevent ties |

### Tests

| Test file | Coverage |
|-----------|----------|
| `tests/rag/test_engine.py` | All retrieval strategies, RRF math, degradation paths |
| `tests/rag/test_bm25.py` | BM25Retriever, tokenization, fallback TF scoring |
| `tests/rag/test_store.py` | In-memory hybrid search, dual-path writes |
| `tests/rag/test_patterns.py` | FLARE uncertainty detection, RAPTOR tree building, Self-RAG critique tokens |
| `tests/rag/test_cross_encoder.py` | Model loading, TF-IDF fallback |
| `tests/rag/test_colbert.py` | MaxSim with/without sentence-transformers |
| `tests/ingestion/test_chunkers.py` | All 10 chunking strategies |
| `tests/ingestion/test_classifier.py` | ContentClassifier heuristics |
| `tests/context/test_rerank.py` | MMR with/without embeddings, RRF fusion, CROSS_ENCODER strategy |

Run the full RAG suite:
```bash
uv run pytest tests/rag/ tests/ingestion/ tests/context/ -v
uv run pytest tests/rag/ -m integration  # needs Docker + Postgres
```

---

## Part G: Complete Retrieval Engine Source Reference

### The `retrieve()` dispatcher (`app/rag/engine.py:~500`)

`retrieve()` is the single entry point for all retrieval strategies. Its full signature:

```python
async def retrieve(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    top_k: int = 10,
    strategy: str = "hybrid",
    ef_search: int = 200,
    provider: Any = None,           # needed for hyde, multi_hop, fusion, flare, self_rag, raptor
    embedding_dim: int | None = None,
    embedder: Any = None,           # needed for per-variant embeddings in fusion
    long_term_memory: Any = None,   # needed for "memory" strategy
    tenant_ctx: Any = None,         # needed for "memory" strategy
    retrieval_mode: str = "hybrid", # "hybrid" | "lexical" | "vector"
    metadata_filter: dict | None = None,
) -> list[RetrievalResult]:
```

Every strategy either delegates to `hybrid_search()`, a pattern class, or a dedicated function. Failures are caught individually — `retrieve()` never raises; it falls back to `hybrid_search()` on any exception (`engine.py:859`).

### Error handling philosophy

The engine follows the **graceful degradation** principle throughout:

```
colbert requested, sentence-transformers not installed → hybrid_search fallback
raptor requested, provider=None → hybrid_search on raw chunks (no summarization)
fusion requested, QueryExpander fails → single-variant hybrid_search
multi_hop requested, LLM returns invalid JSON → single original query hybrid_search
memory requested, LTM not available → returns []
graph requested, kg_store not injected → hybrid_search fallback + INFO log
```

This means retrieval never hard-blocks a goal. The tradeoff is that quality silently degrades — monitor `retrieve_dispatch_failed` log events to detect when better strategies should be configured.

### Reciprocal Rank Fusion: the math

RRF is the core fusion algorithm used across hybrid search, fusion RAG, and the rerank pipeline. Given multiple ranked lists, the score for document `d` is:

```
RRF(d) = Σ_{r ∈ rankings_containing_d}  1 / (k + rank(d, r))
```

With `k=60` (standard), a document ranked 1st in one list scores `1/61 ≈ 0.0164`. A document ranked 1st in *two* lists scores `2/61 ≈ 0.0328`. This means:

- Documents that appear in multiple retrieval legs are strongly promoted (the key benefit).
- Documents with excellent scores in one leg but absent from others are ranked moderately.
- The `k=60` constant flattens the curve — rank 1 vs rank 10 differ by much less than in pure score-based ranking.

The practical implication: **RRF rewards documents that are "good enough across all legs" over documents that are "perfect for one leg."** This is the right behavior for RAG — we want evidence that multiple retrieval signals agree on.

---

## Part H: Ingestion Deep-Dive

### QualityChecker filters

Before any chunk is stored, `QualityChecker` applies:

1. **Minimum length**: `len(content) < 20` → rejected. Prevents stub chunks like `"N/A"` or `"—"` from polluting the index.
2. **Noise ratio**: if >40% of characters are special characters (`!@#$%^&*()`), the chunk is rejected. Catches OCR artifacts from low-quality PDFs.
3. **Encoding issues**: checks for high density of replacement characters (`\ufffd`) which indicate encoding corruption.

### EmbeddingPolicySelector

Selects which embedding model to use based on:

| Condition | Model selected |
|-----------|---------------|
| `content_type=CODE` | `voyage-code-2` (code-optimized) |
| `content_type=IMAGE` | `text-embedding-3-large` (multimodal captions) |
| `collection.document_count > 10000` | Cheaper model (cost control) |
| `content_type=AUDIO or VIDEO` | `voyage-3` (optimized for transcripts) |
| Default | Collection's configured embedder |

This means a single collection can receive chunks from different embedding models if the policy selector overrides the collection default. The `embedding_dim` stored per-chunk ensures correct table routing.

### Deduplication

Before ingestion, `Document.content_hash` (SHA-256 of the raw content) is checked against existing documents in the collection. If a matching hash exists, the ingest is skipped and the existing document's `updated_at` timestamp is refreshed. This prevents duplicate chunks from appearing in retrieval.

For GitHub and Confluence connectors, the dedup key is based on the commit SHA or page version number, allowing content updates to be detected and re-ingested.

### Batch embedding

The ingestion orchestrator does not embed chunks one-at-a-time. It batches all chunks from a single document into a single `embedder.embed(EmbedRequest(texts=[...]))` call. The batch size is capped at 2048 texts to avoid API payload limits. Very large documents (>2048 chunks) are split into multiple batches with retry logic.

---

## Part I: The `ContextPipeline` in Detail

`ContextPipeline.run()` is the bridge between raw `RetrievalResult` objects and the formatted context string injected into the LLM prompt. Its 7 steps are strictly sequential.

### Step 1: Deduplication

Content-based: takes the first 200 characters of each chunk and adds to a `seen_content` set. Chunks with identical content (regardless of `chunk_id` or source) are dropped. This prevents the same text appearing twice from different connectors (e.g., Jira comment and Slack message with the same content).

### Step 2: Reranking

Applies the `RerankPolicy` (SCORE, RRF, CROSS_ENCODER, LLM, or DIVERSITY). Strategy is chosen by the `ContextPipeline` based on the collection's quality settings:

- **Free tier**: SCORE (cheapest).
- **Professional tier**: CROSS_ENCODER (best quality for top-50 candidates).
- **Enterprise tier**: LLM + DIVERSITY (maximum quality).

### Step 3: Min-score filter

Drops any chunk with `score < min_score`. Default `min_score=0.0` (no filtering). Configurable per collection. Setting `min_score=0.3` significantly reduces noise at the cost of potentially missing relevant but low-scoring chunks.

### Step 4: Diversity (MMR)

When `RerankStrategy.DIVERSITY` is active, `_diversity_rerank()` runs the full MMR algorithm. With `lambda_=0.5`, relevance and diversity are equally weighted. Increasing `lambda_` toward 1.0 prioritizes relevance; decreasing toward 0.0 maximizes diversity.

The embedding-based path (`_mmr_with_embeddings`) requires chunks to carry their `embedding` field — which they do when retrieved from Postgres but not always from the in-memory store. The token-overlap fallback (`_mmr_with_tokens`) uses Jaccard similarity on tokenized content.

### Step 5: Token budget enforcement

Counts tokens in accumulated chunks (using the 4 chars/token approximation) until the configured context window budget is exhausted. Default budget: 8192 tokens. Chunks are added in ranking order until the budget is hit; remaining chunks are dropped.

### Step 6: CitationThreader

Assigns `[1]`, `[2]`, ... indices to chunks in ranking order. Builds the `references` list:

```python
[
    {"index": 1, "chunk_id": "abc123", "source": "https://docs.company.com/api", "page": 3},
    {"index": 2, "chunk_id": "def456", "source": "app/agent/graph.py", "page": None},
]
```

These are stored in `AgentState.provenance` for the citation audit trail.

### Step 7: PromptBuilder

Formats the final context block:

```
[CONTEXT]
[1] <chunk content, up to 600 tokens>
   Source: https://docs.company.com/api (page 3)

[2] <chunk content>
   Source: app/agent/graph.py

You MUST cite sources inline using [N] notation where N is the source number above.
```

The LLM receives this block prepended to its system prompt. The final answer in `AgentState.cited_answer` contains inline `[N]` citations that map to the `provenance` list.

---

## Part J: Knowledge Graph Integration

### Graph strategy in `retrieve()`

When `strategy="graph"` is requested, `retrieve()` attempts to use `KGQueryEngine` from `app/state_runtime/kg_query_engine.py`. The knowledge graph stores entities and relationships extracted from documents during ingestion.

**Entity extraction**: During ingestion, the `KGBuilder` (wired in the orchestrator for ANALYTICAL collections) runs an LLM over each chunk to extract named entities (people, organizations, technical concepts, products) and their relationships. These are stored in a graph adjacency table.

**Graph retrieval**: `KGQueryEngine` translates the natural-language query into entity lookups, then traverses the graph to find related entities and their source chunks. This is especially powerful for questions like "What services depend on service X?" or "Who approved this architecture decision?" — questions where the answer is encoded in relationships, not just individual text chunks.

**Current status**: The KG store injection is marked as needing full wiring (`engine.py:841`). When the `kg_store` is not injected (most deployments), the graph strategy logs an INFO event and falls back to `hybrid_search`. Watch for `graph_strategy_requested_no_store_fallback` in logs to detect when full graph retrieval should be enabled.

---

## Part K: Memory-Augmented Retrieval

### Long-term memory (`strategy="memory"`)

When `strategy="memory"`, `retrieve()` calls `LongTermMemoryStore.recall_async()`. This performs a pgvector cosine similarity search over the `long_term_memories` table — which stores:

1. **Goal learnings**: facts extracted from successful goal runs (what worked, what was found).
2. **RPA extractions**: text extracted from web pages during RPA tool calls (stored by `graph.py:2787`).
3. **Explicit memories**: facts the user explicitly told the agent via `POST /memory`.

Each memory record has: `content`, `embedding`, `memory_type`, `confidence`, `source_goal_id`, `tenant_id`.

The `memory` strategy is invoked in the **FallbackChain** when all other strategies fail to find relevant knowledge — it's the last resort before `parametric` (LLM-only). Memory recall uses `top_k` (default 10) cosine similarity hits, returned as `RetrievalResult` objects with `retrieval_legs=["long_term_memory"]`.

### SemanticCache

`SemanticCache` (`app/rag/semantic_cache.py`) caches at the *query level*, not the chunk level. When a new query arrives, it is embedded and compared against cached query embeddings. If cosine similarity > threshold (default 0.92), the cached retrieval results are returned immediately — no DB queries.

This is distinct from `LLMResponseCache` which caches at the LLM response level. SemanticCache can return the same chunks for slightly different phrasings; LLMResponseCache returns the same complete answer.

---

## Part L: Ingestion and Retrieval Configuration Recipes

### Recipe: Code knowledge base (high-precision code search)

```
Content type: CODE
Parser: CodeParser
Chunker: ASTChunker (Python) / regex fallback (JS/TS)
Embedder: voyage-code-2
Retrieval strategy: colbert (MaxSim for token-level code matching)
Reranking: CROSS_ENCODER
Context pipeline: min_score=0.2, diversity=False (code duplication acceptable)
```

Why ColBERT for code? Code queries often match specific identifier names that appear as tokens. MaxSim scoring finds the document where at least one token exactly matches a query token — better than bag-of-words for identifier lookup.

### Recipe: Large document corpus (books, reports)

```
Content type: PDF/DOCX
Parser: PDFParser / DOCXParser
Chunker: ParentChildChunker (parent=1500, child=400)
Additional: SentenceWindowChunker for dense factual sections
Embedder: voyage-3 (1024-dim for smaller table)
Retrieval strategy: raptor (for synthesis) or multi_hop (for comparison)
Reranking: DIVERSITY (MMR, lambda=0.6)
Context pipeline: min_score=0.15, token_budget=12000
```

Why parent-child? For a 400-page book, individual sentence chunks are precise for matching but lose context. Parent-child retrieval returns the full section (1500 chars) while matching at the paragraph level (400 chars).

### Recipe: Real-time data (news, Slack, live dashboards)

```
Content type: TEXT/JSON
Ingestion: triggered by webhook from Slack connector / RSS feed
Chunker: TimestampChunker or SemanticChunker
Retrieval strategy: flare (uncertainty-driven) or corrective_rag (web fallback)
PatternAssembler trigger: requires_web=True, time_sensitivity="realtime"
→ web_auto_activate=True, web_augmented_rag and flare added automatically
```

For real-time data, the KB is supplemented by web search. FLARE generates initial answers from the KB and then retrieves web content specifically for uncertain claims — minimizing hallucination on time-sensitive facts.

### Recipe: Multi-tenant isolation

Every KnowledgeCollection, Document, and Chunk record carries `tenant_id`. PostgreSQL Row-Level Security (`app/db/rls.py`) enforces tenant isolation at the database layer using `SET LOCAL app.tenant_id = :tid` via `rls_context()`. Even if application code has a bug that omits a `WHERE tenant_id = :tid` filter, the RLS policy prevents cross-tenant data leakage.

The `KnowledgeStore` in-memory path does *not* use RLS — it relies on application-level filtering by `collection_id` (which is always tenant-scoped). This is acceptable because the in-memory store is only used in tests and single-tenant dev environments.

---

## Part M: Debugging the RAG Pipeline

### How to tell which retrieval leg fired

Every `RetrievalResult` carries `retrieval_legs: list[str]`. Values:

- `["vector"]` — only pgvector ANN hit.
- `["fts"]` — only FTS hit.
- `["trgm"]` — only trigram hit.
- `["vector", "fts", "trgm"]` — appeared in all three legs (highest RRF score possible).
- `["colbert"]` — ColBERT reranking applied.
- `["raptor"]` — RAPTOR summary result.
- `["long_term_memory"]` — recalled from LTM.
- `["speculative"]` — speculative RAG candidate.

Log the `retrieval_legs` distribution in production. If you see mostly `["fts"]` and rarely `["vector"]`, the embedding provider may be misconfigured or the HNSW index may need re-indexing.

### Why hybrid search returns no results

Common causes:
1. **Empty collection**: `document_count=0` in `KnowledgeCollection`.
2. **Wrong `embedding_dim`**: if the collection's `embedding_dim` is 1024 but the table is `knowledge_chunks_1536`, queries return 0 results. Check `SELECT embedding_dim FROM knowledge_collections WHERE id = :cid`.
3. **FTS language mismatch**: FTS uses `to_tsvector('english', ...)`. Non-English content needs the appropriate language configuration.
4. **pg_trgm not installed**: the trigram leg silently fails (`logger.debug("trgm_leg_failed")`). Install `CREATE EXTENSION pg_trgm`.
5. **Vector leg only, `query_embedding=None`**: if the embedder is not configured, `query_embedding` is None and the vector leg is skipped. Check `VOYAGE_API_KEY` / `OPENAI_API_KEY` env vars.

### Diagnosing low-quality retrieval

1. **Enable CROSS_ENCODER reranking** — the most reliable quality improvement. Requires `sentence-transformers` installed. Verify with `is_cross_encoder_available()`.
2. **Check chunk sizes** — if chunks are >1500 chars, the embedding covers too much ground. Reduce `max_chunk_tokens` in `SemanticChunker`.
3. **Check for stale chunks** — re-ingest after document updates. There is no automatic change detection for file-based sources.
4. **Add BM25** — install `rank_bm25` for the true Okapi BM25 fourth leg in in-memory search.
5. **Try parent-child** — if precision is low, the flat chunking strategy is likely the bottleneck.

### RAPTOR not producing better results

RAPTOR requires `provider` to be passed to `retrieve()`. Without a provider, RAPTOR falls back to plain `hybrid_search`. Also, RAPTOR builds its tree from the `top_k * 2` results of `hybrid_search` — if the base retrieval is poor, the summaries will be poor. Fix the base retrieval first.

### ColBERT fallback detection

If `is_cross_encoder_available()` returns False but ColBERT is requested, ColBERT silently falls back to TF-IDF token overlap scoring. The `colbert_score` in the result metadata will be in the range 0.0–0.5 (TF-IDF) rather than 0.0–1.0 (true MaxSim). Monitor the `colbert_score` distribution to detect when the model is unavailable.

---

## Quick Reference

### Which chunker for which content?

| Content type | Primary chunker | When to switch |
|-------------|----------------|----------------|
| Blog posts, documentation | SemanticChunker | → HeadingChunker if structured |
| Markdown with clear sections | HeadingChunker | — |
| Python code | ASTChunker | — |
| Non-Python code | ASTChunker (regex path) | — |
| Scanned PDFs | PDFLayoutChunker | — |
| Audio transcripts | TimestampChunker | — |
| Video | SceneChunker | — |
| Tabular data | TableChunker | — |
| Dense factual text (papers, legal) | SentenceWindowChunker | — |
| High-value enterprise docs | ParentChildChunker | — |
| Any (highest precision) | AgenticChunkingPattern | Only when LLM cost is acceptable |

### Which reranking strategy for which scenario?

| Scenario | Recommended strategy | Why |
|----------|---------------------|-----|
| Default / cost-sensitive | SCORE | Zero overhead, reasonable results |
| Developer / code search | CROSS_ENCODER | Precise (query, passage) scoring |
| Research synthesis | DIVERSITY (MMR, λ=0.5) | Avoids redundant evidence |
| Ambiguous queries | LLM | Most flexible, highest cost |
| Already-fused results | RRF | Normalizes mixed score distributions |

### Which retrieval strategy for which query type?

| Query type | Best strategy | Runner-up |
|-----------|--------------|-----------|
| "What is X?" | hyde | hybrid |
| "Compare A and B" | multi_hop | fusion |
| Find ticket JIRA-123 | lexical | — |
| Summarize all recent changes | fusion | multi_hop |
| Real-time question | flare | corrective |
| Factual from long docs | raptor | multi_hop |
| Code symbol lookup | colbert | lexical |
| Recall previous work | memory | hybrid |
| No KB, LLM only | parametric | — |
| High-accuracy factual | self_rag | corrective |

---

## Part N: BM25 — The Fourth Retrieval Leg

`app/rag/bm25.py` implements true Okapi BM25 retrieval over an in-memory chunk collection. While the three primary legs in `engine.py:hybrid_search()` are Postgres-based, `BM25Retriever` provides an additional scoring dimension when `rank_bm25` is installed.

### Why Okapi BM25 matters

PostgreSQL FTS (`to_tsvector + ts_rank_cd`) approximates BM25 behavior but is not a true Okapi BM25 implementation. Key differences:

| Feature | PostgreSQL FTS | Okapi BM25 |
|---------|---------------|-----------|
| Term frequency saturation | Not explicit | k1=1.5 parameter |
| Document length normalization | Approximate | b=0.75 parameter |
| IDF computation | Approximate | Exact from corpus |
| Partial token match | Yes (stemming) | No (exact tokens) |

True BM25 outperforms PostgreSQL FTS on exact phrase queries and technical identifiers. The `k1=1.5` term saturation parameter prevents high-frequency terms from dominating; `b=0.75` normalizes for document length — important when chunk sizes vary across a collection.

### BM25Retriever internals

`_tokenize()` (`bm25.py:16`) uses `re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())` — lowercase alphanumeric word extraction without stemming. The no-stemming choice is intentional: code identifiers like `AgentGraph` and `_node_verify` must not be collapsed to the same stem as generic words.

`BM25Retriever.search()` returns only positive-scoring results (chunks where at least one query token appears) sorted by BM25 score descending. An empty result is valid and handled gracefully by the hybrid search fusion.

**Fallback**: when `rank_bm25` is not installed, `index()` sets `self._bm25 = None` and `search()` falls back to simple term-frequency scoring. This is weaker but never hard-fails. Install the true BM25 leg with:

```bash
uv add rank_bm25 && uv sync
```

---

## Part O: Embedding Dimensions and HNSW Index Health

### Dynamic table routing in detail

`engine.py:79–89` queries `knowledge_collections.embedding_dim` at every retrieval call. This lookup is fast (primary key scan) but adds one DB round-trip. For very high-throughput deployments, consider caching the `embedding_dim` in application memory (e.g., a per-collection dict in `KnowledgeStore`) to avoid the extra query.

The three supported tables correspond to major embedding provider dimensions:

| Table | Provider | Model |
|-------|---------|-------|
| `knowledge_chunks_1536` | OpenAI | text-embedding-3-large |
| `knowledge_chunks_1024` | Voyage | voyage-3, voyage-code-2 |
| `knowledge_chunks_768` | Local | all-MiniLM-L6-v2 (sentence-transformers) |

A single tenant may have collections at different dimensions (e.g., code collection at 1024 with `voyage-code-2`, documentation collection at 1536 with OpenAI). The dynamic routing handles this transparently.

### HNSW index parameters

The pgvector HNSW index on each chunk table is built with:

```sql
CREATE INDEX ON knowledge_chunks_1536
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

`m=16` and `ef_construction=64` are conservative defaults. For higher recall at the cost of memory and build time, use `m=32, ef_construction=128`. At query time, `ef_search=200` provides high recall; reduce to 50–100 for latency-sensitive chatbot applications.

### Index health monitoring

| Metric | SQL query | Alert threshold |
|--------|-----------|----------------|
| Chunk count | `SELECT COUNT(*) FROM knowledge_chunks_1536 WHERE collection_id = :cid` | Unexpected drop |
| Null embeddings | `SELECT COUNT(*) FROM knowledge_chunks_1536 WHERE embedding IS NULL` | Any >0 |
| Dead tuples | `SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname LIKE 'knowledge_chunks_%'` | >10% of live tuples |
| Index size | `SELECT pg_indexes_size('knowledge_chunks_1536_embedding_idx')` | Monitor growth rate |

Null embeddings indicate a failed embedding API call not caught by the orchestrator's retry. Re-ingest the affected document. Dead tuples from document re-ingestion accumulate over time — schedule `VACUUM ANALYZE knowledge_chunks_1536` weekly.

---

## Part P: Advanced Ingestion Patterns

### Incremental re-ingestion

`Document.content_hash` (SHA-256 of raw content) enables efficient incremental updates. On re-ingestion:
1. New content hash computed.
2. Hash matches stored hash → update `updated_at` only, skip chunking and embedding entirely.
3. Hash differs → delete all old chunks for this `document_id`, re-chunk, re-embed, insert.

For very large documents (books, codebases), consider splitting at a higher level (chapters, modules) so only changed sections require re-embedding.

### Webhook-triggered real-time ingestion

For Slack, GitHub, and Jira connectors, ingestion is triggered asynchronously via Celery. The task queue routing (`app/scaling/celery_app.py`) uses per-plan priority: enterprise tenant webhooks go to `goals.enterprise`, isolating them from free-tier traffic. This means a spike in free-tier ingestion cannot delay enterprise real-time updates.

### Multi-language content

The ingestion pipeline detects content type but not language. For non-English content:
- Change FTS configuration: `to_tsvector('french', content)` in schema.
- Use a multilingual embedding model (Voyage and OpenAI models are multilingual by default).
- The trigram and BM25 legs are language-agnostic (operate on characters and tokens respectively).

`ContentClassifier` limitation: language detection is not implemented — it only classifies format. Adding language detection would require an external library (`langdetect` or `fasttext`) or an LLM call during ingestion.
