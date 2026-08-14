# RAG Integration Guide: Embeddings, Chunking, Guardrails & Governance

How all the pieces fit together: from raw document ingestion through to a fully governed, audited, citation-verified answer.

---

## The Complete RAG Pipeline

```
RAW DOCUMENT (PDF, DOCX, audio, video, code, email, HTML)
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  INGESTION PIPELINE  (app/ingestion/)                      │
│                                                           │
│  1. Parser selection (ContentClassifier)                  │
│     pdf_parser, docx_parser, audio_parser, video_parser   │
│     email_parser, vision_parser                           │
│                                                           │
│  2. Chunking strategy (ChunkingStrategySelector)          │
│     SemanticChunker, HeadingChunker, ASTChunker           │
│     PDFLayoutChunker, TableChunker, TimestampChunker       │
│                                                           │
│  3. Data classification (DataClassification)              │
│     sensitivity: public/internal/confidential/secret      │
│     category: pii/financial/health/code                   │
│     handling: store/no_store/redact/encrypt               │
│                                                           │
│  4. Embedding (EmbeddingOrchestrator)                     │
│     Model selection: free/standard/premium tier           │
│     embed_with_fallback()                                  │
│                                                           │
│  5. KnowledgeStore write                                  │
│     chunk + embedding + metadata → PostgreSQL + pgvector  │
│     IngestionProvenance recorded                          │
└───────────────────────────────────────────────────────────┘
        │
        ▼ (at query time)
┌───────────────────────────────────────────────────────────┐
│  RETRIEVAL  (app/rag/)                                     │
│                                                           │
│  1. Semantic cache check (SemanticCache L1+L2)            │
│     Skip everything if similar query cached               │
│                                                           │
│  2. Strategy selection (RetrievalPlanner / PatternAssembler)│
│     hybrid / naive / hyde / multi_hop / flare / raptor...  │
│                                                           │
│  3. Hybrid search (KnowledgeStore.hybrid_search)          │
│     pgvector + BM25 + trigram + RRF                       │
│                                                           │
│  4. CrossEncoder rerank (optional)                        │
│     LLM scores (query, passage) pairs                     │
│                                                           │
│  5. CitationManager.register_chunks()                     │
│     Assign [1][2][3] IDs to chunks                        │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CONTEXT PIPELINE  (app/context/)                          │
│                                                           │
│  1. PromptBudget.fit()                                    │
│     6000 tok planner / 3000 tok executor / 2000 tok verify│
│                                                           │
│  2. ContextualEnricher                                    │
│     Adds doc-level header to each chunk                   │
│                                                           │
│  3. OutputContractBuilder                                 │
│     Appends JSON output schema to prompt                  │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  GOVERNANCE GATES  (app/governance/)                       │
│                                                           │
│  1. OutputSanitizer                                       │
│     Strip PII/secrets from retrieved chunk content        │
│     DataClassification: redact/no_log fields              │
│                                                           │
│  2. GuardrailsV2                                          │
│     Content safety on retrieved content                   │
│     Declarative YAML rules + pluggable evaluators         │
│                                                           │
│  3. PolicyEngine                                          │
│     Tenant-specific retrieval restrictions                │
│     "Only retrieve from approved collections"             │
│                                                           │
│  4. CostController                                        │
│     Budget check before LLM generation call              │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  LLM GENERATION  (app/agent/)                             │
│                                                           │
│  Planner / Executor / Verifier LLMs                       │
│  ModelRouter selects model per role                       │
│  PromptBuilder.build_*_context()                          │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  POST-GENERATION  (app/provenance/, app/governance/)       │
│                                                           │
│  1. ProvenanceLedger.verify_citations()                   │
│     Ensure [1][2][3] citations exist in KnowledgeStore    │
│                                                           │
│  2. GroundingChecker                                      │
│     Factual claims groundable against retrieved context?  │
│                                                           │
│  3. AuditLog.record()                                     │
│     Immutable record: query, retrieved chunks, response   │
│     tenant_id, user_id, timestamp, data_classification    │
└───────────────────────────────────────────────────────────┘
```

---

## Chunking Strategies: The Foundation of Retrieval Quality

