# Use Cases 71–80: Research, Data Analytics, Executive Reporting, Browser/RPA & Multimodal

---

## Use Case 71: Extract Market Research Key Findings

**Goal:** Read a lengthy market research report PDF, extract and synthesize the key findings, data points, and strategic implications, and produce an executive brief with supporting evidence citations.

**Business problem:** Analysts and executives cannot read 80-page research reports for every market they track; automated extraction of key findings accelerates insight consumption without sacrificing accuracy.

**Actors:** Strategy analyst, executive, AgentVerse agent

**Inputs:** Market research PDF (50–120 pages, may include charts, tables, and footnotes), research focus areas (e.g., "addressable market size", "competitive dynamics", "customer segment growth"), internal market assumptions (Confluence)

**Agent pattern:** Self-Consistency — runs key findings extraction 3 times with different reading lenses (quantitative focus, competitive focus, customer segment focus); aggregates findings by consensus and confidence

**RAG pattern:** `raptor` — hierarchical document processing: page-level summaries → chapter summaries → section themes → executive synthesis; enables coherent extraction from 100+ page PDFs

**Memory used:** Execution memory (prior research reports on this market for trend tracking), Episodic memory (known high-value finding categories: TAM/SAM/SOM, CAGR, segment breakdown), Long-term memory (market size assumptions from past quarters for consistency checking)

**Ingestion path:** Research PDF → `PDFParser` (text + table extraction) + `PDFLayoutChunker` (respects columns, headers, page breaks) → `text-embedding-3-large` → `knowledge_chunks_3072`; internal assumptions → `HeadingChunker`

**Retrieval path:** Research focus area → `raptor`: (1) chapter-level summaries, (2) section-level key claims, (3) drill into specific pages for quantitative evidence, (4) synthesize across sections

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `text-embedding-3-large`

**Guardrails and governance:**
- PolicyEngine: no external API calls required (PDF processed locally); output contains citations with page numbers
- OutputContractBuilder: "key findings" + "executive brief" → structured output: Headline (1 sentence), 5 Key Findings (each with supporting data point + page citation), Strategic Implications (3 bullets), Data Limitations
- Citation enforcement: verifier checks every finding has a page number citation; findings without citation are flagged for human review

**End-to-end flow:**
1. Analyst uploads PDF via `POST /goals` with focus areas; queues to `goals.professional`
2. `_node_initialize`: RAPTOR strategy; Self-Consistency N=3; OutputContractBuilder → executive brief structure
3. `_node_rag_retrieval`: `PDFParser` + `PDFLayoutChunker` processes PDF → preserves table structure and footnotes; RAPTOR builds hierarchical summaries: 120 pages → 24 chapter summaries → 8 section themes
4. `_node_execute (Run 1 — Quantitative lens)`: retrieves data tables, market size figures, CAGR numbers; extracts top 5 quantitative findings with page citations
5. `_node_execute (Run 2 — Competitive lens)`: retrieves competitive landscape sections; extracts top 5 competitive dynamic findings
6. `_node_execute (Run 3 — Customer segment lens)`: retrieves segmentation sections; extracts top 5 customer behavior findings
7. Self-Consistency aggregation: findings appearing in 2/3 runs → HIGH confidence; 1/3 runs → MEDIUM confidence; 0/3 → excluded
8. Peer Review: independent reviewer validates that strategic implications are supported by cited findings; checks for analyst bias in framing

**Observability:** RAPTOR SSE with summarization levels; Self-Consistency agreement rate per finding; `GOAL_DURATION` target < 8min for 120-page PDF; cost ~$0.40; citation coverage rate (% findings with page citations)

**Eval path:** `grounding` (all findings cite specific page numbers), `goal_success` (executive brief complete with all required sections), Self-Consistency consensus rate (target > 80% finding agreement across 3 runs)

**Expected output:** Executive brief (2-page equivalent): headline, 5 key findings with citations, 3 strategic implications, market size summary with source page, data quality assessment

**Failure modes:** PDF is scanned image (no selectable text) → `PDFParser` OCR mode with lower confidence; tables not extractable → `VisionParser` processes table screenshots; PDF DRM protection → processing blocked, user instructed to provide text version; findings contradict internal assumptions → surfaced as "Assumption Challenge" section for strategy team review

**Code references:** `app/ingestion/parsers/pdf_parser.py`, `app/ingestion/chunkers/pdf_layout_chunker.py`, `app/rag/agentic/patterns/raptor.py`, `app/agent/patterns/self_consistency.py`, `app/agent/patterns/peer_review.py`

---

## Use Case 72: Analyze BigQuery Dataset → Executive Summary

**Goal:** Query a BigQuery dataset to extract key business metrics, identify trends and anomalies, and generate a narrative executive summary with supporting visualizations and statistical insights.

**Business problem:** Data teams produce raw query results that business stakeholders cannot interpret; automated narrative generation bridges the gap between data and decision-making.

**Actors:** Data analyst, business stakeholder, AgentVerse agent, BigQuery MCP server

**Inputs:** BigQuery dataset/table specification, analysis period (date range), key metrics of interest, business context (what decisions this analysis informs)

**Agent pattern:** ReAct — iterative: explore schema → formulate queries → execute → analyze results → refine queries based on findings → synthesize narrative

**RAG pattern:** `self_rag` — agent self-evaluates whether it has sufficient statistical context from the dataset before generating narrative; if coverage < 0.7, runs additional targeted queries; final self-evaluation confirms narrative is grounded in actual query results

