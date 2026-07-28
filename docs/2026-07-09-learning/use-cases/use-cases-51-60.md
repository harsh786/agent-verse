# Use Cases 51–60: Project Management · Healthcare · Insurance · Banking

> **Domains covered:** Project Management (51–52) · Healthcare (53–56) · Insurance (57–59) · Banking (60)
> All use cases follow the AgentVerse canonical field set and cite real `app/` source files.

---

## UC51 — Build Project Risk Register from Planning Docs

| Field | Value |
|-------|-------|
| **ID** | UC51 |
| **Title** | Build Project Risk Register from Planning Docs |
| **Domain** | Project Management |
| **Vertical / Industry** | Technology · PMO |
| **Trigger** | PM uploads Confluence space + Jira epic backlog and requests a risk register |
| **Actor / Persona** | Project Manager, PMO Lead |
| **LLM** | gpt-4o |
| **RAG Pattern** | `raptor` — hierarchical cluster tree across large planning corpora |
| **Agent Pattern** | Plan-Execute |
| **Compliance Bundle** | Standard |
| **HITL Gate** | Critical (H×H) risk entries require PMO Lead approval before register is finalised |
| **Memory** | Episodic — scoped to project ID |

### Problem Statement

Planning artefacts, sprint backlogs, and milestone docs scattered across Confluence and Jira hide inter-dependencies and latent schedule risks. A PMO Lead needs a structured risk register — with probability, impact, owner, mitigation, and contingency — without reading 200+ pages manually.

### Inputs

- Confluence space export (HTML/PDF, 50–200 pages)
- Jira epic list with story-point estimates, assignees, and due dates
- Project charter PDF (optional)

### Expected Output

- Risk register (Markdown table): ID · risk description · probability (H/M/L) · impact (H/M/L) · risk score · owner · mitigation · contingency
- Executive risk summary (≤ 250 words)
- Jira issues pre-populated with `risk-register` label (write-back via Jira MCP)

### MCP Servers / External Tools

- `confluence_server.py` — page fetch, space export
- `jira_server.py` — epic/story read; issue create for risk items
- `filesystem_server.py` — local PDF staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses project charter PDF into raw text + metadata
- `app/ingestion/chunkers/pdf_layout.py` — section-aware chunking preserving heading hierarchy (H1→H2→H3)
- `app/rag/parent_child_chunker.py` — parent = section, child = paragraph; child chunks retrieved, parent context surfaced

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR builds a 3-level cluster tree: document level (strategic themes) → topic level (risk categories) → leaf level (evidence sentences). The Plan-Execute agent first queries at cluster level to enumerate risk categories, then drills into leaf chunks for supporting evidence and source citations.

### Agent Pattern

