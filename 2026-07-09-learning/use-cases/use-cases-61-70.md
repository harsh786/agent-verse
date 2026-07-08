# Use Cases 61–70: Banking · E-commerce · Education · Research

> **Domains covered:** Banking cont. (61–62) · E-commerce (63–65) · Education (66–68) · Research (69–70)
> All use cases follow the AgentVerse canonical field set and cite real `app/` source files.

---

## UC61 — Validate KYC Identity Documents

| Field | Value |
|-------|-------|
| **ID** | UC61 |
| **Title** | Validate KYC Identity Documents |
| **Domain** | Banking |
| **Vertical / Industry** | Retail Banking / FinTech |
| **Trigger** | Customer submits identity document images during onboarding; compliance system initiates KYC validation job |
| **Actor / Persona** | KYC Compliance Officer, Onboarding System |
| **LLM** | gpt-4o (vision-enabled) |
| **RAG Pattern** | `corrective_rag` — validates extracted ID fields against KYC ruleset; corrects low-confidence extractions |
| **Agent Pattern** | Strict PII Detection + HITL on fraud indicators |
| **Compliance Bundle** | REGULATED + strict PII Detection + Full Audit Trail |
| **HITL Gate** | Fraud indicators (altered document, expired ID, name mismatch with application) escalated to KYC Compliance Officer |
| **Memory** | None — stateless per document, full audit trail written externally |

### Problem Statement

KYC document validation is a regulated anti-money-laundering (AML) control. Government-issued IDs must be validated for authenticity, expiry, and consistency with the customer's application data. Errors here are a regulatory breach — not just a UX issue.

### Inputs

