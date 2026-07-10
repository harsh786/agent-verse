# Use Cases 31–40: Sales / CRM & Finance Operations

---

## UC31 — Find Similar Past Zendesk Tickets for a New Issue

**Goal:** Given a newly opened support ticket, retrieve the top-5 most similar resolved past tickets and surface their resolutions to the handling agent.

**Business problem:** Support agents spend 10–15 min per ticket manually hunting for precedent. LTM-backed retrieval with ColBERT reranking cuts average handle time by ~40% and prevents duplicate escalations.

**Actors:** Support agent (trigger), `KnowledgeStore`, `LongTermMemoryStore`, Zendesk ticket writer (consumer)

**Inputs:**
- New ticket text (subject + body, UTF-8)
- Optional: product-area tag, severity label

**Agent pattern:** ReAct (single iteration) — retrieve → surface. Low-complexity; no planning needed.

**RAG pattern:** `colbert` — late-interaction MaxSim token-level reranking rescores the dense+BM25 fused shortlist (top-20 → top-5). Highly effective for intent-overlapping but lexically-divergent ticket pairs.

**Memory used:**
- **Long-term memory (LTM):** `LongTermMemoryStore` accumulates resolved-ticket summaries with voyage-3-large embeddings; retrieval strategy = `memory` (cosine over LTM vectors, BM25 fallback).
- Execution memory: not used (single-step, no multi-turn state).

**Ingestion path:**
1. Zendesk nightly export via `zendesk_server.py → list_tickets(status=closed, since=7d)`.
2. Each ticket body → `ingestion/orchestrator.py` → `content_classifier.py` (type=`support_ticket`).
3. Chunker: `semantic` (256 tokens, 32 overlap); title stored as separate chunk.
4. Embedder: voyage-3-large (1536d). Vectors + BM25 tokens upserted to `KnowledgeStore`.
5. Resolution summaries written to `LongTermMemoryStore` via `memory/long_term.py`.

**Retrieval path:**
1. New ticket text embedded → dense recall top-20 from pgvector (`rag/store.py`).
2. BM25 recall top-20 (`rag/bm25.py`).
3. Reciprocal-rank fusion merges both lists.
4. `colbert` reranker (`rag/agentic/patterns/colbert.py`) rescores fused top-20 → returns top-5 with MaxSim scores.
5. Citation metadata (ticket ID, resolution, agent notes) attached via `rag/agentic/citation_threader.py`.

**Model routing:** `gpt-4o-mini` — `model_router.py` selects `tier=low` for short-context single-step summary tasks.

**Guardrails:**
- Output ≤ 500 tokens; no full ticket body exposure.
- Customer name/email masked in retrieved context (`intelligence/guardrails.py`).
- Tool scope: `zendesk:read` only (`governance/permissions.py`).

**End-to-end flow:**
1. Goal received: `"Find similar past tickets for: #98234 — Login fails after SSO token refresh"`.
2. Executor calls `zendesk_server.py → get_ticket(id=98234)` → retrieves full ticket object.
3. Executor calls `rag/agentic/retriever_tool.py` with strategy=`memory`, pattern=`colbert`, k=5.
4. ColBERT reranker scores fused top-20 → returns top-5 as `[score, ticket_id, resolution_summary]`.
5. Verifier checks: ≥3 results returned, each has non-empty resolution → `success`.
6. Executor renders ranked list with resolution snippets as structured JSON.
7. SSE streams result to agent UI; `governance/audit.py` logs READ event.

**Observability:**
- `observability/rag_trace.py`: query, strategy, pattern, k, latency, ColBERT scores.
- `observability/metrics.py`: `ticket_similarity_p95_latency`, `colbert_rerank_score_mean`.
- `governance/audit.py`: READ event on KnowledgeStore with retrieved ticket IDs.

**Eval path:**
- `evals/rag_score.py`: NDCG@5 against human-labeled similar-ticket pairs. Target ≥ 0.82.
- `evals/goal_score.py`: ≥3/5 results rated helpful by agent. Target ≥ 0.80.
- Offline: ColBERT vs. cross-encoder on 500 ticket pairs; ColBERT wins on latency.

**Expected output:**
```json
{
  "similar_tickets": [
    {"id": "TKT-7821", "score": 0.94, "resolution": "Reset PKCE token cache on SSO provider."},
    {"id": "TKT-6612", "score": 0.91, "resolution": "Revoke and re-issue OAuth refresh token."}
  ]
}
```

**Failure modes:**
- Cold LTM (< 100 resolved tickets): fall back to BM25-only; log `ltm_cold_start` warning.
- ColBERT unavailable: fall back to cross-encoder (`rag/cross_encoder.py`).
- Ticket body empty: agent returns `NO_CONTENT`; `evals/safety_score.py` flags goal.

**Code references:**
- `app/mcp/servers/zendesk_server.py` — Zendesk MCP connector (list/get tickets)
- `app/rag/agentic/patterns/colbert.py` — ColBERT late-interaction reranker
- `app/memory/long_term.py` — LTM write and cosine query
- `app/rag/bm25.py` — BM25 recall
- `app/rag/agentic/citation_threader.py` — citation metadata attachment
- `app/governance/audit.py` — READ event audit trail
- `app/intelligence/guardrails.py` — PII masking
- `app/agent/model_router.py` — tier=low routing

---

## UC32 — Generate FAQ from 6 Months of Support Tickets

**Goal:** Bulk-export 6 months of Zendesk tickets, cluster them by topic, and generate a structured FAQ document with question/answer pairs ready for a Confluence knowledge base.

**Business problem:** Support knowledge base is stale; agents waste time answering the same questions. Auto-generated FAQs from real ticket data reduce ticket volume by 20–30% via self-service deflection.

**Actors:** Support manager (trigger), `KnowledgeStore`, Confluence writer (consumer)

**Inputs:**
- Date range: last 180 days
- Optional: tag filter (e.g., `billing`, `auth`, `integrations`)
- Confluence space key for output publication

**Agent pattern:** Plan-Execute with RAPTOR synthesis. Planner decomposes by topic cluster; Executor generates per-cluster FAQs; final synthesis pass merges overlaps.

**RAG pattern:** `raptor` — hierarchical clustering of ticket summaries into topic trees; leaf nodes are individual tickets, cluster nodes are LLM-generated summaries. Enables cross-ticket synthesis at scale without exceeding context limits.

**Memory used:**
- Execution memory (`memory/execution.py`): tracks processed cluster IDs to enable resumption on partial failure.
- LTM: not used (batch pipeline, not interactive).

