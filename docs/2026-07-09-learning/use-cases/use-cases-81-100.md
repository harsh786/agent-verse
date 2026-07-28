# Use Cases 81–100: Multimodal · Incident Response · Procurement · Marketing · Field Ops · Multi-domain Complex

> All use cases cite real `agent-verse-backend/app/` source files, real MCP server modules,
> and production-verified strategy keys from `app/rag/engine.py`.

---

## UC81 — Transcribe and Summarize Recorded Meeting

**Goal:** Transcribe an audio recording of a 60-minute business meeting and produce a structured summary with decisions, action items, and owners.

**Business problem:** Meeting participants spend 30+ minutes writing notes after calls; automated transcription with structured extraction saves time and ensures nothing is missed.

**Actors:** Meeting organizer, AgentVerse agent
**Inputs:** `.mp3` or `.m4a` audio recording (up to 2h)

**Agent pattern:** Plan-Execute — structured plan: transcribe → chunk by speaker → extract decisions → format output

**RAG pattern:** `raptor` — hierarchical summarization: segment-level → topic-level → executive summary

**Memory used:** Session memory (transcript within this run), Episodic memory (past meeting summaries for consistency of formatting)

**Ingestion path:** Audio file → `AudioParser` (OpenAI Whisper API, `verbose_json` with segment timestamps) → `TimestampChunker` (60-second windows) → `text-embedding-3-small` → `knowledge_chunks_1536`

**Retrieval path:** `raptor` — LLM summarizes 60-second chunks → groups by topic → produces final context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PII detection on transcript (names, phone numbers) — flagged but not blocked
- `GuardrailChecker` screens for confidential content markers
- Audit trail: transcript storage logged per tenant

**End-to-end flow:**
1. `AudioParser._transcribe_with_whisper()` sends audio to Whisper API; returns transcript with segment timestamps
2. `TimestampChunker` splits into 60s windows with `start_time`, `end_time` metadata
3. RAPTOR level-1: LLM summarizes each 5-minute block into topic summary
4. RAPTOR level-2: groups topic summaries into agenda items
5. `_node_plan` builds final document: summary → decisions → action items table → next steps
6. `OutputContractBuilder` detects "summary" → markdown output contract
7. Publishes to Confluence via `confluence_server.py:create_page`

**Observability:** Whisper API cost (~$0.006/min); `GOAL_DURATION` target < 3min for 60min recording; `eval_score_recorded` SSE

**Eval path:** `goal_success` (Confluence page created), `grounding` (decisions reference actual transcript), `tool_success_rate`

**Expected output:** Confluence page with: executive summary, attendees, decisions, action items table (owner + due date), next meeting date

**Failure modes:** Audio quality too low → Whisper returns empty segments → fallback to "audio quality insufficient" message; recording > 25MB (Whisper limit) → chunked uploads; Whisper API timeout → retry with exponential backoff

**Code references:** `app/ingestion/parsers/audio_parser.py`, `app/ingestion/chunkers/timestamp.py`, `app/rag/agentic/patterns/raptor.py`, `app/mcp/servers/confluence_server.py`

---

## UC82 — Convert Video Tutorial to Written Step-by-Step Guide

**Goal:** Process a screen-recording video tutorial and produce a written guide with numbered steps, screenshots at key moments, and code snippets.

**Business problem:** Video tutorials are not searchable or scannable; written guides with screenshots enable faster learning and reference.

**Actors:** Documentation team, AgentVerse agent
**Inputs:** `.mp4` screen recording (5-30 min)

**Agent pattern:** Plan-Execute — structured: extract audio → transcribe → extract key frames → synthesize guide

**RAG pattern:** `raptor` — hierarchical: timestamp chunks → scene summaries → full guide

**Memory used:** Session memory (transcript + frame descriptions), Procedural memory (learned guide structure from past tutorials)

**Ingestion path:** Video → `VideoParser` (ffmpeg extracts audio track) → `AudioParser` (Whisper transcription) → `SceneChunker` (keyframe markers) → `text-embedding-3-small`

**Retrieval path:** `raptor` with `scene` chunking strategy — scene-level summaries provide structure

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o` (with vision for frames), verifier: `gpt-4o-mini`

**Guardrails and governance:**
- No HITL needed (documentation, non-destructive)
- OutputContractBuilder: "step-by-step guide" → markdown output

**End-to-end flow:**
1. `VideoParser._extract_and_transcribe()`: ffmpeg extracts audio → AudioParser transcribes
2. At key scene transitions (detected from transcript pauses + slide changes), VisionParser describes screen state
3. SceneChunker segments transcript by `[SCENE N:]` markers
4. RAPTOR level-1: summarizes each scene (what was demonstrated)
5. `_node_plan`: maps scenes to numbered steps with screen descriptions
6. `_node_execute`: for each step — generates instruction text + notes screenshot timestamp + includes code from transcript
7. Published to Confluence as formatted guide

**Observability:** ffmpeg extraction time; Whisper cost; vision model cost per frame; total ~$0.25 for 15min video

**Eval path:** `goal_success` (guide published), `grounding` (steps reference video content), `goal_success`

**Expected output:** Confluence page with numbered steps, inline screenshots (by timestamp reference), code blocks, and summary

**Failure modes:** ffmpeg not installed → error with installation instructions; video has no audio track → vision-only analysis with lower quality; video > 500MB → chunked processing

**Code references:** `app/ingestion/parsers/video_parser.py`, `app/ingestion/parsers/audio_parser.py`, `app/ingestion/parsers/vision_parser.py`, `app/ingestion/chunkers/scene.py`

---

## UC83 — Coordinate P1 Incident Response

**Goal:** When a P1 alert fires, simultaneously gather diagnostic context from 4 systems, acknowledge the incident, notify stakeholders, and create a structured incident ticket — all within 2 minutes.

**Business problem:** P1 incident response requires coordinating 4+ systems under extreme time pressure; automated parallel coordination reduces time-to-acknowledge from 8 minutes to under 90 seconds.

**Actors:** On-call engineer, AgentVerse Supervisor agent (4 sub-agents), PagerDuty, Datadog, Kubernetes, GitHub, Slack, Jira MCP servers

**Inputs:** PagerDuty webhook payload, affected service name

**Agent pattern:** Supervisor — main agent spawns 4 parallel sub-agents: (1) alert analyzer, (2) metrics/traces collector, (3) recent deployment checker, (4) runbook finder

**RAG pattern:** `flare` — uncertainty-driven: initial alert description may be ambiguous; FLARE fetches targeted runbook sections when confidence < 0.6

**Memory used:** Execution memory (past incidents for this service), Episodic memory (resolution patterns), Reflexion memory (lessons: "check deployment timestamp relative to alert")

**Ingestion path:** PagerDuty JSON → `SemanticChunker`; Confluence runbooks → `HeadingChunker` → `text-embedding-3-small`

**Retrieval path:** Alert name → `flare` (uncertainty detection → targeted runbook retrieval) → ColBERT reranking for precision

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o` (fast for parallel agents), verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: agent cannot auto-remediate (restart pods, rollback deploy) without human approval
- PolicyEngine: `kubernetes.restart_pod` requires REQUIRE_APPROVAL
- Audit trail: every action logged with timestamp and decision rationale
- Budget: incident response has no cost ceiling (business criticality override)

