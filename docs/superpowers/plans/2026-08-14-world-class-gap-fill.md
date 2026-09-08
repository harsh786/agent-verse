# Gap-Fill Implementation Plan — World-Class AgentVerse

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all feasible missing/partial features identified in the gap analysis to bring AgentVerse from ~84% to ~95% world-class coverage across the 20 audit categories.

**Architecture:** Each gap is self-contained. New modules follow existing patterns (dataclass + service class + async methods). All changes are backward-compatible; nothing is deleted.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Redis, pgvector, LangGraph, Celery

---

## Scope: 25 Implementable Gaps (Grouped into 9 Phases)

**Excluded** (require ML training infrastructure outside this codebase):
- Online RLHF / fine-tuning loop
- MCTS (Monte Carlo Tree Search)  
- SPLADE / learned sparse retrieval (needs model weights)
- Matryoshka embeddings (provider-dependent)

---

## Files to Create / Modify

### Phase 1 — Memory Intelligence (3 missing)
| File | Action | Purpose |
|---|---|---|
| `app/memory/salience.py` | **CREATE** | Memory importance scoring & decay |
| `app/memory/consolidation.py` | **CREATE** | Memory compression/consolidation pipeline |
| `app/memory/working_memory.py` | **CREATE** | Bounded short-term working memory window |
| `app/memory/long_term.py` | **MODIFY** | Add salience field + decay factor to LongTermMemory |
| `tests/memory/test_salience.py` | **CREATE** | Tests for salience scoring |
| `tests/memory/test_consolidation.py` | **CREATE** | Tests for memory consolidation |
| `tests/memory/test_working_memory.py` | **CREATE** | Tests for working memory |

### Phase 2 — Knowledge Graph Auto-Wiring (2 missing/partial)
| File | Action | Purpose |
|---|---|---|
| `app/knowledge_graph/ingestion_hook.py` | **CREATE** | Auto-extract entities/relations during ingestion |
| `app/knowledge_graph/multi_hop.py` | **CREATE** | BFS/DFS multi-hop path reasoning over KG |
| `app/ingestion/orchestrator.py` | **MODIFY** | Call `ingestion_hook` after chunking |
| `tests/knowledge_graph/test_ingestion_hook.py` | **CREATE** | Tests for KG auto-wiring |
| `tests/knowledge_graph/test_multi_hop.py` | **CREATE** | Tests for multi-hop reasoning |

### Phase 3 — Contextual Chunk Enrichment & Late Chunking (2 missing)
| File | Action | Purpose |
|---|---|---|
| `app/rag/contextual_enricher.py` | **CREATE** | Prepend doc-level summary context to each chunk before embedding |
| `app/rag/late_chunker.py` | **CREATE** | Embed full document → slice embeddings post hoc |
| `app/ingestion/orchestrator.py` | **MODIFY** | Hook contextual enricher as optional post-process |
| `tests/rag/test_contextual_enricher.py` | **CREATE** | Tests |
| `tests/rag/test_late_chunker.py` | **CREATE** | Tests |

### Phase 4 — Hallucination: NLI + Claim Decomposition (2 missing)
| File | Action | Purpose |
|---|---|---|
| `app/intelligence/nli_checker.py` | **CREATE** | NLI-based factual consistency scoring (heuristic + LLM fallback) |
| `app/intelligence/claim_decomposer.py` | **CREATE** | Decompose answer into atomic claims + verify each |
| `app/agent/grounding.py` | **MODIFY** | Wire NLI checker + claim decomposer into grounding check |
| `tests/intelligence/test_nli_checker.py` | **CREATE** | Tests |
| `tests/intelligence/test_claim_decomposer.py` | **CREATE** | Tests |

### Phase 5 — Streaming Guardrails + Toxicity Detection (2 missing)
| File | Action | Purpose |
|---|---|---|
| `app/guardrails_v2/streaming_guard.py` | **CREATE** | Mid-stream token-level guardrail filter |
| `app/guardrails_v2/toxicity.py` | **CREATE** | Toxicity/hate-speech pattern classifier |
| `app/agent/graph.py` | **MODIFY** | Wire `StreamingGuard` into `_on_token` callback |
| `tests/guardrails/test_streaming_guard.py` | **CREATE** | Tests |
| `tests/guardrails/test_toxicity.py` | **CREATE** | Tests |