**Ingestion path:**
1. `zendesk_server.py → bulk_export_tickets(since=180d)` → paginated JSONL (up to 50k tickets).
2. `ingestion/orchestrator.py` → `content_classifier.py` → type=`support_ticket_bulk`.
3. Chunker: `semantic` per ticket; metadata fields (tags, resolution_time) preserved.
4. RAPTOR tree built by `rag/agentic/patterns/raptor.py`: embed all leaves, cluster (k-means, k=auto), summarize each cluster with gpt-4o, recurse until root node.
5. Cluster tree stored in `KnowledgeStore` with `cluster_level` metadata.

**Retrieval path:**
1. For each cluster, retrieve its summary node from RAPTOR tree.
2. Retrieve representative leaf tickets (top-3 per cluster by centrality) for example grounding.
3. `rag/agentic/patterns/raptor.py → synthesize_cluster(level=N)` generates FAQ Q&A pairs.
4. Cross-cluster dedup check via embedding similarity (cosine ≥ 0.92 → merge).

**Model routing:** `gpt-4o` — `model_router.py` selects `tier=high` for multi-document synthesis requiring deep coherence. Cluster summarization passes also use gpt-4o.

**Guardrails:**
- No customer PII in FAQ output (`intelligence/guardrails.py`; names/emails stripped).
- FAQ entries must be grounded in ≥3 ticket examples per Q&A (`governance/policies.py`).
- Hallucination check: `agent/grounding.py` verifies each answer against source tickets.

**End-to-end flow:**
1. Goal: `"Generate FAQ from last 6 months of Zendesk tickets, publish to Confluence space SUPP-KB"`.
2. Planner builds step list: bulk_export → cluster → per-cluster_FAQ_generate → dedup → publish.
3. Executor bulk-exports ~30k tickets from `zendesk_server.py`; progress stored in execution memory.
4. RAPTOR builds 4-level topic tree (leaves=tickets, L1=sub-topics, L2=topics, L3=root).
5. Executor generates Q&A pairs for each L1 cluster (avg 15 Q&As per cluster × 12 clusters = 180 Q&As).
6. Dedup pass removes 23 overlapping entries; final FAQ has 157 entries.
7. Verifier checks: ≥3 source citations per Q&A, no PII in output → `success`.
8. Executor calls `confluence_server.py → create_page(space=SUPP-KB, content=faq_markdown)`.
9. Audit log records page URL + source ticket count.

**Observability:**
- `observability/rag_trace.py`: RAPTOR tree depth, cluster count, synthesis latency per level.
- `observability/metrics.py`: `faq_generation_ticket_count`, `raptor_tree_levels`, `dedup_removed_count`.
- `governance/audit.py`: WRITE event on Confluence with FAQ page ID.

**Eval path:**
- `evals/rag_score.py`: ROUGE-L of generated answers vs. human-written FAQ samples. Target ≥ 0.55.
- `evals/goal_score.py`: completeness (all major ticket categories covered). Target ≥ 0.85.
- A/B: ticket deflection rate 30 days after FAQ publication vs. baseline.

**Expected output:** Confluence page with 150–200 Q&A pairs grouped by topic (auth, billing, integrations…), each with 3 source ticket links.

**Failure modes:**
- Zendesk bulk export rate-limit (429): exponential backoff in `mcp/client.py`; resumable from last cursor.
- RAPTOR cluster count = 1 (all tickets too similar): fall back to semantic chunking + flat summarization.
- Confluence write fails: save draft to MinIO via `rpa/artifacts.py`, alert manager.

**Code references:**
- `app/mcp/servers/zendesk_server.py` — bulk ticket export
- `app/rag/agentic/patterns/raptor.py` — hierarchical clustering and synthesis
- `app/mcp/servers/confluence_server.py` — knowledge base publication
- `app/memory/execution.py` — resumable pipeline state
- `app/ingestion/orchestrator.py` — bulk ingestion pipeline
- `app/agent/grounding.py` — answer grounding verification
- `app/intelligence/guardrails.py` — PII stripping

---

## UC33 — Enrich Salesforce Lead with Company Research

**Goal:** For a newly created Salesforce lead, auto-enrich the record with company size, funding stage, tech stack, and recent news by running parallel research via web scraping and CRM signals.

**Business problem:** SDRs spend 20–30 min per lead on manual research. Automated enrichment lifts contact-to-meeting conversion by enabling hyper-personalized outreach at scale.

**Actors:** Salesforce lead webhook (trigger), SDR (consumer), `GoalTreePattern`, RPA browser agent

**Inputs:**
- Salesforce Lead ID
- Lead email domain
- Optional: LinkedIn URL, company website

**Agent pattern:** `goal_tree` — root goal decomposed into 4 parallel sub-goals: (1) CRM signal extraction, (2) company website scraping, (3) tech-stack fingerprinting, (4) news/funding search. Results merged at root.

**RAG pattern:** `flare` (Forward-Looking Active Retrieval) — used for uncertain company data fields. When the agent is generating a company profile and confidence < 0.7 on a claim (e.g., funding stage), FLARE triggers an additional targeted retrieval pass before completing the sentence.

**Memory used:**
- Execution memory (`memory/execution.py`): sub-goal results stored per goal_tree branch for merge step.
- LTM: previously enriched companies cached to avoid re-scraping (`memory/long_term.py`).

**Ingestion path:**
1. RPA browser (`rpa/runner.py`) scrapes company website → `vision_parser.py` for visual content + `pdf_parser.py` for any investor deck PDFs found.
2. Scraped text → `ingestion/orchestrator.py` → `semantic` chunker.
3. News articles fetched via `serpapi_server.py`; `heading` chunker applied.
4. All vectors + BM25 tokens upserted to per-lead scoped `KnowledgeStore` partition.

**Retrieval path:**
1. FLARE monitors generation confidence via logprob thresholding.
2. On low-confidence spans, `flare.py` formulates a targeted query and retrieves from `KnowledgeStore`.
3. Retrieved facts injected into generation context; generation continues.
4. Final enrichment profile assembled from all sub-goal outputs + FLARE-verified facts.

**Model routing:** `gpt-4o` — `model_router.py` selects `tier=high` for complex cross-source synthesis. Sub-goal extractors use `gpt-4o-mini` (tier=low) for structured field extraction.

**Guardrails:**
- Only public data sources; no social engineering or credential-harvesting patterns (`intelligence/guardrail_patterns.py`).
- RPA session sandboxed (`rpa/session.py`): no auth flows, no form submission.
- GDPR: company data only, no individual profiling beyond lead contact (`governance/compliance_bundles.py`).