- Government-issued ID images (passport, national ID, driver's licence — JPEG/PNG/PDF)
- Customer application data (name, DOB, address) from onboarding form
- KYC validation rules (internal policy — Postgres table or PDF)

### Expected Output

- KYC validation result: Pass / Fail / Manual Review Required
- Extracted ID fields (name, DOB, ID number, expiry date, nationality, issuing country)
- Fraud indicator flags (if any): document alteration, expiry, data mismatch
- Full audit trail entry (immutable)

### MCP Servers / External Tools

- `filesystem_server.py` — ID image staging
- `postgres_server.py` — KYC rule lookup, customer application data fetch, audit trail write

### Ingestion Pipeline

- `app/ingestion/parsers/vision_parser.py` — GPT-4o vision extracts all ID document fields: MRZ (Machine Readable Zone) lines, printed fields, photo region, security feature indicators; returns a region-annotated extraction map with per-field confidence scores

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after initial vision extraction, each ID field is validated against the KYC ruleset (format rules, expiry date check, country code validity). Fields that fail format validation or have confidence < 0.85 trigger a corrective re-extraction pass with a region-focused prompt. After two passes, fields still failing are escalated.

### Agent Pattern

Strict PII handling enforced by `app/guardrails_v2/engine.py` at every step. Inline HITL gating via `app/governance/hitl.py` for fraud indicator conditions: document expiry detected, name/DOB mismatch with application data, MRZ inconsistency, signs of document alteration.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — strict PII detection (name, DOB, ID number, address redacted in all logs); REGULATED bundle enforces full data handling per AML/KYC regulations; output schema validation
- `app/governance/audit.py` — append-only audit trail: every extraction attempt, confidence score, corrective pass, and HITL decision written with timestamp and agent ID

### End-to-End Steps

1. **Ingest**: `vision_parser.py` receives ID images. GPT-4o vision performs region detection: MRZ zone, printed data fields, photo, signature, and security features. Returns initial field-value map with confidence scores.
2. **Corrective Extraction Pass**: `corrective.py` validates each extracted field against KYC format rules (e.g. MRZ checksum, date format, country code list). Fields below confidence threshold or failing format checks trigger a region-focused re-extraction.
3. **Cross-Reference Validation**: Agent compares extracted ID data (name, DOB) against customer application data from Postgres. Mismatches are flagged as fraud indicators.
4. **Fraud Indicator Analysis**: Agent evaluates additional fraud signals: document expiry status, MRZ-vs-VIZ consistency, digital alteration artifacts (checked via vision model output confidence patterns).
5. **PII Redaction**: `engine.py` applies strict PII redaction — ID number, DOB, and full name are hashed in all telemetry and log records. Only anonymised results flow to downstream audit records.
6. **HITL Gate**: Any detected fraud indicator routes the case via `hitl.py` to the KYC Compliance Officer queue. Officer reviews the original image alongside extracted data and makes a Pass/Fail decision with a reason code.
7. **Audit Trail & Output**: Every step written to the append-only audit trail via `audit.py`. Final KYC result (Pass/Fail/Manual Review) returned with extracted fields, fraud flags, and audit trail reference ID.

### Real App/ Files Cited

- `app/ingestion/parsers/vision_parser.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/governance/hitl.py`
- `app/governance/audit.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Field-level extraction accuracy ≥ 97% on a gold-standard ID document test set
- MRZ checksum validation passes for all valid test documents
- Fraud indicator detection precision ≥ 95% on a synthetic altered-document test set
- HITL triggered for 100% of flagged fraud indicators
- PII (ID number, DOB) absent from all log and telemetry records (compliance audit check)
- Full audit trail written for 100% of validation events

---

## UC62 — Generate SAR from Transaction Logs

| Field | Value |
|-------|-------|
| **ID** | UC62 |
| **Title** | Generate Suspicious Activity Report (SAR) from Transaction Logs |
| **Domain** | Banking |
| **Vertical / Industry** | Retail / Commercial Banking — AML Compliance |
| **Trigger** | AML monitoring system flags a customer account; compliance analyst initiates SAR generation workflow |
| **Actor / Persona** | AML Compliance Analyst, Compliance Officer, MLRO |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `raptor` — hierarchical analysis of large transaction log corpora to surface suspicious patterns |
| **Agent Pattern** | Peer Review |
| **Compliance Bundle** | REGULATED + Full Audit Trail + PII Detection |
| **HITL Gate** | Final SAR narrative and submission require MLRO (Money Laundering Reporting Officer) approval |
| **Memory** | Episodic — scoped to investigation case ID |

### Problem Statement

Suspicious Activity Reports require precise narrative construction from large volumes of transaction data, counterparty relationships, and regulatory typology references. The narrative must be factually accurate, legally defensible, and formatted to FinCEN/FCA SAR specifications. A single-pass LLM output is insufficient for a document that may be reviewed by law enforcement.

### Inputs

- Transaction logs for the flagged account (Postgres — transaction history, amounts, counterparties, timestamps)
- Aggregated transaction analytics (BigQuery — velocity metrics, layering indicators, geographic anomalies)
- AML typology reference library (internal PDF or Confluence KB)
- Customer profile and KYC data (Postgres)

### Expected Output

- Draft SAR narrative: subject information · suspicious activity description · transaction summary · typology classification · supporting evidence
- Formatted SAR document (regulatory template — FinCEN/FCA format)
- Evidence packet: transaction extracts, timeline, counterparty graph

### MCP Servers / External Tools

- `postgres_server.py` — transaction history, customer profile, KYC data
- `bigquery_server.py` — aggregated AML analytics, velocity metrics, geographic heatmaps

### Ingestion Pipeline

- Structured data from Postgres + BigQuery (no PDF parsing required for transaction data)
- `app/rag/agentic/patterns/raptor.py` — applied to the AML typology reference library (PDF/KB) to build a cluster tree of suspicious activity patterns

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR builds a hierarchy over the AML typology library: document summaries → typology categories (structuring, layering, smurfing, trade-based ML) → specific indicator patterns. The agent queries at the typology category level to classify the detected activity, then retrieves specific indicator patterns as evidence.

### Agent Pattern

`app/agent/patterns/peer_review.py` — primary agent drafts the SAR narrative. Reviewer agent (with an AML compliance checklist prompt) validates: (a) all required SAR fields populated, (b) transaction evidence accurately cited, (c) typology classification supported by the typology library, (d) narrative is factual (no speculation beyond evidence), (e) regulatory format compliance.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — strict PII detection (subject name, account numbers, SSN/TIN redacted in internal logs, present only in the restricted SAR document); REGULATED bundle; output schema validation for SAR regulatory template
- `app/governance/audit.py` — append-only audit trail of every data retrieval, agent draft, peer review finding, and HITL decision

### End-to-End Steps

1. **Data Retrieval**: `postgres_server.py` retrieves the flagged account's full transaction history, counterparty data, and KYC profile. `bigquery_server.py` pulls aggregated AML analytics: velocity scores, geographic anomaly flags, structuring indicators.
2. **RAPTOR Typology Indexing**: `raptor.py` builds a cluster tree over the AML typology reference library. Cluster nodes represent typology families (structuring, layering, trade-based ML, etc.).
3. **Suspicious Activity Classification**: Agent queries RAPTOR typology tree to classify the flagged activity pattern. Retrieves specific indicator patterns as evidence. Episodic memory stores the developing case context.
4. **SAR Narrative Draft**: gpt-5.2 generates the SAR narrative: subject information, activity description, transaction timeline, typology classification, supporting evidence citations. Follows regulatory SAR template structure.
5. **Peer Review**: `peer_review.py` reviewer agent audits the draft: verifies all required SAR fields are populated, checks that every factual claim is traceable to retrieved evidence, validates typology classification against the typology library, confirms no speculative language.
6. **HITL Gate — MLRO Approval**: The reviewed draft SAR is routed via `hitl.py` to the MLRO (Money Laundering Reporting Officer). MLRO reviews the narrative and evidence packet, makes any edits, and either approves for submission or returns for revision with notes.
7. **Audit Trail & Output**: Full audit trail written via `audit.py` (every retrieval, draft, peer review finding, MLRO decision). Final approved SAR document + evidence packet returned for regulatory submission.

### Real App/ Files Cited

- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/peer_review.py`
- `app/governance/hitl.py`
- `app/governance/audit.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All required SAR fields populated in 100% of generated reports
- Peer review catches ≥ 95% of deliberately injected factual errors in test cases
- MLRO HITL approval required before any SAR is marked as ready for submission (zero auto-submissions)
- Typology classification accuracy ≥ 90% on a labelled historical SAR test set
- Full audit trail written for 100% of SAR generation events

---

## UC63 — Generate Product Review Response Strategy

| Field | Value |
|-------|-------|
| **ID** | UC63 |
| **Title** | Generate Product Review Response Strategy |
| **Domain** | E-commerce |
| **Vertical / Industry** | Consumer Retail |
| **Trigger** | Weekly job or manual trigger by the CX team lead when review volume or sentiment threshold is crossed |
| **Actor / Persona** | Customer Experience Lead, Brand Manager, Product Manager |
| **LLM** | gpt-4o |
| **RAG Pattern** | `raptor` — hierarchical theme clustering across large review corpora |
| **Agent Pattern** | Supervisor with parallel analysis channels |
| **Compliance Bundle** | Standard |
| **HITL Gate** | Brand-damaging or legally sensitive response drafts reviewed by Brand Manager before publishing |
| **Memory** | Episodic — scoped to product ID + review period |

### Problem Statement

Consumer products accumulate hundreds of reviews per week across Zendesk, App Store, Google Play, and third-party retailers. Manually reading and forming a coherent response strategy is impractical. The CX team needs thematic analysis, competitor context, and ready-to-use response templates — surfaced automatically.

### Inputs

- Customer reviews from Zendesk (via Zendesk MCP)
- App Store / Google Play reviews (via scraping or API)
- Product specification and known issues list (Confluence or internal Notion)
- Competitor review data (optional, scraped)

### Expected Output

- Thematic review analysis: top complaint themes, top praise themes, trend over time
- Competitor comparison summary (if competitor data provided)
- Response template set: one template per major complaint theme
- Escalation list: reviews requiring immediate individual response (legal threats, safety reports)

### MCP Servers / External Tools

- `zendesk_server.py` — fetch customer support tickets and review data
- Web scraping connector — App Store / Google Play review ingestion
- `confluence_server.py` — product KB and known issues reference

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR builds a cluster tree over all ingested reviews: document level (overall sentiment summary) → theme nodes (complaints: battery life, connectivity, UI bugs; praise: design, speed, support) → leaf sentences (specific review excerpts). The Supervisor agent queries at theme level for strategy decisions, and at leaf level for verbatim evidence in response templates.

### Agent Pattern

`app/agent/patterns/supervisor.py` — Supervisor orchestrates three parallel analysis sub-agents:
- **Agent A (Sentiment & Theme Analysis)**: queries RAPTOR theme nodes, classifies top complaint and praise themes, tracks sentiment trend.
- **Agent B (Competitor Benchmarking)**: compares complaint themes against competitor review clusters, identifies relative strengths/weaknesses.
- **Agent C (Response Template Generation)**: for each major complaint theme, drafts a response template using RAPTOR leaf excerpts as context.
Supervisor aggregates outputs and identifies escalation-worthy reviews.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required sections present); brand voice tone check (configurable keywords); flags response drafts containing legal admissions or safety acknowledgements for HITL

### End-to-End Steps

1. **Ingest**: `zendesk_server.py` fetches reviews and support tickets for the target product and time window. Web scraping connector retrieves App Store / Google Play reviews. `confluence_server.py` fetches known issues and product KB.
2. **RAPTOR Indexing**: `raptor.py` clusters all reviews into a theme hierarchy: overall sentiment → theme categories → individual review excerpts.
3. **Supervisor Dispatch**: `supervisor.py` spawns three parallel sub-agents (Sentiment/Theme, Competitor Benchmarking, Response Template).
4. **Parallel Analysis**: Each sub-agent executes its analysis track against the RAPTOR index and competitor data. All three run concurrently.
5. **Aggregation**: Supervisor collects sub-agent outputs. Cross-references complaint themes with known issues from Confluence KB. Identifies reviews with legal/safety language for escalation.
6. **HITL Gate**: Response drafts containing brand-risk language (legal admissions, safety acknowledgements) are routed via `hitl.py` for Brand Manager review and approval.
7. **Output**: `engine.py` validates schema. Final strategy document (thematic analysis + competitor summary + response templates + escalation list) returned. Episodic memory stores product review context for the next weekly cycle.

### Real App/ Files Cited

- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/supervisor.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Top 5 complaint themes match manual thematic coding (agreement ≥ 85%)
- Response templates rated "on-brand" by CX team in A/B evaluation ≥ 80% of the time
- All reviews with legal/safety language identified and routed to HITL
- Parallel agent execution completes within 60 s for a 500-review batch

---

## UC64 — Create Product Descriptions from Spec Sheets

| Field | Value |
|-------|-------|
| **ID** | UC64 |
| **Title** | Create Product Descriptions from Spec Sheets |
| **Domain** | E-commerce |
| **Vertical / Industry** | Consumer Retail / Manufacturing |
| **Trigger** | Product manager uploads a batch of spec sheet PDFs/DOCX files and requests SEO-ready product descriptions |
| **Actor / Persona** | Product Manager, Content Writer, SEO Specialist |
| **LLM** | gpt-4o |
| **RAG Pattern** | `corrective_rag` — validates that every spec claim in the description is traceable to the source document |
| **Agent Pattern** | Self-Refine (iterative critique + revision loop) |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None for standard descriptions; optional manual review flag per SKU |
| **Memory** | None — stateless per SKU |

### Problem Statement

Manufacturing spec sheets contain technical data (dimensions, materials, certifications, performance metrics) in an inconsistent format. Product descriptions for e-commerce must be accurate, SEO-optimised, and in brand voice — a task that is currently manual and takes 20–30 minutes per SKU.

### Inputs

- Product spec sheet (PDF or DOCX, 1–10 pages per SKU)
- Brand voice guide (PDF or text)
- Target keywords list (CSV, per SKU) — optional
- Competitor description examples — optional

### Expected Output

- Product description (long form, 200–400 words): headline, key features, technical specs table, call to action
- Short description variant (50–80 words) for listing pages
- SEO metadata: title tag, meta description, structured data fields

### MCP Servers / External Tools

- `filesystem_server.py` — batch spec sheet staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses spec sheet PDF/DOCX, extracting text, tables, and technical specifications
- `app/ingestion/chunkers/semantic.py` — semantic chunking groups related spec features (dimensions, materials, performance, certifications) into cohesive chunks for accurate attribute-level retrieval

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after the Self-Refine agent generates a description draft, a corrective validation pass retrieves the source spec chunk for every claim in the description and verifies it is supported. Any unsupported or inaccurate claim is flagged as a correction target for the next Self-Refine iteration.

### Agent Pattern

`app/agent/patterns/self_refine.py` — the agent generates an initial description draft, then enters a critique-revise loop (max 3 iterations):
1. **Critique**: Evaluate the draft against criteria (accuracy, completeness, brand voice, SEO keyword inclusion, readability score).
2. **Revise**: Rewrite sections that fail the critique criteria.
3. **Corrective validation** (via `corrective.py`) runs after each revision to ensure spec accuracy.
Loop terminates when all criteria pass or after 3 iterations.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required description sections, word count within bounds, no prohibited claims like unverified certifications)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses each spec sheet. `semantic.py` creates semantically coherent chunks grouping related attributes (dimensions, materials, performance, safety certifications).
2. **Initial Description Draft**: Agent retrieves all spec chunks and generates an initial product description following the brand voice guide and target keyword list.
3. **Self-Refine — Critique Pass**: `self_refine.py` critique step evaluates the draft: spec accuracy, completeness (all key features mentioned), brand voice adherence, keyword density, readability (Flesch score).
4. **Corrective Validation**: `corrective.py` verifies each claim in the draft against its source spec chunk. Unsupported claims are tagged for revision.
5. **Self-Refine — Revision Pass**: Agent revises sections failing critique or corrective validation. Up to 3 total iterations.
6. **SEO Metadata Generation**: After description is finalised, agent generates title tag (≤ 60 chars), meta description (≤ 155 chars), and structured data fields.
7. **Output**: `engine.py` validates schema. Long description, short variant, and SEO metadata returned per SKU.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/semantic.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/agent/patterns/self_refine.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Every technical spec claim traceable to source document (corrective validation passes for ≥ 98% of claims)
- SEO keyword inclusion rate ≥ 90% for provided target keywords
- Self-Refine reduces critique failures by ≥ 60% from iteration 1 to final
- Output word count within specified range for 100% of SKUs
- Batch processing time < 30 s per SKU

---

## UC65 — Identify Inventory Shrinkage Anomalies

| Field | Value |
|-------|-------|
| **ID** | UC65 |
| **Title** | Identify Inventory Shrinkage Anomalies |
| **Domain** | E-commerce |
| **Vertical / Industry** | Consumer Retail / Warehouse Operations |
| **Trigger** | Daily scheduled job or manual trigger by the loss prevention team |
| **Actor / Persona** | Loss Prevention Analyst, Warehouse Operations Manager |
| **LLM** | gpt-4o |
| **RAG Pattern** | `self_rag` (adaptive query generation) + `corrective_rag` (finding validation) |
| **Agent Pattern** | Adaptive querying with HITL for high-value events |
| **Compliance Bundle** | Standard |
| **HITL Gate** | High-value shrinkage events (above configured threshold) escalated to Loss Prevention Manager |
| **Memory** | Episodic — scoped to investigation time window |

### Problem Statement

Inventory shrinkage — theft, damage, administrative error, supplier fraud — is hidden in the gap between system inventory and physical counts across multiple locations. Anomaly patterns (sudden SKU drops, location-specific irregularities, time-correlated spikes) must be detected and investigated before write-off cycles crystallise the loss.

### Inputs

- Inventory transaction log (BigQuery — stock movements, adjustments, write-offs)
- Physical inventory count data (Postgres — periodic count records)
- Supplier delivery records (Postgres)
- Historical shrinkage baseline (BigQuery)

### Expected Output

- Anomaly report: anomaly type · SKU · location · time window · estimated value · confidence score
- Root cause hypotheses per anomaly (theft / damage / admin error / supplier discrepancy)
- High-value event list for HITL escalation

### MCP Servers / External Tools

- `bigquery_server.py` — inventory transaction logs, historical baseline, aggregated metrics
- `postgres_server.py` — physical count records, supplier delivery data

### RAG Pattern

`app/rag/agentic/patterns/self_rag.py` — Self-RAG enables adaptive query generation: the agent dynamically generates its own retrieval queries based on what it finds (e.g. discovering a SKU anomaly triggers a follow-up query for that SKU's supplier delivery records). This iterative pattern is essential for shrinkage investigation where the anomaly signature is not known in advance.

`app/rag/agentic/patterns/corrective.py` — after Self-RAG surfaces a candidate anomaly, corrective RAG validates the finding against the historical baseline. Low-confidence anomalies (could be seasonal variation) trigger a refined validation query before being included in the report.

### Agent Pattern

Adaptive querying driven by Self-RAG's iterative retrieval loop. Inline HITL via `app/governance/hitl.py` for anomalies with estimated value above configured threshold.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (all anomaly fields required, value estimates within plausible range); no PII in output (operational data only)

### End-to-End Steps

1. **Baseline Fetch**: `bigquery_server.py` retrieves the historical shrinkage baseline (average shrinkage rates by SKU category, location, and time period). `postgres_server.py` fetches the most recent physical inventory counts.
2. **Variance Computation**: Agent computes system-vs-physical inventory variance per SKU per location for the investigation window. BigQuery analytics provide velocity metrics and geographic anomaly flags.
3. **Self-RAG Adaptive Investigation**: `self_rag.py` enters an adaptive query loop: (1) initial query identifies SKUs with variance above threshold, (2) follow-up queries investigate those SKUs' supplier delivery records, adjustment logs, and count timing, (3) further hops investigate correlated SKUs or locations. Loop continues until the agent determines it has sufficient evidence.
4. **Corrective Validation**: `corrective.py` validates each surfaced anomaly against the historical baseline. Anomalies explained by seasonal variation or scheduled write-offs are filtered. Remaining anomalies are classified by root cause hypothesis (theft pattern, damage pattern, admin error, supplier discrepancy).
5. **Anomaly Scoring**: Each validated anomaly is scored by confidence × estimated value. High-value, high-confidence anomalies are flagged for HITL escalation.
6. **HITL Gate**: Anomalies above the value threshold are routed via `hitl.py` to the Loss Prevention Manager, who reviews the evidence chain and initiates a physical investigation or escalation.
7. **Output**: `engine.py` validates schema. Final anomaly report (ranked by estimated value) returned. Episodic memory stores investigation context for the next cycle.

### Real App/ Files Cited

- `app/rag/agentic/patterns/self_rag.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Anomaly recall ≥ 90% against manually identified shrinkage events in a test dataset
- False positive rate < 15% (seasonal variation and legitimate write-offs not flagged)
- Self-RAG adaptive loop detects correlated multi-SKU anomalies that single-query approaches miss
- HITL triggered for 100% of high-value events

---

## UC66 — Generate Quiz from Training Material

| Field | Value |
|-------|-------|
| **ID** | UC66 |
| **Title** | Generate Quiz from Training Material |
| **Domain** | Education |
| **Vertical / Industry** | Corporate L&D / EdTech |
| **Trigger** | L&D specialist uploads training material PDF/DOCX and requests a quiz for learner assessment |
| **Actor / Persona** | L&D Specialist, Instructional Designer |
| **LLM** | gpt-4o |
| **RAG Pattern** | Hybrid retrieval — BM25 keyword + vector search, fused via `fusion.py` |
| **Agent Pattern** | Tree-of-Thoughts with two branches: Boundary Value Analysis (BVA) and Equivalence Partitioning (EP) |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None — fully automated; optional manual review flag per module |
| **Memory** | None — stateless per quiz generation job |

### Problem Statement

Manually authoring a diverse, well-calibrated quiz from a 50-page training module takes an instructional designer 2–4 hours. A good quiz requires questions at different cognitive levels (recall, application, analysis) and must avoid trivial or ambiguous questions — exactly the kind of structured reasoning Tree-of-Thoughts excels at.

### Inputs

- Training material PDF or DOCX (1–100 pages)
- Quiz configuration: number of questions, question types (MCQ, T/F, short answer), difficulty distribution, target learning objectives

### Expected Output

- Question bank (JSON + Markdown): questions · answer options · correct answer · explanation · cognitive level · source section reference
- Quiz manifest: coverage map (which source sections each question tests)

### MCP Servers / External Tools

- `filesystem_server.py` — training material staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses training material PDF/DOCX; extracts text, tables, diagrams (alt-text from PDF metadata)
- `app/ingestion/chunkers/heading.py` — heading-based chunking: each section/sub-section becomes a chunk, preserving course structure (Module → Chapter → Section). This ensures quiz questions are mapped to specific learning units.

### RAG Pattern

Hybrid retrieval via `app/rag/agentic/patterns/fusion.py` — BM25 keyword retrieval (exact term matching for technical definitions and numerical values) fused with dense vector retrieval (semantic similarity for conceptual content). The hybrid approach ensures both terminological precision and conceptual breadth in retrieved passages for question generation.

### Agent Pattern

`app/agent/patterns/tree_of_thoughts.py` — two parallel reasoning branches are explored:
- **Branch A — Boundary Value Analysis (BVA)**: generates questions targeting edge cases, threshold values, and boundary conditions in the material (e.g. "What is the minimum required value for X?"). Produces high-difficulty discriminating questions.
- **Branch B — Equivalence Partitioning (EP)**: generates questions covering representative examples from each conceptual category in the material. Produces balanced coverage questions across the learning objectives.
The ToT evaluator scores questions from both branches on quality, uniqueness, and coverage. Best questions from both branches are merged into the final question bank.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required question fields, answer options count for MCQ, correct answer present); quality checks (question length, no trivially true/false questions)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the training material. `heading.py` creates section-structured chunks mapped to learning units (Module → Chapter → Section).
2. **Hybrid Indexing**: Both BM25 and vector indexes built over section chunks via `fusion.py`. BM25 index captures technical terminology; vector index captures conceptual meaning.
3. **ToT Branch A — BVA**: `tree_of_thoughts.py` Branch A queries for numerical thresholds, boundary conditions, and exception cases. Generates edge-case questions with precise answer ranges.
4. **ToT Branch B — EP**: Branch B queries for representative concepts across each learning objective. Generates coverage questions ensuring each module section is tested.
5. **ToT Evaluation**: Evaluator node scores all questions from both branches on quality dimensions (specificity, answerability, no ambiguity, cognitive level, source traceability). Top-N questions selected based on target quiz configuration.
6. **Deduplication & Coverage Check**: Agent deduplicates semantically similar questions across branches and verifies coverage map (each source section has at least one question).
7. **Output**: `engine.py` validates schema. Final question bank (JSON + Markdown) + coverage map returned.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/heading.py`
- `app/rag/agentic/patterns/fusion.py`
- `app/agent/patterns/tree_of_thoughts.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Coverage map shows ≥ 1 question per source section
- Question quality rating by instructional designer ≥ 4.0 / 5.0 on a 20-question sample
- BVA branch produces ≥ 30% of questions targeting boundary/edge conditions
- No duplicate questions in the final bank (semantic similarity < 0.85)
- Schema validation passes for 100% of generated questions