### Phase 6 — Observability: Alert Routing + SLO Dashboard (2 missing/partial)
| File | Action | Purpose |
|---|---|---|
| `app/observability/alert_router.py` | **CREATE** | Threshold-based alert routing to Slack/webhook |
| `app/observability/slo_tracker.py` | **CREATE** | SLO burn-rate tracking (error budget per window) |
| `app/api/observability.py` | **MODIFY** | Add `GET /slo/burn-rate` + `POST /alerts/config` endpoints |
| `tests/observability/test_alert_router.py` | **CREATE** | Tests |
| `tests/observability/test_slo_tracker.py` | **CREATE** | Tests |

### Phase 7 — SharePoint / MS365 Connector (1 missing)
| File | Action | Purpose |
|---|---|---|
| `app/ingestion/connectors/sharepoint_connector.py` | **CREATE** | SharePoint + OneDrive REST API connector |
| `app/ingestion/connectors/__init__.py` | **MODIFY** | Export `SharePointConnector` |
| `app/api/knowledge.py` | **MODIFY** | Add `POST /ingest/sharepoint` endpoint |
| `tests/ingestion/test_sharepoint_connector.py` | **CREATE** | Tests |

### Phase 8 — Multi-turn Eval + Attribution Verification (2 missing/partial)
| File | Action | Purpose |
|---|---|---|
| `app/evals/multi_turn_eval.py` | **CREATE** | Multi-turn dialogue eval runner |
| `app/evals/attribution_verifier.py` | **CREATE** | Per-claim attribution verification against retrieved chunks |
| `app/evals/goal_score.py` | **MODIFY** | Wire attribution verifier into goal scoring |
| `tests/evals/test_multi_turn_eval.py` | **CREATE** | Tests |
| `tests/evals/test_attribution_verifier.py` | **CREATE** | Tests |

### Phase 9 — Dynamic Model Complexity Routing + Shadow Routing (2 missing/partial)
| File | Action | Purpose |
|---|---|---|
| `app/ai_router/complexity_scorer.py` | **CREATE** | Score query complexity → route to appropriate model tier |
| `app/ai_router/shadow_router.py` | **CREATE** | Shadow route to candidate model, compare, do not use for user response |
| `app/ai_router/router.py` | **MODIFY** | Wire complexity scorer and shadow router |
| `tests/ai_router/test_complexity_scorer.py` | **CREATE** | Tests |
| `tests/ai_router/test_shadow_router.py` | **CREATE** | Tests |

---

## Phase-by-Phase Task Breakdown

---

### Phase 1: Memory Intelligence

#### Task 1.1 — Memory Salience Scoring
- [ ] Create `app/memory/salience.py` with `SalienceScorer` class
  - `score(memory: LongTermMemory, query: str) -> float` — TF-IDF relevance × recency × access_count
  - `apply_decay(memory: LongTermMemory, days_since_access: float) -> LongTermMemory` — exponential decay
- [ ] Add `salience_score: float = 1.0`, `access_count: int = 0`, `last_accessed_at: datetime` fields to `LongTermMemory` in `app/memory/long_term.py`
- [ ] Write test: scoring higher for recently accessed memories
- [ ] Write test: decay reduces salience over time
- [ ] Run tests; commit

#### Task 1.2 — Memory Consolidation
- [ ] Create `app/memory/consolidation.py` with `MemoryConsolidator`
  - `consolidate(memories: list[LongTermMemory], provider: LLMProvider) -> list[LongTermMemory]` — cluster by topic, summarize clusters >3 items
  - `_cluster_memories(memories) -> dict[str, list]` — keyword-based clustering
- [ ] Write test: consolidate 10 memories into 3 summaries
- [ ] Write test: idempotent on already-small set
- [ ] Run tests; commit

#### Task 1.3 — Working Memory
- [ ] Create `app/memory/working_memory.py` with `WorkingMemory`
  - Bounded `deque` of max N items (default 10)
  - `push(item: dict) -> None` — adds item, evicts oldest on overflow
  - `snapshot() -> list[dict]` — current contents
  - `clear() -> None`
- [ ] Write test: eviction at capacity
- [ ] Write test: snapshot returns correct order
- [ ] Run tests; commit

---

### Phase 2: Knowledge Graph Auto-Wiring

#### Task 2.1 — KG Ingestion Hook
- [ ] Create `app/knowledge_graph/ingestion_hook.py` with `KGIngestionHook`
  - `async process(chunks: list[str], document_id: str, tenant_id: str, provider: LLMProvider, kg_store: KGStore) -> None`
  - Calls `extract_entities_llm` and `extract_relationships_llm` from existing extractor
  - Stores results in `kg_store`
  - Graceful: if LLM unavailable, falls back to deterministic extraction