**End-to-end flow:**
1. PagerDuty webhook → `POST /webhooks/alerts/pagerduty` → Redis → `fire_due_schedules` triggers within 30s
2. Supervisor spawns 4 parallel agents: `pagerduty_server.py:get_incident`, `datadog_server.py:get_metrics` (memory/CPU/error rate), `github_server.py:list_commits` (last 2h), `confluence_server.py:search_pages` (runbooks)
3. FLARE: if root cause uncertain (confidence < 0.6), fetches specific runbook sections
4. HITL gate: human acknowledges incident; agent pauses on remediation
5. `jira_server.py:create_issue` → P1 ticket with: timeline, metrics charts, recent deploys, runbook link
6. `slack_server.py:send_message` → `#incidents` channel with incident summary
7. `pagerduty_server.py:acknowledge_incident` after human acknowledgment

**Observability:** Supervisor SSE showing 4 parallel agent progress; HITL SSE event; `GOAL_DURATION` target < 90s; cost ~$0.12/incident

**Eval path:** `goal_success` (ticket created + Slack notified), `tool_success_rate` (all 4 APIs succeed), `grounding` (ticket references actual metrics)

**Expected output:** Jira P1 ticket (alert details, metrics, timeline, runbook link) + Slack notification + PagerDuty acknowledgment

**Failure modes:** PagerDuty API down → fallback to Redis webhook payload; Datadog timeout → ticket created noting metric unavailability; HITL timeout (5min) → auto-escalate to incident commander; one sub-agent fails → continues with partial data

**Code references:** `app/agent/patterns/supervisor.py`, `app/mcp/servers/pagerduty_server.py`, `app/mcp/servers/datadog_server.py`, `app/rag/agentic/patterns/flare.py`, `app/governance/hitl.py`

---

## UC84 — Write Root Cause Analysis from Incident Timeline

**Goal:** After an incident is resolved, generate a comprehensive root cause analysis document covering timeline, contributing factors, detection gap, remediation steps, and prevention measures.

**Business problem:** Post-incident RCAs are often written hastily and incompletely; structured AI-assisted RCA ensures consistency and captures all contributing factors.

**Actors:** Incident commander, AgentVerse agent, Jira + Confluence + postgres_server MCP servers

**Inputs:** Incident ticket, Slack incident channel history, monitoring data, git commits during incident window

**Agent pattern:** Reflection + Peer Review — iterates on root cause if initial hypothesis doesn't explain all data; peer review ensures accuracy

**RAG pattern:** `raptor` — hierarchical analysis of multi-source incident data

**Memory used:** Reflexion memory (stores RCA lessons for future incidents), Episodic memory (past similar incidents for comparison)

**Ingestion path:** Jira ticket → `SemanticChunker`; Slack export → `TimestampChunker`; monitoring data → structured JSON

**Retrieval path:** `raptor` (hourly clusters of Slack messages + metrics) → final context for RCA writing

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`

**Guardrails and governance:**
- Peer Review: second agent reviews for factual accuracy and completeness
- No HITL required (documentation)
- OutputContractBuilder: "RCA" → structured markdown format

**End-to-end flow:**
1. Fetches incident timeline: Jira comments, Slack `#incidents` messages (by time), Datadog alert history
2. RAPTOR clusters events by 15-minute windows → identifies: (a) first symptom, (b) alert firing, (c) detection gap, (d) remediation actions
3. `_node_plan`: structures RCA: incident summary → timeline → root cause → contributing factors → detection gap → remediation → prevention
4. Reflection: if initial root cause doesn't explain timeline → generates alternative hypothesis
5. Peer Review: second agent scores completeness 0-1; low score → critique injected → revision
6. Reflexion stores lessons: "always check connection pool exhaustion when DB latency spikes" → `reflexion_lessons` table
7. Published to Confluence under `/incidents/YYYY-MM-DD-incident-name`

**Observability:** Reflection SSE events; Peer Review score in context; `eval_score_recorded` with `grounding` dimension; cost ~$0.20

**Eval path:** `grounding` (root cause references actual evidence), `goal_success`, `citation_quality`

**Expected output:** Confluence RCA page with: 5-why analysis, timeline diagram, action items for prevention, lessons learned

**Failure modes:** Slack data not available → RCA based on Jira + Datadog only (noted); root cause ambiguous → Debate pattern invoked for multi-hypothesis analysis; reflexion lesson too vague → agent requests clarification

**Code references:** `app/agent/patterns/reflection.py`, `app/agent/patterns/reflexion.py`, `app/agent/patterns/peer_review.py`, `app/rag/agentic/patterns/raptor.py`, `app/state_runtime/reflexion_store.py`

---

## UC85 — Draft Customer Outage Communication

**Goal:** During or after a service outage, draft customer-facing communication (status page update, email, Slack) that is accurate, empathetic, and free of technical jargon.

**Business problem:** Poorly written outage communications damage customer trust; automated drafting with tone review ensures consistency and compliance with communication policy.

**Actors:** Customer success lead, AgentVerse agent, Confluence + Slack MCP servers

**Inputs:** Incident ticket, communication policy Confluence page, affected customer list

**Agent pattern:** ReAct + Peer Review — ReAct drafts communication; Peer Review scores tone and accuracy

**RAG pattern:** `corrective_rag` — fetches communication templates and past approved messages; fallback if templates don't match current scenario

**Memory used:** Execution memory (incident details from RCA), Procedural memory (learned: "never reveal internal system names in customer communication")

**Ingestion path:** Confluence communication templates → `HeadingChunker` → `text-embedding-3-small`

**Retrieval path:** `corrective_rag` — fetches templates + past communications; confidence check ensures retrieved content is relevant

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- GuardrailChecker: scans output for internal system names, credentials, technical jargon
- Peer Review: tone score + accuracy score (must both be ≥ 0.8)
- HITL: customer-facing communications always require human approval before sending
- OutputContractBuilder: detects "communication" → plain text format

**End-to-end flow:**
1. Fetches incident summary from Jira + RCA document
2. `corrective_rag` retrieves: (a) communication templates, (b) past approved messages for similar outages
3. Drafts 3 versions: (1) status page update (150 words), (2) customer email (300 words), (3) Slack notification (50 words)
4. Peer Review scores each for empathy, accuracy, clarity, tone compliance
5. GuardrailChecker scans for internal details or jargon → removes if found
6. HITL: human approves final communications
7. `slack_server.py:send_message` to customer channels; email drafted for manual send

**Observability:** Peer Review score SSE; HITL SSE; `eval_score_recorded` with `safety` and `grounding`

**Eval path:** `safety` (no internal info leaked), `grounding` (incident facts accurate), `goal_success`

**Expected output:** 3 approved communication drafts (status page + email + Slack) ready for distribution

**Failure modes:** Incident scope unclear → agent asks for clarification; peer reviewer rejects all drafts → Reflection generates revised approach; HITL not responded in 10min → escalates to incident commander

**Code references:** `app/agent/patterns/peer_review.py`, `app/rag/agentic/retriever_tool.py:retrieve_corrective`, `app/governance/hitl.py`, `app/mcp/servers/slack_server.py`

---

## UC86 — Score Vendor Proposals Against Evaluation Criteria

**Goal:** Evaluate 5 vendor RFP proposals against a weighted scoring rubric covering technical capability, pricing, security, support, and references, producing a ranked recommendation.