The quality of RAG is fundamentally determined by **how documents are chunked**. The wrong chunking strategy guarantees poor retrieval.

### The 7 chunkers in AgentVerse

**1. SemanticChunker** (`app/ingestion/chunkers/semantic.py`)

```
Algorithm: split at semantic boundaries (sentence-level embeddings)
  
Input: "The AWS Lambda function executed successfully. The DynamoDB 
        table was updated. However, the SQS message was not acknowledged."

SemanticChunker:
  Chunk 1: "The AWS Lambda function executed successfully."
  Chunk 2: "The DynamoDB table was updated."
  Chunk 3: "However, the SQS message was not acknowledged."

Why: Each sentence is a standalone retrievable fact.
Best for: News articles, academic papers, technical documentation.
Avoid for: Legal contracts (clause boundaries matter, not sentence boundaries)
```

**2. HeadingChunker** (`app/ingestion/chunkers/heading.py`)

```
Algorithm: split on markdown/HTML heading boundaries

Input (markdown):
  # Introduction
  This section covers...
  ## Key Concepts
  The main concept is...
  ## Implementation
  To implement this...

HeadingChunker:
  Chunk 1: "# Introduction\nThis section covers..."
  Chunk 2: "## Key Concepts\nThe main concept is..."
  Chunk 3: "## Implementation\nTo implement this..."

Why: Preserves document structure; each chunk = one topic.
Best for: Product documentation, knowledge bases, READMEs, wikis.
```

**3. ASTChunker** (`app/ingestion/chunkers/ast_chunker.py`)

```
Algorithm: parse code via AST, split at function/class/method boundaries

Input (Python):
  class UserService:
      def create_user(self, name: str) -> User:
          ...
      def delete_user(self, user_id: str) -> None:
          ...

ASTChunker:
  Chunk 1: "class UserService: [class-level docstring + attributes]"
  Chunk 2: "def create_user(self, name: str) -> User: [full method]"
  Chunk 3: "def delete_user(self, user_id: str) -> None: [full method]"

Why: Preserves callable units for code search; each method is independently queryable.
Best for: Code repositories, SDK documentation, API reference.
```

**4. PDFLayoutChunker** (`app/ingestion/chunkers/pdf_layout.py`)

```
Algorithm: preserve PDF layout — columns, tables, headers, footers

Input: SEC 10-K filing with 2-column layout
  Column 1: Risk Factors text
  Column 2: Financial tables
  
Without PDFLayoutChunker: columns interleaved → garbage text
With PDFLayoutChunker: 
  Chunk 1: Left column continuous text
  Chunk 2: Right column financial data

Best for: Financial filings, research papers, legal documents
Avoid for: Simple single-column text documents (overhead not worth it)
```

**5. TableChunker** (`app/ingestion/chunkers/table.py`)

```
Algorithm: each table row becomes one chunk (with column headers repeated)

Input: product specifications table
  | Model | CPU | RAM | Price |
  | X100  | M3  | 16GB| $999  |
  | X200  | M3 Pro | 32GB | $1499 |

TableChunker:
  Chunk 1: "Model=X100, CPU=M3, RAM=16GB, Price=$999"
  Chunk 2: "Model=X200, CPU=M3 Pro, RAM=32GB, Price=$1499"

Why: Each row is independently queryable; "which model has 32GB?" hits Chunk 2 directly.
Best for: Product catalogs, pricing tables, comparison matrices, data sheets.
```

**6. SceneChunker** (`app/ingestion/chunkers/scene.py`)

```
Algorithm: video/audio split at scene boundaries (shot detection for video,
           silence gaps for audio)

Input: 2-hour conference talk video

SceneChunker:
  Chunk 1: 00:00–05:30 "Introduction and agenda"
  Chunk 2: 05:30–18:45 "Core concept explanation with live demo"
  ...

Best for: Video lectures, training materials, meetings, podcasts.
Combined with: audio_parser for transcription, vision_parser for slides OCR.
```

**7. TimestampChunker** (`app/ingestion/chunkers/timestamp.py`)