- [ ] Modify `app/ingestion/orchestrator.py`: after `ingest()` succeeds, fire `KGIngestionHook.process()` as background task
- [ ] Write test: hook extracts entities from sample text
- [ ] Write test: hook is silent on failure (does not break ingestion)
- [ ] Run tests; commit

#### Task 2.2 — Multi-Hop Graph Reasoning
- [ ] Create `app/knowledge_graph/multi_hop.py` with `MultiHopReasoner`
  - `find_paths(start: str, end: str, max_hops: int = 3) -> list[list[str]]` — BFS over KG edges
  - `answer_via_path(query: str, path: list[str], store: KGStore) -> str` — chain facts along path
  - `retrieve_subgraph(entity: str, depth: int = 2, store: KGStore) -> dict` — ego-network extraction
- [ ] Write test: BFS finds 2-hop path between two connected entities
- [ ] Write test: depth-limited subgraph extraction
- [ ] Run tests; commit

---

### Phase 3: Contextual Chunk Enrichment & Late Chunking

#### Task 3.1 — Contextual Enricher (Anthropic-style contextual RAG)
- [ ] Create `app/rag/contextual_enricher.py` with `ContextualChunkEnricher`
  - `enrich(chunks: list[str], document_summary: str) -> list[str]`
    - Prepends `"[Context: {document_summary[:200]}]\n\n"` to each chunk
  - `summarize_document(content: str, provider: LLMProvider) -> str` — 1-paragraph summary via LLM
  - `enrich_with_llm(chunks, full_content, provider) -> list[str]` — per-chunk contextual prefix via LLM
- [ ] Add `use_contextual_enrichment: bool = False` field to `IngestionOrchestrator.__init__`
- [ ] Wire into `orchestrator.ingest()` if flag is set and embedder is available
- [ ] Write test: enriched chunk contains document context prefix
- [ ] Write test: graceful when LLM unavailable (returns original chunks)
- [ ] Run tests; commit

#### Task 3.2 — Late Chunker
- [ ] Create `app/rag/late_chunker.py` with `LateChunker`
  - `chunk_and_embed(content: str, embedder: LLMProvider) -> list[LateChunk]`
    - Embed full document → get token-level embeddings
    - Split at sentence boundaries → assign average of token embeddings per chunk
  - `LateChunk(content: str, embedding: list[float], span: tuple[int, int])`
  - Fallback: if embedder doesn't support token embeddings, return `None` (caller uses standard embedding)
- [ ] Write test: produces chunks with embeddings
- [ ] Write test: fallback returns None gracefully
- [ ] Run tests; commit

---

### Phase 4: Hallucination — NLI + Claim Decomposition

#### Task 4.1 — NLI Checker
- [ ] Create `app/intelligence/nli_checker.py` with `NLIChecker`
  - `check_consistency(claim: str, evidence: str, provider: LLMProvider) -> NLIResult`
    - Prompt: "Does the evidence support the claim? Answer: ENTAILS / CONTRADICTS / NEUTRAL"
    - Returns `NLIResult(verdict: str, confidence: float)`
  - `check_answer_consistency(answer: str, chunks: list[str], provider) -> float` — average over chunks
- [ ] Write test: `ENTAILS` when evidence directly supports claim
- [ ] Write test: `CONTRADICTS` when evidence refutes claim
- [ ] Write test: handles provider error gracefully (returns NEUTRAL)
- [ ] Run tests; commit

#### Task 4.2 — Claim Decomposer
- [ ] Create `app/intelligence/claim_decomposer.py` with `ClaimDecomposer`
  - `decompose(text: str, provider: LLMProvider) -> list[str]` — split into atomic factual claims
  - `verify_claims(claims: list[str], evidence_chunks: list[str], nli: NLIChecker, provider) -> ClaimVerificationReport`
  - `ClaimVerificationReport(claims, verdicts, overall_score, unsupported_claims)`
- [ ] Modify `app/agent/grounding.py`: add `verify_with_claims(answer, chunks, provider) -> float` using `ClaimDecomposer`
- [ ] Write test: decompose "The sky is blue and water is wet" into 2 claims
- [ ] Write test: `ClaimVerificationReport.overall_score` is 1.0 when all claims supported
- [ ] Run tests; commit

---

### Phase 5: Streaming Guardrails + Toxicity