**Business problem:** Manual vendor evaluation is subjective and time-consuming; structured AI scoring ensures consistency and reduces bias.

**Actors:** Procurement manager, AgentVerse agent

**Inputs:** 5 PDF vendor proposals (50-200 pages each), scoring rubric Confluence page

**Agent pattern:** Supervisor — 5 parallel agents (one per vendor), then synthesis agent for ranking

**RAG pattern:** `colbert` — late-interaction reranking for precision matching of vendor claims against rubric criteria

**Memory used:** Procedural memory (learned scoring methodology from past evaluations), Episodic memory (past vendor evaluation outcomes)

**Ingestion path:** PDFs → `PDFParser` (pymupdf → pdfminer fallback) → `PDFLayoutChunker` → `ParentChildChunker` (parent=1500 chars, child=400) → `voyage-3` (1024-dim) → `knowledge_chunks_1024`

**Retrieval path:** Each rubric criterion → `colbert` (MaxSim token-level scoring) → top-5 relevant proposal sections per criterion

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o` (parallel scoring agents), verifier: `gpt-5.2`, embedder: `voyage-3`

**Guardrails and governance:**
- No HITL required for scoring (recommendation, not binding decision)
- Peer Review: second agent validates scoring consistency across vendors
- OutputContractBuilder: "scoring matrix" → JSON output contract
- Audit trail: all scores logged with evidence citations

**End-to-end flow:**
1. Ingest all 5 PDFs with `PDFParser` + `PDFLayoutChunker` + `ParentChildChunker`
2. Supervisor spawns 5 parallel agents — each scores one vendor against all rubric criteria
3. For each criterion: `colbert` retrieves most relevant proposal sections → LLM scores 1-5 with evidence quote
4. Parallel agents return: vendor scorecard with score + evidence per criterion
5. Synthesis agent weights scores by rubric weights → produces ranked comparison
6. Self-Consistency: synthesis run 3x → majority-vote ranking for high-stakes tie-breaking
7. Peer Review validates scoring consistency (same standard applied to all vendors)
8. Published to Confluence as comparison matrix

**Observability:** Supervisor SSE (5 parallel agents); ingestion cost per PDF (~$0.05); scoring cost per vendor (~$0.15); total ~$1.00 for 5-vendor evaluation

**Eval path:** `grounding` (scores cite actual proposal text), `goal_success` (matrix published), `citation_quality`

**Expected output:** Confluence page with: ranked vendor comparison matrix, individual scorecards with evidence quotes, recommendation with rationale

**Failure modes:** PDF with scanned pages → VisionParser fallback for those pages; vendor uses ambiguous language → score noted as "unable to verify" with explanation; proposals too similar for ColBERT → fallback to `hybrid` retrieval

**Code references:** `app/agent/patterns/supervisor.py`, `app/rag/agentic/patterns/colbert.py`, `app/ingestion/parsers/pdf_parser.py`, `app/rag/parent_child_chunker.py`, `app/agent/patterns/self_consistency.py`

---

## UC87 — Flag Non-Standard Payment Terms in Supplier Contracts

**Goal:** Review a batch of 20 supplier contracts and identify any payment terms that deviate from company standard (Net-30, 2% early payment discount, no penalty clauses) with risk assessment.

**Business problem:** Non-standard payment terms in supplier contracts create cash flow risk and finance complexity; automated review of contract batches saves 40 hours of legal review.

**Actors:** Finance/Legal team, AgentVerse agent

**Inputs:** 20 PDF contracts (10-50 pages each), payment terms policy Confluence page

**Agent pattern:** Goal Tree — parallel agents review each contract; synthesis agent compiles deviations

**RAG pattern:** `corrective_rag` — fetches payment terms standards; web fallback for industry-standard terms when KB confidence < 0.5

**Memory used:** Episodic memory (past contract review findings), Procedural memory (payment term red flags)

**Ingestion path:** PDFs → `PDFParser` → `PDFLayoutChunker` → `ParentChildChunker` → `text-embedding-3-small`

**Retrieval path:** Payment clause text → `corrective_rag` (hybrid retrieval of policy + past deviations) → parent-child expansion for full clause context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: deviations with financial impact > $50k require legal sign-off
- PolicyEngine: no auto-approve/reject permissions — advisory only
- Audit trail: all findings logged with contract name + clause location
- REGULATED guardrail bundle for contract content

**End-to-end flow:**
1. Goal Tree: 20 parallel agents (one per contract) extract payment clause sections
2. Each agent: `PDFLayoutChunker` finds "Payment" heading sections → `corrective_rag` retrieves policy standards
3. LLM compares: payment period (Net-30?), discount terms (2%?), late fees (penalty clause?), currency/jurisdiction
4. Flags deviations with risk level: LOW (Net-45), MEDIUM (no early payment discount), HIGH (penalty > 2%)
5. HITL for HIGH-risk deviations requiring legal review
6. Synthesis: creates deviation summary matrix with risk scores
7. Published to Confluence + Jira tickets created for HIGH-risk items via `jira_server.py:create_issue`

**Observability:** Goal Tree SSE (20 parallel agents); processing time ~5min for 20 contracts; cost ~$0.08/contract

**Eval path:** `grounding` (deviations reference actual contract text), `goal_success` (matrix published + tickets created)

**Expected output:** Confluence deviation matrix, Jira tickets for HIGH-risk items, risk-ranked list of contracts requiring renegotiation

**Failure modes:** Scanned PDFs → VisionParser extracts text with lower accuracy (flagged); non-standard contract structure → clause not found → flagged for manual review; contract in non-English language → translation step added

**Code references:** `app/agent/patterns/goal_tree.py`, `app/ingestion/parsers/pdf_parser.py`, `app/ingestion/chunkers/pdf_layout.py`, `app/rag/parent_child_chunker.py`, `app/governance/hitl.py`

---

## UC88 — Generate Vendor Risk Assessment

**Goal:** Research a new vendor's financial stability, security posture, regulatory compliance, and customer references using public data, producing a structured risk assessment report.

**Business problem:** Vendor risk assessments are research-intensive; automated gathering from public sources reduces time from 2 days to 2 hours.

**Actors:** Procurement officer, AgentVerse agent

**Inputs:** Vendor name, vendor website URL, assessment criteria Confluence template

**Agent pattern:** Supervisor + Debate — 3 parallel research agents (financial, security, compliance); Debate for conflicting findings

**RAG pattern:** `flare` — uncertainty-driven: many public data points are incomplete; FLARE triggers additional retrieval on uncertainty

**Memory used:** Episodic memory (past vendor assessments), Procedural memory (research tool sequence)

**Ingestion path:** Web pages (via RPAExecutor) → `SemanticChunker` → `text-embedding-3-small`; vendor security page → `HeadingChunker`

**Retrieval path:** `flare` — generates initial assessment → detects uncertainty → targeted web retrieval for gaps

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- No HITL required (advisory report, not binding)
- GuardrailChecker: scans for any internal system references in output
- Audit trail: all data sources logged with access timestamp

**End-to-end flow:**
1. Supervisor spawns 3 agents: (1) financial — annual report, funding, bankruptcy checks via web; (2) security — SOC2/ISO27001 certificates, breach history via web; (3) compliance — GDPR/CCPA/HIPAA certifications
2. FLARE: uncertain data points trigger additional web search via RPAExecutor
3. Debate: if agents disagree on risk level → structured argument exchange → consensus verdict
4. Synthesis: compiles risk matrix with score per category (GREEN/AMBER/RED)
5. Self-Consistency: risk conclusions validated across 3 model runs
6. Published to Confluence as structured risk assessment

**Observability:** Supervisor SSE with 3 agent progress; FLARE uncertainty trigger count; Debate SSE rounds; cost ~$0.30

**Eval path:** `grounding` (claims cite actual sources), `goal_success`, `citation_quality`

**Expected output:** Vendor risk assessment report with: overall risk score, category breakdown, evidence sources, recommended conditions for engagement

**Failure modes:** Vendor has no web presence → manual research flagged; conflicting public data → Debate pattern resolves; RPAExecutor blocked by anti-bot measures → alternative source found

**Code references:** `app/agent/patterns/supervisor.py`, `app/agent/patterns/debate.py`, `app/rag/agentic/patterns/flare.py`, `app/rpa/executor.py`

---

## UC89 — Create Content Calendar from Marketing Strategy

**Goal:** Transform a marketing strategy document into a 3-month content calendar with weekly themes, content ideas, channel assignments, and success metrics.

**Business problem:** Content planning is time-consuming; AI-driven calendar generation from strategy ensures alignment and saves 8 hours of planning per quarter.

**Actors:** Marketing manager, AgentVerse agent

**Inputs:** Marketing strategy PDF, brand guidelines Confluence page, content performance data

**Agent pattern:** Plan-Execute with Goal Tree — parallel content generation per month/channel

**RAG pattern:** `raptor` — hierarchical extraction from strategy doc: campaign themes → monthly focus → weekly execution

**Memory used:** Episodic memory (past content performance data), Procedural memory (content formula: hook + body + CTA structure)

**Ingestion path:** PDF strategy → `PDFParser` → `HeadingChunker` → `text-embedding-3-small`; brand guidelines → `HeadingChunker`

**Retrieval path:** `raptor` — extracts campaign pillars from strategy → groups into 3-month arc → weekly decomposition

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- No HITL required
- OutputContractBuilder: "calendar" in goal → structured JSON output with date fields
- Peer Review: brand consistency review (does content align with brand voice?)

**End-to-end flow:**
1. RAPTOR extracts: Q1 campaign themes, target segments, key messages, channels
2. Goal Tree: 3 parallel agents plan Month 1, 2, 3 content arcs
3. Each month agent: generates weekly themes aligned to campaign arc + seasonal hooks
4. Per week: 5 content ideas per channel (blog, LinkedIn, Twitter, email, webinar)
5. Peer Review: brand consistency check — flags off-brand language
6. Self-Refine: optimizes CTAs based on procedural memory of high-converting content patterns
7. Published to Notion via `notion_server.py:create_page` as calendar database

**Observability:** Goal Tree SSE; Peer Review score; `eval_score_recorded` with `grounding` (calendar aligns with strategy)

**Eval path:** `grounding` (calendar themes reference strategy), `goal_success` (Notion page published)

**Expected output:** 3-month content calendar in Notion with: weekly themes, 5 ideas per channel per week, estimated engagement metrics, resource assignments

**Failure modes:** Strategy document too vague → agent asks for clarification on top 3 priorities; brand guidelines conflict with strategy → Peer Review flags for human resolution; Notion API down → output as Confluence page

**Code references:** `app/agent/patterns/goal_tree.py`, `app/rag/agentic/patterns/raptor.py`, `app/agent/patterns/peer_review.py`, `app/agent/patterns/self_refine.py`

---

## UC90 — Optimize Underperforming Ad Campaigns

**Goal:** Analyze digital advertising campaign performance data, identify underperforming ad sets, diagnose root causes, and generate specific optimization recommendations.

**Business problem:** Marketing teams lack time to analyze campaign data depth; AI-driven optimization recommendations improve ROAS without requiring data science expertise.

**Actors:** Digital marketing manager, AgentVerse agent

**Inputs:** Campaign performance data (BigQuery), creative assets, target audience definitions

**Agent pattern:** ReAct with Self-RAG — iteratively queries campaign data; Self-RAG decides when to fetch additional context

**RAG pattern:** `self_rag` — adaptive retrieval: agent decides "do I need industry benchmark data for this metric?" before each retrieval call

**Memory used:** Episodic memory (past optimization outcomes: "reducing target age range by 10 years improved CTR 35%"), Execution memory (current campaign data within run)

**Ingestion path:** BigQuery CSV exports → row_group chunking (50 rows per chunk) → `text-embedding-3-small`; marketing KB → `HeadingChunker`

**Retrieval path:** `self_rag` — LLM decides `should_retrieve=True` for industry benchmarks and `should_retrieve=False` for calculations from in-context data

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- No HITL (recommendations, not auto-execution)
- Budget cap on BigQuery query cost per run ($5 limit)
- OutputContractBuilder: "recommendations" → JSON output with priority ranking

**End-to-end flow:**
1. `bigquery_server.py:run_query` — fetch campaign metrics: CTR, CPC, ROAS, conversion rate by ad set
2. Self-RAG: decides to retrieve industry benchmark data for comparison context
3. Analyzes performance gaps: identifies bottom 20% ad sets by ROAS
4. For each underperformer: diagnoses cause (audience fatigue? bid strategy? creative format?)
5. Episodic memory recalls: "narrow age range improved CTR for similar B2B campaigns"
6. Generates ranked recommendations: (1) pause, (2) refresh creative, (3) adjust targeting, (4) restructure bid strategy
7. Self-Refine: optimizes recommendation language for actionability

**Observability:** BigQuery query cost tracked; Self-RAG retrieve decision SSE events; `eval_score_recorded`

**Eval path:** `grounding` (recommendations reference actual performance data), `goal_success`

**Expected output:** Prioritized optimization plan with: underperformer list, root cause diagnosis per ad set, specific action items, estimated impact

**Failure modes:** BigQuery quota exceeded → analysis of partial data with caveat; campaign data missing key metrics → flagged for data team; all campaigns underperforming → broader strategy review recommended

**Code references:** `app/rag/agentic/patterns/self_rag.py`, `app/mcp/servers/bigquery_server.py`, `app/agent/patterns/self_refine.py`, `app/memory/episodic.py`

---

## UC91 — Transform Blog Post to Social Media Content Variants

**Goal:** Take a long-form blog post and generate optimized content variants for LinkedIn (3 posts), Twitter/X (5 tweets), Instagram caption, and email newsletter snippet.

**Business problem:** Repurposing content for multiple channels requires understanding platform-specific norms; automated variant generation maintains voice consistency across 4 channels.

**Actors:** Content creator, AgentVerse agent

**Inputs:** Blog post markdown or URL, brand voice guidelines, channel performance data

**Agent pattern:** ReAct with Self-Refine — generates each variant; Self-Refine improves based on brand guidelines

**RAG pattern:** `corrective_rag` — fetches brand voice examples and past high-performing posts; corrects when confidence on tone is low

**Memory used:** Procedural memory (per-channel formulas: LinkedIn=hook+insight+CTA, Twitter=compression+emoji, Instagram=visual-first)

**Ingestion path:** Blog post URL → RPA scraper or markdown direct → `SemanticChunker` → `text-embedding-3-small`

**Retrieval path:** `corrective_rag` — fetches brand examples when tone confidence < 0.7; sentence-window expansion for full context

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- GuardrailChecker: scans for false claims, competitive disparagement, regulatory language
- Peer Review: brand voice consistency score (target ≥ 0.8)
- OutputContractBuilder: "social media" → JSON output with `platform`, `content`, `hashtags` fields

**End-to-end flow:**
1. Reads blog post; identifies top 3 key insights and 1 hook concept
2. `corrective_rag` fetches: 3 past high-performing LinkedIn posts, 5 past high-performing tweets
3. Generates: LinkedIn post 1 (hook + insight + CTA), 2 (data point focus), 3 (question/engagement)
4. Generates: 5 tweets (one per key point, compressed, hashtags, emoji)
5. Generates: Instagram caption (visual-first, story-driven, hashtags)
6. Generates: Email snippet (subject line + 3-sentence preview)
7. Self-Refine per variant: improves CTAs, tightens copy
8. Peer Review: brand voice check — flags variants that sound "too formal" or "too casual"

**Observability:** Cost per batch ~$0.02 (low tier); `eval_score_recorded`; `GOAL_DURATION` target < 60s

**Eval path:** `goal_success` (all 10 variants generated), `grounding` (variants reference blog content)

**Expected output:** 10 ready-to-post content pieces (3 LinkedIn, 5 tweets, 1 Instagram, 1 email snippet) in JSON

**Failure modes:** Blog post inaccessible (paywalled) → error with clear message; brand guidelines missing → generic brand voice assumed; Peer Review rejects all variants → Reflection generates new approach

**Code references:** `app/agent/patterns/self_refine.py`, `app/rag/agentic/retriever_tool.py:retrieve_corrective`, `app/agent/patterns/peer_review.py`, `app/context/output_contract_builder.py`

---

## UC92 — Generate Field Service Report from Voice Notes and Photos

**Goal:** Process a field technician's voice-recorded notes and inspection photos to generate a structured service report with findings, recommendations, and parts list.

**Business problem:** Field technicians spend 45+ minutes on paperwork after site visits; voice + photo input reduces documentation time to under 5 minutes.

**Actors:** Field service technician, AgentVerse agent

**Inputs:** `.m4a` voice recording (5-15 min), JPEG photos of equipment (5-20 images)

**Agent pattern:** Plan-Execute — structured: transcribe audio → analyze photos → synthesize report

**RAG pattern:** `raptor` — hierarchical: audio segments → equipment analysis → combined findings → final report

**Memory used:** Procedural memory (field report structure: findings → root cause → recommendations → parts → labor estimate), Episodic memory (similar equipment failure patterns)

**Ingestion path:** Voice → `AudioParser` (Whisper with timestamps) → `TimestampChunker`; photos → `VisionParser` (GPT-4o vision) → `region` chunking

**Retrieval path:** `raptor` — audio + photo descriptions summarized hierarchically → full report context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o` (vision for photos), verifier: `gpt-4o-mini`