```
Algorithm: split on timestamps in transcripts/logs

Input: customer support call transcript
  [00:02:15] Agent: "Your account shows..."
  [00:03:42] Customer: "But I was charged..."
  [00:05:10] Agent: "I'll issue a refund..."

TimestampChunker:
  Chunk 1: "[00:02:15–00:03:42] Agent explains account status"
  Chunk 2: "[00:03:42–00:05:10] Customer billing dispute"
  Chunk 3: "[00:05:10–...] Refund resolution"

Best for: Call center recordings, Zoom meeting transcripts, debug logs.
```

### Chunking strategy selection matrix

| Document type | Recommended chunker | Chunk size |
|---|---|---|
| Technical docs, READMEs | HeadingChunker | Section-based |
| Academic papers | SemanticChunker | 2–4 sentences |
| Source code | ASTChunker | Function/class |
| PDF filings (SEC, legal) | PDFLayoutChunker | Column-aware |
| Data tables, CSVs | TableChunker | 1 row per chunk |
| Video/audio content | SceneChunker + TimestampChunker | Scene boundaries |
| News articles | SemanticChunker | Paragraph level |
| Chat logs, transcripts | TimestampChunker | Turn level |

---

## Late Chunking: Context-Aware Embeddings

Standard chunking embeds each chunk **in isolation** — losing surrounding context.

**Late chunking** (`app/rag/late_chunker.py`) embeds chunks **with their context window**:

```
Standard chunking:
  Chunk: "It was introduced in 2015."
  embed("It was introduced in 2015.") → ambiguous vector ("it" = ?)

Late chunking:
  Context: "Docker containerization technology. It was introduced in 2015."
  embed("Docker containerization technology. It was introduced in 2015.") 
  → precise vector (clearly about Docker, 2015)
```

**When late chunking matters:**
- Documents with many pronoun references ("it", "this", "these")
- Sequential narrative (step 1 → step 2 → step 3)
- Tables where column headers give meaning to cell values

**Cost:** ~3× more context tokens per embedding call. Enable only for documents where coreference resolution is critical.

---

## Parent-Child Chunking: Small Retrieve, Large Return

`app/rag/parent_child_chunker.py`

```
Index time:
  Large parent chunk (512 tokens): stored for context
  Small child chunks (128 tokens each): stored for precision retrieval
  child_chunks.parent_id = parent_chunk.id

Query time:
  embed(query) → find most similar child chunks (128 tok = precise match)
  return parent_chunk.content (512 tok = rich context)

Why this is better than just indexing 512-token chunks:
  Large chunks: lower vector precision (averaged over many sentences)
  Small chunks: high vector precision but insufficient context for LLM
  Parent-child: best of both — precise retrieval + rich context
```

**Real-world example:**
```
Legal contract, 50-page document:

Large parent: "Section 5.2 Termination for Cause (512 tokens):
  Either party may terminate this Agreement for Cause upon written 
  notice if the other party materially breaches... [full legal clause]"

Small child: "either party terminate for Cause written notice material breach"
             (semantic keywords, 20 tokens, high retrieval precision)

Query: "under what conditions can we terminate the contract?"
  → Matches child (high precision)
  → Returns parent (full legal clause for LLM context)
```

---

## Guardrails and Governance in the RAG Pipeline

### Where guardrails fire in RAG

```
Document ingestion:
  DataClassification.classify() → tags every chunk with sensitivity level
  no_log: don't include in audit
  redact: strip before storing (SSN, credit cards)
  no_store: don't persist (real-time financial data)

Pre-retrieval (query phase):
  GuardrailChecker.check_query() → is the query itself safe?
  Example: "retrieve all SSNs from the HR database" → BLOCKED

Retrieved context:
  OutputSanitizer → strip PII from chunks before sending to LLM
  DataClassification.handling = "redact" → replace SSNs with [REDACTED]

Generated response:
  GuardrailsV2 streaming check → token-by-token safety scan
  PolicyEngine → enforce tenant-specific content restrictions
  "Do not reveal customer personal information in responses"
```

### Data Classification + RAG: practical scenarios

| Classification | RAG behavior |
|---|---|
| `sensitivity: public` | Included in normal retrieval, no restrictions |
| `sensitivity: internal` | Only returned to authenticated internal users |
| `sensitivity: confidential` | Requires specific collection access grant |
| `category: pii, handling: redact` | Names/SSNs redacted before LLM sees them |
| `category: financial, handling: no_log` | Used in retrieval but not written to AuditLog |
| `category: health, handling: encrypt_at_rest` | Decrypted only in memory during retrieval |