**Memory used:** Execution memory (prior analysis runs for trend comparison), Procedural memory (learned: always check for NULL values and data quality before running analysis queries), Episodic memory (known seasonal patterns in this dataset)

**Ingestion path:** BigQuery schema → `bigquery_server.py:get_schema` → structured metadata; query results → JSON → `SemanticChunker` (by metric dimension); prior analysis summaries → `SemanticChunker`

**Retrieval path:** Analysis question → `self_rag`: (1) schema exploration determines available dimensions, (2) query results chunked by metric, (3) self-evaluate: is result statistically representative? sufficient sample size? → requery if needed

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `bigquery.execute_query` requires `bigquery:read` scope; no DML queries (INSERT/UPDATE/DELETE) allowed — read-only analysis
- GuardrailChecker: queries exceeding 10GB bytes processed are blocked; cost cap enforced per query ($5 max per query execution)
- Output contract: all narrative claims must reference specific query result row/column; statistical significance flags required for trend claims
- Audit trail: all executed BigQuery queries logged with bytes processed and cost

**End-to-end flow:**
1. Data analyst submits `POST /goals` with BigQuery project/dataset/table + analysis focus; queues to `goals.professional`
2. `_node_initialize`: `self_rag` strategy; ReAct pattern; query cost guard activated
3. `_node_rag_retrieval`: `bigquery_server.py:get_schema` → schema exploration; self-rag retrieves prior analysis summaries for trend context
4. `_node_execute (exploration)`: `bigquery_server.py:execute_query` — data quality check (NULL counts, date range coverage, row counts); identifies available dimensions and metrics
5. `_node_execute (core analysis)`: 3–5 targeted queries: time-series trend (month-over-month), top-N breakdown (by segment/region), anomaly detection (values > 2σ from rolling mean), cohort comparison
6. Self-RAG evaluates coverage: "Do query results answer the business question?" → if gaps, formulates additional queries
7. Narrative generation: interprets each metric with trend direction, magnitude, and business implication; flags anomalies with potential explanations
8. Generates JSON chart data for BI tool consumption; Confluence page with narrative + chart data

**Observability:** `TOOL_CALL_TOTAL` for BigQuery queries; bytes processed per query; total query cost logged; `GOAL_DURATION` target < 6min; self-rag re-query count; cost breakdown per analysis dimension

**Eval path:** `grounding` (narrative claims reference specific query results with row counts), `goal_success` (Confluence summary published), query efficiency (bytes processed vs insight value)

**Expected output:** Executive summary (3–4 paragraphs) with: top 3 metrics with period-over-period trend, 2–3 notable anomalies with context, key insights for business decision, chart data JSON for BI tools

**Failure modes:** Query timeout (BigQuery > 60s) → query decomposed into smaller time-window queries; insufficient data for statistical significance → claim flagged as "directional, not conclusive"; schema changed since last analysis → schema diff detected and highlighted in summary; bytes cost cap exceeded → query rewritten with partition filter

**Code references:** `app/mcp/servers/bigquery_server.py`, `app/rag/agentic/patterns/self_rag.py`, `app/ingestion/chunkers/semantic_chunker.py`, `app/governance/cost.py`, `app/mcp/servers/confluence_server.py`

---

## Use Case 73: Profile CSV Data Quality

**Goal:** Ingest a CSV file, perform comprehensive data quality profiling (completeness, consistency, validity, uniqueness, timeliness), and generate a data quality report with actionable remediation recommendations.

**Business problem:** Poor data quality silently corrupts analytics and ML models; automated profiling before data pipeline ingestion catches quality issues before they propagate downstream.

**Actors:** Data engineer, data analyst, AgentVerse agent

**Inputs:** CSV file (up to 1M rows), schema definition (optional), data quality rules (JSON config), business rules for validity (e.g., "age must be 18–120")

**Agent pattern:** ReAct — iterative: sample data → profile dimensions → detect anomalies → validate against rules → generate report

**RAG pattern:** `corrective_rag` — retrieves data quality standards and validation rules from KB; self-corrects if retrieved rules don't apply to the detected data types in the CSV; web fallback for domain-specific format standards (e.g., IBAN format, ISBN-13)

**Memory used:** Execution memory (prior profiling runs for schema drift detection), Procedural memory (profiling sequence: completeness → uniqueness → validity → consistency → timeliness), Reflexion memory (lesson: "always check for BOM characters in CSV header before processing")

**Ingestion path:** CSV → `row_group` chunker (batches of 10K rows for parallel profiling) → statistical aggregation; schema definition → `SemanticChunker`; data quality rules → structured JSON parse

**Retrieval path:** Column data type + domain → `corrective_rag`: retrieves applicable validation rules → self-evaluates: are rules applicable to detected data type? → corrects filter if needed

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: file processing sandboxed in execution environment; no external data exfiltration
- GuardrailChecker: PII column detection (email, phone, SSN, credit card patterns) → automatic flagging with GDPR classification recommendation
- Output contract: data quality scores must be computed from actual row statistics, not estimated
- Data retention: CSV contents not stored after analysis; only statistical profile retained