`app/agent/patterns/plan_execute.py` — Planner emits a 5-step execution plan:
1. Enumerate risk signals from RAPTOR summary nodes
2. Score each risk (probability × impact matrix)
3. Cross-reference Jira story-point burn rate for schedule risks
4. Draft mitigation + contingency per risk
5. Write back to Jira and format final register

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — validates output schema (required columns present, scores within valid range), enforces max-token budget per risk entry

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the project charter PDF. `pdf_layout.py` extracts section hierarchy. `confluence_server.py` fetches all Confluence pages in the project space. `jira_server.py` pulls epics, story points, and assignees.
2. **Chunk & Index**: `parent_child_chunker.py` creates parent=section / child=paragraph chunk pairs. `raptor.py` clusters all chunks into a 3-level hierarchy indexed in the vector store.
3. **Plan**: `plan_execute.py` Planner calls RAPTOR at the document-summary level to enumerate top-level risk themes and emits a 5-step execution plan.
4. **Execute — Risk Extraction**: Executor queries RAPTOR topic nodes ("what are the delivery risks in this domain?"), retrieves leaf chunks as evidence, and constructs structured risk records (ID, description, probability, impact).
5. **Execute — Schedule Risk Enrichment**: Executor calls `jira_server.py` to fetch story-point burn rate, overdue flags, and unassigned critical-path items; merges schedule evidence into relevant risk records.
6. **HITL Gate**: Any risk entry with risk score = Critical (H×H) is routed through `app/governance/hitl.py`; the PMO Lead reviews and either approves, downgrades, or adds a note before the register is sealed.
7. **Output & Write-back**: `engine.py` validates schema completeness. `jira_server.py` creates risk-tagged Jira issues. Final risk register + executive summary are returned to the caller.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/parent_child_chunker.py`
- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/plan_execute.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Risk register recall ≥ 80% against a manually curated baseline
- All Critical (H×H) risks present and HITL gate triggered for each
- Jira write-back creates issues with correct label and risk score field
- End-to-end latency < 90 s for a 100-page Confluence space

---

## UC52 — Identify Critical Path Schedule Risks

| Field | Value |
|-------|-------|
| **ID** | UC52 |
| **Title** | Identify Critical Path Schedule Risks |
| **Domain** | Project Management |
| **Vertical / Industry** | Technology · PMO |
| **Trigger** | Scheduled weekly job or manual PM trigger on project timeline data |
| **Actor / Persona** | Project Manager, Delivery Lead |
| **LLM** | gpt-4o |
| **RAG Pattern** | `multi_hop` — multi-step reasoning across dependency chains (`app/rag/agentic/patterns/adaptive.py`) |
| **Agent Pattern** | Plan-Execute |
| **Compliance Bundle** | Standard |
| **HITL Gate** | Schedule risk above configured threshold (e.g. > 2 weeks slip) requires Delivery Lead approval |
| **Memory** | Episodic — scoped to project ID + sprint cycle |

### Problem Statement

Critical path risks are invisible until a deadline slips. By multi-hop traversal of Jira dependency links and Gantt chart data, the agent surfaces which tasks, if delayed, collapse the entire delivery schedule — before it happens.

### Inputs

- Jira epic + story tree with `blocks`/`is blocked by` links
- Project timeline PDF or Gantt export (CSV / PDF)
- Historical velocity data from Jira (story points per sprint)

### Expected Output

- Critical path diagram annotation (Markdown table + Mermaid graph)
- List of top-5 schedule risk items ranked by slip potential (days)
- Recommended mitigation actions per risk item

### MCP Servers / External Tools

- `jira_server.py` — epic/story/link read; sprint velocity query
- `filesystem_server.py` — Gantt PDF staging

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses Gantt/charter PDF into structured text
- `app/ingestion/chunkers/pdf_layout.py` — preserves table structure for milestone schedules

### RAG Pattern

`app/rag/agentic/patterns/adaptive.py` (multi_hop mode) — multi-step retrieval chains: (1) retrieve tasks on the critical path, (2) follow dependency links to upstream blockers, (3) retrieve velocity history for those tasks. Each hop narrows the search context for the next.

### Agent Pattern

`app/agent/patterns/plan_execute.py` — Planner emits steps: (1) build dependency graph from Jira links, (2) identify critical path nodes, (3) compute float for each node, (4) project slip risk using velocity data, (5) rank and report.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — validates that every reported risk item has a Jira issue key, estimated slip days, and at least one mitigation action

### End-to-End Steps

1. **Ingest**: `jira_server.py` fetches the full epic/story tree including `blocks` and `is blocked by` links. `pdf_parser.py` parses the Gantt PDF. `pdf_layout.py` extracts milestone tables.
2. **Dependency Graph Construction**: Executor builds an in-memory directed acyclic graph (DAG) of task dependencies from Jira links + Gantt milestone data.
3. **Multi-Hop RAG Traversal**: `adaptive.py` (multi_hop mode) executes a chain: retrieve tasks on the longest path → hop to each upstream dependency → retrieve velocity history for those stories → derive schedule float per node.
4. **Critical Path Analysis**: Executor computes float (slack) for each path segment; nodes with float = 0 are critical. Compares current burn rate to planned velocity to project slip probability.
5. **Risk Ranking**: Ranks risk items by (slip probability × business impact) using RAPTOR summary nodes for impact context. Generates Mermaid graph annotating critical nodes.
6. **HITL Gate**: Any item with projected slip > configured threshold is surfaced to the Delivery Lead via `app/governance/hitl.py` for approval or re-scoping decision.
7. **Output**: `engine.py` validates schema. Final schedule risk report (Markdown + Mermaid) returned; Jira comments added to critical-path issues.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/agentic/patterns/adaptive.py`
- `app/agent/patterns/plan_execute.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All tasks with float = 0 identified on a 50-story test project
- Slip projections within ± 1 sprint of ground truth on historical data
- HITL triggered for every item above slip threshold
- Mermaid graph renders without syntax errors

---

## UC53 — Extract Medication Instructions from Clinical PDF

| Field | Value |
|-------|-------|
| **ID** | UC53 |
| **Title** | Extract Medication Instructions from Clinical PDF |
| **Domain** | Healthcare |
| **Vertical / Industry** | Clinical / Hospital Pharmacy |
| **Trigger** | Pharmacist or nurse uploads a clinical discharge summary or prescription PDF |
| **Actor / Persona** | Clinical Pharmacist, Nursing Staff |
| **LLM** | gpt-4o |
| **RAG Pattern** | `corrective_rag` — self-evaluation loop corrects low-confidence extractions |
| **Agent Pattern** | HITL gate on dosage ambiguity |
| **Compliance Bundle** | HIPAA REGULATED |
| **HITL Gate** | Any dosage, frequency, or route field flagged as ambiguous routes to pharmacist review |
| **Memory** | None — stateless per encounter for HIPAA compliance |

### Problem Statement

Clinical discharge PDFs mix free-text, tables, and scanned sections. Medication names, doses, frequencies, and routes must be extracted with zero tolerance for error. Ambiguous fields must never be silently defaulted — they must surface to a clinician.

### Inputs

- Clinical discharge summary or prescription PDF (1–20 pages)
- Optional: structured formulary reference for drug name normalisation

### Expected Output

- Structured medication list (JSON): drug name · dose · unit · frequency · route · duration · prescriber
- Flagged entries list (for HITL review)
- Audit record of extraction with confidence scores

### MCP Servers / External Tools

- `filesystem_server.py` — PDF ingestion staging
- Optional: `postgres_server.py` — formulary DB lookup for drug name normalisation

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — full PDF text + table extraction; handles mixed layout clinical docs
- `app/ingestion/chunkers/pdf_layout.py` — layout-aware chunking: preserves medication tables, section headers (e.g. "DISCHARGE MEDICATIONS"), footnote text
- `app/rag/parent_child_chunker.py` — parent = medication section block, child = individual medication line; enables targeted retrieval of a single drug entry without losing surrounding clinical context

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after initial extraction, the corrective RAG loop evaluates each extracted field's confidence against the source chunk. Fields scoring below the confidence threshold trigger a re-retrieval with a refined query (e.g. "dosage for metformin in this document") and a second extraction pass before flagging for HITL.

### Agent Pattern

Inline HITL gating via `app/governance/hitl.py` — not a separate pattern file; any extraction result containing `ambiguous=true` on dose, frequency, or route fields is routed to the HITL approval queue before the structured output is returned to the caller.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (required fields, type checking), PII detection on all extracted text, HIPAA REGULATED bundle enforces PHI redaction in logs and audit records

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the clinical PDF, extracting text from all pages including embedded tables and multi-column layouts.
2. **Layout Chunking**: `pdf_layout.py` identifies medication sections by heading patterns ("CURRENT MEDICATIONS", "DISCHARGE MEDICATIONS"). `parent_child_chunker.py` creates parent=section / child=line-item chunk pairs.
3. **Initial Extraction**: The agent queries each child chunk with a structured extraction prompt; gpt-4o returns a provisional JSON record per medication line.
4. **Corrective RAG Validation**: `corrective.py` evaluates each extracted field against the source chunk text. Fields with confidence < 0.85 trigger a refined re-retrieval + re-extraction pass.
5. **PII Detection**: `engine.py` runs PII detection (patient name, DOB, MRN) on all extracted text; PHI is redacted in logs per HIPAA REGULATED bundle; only anonymised records flow to downstream steps.
6. **HITL Gate**: Records with any field still `ambiguous=true` after the corrective pass are enqueued via `hitl.py`; the reviewing pharmacist either confirms the value, edits it, or rejects the record.
7. **Output**: Approved records are merged into the final structured medication list (JSON). Audit record written with confidence scores, correction history, and HITL decisions.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/parent_child_chunker.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Field-level recall ≥ 98% on a gold-standard clinical PDF test set
- Zero ambiguous fields silently passed through (HITL always triggered for ambiguous)
- PHI absent from all log records (HIPAA audit check)
- PII detection triggers on synthetic patient name + MRN in test documents

---

## UC54 — Create Patient Care Team Briefing from Admission Records

| Field | Value |
|-------|-------|
| **ID** | UC54 |
| **Title** | Create Patient Care Team Briefing from Admission Records |
| **Domain** | Healthcare |
| **Vertical / Industry** | Clinical / Hospital Ward |
| **Trigger** | Charge nurse or attending physician uploads admission record bundle and requests a care team briefing |
| **Actor / Persona** | Charge Nurse, Attending Physician, Care Coordinator |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `raptor` — hierarchical summarisation across multi-document admission bundle |
| **Agent Pattern** | Peer Review |
| **Compliance Bundle** | HIPAA REGULATED |
| **HITL Gate** | Nurse reviewer must sign off on clinical narrative before briefing is distributed |
| **Memory** | Episodic — scoped to patient encounter ID |

### Problem Statement

Admission record bundles (triage notes, lab results, imaging reports, prior discharge summaries) can span 30–80 pages. The care team needs a concise, clinically accurate briefing within minutes of admission — not after an hour of manual reading.

### Inputs

- Admission record PDF bundle (triage, labs, imaging reports, prior discharge summaries)
- Patient demographics (structured, from EHR integration or manual upload)

### Expected Output

- Care team briefing (1–2 pages): chief complaint, history, active medications, allergies, pending labs/imaging, care plan priorities, escalation triggers
- Flagged clinical concerns requiring immediate attention (highlighted)

### MCP Servers / External Tools

- `filesystem_server.py` — PDF bundle staging
- Optional: `postgres_server.py` — EHR patient record lookup

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — multi-document PDF parsing; handles varied clinical formats
- `app/ingestion/chunkers/pdf_layout.py` — section-aware chunking: preserves "Assessment", "Plan", "Medications", "Allergies" sections

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR clusters admission chunks across all documents into a hierarchy: document summaries → clinical themes (medications, labs, history, plan) → detail sentences. The agent queries at the theme level for the briefing structure, then retrieves detail chunks for specific sections.

### Agent Pattern

`app/agent/patterns/peer_review.py` — the primary agent drafts the briefing. A second "reviewer" agent (with a clinical accuracy checklist prompt) critiques: checks that all active medications are listed, allergies are present, and no contradictory information exists. Discrepancies trigger a revision pass.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection on all generated text; HIPAA REGULATED bundle enforces PHI redaction in telemetry and audit logs; output schema validation (required briefing sections)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the full admission bundle; `pdf_layout.py` identifies section types across all documents (triage, labs, imaging, prior records).
2. **RAPTOR Indexing**: `raptor.py` builds a cluster tree across the entire bundle: document-level summaries → clinical theme nodes (medications, allergies, history, plan) → leaf evidence sentences.
3. **Briefing Draft**: Primary agent queries RAPTOR theme nodes to populate each briefing section (chief complaint, history, medications, allergies, care plan priorities). gpt-5.2 generates the initial narrative.
4. **Peer Review**: `peer_review.py` reviewer agent checks: (a) all active medications listed and cross-referenced against admission notes, (b) allergy list complete, (c) no contradictions between documents, (d) escalation triggers are explicit.
5. **Revision Pass**: Any discrepancies flagged by the reviewer trigger a targeted re-retrieval from RAPTOR leaf nodes and a section rewrite.
6. **PII Detection & HITL**: `engine.py` runs PII detection; PHI redacted from telemetry. Briefing routed to charge nurse via `hitl.py` for clinical sign-off before distribution.
7. **Output**: Approved briefing (PDF + Markdown) stamped with reviewer name, timestamp, and HITL approval ID. Episodic memory stores encounter context for follow-up queries.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/peer_review.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All active medications from source documents present in briefing (recall = 100% on test set)
- Allergies section never omitted
- Peer review catches ≥ 90% of deliberately injected contradictions in test documents
- HITL nurse sign-off triggered before every briefing distribution
- PHI absent from telemetry/audit logs (HIPAA audit check)

---

## UC55 — Flag Scheduling Conflicts in Clinical Staff Calendar

| Field | Value |
|-------|-------|
| **ID** | UC55 |
| **Title** | Flag Scheduling Conflicts in Clinical Staff Calendar |
| **Domain** | Healthcare |
| **Vertical / Industry** | Clinical / Hospital Operations |
| **Trigger** | Scheduled nightly job or manual trigger by the scheduling coordinator |
| **Actor / Persona** | Scheduling Coordinator, Nurse Manager |
| **LLM** | gpt-4o-mini |
| **RAG Pattern** | `corrective_rag` — validates detected conflicts against scheduling policy rules |
| **Agent Pattern** | HITL gate for coverage gaps on critical roles |
| **Compliance Bundle** | Standard (no PHI in schedule data) |
| **HITL Gate** | Any uncovered shift on a critical role (ICU nurse, attending physician) escalated to Nurse Manager |
| **Memory** | Ephemeral — per scheduling run |

### Problem Statement

Clinical staff schedules span multiple wards, shift types, and certification requirements. Conflicts (double-bookings, mandatory rest violations, uncovered critical roles) must be detected and escalated before the shift begins — not discovered at handover.

### Inputs

- Staff calendar data (iCal export or calendar API feed)
- Shift coverage rules and minimum staffing requirements (Postgres table or PDF policy)
- Staff certification records (Postgres)

### Expected Output

- Conflict report: type of conflict · affected staff · shift date/time · severity
- Coverage gap report: uncovered critical roles with escalation priority
- Recommended resolution options per conflict

### MCP Servers / External Tools

- Calendar API connector — fetches staff schedule events
- `postgres_server.py` — queries shift rules, minimum staffing requirements, certification records

### Ingestion Pipeline

- No PDF ingestion required; data is structured from calendar API + Postgres
- If scheduling policy is in PDF form: `app/ingestion/parsers/pdf_parser.py` + `app/ingestion/chunkers/pdf_layout.py` for rule extraction

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after the agent detects a candidate conflict, corrective RAG retrieves the relevant scheduling policy clause to confirm that a rule violation actually applies (e.g. "is 10-hour rest required between night and day shifts for this role?"). Low-confidence policy matches trigger a second retrieval pass.

### Agent Pattern

Inline HITL gating via `app/governance/hitl.py` — conflicts classified as "critical coverage gap" (uncovered ICU slot, no attending on ward) are escalated to the Nurse Manager approval queue for manual resolution before the shift.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — output schema validation (all required fields per conflict record), no PHI in output (staff names treated as internal PII, redacted in external logs)

### End-to-End Steps

1. **Data Fetch**: Calendar API connector retrieves all staff shift events for the upcoming 48-hour window. `postgres_server.py` queries shift rules, minimum staffing levels per ward/role, and staff certification records.
2. **Conflict Detection**: Agent performs pairwise overlap detection (double-bookings) and time-distance checks (rest period violations) across all staff shift pairs.
3. **Policy Validation via Corrective RAG**: For each candidate conflict, `corrective.py` retrieves the relevant scheduling policy clause. If the initial retrieval confidence is low, a refined query is re-issued against the policy corpus.
4. **Coverage Gap Analysis**: Agent queries Postgres for each critical role slot in the 48-hour window and checks it against confirmed staff assignments. Uncovered slots are ranked by criticality (ICU > Ward > Admin).
5. **HITL Escalation**: Any critical coverage gap (ICU nurse, attending physician) is enqueued via `hitl.py` for Nurse Manager resolution. Non-critical conflicts are flagged in the report but do not block.
6. **Resolution Suggestions**: For each conflict/gap, the agent generates 2–3 resolution options (e.g. swap staff, extend shift, call on-call roster) using available certification and availability data.
7. **Output**: `engine.py` validates schema. Final conflict + coverage gap report returned; critical gaps remain in HITL queue until resolved.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py` (if policy is PDF)
- `app/ingestion/chunkers/pdf_layout.py` (if policy is PDF)
- `app/rag/agentic/patterns/corrective.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All double-bookings detected on a 200-staff synthetic schedule (recall = 100%)
- Rest period violations detected with < 2% false positive rate
- HITL triggered for every critical coverage gap
- Policy validation reduces false positives by ≥ 30% vs. rule-only detection

---

## UC56 — Summarize Clinical Trial Eligibility Criteria

| Field | Value |
|-------|-------|
| **ID** | UC56 |
| **Title** | Summarize Clinical Trial Eligibility Criteria |
| **Domain** | Healthcare |
| **Vertical / Industry** | Clinical Research / Pharma |
| **Trigger** | Research coordinator uploads clinical trial protocol PDF and requests an eligibility summary |
| **Actor / Persona** | Clinical Research Coordinator, Principal Investigator |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `colbert` reranking — fine-grained passage ranking for precise eligibility clause retrieval |
| **Agent Pattern** | Peer Review |
| **Compliance Bundle** | HIPAA REGULATED |
| **HITL Gate** | Ambiguous inclusion/exclusion criteria escalated to Principal Investigator |
| **Memory** | None — stateless per protocol |

### Problem Statement

Clinical trial protocols are dense legal-scientific documents (50–300 pages). Eligibility criteria — inclusion, exclusion, washout periods, co-medication restrictions — are distributed across multiple sections. Research coordinators need a precise, structured summary to screen patients quickly and accurately.

### Inputs

- Clinical trial protocol PDF (50–300 pages)
- Optional: existing patient cohort data for screening pre-check

### Expected Output

- Structured eligibility summary: inclusion criteria list · exclusion criteria list · washout periods · co-medication restrictions · age/demographic requirements
- Ambiguity flags list (criteria requiring PI clarification)
- Patient screening checklist (Markdown)

### MCP Servers / External Tools

- `filesystem_server.py` — protocol PDF staging
- Optional: `postgres_server.py` — patient cohort pre-screening

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — full protocol PDF parsing; handles numbered sections, complex tables, cross-references
- `app/ingestion/chunkers/pdf_layout.py` — layout-aware chunking preserving section numbering and table structure for criteria tables
- `app/rag/parent_child_chunker.py` — parent = eligibility section (e.g. "5.1 Inclusion Criteria"), child = individual criterion; enables single-criterion retrieval with full section context

### RAG Pattern

`app/rag/agentic/patterns/colbert.py` — ColBERT late-interaction reranking applied after initial vector retrieval. ColBERT's token-level matching precisely scores passages containing eligibility criterion language (e.g. "HbA1c ≥ 7.5%", "no prior SGLT2 inhibitor use") against the query, reducing false-positive retrievals from dense scientific prose.

### Agent Pattern

`app/agent/patterns/peer_review.py` — primary agent drafts the structured eligibility summary. Reviewer agent (with a clinical trial screening checklist prompt) checks: (a) all numbered criteria extracted, (b) no contradiction between inclusion/exclusion criteria, (c) quantitative thresholds captured exactly as stated (not paraphrased), (d) cross-references resolved.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection on output (patient data must not appear in protocol summaries), output schema validation (required sections), HIPAA REGULATED bundle

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the full protocol PDF. `pdf_layout.py` identifies eligibility sections by heading patterns ("Inclusion Criteria", "Exclusion Criteria", "Section 5").
2. **Parent-Child Indexing**: `parent_child_chunker.py` creates parent=eligibility section / child=individual criterion chunk pairs. Both levels indexed in the vector store.
3. **ColBERT Retrieval**: `colbert.py` performs initial vector retrieval of all eligibility-related passages, then applies late-interaction reranking to surface the most precisely relevant criterion sentences.
4. **Initial Summary Draft**: gpt-5.2 structures the retrieved criteria into inclusion/exclusion lists, extracts quantitative thresholds verbatim, and notes washout periods and co-medication restrictions.
5. **Peer Review**: `peer_review.py` reviewer agent checks criterion count against the protocol's own table of contents, flags any criterion with ambiguous language ("significant renal impairment" without numeric definition), and validates quantitative thresholds against source text.
6. **HITL Gate**: Criteria flagged as ambiguous are enqueued via `hitl.py` for Principal Investigator clarification. PI adds a note or explicit interpretation for each flagged item.
7. **Output**: `engine.py` validates schema. Final structured eligibility summary + patient screening checklist returned. PII detection run on all output text.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/parent_child_chunker.py`
- `app/rag/agentic/patterns/colbert.py`
- `app/agent/patterns/peer_review.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- All numbered inclusion criteria extracted (recall = 100% on a gold-standard protocol test set)
- Quantitative thresholds reproduced verbatim (no paraphrasing)
- Peer review catches ≥ 95% of deliberately ambiguous criteria injected into test protocols
- HITL triggered for every ambiguous criterion
- PII detection finds zero patient data in output

---

## UC57 — Extract Insurance Claim Fields from Scanned Forms

| Field | Value |
|-------|-------|
| **ID** | UC57 |
| **Title** | Extract Insurance Claim Fields from Scanned Forms |
| **Domain** | Insurance |
| **Vertical / Industry** | Property & Casualty Insurance |
| **Trigger** | Claims processor uploads a batch of scanned claim forms (TIFF/PDF) |
| **Actor / Persona** | Claims Processor, Claims Adjuster |
| **LLM** | gpt-4o (vision-enabled) |
| **RAG Pattern** | `corrective_rag` — low-confidence OCR fields trigger re-retrieval and re-extraction |
| **Agent Pattern** | HITL gate for fields below confidence threshold |
| **Compliance Bundle** | Standard + PII Detection |
| **HITL Gate** | Any field with confidence < 0.80 or detected as conflicting routes to human review |
| **Memory** | None — stateless per claim |

### Problem Statement

Scanned claim forms (handwritten or typed) arrive in varied formats — ACORD forms, proprietary carrier forms, faxed documents. OCR errors, low-resolution scans, and handwriting variation produce unreliable extractions. Every misread field is a downstream liability.

### Inputs

- Scanned claim form images (TIFF, JPEG) or scanned PDF
- Claim field schema (required fields list per form type)

### Expected Output

- Structured claim data (JSON): claimant name · policy number · incident date · incident description · claimed amount · supporting evidence list · signature presence
- Confidence score per field
- HITL queue entries for low-confidence fields

### MCP Servers / External Tools

- `filesystem_server.py` — image/PDF staging
- Optional: `postgres_server.py` — policy number validation lookup

### Ingestion Pipeline

- `app/ingestion/parsers/vision_parser.py` — GPT-4o vision model extracts text, tables, checkboxes, and handwritten content from scanned images; performs region detection (header, body fields, signature block, supporting docs section); returns structured region map

### RAG Pattern

`app/rag/agentic/patterns/corrective.py` — after initial extraction, each field is evaluated against the source image region. Fields with confidence < threshold trigger a corrective pass: the region is re-cropped, the query is refined (e.g. "read the handwritten dollar amount in the 'claimed amount' box"), and a second extraction is attempted before HITL flagging.

### Agent Pattern

Inline HITL gating via `app/governance/hitl.py` — fields still below confidence after the corrective pass, or fields where extracted values conflict with each other (e.g. incident date after claim submission date), are routed to the claims processor review queue.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection on all extracted fields (claimant name, policy number, address, phone number); output schema validation; PII tagged and handled per data retention policy

### End-to-End Steps

1. **Ingest**: `vision_parser.py` receives the scanned form image/PDF. GPT-4o vision performs region detection, identifying form header, field boxes, checkboxes, signature area, and attachment section.
2. **Initial Extraction**: `vision_parser.py` extracts text from each detected region, returning a provisional field-value map with per-field confidence scores.
3. **Corrective RAG Pass**: `corrective.py` evaluates each extracted field. Fields below confidence threshold (0.80) trigger a re-cropped region query with a refined extraction prompt. A second extraction pass is performed.
4. **Cross-Field Validation**: Agent checks logical consistency between fields (incident date ≤ claim date, claimed amount is numeric, policy number format matches schema). Inconsistencies are flagged as conflicts.
5. **PII Detection**: `engine.py` runs PII detection on all extracted field values. Claimant PII is tagged; downstream systems receive redacted versions for processing logs.
6. **HITL Gate**: Fields still low-confidence or conflicting after corrective pass are enqueued via `hitl.py`. The claims processor reviews the original image region alongside the extracted value and either confirms, corrects, or marks as unreadable.
7. **Output**: Approved field values merged into the final structured claim JSON. Confidence scores and HITL decisions included in audit metadata.

### Real App/ Files Cited

- `app/ingestion/parsers/vision_parser.py`
- `app/rag/agentic/patterns/corrective.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Field-level extraction accuracy ≥ 95% on a gold-standard scanned form test set
- Confidence calibration: predicted high-confidence fields correct ≥ 98% of the time
- HITL triggered for all fields below threshold
- PII detection captures claimant name, policy number, and address on all test forms
- Zero conflicting fields silently passed through

