# Use Cases 41–50: HR Operations, Product Management & Project Management

---

## Use Case 41: Score Resume vs Job Description

**Goal:** Compare a candidate's resume against a job description, score the candidate across key dimensions (technical skills, experience level, cultural fit signals), and produce a structured hiring recommendation report.

**Business problem:** Recruiters spend 30+ hours per open role manually screening resumes; automated scoring with explainable reasoning accelerates screening while reducing unconscious bias.

**Actors:** Recruiter, hiring manager, AgentVerse agent, Confluence MCP server (job requirements KB)

**Inputs:** Candidate resume (PDF), job description (text or Confluence page), company competency framework (Confluence), scoring rubric

**Agent pattern:** Peer Review — two-pass evaluation: (1) technical skills match scoring, (2) experience and trajectory assessment; scores averaged with independent rubric enforcement

**RAG pattern:** `hybrid` — vector search for semantic skill similarity (Python ↔ "Python programming experience") + lexical for exact must-have requirements ("5 years", "AWS certified"); ColBERT reranking for precision on competency framework

**Memory used:** Execution memory (past candidate evaluations for this role to ensure score calibration consistency), Procedural memory (learned rubric application sequence)

**Ingestion path:** Resume PDF → `PDFParser` + `HeadingChunker` (by section: Education, Experience, Skills, Projects) → `text-embedding-3-small` → `knowledge_chunks_1536`; job description → `SemanticChunker`; competency framework → `HeadingChunker`

**Retrieval path:** Job requirement phrase → `hybrid` (FTS for exact keywords + vector for semantic equivalent phrasing) → ColBERT reranking → top-10 matching resume evidence chunks

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: PII detection and redaction — candidate name, email, address, phone number redacted in processing logs; only anonymized candidate ID in audit trail
- PolicyEngine: resume data access restricted to `hr:read` scope; no resume data retained after 30 days per data retention policy
- Output contract: scores must include explicit evidence citations (resume section + quote) for each dimension — no score without citation
- Audit trail: scoring decision with evidence logged for each candidate

**End-to-end flow:**
1. Recruiter submits resume PDF + JD via `POST /goals` with candidate ID (anonymized); queues to `goals.professional`
2. `_node_initialize`: `hybrid` RAG strategy; Peer Review pattern; OutputContractBuilder detects "score" in goal → structured JSON score contract
3. `_node_rag_retrieval`: `PDFParser` extracts resume sections; `HeadingChunker` indexes by section; hybrid retrieval matches JD requirements to resume evidence
4. `_node_plan`: identifies 6 scoring dimensions: technical skills, years experience, domain knowledge, leadership signals, communication quality, culture fit indicators
5. `_node_execute (Pass 1 — Technical)`: for each dimension → retrieve evidence chunks → score 0–10 with quoted justification; "AWS certified" requirement → check certifications section; lexical match for exact required skills
6. `_node_peer_review (Pass 2 — Experience & Trajectory)`: independent reviewer scores seniority progression, scope of past roles, impact demonstrated; resolves any dimension score disagreements
7. Aggregates weighted score: technical × 0.40, experience × 0.30, domain × 0.20, other × 0.10
8. Generates recommendation: STRONG YES / YES / MAYBE / NO with top 3 strengths and top 2 concerns

**Observability:** `peer_review_score` for calibration tracking; `GOAL_DURATION` target < 90s per resume; cost ~$0.04/resume; PII detection event count; `eval_score_recorded`

**Eval path:** `grounding` (score dimensions cite actual resume text), `goal_success` (structured report generated), calibration against hiring manager ground truth (A/B eval)

**Expected output:** Structured hiring recommendation: dimension scores (0–10) with evidence citations, overall score, recommendation tier, top strengths/concerns, comparable candidate benchmarks for this role

**Failure modes:** Resume PDF is scanned image → `PDFParser` falls back to OCR; resume in non-English language → translation step added before scoring; competency framework not loaded → scores with lower confidence and flags for manual review; candidate experience far outside expected range (intern vs senior) → verifier flags calibration issue

**Code references:** `app/ingestion/parsers/pdf_parser.py`, `app/ingestion/chunkers/heading_chunker.py`, `app/agent/patterns/peer_review.py`, `app/rag/engine.py:retrieve_hybrid`, `app/intelligence/guardrails.py`

---

## Use Case 42: Generate Employee Onboarding Checklist

**Goal:** Generate a personalized onboarding checklist for a new employee based on their role, team, start date, and location, pulling tasks from the company knowledge base and HR systems.

**Business problem:** Generic onboarding checklists miss role-specific requirements; personalized checklists reduce new hire time-to-productivity and prevent compliance gaps (mandatory training, system access provisioning).

**Actors:** HR coordinator, hiring manager, AgentVerse agent, Confluence MCP server, HR system MCP server

**Inputs:** New hire profile (role, level, team, location, start date, remote/onsite), Confluence HR KB (onboarding guides per department), IT provisioning runbook, compliance training requirements

**Agent pattern:** Plan-Execute — structured phases: (1) gather role requirements from Confluence, (2) query HR system for mandatory training by location/level, (3) assemble personalized checklist, (4) publish to onboarding portal