**End-to-end flow:**
1. Data engineer uploads CSV via `POST /goals` with optional schema and rules; queues to `goals.starter`
2. `_node_initialize`: `corrective_rag` strategy; `row_group` chunker; `gpt-4o-mini` for cost efficiency
3. `_node_rag_retrieval`: `corrective_rag` retrieves data quality rules for detected column types; PII detection patterns loaded
4. `_node_execute (profiling per column)`: for each column — completeness (% non-null), uniqueness (% distinct), format validity (regex check), range validity (min/max within bounds), distribution (mean/std/percentiles for numeric), top-10 most frequent values
5. `_node_execute (cross-column consistency)`: referential integrity checks (foreign key values exist in lookup), temporal consistency (end_date > start_date), business rule checks from config
6. PII scan: flags columns matching PII patterns with GDPR classification (personal, sensitive, special category)
7. Anomaly detection: columns with sudden completeness drop vs prior profile (schema drift); unexpected value distributions
8. Quality score per column (0–100) and overall dataset score; remediation recommendations ranked by impact

**Observability:** `GOAL_DURATION` target < 5min for 100K rows; cost ~$0.02/100K rows; column count per quality tier; PII detection events; schema drift flag events

**Eval path:** `goal_success` (all columns profiled, report generated), `grounding` (quality scores derived from actual row statistics), PII detection accuracy

**Expected output:** Data quality report with: overall score (0–100), per-column quality scorecard, top 10 issues by severity, PII classification, remediation recommendations, SQL/Pandas fix snippets for top issues

**Failure modes:** CSV > 1M rows → streaming processing in 10K batches with incremental stats; malformed CSV (inconsistent delimiters) → auto-detected delimiter with fallback sequence; all columns have 100% null → flags as likely wrong file; encoding issues → BOM detection and UTF-8 normalization

**Code references:** `app/ingestion/chunkers/row_group_chunker.py`, `app/rag/agentic/patterns/corrective_rag.py`, `app/intelligence/guardrails.py`, `app/agent/loop.py:_node_execute`, `app/context/output_contract_builder.py`

---

## Use Case 74: Generate Data Dictionary from PostgreSQL

**Goal:** Introspect a PostgreSQL database schema, infer business meaning for each table and column from naming conventions and relationships, and generate a comprehensive data dictionary with business definitions.

**Business problem:** Undocumented database schemas slow onboarding and cause incorrect query construction; automated data dictionary generation from schema + usage patterns creates living documentation.

**Actors:** Data engineer, data analyst, AgentVerse agent, postgres_server.py

**Inputs:** PostgreSQL connection (read-only), list of schemas to document, existing partial data dictionary (if any), sample query log (for usage pattern inference)

**Agent pattern:** Plan-Execute — structured: (1) introspect schema structure, (2) infer business meaning per table, (3) infer column semantics, (4) enrich with relationships and examples, (5) publish

**RAG pattern:** `hybrid` — vector search for semantically similar table/column names in existing documentation + lexical for exact table name matches in query logs (to understand how tables are actually used)

**Memory used:** Execution memory (previously documented tables to avoid duplication), Procedural memory (naming convention inference: `_id` suffix → foreign key, `_at` suffix → timestamp, `_count` → aggregation)

**Ingestion path:** PostgreSQL schema → `postgres_server.py:get_schema` → structured metadata (tables, columns, types, constraints, foreign keys, indexes); query log → `TimestampChunker`; existing data dictionary → `HeadingChunker`

**Retrieval path:** Table/column name → `hybrid`: (1) vector for semantic naming convention patterns, (2) lexical for query log mentions (how is this table JOINed, what WHERE clauses are used?) → combined context for definition generation

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `postgres.execute_query` in read-only mode; schema introspection limited to specified schemas; no SELECT on actual data rows (metadata only)
- GuardrailChecker: connection string and credentials never logged; schema output sanitized before storage
- OutputContractBuilder: "data dictionary" → structured JSON output: table_name, table_description, columns array with (name, type, description, example_values, constraints, related_tables)

**End-to-end flow:**
1. Data engineer submits `POST /goals` with connection params (read-only user) and schema list; queues to `goals.professional`
2. `_node_initialize`: `hybrid` RAG; Plan-Execute; OutputContractBuilder → data dictionary JSON contract; credentials handled via vault
3. `_node_rag_retrieval`: `postgres_server.py:get_schema` fetches full schema metadata; query log loaded if provided; existing partial dictionary fetched from Confluence
4. `_node_plan`: groups tables by domain based on naming patterns and FK relationships; orders by dependency (parent tables documented before children)
5. `_node_execute (per table)`: generates table description from name + columns + FK relationships + query log patterns; for each column: infers business meaning from name/type/constraints, generates description, notes related tables, infers example values from type/constraints
6. FK relationship graph: maps joins between tables → "This table relates to `orders` via `customer_id`"
7. `postgres_server.py:execute_query` (LIMIT 3): samples 3 real rows from non-PII tables for concrete examples
8. Assembles data dictionary JSON; publishes to Confluence with navigation index; generates Markdown for GitHub

**Observability:** Table count processed; `GOAL_DURATION` target < 10min for 200-table schema; cost ~$0.05 for 50 tables; `tool_success_rate` for postgres introspection

**Eval path:** `goal_success` (all tables documented, published), `grounding` (descriptions consistent with column types and constraints), definition quality (human rating via eval)

**Expected output:** Complete data dictionary with: table list and business domains, per-table description with example use cases, per-column definitions with types/constraints/examples, entity-relationship summary, query patterns observed from query log