**End-to-end flow:**
1. Webhook fires on Salesforce `lead.created` event → goal submitted to AgentVerse.
2. `goal_tree.py` decomposes into 4 parallel branches; all dispatched concurrently.
3. Branch 1: `salesforce_server.py → get_lead(id)` extracts CRM fields (industry, company_size, last_activity).
4. Branch 2: RPA scrapes `{domain}/about` and `{domain}/careers` → extracts headcount signals, open roles.
5. Branch 3: BuiltWith-equivalent fingerprinting via `serpapi_server.py` → tech stack list.
6. Branch 4: News search → FLARE-enriched funding stage extraction from press releases.
7. Root node merges branches; FLARE resolves 3 uncertain fields (funding, ARR estimate, HQ).
8. Verifier checks: ≥8/12 enrichment fields populated with source citations → `success`.
9. `salesforce_server.py → update_lead(id, enriched_fields)` patches the record.
10. Execution memory archived; LTM updated with company profile for future leads from same domain.

**Observability:**
- `observability/pattern_trace.py`: goal_tree branch latencies, merge step timing.
- `observability/rag_trace.py`: FLARE trigger count, retrieval latency per confidence event.
- `observability/metrics.py`: `lead_enrichment_fields_populated_ratio`, `flare_trigger_rate`.

**Eval path:**
- `evals/goal_score.py`: field accuracy vs. manual enrichment gold set (200 leads). Target ≥ 0.88.
- `evals/rag_score.py`: FLARE precision (retrieved fact actually resolves uncertain field). Target ≥ 0.80.
- SDR feedback loop: thumbs-up/down per enrichment surfaced in Salesforce UI.

**Expected output:** Salesforce lead record updated with: company_size=1200, funding_stage=Series C, tech_stack=[Salesforce, Snowflake, dbt], hq=Austin TX, recent_news=["Raised $80M Series C — Jan 2026"], confidence_scores per field.

**Failure modes:**
- Company website blocks RPA (Cloudflare): fall back to `serpapi_server.py` search-only.
- Salesforce API rate limit: queue update in `services/goal_queue.py`, retry with jitter.
- FLARE retrieval loop > 3 iterations: break and mark field as `low_confidence`.

**Code references:**
- `app/mcp/servers/salesforce_server.py` — lead read/update
- `app/rag/agentic/patterns/flare.py` — confidence-triggered retrieval
- `app/agent/patterns/goal_tree.py` — parallel sub-goal decomposition
- `app/rpa/runner.py` — browser scraping execution
- `app/ingestion/parsers/vision_parser.py` — visual content extraction
- `app/memory/long_term.py` — company profile cache
- `app/memory/execution.py` — sub-goal result storage
- `app/governance/compliance_bundles.py` — GDPR data classification

---

## UC34 — Generate Personalized Sales Outreach Email

**Goal:** Given a Salesforce opportunity and the enriched lead profile, generate a personalized cold outreach email that references the prospect's business context, pain points, and relevant product capabilities.

**Business problem:** Generic outreach emails get < 2% reply rates. Personalized emails grounded in company research achieve 8–15% reply rates; manual personalization at scale is not feasible.

**Actors:** SDR / AE (trigger), Salesforce opportunity (source), Confluence sales playbooks (knowledge), email sender (consumer)

**Inputs:**
- Salesforce Opportunity ID (with enriched lead fields from UC33)
- Rep persona and tone preference (formal / conversational)
- Product capability tags to highlight

**Agent pattern:** `peer_review` — a Drafter agent generates the email; a Critic agent reviews for tone, factual accuracy, and personalization depth; a Reviser applies critiques; one iteration.

**RAG pattern:** `corrective_rag` — if the email draft references a product capability or pricing claim, Corrective RAG validates the claim against the Confluence playbook knowledge base. Incorrect claims trigger a correction retrieval pass before finalization.

**Memory used:**
- Execution memory: draft history and critique notes stored across peer_review turns.
- LTM: past successful emails for this prospect domain retrieved to inform tone and structure.

**Ingestion path:**
1. Confluence sales playbooks synced daily via `confluence_server.py → list_pages(space=SALES)`.
2. `knowledge/ingestors/confluence_ingestor.py` → `heading` chunker per section.
3. Product capability pages → `semantic` chunker.
4. Vectors in `KnowledgeStore` tagged with `source=confluence, doc_type=playbook`.

**Retrieval path:**
1. Drafter retrieves prospect context: `salesforce_server.py → get_opportunity(id)`.
2. Corrective RAG (`rag/agentic/patterns/corrective.py`) validates each factual claim in draft.
3. On incorrect claim: targeted retrieval from `KnowledgeStore` → correction injected into Reviser prompt.
4. LTM query for past successful emails from similar ICP (industry + size) via `memory/long_term.py`.

**Model routing:** `gpt-4o` — `model_router.py` selects `tier=high` for Drafter and Critic (nuanced personalization). Reviser uses `gpt-4o-mini`.

**Guardrails:**
- No false pricing claims: Corrective RAG enforces against playbook (`intelligence/guardrail_engine.py`).
- Email length: 150–300 words enforced in Verifier.
- Tone compliance: `exfil_guard.py` blocks any competitive disparagement.
- `governance/policies.py`: email must include opt-out language.

**End-to-end flow:**
1. SDR clicks "Generate Outreach" in Salesforce → goal submitted with opportunity ID.
2. Executor reads opportunity + enriched lead fields via `salesforce_server.py`.
3. Executor retrieves relevant playbook sections and past ICP-matched emails.
4. Drafter generates 250-word personalized email draft referencing funding news, tech stack, pain point.
5. Corrective RAG validates 3 product capability claims → 1 corrected (pricing tier updated).
6. Critic (Peer Review) scores draft: personalization=9/10, accuracy=8/10, CTA_clarity=7/10.
7. Reviser applies 2 critique items: sharpens CTA, adds specific ROI metric.
8. Verifier checks: word count 150–300, opt-out present, no pricing errors → `success`.
9. Final email returned to Salesforce UI for SDR review; logged to `governance/audit.py`.

**Observability:**
- `observability/pattern_trace.py`: peer_review iteration count, critic scores.
- `observability/rag_trace.py`: corrective_rag trigger count, correction latency.
- `governance/audit.py`: email generation event with opportunity ID.

**Eval path:**
- `evals/goal_score.py`: human rating of personalization depth (1–5 scale). Target ≥ 4.2.
- `evals/rag_score.py`: factual accuracy of capability claims vs. playbook. Target ≥ 0.95.
- Business metric: reply rate of AI-generated vs. manually written emails over 30-day cohort.

**Expected output:** 250-word email with: personalized opening referencing Series C funding, one specific pain-point hypothesis, two product capability references (corrective-validated), clear CTA for 15-min demo.

**Failure modes:**
- Salesforce opportunity has no enriched fields: agent requests UC33 enrichment first via goal chaining.
- Corrective RAG loops > 3 corrections: halt, flag as `high_uncertainty`, escalate to human reviewer.
- Peer Review critic score < 6/10 after 2 iterations: output as draft with low-confidence annotation.