**Guardrails and governance:**
- No HITL required (report generation, not action)
- PII detection: customer names/addresses in audio
- OutputContractBuilder: "field report" → structured JSON with required fields

**End-to-end flow:**
1. `AudioParser._transcribe_with_whisper()` transcribes voice notes with segment timestamps
2. `TimestampChunker` segments into observation windows
3. `VisionParser` describes each photo: equipment type, visible damage/wear, measurement readings
4. RAPTOR level-1: correlates audio observations with photo descriptions
5. Episodic memory: recalls similar equipment failures and their root causes
6. Plan: structures report — site conditions → equipment inspection → findings → root cause → recommendations → parts list → labor estimate
7. Generates structured JSON report with all fields populated

**Observability:** Whisper API cost per audio minute; vision model cost per photo; total ~$0.15 for typical site visit; `GOAL_DURATION`

**Eval path:** `grounding` (report findings reference audio/photo evidence), `goal_success` (all required fields populated)

**Expected output:** Structured field service report JSON with: site ID, date, findings (with photo references), root cause, recommended actions, parts list (SKU + quantity), estimated labor hours

**Failure modes:** Audio quality poor → manual review flagged; photo blurry → VisionParser returns low-confidence description (noted in report); equipment not in KB → description only, no historical context

**Code references:** `app/ingestion/parsers/audio_parser.py`, `app/ingestion/parsers/vision_parser.py`, `app/ingestion/chunkers/timestamp.py`, `app/rag/agentic/patterns/raptor.py`