**Failure modes:** Table has cryptic name with no patterns → description generated as "Purpose unclear — review with data owner"; FK references across schemas → cross-schema relationships documented with note; query log too large → top-100 most-executed queries used; PII column detected in sample → sampling skipped for that column, documented as "contains PII, no examples shown"

**Code references:** `app/mcp/servers/postgres_server.py`, `app/rag/engine.py:retrieve_hybrid`, `app/ingestion/chunkers/heading_chunker.py`, `app/context/output_contract_builder.py`, `app/mcp/servers/confluence_server.py`

---

## Use Case 75: Weekly BI Report from Multiple Sources

**Goal:** Aggregate data from BigQuery, Salesforce, and Jira, compute weekly business metrics (revenue pipeline, engineering velocity, customer health scores), and generate a comprehensive BI report distributed to stakeholders.

**Business problem:** Weekly reporting requires manual data gathering from 5+ systems taking 3–4 hours; automated multi-source aggregation delivers consistent, timely reports every Friday without analyst effort.

**Actors:** Business analyst, VP of Operations, AgentVerse agent, BigQuery + Salesforce + Jira MCP servers

**Inputs:** Week date range, stakeholder distribution list, prior week report (for delta), KPI targets (Confluence), dashboard configuration (which metrics per audience)

**Agent pattern:** Supervisor — main agent coordinates 3 parallel data-gathering agents: (1) BigQuery revenue/product metrics agent, (2) Salesforce pipeline agent, (3) Jira engineering velocity agent; Supervisor merges and generates unified report

**RAG pattern:** `raptor` — hierarchical synthesis: raw metrics per source → per-source summaries → cross-source business narrative; enables coherent multi-source synthesis at scale

**Memory used:** Execution memory (prior 4 weeks of reports for trend computation), Procedural memory (report assembly sequence: Executive Summary → Revenue → Product → Engineering → Risks), Episodic memory (seasonal patterns: Q4 pipeline always spikes, January engineering velocity dips)

**Ingestion path:** BigQuery results → JSON → `SemanticChunker`; Salesforce opportunities → `salesforce_server.py:query` → `SemanticChunker`; Jira sprint data → `jira_server.py:search_issues` → `row_group` chunker; prior reports → `HeadingChunker`

**Retrieval path:** Week's metric batch → `raptor`: (1) per-source metric summaries, (2) week-over-week delta computation, (3) cross-source synthesis (Jira velocity vs product delivery vs revenue recognition), (4) narrative generation with trend context

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `bigquery.execute_query` requires `bigquery:read`; `salesforce.query` requires `salesforce:read`; `jira.search_issues` requires `jira:read`
- GuardrailChecker: Salesforce deal names and amounts require `business:confidential` classification in output
- NLScheduler: runs every Friday at 9am via `TriggerSpec`; SLA 15min completion
- Audit trail: data sources accessed, timestamps, and row counts logged per run

**End-to-end flow:**
1. NLScheduler fires Friday 9am → `fire_due_schedules` → `POST /goals`; queues to `goals.enterprise`
2. `_node_initialize`: Supervisor pattern with 3 sub-agents; RAPTOR strategy; CONFIDENTIAL output classification
3. Supervisor dispatches parallel sub-agents:
   - Agent 1: `bigquery_server.py:execute_query` — WoW revenue, DAU/MAU, feature adoption rates, top error events
   - Agent 2: `salesforce_server.py:query` — pipeline created, pipeline closed-won, average deal size, top 10 opportunities by stage
   - Agent 3: `jira_server.py:search_issues` — stories completed, story points velocity, bugs opened/closed, P1 incidents
4. Supervisor waits for all 3 agents; merges results
5. RAPTOR synthesizes: per-source summaries → cross-source narrative (e.g., "Velocity increase correlated with 15% feature adoption growth")
6. WoW delta computation against prior week report from execution memory
7. Dual-audience report: Executive (8 KPIs + 3 key observations + 1 risk flag) + Operations (full metric tables + trend charts)
8. `slack_server.py:send_message` to `#weekly-metrics`; `confluence_server.py:create_page` for archive

**Observability:** Supervisor SSE with 3 parallel agent progress; `GOAL_DURATION` target < 15min; query cost per source; total run cost ~$0.40; `tool_success_rate` per data source; SLA breach alert if > 15min

**Eval path:** `goal_success` (report published to Slack + Confluence before 9:30am), `grounding` (all metrics cite source query results), data freshness (all sources accessed within 30min of report generation)

**Expected output:** Weekly BI report with: 8 headline KPIs with WoW deltas, revenue pipeline summary, engineering velocity trends, top opportunities, risks and blockers, full metric tables

**Failure modes:** BigQuery query fails → report published with "Revenue metrics unavailable" banner and retry scheduled; Salesforce API down → last week's pipeline used with staleness flag; Jira sprint not closed → velocity calculated from in-sprint completion rate; one sub-agent times out → Supervisor publishes partial report with missing section flagged; cost cap exceeded → query simplified to pre-aggregated tables

**Code references:** `app/agent/patterns/supervisor.py`, `app/mcp/servers/bigquery_server.py`, `app/mcp/servers/jira_server.py`, `app/rag/agentic/patterns/raptor.py`, `app/triggers/nl_scheduler.py`

---

## Use Case 76: Board Presentation from Quarterly Financials

**Goal:** Transform quarterly financial data from BigQuery and finance PDFs into a polished board presentation narrative with key financial highlights, operational metrics, strategic updates, and outlook.