**RAG pattern:** `raptor` — hierarchical retrieval from large HR KB: top-level (universal onboarding) → department-level → role-specific → seniority-level → location-specific overrides

**Memory used:** Execution memory (past onboarding checklists for similar roles for consistency), Procedural memory (learned: always include compliance training before system access for regulated industries)

**Ingestion path:** Confluence HR KB → `HeadingChunker` (by section: IT Setup, Compliance, Team-specific, Benefits) → `text-embedding-3-small` → `knowledge_chunks_1536`; HR system → structured API calls

**Retrieval path:** Role + team + location → `raptor`: (1) universal tasks (Day 1 must-dos), (2) department tasks (Engineering: laptop setup, repo access), (3) role tasks (Senior Engineer: on-call rotation, architecture review access), (4) location overrides (GDPR training for EU employees)

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: HR system API requires `hr:read` scope; Confluence write requires `confluence:write`
- GuardrailChecker: PII redaction in processing (employee ID replaces name in logs)
- OutputContractBuilder: "checklist" in goal → structured markdown with checkbox format and owner assignments
- Compliance enforcement: mandatory training tasks cannot be removed or deferred

**End-to-end flow:**
1. HR coordinator submits `POST /goals` with new hire profile JSON; queues to `goals.professional`
2. `_node_initialize`: RAPTOR strategy selected; Plan-Execute pattern; OutputContractBuilder → checklist contract
3. `_node_rag_retrieval`: RAPTOR hierarchical retrieval from HR KB: universal → Engineering department → Senior SRE role → US-West location
4. `_node_plan`: assembles 4-phase plan: Week 1 (systems, accounts, compliance), Week 2 (team processes, tooling), Month 1 (role proficiency, 1:1 cadences), Month 3 (first major project milestone)
5. `_node_execute`: queries HR system for mandatory training by role/location; fetches IT provisioning tasks from runbook; fetches team-specific tasks from hiring manager's Confluence page; assigns owners and due dates per task
6. Checklist assembled with 35–60 items categorized by phase, owner (IT/HR/manager/new hire), and due date
7. `confluence_server.py:create_page` creates personalized onboarding page in new hire's space
8. `_node_verify`: checks all mandatory compliance items present; verifier confirms role-specific tasks match role profile

**Observability:** RAPTOR SSE showing retrieval hierarchy; `GOAL_DURATION` target < 2min; cost ~$0.02/checklist; `tool_success_rate` for HR system and Confluence API

**Eval path:** `goal_success` (Confluence page created with all required sections), `grounding` (tasks reference actual KB sources), completeness score (mandatory tasks coverage)

**Expected output:** Personalized Confluence onboarding page with categorized checklist (35–60 items), owner assignments, due dates, and links to relevant resources

**Failure modes:** HR system returns sparse role data → agent falls back to level-based defaults and flags for manager review; Confluence KB has conflicting information for role → surfaces both options with flag; location not in KB → uses generic + flags for HR coordinator to add location-specific items; new role type not in KB → generates generic engineering template and requests HR to enrich KB

**Code references:** `app/rag/agentic/patterns/raptor.py`, `app/ingestion/chunkers/heading_chunker.py`, `app/mcp/servers/confluence_server.py`, `app/context/output_contract_builder.py`, `app/agent/loop.py:_node_plan`

---

## Use Case 43: Analyze Engagement Survey Response Themes

**Goal:** Process a set of open-ended employee engagement survey responses, identify recurring themes, sentiment patterns, and critical concerns, and produce an executive summary with department-level breakdowns.

**Business problem:** Hundreds of free-text survey responses are manually analyzed by HR teams, taking weeks and introducing analyst bias; automated thematic analysis reveals patterns at scale with consistent methodology.

**Actors:** CHRO, HR analytics team, AgentVerse agent

**Inputs:** CSV export of survey responses (employee ID anonymized, department, tenure bracket, response text), question prompts, previous survey themes for trend comparison

**Agent pattern:** Supervisor — main agent coordinates 2 parallel sub-agents: (1) theme extraction and clustering agent, (2) sentiment scoring agent; results merged for executive report

**RAG pattern:** `hybrid` — vector clustering for thematic similarity (responses about management → same cluster) + lexical for exact critical keywords ("burnout", "toxic", "safety", "discrimination") that must always be flagged

**Memory used:** Execution memory (prior quarter survey themes for trend delta), Episodic memory (department-level baseline sentiment scores), Long-term memory (multi-year engagement trend patterns)

**Ingestion path:** CSV responses → `row_group` chunker (groups responses by department × tenure bracket) → `text-embedding-3-small`; prior survey summaries → `SemanticChunker`

**Retrieval path:** Response batch → `hybrid`: (1) vector clustering into emergent themes, (2) lexical detection of mandatory escalation keywords; semantic search against prior themes for trend comparison

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: hard prohibition on identifying individual respondents from response text patterns; minimum group size ≥ 5 responses per demographic slice before reporting
- PolicyEngine: survey data access restricted to `hr:sensitive:read` scope; data not retained post-analysis
- Output contract: all quotes in report must be paraphrased (not verbatim) unless from cohort of 20+ respondents to preserve anonymity
- Audit trail: data access logged; analysis methodology documented for employee disclosure