---

## UC58 — Assess Insurance Claim Against Policy Terms

| Field | Value |
|-------|-------|
| **ID** | UC58 |
| **Title** | Assess Insurance Claim Against Policy Terms |
| **Domain** | Insurance |
| **Vertical / Industry** | Property & Casualty Insurance |
| **Trigger** | Claims adjuster submits a claim file (scanned form + policy PDF) for coverage assessment |
| **Actor / Persona** | Claims Adjuster, Senior Claims Examiner |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `colbert` reranking on policy terms + `parent_child_chunker` for context expansion |
| **Agent Pattern** | Self-Consistency (3 independent assessments → majority verdict) |
| **Compliance Bundle** | Standard + PII Detection |
| **HITL Gate** | Borderline or disputed coverage determinations escalated to Senior Claims Examiner |
| **Memory** | Episodic — scoped to claim ID |

### Problem Statement

Policy documents are dense legal text with exclusions, sub-limits, endorsements, and conditions. Determining whether a specific claim is covered requires precise clause retrieval and multi-angle reasoning — a single LLM pass is insufficient for disputed or borderline cases.

### Inputs

- Insurance policy PDF (master policy + endorsements)
- Scanned claim form (image or PDF) with extracted field data (from UC57)
- Claim narrative (free text from adjuster notes)