**Business problem:** CFO staff spend 2 weeks manually preparing board presentation content from raw financial reports; automated narrative generation from structured data creates first-draft board materials in hours.

**Actors:** CFO, finance team, AgentVerse agent, BigQuery MCP server

**Inputs:** BigQuery finance tables (P&L, balance sheet, cash flow), quarterly investor reports (PDF), strategic plan (Confluence), prior quarter board deck (PDF for continuity), analyst consensus estimates (for comparison)

**Agent pattern:** Peer Review — (1) narrative generation pass creates financial narrative with data-backed claims, (2) independent CFO-proxy reviewer validates financial accuracy, appropriate disclosure language, and presentation standards

**RAG pattern:** `raptor` — hierarchical processing of financial PDFs and large query results: line-item data → financial statement summaries → narrative synthesis with quarter-over-quarter trends

**Memory used:** Execution memory (prior 4 quarters for YoY and QoQ trends), Procedural memory (board presentation structure: Agenda → Financial Highlights → Business Unit Performance → Strategic Updates → Outlook → Q&A prep), Long-term memory (analyst consensus history for comparison)

**Ingestion path:** Finance PDFs → `PDFParser` + `PDFLayoutChunker` (financial statement page detection) → `text-embedding-3-large`; BigQuery → `bigquery_server.py:execute_query` → JSON → `SemanticChunker`; prior board deck → `PDFParser`