**End-to-end flow:**
1. HR team uploads CSV to `POST /goals` with department breakdown flag; queues to `goals.professional`
2. `_node_initialize`: `hybrid` strategy; Supervisor pattern with 2 sub-agents; GuardrailChecker anonymization rules activated
3. `_node_rag_retrieval`: `row_group` chunker groups responses by department and tenure; prior survey theme embeddings loaded for comparison
4. Supervisor launches Sub-agent 1 (theme extraction): vector-clusters responses into 8–12 emergent themes; labels each cluster using LLM; scores theme prevalence by response count and department distribution
5. Supervisor launches Sub-agent 2 (sentiment scoring): assigns sentiment (positive/neutral/negative) + intensity (1–5) per response; aggregates by theme and department
6. Lexical scan for escalation keywords across all responses → any "discrimination" or "safety" response → immediate HITL notification to CHRO regardless of group size
7. Supervisor merges theme × sentiment matrix; computes delta from prior survey themes
8. Generates executive summary + department heat map + theme trend chart data

**Observability:** Supervisor SSE showing parallel agent progress; cluster quality metrics (silhouette score); `GOAL_DURATION` target < 10min for 500 responses; anonymization event count; cost ~$0.08/100 responses

**Eval path:** `goal_success` (executive summary generated with all department breakdowns), `grounding` (theme labels consistent with response content), anonymization compliance (no PII in output)

**Expected output:** Executive summary report with: overall engagement score, top 5 themes with prevalence and sentiment, department heat map, trend delta from prior survey, critical concern alerts, recommended action areas

**Failure modes:** Response CSV has < 5 responses per department → department results suppressed with note; escalation keyword found in single-respondent department → HR notified but result suppressed from report; clustering produces too many micro-themes (> 15) → hierarchical merge step; prior survey themes not available → trend analysis skipped with note

**Code references:** `app/ingestion/chunkers/row_group_chunker.py`, `app/agent/patterns/supervisor.py`, `app/rag/engine.py:retrieve_hybrid`, `app/intelligence/guardrails.py`, `app/governance/hitl.py`

---

## Use Case 44: Compile 360 Feedback into Performance Review

**Goal:** Synthesize multiple stakeholder 360-degree feedback submissions for an employee into a coherent, structured performance review document with strength/development themes, representative quotes, and recommended rating.

**Business problem:** Managers spend 3–5 hours per employee synthesizing 360 feedback manually; automated synthesis ensures comprehensive representation while reducing recency bias and halo/horn effects.

**Actors:** Manager, HR partner, AgentVerse agent

**Inputs:** Multiple free-text 360 feedback submissions (peer, manager, direct report, self-assessment), company competency framework, prior review (if available), performance objectives for the review period

**Agent pattern:** Peer Review — (1) synthesis pass: themes extracted and organized by competency, (2) calibration pass: independent reviewer checks for bias patterns (recency, leniency, harshness) and corrects

**RAG pattern:** `corrective_rag` — retrieves company competency definitions and behavioral anchors from KB; self-corrects when synthesis doesn't align with competency framework definitions; regenerates if grounding score < 0.65

**Memory used:** Execution memory (prior year review themes for longitudinal perspective), Procedural memory (learned: always surface specific behavioral examples, not just adjective summaries), Long-term memory (cross-employee calibration benchmarks for the team)

**Ingestion path:** Feedback text inputs → `SemanticChunker` (by competency signals) → `text-embedding-3-small`; competency framework → `HeadingChunker`; prior review → `SemanticChunker`

**Retrieval path:** Feedback themes → `corrective_rag`: retrieves competency behavioral anchors → checks synthesis aligns with anchor descriptions → regenerates mapping if misaligned

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: PII detection — reviewer names redacted in synthesis (identified only as "a peer" / "a direct report"); no demographic signals used in synthesis
- PolicyEngine: feedback data requires `hr:sensitive:read` scope; 90-day data retention only
- Bias detection: verifier checks for leniency bias (all competencies scored 4/5+), recency bias (only last-month events cited), or harshness (single negative event dominating)
- OutputContractBuilder: "performance review" → structured sections: Summary, Competency Evidence, Development Areas, Rating Recommendation, Suggested Actions
- Audit trail: synthesis methodology documented for employee right-to-explanation

**End-to-end flow:**
1. Manager submits feedback bundle via `POST /goals`; queues to `goals.professional`
2. `_node_initialize`: `corrective_rag` strategy; Peer Review pattern; PII redaction activated; OutputContractBuilder → review structure contract
3. `_node_rag_retrieval`: `corrective_rag` retrieves behavioral anchors for each of 8 competencies; fetches prior year review for longitudinal context
4. `_node_plan`: groups feedback signals by competency; identifies N evidence clusters
5. `_node_execute (Pass 1 — Synthesis)`: for each competency → extract behavioral examples from all feedback sources (weighted: manager 40%, peers 35%, directs 15%, self 10%) → synthesize into 2–3 sentence evidence-backed narrative with paraphrased quotes → assign preliminary rating (1–5) with behavioral anchor reference
6. `_node_peer_review (Pass 2 — Calibration)`: independent reviewer checks for bias patterns; flags leniency if 6+ competencies rated 4+; flags recency if > 60% examples from last 30 days; injects critique → synthesis agent revises
7. Generates development recommendations: top 2 strengths (leverage), top 2 areas (develop), suggested next 6-month objectives
8. Recommended rating justification mapped to company rating scale