**Code references:**
- `app/mcp/servers/salesforce_server.py` — opportunity and lead data
- `app/rag/agentic/patterns/corrective.py` — claim validation and correction
- `app/agent/patterns/peer_review.py` — Drafter/Critic/Reviser loop
- `app/knowledge/ingestors/confluence_ingestor.py` — playbook ingestion
- `app/memory/long_term.py` — ICP-matched email retrieval
- `app/agent/exfil_guard.py` — tone and content policy enforcement
- `app/governance/policies.py` — email compliance rules

---

## UC35 — Analyze Lost Deals by Reason Code

**Goal:** Aggregate 12 months of lost Salesforce opportunities, group by loss reason code, identify patterns (competitor wins, pricing objections, timing), and generate an executive summary with actionable recommendations.

**Business problem:** Win/loss analysis is done manually quarterly and is incomplete. Automated analysis surfaces patterns in days, enabling faster strategy pivots on pricing, product, and competitive positioning.

**Actors:** Revenue/Sales ops manager (trigger), `bigquery_server.py` (historical data), Salesforce (CRM data), executive report consumer

**Inputs:**
- Date range: last 365 days
- Salesforce stage filter: `Closed Lost`
- BigQuery dataset: `sales_analytics.opportunities_history`
- Optional: deal size threshold (e.g., > $10k ARR only)

**Agent pattern:** `self_consistency` — 3 independent analysis agents run in parallel over the same dataset, each producing a ranked list of loss patterns and recommendations. Majority-vote consensus determines the final ranked list.

**RAG pattern:** `raptor` — hierarchical clustering of deal narratives (close notes, last-activity notes) to surface latent themes across hundreds of records without context-window overflow.

**Memory used:**
- Execution memory: BigQuery query results and per-cluster summaries cached to avoid re-querying.
- LTM: previous quarter's analysis retrieved for trend delta comparison.

**Ingestion path:**
1. `bigquery_server.py → run_query("SELECT * FROM opportunities_history WHERE stage='Closed Lost' AND close_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)")`.
2. Result rows → `ingestion/orchestrator.py` → each row treated as a document with `table` chunker.
3. Free-text fields (close_notes, competitor_mention) → `semantic` chunker (128 tokens).
4. RAPTOR tree built over close_notes embeddings: L0=individual deals, L1=reason-code clusters, L2=thematic groups.

**Retrieval path:**
1. Each Self-Consistency agent retrieves from RAPTOR tree at L2 (thematic) and L1 (reason-code) levels.
2. Each agent issues independent natural-language queries (e.g., "What are the top competitor wins?").
3. RAPTOR cluster summaries + representative leaf deals returned per query.
4. Majority vote on ranked reason codes: consensus kept, divergent items flagged for human review.

**Model routing:** `gpt-4o` — `model_router.py` `tier=high` for each Self-Consistency agent (analytical depth required). RAPTOR cluster summarization uses `gpt-4o`.

**Guardrails:**
- No individual rep names in executive report (aggregate only, `intelligence/guardrails.py`).
- BigQuery query parameterized to prevent SQL injection (`governance/policies.py`).
- Report length ≤ 2000 tokens; no raw deal data exported.

**End-to-end flow:**
1. Goal: `"Analyze all Closed Lost deals from last 12 months and identify top 5 loss patterns"`.
2. Executor queries BigQuery via `bigquery_server.py` → 847 lost deals retrieved.
3. Executor ingests close_notes into RAPTOR tree → 6 L1 clusters, 3 L2 themes emerge.
4. Self-Consistency: 3 agents analyze independently → each produces ranked 5-reason list.
5. Majority vote: "Pricing too high" #1 (3/3), "Lost to Competitor X" #2 (3/3), "Budget freeze" #3 (2/3).
6. Minority divergence on #4: agent 2 says "product gap (API)", agents 1&3 say "timing/budget". Flagged.
7. LTM retrieval: last quarter's top reason was "Sales cycle too long" → now dropped to #6 (trend noted).
8. Verifier checks: top 5 reasons all have ≥ 10 supporting deals, citations present → `success`.
9. Executive summary rendered as markdown with deal counts, recommended actions, trend delta.

**Observability:**
- `observability/pattern_trace.py`: self_consistency agent agreement rates, majority-vote confidence.
- `observability/rag_trace.py`: RAPTOR tree depth, cluster sizes, retrieval latency.
- `observability/metrics.py`: `lost_deal_analysis_deals_processed`, `self_consistency_agreement_rate`.

**Eval path:**
- `evals/goal_score.py`: accuracy of top-5 reasons vs. manual audit of 50 deals. Target ≥ 0.90.
- `evals/agent_score.py`: self-consistency convergence rate (3/3 agreement on top-3). Target ≥ 0.85.
- Business: quarterly comparison — did recommendations lead to measurable improvement?

**Expected output:** Executive report with: top 5 loss reasons (with deal counts and % of lost ARR), 3 trend deltas vs. prior quarter, 5 actionable recommendations (pricing tiering, competitive battle cards, deal-acceleration plays).

**Failure modes:**
- BigQuery quota exceeded: `reliability/circuit_breaker.py` opens; fallback to Salesforce SOQL via `salesforce_server.py`.
- Self-Consistency produces no majority (3 divergent lists): escalate to human synthesis with all 3 outputs.
- RAPTOR cluster count = 1: flat summarization fallback with manual reason-code grouping.

**Code references:**
- `app/mcp/servers/salesforce_server.py` — CRM opportunity data
- `app/mcp/servers/bigquery_server.py` — historical analytics warehouse
- `app/rag/agentic/patterns/raptor.py` — hierarchical deal narrative clustering
- `app/agent/patterns/self_consistency.py` — parallel analysis with majority vote
- `app/memory/long_term.py` — prior quarter analysis retrieval
- `app/memory/execution.py` — BigQuery result caching
- `app/reliability/circuit_breaker.py` — BigQuery quota failover

---

## UC36 — Create HubSpot Follow-up Tasks from Call Transcript

**Goal:** Given a recorded sales call audio file, transcribe it, extract action items and commitments, and automatically create follow-up tasks in HubSpot assigned to the correct deal owner.

**Business problem:** 60–70% of verbal commitments made on sales calls are never logged. Automatic task creation from call audio ensures follow-through, shortens deal cycles, and improves CRM data quality.

**Actors:** Sales rep (call initiator), Gong/recording system (trigger), `AudioParser`, `hubspot_server.py`, CRM (task consumer)

**Inputs:**
- Audio file: MP3/WAV (30–90 min call recording)
- HubSpot Deal ID
- Call participants list (name → rep ID mapping)