---

## UC67 — Create Personalized Learning Path from Assessment

| Field | Value |
|-------|-------|
| **ID** | UC67 |
| **Title** | Create Personalized Learning Path from Assessment |
| **Domain** | Education |
| **Vertical / Industry** | Corporate L&D / EdTech |
| **Trigger** | Learner completes a skill assessment; L&D system submits results for personalised path generation |
| **Actor / Persona** | Learner, L&D Platform, Manager |
| **LLM** | gpt-4o |
| **RAG Pattern** | `corrective_rag` — validates course recommendations against available Confluence KB content |
| **Agent Pattern** | Plan-Execute with Goal Tree |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None — fully automated; Manager notification on path generation |
| **Memory** | Episodic — learner profile stored and updated per assessment cycle |

### Problem Statement

Generic learning paths ignore individual skill gaps. A personalised path that sequences content by gap severity, matches the learner's preferred format, and draws from the organisation's actual content library — not a generic catalogue — dramatically improves completion rates and skill transfer.

### Inputs

- Learner skill assessment results (JSON — skill domain scores, identified gaps)
- Learner profile (role, seniority, preferred learning format, past completions)
- Confluence KB (course and module catalogue with metadata: duration, format, skill tags)
- Learning objectives framework (PDF or structured data)

### Expected Output