**Retrieval path:** Board section topic → `raptor`: (1) financial statement summaries, (2) business unit drill-downs, (3) variance explanations from management notes, (4) strategic context from Confluence

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-5.2`, embedder: `text-embedding-3-large`

**Guardrails and governance:**
- HITL: **mandatory CFO review and approval** before any content is finalized — board materials require human attestation
- Peer Review: independent financial accuracy reviewer validates all numbers against source data
- PolicyEngine: financial data access requires `finance:confidential` scope; output classified as BOARD_CONFIDENTIAL
- GuardrailChecker: forward-looking statements must include standard safe harbor language; regulatory disclosure checks for public company requirements
- Audit trail: all financial figures cited logged with source table, column, and query timestamp for SOX audit trail
- Governance bundle: REGULATED (immutable audit + compliance)

**End-to-end flow:**
1. Finance team submits `POST /goals` with quarter parameters; queues to `goals.enterprise`; REGULATED bundle + BOARD_CONFIDENTIAL classification activated
2. `_node_initialize`: RAPTOR strategy; Peer Review pattern; HITL configured for CFO approval; OutputContractBuilder → board deck section structure
3. `_node_rag_retrieval`: BigQuery queries for revenue, gross margin, operating expenses, cash position, ARR/NRR (SaaS metrics); RAPTOR processes finance PDFs; prior board deck loaded for continuity
4. `_node_plan`: maps content to 8 board deck sections; identifies slides requiring charts vs narrative
5. `_node_execute (Pass 1 — Narrative generation)`: for each section — retrieves relevant financials → generates narrative with specific numbers → computes QoQ and YoY deltas → identifies 3 key observations per section
6. Safe harbor language injected for forward-looking statements via PolicyEngine
7. `_node_peer_review (Pass 2 — Financial accuracy)`: independent reviewer validates: all numbers match BigQuery source, no arithmetic errors, P&L items properly classified, margin calculations correct; flags any discrepancies
8. HITL: CFO reviews complete draft; approves, edits, or requests revision with specific feedback; feedback injected into context → revision cycle

**Observability:** `peer_review_score` for financial accuracy; HITL SSE event with CFO identity; `GOAL_DURATION` for draft generation target < 30min; cost ~$1.50 for full board deck; SOX audit trail event count

**Eval path:** `grounding` (all financial figures traceable to BigQuery source), `safety` (no undisclosed material information, safe harbor included), `goal_success` (CFO HITL approved and deck sections complete)

**Expected output:** Board presentation narrative for 8 sections with specific financials, QoQ/YoY comparisons, chart data specifications, management commentary, outlook narrative with appropriate disclosure language

**Failure modes:** Financial data inconsistency between PDFs and BigQuery → discrepancy flagged for CFO resolution before proceeding; prior board deck format changed → continuity section skipped with note; HITL CFO approval timeout → deck marked "pending approval" with escalation to board secretary; Peer Review finds > 3 numerical errors → full revision cycle triggered rather than inline corrections

**Code references:** `app/mcp/servers/bigquery_server.py`, `app/ingestion/parsers/pdf_parser.py`, `app/rag/agentic/patterns/raptor.py`, `app/agent/patterns/peer_review.py`, `app/governance/hitl.py`, `app/governance/audit.py`

---

## Use Case 77: Extract Data from Web Portal (No API)

**Goal:** Navigate a web portal that has no public API, authenticate, navigate to the target data section, extract structured data across multiple pages, and return it as structured JSON.

**Business problem:** Many business systems (government portals, legacy vendor portals, partner dashboards) have no API; manual data extraction is error-prone and time-consuming; automated RPA-based extraction enables integration without API.

**Actors:** Data engineer, operations team, AgentVerse agent, RPAExecutor (Playwright)

**Inputs:** Portal URL, authentication credentials (stored in vault), target data section and pagination rules, output schema definition, extraction frequency

**Agent pattern:** ReAct — iterative: navigate → extract → paginate → verify completeness → retry on failure

**RAG pattern:** `flare` — uncertainty-driven: when page structure is ambiguous (e.g., multiple similar tables on page), FLARE triggers additional analysis pass to confirm correct data section; VisionParser assists when text extraction is unreliable

**Memory used:** Execution memory (last successful extraction selector paths for this portal), Procedural memory (learned navigation sequence: login → dashboard → export section → pagination), Reflexion memory (lesson: "always wait for network idle before extracting — portal uses lazy loading")

**Ingestion path:** Web portal pages → `RPAExecutor` (Playwright) renders and extracts HTML → `VisionParser` for screenshots → structured data parsing; portal navigation flow → Procedural memory

**Retrieval path:** Page layout → `flare`: (1) identifies data tables via CSS selectors from memory, (2) uncertain match → `VisionParser` screenshot analysis to confirm correct table, (3) confirmed selector → extraction proceeds

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: web scraping tools require `web:scrape` scope; portal credentials stored in `vault.py` (never in goal text)
- GuardrailChecker: rate limiting enforced — max 10 requests/minute to avoid portal blocks; politeness delay 2–5s between pages
- HITL: not required for scheduled extraction; required if portal behavior changes significantly (e.g., login page structure change)
- Data classification: extracted data inherits source system's data classification

**End-to-end flow:**
1. NLScheduler triggers weekly extraction `TriggerSpec`; `POST /goals` queued to `goals.professional`
2. `_node_initialize`: `flare` strategy; RPAExecutor initialized; portal credentials retrieved from vault
3. `_node_execute (navigation)`: `RPAExecutor:navigate` → portal login page; fills credentials; handles MFA if configured; waits for dashboard to load (network idle)
4. `_node_execute (extraction)`: navigates to target data section; FLARE confidence check on identified data table → confirms via VisionParser screenshot; extracts table headers and rows; identifies pagination controls
5. Pagination loop: clicks "Next" or increments page parameter; extracts each page; tracks total records vs expected count from page header; stops when last page detected
6. Data validation: cross-checks extracted row count vs portal's displayed "X records" count; flags discrepancy > 2%
7. Transforms extracted data to output schema (JSON/CSV); stores in specified destination (S3, Postgres, etc.)
8. `_node_verify`: spot-checks 3 random rows against manual verification if reference data available

**Observability:** `TOOL_CALL_TOTAL` per page navigation; extraction rate (rows/min); FLARE trigger count; `GOAL_DURATION` per extraction run; rate limit hit count; data completeness score

**Eval path:** `goal_success` (all pages extracted, output schema valid), `grounding` (extracted data matches portal display values), completeness rate (extracted rows vs portal total count)

**Expected output:** Structured JSON/CSV of extracted data matching output schema, extraction summary (pages visited, rows extracted, data freshness timestamp), anomaly flags if structure changed

**Failure modes:** Portal login fails (expired credentials) → immediate HITL alert to operator; page structure changed (selector not found) → FLARE uncertainty → VisionParser attempts new selector identification → Reflexion stores new selector; CAPTCHA encountered → human-in-the-loop request for manual solve; portal timeout → retry with exponential backoff × 3, then alert

**Code references:** `app/rpa/executor.py`, `app/perception/vision_parser.py`, `app/rag/agentic/patterns/flare.py`, `app/governance/vault.py`, `app/triggers/nl_scheduler.py`

---

## Use Case 78: Fill Insurance Claim Form from JSON

**Goal:** Take a structured insurance claim JSON payload, navigate to the insurance portal, and accurately complete and submit the claim form across multiple pages, handling field validation, dropdowns, and file attachments.

**Business problem:** Insurance claim submission requires navigating complex multi-page forms; manual entry introduces errors and takes 45+ minutes per claim; automated form filling with validation enables high-volume, error-free submission.

**Actors:** Claims processor, AgentVerse agent, RPAExecutor (Playwright)

**Inputs:** Claim JSON (claimant info, incident details, coverage type, dollar amounts, supporting document paths), insurance portal credentials (vault), form field mapping configuration

**Agent pattern:** Plan-Execute — structured plan: (1) map JSON fields to form fields, (2) navigate to form, (3) fill section by section with validation, (4) attach documents, (5) submit with confirmation capture

**RAG pattern:** `lexical` — field name matching between JSON keys and portal form field labels; BM25 for fuzzy label matching when JSON key doesn't exactly match form label (e.g., `claim_date` → "Date of Loss")

**Memory used:** Procedural memory (learned field mapping for this portal version), Reflexion memory (lesson: "dropdown 'Type of Loss' expects full label not code — use 'Fire Damage' not 'FIRE'"), Execution memory (prior claim IDs for deduplication)

**Ingestion path:** Claim JSON → structured parsing; form field labels → `RPAExecutor:get_form_fields` → `lexical` mapping; field mapping config → static rules

**Retrieval path:** JSON key → `lexical` BM25 → form label match; ambiguous matches → VisionParser screenshot analysis to confirm correct field before filling

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: mandatory validation checkpoint before final form submission — human must confirm claim data is accurate before `submit_form` is called
- PolicyEngine: `rpa.submit_form` requires `claims:write` scope + HITL token
- GuardrailChecker: dollar amount fields validated against claim JSON value before submission; any discrepancy > $0.01 → abort and alert
- Audit trail: every field filled logged with field name, value, and timestamp; form submission confirmation logged

**End-to-end flow:**
1. Claims system submits `POST /goals` with claim JSON attachment; queues to `goals.professional`
2. `_node_initialize`: Plan-Execute pattern; HITL configured for pre-submission; lexical field mapping strategy
3. `_node_plan`: maps all 40+ claim JSON fields to portal form fields using BM25 label matching; flags unmapped fields for manual review
4. `_node_execute (Section 1 — Claimant Info)`: navigates to claimant section; fills name, policy number, contact info; handles dropdown for coverage type via lexical label match
5. `_node_execute (Section 2 — Incident Details)`: fills date of loss, incident type, location; handles multi-select for damages; uploads supporting documents via file input
6. `_node_execute (Section 3 — Financial)`: fills claim amounts; double-validates dollar amounts against JSON source before entering
7. HITL checkpoint: sends claim summary to claims processor for review; processor confirms or requests correction; on approval → proceed to submission
8. `RPAExecutor:submit_form`; captures confirmation page → extracts claim reference number; logs to audit trail

**Observability:** Field fill success rate; HITL wait time; `GOAL_DURATION` target < 5min (excluding HITL); claim reference number captured; `TOOL_CALL_TOTAL` per field interaction

**Eval path:** `goal_success` (claim submitted, reference number captured), field accuracy (filled values match JSON source 100%), HITL approval rate

**Expected output:** Claim submission confirmation with portal-assigned claim reference number, filled field log with values, HITL approval record, any warnings for fields that required manual intervention

**Failure modes:** Form field not found (portal update) → Reflexion stores new field selector; dropdown value not in options list → HITL alert with available options; session timeout during long form → re-authenticate and resume from last saved section; file attachment fails → claim submitted without attachment, file upload failure logged for manual resolution; HITL timeout → claim not submitted, escalated to supervisor

**Code references:** `app/rpa/executor.py`, `app/perception/vision_parser.py`, `app/rag/engine.py:retrieve_lexical`, `app/governance/hitl.py`, `app/governance/audit.py`

---

## Use Case 79: Monitor Competitor Pricing Page

**Goal:** Periodically scrape a competitor's pricing page, detect any changes in plan names, prices, or feature inclusions, and alert the product and sales teams with a diff summary.

**Business problem:** Competitor pricing changes directly affect win/loss rates; manual monitoring is inconsistent and slow; automated change detection enables rapid pricing response within hours of a competitor update.

**Actors:** Product manager, sales team, AgentVerse agent, RPAExecutor (Playwright), VisionParser

**Inputs:** Competitor pricing page URLs (list), prior extracted pricing data (PostgreSQL), alert distribution list (Slack channels + email), change significance threshold (price change > 10% = HIGH alert)

**Agent pattern:** ReAct — iterative: navigate → extract → compare to prior → classify change → alert if significant

**RAG pattern:** `corrective_rag` — retrieves prior pricing snapshot from KB; self-corrects if retrieved snapshot is stale (> 7 days); regenerates fresh baseline from PostgreSQL before comparison

**Memory used:** Execution memory (last pricing snapshot per competitor for diff computation), Procedural memory (extraction sequence per portal — different selectors per competitor), Reflexion memory (lesson: "Competitor X uses A/B testing on pricing — take 3 screenshots to confirm consistency")

**Ingestion path:** Pricing pages → `RPAExecutor` (Playwright with JS rendering) → `VisionParser` (screenshots for visual comparison) + HTML text extraction → `SemanticChunker`; prior snapshot → `postgres_server.py:query`

**Retrieval path:** Competitor URL → `corrective_rag`: (1) retrieves prior snapshot from PostgreSQL, (2) checks staleness: if last scrape > 7 days → fresh baseline required → re-scrapes reference, (3) confirmed fresh baseline → diff computation

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- NLScheduler: runs every Monday and Thursday at 8am via `TriggerSpec`; configurable cadence
- PolicyEngine: `web:scrape` scope required; compliance review: only publicly visible pricing data extracted
- GuardrailChecker: rate limiting (max 5 requests/competitor page with 3s delay); respectful scraping per robots.txt
- Audit trail: all pricing snapshots logged with timestamp and page SHA for evidence of competitor pricing history

**End-to-end flow:**
1. NLScheduler fires Monday 8am → `POST /goals`; queues to `goals.professional`
2. `_node_initialize`: `corrective_rag` strategy; RPAExecutor with VisionParser; per-competitor selector configs loaded
3. Per competitor (parallel): `RPAExecutor:navigate` → renders full pricing page (JS executed); `VisionParser` captures full-page screenshot; HTML parser extracts plan names, prices, feature list items
4. `corrective_rag` retrieves prior snapshot from PostgreSQL; freshness check; constructs structured diff
5. Change classification: plan removed → CRITICAL; price increase > 10% → HIGH; feature added/removed → MEDIUM; UI-only change → LOW; no change → skip
6. For HIGH/CRITICAL changes: `slack_server.py:send_message` to `#competitive-intel` with diff summary, screenshot comparison, and impact assessment
7. `postgres_server.py:insert` saves new snapshot with timestamp and SHA
8. `confluence_server.py:update_page` updates competitive pricing comparison page