### Expected Output

- Coverage determination: Covered / Partially Covered / Not Covered / Borderline (HITL)
- Supporting policy clauses cited (with page + section references)
- Exclusion analysis: which exclusions were evaluated and why they do/don't apply
- Recommended next actions for the adjuster

### MCP Servers / External Tools

- `filesystem_server.py` — policy PDF + claim document staging
- Optional: `postgres_server.py` — claim history and policy endorsement lookup

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — policy PDF parsing (handles long-form legal documents, tables of contents, endorsement riders)
- `app/ingestion/parsers/vision_parser.py` — claim form image parsing (GPT-4o vision extracts damage descriptions, amounts, dates from scanned form)
- `app/rag/parent_child_chunker.py` — parent = policy section (e.g. "Section 4 — Property Coverage"), child = individual clause; ColBERT retrieves child clauses, parent sections surfaced for full context

### RAG Pattern

`app/rag/agentic/patterns/colbert.py` — ColBERT late-interaction reranking applied to policy clause retrieval. Token-level matching precisely scores legal clause language (e.g. "sudden and accidental", "wear and tear exclusion") against the claim description, dramatically reducing false-positive policy clause matches. Parent-child expansion via `parent_child_chunker.py` ensures the full policy section context is available alongside the retrieved clause.

### Agent Pattern