**Observability:** `peer_review_score` for calibration quality; bias pattern detection events; `GOAL_DURATION` target < 5min; cost ~$0.10/review; grounding score per competency

**Eval path:** `grounding` (competency narratives cite actual feedback signals), `safety` (no PII in output), `goal_success` (all sections complete), bias detection rate

**Expected output:** Structured performance review document with 8 competency assessments (narrative + behavioral examples + rating), overall rating recommendation, 2 development priorities, and 6-month action plan

**Failure modes:** Insufficient feedback volume (< 3 submissions) → agent flags as "insufficient data" and identifies missing stakeholder categories; conflicting feedback between sources (peer says collaborative, manager says siloed) → both perspectives documented with "areas for clarification" flag; self-assessment significantly diverges from peer feedback → gap surfaced explicitly for manager discussion

**Code references:** `app/rag/agentic/patterns/corrective_rag.py`, `app/agent/patterns/peer_review.py`, `app/intelligence/guardrails.py`, `app/ingestion/chunkers/heading_chunker.py`, `app/governance/audit.py`

---

## Use Case 45: Synthesize App Store + Zendesk Reviews

**Goal:** Aggregate customer reviews from App Store and Google Play with Zendesk support tickets, identify top recurring pain points and feature requests, and generate a prioritized product feedback summary for the product team.

**Business problem:** Product managers synthesize hundreds of app store reviews and support tickets manually each sprint; automated synthesis reveals signal from noise and ensures voice-of-customer informs roadmap decisions.

**Actors:** Product manager, AgentVerse agent, Zendesk MCP server

**Inputs:** App Store and Google Play review exports (JSON), Zendesk ticket exports for the period, current product roadmap themes (Confluence), product taxonomy for categorization

**Agent pattern:** Supervisor — main agent coordinates 2 parallel sub-agents: (1) app store review analyzer, (2) Zendesk ticket theme extractor; Supervisor merges and cross-references

**RAG pattern:** `raptor` — hierarchical synthesis: raw reviews → per-theme clusters → category summaries → executive narrative; enables coherent synthesis across 500+ reviews

**Memory used:** Execution memory (prior period feedback themes for trend tracking), Episodic memory (known seasonal patterns in reviews), Long-term memory (multi-quarter feedback trend evolution)

**Ingestion path:** App store reviews → `SemanticChunker` (by rating tier: 1–2 star, 3 star, 4–5 star) → `text-embedding-3-small`; Zendesk tickets → `zendesk_server.py:list_tickets` → `SemanticChunker`; product taxonomy → `HeadingChunker`

**Retrieval path:** Review batch → `raptor`: (1) cluster by semantic similarity → emergent themes, (2) summarize theme clusters, (3) cross-reference themes with Zendesk ticket categories, (4) map to product taxonomy for roadmap relevance

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: customer PII redacted from Zendesk data (email, name → anonymized); only ticket IDs retained
- PolicyEngine: `zendesk.list_tickets` requires `zendesk:read` scope
- OutputContractBuilder: "product feedback" → structured sections: Executive Summary, Top Pain Points, Feature Requests, Rating Trends, Competitive Mentions
- Data minimization: only ticket subject + body processed, not customer contact details

**End-to-end flow:**
1. PM submits `POST /goals` with date range and app version filter; queues to `goals.professional`
2. `_node_initialize`: RAPTOR strategy; Supervisor pattern; OutputContractBuilder → product feedback structure
3. Supervisor launches 2 parallel sub-agents
4. Sub-agent 1: fetches 500 app store reviews via scraping or API; `SemanticChunker` by rating; RAPTOR clusters into themes; counts per theme
5. Sub-agent 2: `zendesk_server.py:list_tickets` with date filter → 300 tickets; RAPTOR clusters by issue type; cross-references with known product categories
6. Supervisor merges: identifies themes present in both channels (convergence = high priority), themes unique to each channel (divergence = investigate); counts combined signal volume
7. Cross-references themes against current roadmap (from Confluence) → classifies as: ALIGNED / NOT_PLANNED / SUPERSEDES_PLANNED
8. Generates prioritized report: pain points ranked by combined signal volume × severity; feature requests ranked by frequency × strategic alignment

**Observability:** RAPTOR SSE with cluster hierarchy; Supervisor SSE for parallel agents; `GOAL_DURATION` target < 8min for 800 reviews/tickets; cost ~$0.10/synthesis run; theme stability score (% themes repeated from prior period)

**Eval path:** `goal_success` (all report sections complete), `grounding` (theme descriptions cite representative review text), signal coverage rate (% reviews categorized)

**Expected output:** Product feedback summary with: top 10 pain points (volume + severity), top 10 feature requests (frequency + roadmap alignment), App Store rating trend, Zendesk ticket volume trend, 3 urgent items for immediate PM attention

**Failure modes:** App Store API returns rate limit → batch scraping with 2s delays; Zendesk ticket body too long → truncate to 500 chars for embedding; reviews in multiple languages → language detection + parallel translation before clustering; review spam detected (many identical reviews) → deduplication before analysis