#### Task 5.1 — Streaming Guard
- [ ] Create `app/guardrails_v2/streaming_guard.py` with `StreamingGuard`
  - `__init__(patterns: list[str], buffer_size: int = 200)` — patterns as regex
  - `async check_token(token: str) -> GuardDecision` — accumulate in rolling buffer, check patterns
  - `GuardDecision(allow: bool, reason: str | None, matched_pattern: str | None)`
  - `reset()` — clear buffer between goals
- [ ] Modify `app/agent/graph.py` `_on_token` callback: pass token through `StreamingGuard`; if blocked, set `_token_buffer.append("[REDACTED]")`
- [ ] Write test: pattern match triggers block
- [ ] Write test: normal token stream passes through
- [ ] Write test: buffer rolls correctly (only last N chars scanned)
- [ ] Run tests; commit

#### Task 5.2 — Toxicity Classifier
- [ ] Create `app/guardrails_v2/toxicity.py` with `ToxicityClassifier`
  - Pattern-based first pass (hate speech, slurs, threats) with curated word list
  - `classify(text: str) -> ToxicityResult(score: float, categories: list[str], is_toxic: bool)`
  - LLM-based second pass for ambiguous cases (score between 0.3–0.7)
  - `ToxicityResult.categories` includes: `hate_speech`, `threat`, `sexual`, `self_harm`, `violence`
- [ ] Wire into `app/guardrails_v2/engine.py` as a check step
- [ ] Write test: clear toxic content returns `is_toxic=True`
- [ ] Write test: normal text returns `is_toxic=False`
- [ ] Write test: score thresholds are correct
- [ ] Run tests; commit

---

### Phase 6: Observability — Alert Routing + SLO Tracker

#### Task 6.1 — Alert Router
- [ ] Create `app/observability/alert_router.py` with `AlertRouter`
  - `AlertRule(metric: str, threshold: float, window_seconds: int, severity: str, webhook_url: str)`
  - `async evaluate(metric_name: str, value: float, tenant_id: str) -> list[FiredAlert]`
  - `async send_alert(alert: FiredAlert, webhook_url: str) -> None` — POST JSON to webhook (Slack, PagerDuty compatible)
  - `register_rule(rule: AlertRule) -> None`
- [ ] Add `GET /observability/alert-rules` and `POST /observability/alert-rules` to `app/api/observability.py`
- [ ] Write test: rule fires when threshold exceeded
- [ ] Write test: rule does not fire below threshold
- [ ] Write test: webhook send is async and non-blocking
- [ ] Run tests; commit

#### Task 6.2 — SLO Burn Rate Tracker
- [ ] Create `app/observability/slo_tracker.py` with `SLOTracker`
  - `SLODefinition(name: str, target: float, window_hours: int)` (e.g., 99.9% success over 24h)
  - `record_event(success: bool, tenant_id: str) -> None` — store in Redis sorted set with timestamp
  - `burn_rate(tenant_id: str, slo: SLODefinition) -> SLOStatus` — current error rate vs target
  - `SLOStatus(slo_name, target, current_rate, burn_rate_multiple, minutes_to_exhaustion)`
- [ ] Add `GET /slo/status` endpoint to `app/api/observability.py`
- [ ] Write test: 100% success = burn_rate 0
- [ ] Write test: all failures = burn_rate exhausts budget instantly
- [ ] Run tests; commit

---

### Phase 7: SharePoint / MS365 Connector

#### Task 7.1 — SharePoint Connector
- [ ] Create `app/ingestion/connectors/sharepoint_connector.py` with `SharePointConnector`
  - Uses Microsoft Graph API (`https://graph.microsoft.com/v1.0`)
  - `__init__(tenant_id, client_id, client_secret)` — OAuth2 client credentials
  - `async get_access_token() -> str` — POST to `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`
  - `async list_files(site_id: str, drive_id: str) -> list[dict]` — GET `/sites/{site}/drives/{drive}/root/children`
  - `async download_file(item_id: str, site_id: str) -> str` — GET `/sites/{site}/drive/items/{id}/content`
  - `async list_sites() -> list[dict]` — GET `/sites?search=*`
- [ ] Update `app/ingestion/connectors/__init__.py` to export `SharePointConnector`
- [ ] Add `POST /ingest/sharepoint` to `app/api/knowledge.py` with `SharePointIngestRequest(tenant_id, client_id, client_secret, site_id, drive_id, collection_id)`
- [ ] Write test: `_blocks_to_url()` produces correct Graph API URL
- [ ] Write test: connector handles auth error gracefully
- [ ] Write test: file list parsing
- [ ] Run tests; commit