`app/agent/patterns/self_consistency.py` — the claim is assessed three times independently by three parallel agent instances (each with a different "lens": coverage maximisation, exclusion focus, sub-limit analysis). The three verdicts are compared; if 2/3 agree, the majority verdict is adopted. If all three disagree or the verdict is "Borderline", HITL is triggered.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection on claim narrative and output; output schema validation (verdict, supporting clauses, exclusion analysis all required)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses the policy PDF including endorsements. `vision_parser.py` extracts claim form fields and damage narrative. Claim adjuster notes ingested as plain text.
2. **Policy Indexing**: `parent_child_chunker.py` creates parent=section / child=clause chunk pairs for the policy. ColBERT index built over child clauses.
3. **Claim Summarisation**: Agent synthesises a claim summary from vision-extracted fields + adjuster notes for use as the retrieval query.
4. **ColBERT Clause Retrieval**: `colbert.py` retrieves and reranks the top-k most relevant policy clauses for the claim. Parent sections expanded to provide full definitional context.
5. **Self-Consistency Assessment**: `self_consistency.py` runs three parallel assessments — (A) coverage-forward analysis, (B) exclusion-focused analysis, (C) sub-limit and conditions analysis. Each returns a verdict and supporting clause citations.
6. **Majority Verdict**: If 2/3 agree, the majority verdict is adopted with the union of their supporting evidence. If all three disagree or verdict is Borderline, `hitl.py` routes to Senior Claims Examiner with all three assessments displayed side by side.
7. **Output**: `engine.py` validates schema. Final determination report (verdict + clause citations + exclusion analysis) returned. Episodic memory stores the assessment for claim lifecycle tracking.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/parsers/vision_parser.py`
- `app/rag/parent_child_chunker.py`
- `app/rag/agentic/patterns/colbert.py`
- `app/agent/patterns/self_consistency.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Coverage determination matches ground truth on 200 adjudicated historical claims (accuracy ≥ 92%)
- All borderline cases (ground truth = "disputed") trigger HITL
- ColBERT reranking reduces false-positive clause matches by ≥ 40% vs. vector-only retrieval
- Self-consistency majority vote improves accuracy by ≥ 5% vs. single-pass assessment