- Personalised learning path (Markdown + JSON): sequenced module list with rationale, estimated time, format, skill gap addressed, and prerequisite mapping
- Learning path summary for manager notification
- LMS write-back payload (JSON — structured for LMS API)

### MCP Servers / External Tools

- `confluence_server.py` — course catalogue and content KB fetch
- Optional: LMS API connector — path write-back

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after Goal Tree generates recommended content for each skill gap, corrective RAG validates that the recommended course/module actually exists in the Confluence KB with the required skill tag. Recommendations referencing non-existent content are corrected via a re-retrieval pass to find the closest available alternative.

### Agent Pattern

`app/agent/patterns/plan_execute.py` — Plan-Execute orchestrates the path construction process. The Planner uses `app/agent/patterns/goal_tree.py` to decompose the learner's learning goal into a tree of subgoals by skill domain and gap severity. Each leaf subgoal maps to one or more content recommendations. Executor sequences the subgoals into a dependency-ordered learning path.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required path fields, prerequisite ordering validity, total estimated duration within role-appropriate bounds)

### End-to-End Steps

1. **Learner Context Load**: Assessment results and learner profile loaded. Episodic memory checks for prior learning path iterations and past module completions.
2. **Goal Tree Decomposition**: `goal_tree.py` decomposes the learner's overall skill development goal into a tree of subgoals: skill domain → skill cluster → specific gap. Each gap node is prioritised by gap severity and role relevance.
3. **Corrective RAG Content Matching**: For each leaf gap node, `corrective.py` retrieves matching courses/modules from the Confluence KB by skill tag. Validates that retrieved content exists and is current. Re-retrieves for any stale or non-existent recommendations.
4. **Plan-Execute Path Sequencing**: `plan_execute.py` Executor sequences content recommendations respecting prerequisite dependencies (e.g. "Foundations" before "Advanced"). Formats the ordered path with rationale per module.
5. **Learning Format Personalisation**: Agent applies learner's preferred format filter (video, reading, hands-on) and adjusts time estimates based on historical completion velocity from episodic memory.
6. **Episodic Memory Update**: Updated learner profile (new path, pending modules) written to episodic memory for the next assessment cycle.
7. **Output**: `engine.py` validates schema. Personalised learning path (Markdown + JSON) returned. LMS write-back payload generated. Manager notification summary produced.