---

### Phase 8: Multi-turn Eval + Attribution Verifier

#### Task 8.1 — Multi-turn Dialogue Eval
- [ ] Create `app/evals/multi_turn_eval.py` with `MultiTurnEvaluator`
  - `Turn(role: str, content: str)`
  - `MultiTurnCase(turns: list[Turn], expected_final: str, eval_criteria: list[str])`
  - `async evaluate(case: MultiTurnCase, agent_fn: Callable, provider: LLMProvider) -> MultiTurnResult`
  - `MultiTurnResult(turns_completed, coherence_score, goal_achieved, per_turn_scores)`
  - Coherence: LLM judge scores each assistant turn for relevance to conversation history
- [ ] Write test: coherent dialogue scores > 0.7
- [ ] Write test: non-sequitur response scores low
- [ ] Run tests; commit

#### Task 8.2 — Attribution Verifier
- [ ] Create `app/evals/attribution_verifier.py` with `AttributionVerifier`
  - `verify(answer: str, citations: list[Citation], chunks: list[str]) -> AttributionReport`
  - For each citation, check if chunk text actually supports the answer sentence referencing it
  - `AttributionReport(verified_count, failed_count, unsupported_claims, precision_score)`
  - `_sentence_overlap(claim: str, chunk: str) -> float` — Jaccard similarity of key terms
- [ ] Modify `app/evals/goal_score.py`: add `attribution_score` to `GoalScore` using `AttributionVerifier`
- [ ] Write test: citation to directly relevant chunk scores high
- [ ] Write test: citation to unrelated chunk scores low
- [ ] Run tests; commit

---

### Phase 9: Dynamic Model Routing + Shadow Routing

#### Task 9.1 — Query Complexity Scorer
- [ ] Create `app/ai_router/complexity_scorer.py` with `QueryComplexityScorer`
  - `score(query: str, context: dict | None = None) -> ComplexityScore`
  - Features: word count, clause count (`,`, `;`), question words, technical term density, multi-part indicator (`and also`, `furthermore`)
  - Returns `ComplexityScore(level: Literal["simple","moderate","complex"], score: float, features: dict)`
  - Routing table: simple→smallest model, moderate→mid model, complex→largest model
- [ ] Modify `app/ai_router/router.py`: call `ComplexityScorer.score()` before model selection; override role model if complexity demands it
- [ ] Write test: "What time is it?" → simple
- [ ] Write test: multi-clause research question → complex
- [ ] Run tests; commit

#### Task 9.2 — Shadow Router
- [ ] Create `app/ai_router/shadow_router.py` with `ShadowRouter`
  - `async shadow_call(request: CompletionRequest, primary_provider: LLMProvider, shadow_provider: LLMProvider) -> ShadowResult`
  - Fires both calls concurrently via `asyncio.gather`; returns primary response to user
  - Records shadow response, latency delta, and token count to `shadow_routing_log` table (Redis list with TTL)
  - `ShadowResult(primary_response, shadow_response, latency_delta_ms, shadow_provider)`
  - `ShadowRoutingConfig(enabled: bool, shadow_provider_id: str, sample_rate: float = 0.1)` — only shadow N% of requests
- [ ] Add `GET /ai-router/shadow-log` endpoint to expose recent shadow results for analysis
- [ ] Write test: shadow call fires both providers
- [ ] Write test: primary response is always returned regardless of shadow result
- [ ] Write test: sample_rate=0 disables shadow
- [ ] Run tests; commit

---

## Testing Standards

- Every new class has at least 3 unit tests
- Provider-dependent methods use `FakeProvider` from `app/providers/fake.py`
- No integration tests (no real DB/Redis calls in unit tests)
- All tests use `pytest` + `pytest-asyncio`
- Run `uv run pytest tests/<new_test_file> --no-cov -v` after each task

## Commit Convention

Each phase gets a single commit:
- `feat(memory): salience scoring, consolidation, working memory`
- `feat(kg): auto-wiring ingestion hook + multi-hop path reasoning`
- `feat(rag): contextual chunk enrichment + late chunking`
- `feat(intelligence): NLI checker + claim decomposer`
- `feat(guardrails): streaming guard + toxicity classifier`
- `feat(observability): alert router + SLO burn-rate tracker`
- `feat(ingestion): SharePoint/OneDrive connector + API endpoint`
- `feat(evals): multi-turn eval + attribution verifier`
- `feat(ai-router): complexity scorer + shadow router`