---

## UC59 — Generate Underwriting Summary from Applicant Docs

| Field | Value |
|-------|-------|
| **ID** | UC59 |
| **Title** | Generate Underwriting Summary from Applicant Docs |
| **Domain** | Insurance |
| **Vertical / Industry** | Life / Commercial Insurance |
| **Trigger** | Underwriter uploads applicant document package and requests a summary for underwriting decision |
| **Actor / Persona** | Underwriter, Senior Underwriter |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `raptor` — hierarchical summarisation across multi-document applicant package |
| **Agent Pattern** | Peer Review |
| **Compliance Bundle** | Standard + PII Detection |
| **HITL Gate** | High-risk applicants (risk score ≥ threshold) escalated to Senior Underwriter |
| **Memory** | Episodic — scoped to application ID |

### Problem Statement

Underwriting packages contain financial statements, medical reports, property appraisals, and prior insurance history — often 50–150 pages across multiple files. Underwriters need a concise, risk-stratified summary with specific risk factors called out before making a coverage and pricing decision.

### Inputs

- Applicant document package (multiple PDFs): financial statements, medical/health reports, property inspection report, prior claims history, application form
- Underwriting guidelines PDF (product-specific)

### Expected Output

- Underwriting summary: applicant profile · financial risk indicators · health/property risk factors · prior claims history · coverage recommendation · suggested premium tier
- Risk score (numerical, derived from extracted factors)
- Supporting evidence citations per risk factor