---

## UC93 — Create Equipment Maintenance Schedule from Spec Documents

**Goal:** Analyze equipment manufacturer specification documents and create a structured preventive maintenance schedule with intervals, procedures, and part replacement timelines.

**Business problem:** Maintenance schedules derived from spec documents are error-prone when done manually; AI extraction from PDFs ensures completeness.

**Actors:** Maintenance manager, AgentVerse agent

**Inputs:** Equipment spec PDFs (multiple manuals, 50-200 pages each), current maintenance log CSV

**Agent pattern:** Plan-Execute — structured: extract maintenance requirements → integrate with existing log → generate schedule

**RAG pattern:** `hybrid` — vector + FTS for maintenance interval extraction from PDF tables

**Memory used:** Procedural memory (schedule generation formula), Execution memory (extracted maintenance items within run)

**Ingestion path:** PDFs → `PDFParser` → `PDFLayoutChunker` (preserves table structure) → `TableChunker` (for maintenance schedule tables) → `text-embedding-3-small`

**Retrieval path:** `hybrid` (FTS for "maintenance interval" + "replace every" + vector for similar maintenance requirements) → parent-child expansion for full procedure context

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- OutputContractBuilder: "maintenance schedule" → JSON output with date and interval fields
- No HITL required

**End-to-end flow:**
1. `PDFParser` with `PDFLayoutChunker` extracts maintenance sections; `TableChunker` extracts interval tables
2. `hybrid` retrieval finds all maintenance intervals and procedures
3. Reads current maintenance log CSV for last service dates
4. LLM computes: next due dates = last service date + interval
5. Sorts by urgency: overdue (RED), due within 30 days (AMBER), upcoming (GREEN)
6. Generates schedule with: equipment ID, maintenance task, interval, last done, next due, responsible person, estimated duration
7. Published to Confluence; Jira tasks created for RED items via `jira_server.py:create_issue`

**Observability:** Cost per PDF ~$0.03; `GOAL_DURATION` target < 3min for 5 manuals

**Eval path:** `grounding` (intervals reference PDF sources), `goal_success`

**Expected output:** CSV/Confluence maintenance schedule + Jira tickets for overdue items

**Failure modes:** PDF without machine-readable text → VisionParser fallback; conflicting intervals in different manuals → flagged for human review; equipment ID not in current log → new entry created

**Code references:** `app/ingestion/parsers/pdf_parser.py`, `app/ingestion/chunkers/pdf_layout.py`, `app/ingestion/chunkers/table.py`, `app/mcp/servers/jira_server.py`

---

## UC94 — Detect IoT Sensor Telemetry Anomalies

**Goal:** Continuously analyze IoT sensor data streams to detect anomalies indicating equipment failure risk, generate alerts, and create maintenance requests before failure occurs.

**Business problem:** Equipment failures cause unplanned downtime costing $10k-$500k per incident; predictive anomaly detection enables planned maintenance.