---

## Citation and Provenance: Tracing Every Answer to Its Source

### CitationManager workflow

```
RAG retrieves 5 chunks
        │
        ▼
CitationManager.register_chunks():
  [1] → chunk_id: abc123, source: "Q3 2023 10-K, Page 42"
  [2] → chunk_id: def456, source: "Annual Report 2022, Section 3.2"
  [3] → chunk_id: ghi789, source: "SEC Form 8-K, Nov 2023"
        │
        ▼
Injected into LLM prompt:
  "When citing information, use [1], [2], [3] references.
   Context:
   [1] Revenue for Q3 2023 was $23.35B...
   [2] Prior year comparison showed $19.96B...
   [3] CFO statement: margin pressure from pricing strategy..."
        │
        ▼
LLM answer:
  "Revenue grew 17% YoY to $23.35B [1], up from $19.96B in Q3 2022 [2].
   The CFO noted margin pressure from pricing decisions [3]."
        │
        ▼
ProvenanceLedger.verify_citations():
  Checks [1], [2], [3] exist in KnowledgeStore ✓
  Maps each citation back to source document
  Records: answer_claim → chunk → ingestion_provenance → raw document
```

### AuditLog entry structure

```json
{
  "event_type": "rag_retrieval",
  "tenant_id": "abc-123",
  "goal_id": "goal-xyz",
  "query": "What was Tesla Q3 2023 revenue?",
  "strategy": "hybrid",
  "retrieved_chunks": [
    {
      "chunk_id": "abc123",
      "source_uri": "s3://bucket/tesla-10q-q3-2023.pdf",
      "page": 42,
      "content_hash": "sha256:...",
      "data_classification": "public",
      "relevance_score": 0.94
    }
  ],
  "answer_excerpt": "Revenue grew 17% YoY to $23.35B [1]...",
  "citations_verified": true,
  "cost_usd": 0.0023,
  "latency_ms": 387,
  "timestamp": "2024-01-15T14:23:07Z"
}
```

---

## Full Integration Example: Financial Compliance Query

**Query:** "What were the material risk factors disclosed by Goldman Sachs in their 2023 10-K that could affect dividend payments?"

**Documents ingested:**
- 500K SEC filings (10-K, 10-Q, 8-K) using PDFLayoutChunker
- Classified: `sensitivity=internal`, `category=financial`

```
Step 1: INGESTION (already done)
  PDFLayoutChunker splits GS 2023 10-K into 340 chunks
  Each chunk: DataClassification = {sensitivity: internal, category: financial}
  Embeddings: premium tier (text-embedding-3-large, $0.13/1M tokens)
  ProvenanceBuilder records: source=GS-10K-2023.pdf, page ranges, ingestion time

Step 2: PATTERN SELECTION
  PatternAssembler: complexity=HARD, domain=FINANCIAL, risk=HIGH
  → rag_patterns = ["corrective_rag", "multi_hop"]
  
Step 3: RETRIEVAL (CORRECTIVE_RAG mode)
  Hop 1: "Goldman Sachs 2023 10-K risk factors"
    → Hybrid search: 8 chunks retrieved
    → GRADER: grades each chunk
       CORRECT: "Market Risk section: counterparty exposure, credit risk..."
       INCORRECT: 2022 filing section (wrong year) → discarded
       CORRECT: "Regulatory Risk: capital requirements, stress tests..."
    → 6 chunks kept, 2 discarded

  Hop 2: "Goldman Sachs dividend policy and constraints"
    → Retrieves: "Capital allocation policy: dividends subject to regulatory
                  capital ratios, board approval..."

Step 4: DATA CLASSIFICATION CHECK
  All chunks: category=financial, handling=no_log
  → AuditLog will NOT include chunk content (privacy)
  → Will include: chunk_ids, source_uris, relevance scores

Step 5: CONTEXT PIPELINE
  PromptBudget: 6000 tok, drops lowest-ranked chunk (within budget)
  CitationManager: [1]...[5] assigned to chunks
  OutputContractBuilder: requires structured JSON response with citations

Step 6: LLM GENERATION
  PromptOptimizer.select_variant("planner") → compliance-optimized template
  Answer: "Goldman Sachs 2023 10-K identifies five material risk factors 
           affecting dividend payments:
           1. Regulatory capital requirements (Basel III) [1][3]
           2. Credit market volatility exposure [2]
           3. Counterparty risk concentration [2]
           4. Federal Reserve stress test results [3]
           5. Litigation provisions [4]
           Dividends require board approval and are constrained by CET1 
           capital ratio targets set in the firm's capital plan [1][3]."

Step 7: POST-GENERATION
  ProvenanceLedger.verify_citations(): [1][2][3][4] all verified ✓
  GroundingChecker: all claims grounded in retrieved context ✓
  AuditLog: records query + chunk_ids + cost (no chunk content, no_log flag)
  
Total latency: 2.8 seconds
Cost: $0.031 (premium embeddings + corrective grading + GPT-4o generation)
Citations: 4 verified, all from 2023 10-K (not prior years)
```