**Agent pattern:** Plan-Execute — Planner decomposes: transcribe → extract_commitments → classify_by_owner → create_tasks. Linear pipeline, each step feeds the next.

**RAG pattern:** `hybrid` — dense + BM25 hybrid retrieval over HubSpot contact history and prior deal notes to contextualize extracted commitments (e.g., "send updated pricing" grounded against prior pricing discussions).

**Memory used:**
- Execution memory: transcript and extracted action items stored across pipeline steps.
- Episodic memory (`memory/episodic.py`): prior call summaries for this deal retrieved to track commitment history.

**Ingestion path:**
1. Audio file → `ingestion/parsers/audio_parser.py` → Whisper transcription (word-level timestamps).
2. `ingestion/chunkers/timestamp.py` → 30-second overlap-sliding chunks preserving speaker diarization.
3. Transcript chunks embedded (voyage-3-large) → `KnowledgeStore` tagged with `call_id`, `deal_id`.
4. HubSpot deal notes synced via `hubspot_server.py → list_engagements(deal_id)` → `heading` chunker.

**Retrieval path:**
1. Hybrid retrieval (dense + BM25) from transcript + deal history context.
2. `rag_platform/retriever.py` with `hybrid` strategy merges dense and BM25 results.
3. Timestamp chunking enables precise attribution: each action item linked to call timestamp.
4. Episodic memory retrieves prior call summaries for commitment continuity checking.

**Model routing:** `gpt-4o-mini` — `model_router.py` `tier=low`; commitment extraction is structured-output task (low semantic complexity after transcription). Diarization attribution uses same model.

**Guardrails:**
- PII: customer names masked in logs but preserved in HubSpot task text (`intelligence/guardrails.py`).
- Task assignee validation: only valid HubSpot user IDs accepted (`governance/permissions.py`).
- Max 10 tasks per call to prevent spam (`governance/policies.py`).
- Commitment confidence < 0.6: task created as `draft` requiring rep approval.

**End-to-end flow:**
1. Goal: `"Create HubSpot tasks from call recording for Deal #HSD-4821"`.
2. Executor loads audio → `audio_parser.py` transcribes (~90 min → ~12k tokens).
3. Timestamp chunker splits into 180 × 30s chunks with speaker labels.
4. Executor issues extraction prompt: `"List all action items, commitments, and next steps with owner and deadline"`.
5. Hybrid retrieval grounds extraction against prior deal notes → 3 commitments disambiguated.
6. Structured output: 7 action items with `[owner, description, deadline, call_timestamp, confidence]`.
7. 2 items with confidence < 0.6 marked as `draft`; 5 items ready for creation.
8. Verifier checks: all 5 ready items have valid HubSpot user IDs, deadlines within 30 days → `success`.
9. `hubspot_server.py → create_task()` called 5 times; 2 drafts queued for rep review.
10. Episodic memory updated with call summary and extracted commitments.

**Observability:**
- `observability/model_trace.py`: Whisper transcription latency, word-error-rate estimate.
- `observability/rag_trace.py`: hybrid retrieval scores, timestamp chunk attribution.
- `observability/metrics.py`: `call_tasks_created_per_call`, `task_confidence_mean`.

**Eval path:**
- `evals/goal_score.py`: precision/recall of action item extraction vs. manual review. Target precision ≥ 0.88, recall ≥ 0.82.
- `evals/rag_score.py`: hybrid retrieval relevance for commitment grounding. Target ≥ 0.80.
- Business: task completion rate (AI-created vs. manually created) over 30-day period.

**Expected output:** 7 HubSpot tasks created/drafted: e.g., "Send updated pricing deck to CFO by July 15 (Rep: John)", "Schedule technical demo with engineering team by July 20", each with call timestamp link.

**Failure modes:**
- Audio quality too low (SNR < 15dB): `audio_parser.py` returns low-confidence transcript; agent halts and notifies rep.
- HubSpot API unavailable: tasks written to `KnowledgeStore` as draft records; sync retried via `services/goal_queue.py`.
- All items confidence < 0.6: full call summary sent to rep for manual task creation.

**Code references:**
- `app/mcp/servers/hubspot_server.py` — task creation and engagement listing
- `app/ingestion/parsers/audio_parser.py` — Whisper transcription
- `app/ingestion/chunkers/timestamp.py` — speaker-diarized timestamped chunks
- `app/agent/patterns/plan_execute.py` — linear pipeline orchestration
- `app/memory/episodic.py` — prior call summary retrieval
- `app/rag_platform/retriever.py` — hybrid dense+BM25 retrieval
- `app/intelligence/guardrails.py` — PII handling

---

## UC37 — Reconcile Invoices vs. Purchase Orders

**Goal:** Match scanned vendor invoices against open purchase orders in the ERP/database, flag discrepancies, auto-approve matches within tolerance, and route discrepancies > $10,000 for human approval.

**Business problem:** Manual AP reconciliation takes 2–3 FTE-days per month and has a 4% error rate. Automated matching reduces processing time by 85% and catches discrepancies that lead to duplicate payments.

**Actors:** AP team (trigger), VisionParser (invoice OCR), `postgres_server.py` (ERP data), `governance/hitl.py` (escalation)

**Inputs:**
- Scanned invoice PDFs or images (batch, up to 500 per run)
- PostgreSQL PO table schema and connection config
- Tolerance thresholds: auto-approve ≤ $500 variance, HITL > $10,000 variance

**Agent pattern:** `self_consistency` — 3 independent extraction agents process each invoice; majority-vote on extracted fields (invoice_number, vendor_id, amount, line_items) before reconciliation, ensuring OCR accuracy.

**RAG pattern:** `corrective_rag` — after initial PO matching, Corrective RAG validates the matched PO against the PO database schema and catches cases where the agent matched on wrong vendor ID or incorrect PO number.

**Memory used:**
- Execution memory: batch processing state; processed invoice IDs tracked for idempotency.
- LTM: vendor name→vendor_id mappings cached to handle OCR variants of vendor names.

**Ingestion path:**
1. Scanned invoices → `ingestion/parsers/vision_parser.py` (GPT-4o Vision or Tesseract fallback) → structured JSON (invoice fields + bounding boxes).
2. `ingestion/orchestrator.py` → `table` chunker for line-item tables; `semantic` for free-text sections.
3. Embeddings stored with `doc_type=invoice` metadata for future retrieval.
4. PO data: `postgres_server.py → query("SELECT * FROM purchase_orders WHERE status='open'")` → row-level documents.