**Observability:** `GOAL_DURATION` per competitor run; change detection rate; false positive rate (UI changes flagged as pricing changes); `TOOL_CALL_TOTAL` per competitor; alert volume per competitor over time

**Eval path:** `goal_success` (all competitors scraped and compared), change detection accuracy, false positive rate (UI vs content changes)

**Expected output:** Per-competitor change report: no change (suppressed) or change summary with diff, percentage change, impact classification, screenshot comparison, and recommended response action

**Failure modes:** Competitor adds CAPTCHA → marks as "requires manual check" + HITL alert; pricing page requires login → skips with flag (requires manual monitoring for that competitor); A/B test detected (prices differ between scrapes) → takes median of 3 scrapes; CDN shows cached old page → adds cache-busting headers; page structure completely redesigned → VisionParser screenshot diff flagged as structural change requiring selector update

**Code references:** `app/rpa/executor.py`, `app/perception/vision_parser.py`, `app/rag/agentic/patterns/corrective_rag.py`, `app/mcp/servers/postgres_server.py`, `app/triggers/nl_scheduler.py`

---

## Use Case 80: Extract Line Items from Scanned Invoice

**Goal:** Process a scanned invoice image or PDF, extract all line items (description, quantity, unit price, total), vendor details, invoice number, and payment terms, and output structured JSON for ERP ingestion.