**Actors:** Operations team, AgentVerse agent (scheduled via NLScheduler), Jira + Slack MCP servers

**Agent pattern:** ReAct with Self-RAG — iteratively queries sensor data; Self-RAG fetches baseline data when anomaly detected

**RAG pattern:** `self_rag` — adaptive: fetches baseline sensor patterns only when anomaly confidence > 0.7; skips retrieval for normal readings

**Memory used:** Execution memory (sensor baselines established in this run), Episodic memory (past anomaly → failure correlation patterns)

**Ingestion path:** PostgreSQL time-series sensor data → row_group chunking → `text-embedding-3-small`; maintenance KB → `HeadingChunker`

**Retrieval path:** `self_rag` — for anomalous readings, fetches: (1) baseline sensor profiles from KB, (2) past anomaly-to-failure correlations from episodic memory

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- HITL: for HIGH-severity anomalies (imminent failure risk) → human approval before maintenance request
- NLScheduler: "every 15 minutes" schedule
- Budget: $0.10/run cap

**End-to-end flow:**
1. NLScheduler triggers every 15 minutes
2. `postgres_server.py:query` fetches last 15 minutes of sensor readings (temperature, vibration, pressure, flow)
3. Self-RAG: if reading > 2σ from baseline → `should_retrieve=True` → fetches baseline profile + past anomaly patterns
4. Anomaly classification: NORMAL, WARNING (1.5-2σ), CRITICAL (>2σ)
5. Episodic memory: "vibration spike + temperature rise = bearing wear" → confidence 0.85
6. For CRITICAL: HITL gate → human approves maintenance request
7. `jira_server.py:create_issue` (P2 maintenance ticket) + `slack_server.py:send_message` to `#maintenance-alerts`

**Observability:** PostgreSQL query cost per run; anomaly detection rate; `GOAL_DURATION` target < 30s per run; `tool_success_rate`

**Eval path:** `goal_success` (tickets created when anomalies found), `grounding` (anomalies reference actual sensor readings)

**Expected output:** Jira maintenance tickets for CRITICAL anomalies + Slack alerts + trend dashboard update

**Failure modes:** PostgreSQL connection timeout → retry 3x then alert operations; sensor data gap → noted in ticket as "insufficient data"; false positive rate high → Reflexion stores lesson to adjust thresholds

**Code references:** `app/rag/agentic/patterns/self_rag.py`, `app/mcp/servers/postgres_server.py`, `app/triggers/nl_scheduler.py`, `app/governance/hitl.py`, `app/memory/episodic.py`

---

## UC95 — Customer Onboarding End-to-End

**Goal:** Execute complete new customer onboarding: verify identity documents, create accounts across 5 systems, provision access, and send personalized welcome communications.

**Business problem:** Manual onboarding takes 3-5 business days across multiple teams; automated orchestration reduces to 2-4 hours with zero handoff errors.

**Actors:** Customer success manager, AgentVerse Supervisor agent, multiple MCP servers

**Inputs:** Customer application form, identity documents (PDF/images), signed contract

**Agent pattern:** Supervisor with Goal Tree — 4 parallel sub-goals: (1) identity verification, (2) account creation across systems, (3) access provisioning, (4) welcome communication

**RAG pattern:** `raptor` — hierarchical document analysis for KYC; `corrective_rag` for onboarding policy

**Memory used:** Procedural memory (onboarding checklist), Episodic memory (past onboarding issues to avoid)

**Ingestion path:** Identity docs → `VisionParser` (GPT-4o vision) → PII detection; contract → `PDFParser` + `HeadingChunker`

**Retrieval path:** `raptor` for contract analysis; `corrective_rag` for onboarding policy

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-5.2`

**Guardrails and governance:**
- HITL: identity verification decision requires human approval before account creation
- REGULATED guardrail bundle (KYC/AML compliance)
- PII detection: all identity data handled with PII guardrails
- Full audit trail: every step logged for compliance
- RollbackEngine: account creations registered as compensating actions

**End-to-end flow:**
1. Sub-goal 1 (Identity): VisionParser extracts data from ID + address proof → cross-references against fraud database → HITL for human identity verification approval
2. Sub-goal 2 (Accounts): After HITL approval → parallel creation in: CRM (Salesforce), billing system (Stripe), support (Zendesk), collaboration (Slack), knowledge portal
3. Sub-goal 3 (Access): Provisions permissions based on purchased plan → tests each access
4. Sub-goal 4 (Welcome): Generates personalized welcome email + Slack message using customer name, purchased products, assigned CSM
5. Verifier: confirms all 5 accounts created, all accesses work, welcome sent
6. Audit: complete onboarding trail persisted to `audit_log` table

**Observability:** Goal Tree SSE (4 parallel sub-goals); HITL SSE event; RollbackEngine registrations; cost ~$0.50/onboarding

**Eval path:** `goal_success` (all accounts created + welcome sent), `tool_success_rate` (5 CRM systems), `safety` (PII handled correctly)

**Expected output:** Fully onboarded customer: 5 system accounts, provisioned access, personalized welcome, audit trail

**Failure modes:** Identity verification failed → HITL rejection → onboarding halted with customer notification; one system API down → RollbackEngine undoes completed accounts → retry; contract ambiguity → HITL for human clarification

**Code references:** `app/agent/patterns/supervisor.py`, `app/agent/patterns/goal_tree.py`, `app/ingestion/parsers/vision_parser.py`, `app/governance/hitl.py`, `app/reliability/rollback.py`, `app/governance/audit.py`

---

## UC96 — Employee Offboarding End-to-End

**Goal:** Execute complete employee offboarding on the last day: revoke all system access, archive data, notify relevant teams, collect equipment, and update HR records — with full audit trail.

**Business problem:** Manual offboarding misses access revocations 23% of the time (SOC2 finding); automated orchestration ensures complete access removal within 4 hours of termination.

**Actors:** HR team, AgentVerse Supervisor agent

**Inputs:** HR termination record (employee ID, last day, access list), IT asset register

**Agent pattern:** Supervisor with Goal Tree — 5 parallel sub-goals: (1) access revocation, (2) data archiving, (3) team notifications, (4) equipment tracking, (5) HR system updates

**RAG pattern:** `corrective_rag` — fetches offboarding checklist and compliance requirements

**Memory used:** Procedural memory (offboarding tool sequence), Episodic memory (past offboarding gaps found in audits)

**Ingestion path:** Offboarding policy → `HeadingChunker`; access list → structured JSON

**Retrieval path:** `corrective_rag` — compliance requirements for data retention, access removal timelines

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- HITL: executive/admin access revocation requires CISO approval
- Full audit trail: every access revocation logged with timestamp
- PolicyEngine: `github.remove_collaborator` requires ADMIN scope
- REGULATED guardrail bundle
- RollbackEngine: NOT registered (access revocation should not be auto-reversible)

**End-to-end flow:**
1. Sub-goal 1 (Access): Revoke GitHub org membership, Jira permissions, Slack deactivation, Google Workspace suspension, AWS IAM removal, VPN credentials
2. Sub-goal 2 (Archive): Archive employee's Google Drive to cold storage; export Slack DMs per legal hold policy
3. Sub-goal 3 (Notify): Slack DM to manager + team + IT; calendar block for equipment return
4. Sub-goal 4 (Equipment): Create IT ticket for laptop/equipment collection via `jira_server.py`
5. Sub-goal 5 (HR): Update HRIS with termination date; trigger final payroll processing flag
6. Verifier: confirms all 6 systems revoked; generates compliance audit report

**Observability:** Goal Tree SSE; access revocation count; HITL SSE for executive accounts; cost ~$0.15/offboarding; full audit trail

**Eval path:** `goal_success` (all accesses revoked), `tool_success_rate`, `safety` (no active access remains)

**Expected output:** Completed offboarding with audit report confirming: N access revocations, data archived, notifications sent, equipment ticket created, HR updated

**Failure modes:** System API down during revocation → flagged for manual completion + monitoring; employee data not found in system → HR notified; legal hold on data → archiving paused pending legal clearance

**Code references:** `app/agent/patterns/supervisor.py`, `app/agent/patterns/goal_tree.py`, `app/governance/audit.py`, `app/governance/hitl.py`, `app/mcp/servers/github_server.py`, `app/mcp/servers/slack_server.py`

---

## UC97 — Software Release Management End-to-End

**Goal:** Orchestrate a complete software release: analyze merged PRs, validate test coverage, generate changelog, coordinate deployment, and notify stakeholders.

**Business problem:** Release coordination requires tracking 20+ PRs, 5+ systems, and 3+ teams; automated orchestration reduces release preparation from 4 hours to 45 minutes.

**Actors:** Release engineer, AgentVerse Supervisor agent

**Inputs:** Release branch name, Jira sprint with completed tickets, CI/CD pipeline config

**Agent pattern:** Supervisor — 4 parallel agents: (1) PR analyzer, (2) test validator, (3) changelog generator, (4) deployment coordinator

**RAG pattern:** `raptor` — hierarchical: individual PR summaries → feature groups → release summary

**Memory used:** Episodic memory (past release issues and rollbacks), Procedural memory (release checklist)

**Ingestion path:** GitHub PR diffs → `ASTChunker`; Jira tickets → `SemanticChunker`; Confluence runbooks → `HeadingChunker`

**Retrieval path:** `raptor` for PR analysis; `lexical` for Jira ticket lookup

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-5.2`