**Code references:** `app/mcp/servers/zendesk_server.py`, `app/agent/patterns/supervisor.py`, `app/rag/agentic/patterns/raptor.py`, `app/ingestion/chunkers/semantic_chunker.py`, `app/context/output_contract_builder.py`

---

## Use Case 46: Generate PRD from Feature Request

**Goal:** Transform a brief feature request into a complete Product Requirements Document (PRD) with problem statement, user stories, acceptance criteria, scope boundaries, and technical considerations, informed by the existing product knowledge base.

**Business problem:** Writing comprehensive PRDs from scratch is time-consuming; automated generation from a brief ensures consistent structure, surfaces related past decisions, and accelerates from idea to engineering kickoff.

**Actors:** Product manager, AgentVerse agent, Confluence MCP server, Jira MCP server

**Inputs:** Feature request brief (1–3 paragraphs), existing PRD templates (Confluence), related Jira epics and user stories, product strategy documentation, competing features documentation

**Agent pattern:** Tree-of-Thoughts — generates 3 alternative PRD framings for the same feature (user-centric, technical-constraint, business-value framings); evaluates each; expands the highest-scoring framing into a full PRD

**RAG pattern:** `hybrid` — vector search for semantically related prior PRDs and user research + lexical search for specific product terms, feature names, and component names referenced in the brief

**Memory used:** Execution memory (PRDs for related features in the same domain), Procedural memory (learned PRD structure: Problem Statement → Goals → Non-Goals → User Stories → AC → Technical Considerations), Episodic memory (features that were descoped and why)

**Ingestion path:** Confluence PRDs → `HeadingChunker` (by section) → `text-embedding-3-small`; Jira epics → `jira_server.py:search_issues` → `SemanticChunker`; product strategy → `HeadingChunker`

**Retrieval path:** Feature brief → `hybrid`: (1) vector search for related PRDs and user research, (2) lexical for specific product/component names referenced → context for Tree-of-Thoughts evaluation

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: Confluence write requires `confluence:write` scope; Jira create requires `jira:write` scope
- OutputContractBuilder: "PRD" in goal → structured document contract with mandatory sections (Problem, Goals, Non-Goals, User Stories, Acceptance Criteria, Technical Risks)
- Non-goals section enforced: verifier checks PRD includes explicit scope exclusions

**End-to-end flow:**
1. PM submits feature brief via `POST /goals` with context (product area, target user segment, business driver); queues to `goals.professional`
2. `_node_initialize`: Tree-of-Thoughts enabled (complexity=EXPERT + domain=PRODUCT); `hybrid` RAG; OutputContractBuilder → PRD structure
3. `_node_rag_retrieval`: hybrid retrieval surfaces 5 related PRDs + relevant user research + technical architecture notes
4. `_node_tree_of_thoughts`: generates 3 framings — (A) user-journey framing starting from pain point, (B) API-contract framing starting from integration points, (C) metrics-first framing starting from success KPIs; evaluates each on completeness (0–1), feasibility (0–1), alignment with strategy (0–1)
5. Highest-scoring framing (often A or C) expanded into full PRD
6. User stories generated following "As a [user], I want [action], so that [value]" format; 10–20 stories per feature
7. Acceptance criteria generated per story with testable conditions
8. `confluence_server.py:create_page` publishes PRD; `jira_server.py:create_issue` creates parent epic with story children

**Observability:** Tree-of-Thoughts SSE with 3 branch scores; `GOAL_DURATION` target < 4min; cost ~$0.20/PRD; OutputContract completeness score; `eval_score_recorded`

**Eval path:** `goal_success` (Confluence page created, all mandatory sections present), `grounding` (user stories reference actual user needs from research), PRD completeness score

**Expected output:** Confluence PRD with all standard sections (12–15 pages equivalent), 15–20 user stories with acceptance criteria, linked Jira epic with child stories created

**Failure modes:** Feature brief too vague → Tree-of-Thoughts all branches score < 0.5 → agent requests clarifying questions from PM; conflicting requirements in retrieved PRDs → surfaces conflict explicitly in Technical Risks section; Jira project key not found → PRD created but epic creation skipped with instruction

**Code references:** `app/agent/patterns/tree_of_thoughts.py`, `app/rag/engine.py:retrieve_hybrid`, `app/mcp/servers/confluence_server.py`, `app/mcp/servers/jira_server.py`, `app/context/output_contract_builder.py`

---

## Use Case 47: Score and Prioritize Backlog

**Goal:** Analyze all open Jira epics and stories in a product backlog, score each against RICE framework (Reach, Impact, Confidence, Effort), and produce a prioritized backlog order with justification.

**Business problem:** Backlog prioritization is subjective and time-consuming; systematic RICE scoring with data-backed inputs creates transparent, data-driven prioritization that stakeholders can interrogate.

**Actors:** Product manager, AgentVerse agent, Jira MCP server, Confluence MCP server

**Inputs:** Jira project backlog (all unstarted epics/stories), product strategy OKRs (Confluence), customer impact data (analytics export), engineering estimates (Jira story points), market sizing data

**Agent pattern:** Self-Consistency — runs RICE scoring 3 times with slight context variations; takes majority vote on score tier (High/Medium/Low) per dimension to reduce LLM scoring variance