### MCP Servers / External Tools

- `filesystem_server.py` — multi-document PDF staging
- Optional: `postgres_server.py` — prior claims history lookup

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses each document in the applicant package; handles financial tables, medical report formats, inspection checklists
- `app/ingestion/chunkers/pdf_layout.py` — layout-aware chunking: preserves financial tables, risk factor sections, summary paragraphs

### RAG Pattern

`app/rag/agentic/patterns/raptor.py` — RAPTOR builds a cluster tree across the entire applicant document package: document summaries → risk theme nodes (financial risk, health risk, property risk, claims history) → detail evidence sentences. The agent queries at the risk-theme level to populate each summary section, then drills into evidence leaves for supporting citations.

### Agent Pattern

`app/agent/patterns/peer_review.py` — primary agent drafts the underwriting summary. Reviewer agent (with an underwriting checklist prompt) validates: (a) all required risk factors addressed, (b) risk score calculation traceable to source evidence, (c) no contradictions between documents, (d) coverage recommendation consistent with underwriting guidelines.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection on applicant data; output schema validation (all summary sections required, risk score within valid range)

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses each document in the applicant package. `pdf_layout.py` identifies and preserves financial tables, risk indicator sections, and inspection checklists across all documents.
2. **RAPTOR Indexing**: `raptor.py` builds a cluster tree over all applicant documents: document summaries → risk theme nodes (financial, health/property, claims history) → detail evidence sentences.
3. **Risk Factor Extraction**: Agent queries RAPTOR theme nodes for each risk category. gpt-5.2 extracts specific risk indicators (e.g. debt-to-income ratio, prior claim frequency, medical condition flags) with source citations.
4. **Risk Scoring**: Agent computes a numerical risk score by weighting extracted risk factors against underwriting guidelines. Score bands map to coverage recommendations and premium tiers.
5. **Peer Review**: `peer_review.py` reviewer agent checks: all risk factors addressed, risk score traceable to evidence, coverage recommendation consistent with guidelines, no document contradictions. Discrepancies trigger a targeted revision pass.
6. **HITL Gate**: Applications with risk score ≥ high-risk threshold are enqueued via `hitl.py` for Senior Underwriter review before any coverage decision is issued.
7. **Output**: `engine.py` validates schema. Final underwriting summary (PDF + JSON) with risk score, factor evidence, and coverage recommendation returned. Episodic memory stores for policy lifecycle tracking.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/agentic/patterns/raptor.py`
- `app/agent/patterns/peer_review.py`
- `app/governance/hitl.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Risk score within ± 10% of manually computed score on a gold-standard applicant test set
- All high-risk applicants (ground truth) trigger HITL
- Peer review catches ≥ 90% of deliberately missing risk factors in test packages
- Every risk factor in the summary has a traceable source citation

---