**Guardrails and governance:**
- HITL: production deployment requires human approval
- PolicyEngine: `kubernetes.deploy` and `terraform.apply` require ADMIN scope + REQUIRE_APPROVAL
- RollbackEngine: deployment registered as compensating action (can trigger rollback)
- Full audit trail

**End-to-end flow:**
1. Agent 1: `github_server.py:list_prs` → analyzes each merged PR for scope/risk/breaking changes → RAPTOR summary
2. Agent 2: `github_server.py:get_check_runs` → validates test coverage > threshold; flags failing checks
3. Agent 3: Generates changelog from PR summaries (Breaking Changes / Features / Fixes)
4. HITL: human approves changelog + deployment plan
5. Agent 4: After approval → triggers CI/CD pipeline → monitors deployment health via `datadog_server.py`
6. If deployment fails → `RollbackEngine.rollback_all_async()` → previous version restored
7. Sends notifications: `slack_server.py` to `#releases`, `confluence_server.py` release notes

**Observability:** Supervisor SSE (4 agents); HITL SSE for deployment; RollbackEngine registration; cost ~$0.30/release

**Eval path:** `goal_success` (deployed + changelog published), `tool_success_rate` (all APIs succeed), `safety` (no unauthorized deployments)

**Expected output:** Deployed release with: published CHANGELOG, Confluence release notes, Slack announcements, deployment verification report

**Failure modes:** Test coverage below threshold → release blocked, Jira tickets created for gaps; deployment fails health checks → automatic rollback triggered; API rate limits during parallel PR analysis → serialized with delay

**Code references:** `app/agent/patterns/supervisor.py`, `app/mcp/servers/github_server.py`, `app/rag/agentic/patterns/raptor.py`, `app/reliability/rollback.py`, `app/governance/hitl.py`

---

## UC98 — Regulatory Audit Response End-to-End

**Goal:** Respond to a regulatory audit request by collecting evidence from 8 systems, analyzing compliance gaps, generating the formal response document, and creating remediation tasks.

**Business problem:** Regulatory audit responses require gathering evidence from 8+ systems in 5 business days; automated collection reduces this to 8 hours with higher completeness.

**Actors:** Compliance officer, AgentVerse Supervisor agent

**Inputs:** Audit request letter (PDF), list of required evidence items, compliance framework (SOC2/ISO27001/GDPR)

**Agent pattern:** Goal Tree + Debate + Peer Review — parallel evidence collection; Debate for disputed interpretations; Peer Review for accuracy

**RAG pattern:** `raptor` — hierarchical: individual evidence items → control areas → full audit response

**Memory used:** Episodic memory (past audit findings and responses), Procedural memory (evidence collection checklist)

**Ingestion path:** Audit letter → `PDFParser`; compliance framework → `HeadingChunker`; evidence artifacts → multiple parsers