**RAG pattern:** `self_rag` — agent self-evaluates whether retrieved market sizing and customer analytics data is relevant and recent enough before using it in RICE scores; regenerates retrieval for stale data

**Memory used:** Execution memory (last 3 sprint prioritization decisions for consistency), Procedural memory (RICE formula: Reach × Impact × Confidence / Effort), Episodic memory (items that were over/under-prioritized and why)

**Ingestion path:** Jira backlog → `jira_server.py:search_issues` → `SemanticChunker`; OKRs → `HeadingChunker`; analytics data → `row_group` chunker (by feature area)

**Retrieval path:** Epic/story title + description → `self_rag`: retrieves OKR alignment data + customer analytics → self-evaluates staleness and relevance → confirmed data fed to RICE scorer

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: Jira read requires `jira:read`; Confluence read requires `confluence:read`; no Jira writes without explicit confirmation
- OutputContractBuilder: "prioritize backlog" → structured spreadsheet-compatible JSON output with columns: Epic/Story | RICE Score | Reach | Impact | Confidence | Effort | Justification | OKR Alignment
- Determinism: Self-Consistency with N=3 reduces variance; final score requires 2/3 agreement on tier

**End-to-end flow:**
1. PM submits `POST /goals` with Jira project key and OKR document link; queues to `goals.professional`
2. `_node_initialize`: Self-Consistency pattern (N=3); `self_rag` strategy; OutputContractBuilder → RICE table contract
3. `_node_rag_retrieval`: `jira_server.py:search_issues` fetches all 40–100 backlog items; `self_rag` retrieves OKR targets and customer analytics; self-evaluates: analytics data from last quarter → CONFIRMED; data older than 6 months → STALE → requests PM to provide updated data or uses with confidence penalty
4. `_node_plan`: groups items by type (epic vs story) and product area for parallel scoring
5. `_node_execute (×3 with Self-Consistency)`: for each item — score Reach (0–1000 users impacted), Impact (0.25/0.5/1/2/3), Confidence (percentage), Effort (person-weeks); RICE = Reach × Impact × Confidence / Effort; 3 independent scoring runs
6. Majority vote on score tier (within 20% of median) → final RICE score per item
7. Ranks backlog by RICE score descending; groups into tiers: P1 (top 20%), P2 (middle 50%), P3 (bottom 30%)
8. Publishes ranked backlog to Confluence; optionally updates Jira priority field if PM confirms

**Observability:** Self-Consistency vote distribution per item (how often 3 runs agreed); `GOAL_DURATION` target < 5min for 50 items; cost ~$0.08; self-rag staleness detection events

**Eval path:** `goal_success` (all items scored, backlog published), `grounding` (RICE scores justified with data), scoring consistency rate (% items where all 3 runs agreed)

**Expected output:** Ranked backlog table (all items with RICE scores, tier assignment, OKR alignment, and 1–2 sentence justification per item), Confluence prioritization page

**Failure modes:** Backlog has 200+ items → batched scoring in groups of 50 with intermediate results; story lacks description → scored with confidence=LOW and flag for PM to enrich; OKR document not found → RICE scored without OKR alignment dimension with note; Self-Consistency fails (all 3 runs disagree > 30% on same item) → flagged for PM manual scoring

**Code references:** `app/mcp/servers/jira_server.py`, `app/rag/agentic/patterns/self_rag.py`, `app/agent/patterns/self_consistency.py`, `app/ingestion/chunkers/row_group_chunker.py`, `app/context/output_contract_builder.py`

---

## Use Case 48: Create Competitive Analysis Matrix

**Goal:** Research 5–8 competitors, extract their features, pricing, positioning, and recent changes, and produce a structured competitive analysis matrix comparing them against the company's product.

**Business problem:** Competitive landscapes change monthly; manual competitive analysis takes weeks; automated, structured research enables quarterly cadence with consistent methodology.

**Actors:** Product manager, strategy team, AgentVerse agent

**Inputs:** Competitor list (names + URLs), comparison dimensions list (features, pricing, integrations, target market, differentiators), internal product feature list (Confluence), prior competitive analysis (Confluence)

**Agent pattern:** Peer Review — (1) research and data collection pass per competitor, (2) cross-competitor comparison and positioning analysis pass with independent verification

**RAG pattern:** `flare` — uncertainty-driven: initial competitor feature extraction may be uncertain (information not always clearly stated on public pages); FLARE triggers targeted web retrieval or additional RPA scraping when confidence < 0.55

**Memory used:** Execution memory (prior quarter competitor analysis for delta tracking), Episodic memory (known competitor update patterns), Long-term memory (competitor strategic evolution over multiple quarters)

**Ingestion path:** Competitor websites → `RPAExecutor` + `VisionParser` (screenshots for pricing/features pages) → `SemanticChunker`; Confluence product feature list → `HeadingChunker`; prior analysis → `SemanticChunker`

**Retrieval path:** Competitor + dimension → `flare`: initial retrieval from scraped content → confidence check per data point → uncertain data points → targeted additional scraping or web search → combined context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: web scraping tools require `web:read` scope; no data from paywalled sources
- OutputContractBuilder: "competitive analysis" → structured matrix: rows=competitors, columns=dimensions, cells=value+confidence+source
- Source attribution: every competitive data point includes URL and access date
- Confidence labeling: all cells labeled CONFIRMED / INFERRED / ESTIMATED — no silent assumptions