**Retrieval path:**
1. Corrective RAG (`rag/agentic/patterns/corrective.py`) validates each matched PO.
2. On suspect match: re-retrieves from PO knowledge base using vendor name + amount range filter.
3. Semantic search over vendor LTM for fuzzy vendor-name resolution.
4. Cross-reference: invoice line items vs. PO line items via `table` chunker comparison.

**Model routing:** `gpt-4o` — `model_router.py` `tier=high` for invoice OCR interpretation (VisionParser uses gpt-4o vision endpoint). Self-Consistency extraction agents use `gpt-4o-mini`.

**Guardrails:**
- HITL mandatory for discrepancy > $10,000 (`governance/hitl.py`; approval_required=True).
- Financial figures: must be exact numeric match or within tolerance; no rounding by agent (`governance/policy_rules.py`).
- Audit trail: every match decision recorded in append-only audit log (`governance/audit_v3.py`).
- Duplicate invoice check: invoice_number + vendor_id uniqueness enforced before any approval.

**End-to-end flow:**
1. Goal: `"Reconcile 480 scanned invoices from July 2026 batch against open POs"`.
2. `vision_parser.py` processes 480 invoice images → structured JSON for each.
3. Self-Consistency: 3 agents each extract key fields; majority vote resolves OCR ambiguities (e.g., "8" vs. "B" in invoice numbers) on 34 invoices.
4. Corrective RAG validates 480 PO matches → 12 mismatches corrected (wrong vendor_id).
5. Reconciliation engine: 461 exact matches (≤ $500 variance) → auto-approved.
6. 14 discrepancies $500–$10,000 → logged with discrepancy notes, routed to AP team queue.
7. 5 discrepancies > $10,000 → `governance/hitl.py` creates approval tasks with full context.
8. `postgres_server.py → update_po_status(id, status='matched', invoice_id=...)` for approved items.
9. Verifier checks: all 480 invoices have a reconciliation decision, 5 HITL items queued → `success`.
10. Audit log: full decision trail per invoice ID with match confidence and applied rule.

**Observability:**
- `observability/rag_trace.py`: corrective_rag correction rate, match confidence distribution.
- `observability/pattern_trace.py`: self_consistency agreement rate on field extraction.
- `observability/metrics.py`: `invoices_processed`, `auto_approved_rate`, `hitl_escalation_rate`.

**Eval path:**
- `evals/goal_score.py`: match accuracy vs. manual reconciliation ground truth (100 invoices). Target ≥ 0.97.
- `evals/safety_score.py`: zero false auto-approvals above tolerance threshold. Target = 1.00.
- Business: AP processing time reduction and error rate vs. prior month.

**Expected output:** Reconciliation report: 461 auto-approved, 14 in AP review queue, 5 HITL approval requests, discrepancy summary by vendor and amount range.

**Failure modes:**
- OCR failure (image too blurry): `vision_parser.py` returns `low_confidence`; invoice flagged for manual scan.
- PO database timeout: `reliability/circuit_breaker.py`; batch paused, notification sent to AP team.
- Self-Consistency produces all-divergent extractions (rare): invoice flagged as `manual_review`.

**Code references:**
- `app/ingestion/parsers/vision_parser.py` — invoice OCR with bounding-box extraction
- `app/mcp/servers/postgres_server.py` — PO database queries and status updates
- `app/rag/agentic/patterns/corrective.py` — PO match validation
- `app/agent/patterns/self_consistency.py` — parallel extraction with majority vote
- `app/governance/hitl.py` — HITL escalation for large discrepancies
- `app/governance/audit_v3.py` — append-only financial audit trail
- `app/governance/policy_rules.py` — financial tolerance rules
- `app/reliability/circuit_breaker.py` — DB timeout handling

---

## UC38 — Extract Expense Data from Receipt Images

**Goal:** Given a batch of receipt images uploaded by employees, extract structured expense data (vendor, amount, date, category, payment method) and write records to the expense management database with PII-safe handling.

**Business problem:** Manual expense entry is error-prone and slow; employees lose receipts. Automated extraction reduces submission time from 15 min to 90 seconds per report and reduces reimbursement cycle from 14 days to 3 days.

**Actors:** Employee (trigger via expense app), VisionParser (OCR), `postgres_server.py` (expense DB), Finance team (consumer)

**Inputs:**
- Receipt images (JPEG/PNG, up to 20 per submission)
- Employee ID and submission date
- Expense policy configuration (category limits, allowed merchants)

**Agent pattern:** ReAct (per-receipt iteration) — for each receipt: extract → validate → write. No planning needed; linear per-item loop.

**RAG pattern:** `corrective_rag` — after initial extraction, Corrective RAG validates extracted data against the expense policy knowledge base (e.g., "hotel > $200/night requires pre-approval", "alcohol not reimbursable") and flags policy violations before DB write.

**Memory used:**
- Execution memory: batch submission state; processed receipt IDs for idempotency.
- LTM: merchant name normalization (e.g., "AMZN MKTP" → "Amazon Marketplace") cached in LTM.

**Ingestion path:**
1. Receipt images → `ingestion/parsers/vision_parser.py` → JSON `{vendor, amount, date, category, payment_method, tax, line_items}`.
2. Expense policy PDFs → `ingestion/parsers/pdf_parser.py` → `semantic` chunker → `KnowledgeStore`.
3. Merchant normalization table loaded into LTM from prior batches.

**Retrieval path:**
1. Corrective RAG validates each extracted record against expense policy (`rag/agentic/patterns/corrective.py`).
2. Policy retrieval query: e.g., `"What is the per-diem limit for hotel accommodation?"`.
3. Retrieved policy chunks used to classify: `approved`, `requires_pre_approval`, `rejected`.
4. LTM merchant lookup (`memory/long_term.py`) normalizes vendor names before DB write.

**Model routing:** `gpt-4o` — `model_router.py` `tier=high` for VisionParser (image understanding). Policy validation uses `gpt-4o-mini` (structured rule lookup).

**Guardrails:**
- PII detection: personal credit card numbers, employee SSN-like patterns stripped from extracted data (`intelligence/guardrails.py` PII regex patterns).
- Financial accuracy: amounts must parse as valid currency; centavo-level precision required.
- Policy enforcement: `rejected` items cannot be written to DB; logged with policy citation.
- Employee data: expense records scoped to submitting employee ID via RLS (`db/rls.py`).

**End-to-end flow:**
1. Employee uploads 8 receipts via expense app → goal submitted with employee_id=EMP-2341.
2. `vision_parser.py` processes 8 images → 8 structured JSON objects.
3. LTM merchant normalization applied: "UBER* TRIP" → "Uber", "AMZN MKTP" → "Amazon".
4. Corrective RAG validates each against policy:
   - 6 receipts: `approved` (within limits).
   - 1 receipt: hotel $285/night → `requires_pre_approval` (limit is $250).
   - 1 receipt: alcohol purchase → `rejected` (policy: not reimbursable).