**Business problem:** AP teams manually key invoice data from PDFs into ERP systems; errors and delays slow payment processing; automated extraction with validation reduces processing time from 30 minutes to 30 seconds per invoice.

**Actors:** AP team member, AgentVerse agent, VisionParser (GPT-4o vision)

**Inputs:** Scanned invoice PDF or image (JPEG/PNG), ERP field schema (required output JSON structure), vendor master list (for vendor ID lookup), GL code mapping rules

**Agent pattern:** ReAct — iterative: parse invoice → extract structured data → validate against business rules → map to ERP schema → flag exceptions

**RAG pattern:** `corrective_rag` — retrieves vendor master records and GL code mapping from KB; self-corrects if extracted vendor name doesn't match any vendor master entry (fuzzy match fallback); web fallback disabled (internal data only)

**Memory used:** Execution memory (past invoices from this vendor for anomaly detection — price changes, new line items), Procedural memory (learned: always extract header data first, then line items, then totals and cross-validate), Reflexion memory (lesson: "Vendor X uses non-standard date format MM.DD.YY — apply custom parser")

**Ingestion path:** Invoice PDF/image → `VisionParser` (GPT-4o vision model) → structured extraction with bounding box coordinates; vendor master → `postgres_server.py:query`; GL mapping rules → `HeadingChunker` → `lexical` lookup

**Retrieval path:** Extracted vendor name → `corrective_rag`: (1) fuzzy match against vendor master (postgres_server.py query with trigram similarity), (2) self-evaluate: is match confident (similarity > 0.9)? → if not, flag for AP team review; GL code → lexical exact match against mapping table

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o` (vision), verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: PII detection on invoice (vendor bank account numbers, tax IDs → redacted in logs); only invoice metadata and line item data retained
- PolicyEngine: ERP write requires `erp:write` scope; AP team confirmation required for invoices > $10,000
- HITL: mandatory for invoices > $10,000 or any invoice with validation failures (total mismatch, unmatched vendor)
- Audit trail: original invoice SHA, extracted JSON, and validation results logged for AP audit trail
- Math validation: sum of line item totals must equal invoice total ± $0.01 rounding tolerance

**End-to-end flow:**
1. AP team submits invoice file via `POST /goals`; queues to `goals.professional`
2. `_node_initialize`: `corrective_rag` strategy; VisionParser with GPT-4o vision; math validation rules activated; HITL configured for threshold conditions
3. `_node_execute (header extraction)`: `VisionParser` processes full invoice image → extracts: vendor name, invoice number, invoice date, due date, payment terms, PO reference, currency
4. `_node_execute (region chunking)`: VisionParser identifies line item table region via layout analysis → extracts each line item row: description, quantity, unit price, tax rate, line total
5. `_node_execute (totals)`: extracts subtotal, tax amounts, shipping, grand total; math validation: Σ(line totals) + tax + shipping = grand total → within $0.01 tolerance
6. `corrective_rag` vendor lookup: trigram similarity match against vendor master → vendor_id resolved; GL code mapped from line item descriptions
7. Math passes, invoice < $10K: output ERP JSON automatically; ERP ingestion call
8. Math fails OR invoice > $10K: HITL alert to AP team with exception details; human reviews and corrects in portal

**Observability:** `VisionParser` confidence score per field; math validation pass rate; vendor match confidence; `GOAL_DURATION` target < 30s per invoice; cost ~$0.02/invoice (GPT-4o vision); PII detection event count; HITL trigger rate

**Eval path:** `grounding` (extracted values match invoice source — spot-check sample), field extraction accuracy (% fields correctly extracted), math validation pass rate, `goal_success` (ERP JSON generated and validated)

**Expected output:** Structured ERP-ready JSON with: vendor ID, invoice number, date, payment terms, line items array (description/quantity/unit_price/gl_code/amount), totals, validation status, confidence scores per field

**Failure modes:** Invoice is handwritten → VisionParser confidence < 0.6 → HITL with extracted draft for human correction; multi-page invoice → VisionParser processes each page, combines line items with page-boundary detection; rotated/skewed scan → VisionParser handles rotation; vendor not in master → new vendor flag → AP team adds to master before ERP ingestion; currency mismatch between line items and total → flagged as critical validation failure requiring human review

**Code references:** `app/perception/vision_parser.py`, `app/rag/agentic/patterns/corrective_rag.py`, `app/mcp/servers/postgres_server.py`, `app/governance/hitl.py`, `app/governance/audit.py`