**End-to-end flow:**
1. PM submits `POST /goals` with competitor list and dimension list; queues to `goals.professional`
2. `_node_initialize`: `flare` strategy; Peer Review pattern; OutputContractBuilder → matrix contract with confidence labels
3. `_node_rag_retrieval`: FLARE initial retrieval from prior analysis KB; fetches internal product feature list from Confluence
4. `_node_plan`: 1 sub-goal per competitor; 7 dimensions per competitor = N data points to gather
5. `_node_execute (Pass 1 — Research)`: per competitor — `RPAExecutor` scrapes pricing page, features page, docs; `VisionParser` processes screenshots for pricing tables; FLARE uncertainty check per data point → additional targeted scraping for uncertain dimensions
6. `_node_peer_review (Pass 2 — Comparison)`: independent reviewer reads all competitor profiles → identifies positioning differentiators; validates internal product comparison claims against Confluence product feature list; flags any claims requiring verification
7. Assembles matrix with confidence ratings; computes competitive gap analysis (features competitors have that internal product lacks and vice versa)
8. Publishes to Confluence with heat map visualization; Jira tasks for flagged gaps

**Observability:** FLARE retrieval trigger count per competitor; `peer_review_score`; `GOAL_DURATION` target < 15min; cost ~$0.30 for 8 competitors; data freshness score per competitor

**Eval path:** `grounding` (claims cite specific source URLs), `goal_success` (all competitors × dimensions populated), data freshness (all sources accessed within 24h)

**Expected output:** Competitive analysis matrix (8 competitors × 10 dimensions) with confidence labels, source URLs, competitive gap summary, and 3 strategic recommendations

**Failure modes:** Competitor uses JS-rendered pricing page → `RPAExecutor` Playwright renders page before scraping; competitor uses anti-bot measures → fallback to manual research flag for analyst; matrix dimension not publicly available → ESTIMATED label with "best guess" note; scraping returns outdated cache → VisionParser screenshot timestamp check

**Code references:** `app/rpa/executor.py`, `app/perception/vision_parser.py`, `app/rag/agentic/patterns/flare.py`, `app/agent/patterns/peer_review.py`, `app/mcp/servers/confluence_server.py`

---

## Use Case 49: Estimate Story Points from Jira History

**Goal:** Analyze a new Jira user story and estimate its story points by finding historically similar completed stories in the project's history, comparing complexity signals, and producing a calibrated estimate with confidence range.

**Business problem:** Story point estimation sessions are time-consuming and prone to anchoring bias; data-driven estimation from historical velocity provides objective baselines for planning.

**Actors:** Engineering team lead, AgentVerse agent, Jira MCP server

**Inputs:** New Jira story (title, description, acceptance criteria), project's Jira history (completed stories with actual story points), team velocity data

**Agent pattern:** ReAct — iterative: search for similar stories → retrieve comparable stories → analyze complexity signals → estimate → verify against velocity

**RAG pattern:** `corrective_rag` — retrieves historically similar stories from Jira KB; self-corrects if retrieved stories are not actually comparable (different component, different team, different era); regenerates with narrower similarity criteria

**Memory used:** Execution memory (team velocity per sprint for calibration), Procedural memory (learned estimation signals: external API dependency → +2 points, database migration → +3 points, UI change → +1 point), Long-term memory (estimation accuracy vs actual points for continuous calibration)

**Ingestion path:** Jira completed stories → `jira_server.py:search_issues` (JQL: project AND status=Done AND resolved >= -90d) → `SemanticChunker` → `text-embedding-3-small`; team velocity → structured sprint report data

**Retrieval path:** New story description → `corrective_rag`: (1) vector similarity search for top-10 most similar completed stories, (2) self-evaluate: are retrieved stories from the same component/team? → correct filter if needed, (3) confirmed analogues passed to estimator

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `jira.search_issues` requires `jira:read` scope; no story edits without PM confirmation
- OutputContractBuilder: "estimate" → structured output with point recommendation + confidence interval + top 3 analogues cited
- Calibration bound: estimate must fall within ±50% of median similar-story actuals or flag as outlier

**End-to-end flow:**
1. Team lead submits new story URL via `POST /goals`; queues to `goals.starter`
2. `_node_initialize`: `corrective_rag` strategy; `gpt-4o-mini` for cost efficiency; OutputContractBuilder → estimate structure
3. `_node_rag_retrieval`: `jira_server.py:search_issues` fetches last 90 days of completed stories → embed and index; `corrective_rag` retrieves top-10 similar stories → self-evaluates: 8 confirmed analogues, 2 rejected (different team)
4. `_node_execute`: for each analogue — extract complexity signals (external dependency, new service, DB change, UI work, test coverage requirement); compare to new story signals
5. Estimation: median of 8 analogues = 5 points; adjust for complexity delta: new story has 1 additional external dependency → +1.5 → estimate 6–8 points with P50 = 7
6. Velocity calibration check: 7 points = 14% of sprint capacity (team velocity 50/sprint) → feasible
7. `_node_verify`: estimate within ±50% of analogue median → PASS; generates explanation citing top 3 analogues with their actual points
8. Optionally updates Jira story estimate field if confirmed by team lead