5. Verifier checks: 6 approved have full required fields, PII stripped, policy citations attached → `success`.
6. `postgres_server.py → insert_expense_records([...])` writes 6 approved records.
7. 1 pre-approval request created in workflow queue.
8. 1 rejection notification sent to employee with policy citation.

**Observability:**
- `observability/rag_trace.py`: corrective_rag policy match rate, correction latency.
- `observability/model_trace.py`: vision_parser extraction confidence per field.
- `observability/metrics.py`: `receipts_processed`, `auto_approved_rate`, `policy_rejection_rate`.

**Eval path:**
- `evals/goal_score.py`: field extraction accuracy vs. manual entry (200 receipts). Target ≥ 0.95.
- `evals/safety_score.py`: zero PII written to expense DB. Target = 1.00.
- Business: reimbursement cycle time reduction and employee NPS on expense submission.

**Expected output:** 6 expense records written to DB, 1 pre-approval request queued, 1 rejection with policy citation. Each record: `{employee_id, vendor, amount, date, category, policy_status, receipt_image_url}`.

**Failure modes:**
- Receipt image unreadable (e.g., dark photo): `vision_parser.py` returns `extraction_failed`; employee prompted to retake.
- Postgres write conflict (duplicate receipt): `reliability/idempotency.py` deduplicates on `(employee_id, vendor, amount, date)`.
- Policy KB stale (> 30 days since sync): agent warns on output and flags for policy refresh.

**Code references:**
- `app/ingestion/parsers/vision_parser.py` — receipt image OCR and structured extraction
- `app/mcp/servers/postgres_server.py` — expense record DB writes
- `app/rag/agentic/patterns/corrective.py` — policy compliance validation
- `app/ingestion/parsers/pdf_parser.py` — expense policy document ingestion
- `app/memory/long_term.py` — merchant name normalization
- `app/intelligence/guardrails.py` — PII detection and stripping
- `app/db/rls.py` — employee-scoped row-level security
- `app/reliability/idempotency.py` — duplicate receipt prevention

---

## UC39 — Flag Transactions Above Threshold for Review

**Goal:** Continuously monitor financial transactions in BigQuery and PostgreSQL, flag any transaction above configurable thresholds (amount, velocity, unusual merchant category), create review tasks, and maintain an immutable audit trail.

**Business problem:** Manual transaction monitoring misses 15–20% of policy violations. Automated flagging with audit trail satisfies SOC2/PCI compliance requirements and reduces fraud exposure.

**Actors:** Finance compliance team (rule config), `bigquery_server.py` (transaction warehouse), `postgres_server.py` (operational DB), `governance/hitl.py` (reviewer)

**Inputs:**
- BigQuery dataset: `finance.transactions` (real-time or T+1)
- Threshold config: `{amount_threshold: 50000, velocity_window: 1h, velocity_max_count: 5}`
- PostgreSQL: approved vendor list, employee spending limits

**Agent pattern:** ReAct with `self_rag` for adaptive query refinement. Agent iterates: query → assess → refine_query_if_needed → flag.

**RAG pattern:** `self_rag` — agent dynamically decides whether to retrieve additional context (vendor history, employee spending history) before making a flag decision. If a transaction is borderline, self_rag triggers a focused retrieval of historical patterns for that vendor/employee before deciding.

**Memory used:**
- Execution memory: current monitoring session state, already-flagged transaction IDs.
- LTM: historical flagging patterns — vendor risk scores, employee spending velocity baselines.

**Ingestion path:**
1. `bigquery_server.py → run_query("SELECT * FROM finance.transactions WHERE transaction_date = CURRENT_DATE()")` — daily batch; real-time via streaming insert trigger.
2. Transaction rows → `ingestion/orchestrator.py` → `table` chunker (row-level documents).
3. Vendor master data from PostgreSQL → `semantic` chunker.
4. Historical flag patterns written to LTM after each completed monitoring cycle.

**Retrieval path:**
1. Self-RAG (`rag/agentic/patterns/self_rag.py`) evaluates each flagged candidate.
2. On borderline transactions: retrieves vendor history (avg amount, frequency) from LTM.
3. Employee spending velocity retrieved from PostgreSQL via `postgres_server.py`.
4. Adaptive retrieval: if vendor is new (< 3 prior transactions), retrieves approved vendor list for cross-check.

**Model routing:** `gpt-4o-mini` — `model_router.py` `tier=low` for threshold evaluation (mostly structured logic). Self-RAG context synthesis uses `gpt-4o-mini`. HITL notification drafts use `gpt-4o-mini`.

**Guardrails:**
- HITL mandatory for all flagged transactions (`governance/hitl.py`; auto-escalation within 4 hours).
- Audit trail: every flag decision written to append-only `governance/audit_v3.py` with rule_id and evidence.
- False positive protection: transactions within 10% of threshold logged but not flagged (yellow zone).
- PCI compliance: transaction data never logged in plaintext to application logs (`observability/logging.py` masking).

**End-to-end flow:**
1. Goal: `"Monitor today's transactions for policy violations and flag for review"`.
2. Executor queries BigQuery: 12,450 transactions loaded.
3. ReAct loop: for each transaction above threshold:
   a. Self-RAG assesses: does agent need more context? → Yes for 23 borderline cases.
   b. Retrieves vendor history and employee baseline for borderline cases.
   c. Makes flag decision with confidence score.
4. 47 transactions flagged as violations (31 amount threshold, 16 velocity violations).
5. 8 transactions in yellow zone → logged as warnings, not flagged.
6. HITL tasks created for all 47 flagged transactions with evidence packets.
7. `postgres_server.py → insert_flag_records([...])` writes flag records with rule citations.
8. Verifier checks: all 47 flags have audit records, HITL tasks created, no PII in logs → `success`.
9. LTM updated with new vendor/employee risk scores from this cycle.

**Observability:**
- `observability/rag_trace.py`: self_rag retrieval trigger rate, context quality score.
- `observability/metrics.py`: `transactions_monitored`, `flag_rate`, `hitl_tasks_created`, `false_positive_rate`.
- `governance/audit_v3.py`: immutable flag log with rule_id, evidence, and agent confidence.

**Eval path:**
- `evals/safety_score.py`: zero missed true violations (recall = 1.00 target for critical threshold).
- `evals/goal_score.py`: precision of flags (% of flags confirmed by human reviewer). Target ≥ 0.88.
- Compliance: monthly audit report showing 100% of flagged transactions have HITL review records.

**Expected output:** 47 HITL review tasks with: transaction ID, amount, rule triggered, vendor, employee, evidence (historical context), confidence score. Yellow-zone warning report with 8 items.