### Real App/ Files Cited

- `app/rag/agentic/patterns/corrective.py`
- `app/agent/patterns/plan_execute.py`
- `app/agent/patterns/goal_tree.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All recommended modules exist in the Confluence KB (corrective validation passes 100%)
- Prerequisite ordering valid for 100% of generated paths
- Learner satisfaction rating ≥ 4.0/5.0 in A/B test vs. generic path (pilot group)
- Episodic memory correctly carries forward prior completion history across assessment cycles

---

## UC68 — Summarize Research Paper for Executive Audience

| Field | Value |
|-------|-------|
| **ID** | UC68 |
| **Title** | Summarize Research Paper for Executive Audience |
| **Domain** | Education |
| **Vertical / Industry** | Research / Academic / Enterprise Knowledge Management |
| **Trigger** | Researcher or knowledge manager uploads a 50-page research paper and requests an executive summary |
| **Actor / Persona** | Research Lead, Knowledge Manager, Executive Sponsor |
| **LLM** | gpt-4o |
| **RAG Pattern** | `raptor` — hierarchical summarisation enabling query at executive abstraction level |
| **Agent Pattern** | Self-Refine (iterative quality improvement on clarity and conciseness) |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None — fully automated; optional researcher review flag |
| **Memory** | None — stateless per paper |

### Problem Statement

A 50-page technical research paper contains methodology, statistical analysis, raw results, and domain-specific terminology that is inaccessible to a non-expert executive audience. A 1-page executive summary must distil the key findings, business implications, and recommended actions — without technical distortion.

### Inputs

- Research paper PDF (10–100 pages)
- Target audience descriptor (e.g. "C-suite, no technical background")
- Output format specification (length, required sections)

### Expected Output

- Executive summary (1–2 pages): context, key findings, methodology overview, business implications, recommended actions
- Key statistics (bullet list, verbatim from paper)
- Glossary of essential technical terms (plain-language definitions)

### MCP Servers / External Tools

- `filesystem_server.py` — PDF staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — full research paper parsing; handles academic PDF formats (two-column layouts, figure captions, references, appendices)
- `app/ingestion/chunkers/pdf_layout.py` — layout-aware chunking preserving section structure (Abstract, Introduction, Methods, Results, Discussion, Conclusion); handles multi-column academic layout

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR builds a hierarchy over the paper: section summaries → thematic clusters (findings, methodology, implications) → detail sentences. The Self-Refine agent queries at the thematic cluster level for the executive narrative (key findings, implications) and at the detail level for specific statistics and verbatim data points.

### Agent Pattern

`app/agent/patterns/self_refine.py` — generates an initial executive summary, then enters a critique-revise loop (max 3 iterations):
1. **Critique**: Evaluate on readability (Flesch score ≥ 60 for executive audience), absence of unexplained jargon, completeness (all required sections), accuracy (statistics match source).
2. **Revise**: Simplify jargon, tighten narrative, ensure business implication is explicitly stated for each finding.
3. RAPTOR re-queries used when a revision requires sourcing an additional finding.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required sections present, length within specified range); readability score check; no hallucinated statistics (all numeric claims validated against source)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the full 50-page paper. `pdf_layout.py` identifies section structure, handles two-column academic layout, and segments figures from body text.
2. **RAPTOR Indexing**: `raptor.py` builds a cluster tree: section-level summaries → thematic nodes (findings, methodology, implications, limitations) → sentence-level evidence.
3. **Initial Summary Draft**: Agent queries RAPTOR thematic nodes at the executive abstraction level. gpt-4o generates an initial 1-page executive summary with key findings, methodology overview, and business implications.
4. **Self-Refine — Critique Pass**: `self_refine.py` critique evaluates: Flesch readability score, jargon presence (flagged terms), section completeness, statistical accuracy (spot-checks 3 random statistics against RAPTOR source nodes).
5. **Self-Refine — Revision Pass**: Agent revises sections failing critique. Jargon replaced with plain-language equivalents. Business implications made explicit. Statistics re-verified against source.
6. **Glossary Generation**: Agent generates a glossary of essential technical terms using RAPTOR detail-level nodes for precise definitions, rewritten in plain language.
7. **Output**: `engine.py` validates schema and readability score. Final executive summary + key statistics bullet list + glossary returned.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/self_refine.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Flesch readability score ≥ 60 in final output
- All key statistics in summary traceable to source document (zero hallucinated numbers)
- Self-Refine reduces jargon count by ≥ 50% from iteration 1 to final
- Executive audience comprehension rating ≥ 4.0/5.0 in user study

---

## UC69 — Answer Technical Question from KB with Citations

| Field | Value |
|-------|-------|
| **ID** | UC69 |
| **Title** | Answer Technical Question from KB with Citations |
| **Domain** | Research |
| **Vertical / Industry** | Technology / Enterprise Knowledge Management |
| **Trigger** | Developer or technical user submits a natural-language technical question via the internal KB assistant |
| **Actor / Persona** | Software Engineer, Technical Architect, Developer |
| **LLM** | gpt-4o |
| **RAG Pattern** | `corrective_rag` + `colbert` reranking + `parent_child_chunker` expansion |
| **Agent Pattern** | CitationManager (inline citation tracking and validation) |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None — fully automated |
| **Memory** | None — stateless per question |

### Problem Statement

Enterprise knowledge bases (Confluence, GitHub docs, internal wikis) contain the ground truth for technical questions — but raw vector retrieval returns imprecise passages that lead to hallucinated or uncited answers. Every technical answer must be grounded in retrievable source passages with precise citations.

### Inputs

- Natural-language technical question (user input)
- Confluence KB (internal technical documentation)
- GitHub repository documentation (README files, wikis, inline docs)

### Expected Output

- Answer (200–600 words) with inline citation markers [1], [2], etc.
- Citation bibliography: source title · URL/path · section · page/line reference · retrieved passage excerpt
- Confidence score (fraction of answer claims that are citation-backed)

### MCP Servers / External Tools

- `confluence_server.py` — Confluence KB page fetch
- `github_server.py` — GitHub repository docs fetch

### Ingestion Pipeline

- `app/rag/parent_child_chunker.py` — parent = documentation section (e.g. "Configuration Reference"), child = individual paragraph or code block. ColBERT retrieves precise child chunks; parent sections expanded to provide full API/configuration context.

### RAG Pattern

Three-layer retrieval pipeline:

1. `app/rag/agentic/patterns/corrective.py` — initial retrieval of candidate passages from Confluence + GitHub docs. Corrective loop evaluates each retrieved passage for relevance; low-relevance passages trigger a refined query before inclusion.

2. `app/rag/agentic/patterns/colbert.py` — ColBERT late-interaction reranking applied to the corrective-retrieved candidate set. Token-level matching precisely scores technical passages (API names, configuration keys, error messages) against the question, ensuring the top-k passages are genuinely relevant.

3. `app/rag/parent_child_chunker.py` — for the top-k ColBERT-ranked passages, parent section expansion retrieves the full documentation section containing the passage, providing necessary surrounding context (e.g. the full API method signature and parameter list alongside the retrieved example).

### Agent Pattern

CitationManager — implemented as an inline tracking layer on the agent's generation step. Every factual claim in the generated answer is tagged to a retrieved passage at generation time. After generation, the CitationManager validates that all citation tags map to actual retrieved passages and that the passage content supports the claim. Any uncited or unsupported claim triggers a regeneration of that sentence.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (bibliography present, all inline citations resolved, confidence score computed); citation completeness check

### End-to-End Steps

1. **Question Analysis**: Agent analyses the technical question, identifying key entities (API names, system components, configuration keys) for targeted retrieval.
2. **Corrective Initial Retrieval**: `corrective.py` retrieves candidate passages from Confluence and GitHub docs. Low-relevance candidates are filtered via a corrective re-retrieval pass with a refined query.
3. **ColBERT Reranking**: `colbert.py` applies late-interaction reranking to the candidate set. Top-k passages selected based on token-level match scores.
4. **Parent-Child Expansion**: `parent_child_chunker.py` expands each top-k child passage to its parent section, providing full API/configuration context around the retrieved excerpt.
5. **Cited Answer Generation**: Agent generates the answer with inline citation markers linked to retrieved passages at generation time. CitationManager tracks every claim-to-passage mapping.
6. **Citation Validation**: CitationManager verifies all inline citations resolve to actual passages and the passage content supports the associated claim. Any unsupported claims are rewritten or flagged as uncertain.
7. **Output**: `engine.py` validates schema and citation completeness. Answer with inline citations + bibliography (source, URL/path, passage excerpt) + confidence score returned.

### Real App/ Files Cited

- `app/rag/parent_child_chunker.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/rag/agentic/patterns/colbert.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Citation coverage: ≥ 95% of factual claims in the answer are citation-backed
- ColBERT reranking improves top-3 passage relevance (NDCG@3) by ≥ 20% vs. vector-only retrieval
- Zero hallucinated API names or configuration keys on a technical correctness test set
- Parent-child expansion improves answer completeness (user rating) by ≥ 15% vs. child-only retrieval