## UC60 — Analyze Loan Application for Credit Assessment

| Field | Value |
|-------|-------|
| **ID** | UC60 |
| **Title** | Analyze Loan Application for Credit Assessment |
| **Domain** | Banking |
| **Vertical / Industry** | Retail / Commercial Banking |
| **Trigger** | Credit analyst submits a loan application package for automated credit assessment |
| **Actor / Persona** | Credit Analyst, Senior Credit Officer |
| **LLM** | gpt-5.2 |
| **RAG Pattern** | `parent_child_chunker` — financial statement sections retrieved at clause level, full statement surfaced for context |
| **Agent Pattern** | Self-Consistency (3 independent assessments → majority verdict) |
| **Compliance Bundle** | REGULATED (financial data) + PII Detection + Full Audit Trail |
| **HITL Gate** | Borderline credit cases (score within ± 5% of approval threshold) escalated to Senior Credit Officer |
| **Memory** | Episodic — scoped to application ID |

### Problem Statement

Credit decisions on borderline applications carry significant financial and regulatory risk. A single LLM pass is insufficient — parallel independent assessments from multiple angles (income stability, debt coverage, collateral valuation) reduce variance and improve defensibility of the decision.

### Inputs

- Loan application PDF (application form, financial statements, tax returns, bank statements)
- Credit bureau report (structured JSON or PDF)
- Collateral valuation report (PDF, if applicable)
- Internal credit policy document (PDF)

### Expected Output

- Credit assessment report: applicant summary · income analysis · debt service coverage · credit history · collateral assessment · credit score · recommendation (Approve / Decline / Borderline / Conditional)
- Supporting evidence per assessment dimension with source citations
- Full audit trail of assessment process

### MCP Servers / External Tools

- `filesystem_server.py` — document package staging
- `postgres_server.py` — existing customer relationship data, internal credit policy lookup

### Ingestion Pipeline

- `app/ingestion/parsers/pdf_parser.py` — parses financial statements, tax returns, bank statements, and credit policy PDF; handles financial tables and multi-year comparative formats
- `app/ingestion/chunkers/pdf_layout.py` — layout-aware chunking preserving financial table structure and year-over-year comparison rows
- `app/rag/parent_child_chunker.py` — parent = financial statement section (Income Statement, Balance Sheet), child = individual line item or footnote; enables targeted retrieval of specific financial metrics

### RAG Pattern

`app/rag/parent_child_chunker.py` — used as the primary retrieval architecture. Child-level chunks (individual financial line items) are retrieved for specific metric queries (e.g. "EBITDA for 2023", "total outstanding debt"). Parent sections are expanded to provide surrounding context for the full financial picture.

### Agent Pattern

`app/agent/patterns/self_consistency.py` — three parallel credit assessment agents each focus on a distinct dimension: (A) Income & Cash Flow Analysis, (B) Debt Burden & Coverage Ratios, (C) Credit History & Collateral. Each returns a credit sub-score and recommendation. Sub-scores are aggregated into a composite credit score; if 2/3 recommend the same outcome, that verdict is adopted. Borderline cases (consensus not reached, or score within threshold band) trigger HITL.

### Guardrails & Compliance

- `app/guardrails_v2/engine.py` — PII detection (applicant name, SSN/TIN, account numbers); REGULATED bundle compliance; output schema validation (all assessment dimensions required)
- `app/governance/audit.py` — full append-only audit trail of every assessment step, agent verdict, and HITL decision for regulatory examination

### End-to-End Steps

1. **Ingest**: `pdf_parser.py` parses all documents in the loan package. `pdf_layout.py` preserves financial table structure. `parent_child_chunker.py` creates parent=financial statement section / child=line item chunk pairs.
2. **Credit Policy Indexing**: Credit policy PDF similarly chunked and indexed; forms the guardrail reference for all three assessment agents.
3. **Self-Consistency Assessment Launch**: `self_consistency.py` spawns three parallel credit assessment agents — Income/Cash Flow, Debt/Coverage, Credit History/Collateral. Each queries the parent-child index independently.
4. **Individual Assessments**: Each agent retrieves relevant financial metrics, cross-references with credit policy, and produces a sub-score + recommendation with supporting evidence citations.
5. **Score Aggregation & Majority Verdict**: Sub-scores aggregated into composite credit score. 2/3 majority verdict adopted. If all three disagree, or composite score is within ± 5% of approval threshold, Borderline classification is assigned.
6. **HITL Gate**: Borderline cases are routed via `hitl.py` to the Senior Credit Officer, who reviews all three individual assessments side by side before issuing a final decision.
7. **Audit Trail & Output**: Every step — ingest, assessment, HITL decision — is written to the append-only audit trail via `audit.py`. `engine.py` validates output schema. Final credit assessment report returned with full evidence trail.

### Real App/ Files Cited

- `app/ingestion/parsers/pdf_parser.py`
- `app/ingestion/chunkers/pdf_layout.py`
- `app/rag/parent_child_chunker.py`
- `app/agent/patterns/self_consistency.py`
- `app/governance/hitl.py`
- `app/governance/audit.py`
- `app/guardrails_v2/engine.py`

### Success Criteria / Acceptance Tests

- Credit recommendation matches ground truth on 500 historical loan applications (accuracy ≥ 88%)
- All borderline cases trigger HITL (zero borderline cases auto-decided)
- Full audit trail written for 100% of assessments
- PII detection identifies SSN/TIN and account numbers in all test documents
- Self-consistency: three-agent variance lower than single-agent on borderline test cases

---

*End of use-cases-51-60.md*