**Observability:** `corrective_rag` correction events; analogue retrieval precision (% confirmed vs rejected); `GOAL_DURATION` target < 60s; cost ~$0.005/estimation; estimation accuracy tracked in execution memory

**Eval path:** `grounding` (estimate cites actual analogous stories with Jira IDs), `goal_success` (estimate with confidence range produced), calibration accuracy over time

**Expected output:** Point estimate (range + median) with: top 3 analogous completed stories (Jira IDs + actual points), complexity signal comparison, velocity feasibility check, confidence level (HIGH/MEDIUM/LOW)

**Failure modes:** No similar stories in last 90 days → expands to 180 days or related project; all analogues have high variance (1–13 points for similar stories) → estimate flagged as "high uncertainty, recommend planning poker"; story contains novel technology with no history → complexity signals used but confidence=LOW with explicit note

**Code references:** `app/mcp/servers/jira_server.py`, `app/rag/agentic/patterns/corrective_rag.py`, `app/ingestion/chunkers/semantic_chunker.py`, `app/memory/execution_memory.py`, `app/context/output_contract_builder.py`

---

## Use Case 50: Generate Sprint Status Report from Jira

**Goal:** Automatically generate a sprint status report from Jira data including story completion rates, blockers, velocity trends, and risk flags, formatted for both engineering team and executive audience.

**Business problem:** Sprint ceremonies consume 2+ hours per sprint on status gathering; automated reporting from live Jira data enables real-time visibility and frees scrum masters for facilitation.

**Actors:** Scrum master, engineering manager, AgentVerse agent, Jira MCP server

**Inputs:** Active Jira sprint (sprint ID), team velocity history, stakeholder list (engineering vs executive audience), sprint goals from sprint description

**Agent pattern:** Plan-Execute — structured phases: (1) gather sprint data from Jira, (2) analyze velocity and blockers, (3) generate dual-audience report sections, (4) publish

**RAG pattern:** `adaptive` — dynamically selects between lexical (for JQL query construction and exact story lookups) and semantic (for blocker pattern analysis from historical sprints); strategy chosen per sub-task

**Memory used:** Execution memory (last 5 sprint reports for velocity trend), Procedural memory (learned report structure: Headline metrics → Status by component → Blockers → Predictions), Episodic memory (recurring blocker patterns for this team)

**Ingestion path:** Jira sprint data → `jira_server.py:get_sprint` → structured JSON; historical sprint velocity → `jira_server.py:search_issues` (JQL: resolved by sprint) → `row_group` chunker by sprint; blocker patterns → `SemanticChunker`

**Retrieval path:** Current sprint data → `adaptive`: (1) lexical for exact JQL story lookups (ID, status, assignee), (2) semantic for blocker similarity to historical patterns → combined context

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `jira.get_sprint` and `jira.search_issues` require `jira:read` scope
- OutputContractBuilder: "sprint report" → dual-audience format: executive summary (5 bullets max) + detailed engineering view (full table)
- Accuracy guard: story counts must match raw Jira API data exactly (no LLM hallucination on numbers)

**End-to-end flow:**
1. NLScheduler fires every Friday at 4pm via sprint report trigger; `POST /goals` queued to `goals.starter`
2. `_node_initialize`: `adaptive` RAG; Plan-Execute pattern; OutputContractBuilder → dual-audience report contract
3. `_node_rag_retrieval`: `jira_server.py:get_sprint` fetches all stories in active sprint; `jira_server.py:search_issues` for last 5 sprint velocity data; `adaptive` selects lexical for count queries, semantic for blocker pattern matching
4. `_node_plan`: phases: (a) compute metrics, (b) identify blockers, (c) velocity prediction, (d) dual report generation
5. `_node_execute (metrics)`: compute: stories done/in-progress/not-started, story points completed vs committed, percentage complete, stories added mid-sprint (scope creep), stories removed
6. `_node_execute (blockers)`: filter stories with `status=Blocked`; extract blocker descriptions; match to historical patterns; identify owner
7. `_node_execute (prediction)`: current velocity vs historical average → predict sprint completion probability (%)
8. Dual report generated: Executive (4 bullets: completion %, blockers count, prediction, one risk flag) + Engineering (full table per story with status, assignee, blocked reason, completion date estimate)
9. Published to Confluence sprint page and Slack `#sprint-status`

**Observability:** `GOAL_DURATION` target < 90s; cost ~$0.01/report; `tool_success_rate` for Jira API; story count accuracy verified against API response

**Eval path:** `goal_success` (report published to Confluence and Slack), numerical accuracy (story counts match Jira exactly), `tool_success_rate`

**Expected output:** Sprint status report: executive summary (4–5 bullets), full story table, velocity chart data, blocker list with owners, sprint completion prediction (%), and recommended actions

**Failure modes:** Active sprint not found → last sprint used with note; Jira API returns partial data → report published with "data incomplete" banner; mid-sprint goal change → delta from original goals flagged; team member leaves mid-sprint → unassigned stories flagged for triage

**Code references:** `app/mcp/servers/jira_server.py`, `app/rag/engine.py:retrieve_adaptive`, `app/ingestion/chunkers/row_group_chunker.py`, `app/triggers/nl_scheduler.py`, `app/context/output_contract_builder.py`
