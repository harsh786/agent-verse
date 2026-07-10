# AgentVerse: Multimodal Pipeline and AI Model Routing

> **Coverage:** `app/ingestion/parsers/`, `app/rag/`, `app/ai_router/`, `app/agent/model_router.py`, `app/context/`
> **Audience:** Engineers integrating new content types, tuning cost profiles, or adding LLM providers.

---

## Table of Contents

- [Part A — Multimodal Ingestion Pipeline](#part-a--multimodal-ingestion-pipeline)
  - [1. Text](#1-text)
  - [2. Markdown](#2-markdown)
  - [3. PDF](#3-pdf)
  - [4. DOCX](#4-docx)
  - [5. HTML](#5-html)
  - [6. Code](#6-code)
  - [7. Images](#7-images)
  - [8. Audio](#8-audio)
  - [9. Video](#9-video)
  - [10. Screenshots (RPA)](#10-screenshots-rpa)
  - [11. Tables](#11-tables)
  - [12. CSV and JSON](#12-csv-and-json)
  - [ContentType → Full Pipeline Table](#contenttype--full-pipeline-table)
  - [Multimodal Limitations and Dependencies](#multimodal-limitations-and-dependencies)
  - [Agentic Chunking](#agentic-chunking)
  - [Cross-Encoder Reranking](#cross-encoder-reranking)
  - [BM25 Retrieval](#bm25-retrieval)
  - [ColBERT Late Interaction](#colbert-late-interaction)
- [Part B — AI Model Router](#part-b--ai-model-router)
  - [1. ModelOrchestratorAdapter](#1-modelorchestratoradapter)
  - [2. ModelOrchestrator.select_models()](#2-modelorchestratorselect_models)
  - [3. Tier Model Tables](#3-tier-model-tables)
  - [4. ModelRouter](#4-modelrouter)
  - [5. AIRouter](#5-airouter)
  - [6. RAG Pattern → Model Implications](#6-rag-pattern--model-implications)
  - [7. Provider Circuit Breaker](#7-provider-circuit-breaker)
  - [8. Multimodal Model Selection](#8-multimodal-model-selection)
  - [9. Cost-Aware Routing](#9-cost-aware-routing)
  - [Production Wiring Status](#10-production-wiring-status)

---

## Part A — Multimodal Ingestion Pipeline

AgentVerse supports twelve content types. Each follows the same five-stage pipeline:

```
Raw bytes / URL
      │
  [1] Parser          → Extracts structured content, preserves layout metadata
      │
  [2] Chunker         → Splits into retrievable segments with appropriate overlap
      │
  [3] Embedder        → EmbeddingOrchestrator selects model per content type + tenant plan
      │
  [4] Storage         → knowledge_chunks_{dim} table (pgvector), BM25 in-memory index
      │
  [5] Retrieval       → PatternSelector routes to RAG strategy; cross-encoder reranks
```

The output of every retrieval path is a `list[dict]` with at minimum `content`, `score`, and `source_metadata` keys, passed to `ContextPipeline.run()` for the 7-step prompt assembly.

---

### 1. Text

**Parser:** Built-in string handling (no dedicated parser class)  
**Chunker:** `SemanticChunker` — splits on sentence boundaries, targets ~400–600 token windows with ~50-token overlap  
**Embedding:** `text-embedding-3-small` (1536-dim) on free/starter plans; `text-embedding-3-large` (3072-dim) on enterprise  
**Storage:** `knowledge_chunks_1536` or `knowledge_chunks_3072` pgvector table  
**Retrieval:** Hybrid (pgvector ANN + BM25) → cross-encoder reranker

Plain text is the baseline content type. The semantic chunker attempts to preserve sentence boundaries so that each chunk is grammatically complete and semantically self-contained. This avoids the common failure mode where a chunk cut mid-sentence produces a meaningless fragment.

**Chunk metadata keys:**
```json
{
  "content": "...",
  "chunk_index": 0,
  "source_name": "filename.txt",
  "content_type": "text"
}
```

---

### 2. Markdown

**Parser:** Built-in text handling  
**Chunker:** `HeadingChunker` — splits at `##` (H2) heading boundaries  
**Embedding:** Same as text  
**Storage:** Same as text  
**Retrieval:** Hybrid + cross-encoder

Markdown is split at heading boundaries rather than sentence boundaries. Each section becomes a chunk that inherits the heading as its semantic label. This preserves document structure — a `## Authentication` section stays together rather than being split across two semantic window chunks. The heading text is prepended to each chunk's content for retrieval quality.

---

### 3. PDF

**File:** `app/ingestion/parsers/pdf_parser.py` — `PDFParser`, `PDFParseResult`, `PDFPage`

PDF is the most complex text format due to the multi-library fallback chain:

```
parse_bytes(pdf_bytes)
      │
      ├── _parse_with_pymupdf()    (fitz — preserves page structure, tables, images)
      │         If fails (ImportError or parse error)
      │
      ├── _parse_with_pdfminer()   (pdfminer.six — character-level extraction)
      │         If fails
      │
      └── parse_text()             (raw UTF-8 decode → paragraph splitting)
```

**pymupdf** (`fitz`) is the preferred parser. It:
- Extracts text per page with `page.get_text("text")`
- Preserves `PDFPage.width`, `PDFPage.height` (layout metadata)
- Sets `has_tables`, `has_images` flags

**pdfminer** is the fallback for PDFs that `pymupdf` cannot parse (encrypted, malformed, unusual encoding).

**Text fallback** decodes bytes as UTF-8 and splits on `\n\n`, grouping into 3000-char pages.

**Chunker:** `PDFLayoutChunker` (table detection + page-level chunks). `PDFParseResult.to_chunks()` produces one chunk per page:

```json
{
  "content": "<page text>",
  "chunk_index": 0,
  "page_number": 1,
  "source_name": "document.pdf",
  "content_type": "pdf"
}
```

If all pages are empty (image-only PDF), the full text fallback produces a single concatenated chunk.

**Embedding / Storage / Retrieval:** Same as text.

---

### 4. DOCX

**File:** `app/ingestion/parsers/docx_parser.py` — `DOCXParser`, `DOCXParseResult`

Word documents are parsed using the `python-docx` library:

```python
from docx import Document
doc = Document(io.BytesIO(docx_bytes))
for para in doc.paragraphs:
    if para.style.name.startswith("Heading"):
        headings.append(para.text)
    paragraphs.append(para.text)
```

**Headings** are extracted as a separate list (`DOCXParseResult.headings`) for use by the heading-aware chunker.

**Chunker:** Paragraph-boundary chunking within `to_chunks()`. Paragraphs are accumulated until the cumulative length exceeds 2000 chars, then a new chunk starts. This produces chunks of approximately 500–600 words.

**ImportError fallback:** If `python-docx` is not installed, the parser falls back to UTF-8 decoding the raw bytes and splitting on `\n\n`. This will include binary artifacts but is better than failing.

**Chunk metadata:**
```json
{
  "content": "...",
  "chunk_index": 0,
  "source_name": "document.docx",
  "content_type": "docx"
}
```

---

### 5. HTML

**Parser:** Tag-stripping via regex or `html.parser`  
**Chunker:** `SemanticChunker` (same as text after stripping)  
**Embedding / Storage / Retrieval:** Same as text

HTML is pre-processed to remove tags, script, and style blocks before chunking. The stripped text is then treated identically to plain text. Structured elements like `<h1>`–`<h6>` may optionally be preserved as chunk boundaries when the heading chunker is applied.

---

### 6. Code

**Parser:** `CodeParser` — language detection + AST-based splitting (Python); regex-based for JS/Java/Go  
**Fallback parser:** `PDFParser` (for code embedded in documentation PDFs)  
**Chunker:** `ASTChunker`:
- Python: `ast.parse()` → function/class boundary splits
- JS/Java/Go: Regex-based function detection (`function\s+\w+\s*\(`, `def\s+\w+\s*\(`, etc.)

**Embedding:** `voyage-code-3` (1024-dim) on plans where available; falls back to `text-embedding-3-small`

Code requires specialized chunking because semantic sentence boundaries are meaningless for source code. A function is the atomic unit of code retrieval — splitting mid-function produces unchunkable fragments. The `ASTChunker` ensures each chunk is a complete syntactic unit (function, class, or top-level block).

**Retrieval:** ColBERT late interaction (`app/rag/agentic/patterns/colbert.py`) is the preferred retrieval strategy for code goals. The `PatternSelector` routes `goal_type == "code"` to ColBERT because token-level MaxSim scoring captures identifier and keyword matches that dense embeddings smooth over.

---

### 7. Images

**File:** `app/ingestion/parsers/vision_parser.py` — `VisionParser`, `VisionParseResult`

Images are ingested by converting them to natural language descriptions via a vision-capable LLM.

#### Parse flow

```python
parse_image_bytes(image_bytes, source_name, prompt="Describe this image in detail.")
      │
      ├── _detect_image_mime(image_bytes)  →  "image/png" | "image/jpeg" | "image/gif" | "image/webp"
      │         Magic bytes: PNG (\x89PNG), JPEG (\xff\xd8\xff), GIF (GIF87a/GIF89a), WebP (RIFF+WEBP)
      │
      ├── base64.standard_b64encode(image_bytes)
      │
      ├── Provider preference chain (prefer_provider="openai"):
      │       1. openai:  gpt-4o vision API (data URL: data:{mime};base64,{b64})
      │       2. anthropic: Claude Vision (base64 image block)
      │       ── On all failures → VisionParseResult(description="[Image: filename]")
      │
      └── VisionParseResult(description=<LLM description>, model_used=<provider>)
```

**to_chunks()** produces a single chunk:
```json
{
  "content": "<LLM description>",
  "chunk_index": 0,
  "source_name": "photo.jpg",
  "content_type": "image",
  "model_used": "openai"
}
```

The description replaces the image for all downstream processing — it is embedded as text and retrieved via standard text similarity. This means image content is searchable by description, not by visual similarity.

**Screenshot RPA path:** `Playwright page.screenshot()` → bytes → `VisionParser.parse_image_bytes()` → description stored in `LongTermMemoryStore.store_rpa_extraction()` with `source_type="rpa_vision"` and tags `["rpa", "vision", "screenshot-analysis"]`.

---

### 8. Audio

**File:** `app/ingestion/parsers/audio_parser.py` — `AudioParser`, `AudioParseResult`, `AudioSegment`

Audio files are transcribed using the OpenAI Whisper API and chunked by time window.

#### Parse flow

```python
AudioParser(chunk_duration_seconds=60.0)
      │
parse_bytes(audio_bytes, source_name, mime_type="audio/mpeg")
      │
      └── OpenAI Whisper API:
            client.audio.transcriptions.create(
                model="whisper-1",
                file=(source_name, audio_bytes, mime_type),
                response_format="verbose_json",   # returns segments with timestamps
                timestamp_granularities=["segment"]
            )
            → {transcript, segments: [{start, end, text}, ...]}
```

**`verbose_json` response:** Includes `segments[]` with `start` and `end` timestamps in seconds. These are captured as `AudioSegment(start, end, text)` objects.

**TimestampChunker:** `to_chunks(chunk_duration_seconds=60.0)` groups segments into time windows. A new chunk starts when the accumulated duration since the window start exceeds `chunk_duration_seconds`. Each chunk carries `start_time` and `end_time` metadata in `HH:MM:SS` format:

```json
{
  "content": "Welcome to the meeting. Today we will discuss...",
  "chunk_index": 0,
  "start_time": "00:00:00",
  "end_time": "00:01:03",
  "source_name": "meeting.mp3",
  "content_type": "audio"
}
```

When segments are not available (Whisper returned plain text only), a single chunk is created from the full transcript.

**Embedding / Storage / Retrieval:** Transcript chunks are treated as text — embedded with the standard text embedding model and stored in the text pgvector table.

---

### 9. Video

**File:** `app/ingestion/parsers/video_parser.py` — `VideoParser`, `VideoParseResult`

Video is a two-stage pipeline: audio extraction then transcription.

#### Parse flow

```python
parse_bytes(video_bytes, source_name)
      │
      └── _extract_and_transcribe(video_bytes, filename)
                │
                ├── Write MP4 to tempfile  (tempfile.NamedTemporaryFile)
                │
                ├── ffmpeg audio extraction:
                │     ffmpeg -i {video_path} -q:a 0 -map a {audio_path} -y -loglevel quiet
                │     Timeout: 120 seconds
                │
                ├── If ffmpeg succeeds AND audio file exists:
                │     AudioParser().parse_file_path(audio_path) → AudioParseResult
                │     transcript = audio_result.transcript
                │
                └── If ffmpeg fails:
                      transcript = "[Audio extraction failed for {filename}]"
```

**Scene descriptions:** `VideoParseResult.scene_descriptions` is a list that can be populated by a separate frame-sampling pipeline (not yet wired in the base `VideoParser`). When populated, each description becomes a separate chunk with `chunk_type: "scene_description"`.

**`to_chunks()` output:**

```json
[
  {
    "content": "[Transcript]\nWelcome to the demo...",
    "chunk_index": 0,
    "source_name": "demo.mp4",
    "content_type": "video",
    "chunk_type": "transcript"
  },
  {
    "content": "Speaker is pointing at a whiteboard diagram showing...",
    "chunk_index": 1,
    "source_name": "demo.mp4",
    "content_type": "video",
    "chunk_type": "scene_description",
    "scene_index": 0
  }
]
```

If the video has no audio track and no scene descriptions, a placeholder chunk is returned: `"[Video: {source_name}]"`.

---

### 10. Screenshots (RPA)

**Flow:** `Playwright page.screenshot()` → `bytes` → `VisionParser.parse_image_bytes()` → `LongTermMemoryStore.store_rpa_extraction()` → semantic recall

RPA screenshots go through the vision parser (same as image ingestion) but are stored in LTM rather than the knowledge base. This is intentional: scraped page content is ephemeral and belongs to the agent's working experience rather than the tenant's permanent knowledge.

The `store_rpa_extraction()` method in `LongTermMemoryStore` adds specific tags:
- Regular extraction: `["rpa", "web-extraction"]`
- Vision extraction (screenshot): `["rpa", "vision", "screenshot-analysis"]`

Content under 50 characters is discarded as noise (empty pages, login redirects, error pages).

---

### 11. Tables

**Chunker:** `TableChunker`  
**Strategy:** Header preservation + row batching

Tables are treated as structured text. The chunker preserves the header row in every chunk so that each chunk is independently interpretable:

```
Chunk 0: [Header row] + rows 0–10
Chunk 1: [Header row] + rows 11–20
...
```

This prevents the common retrieval failure where a chunk contains rows without headers, making column values uninterpretable.

---

### 12. CSV and JSON

**Chunker strategy:**
- CSV: `row_group` — batch rows into chunks, header preserved
- JSON: `record` — each top-level object becomes a chunk

JSON arrays of objects are the most common JSON input format. Each record is serialized back to a compact JSON string for embedding. This means `{"user_id": "123", "action": "login"}` is embedded as the string `'{"user_id": "123", "action": "login"}'`.

---

### ContentType → Full Pipeline Table

| ContentType | Parser | Chunker | Embedding Model | Storage | Retrieval Strategy |
|-------------|--------|---------|-----------------|---------|-------------------|
| `text` | built-in | `SemanticChunker` | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + cross-encoder |
| `markdown` | built-in | `HeadingChunker` (## splits) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + cross-encoder |
| `pdf` | `PDFParser` (pymupdf → pdfminer → UTF-8) | `PDFLayoutChunker` (per page) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + cross-encoder |
| `docx` | `DOCXParser` (python-docx) | paragraph batching (2000 char) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + cross-encoder |
| `html` | tag stripping | `SemanticChunker` | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + cross-encoder |
| `code` | `CodeParser` + `PDFParser` fallback | `ASTChunker` (function boundary) | voyage-code-3 (1024) | knowledge_chunks_1024 | ColBERT (MaxSim token) |
| `image` | `VisionParser` (GPT-4o → Claude → placeholder) | single chunk | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid |
| `audio` | `AudioParser` (Whisper API, verbose_json) | `TimestampChunker` (60s windows) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid |
| `video` | `VideoParser` (ffmpeg → AudioParser) | transcript + scene chunks | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid |
| `csv` | built-in | `row_group` (header preserved) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + BM25 |
| `json` | built-in | `record` (per top-level object) | text-embedding-3-small (1536) | knowledge_chunks_1536 | Hybrid + BM25 |
| RPA screenshot | `VisionParser` | single chunk | text-embedding-3-small (1536) | `long_term_memory` (LTM) | LTM semantic recall |

> Enterprise plan substitutes `text-embedding-3-large` (3072-dim) for `text-embedding-3-small` in the embedder column, routing to `knowledge_chunks_3072`.

---

### Multimodal Limitations and Dependencies

| Content Type | Limitation | Dependency | Impact if Missing |
|--------------|-----------|------------|-------------------|
| PDF | pymupdf parse of encrypted PDFs fails | `fitz` (PyMuPDF) | Falls back to pdfminer, then UTF-8 raw decode |
| DOCX | Structure (tables, inline images) lost | `python-docx` | Falls back to raw UTF-8 decode with artifacts |
| Audio | Transcription requires OpenAI account | `OPENAI_API_KEY` | `AudioParseResult(error="...")` — no transcript |
| Video | Audio extraction requires binary | `ffmpeg` in PATH | `"[Audio extraction failed for {filename}]"` chunk |
| Image | Description requires vision model | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | `"[Image: {filename}]"` placeholder chunk |
| ColBERT | Token embeddings require model download | `sentence-transformers` | Falls back to TF-IDF weighted MaxSim approximation |
| Cross-encoder | Reranking requires model download | `sentence-transformers` | Falls back to TF-IDF scoring |
| BM25 | True Okapi BM25 requires library | `rank_bm25` | Falls back to simple term-frequency count |
| video → audio track | Many video formats lack audio track | ffmpeg capability | Empty or placeholder transcript chunk |

**ColBERT approximation note:** The `ColBERTPattern` docstring explicitly states that `all-MiniLM-L6-v2` is not a true ColBERT token encoder (which would be `colbert-ir/colbertv2.0`). It uses word-level rather than subword-level embeddings, so the MaxSim scores are an approximation. This is documented in `app/rag/agentic/patterns/colbert.py` and is a known limitation.

---

### Agentic Chunking

**File:** `app/rag/agentic/patterns/agentic_chunking.py`

Based on **Dense X Retrieval** (Chen et al., 2023). Instead of splitting by boundary rules, agentic chunking instructs an LLM to extract all **atomic propositions** from each input chunk.

```
Input chunk: "The Eiffel Tower was built between 1887 and 1889 as the entrance arch
              for the 1889 World's Fair. It was designed by Gustave Eiffel's engineering
              company and stands 330 metres tall."

Atomic propositions extracted:
  1. The Eiffel Tower was built between 1887 and 1889.
  2. It was built as the entrance arch for the 1889 World's Fair.
  3. It was designed by Gustave Eiffel's engineering company.
  4. The Eiffel Tower stands 330 metres tall.
```

Each proposition is a minimal, self-contained claim that can be retrieved independently. This produces higher-precision retrieval at the cost of **N LLM calls per document**, where N is the number of input chunks.

**When to use:** Knowledge bases where precision matters more than ingestion cost — legal documents, compliance policies, technical specifications.

**When to avoid:** Large document collections where ingestion cost is a concern, or content types where propositions are not meaningful (code, tables, CSV).

---

### Cross-Encoder Reranking

**File:** `app/rag/cross_encoder.py`  
**Model:** `cross-encoder/ms-marco-MiniLM-L-6-v2` (22MB, trained on MS MARCO passage ranking)

The cross-encoder is the final reranking step in the retrieval pipeline. Unlike bi-encoder retrieval (which embeds query and document independently), the cross-encoder attends jointly to the (query, document) pair, producing significantly better ranking quality.

```python
cross_encode(query, documents, batch_size=32) -> list[float]
```

**Lazy singleton:** The model is loaded once on first call and cached in `_model_instance`. Subsequent calls reuse the loaded model. Loading takes ~2–4 seconds on first call.

**Input limit:** Documents are truncated to 512 tokens before scoring (`max_length=512`). For longer documents, the first 512 tokens are used.

**TF-IDF fallback:** When `sentence-transformers` is unavailable:
```python
score = Σ (term_freq(t) / doc_len) × IDF(t)  for t in query_tokens
IDF(t) = log((N+1)/(df+1)) + 1   (Okapi-style smoothing)
```

---

### BM25 Retrieval

**File:** `app/rag/bm25.py` — `BM25Retriever`, `BM25Hit`  
**Implementation:** `rank_bm25.BM25Okapi` with k1=1.5, b=0.75

BM25 provides the **lexical** leg in hybrid retrieval, complementing the semantic leg (pgvector ANN). It is especially effective for:
- Proper noun queries (names, identifiers, version strings)
- Exact keyword matching where semantic similarity is misleading
- Short queries where embedding quality degrades

```python
retriever = BM25Retriever(k1=1.5, b=0.75)
retriever.index(chunks)            # tokenizes and builds BM25Okapi index
hits = retriever.search(query, top_k=10)
# Returns BM25Hit(chunk_id, content, score, source_metadata)
```

**Tokenizer:** `re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())` — alphanumeric tokens, lowercase. No stemming or stopword removal.

**Fallback:** When `rank_bm25` is not installed, `BM25Retriever.is_available` returns `False` and the search method falls back to simple term-frequency counting (no IDF, no length normalization).

---

### ColBERT Late Interaction

**File:** `app/rag/agentic/patterns/colbert.py` — `ColBERTPattern`

ColBERT (Contextualized Late Interaction over BERT) reranks by computing **MaxSim** between every query token embedding and every document token embedding:

```
MaxSim(query, doc) = (1/|Q|) × Σ_{q ∈ Q} max_{d ∈ D} cosine(q_emb, d_emb)
```

This captures fine-grained token-level alignment that dense embeddings miss — especially useful for code queries where identifier names (`getUserById`, `fetch_user`) must match exactly.

**Final score (blend):**
```python
final_score = alpha × colbert_score + (1 - alpha) × original_retrieval_score
# Default alpha = 0.5
```

**Backend selection:**
- `sentence-transformers` available: `all-MiniLM-L6-v2` encodes each token, real MaxSim computed
- Not available: TF-IDF weighted token overlap approximation

**Async execution:** `execute()` wraps `rerank()` in `loop.run_in_executor(None, self.rerank, ...)` to avoid blocking the event loop during batch inference.

**Enable/disable:** Controlled by `settings.enable_colbert`. `is_compatible()` checks this flag before the pattern is selected.

---

## Part B — AI Model Router

AgentVerse uses a **layered routing architecture** with four components:

```
Request
    │
    ├── [1] ModelRouter         — Provider defaults per task type (planning/execution/verification)
    │                             In production today for all goal executions
    │
    ├── [2] AIRouter            — Tenant-level policy (health filtering, cost limits, capabilities)
    │
    ├── [3] ModelOrchestrator   — Tier-based (high/medium/low) + budget-ratio downgrade
    │                             Implemented; wired in Phase 6 (not in production path today)
    │
    └── [4] ModelOrchestratorAdapter — Adapter wrapping [3] to implement model_for() interface
                                       Bridges future [3] → current [1] call sites
```

---

### 1. ModelOrchestratorAdapter

**File:** `app/ai_router/model_orchestrator.py` — `ModelOrchestratorAdapter`

The adapter implements the same `model_for(task_type)` interface as `ModelRouter` so that future wiring into `graph.py` is a drop-in replacement. It wraps `ModelOrchestrator` and adds:

1. **Lazy assignment caching:** `update_from_profile()` is called once per `_node_plan` invocation with the goal's `GoalRuntimeProfile` and current `budget_spent_ratio`. The resulting `ModelRoleAssignment` is cached and reused for all task type lookups within that goal.

2. **Default tier fallback:** Before `update_from_profile()` is called, `model_for()` returns from the default tier's `_TIER_MODELS` dict (medium tier by default).

```python
class ModelOrchestratorAdapter:
    def update_from_profile(
        self,
        runtime_profile: GoalRuntimeProfile,
        budget_spent_ratio: float = 0.0,
    ) -> None:
        # Builds PatternConfig from runtime_profile
        # Calls ModelOrchestrator.select_models(pattern_config, budget_spent_ratio)
        # Caches result in self._cached_assignment

    def model_for(self, task_type: str, *, fallback: str = "", goal: str = "") -> str:
        # Returns from _cached_assignment if available, else from default tier
```

**Task type → role mapping:**

| task_type | Assignment field |
|-----------|-----------------|
| `planning` | `assignment.planner` |
| `execution` | `assignment.executor` |
| `verification` | `assignment.verifier` |
| `reflection` | `assignment.planner` |
| `think` / `thinking` | `assignment.planner` |
| `classification` | `assignment.classifier` |
| `judge` | `assignment.judge` |

---

### 2. ModelOrchestrator.select_models()

**File:** `app/ai_router/model_orchestrator.py` — `ModelOrchestrator`

```python
def select_models(
    self,
    config: PatternConfig,
    budget_spent_ratio: float = 0.0,
) -> ModelRoleAssignment:
```

#### Tier selection algorithm

```python
# 1. Base tier from CostLatencyQualityPolicy
tier = self._cost_policy.select_tier(
    complexity=props.complexity,
    risk=props.risk,
    latency_requirement=props.time_sensitivity
)
# → "high" | "medium" | "low"

# 2. Budget-ratio downgrade
if budget_spent_ratio >= 0.90:
    tier = "low"
elif budget_spent_ratio >= 0.75 and tier == "high":
    tier = "medium"

# 3. Per-role resolution
# If PatternConfig specifies a model (config.model_planner != "" and != "default"),
# that model is used. Otherwise, the tier model is used.
# Then _with_failover(model) checks circuit breaker and substitutes if needed.
```

#### Provider failover chain

```python
def _with_failover(self, model: str) -> str:
    provider = _MODEL_PROVIDER.get(model, "openai")
    if not self._health_policy.check(provider).circuit_open:
        return model                          # Primary provider healthy

    # Primary circuit open — try fallback provider
    fallback_provider = _FALLBACK_MODELS.get(provider, "openai")
    # _FALLBACK_MODELS = {"openai": "claude-3-5-sonnet", "anthropic": "gpt-4o", "google": "gpt-4o"}
    if not self._health_policy.check(fallback_provider).circuit_open:
        # Find a non-embedding, non-mini model from the fallback provider
        for m, p in _MODEL_PROVIDER.items():
            if p == fallback_provider and "embedding" not in m and "mini" not in m:
                return m

    return "gpt-4o-mini"  # Last-resort always-available model
```

---

### 3. Tier Model Tables

Source: `_TIER_MODELS` in `app/ai_router/model_orchestrator.py`

| Tier | Planner | Executor | Verifier | Judge | Embedder | Reranker | Classifier |
|------|---------|----------|----------|-------|----------|----------|------------|
| **high** | gpt-5.2 | gpt-5.2 | gpt-5.2 | gpt-5.2 | text-embedding-3-large | gpt-4o-mini | gpt-4o-mini |
| **medium** | gpt-4o | gpt-4o | gpt-4o | gpt-4o | text-embedding-3-small | gpt-4o-mini | gpt-4o-mini |
| **low** | gpt-4o-mini | gpt-4o-mini | gpt-4o-mini | gpt-4o-mini | voyage-3-lite | gpt-4o-mini | gpt-4o-mini |

**Observations:**

- The reranker and classifier are always `gpt-4o-mini` across all tiers. These are binary/classification tasks where the smaller model performs nearly as well as the larger model.
- The embedder uses `voyage-3-lite` (512-dim, Voyage) on low tier rather than an OpenAI model. This is a cost optimization — Voyage's lite model is cheaper than OpenAI's smallest embedding model.
- Budget downgrade only downgrades the planner/executor/verifier triad. Reranker and classifier remain constant.

**Provider map:**

```python
_MODEL_PROVIDER = {
    "gpt-5.2": "openai",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "claude-3-5-sonnet": "anthropic",
    "claude-3-haiku": "anthropic",
    "gemini-2.5-pro": "google",
    "text-embedding-3-large": "openai",
    "text-embedding-3-small": "openai",
    "voyage-3-lite": "voyage",
}
```

**Fallback chain:**

```python
_FALLBACK_MODELS = {
    "openai": "claude-3-5-sonnet",    # OpenAI down → Anthropic
    "anthropic": "gpt-4o",            # Anthropic down → OpenAI
    "google": "gpt-4o",               # Google down → OpenAI
}
```

---

### 4. ModelRouter

**File:** `app/agent/model_router.py` — `ModelRouter`, `ModelRouterConfig`

`ModelRouter` is the **production path** for model selection today. The graph (`app/agent/graph.py`) calls `model_router.model_for(task_type)` and `model_router.model_for_goal(task_type, goal)` for every LLM call.

#### Provider defaults

```python
_PROVIDER_DEFAULTS = {
    "anthropic": ModelRouterConfig(
        planning_model="claude-opus-4-8",
        execution_model="claude-sonnet-4-5",
        verification_model="claude-haiku-3-5",
        fallback_model="claude-opus-4-8",
    ),
    "openai": ModelRouterConfig(
        planning_model="gpt-5.2",
        execution_model="gpt-4o-mini",
        verification_model="gpt-4o-mini",
        fallback_model="gpt-5.2",
    ),
    "groq": ModelRouterConfig(
        planning_model="llama-3.1-70b-versatile",
        execution_model="llama-3.1-8b-instant",
        verification_model="llama-3.1-8b-instant",
        fallback_model="llama-3.1-70b-versatile",
    ),
    "ollama": ModelRouterConfig(
        planning_model="llama3.2",
        execution_model="llama3.2",
        verification_model="llama3.2",
        fallback_model="llama3.2",
    ),
}
```

#### model_for_goal() — goal complexity downgrade

`model_for_goal(task_type, goal)` classifies the goal before returning a model:

```python
# Complexity classification:
_COMPLEX_PATTERNS = r"\b(create|build|deploy|implement|design|architect|automate|
                          migrate|refactor|analyze and|generate report|compare|
                          multi.step|workflow|pipeline|integrate)\b"

_SIMPLE_PATTERNS  = r"\b(list|show|get|find|search|what is|who is|when|count|
                          how many|check status|ping|describe|summarize in one|
                          give me the)\b"

# If COMPLEX → "complex" tier; if SIMPLE → "simple" tier; else → "medium"

# Downgrade logic (planning task only):
if tier == "simple" and task_type == "planning":
    return execution_model  # Use cheaper execution model for simple planning
```

For `task_type == "verification"`, the verification model is always returned regardless of goal complexity — even simple goals need their output verified.

#### with_override() — per-goal model override

```python
def with_override(self, model: str) -> ModelRouter:
    """Copy-on-write: returns NEW ModelRouter with all task types set to model."""
    new_router = copy(self)
    new_router._config = ModelRouterConfig(
        planning_model=model,
        execution_model=model,
        verification_model=model,
        embedding_model=self._config.embedding_model,  # embedding preserved
        fallback_model=model,
    )
    return new_router
```

This is used when a tenant specifies `model_override` in the goal request. The copy-on-write pattern prevents the override from leaking into future goals for the same tenant.

#### get_router_for_tenant()

```python
def get_router_for_tenant(tenant_cfg: dict) -> ModelRouter:
    provider = tenant_cfg.get("provider", "anthropic")
    default_model = tenant_cfg.get("default_model", "")
    base_config = _PROVIDER_DEFAULTS.get(provider, ModelRouterConfig())
    # If tenant has a specific model configured, it becomes the fallback
    # but does not override the provider-specific planning/execution/verification models
    ...
```

---

### 5. AIRouter

**File:** `app/ai_router/router.py` — `AIRouter`

`AIRouter` operates at the **tenant policy level** — it applies health filters, cost constraints, and capability requirements on top of whatever model the ModelRouter has selected.

```python
def select_model(
    self,
    task_type: TaskType,
    tenant_id: str,
    *,
    require_vision: bool = False,
    require_tools: bool = False,
    require_structured: bool = False,
    max_cost_per_1k: float | None = None,
    model_override: str | None = None,
) -> ModelEndpoint | None:
```

#### Selection algorithm

```
1. model_override provided → look up in registry by "provider/model" format, return if found

2. Tenant routing policy → check registry for (tenant_id, task_type) preference
      → if preferred_provider + preferred_model are healthy, return that model

3. Filter all registered models:
      → m.is_available == True
      → NOT registry.get_provider_health(m.provider).circuit_open

4. Filter by task_type:
      → TaskType.EMBEDDING   → ModelCapability.EMBEDDING in m.capabilities
      → TaskType.OCR         → VISION or OCR capability
      → Others               → TEXT_GENERATION capability

5. Filter by optional constraints:
      → require_vision       → m.supports_vision
      → require_tools        → m.supports_tools
      → require_structured   → m.supports_structured_output
      → max_cost_per_1k      → m.cost_per_1k_input <= max_cost_per_1k

6. Sort remaining candidates (quality score desc, cost asc)
7. Return best candidate, or None if empty
```

#### ModelRegistry

`app/ai_router/registry.py` — `model_registry` singleton with 11 built-in endpoints. Supports runtime registration of new endpoints without restart.

---

### 6. RAG Pattern → Model Implications

The `PatternSelector` (`app/rag/agentic/pattern_selector.py`) routes each goal to a RAG strategy. The selected strategy has direct implications for which model capabilities are required:

| Goal Property | RAG Pattern | Model Implication |
|---------------|-------------|-------------------|
| `type == "expert"` | RAPTOR | Needs LLM for recursive summarization during indexing; planner-tier model |
| `type == "complex"` | Fusion RAG | Multiple query decomposition LLM calls; planner-tier model |
| `web == True` | FLARE | Uncertainty detection requires a model with calibrated confidence; any model |
| `type == "code"` | ColBERT | Needs `sentence-transformers` for token embeddings; **no API key required** (local) |
| `type == "realtime"` | Self-RAG | `should_retrieve()` decision before each step; fast model preferred (gpt-4o-mini) |

**RAPTOR detail:** The RAPTOR pattern builds a tree of cluster summaries over the document collection. Each cluster summary requires an LLM call with the documents as input. This ingestion-time LLM usage is billed to the tenant's budget and affects `budget_spent_ratio`, which can trigger model downgrade for subsequent goal steps.

**ColBERT / local model path:** When `enable_colbert=True` and `sentence-transformers` is installed, ColBERT reranking requires no API calls for the reranking step itself. The initial retrieval still uses pgvector (requires embeddings), but the reranking step is purely local. This is the only retrieval strategy with zero per-call API cost.

---

### 7. Provider Circuit Breaker

**File:** `app/ai_router/provider_health_policy.py` — `ProviderHealthPolicy`, `ProviderHealthStatus`

The circuit breaker tracks per-provider error rates and opens the circuit when errors accumulate:

```python
@dataclass
class ProviderHealthStatus:
    provider: str
    healthy: bool = True
    circuit_open: bool = False
    error_rate: float = 0.0
    avg_latency_ms: float = 500.0

class ProviderHealthPolicy:
    def record_failure(self, provider: str) -> None:
        s.error_rate = min(1.0, s.error_rate + 0.1)  # +10% per failure
        if s.error_rate >= 0.5:
            s.circuit_open = True  # Trip at 50% error rate (5 consecutive failures)

    def record_success(self, provider: str, latency_ms: float) -> None:
        s.error_rate = max(0.0, s.error_rate - 0.05)  # Exponential recovery
        s.avg_latency_ms = 0.9 × s.avg_latency_ms + 0.1 × latency_ms
        if s.error_rate < 0.2:
            s.circuit_open = False  # Close circuit when error rate drops below 20%
```

**Asymmetric recovery:** Failures increment by 0.10, successes decrement by 0.05. This means the circuit closes slowly (2 successes per 1 failure) but opens quickly (5–6 failures without successes).

**Redis-backed state (production):** In the production lifespan, `ProviderHealthPolicy` is backed by Redis so circuit state is consistent across replicas. This prevents one replica from sending traffic to a failed provider while another replica has already tripped its circuit.

**Integration with ModelOrchestrator:** `_with_failover(model)` checks `health_policy.check(provider).circuit_open` before returning a model. If the circuit is open, it immediately substitutes a fallback model without making the failing API call.

---

### 8. Multimodal Model Selection

**File:** `app/ai_router/model_orchestrator.py` — `ModelOrchestrator.select_for_content_type()`

```python
def select_for_content_type(self, content_type: ContentType) -> MultimodalModelAssignment:
    modality = _CONTENT_TYPE_MODALITY.get(content_type.value, "text")
    spec = _MULTIMODAL_MODELS.get(modality, _MULTIMODAL_MODELS["text"])
    return MultimodalModelAssignment(
        modality=modality,
        extractor_model=self._with_failover(spec["extractor"]),
        reasoner_model=self._with_failover(spec["reasoner"]),
        requires_vision=spec.get("requires_vision", False),
        requires_audio=modality == "audio",
    )
```

#### Content type → modality map

```python
_CONTENT_TYPE_MODALITY = {
    "image":    "image",
    "audio":    "audio",
    "video":    "video",
    "code":     "code",
    "text":     "text",
    "pdf":      "text",    # PDFs are text after extraction
    "docx":     "text",
    "markdown": "text",
    "html":     "text",
    "csv":      "text",
    "json":     "text",
}
```

#### Multimodal model assignments

```python
_MULTIMODAL_MODELS = {
    "image": {
        "extractor": "gpt-4o",           # Vision: describes image content
        "reasoner":  "gpt-5.2",          # Answers questions about the image
        "requires_vision": True,
    },
    "audio": {
        "extractor": "gpt-4o-audio",     # Whisper-class: transcribes
        "reasoner":  "gpt-5.2",          # Answers questions about transcript
        "requires_vision": False,
    },
    "video": {
        "extractor": "gemini-2.5-pro",   # Long-context multimodal: handles video frames
        "reasoner":  "gpt-5.2",
        "requires_vision": True,
    },
    "code": {
        "extractor": "gpt-5.2",          # Code comprehension and summarization
        "reasoner":  "gpt-5.2",
        "requires_vision": False,
    },
    "text": {
        "extractor": "gpt-4o",           # Standard extraction
        "reasoner":  "gpt-5.2",
        "requires_vision": False,
    },
}
```

#### MultimodalModelAssignment fields

```python
@dataclass
class MultimodalModelAssignment:
    modality: str
    extractor_model: str     # Used during ingestion (parsing/describing)
    reasoner_model: str      # Used during inference (answering questions)
    requires_vision: bool    # True → AIRouter must filter for vision-capable models
    requires_audio: bool     # True → needs audio transcription capability
```

**Extractor vs. reasoner distinction:** The extractor is used once during document ingestion to produce the text representation (vision description, transcription). The reasoner is the model used during goal execution when the agent needs to reason *about* the extracted content. This split allows a cheaper model to handle the mechanical extraction while a more capable model handles the nuanced reasoning.

---

### 9. Cost-Aware Routing

The budget-ratio downgrade system connects the financial state of a goal execution to its model selection:

#### Budget tracking

Every LLM API call increments `agent_state.context["total_cost_usd"]`. The planner reads the current cost and divides by `runtime_profile.model_plan.max_cost_usd` to get `budget_spent_ratio`.

```python
# In _node_plan:
total_cost = state.context.get("total_cost_usd", 0.0)
max_cost = runtime_profile.model_plan.max_cost_usd or 1.0
budget_spent_ratio = min(1.0, total_cost / max_cost)

# Passes to ModelOrchestratorAdapter (Phase 6 path):
adapter.update_from_profile(runtime_profile, budget_spent_ratio)
```

#### Downgrade thresholds

```
budget_spent_ratio < 0.75:  → No change (use originally selected tier)
0.75 ≤ ratio < 0.90:        → Downgrade "high" tier to "medium" (leave "medium" and "low" alone)
ratio ≥ 0.90:               → Force "low" tier for all roles
```

**Effect on per-goal costs:**

A goal that starts with `gpt-5.2` (high tier, ~$15/M output tokens) and crosses the 90% budget threshold will switch to `gpt-4o-mini` (~$0.60/M output tokens) for remaining steps. This reduces the likelihood of budget overruns on long-running goals while keeping quality high for the critical early steps (planning is always done at the original tier).

**Economy mode:** When `budget_spent_ratio ≥ 0.90`, the executor model is downgraded but the verifier model remains the same tier. Verification is a binary pass/fail judgment — it is cheap regardless of model, and maintaining verifier quality prevents false successes on low-quality executor output.

#### Current production path

The budget-aware downgrade is implemented in `ModelOrchestratorAdapter` but not yet wired into `graph.py` (Status: "TODO Phase 6" in the source file). The current production path uses `ModelRouter.model_for_goal()`, which applies goal complexity classification (simple/medium/complex) as a simpler cost proxy but does not track actual USD spend.

---

### 10. Production Wiring Status

Understanding which routing components are active in production avoids confusion when debugging model selection:

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| `ModelRouter` | `app/agent/model_router.py` | **Active (production)** | Used by `graph.py` for all goal executions |
| `AIRouter` | `app/ai_router/router.py` | **Active** | Tenant policy layer, used by ingestion and background tasks |
| `ModelOrchestrator` | `app/ai_router/model_orchestrator.py` | Implemented, not wired | Phase 6: replaces ModelRouter in graph |
| `ModelOrchestratorAdapter` | same file | Implemented, not wired | Adapter for drop-in graph replacement |
| Budget-ratio downgrade | `model_orchestrator.py:select_models()` | Implemented, not wired | Will activate with Phase 6 wiring |
| Provider circuit breaker | `app/ai_router/provider_health_policy.py` | **Active** | Used by ModelOrchestrator's `_with_failover()` and AIRouter |

**Practical implication:** If you add a log statement to `ModelOrchestratorAdapter.model_for()` and see no output during goal execution, it is because `graph.py` is still calling `ModelRouter.model_for()` directly. The orchestrator adapter is only invoked when the wiring in `goal_service.py`'s graph construction is updated to pass the adapter instance.

---

## Part C — Tier Selection: CostLatencyQualityPolicy and RolePolicy

---

### CostLatencyQualityPolicy

**File:** `app/ai_router/cost_latency_quality_policy.py`

This policy encodes the decision matrix that `ModelOrchestrator.select_models()` uses before applying the budget-ratio downgrade:

```python
class CostLatencyQualityPolicy:
    def select_tier(
        self,
        complexity: Complexity,
        risk: RiskLevel,
        latency_requirement: str = "interactive",
    ) -> str:
        # Rule 1: Safety trumps cost — high/critical risk always gets best model
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return "high"

        # Rule 2: Real-time goals need fast models, not large ones
        if latency_requirement == "realtime":
            return "low"

        # Rule 3: Complexity-driven tier
        if complexity == Complexity.EXPERT:    return "high"
        elif complexity == Complexity.COMPLEX: return "medium"
        elif complexity == Complexity.SIMPLE and risk == RiskLevel.LOW:
                                               return "low"
        return "medium"   # Default
```

**Decision matrix:**

| Risk | Latency | Complexity | Tier |
|------|---------|-----------|------|
| HIGH or CRITICAL | any | any | **high** |
| any | realtime | any | **low** |
| LOW–MEDIUM | interactive | EXPERT | **high** |
| LOW–MEDIUM | interactive | COMPLEX | **medium** |
| LOW | interactive | SIMPLE | **low** |
| any | interactive | MEDIUM | **medium** |

**Rule ordering is significant:** Risk is checked before latency. A high-risk real-time goal (e.g., a deployment check under time pressure) gets the high-tier model, not the low-tier speed-optimized model. This intentionally prioritizes correctness over speed for dangerous operations.

**Integration with budget downgrade:** The tier returned by this policy is the *starting* tier. The budget-ratio downgrade in `ModelOrchestrator.select_models()` then overrides it downward if the goal has spent ≥75% or ≥90% of its budget:

```
Policy tier = "high"
Budget ratio = 0.82 (82% spent)
→ Downgrade: "high" → "medium"  (because ratio ≥ _BUDGET_75 and tier was "high")
```

A "medium" tier goal that hits 82% budget is **not** downgraded to "low" — only "high" is downgraded to "medium" at the 75% threshold. The 90% threshold always forces "low" regardless of starting tier.

---

### RolePolicy

**File:** `app/ai_router/role_policy.py` — `RolePolicy`, `AgentRole`

Determines which agent roles are required for a given `PatternConfig`. The standard set always includes Planner, Executor, Verifier, Classifier, Embedder, and Reranker. The Judge role is added only when the goal uses multi-agent debate or consensus patterns:

```python
class RolePolicy:
    def get_required_roles(self, config: PatternConfig) -> list[AgentRole]:
        roles = [PLANNER, EXECUTOR, VERIFIER, CLASSIFIER, EMBEDDER]
        if any(
            p in ("debate", "consensus", "consensus_verification", "peer_review")
            for p in (config.multi_agent_patterns or [])
        ):
            roles.append(AgentRole.JUDGE)    # Judge arbitrates multi-agent disagreements
        roles.append(AgentRole.RERANKER)
        return roles
```

The `JUDGE` role maps to the `judge` key in `_TIER_MODELS`, which is always the same model as the planner/executor at each tier. The judge needs to understand the full execution context to arbitrate disagreements, so it requires planning-tier quality.

---

## Part D — RAG Patterns: Full Routing Table

`PatternSelector` in `app/rag/agentic/pattern_selector.py` evaluates `GoalProperties` and selects a RAG strategy. The full routing table:

| Goal Type | Has Knowledge Base | Web Required | RAG Pattern | Model Requirement |
|-----------|:------------------:|:------------:|-------------|------------------|
| `expert` | ✓ | — | RAPTOR | LLM for recursive summarization |
| `complex` | ✓ | — | Fusion RAG | LLM for multi-query decomposition |
| `web` | any | ✓ | FLARE | Any model with calibrated uncertainty |
| `code` | ✓ | — | ColBERT | `sentence-transformers` (local) |
| `realtime` | any | — | Self-RAG | Fast model (gpt-4o-mini tier) |
| `simple` | ✓ | — | Naive RAG | Embedding only (no reranker) |
| `simple` | — | — | Direct LLM | No retrieval at all |
| `analytical` | ✓ | — | Hybrid | BM25 + pgvector + cross-encoder |

### RAPTOR Detail

RAPTOR (Recursive Abstractive Processing for Tree-Organized Retrieval) builds a tree of summaries over the collection. Each leaf is a raw chunk; parent nodes are LLM-generated summaries of clusters of children.

**Build-time cost:** An LLM call is required for each cluster summary. A collection of 1000 chunks might require 50–100 summarization calls (depending on cluster size). These calls are billed against the tenant's budget at the **planner model** tier.

**Query-time behavior:** Queries traverse the tree top-down, choosing the branch whose summary best matches the query embedding. This is especially effective for questions that require synthesizing information across many documents ("What are all the authentication mechanisms used in the codebase?").

**Why planner model:** Summaries must be abstractive and factually accurate. Using an executor or verification model for summarization produces lower-quality summaries that degrade retrieval quality across all future queries on that collection.

### Fusion RAG Detail

Fusion RAG generates N query variants from the original query (typically 3–5 variants), retrieves separately for each, then merges and reranks results using Reciprocal Rank Fusion (RRF).

**Query decomposition:** Uses a planner-tier LLM call with a prompt like: *"Generate 4 different phrasings of this search query to cover different aspects."*

**RRF scoring:**
```
RRF_score(d) = Σ_{q in queries} 1 / (k + rank_q(d))
where k = 60 (standard constant)
```

**Use case:** Complex queries where users ask multiple implicit sub-questions. `"How does the authentication system interact with rate limiting?"` decomposes into `"authentication system design"`, `"rate limiting implementation"`, `"auth and rate limit integration"`, each retrieving different documents.

### FLARE Detail

FLARE (Forward-Looking Active REtrieval) triggers retrieval mid-generation when the model's confidence drops below a threshold. Instead of retrieving once at the start, FLARE continuously monitors the token-level confidence of the LLM's output.

**Retrieval trigger:** When the model produces a token with probability < `threshold` (typically 0.5), FLARE pauses generation, constructs a retrieval query from the *anticipated* next tokens, retrieves fresh documents, and resumes generation with the augmented context.

**Why "any model":** FLARE's effectiveness depends on calibrated confidence scores, not raw model capability. `gpt-4o-mini` with well-calibrated confidence is more effective for FLARE than `gpt-5.2` with miscalibrated confidence.

**Web search trigger:** The `web=True` goal property routes to FLARE because web search can be triggered per-token, allowing the agent to fetch fresh information exactly when needed rather than pre-fetching all web results upfront.

### Self-RAG Detail

Self-RAG adds a retrieval decision node before each generation step: the model first decides `should_retrieve: yes/no` based on the current context, then either retrieves and generates or generates directly.

**Why fast model:** The `should_retrieve` binary decision is a simple classification task. Using `gpt-4o-mini` for this decision keeps the fast path (no retrieval needed) extremely cheap. The actual generation step (if retrieval is needed) uses the full executor model.

**Realtime goals:** Real-time goals (e.g., "check current server status", "get latest deployment result") often involve many rapid tool calls where retrieval is not needed. Self-RAG avoids unnecessary KB lookups, keeping the per-step latency low.

---

## Part E — Content Type Cost Model

Understanding the per-content-type cost is essential for capacity planning. Costs are per document processed (ingestion) and per retrieval call.

### Ingestion cost by content type

| Content Type | Ingestion LLM call? | Model | Approx. cost per page/item |
|-------------|:-------------------:|-------|--------------------------|
| Text | No | — | Embedding only (~$0.0002) |
| Markdown | No | — | Embedding only |
| PDF | No | — | Embedding only (parser is local) |
| DOCX | No | — | Embedding only |
| HTML | No | — | Embedding only |
| Code | No | — | voyage-code-3 embedding ($0.00016/1k tokens) |
| Image | **Yes** | gpt-4o vision | ~$0.01–0.03 per image (varies by size) |
| Audio | **Yes** | Whisper (gpt-4o-audio) | ~$0.006/minute |
| Video | **Yes** | ffmpeg (free) + Whisper | ~$0.006/minute of audio track |
| RAPTOR indexing | **Yes** | Planner model | ~$0.05–0.50 per collection (amortized) |
| Agentic chunking | **Yes** | Executor model | ~N × $0.005 per chunk (N chunks per doc) |

### Retrieval cost by strategy

| RAG Strategy | Retrieval LLM calls | Per-query cost |
|-------------|:-------------------:|----------------|
| Naive RAG | 0 | Embedding only |
| Hybrid (BM25 + pgvector) | 0 | Embedding + BM25 scan |
| ColBERT | 0 (if local) | Token embedding (local, ~$0) |
| Cross-encoder reranking | 0 | Model inference (local, ~$0) |
| Fusion RAG | 3–5 (query expansion) | ~$0.005–0.015 per goal |
| RAPTOR | 0 (tree traversal) | Embedding only |
| FLARE | 0–N (per-token) | Variable: 0 if no retrieval needed |
| Self-RAG | 1 per step (`should_retrieve`) | ~$0.001 per step decision |
| Agentic chunking (retrieval) | 0 | Embedding only (propositions indexed) |

The cross-encoder and ColBERT rerankers deserve special mention: they are both local models loaded via `sentence-transformers`. Once the model is warm (first call incurs a ~2–4 second load), they add zero API cost to every reranking operation. For high-volume deployments, the local rerankers provide significant cost savings over using an LLM API for reranking.

---

## Part F — End-to-End Model Selection Trace

A concrete example showing how a goal flows through every routing layer:

**Goal:** `"Analyze all open Jira tickets and generate a prioritized report"` (tenant on Professional plan, Anthropic provider, $5 budget, 40% spent)

```
1. PatternSelector
   goal_type = "analytical" (contains "analyze")
   → RAG pattern: Hybrid (BM25 + pgvector + cross-encoder)

2. ModelRouter.model_for_goal("planning", goal)
   provider = "anthropic"
   _COMPLEX_PATTERNS matches "analyze and", "generate report"
   → complexity = "complex" (no downgrade; complex goals keep planning_model)
   → returns "claude-opus-4-8"

3. ModelRouter.model_for("execution")
   → returns "claude-sonnet-4-5"

4. ModelRouter.model_for("verification")
   → returns "claude-haiku-3-5"

5. EmbeddingOrchestrator.select(ContentType.TEXT, tenant_ctx)
   tenant plan = "professional" → allowed_costs = ["free", "low", "medium"]
   best text model at medium cost class = "text-embedding-3-large" (3072-dim)
   → knowledge_chunks_3072 table

6. AIRouter.select_model(TaskType.EMBEDDING, tenant_id, require_tools=False)
   Filters: is_available=True, no circuit open, EMBEDDING capability
   → Returns text-embedding-3-large endpoint

7. Cross-encoder reranking (local)
   cross-encoder/ms-marco-MiniLM-L-6-v2 loaded
   → Re-scores all candidates, no API call

8. ContextPipeline.run()
   → 7-step pipeline → planner_context assembled with all memory sources

9. Budget check (Phase 6 path, when wired):
   budget_spent_ratio = 0.40 (below 0.75 threshold)
   → No downgrade; keep claude-opus-4-8 for planning

10. Goal completes → EpisodicMemoryStore.record() + ProceduralMemoryStore.learn()
    → Jira+analytics domain Skill learned
    → Episode stored with outcome="success", quality_score=0.85
```

This trace illustrates how the routing system layers decisions: content type → embedding model → retrieval strategy → LLM model → budget check → memory write-back. Each layer is independently configurable and falls back gracefully when dependencies are unavailable.

---

*End of `05-multimodal-and-model-routing.md`*