---

## UC70 — Create Literature Review from 15 PDFs

| Field | Value |
|-------|-------|
| **ID** | UC70 |
| **Title** | Create Literature Review from 15 PDFs |
| **Domain** | Research |
| **Vertical / Industry** | Academic / Enterprise R&D |
| **Trigger** | Researcher uploads a batch of 15 research PDFs and requests a synthesised literature review |
| **Actor / Persona** | Research Scientist, Academic Researcher, R&D Lead |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `raptor` — applied both per-paper and cross-paper for hierarchical synthesis |
| **Agent Pattern** | Supervisor with parallel PDF sub-agents |
| **Compliance Bundle** | Standard |
| **HITL Gate** | None — fully automated; optional researcher review flag per section |
| **Memory** | Episodic — scoped to review project ID (supports iterative paper additions) |

### Problem Statement

A 15-paper literature review requires reading, extracting themes, methods, findings, and limitations from each paper, then synthesising across all 15 to identify consensus, contradictions, and research gaps. This takes a researcher 2–5 days manually. Parallelising the per-paper extraction and synthesising with RAPTOR dramatically compresses this timeline.

### Inputs

- Batch of 15 research PDFs (each 10–60 pages)
- Review focus question or research domain (user input)
- Optional: citation style preference (APA, IEEE)