**Retrieval path:** `raptor` for synthesis; `corrective_rag` for compliance policy context

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-5.2`

**Guardrails and governance:**
- HITL: final audit response requires CISO + Legal sign-off before submission
- REGULATED guardrail bundle
- Full audit trail: evidence collection logged with provenance
- Peer Review: compliance accuracy validation

**End-to-end flow:**
1. Goal Tree: 8 parallel evidence collectors — access logs (postgres), system configs (GitHub), policies (Confluence), training records (HRIS), incident history (Jira), pen test reports (file storage), vendor assessments (file storage), change management records (Jira)
2. RAPTOR: organizes evidence by control area
3. Debate: disputed control implementations (e.g., "does our MFA implementation satisfy this control?") → structured argument → consensus
4. Gap analysis: identifies controls with insufficient evidence → creates remediation Jira tickets
5. Peer Review: compliance officer validates accuracy and completeness
6. HITL: CISO + Legal review and approve before submission
7. Generates formal audit response document + evidence package

**Observability:** Goal Tree SSE (8 parallel); Debate SSE rounds; HITL SSE for final approval; cost ~$1.50 for full audit response

**Eval path:** `grounding` (all claims cite actual evidence), `goal_success` (response document complete), `citation_quality`

**Expected output:** Formal audit response package: control mapping, evidence artifacts, gap analysis, remediation plan with Jira tickets, compliance attestation ready for submission

**Failure modes:** Evidence system unavailable → noted as gap in response with alternative evidence; conflicting evidence found → Debate resolves or flags for human resolution; HITL approval delayed → audit response timeline tracked with escalation

**Code references:** `app/agent/patterns/goal_tree.py`, `app/agent/patterns/debate.py`, `app/agent/patterns/peer_review.py`, `app/governance/hitl.py`, `app/governance/audit.py`

---

## UC99 — Competitive Intelligence Gathering End-to-End

**Goal:** Research 3 key competitors across product features, pricing, positioning, and customer sentiment to produce a strategic competitive brief for the product team.

**Business problem:** Competitive intelligence requires 2-3 days of manual research; automated gathering from public sources reduces to 4 hours with more comprehensive coverage.

**Actors:** Product strategist, AgentVerse Supervisor agent

**Inputs:** Competitor names, research framework template, product feature matrix template

**Agent pattern:** Supervisor + FLARE + Debate — 3 parallel research agents (one per competitor); FLARE for uncertain data; Debate for conflicting market interpretations

**RAG pattern:** `flare` — uncertainty-driven: many competitive claims are ambiguous; FLARE triggers additional research retrieval on low-confidence findings

**Memory used:** Episodic memory (past competitive analyses and their accuracy), Procedural memory (research methodology: product → pricing → positioning → sentiment)

**Ingestion path:** Web pages via RPAExecutor → `SemanticChunker` → `text-embedding-3-small`; app store reviews → `SemanticChunker`

**Retrieval path:** `flare` — initial research → uncertainty detection → targeted additional retrieval for unclear claims

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- GuardrailChecker: scans output for unsupported competitive claims (defamation risk)
- Peer Review: accuracy of competitive claims (all must be publicly sourced)
- Reflexion: stores lessons about reliable vs unreliable data sources

**End-to-end flow:**
1. Supervisor spawns 3 agents (one per competitor): RPAExecutor scrapes product pages, pricing pages, case studies, G2/Capterra reviews
2. FLARE: for uncertain pricing tiers or feature availability → triggers additional web search
3. Each agent produces: feature matrix, pricing breakdown, positioning statement, review sentiment score
4. Debate: when agents disagree on market positioning → structured argument → consensus
5. Synthesis: produces side-by-side comparison + strategic gaps + opportunities
6. Reflexion: stores "G2 reviews most reliable for SMB sentiment" as lesson
7. Published to Confluence as competitive brief

**Observability:** Supervisor SSE (3 parallel agents); FLARE uncertainty trigger count; Debate SSE rounds; cost ~$0.40; Reflexion lesson storage

**Eval path:** `grounding` (all claims cite public sources), `goal_success`, `citation_quality`

**Expected output:** Competitive brief with: feature comparison matrix, pricing analysis, positioning map, customer sentiment scores, 5 strategic recommendations

**Failure modes:** Competitor website blocks scraping → alternative sources (press releases, SEC filings) used; rapidly changing pricing → noted as "as of date" with monitoring suggestion; conflicting public claims → Debate resolves with confidence scores

**Code references:** `app/agent/patterns/supervisor.py`, `app/agent/patterns/debate.py`, `app/rag/agentic/patterns/flare.py`, `app/rpa/executor.py`, `app/agent/patterns/reflexion.py`

---

## UC100 — Full Incident to Resolution End-to-End

**Goal:** Handle a complete production incident lifecycle: from initial alert through diagnosis, fix deployment, verification, customer notification, postmortem, and lessons learned — fully orchestrated.

**Business problem:** P1 incidents require coordinating 6+ systems, 4+ teams, and 15+ decisions under extreme time pressure; automated orchestration reduces total time-to-resolution by 60% and ensures no steps are missed.

**Actors:** Incident commander, AgentVerse Supervisor agent (4 sub-agents), full MCP stack

**Inputs:** PagerDuty alert, affected service configuration

**Agent pattern:** Supervisor — 4 sequential-then-parallel phases: (1) Detect & Triage, (2) Diagnose & Fix, (3) Deploy & Verify, (4) Communicate & Learn

**RAG pattern:** `raptor` + `flare` — RAPTOR for log analysis; FLARE for uncertain root causes

**Memory used:** All memory types: Working (incident state), Execution (past similar incidents), LTM (architectural context), Episodic (past incident patterns), Reflexion (lessons from this incident after resolution)

**Ingestion path:** PagerDuty alert → `SemanticChunker`; service logs → `TimestampChunker`; runbooks → `HeadingChunker`

**Retrieval path:** `raptor` for log hierarchical analysis; `flare` for uncertain diagnostics; `memory` strategy for LTM architectural context

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o` (speed for triage), verifier: `gpt-5.2` (accuracy for fix validation), embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL at 3 critical checkpoints: (1) before attempting fix, (2) before deploying to production, (3) before closing incident
- PolicyEngine: production deployments and rollbacks require ADMIN + REQUIRE_APPROVAL
- RollbackEngine: every fix deployment registered for auto-rollback capability
- Full audit trail: complete incident timeline for SOC2/compliance
- Budget: no cap during active P1 incident

**End-to-end flow:**
1. **Phase 1 — Detect & Triage (0-2 min):** PagerDuty webhook → Supervisor spawns parallel: fetch metrics (Datadog), check deployments (GitHub), search runbooks (Confluence). HITL: human acknowledges incident.
2. **Phase 2 — Diagnose & Fix (2-20 min):** RAPTOR analyzes logs hierarchically. FLARE fetches additional context for uncertain root causes. LTM provides architectural context. Agent proposes fix (code change or config). HITL: human approves fix approach.
3. **Phase 3 — Deploy & Verify (20-35 min):** RollbackEngine registers deployment as compensating action. `github_server.py:create_pr` → CI runs. HITL: human approves production deployment. Kubernetes rolling update via `kubernetes_server.py`. Datadog monitors health metrics. If health check fails → `RollbackEngine.rollback_all_async()`.
4. **Phase 4 — Communicate & Learn (35-60 min):** Customer notification drafted (Peer Review for tone) → HITL approval → sent. Slack `#incidents` update. RAPTOR-powered RCA document generated. Reflexion stores N lessons: "service X fails when Y happens". Jira postmortem ticket created with action items.
5. Incident closed: HITL final closure approval.

**Observability:** Complete SSE event timeline from `pattern_assembled` → `rag_strategy_selected` × N → `eval_score_recorded`; all 4 HITL SSE events; RollbackEngine registrations; Prometheus: `GOAL_DURATION` (target < 60min), `TOOL_CALL_TOTAL` (30-50 calls expected), `orchestration_profile_built_total`; cost ~$2.00 for complete incident lifecycle

**Eval path:** `goal_success` (incident resolved + postmortem published), `tool_success_rate` (30+ tool calls), `safety` (no unauthorized actions), `grounding` (RCA references evidence), `retrieval_confidence` (RAPTOR + FLARE quality)

**Expected output:** Complete incident record: real-time Slack updates, Jira P1 ticket + postmortem, customer notification sent, RCA document, N Reflexion lessons stored for future incidents, deployment audit trail

**Failure modes:** Auto-fix causes more damage → RollbackEngine immediately reverts; HITL timeout at Phase 2 → incident commander auto-escalated; multiple concurrent P1s → Bulkhead limits concurrent agents to 5/tenant; cost alert at $5 → human notified but incident work continues (P1 override); Reflexion store full → oldest lessons archived

**Code references:** `app/agent/patterns/supervisor.py`, `app/governance/hitl.py`, `app/reliability/rollback.py`, `app/rag/agentic/patterns/raptor.py`, `app/rag/agentic/patterns/flare.py`, `app/agent/patterns/reflexion.py`, `app/memory/episodic.py`, `app/memory/long_term.py`, `app/governance/audit.py`, `app/observability/runtime_decision_trace.py`, `app/mcp/servers/pagerduty_server.py`, `app/mcp/servers/datadog_server.py`, `app/mcp/servers/kubernetes_server.py`, `app/mcp/servers/github_server.py`, `app/mcp/servers/slack_server.py`, `app/mcp/servers/jira_server.py`

---

*End of Use Cases 81–100. Total: 20 use cases covering Multimodal, Incident Response, Procurement, Marketing, Field Operations, and Multi-domain Complex workflows.*