---

## Key Design Principles

1. **Chunking quality > algorithm sophistication**: The best RAG algorithm with bad chunks produces bad results. Invest in chunking strategy first.

2. **Cache aggressively**: 65% cache hit rate means 65% of your infrastructure cost disappears. SemanticCache L1+L2 is the highest-ROI optimization.

3. **Classify at ingest, not at query**: DataClassification runs once per chunk at ingest time. Enforcing it at query time costs nothing.

4. **Cite everything**: CitationManager + ProvenanceLedger creates an unbroken chain from answer to raw document. This is non-negotiable for compliance use cases.

5. **Match pattern to query complexity**: Using RAPTOR for "What is our PTO policy?" wastes 5× the latency and cost of Naive RAG.

6. **Governa at the boundary, not the center**: GuardrailsV2, DataClassification, PolicyEngine run at the data layer — they can't be bypassed by changing the LLM or the retrieval strategy.

---

## Real-World Examples

**Real-World Example 1 — Research Consultancy (Full Integration Trace Across All Pipeline Stages)**

> A research consultancy deploys an AgentVerse agent over a 2M-document corpus of academic papers and industry reports. For the goal "Summarise the current evidence on mRNA vaccine efficacy against Omicron subvariants": the `PatternAssembler` detects `domain=RESEARCH, complexity=HARD` and selects `["multi_hop", "corrective_rag"]`; Knowledge Graph traversal in `app/knowledge_graph/` identifies 14 related topic nodes (vaccine efficacy, Omicron BA.4, BA.5, XBB.1.5) and expands the retrieval scope before vector search; three retrieval hops via `KnowledgeStore.hybrid_search()` surface 22 candidate chunks; `SemanticCache` L2 returns a partial match from a prior similar query, injecting 2 cached segments to reduce LLM calls; `CitationManager` assigns `[1]`–`[11]` to the final 11 high-ranked chunks; the Planner produces a 380-token answer; `ProvenanceLedger.verify_citations()` confirms all 11 citations resolve to real KnowledgeStore entries; `AuditLog` records the full retrieval lineage including DOIs, ingestion timestamps, and data classification (`sensitivity=public`). Total latency: 4.2 seconds; total cost: $0.047.

**Real-World Example 2 — Telecom Customer Support (Guardrail Intercepts Mislabelled Competitor Chunk)**

> A telecom's customer support agent ingested 40,000 internal KB articles during a bulk migration, but 12 competitor comparison documents were accidentally tagged `sensitivity=public` instead of `sensitivity=confidential`. When a customer asked "How do we compare to [Competitor X] on enterprise pricing?", hybrid search retrieved a chunk from a competitor's internal pricing PDF that had been ingested inadvertently. The `DataClassification` check at retrieval time read `category=financial, source_domain=external_competitor`; the `PolicyEngine` rule "do not retrieve from collections tagged `external_competitor`" fired and excluded the chunk before it reached the LLM context window. The agent responded using only verified internal pricing knowledge. The incident triggered a `PolicyViolation` audit event that identified the mislabelled batch, allowing the security team to re-classify all 12 documents and remove them from the retrieval index within 55 minutes of the first query that would have exposed them.