### Expected Output

- Literature review (3,000–5,000 words): introduction, thematic sections (common themes, methodological approaches, contradictions, research gaps), conclusion
- Citation table: paper reference · key finding · method · limitation
- Research gap analysis section

### MCP Servers / External Tools

- `filesystem_server.py` — PDF batch staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — per-paper PDF parsing; handles varied academic formats (single-column, two-column, proceedings formats)
- `app/ingestion/chunkers/pdf_layout.py` — per-paper layout-aware chunking: preserves section structure (Abstract, Methods, Results, Discussion, Conclusion) across all papers

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — applied in two phases:
1. **Per-paper RAPTOR** (by each sub-agent): builds a cluster tree for an individual paper (section summaries → theme nodes → evidence sentences).
2. **Meta-corpus RAPTOR** (by Supervisor): after all sub-agents complete, Supervisor builds a RAPTOR tree over the 15 per-paper summaries — finding cross-paper themes, contradictions, and consensus.

### Agent Pattern

`app/agent/patterns/supervisor.py` — Supervisor spawns 15 parallel PDF sub-agents (one per paper). Each sub-agent independently:
- Parses its assigned PDF
- Builds a per-paper RAPTOR index
- Extracts: key findings, methods, results, limitations, open questions

Supervisor collects all 15 sub-agent outputs, builds the meta-corpus RAPTOR tree, and synthesises the full literature review. Episodic memory stores per-paper summaries for iterative review updates (researcher can add papers to the review project later).

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required review sections present, word count within range, citation table complete); no hallucinated citations (all references traceable to ingested PDFs)