**Failure modes:**
- BigQuery query timeout (large dataset): partition-based batching via `bigquery_server.py`; 24h window split into 4×6h batches.
- Self-RAG retrieval loop > 5 iterations: break and flag transaction as `manual_review`.
- HITL queue backlog > 100 items: escalate to finance manager with summary digest.

**Code references:**
- `app/mcp/servers/bigquery_server.py` — transaction warehouse queries
- `app/mcp/servers/postgres_server.py` — approved vendor and employee limit lookups
- `app/rag/agentic/patterns/self_rag.py` — adaptive context retrieval for borderline cases
- `app/governance/hitl.py` — mandatory review task creation
- `app/governance/audit_v3.py` — immutable financial audit trail
- `app/memory/long_term.py` — vendor and employee risk score storage
- `app/memory/execution.py` — monitoring session state
- `app/observability/logging.py` — PCI-compliant log masking

---

## UC40 — Generate Budget Variance Analysis

**Goal:** Compare actual spend (from BigQuery financial warehouse) against approved budget (from Excel budget files) across all cost centers, calculate variances, identify root causes, and generate an executive CFO report with commentary.

**Business problem:** Monthly budget reviews take finance teams 3–5 days of data wrangling. Automated variance analysis with narrative commentary compresses this to 2 hours and improves actionability.

**Actors:** CFO / Finance director (consumer), `bigquery_server.py` (actuals), Excel budget files (plan), `peer_review` agents (commentary quality)

**Inputs:**
- BigQuery dataset: `finance.actuals_YTD` (year-to-date actual spend by GL code)
- Excel budget files (multiple cost centers, uploaded to MinIO or Google Drive)
- Variance threshold: flag if |actual - budget| / budget > 10%
- Report period: current month and YTD

**Agent pattern:** `peer_review` — a Finance Analyst agent drafts variance commentary; a Controller Critic reviews for accuracy, materiality framing, and appropriate hedging language; Reviser finalizes.

**RAG pattern:** `raptor` — hierarchical synthesis across 40–60 cost center documents (each with multi-month GL rows). RAPTOR builds a tree: L0=GL line items, L1=cost center roll-ups, L2=department summaries, L3=company total. Enables coherent company-wide narrative without context overflow.

**Memory used:**
- Execution memory: actuals query results and per-cost-center variance tables cached across pipeline steps.
- LTM: prior month's commentary retrieved for YoY and MoM trend framing.

**Ingestion path:**
1. Excel budget files → `mcp/servers/microsoft_excel_server.py → read_workbook()` → tabular JSON per cost center.
2. Tabular data → `ingestion/orchestrator.py` → `table` chunker (row per GL code per month).
3. BigQuery actuals: `bigquery_server.py → run_query("SELECT gl_code, cost_center, SUM(amount) FROM actuals_YTD GROUP BY 1,2")`.
4. Actuals + budget joined in-memory; variance computed per GL code.
5. RAPTOR tree built over variance tables: L1=cost_center, L2=department, L3=company total.

**Retrieval path:**
1. Finance Analyst agent retrieves from RAPTOR tree at L2 (department) and L1 (cost center) levels.
2. Retrieves root-cause context: "What drove the 23% over-spend in Engineering headcount?"
3. LTM retrieval: prior month commentary for trend comparison.
4. Peer Review Critic validates each commentary claim against RAPTOR source nodes.

**Model routing:** `gpt-5.2` — `model_router.py` `tier=premium` for Finance Analyst and Controller Critic (highest reasoning quality required for CFO-grade output). RAPTOR cluster summarization uses `gpt-4o`.

**Guardrails:**
- All variance figures must be traceable to source GL codes (citation required, `rag/agentic/citation_threader.py`).
- No forward-looking projections beyond current quarter without explicit confidence caveat.
- Peer Review Critic must approve before report is finalized (`agent/patterns/peer_review.py` gate).
- HITL: variances > 25% over budget require CFO annotation before report distribution.

**End-to-end flow:**
1. Goal: `"Generate July 2026 budget variance analysis for CFO review"`.
2. Executor loads 47 Excel budget workbooks via `microsoft_excel_server.py`.
3. BigQuery actuals query returns 12,847 GL rows for July 2026.
4. Variance computation: 312 GL codes with >10% variance flagged; 28 > 25% flagged for HITL.
5. RAPTOR tree built: 47 L1 nodes → 8 L2 department nodes → 1 L3 root.
6. Finance Analyst agent drafts commentary for each L2 department (8 sections + executive summary).
7. LTM retrieval: June 2026 commentary retrieved → YoY/MoM trends noted.
8. Peer Review: Controller Critic flags 3 commentary claims as misleadingly hedged; suggests tighter materiality language.
9. Reviser updates 3 sections; Verifier confirms all citations present, HITL items queued.
10. Final report rendered as markdown + XLSX attachment; `governance/audit.py` logs report generation.

**Observability:**
- `observability/rag_trace.py`: RAPTOR tree depth, synthesis latency per level.
- `observability/pattern_trace.py`: peer_review iteration count, critic scores by section.
- `observability/metrics.py`: `gl_codes_analyzed`, `variance_flag_rate`, `hitl_escalation_count`.

**Eval path:**
- `evals/goal_score.py`: commentary accuracy vs. CFO manual review (10 reports). Target ≥ 0.90.
- `evals/rag_score.py`: citation coverage — every variance figure has a source GL citation. Target = 1.00.
- Business: time-to-report reduction vs. manual baseline (target: 3 days → 2 hours).

**Expected output:** CFO report: executive summary (company-wide +3.2% over budget), 8 department sections with variance tables and commentary, 28 HITL annotation requests for material variances, YoY and MoM trend summaries.

**Failure modes:**
- Excel file format mismatch (merged cells, non-standard layouts): `microsoft_excel_server.py` returns parse error; finance team notified to normalize file format.
- BigQuery actuals query returns zero rows (ETL not yet run): agent waits up to 2 hours with polling; reports partial data with warning.
- RAPTOR cluster synthesis diverges from source data: Corrective RAG validation pass added before Peer Review.

**Code references:**
- `app/mcp/servers/bigquery_server.py` — YTD actuals queries
- `app/mcp/servers/microsoft_excel_server.py` — budget workbook ingestion
- `app/rag/agentic/patterns/raptor.py` — hierarchical cost-center synthesis
- `app/agent/patterns/peer_review.py` — Finance Analyst / Controller Critic loop
- `app/memory/long_term.py` — prior month commentary retrieval
- `app/memory/execution.py` — actuals and variance table caching
- `app/rag/agentic/citation_threader.py` — GL code citation attachment
- `app/governance/hitl.py` — material variance HITL escalation