### End-to-End Steps

1. **Supervisor Dispatch**: `supervisor.py` receives the 15-PDF batch. Assigns one paper to each of 15 parallel sub-agents. Passes the review focus question as shared context.
2. **Parallel Per-Paper Processing**: Each sub-agent independently runs: `pdf_parser.py` → `pdf_layout.py` → per-paper `raptor.py` RAPTOR build → extraction of key findings, methods, results, limitations, research questions. All 15 run concurrently.
3. **Sub-Agent Output Collection**: Supervisor collects structured extraction outputs from all 15 sub-agents. Each output includes: paper reference, key findings, method summary, limitation list, open questions, and RAPTOR summary node text.
4. **Meta-Corpus RAPTOR Synthesis**: Supervisor applies `raptor.py` to the 15 per-paper summaries, building a cross-paper cluster tree: cross-paper themes → methodological approaches → specific findings. Identifies consensus (findings appearing in 3+ papers), contradictions (conflicting findings), and research gaps (open questions across papers).
5. **Literature Review Drafting**: gpt-5.2 drafts the full literature review using the meta-corpus RAPTOR tree: thematic sections derived from top-level cluster nodes, with specific paper citations at leaf level.
6. **Episodic Memory Update**: Per-paper summaries and meta-corpus RAPTOR index stored in episodic memory scoped to the review project ID. Researcher can add new papers in a subsequent session without reprocessing all 15.
7. **Output**: `engine.py` validates schema (sections, word count, citation completeness). Full literature review (Markdown + citation table + research gap analysis) returned.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/supervisor.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All 15 papers represented with ≥ 1 citation in the literature review
- Parallel 15-agent batch completes within 3 minutes (wall clock)
- Cross-paper contradiction detection identifies ≥ 80% of deliberately injected contradictions in test batch
- No hallucinated citations (all references traceable to ingested PDFs — 100%)
- Episodic memory correctly carries forward per-paper summaries when a 16th paper is added in a follow-up session

---

*End of use-cases-61-70.md*
