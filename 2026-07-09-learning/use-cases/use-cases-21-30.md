# Use Cases 21–30: Compliance/Audit, Legal/Policy Review, Customer Support

> **Code-grounded use cases** — every file path, class name, strategy key, and tool connector
> refers to a real object in `agent-verse-backend/`. Costs are illustrative at 2026 pricing.

---

## Category: Compliance / Audit (UC21–UC24)

---

### UC21 — Audit Jira Tickets for GDPR Data Retention Violations

| Field | Value |
|---|---|
| **Use Case ID** | UC21 |
| **Category** | Compliance / Audit |
| **Business Goal** | Scan every open and recently-closed Jira ticket in the privacy backlog, detect descriptions/comments that reference personal data with no documented retention schedule, flag confirmed violations, and produce a SOC2-ready audit trail. |
| **MCP Servers** | `app/mcp/servers/jira_server.py` — `jira_search_issues` (bulk JQL export), `jira_get_issue` (per-ticket detail fetch), `jira_add_comment` (violation annotation) |
| **Ingestion / Chunking** | Ticket bodies and comments treated as plain-text documents; no PDF parser needed — chunked inline before RAG |
| **RAG Strategy** | `hybrid` (dense pgvector + BM25 trigram) against the GDPR policy knowledge base for precise policy clause retrieval |
| **Guardrails** | `app/guardrails_v2/engine.py` — `GuardrailsEngine` PII detection (`_PII_PATTERNS`: SSN, email, phone, credit card regexes) scans every ticket body before LLM processes it; `app/security_runtime/guardrail_enforcer.py` — `GuardrailEnforcer.check_tool_args` wraps every `jira_add_comment` call to prevent PII leakage into ticket comments |
| **Agent Pattern** | Standard ReAct loop — plan → classify → flag; no multi-agent overhead; low tier is sufficient |
| **LLM Model** | `gpt-4o-mini` (low tier) — cost-optimised classification at ~$0.004 per ticket |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` triggered for any ticket classified `CONFIRMED_VIOLATION` with a retention period of 0 days (data should already have been deleted); human reviewer must approve before `jira_add_comment` posts the violation flag |
| **Audit Trail** | `app/governance/audit.py` — `AuditLog.record(AuditEvent(...))` writes one immutable record per ticket evaluated; fields: `goal_id`, `tool_name="jira_search_issues"`, `action_level`, `outcome`, `connector_id="jira"`, `api_key_id` |
| **Cost Estimate** | ~$0.004/ticket × 2,000 tickets = **$8 per full audit run** |

#### End-to-End Steps

**Step 1 — Bulk ticket export via JQL**
Agent calls `jira_search_issues` (`app/mcp/servers/jira_server.py`) with JQL:
`project = PRIVACY AND updated >= -90d ORDER BY updated DESC`
`max_results=100`, paginated until all tickets are fetched.
Each `jira_get_issue` call retrieves `description`, `comments`, `customfield_retention_period`,
`customfield_data_category`, and `labels`.

**Step 2 — PII pre-scan on raw ticket content**
Before the LLM sees any ticket body, `GuardrailsEngine.evaluate_content()`
(`app/guardrails_v2/engine.py:47`) runs all `_PII_PATTERNS` against the raw text.
Any ticket whose description contains a live email, phone, or SSN is immediately redacted
(`EnforcementResult.redacted_content`) so the LLM never processes cleartext PII.

**Step 3 — Hybrid RAG for GDPR policy context**
The agent issues a `hybrid` retrieval query against the GDPR policy knowledge base
(dense pgvector cosine + BM25 trigram, re-ranked by RRF).
Retrieved clauses (Article 5, Article 17 Right to Erasure, Recital 39) are injected into
the classifier prompt as grounding context.

**Step 4 — LLM classification per ticket (low tier, `gpt-4o-mini`)**
For each ticket, the LLM is asked: "Does this ticket reference personal data without a
documented retention schedule compliant with the retrieved GDPR clauses?"
Output is a structured JSON: `{ "verdict": "VIOLATION|COMPLIANT|UNCLEAR", "article": "...", "severity": "HIGH|MED|LOW", "rationale": "..." }`.

**Step 5 — HITL gate for confirmed HIGH-severity violations**
Tickets classified `VIOLATION` + `severity=HIGH` are queued via
`HITLGateway.request_approval()` (`app/governance/hitl.py`).
The approval payload includes: ticket key, violated article, data category, days since creation.
On human approval, the agent calls `jira_add_comment` with the structured violation report
and sets label `gdpr-violation`.
On human rejection (false positive), the verdict is logged as `CLEARED` in the audit trail.

**Step 6 — Immutable audit trail**
Every ticket evaluation — compliant or violated — is recorded by
`AuditLog.record(AuditEvent(...))` (`app/governance/audit.py:58`).
Fields include `outcome` (classification verdict), `approver` (HITL reviewer ID),
`connector_id="jira"`, and `request_id` for SOC2 traceability.
Final summary is exported as a JSON compliance report via `AuditLog.query_range()`.

**Expected Output**
Jira dashboard label `gdpr-violation` on all flagged tickets, structured JSON audit report
(`gdpr_audit_YYYYMMDD.json`) with per-ticket verdicts, violation articles, and HITL decisions.

---

### UC22 — Review Vendor Contract Against Compliance Policy

| Field | Value |
|---|---|
| **Use Case ID** | UC22 |
| **Category** | Compliance / Audit |
| **Business Goal** | Ingest a vendor MSA/DPA PDF, identify every clause that deviates from internal compliance policy (SOC2, ISO 27001, GDPR), classify risk, and route high-risk clauses for legal review. |
| **MCP Servers** | None — document supplied as upload; `app/mcp/servers/confluence_server.py` used to retrieve internal policy pages via `confluence_search` and `confluence_get_page` |
| **Ingestion / Chunking** | `app/ingestion/parsers/pdf_parser.py` — `PDFParser.parse_bytes()` → `PDFParseResult.to_chunks()` (preserves page numbers); then `app/rag/parent_child_chunker.py` — `ParentChildChunker` (parent 1500 chars / child 400 chars, 50 char overlap) for high-precision clause retrieval with full-context return |
| **RAG Strategy** | `corrective_rag` — initial retrieval against compliance policy KB; if confidence score < 0.6, re-queries with expanded search terms before generating risk classification |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — `REGULATED` bundle; `check_output()` blocks any LLM output that reproduces verbatim confidential contract text into logs |
| **Agent Pattern** | `app/agent/patterns/peer_review.py` — `PeerReviewPattern`; primary agent classifies risk; reviewer agent (same model, different prompt) scores accuracy (0.0–1.0) and flags under-identified risks; output is only accepted when `approved=True` (score ≥ 0.7) |
| **LLM Model** | `gpt-5.2` (high tier) — long-context contract analysis; 128k context window handles full MSAs |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` for any clause classified `HIGH_RISK`; legal reviewer sees clause text, peer review critique, and remediation suggestion before approval |
| **Audit Trail** | `app/governance/audit.py` — records PDF ingestion event, each RAG query, each clause classification, peer review score, and HITL decision per clause |
| **Cost Estimate** | ~$0.18 per 100-page contract (PDF tokens + peer review double-pass) |

#### End-to-End Steps

**Step 1 — PDF ingestion and parent-child chunking**
`PDFParser.parse_bytes(pdf_bytes, source_name="vendor_contract.pdf")`
(`app/ingestion/parsers/pdf_parser.py:48`) extracts text with page numbers into
`PDFParseResult`. Each `PDFPage.content` block is passed to
`ParentChildChunker.chunk(content, document_id="vendor_contract")`
(`app/rag/parent_child_chunker.py:50`) which creates 1500-char parent chunks each
containing ~3–4 child chunks of 400 chars with 50-char overlap.
Only child chunks are indexed for retrieval; parent chunks are stored for context return.

**Step 2 — Policy KB retrieval via Confluence**
Agent calls `confluence_search` (`app/mcp/servers/confluence_server.py`)
with CQL `space = COMPLIANCE AND type = page AND title ~ "vendor requirements"`
to load the active SOC2, ISO 27001, and GDPR DPA policy pages.
Policy pages are chunked and loaded into the session's working context.

**Step 3 — Corrective RAG clause analysis**
For each contract section heading, `corrective_rag` retrieves the most relevant
policy clauses (top-5 by cosine). If the top result has confidence < 0.6, the strategy
automatically expands the query (adds synonyms: "data processing" → "personal data
processing agreement") and retries before proceeding.
Retrieved policy context is appended to the clause analysis prompt.

**Step 4 — Risk classification by primary agent**
LLM (gpt-5.2) outputs per-clause JSON:
`{ "clause_id": "...", "risk_level": "HIGH|MED|LOW|OK", "policy_violated": "...", "deviation": "...", "remediation": "..." }`.
HIGH risk conditions: unlimited liability waiver, no data deletion obligations,
no breach notification SLA, uncapped sub-processor rights.

**Step 5 — Peer review pass**
`PeerReviewPattern` (`app/agent/patterns/peer_review.py`) sends the full risk classification
JSON to the reviewer LLM. Reviewer evaluates `accuracy` (are the cited policy references
correct?) and `completeness` (were any deviations missed?).
`PeerReviewResult.quality_score < 0.7` triggers a re-analysis of the flagged clauses.

**Step 6 — HITL for HIGH-risk clauses**
Each `HIGH_RISK` clause is submitted to `HITLGateway.request_approval()`
(`app/governance/hitl.py`) with a structured approval payload:
`{ clause_text, risk_level, peer_review_critique, suggested_redline }`.
Legal reviewer approves (adds to redline list) or rejects (marks as accepted risk).
`AuditLog.record()` captures the approver identity and timestamp per clause decision.

**Expected Output**
Risk-ranked clause table (markdown), redline suggestion document, HITL approval log,
Confluence page created via `confluence_create_page` summarising findings.

---

### UC23 — Generate HIPAA Access Control Compliance Report

| Field | Value |
|---|---|
| **Use Case ID** | UC23 |
| **Category** | Compliance / Audit |
| **Business Goal** | Query the production database access logs and HR system to identify every user who accessed PHI (Protected Health Information) resources, verify each access was role-appropriate under HIPAA Minimum Necessary Standard, and produce a signed compliance report. |
| **MCP Servers** | `app/mcp/servers/postgres_server.py` — `postgres_query` for access log tables (`phi_access_log`, `user_roles`, `resource_sensitivity`) and HR data (`employees`, `role_assignments`, `termination_dates`) |
| **Ingestion / Chunking** | SQL result sets returned as structured data; HIPAA policy PDF ingested via `app/ingestion/parsers/pdf_parser.py` |
| **RAG Strategy** | `hybrid` retrieval against HIPAA policy KB (dense + BM25) for "Minimum Necessary" rule, role-based access control requirements, and audit record retention (6-year rule) |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — COMPLIANCE bundle; `check_output()` redacts any PHI that appears in LLM output before writing to report |
| **Agent Pattern** | `app/agent/patterns/self_consistency.py` — `SelfConsistencyPattern(n_samples=3, temperature=0.4)` — three independent passes over each access record classification; `_most_common()` returns majority-vote verdict to eliminate single-pass hallucination risk |
| **LLM Model** | `gpt-4o` (standard tier) — structured data analysis; 3-pass self-consistency at moderate temperature |
| **HITL** | Not required for report generation; HITL triggered only if access anomaly score exceeds threshold (>50 anomalous accesses by a single user in 24h) |
| **Audit Trail** | `app/governance/audit.py` — `AuditLog.record()` for every SQL query executed, every self-consistency vote, and the final report generation event; `api_key_id` and `ip_address` fields populated for SOC2 log integrity |
| **Cost Estimate** | ~$0.12 per 500-user access report (3× self-consistency passes) |

#### End-to-End Steps

**Step 1 — Access log extraction**
Agent calls `postgres_query` (`app/mcp/servers/postgres_server.py`) with:
```sql
SELECT u.user_id, u.name, u.department, pal.resource_id, rs.sensitivity_level,
       pal.access_time, pal.access_type, ur.hipaa_role
FROM phi_access_log pal
JOIN users u ON u.user_id = pal.user_id
JOIN resource_sensitivity rs ON rs.resource_id = pal.resource_id
JOIN user_roles ur ON ur.user_id = pal.user_id
WHERE pal.access_time >= NOW() - INTERVAL '90 days'
ORDER BY pal.user_id, pal.access_time;
```
Result set (potentially 50k rows) is chunked into 500-row batches for processing.

**Step 2 — HR role validation**
Second `postgres_query` fetches current role assignments and any role changes in the
review period from `role_assignments` and `termination_dates`.
Cross-reference: any access by a user whose `termination_date < access_time` is
immediately flagged as a `CRITICAL` anomaly regardless of LLM verdict.

**Step 3 — HIPAA policy retrieval (hybrid RAG)**
`hybrid` strategy retrieves HIPAA §164.312 (Access Control), §164.514 (Minimum Necessary),
and §164.528 (Accounting of Disclosures) from the policy KB.
Retrieved clauses populate the classification prompt as authoritative grounding.

**Step 4 — Self-Consistency classification (3 passes)**
`SelfConsistencyPattern(n_samples=3)` (`app/agent/patterns/self_consistency.py:42`)
runs three independent LLM completions at `temperature=0.4` over each user's access record.
Each pass produces: `{ "user_id": ..., "verdict": "COMPLIANT|VIOLATION|REVIEW_NEEDED", "rule_violated": "...", "confidence": 0.0–1.0 }`.
`_most_common()` (`app/agent/patterns/self_consistency.py:23`) selects the majority verdict.
Disagreement across all three passes sets `confidence=LOW` and flags for human review.

**Step 5 — PHI redaction before report writing**
`GuardrailEnforcer.check_output()` (`app/security_runtime/guardrail_enforcer.py`)
scans the assembled report for `_PII_PATTERNS` (email, SSN, phone).
Any PHI fragment is replaced with `[REDACTED-PHI]` before the report is written.
`EnforcementResult.pii_detected=True` is recorded in the audit trail.

**Step 6 — Signed report generation and audit trail**
Report is written as a structured JSON + markdown document with:
- Summary table: total accesses, compliant %, violation count, critical anomalies
- Per-user verdict table (anonymised user IDs in the exported version)
- Self-consistency confidence scores per classification
- HIPAA rule citations for each finding

`AuditLog.record(AuditEvent(goal_id=..., tool_name="postgres_query", outcome="REPORT_GENERATED", api_key_id=..., ip_address=...))`
(`app/governance/audit.py:58`) creates the compliance-grade audit record.

**Expected Output**
HIPAA access control compliance report (`hipaa_report_Q3_YYYY.json` + `.md`),
anomaly table, self-consistency confidence matrix, immutable audit trail records.

---

### UC24 — Cross-Reference Terminated Employees vs System Access

| Field | Value |
|---|---|
| **Use Case ID** | UC24 |
| **Category** | Compliance / Audit |
| **Business Goal** | Immediately after offboarding, verify that terminated employees have zero active access across all systems (database, GitHub, Slack) and produce compliance evidence. |
| **MCP Servers** | `app/mcp/servers/postgres_server.py` — HR `employees` + `termination_events` tables; `app/mcp/servers/github_server.py` — `github_list_org_members`, `github_get_user`; `app/mcp/servers/slack_server.py` — `slack_list_users`, `slack_get_user_info` |
| **Ingestion / Chunking** | Structured API responses — no RAG chunking needed; access policy PDF retrieved once at start |
| **RAG Strategy** | `hybrid` — internal access termination policy retrieval for SLA thresholds (e.g., "access revoked within 4 hours of termination") |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — COMPLIANCE bundle; PII detection on all HR data flowing through the agent |
| **Agent Pattern** | Supervisor pattern — orchestrator agent fans out to three parallel sub-agents: (1) DB access checker, (2) GitHub org membership checker, (3) Slack workspace checker; results are consolidated and reconciled by the supervisor |
| **LLM Model** | `gpt-4o` (standard tier) — structured data reconciliation |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` for any terminated employee found with *active* access to a `CRITICAL` system (production DB, GitHub org with write permissions); response SLA 30 minutes |
| **Audit Trail** | `app/governance/audit.py` — per-system check records with `outcome` = `ACCESS_FOUND` or `ACCESS_CLEAR`; HITL decision recorded with `approver` field; final compliance evidence bundle references all `event_id` values |
| **Cost Estimate** | ~$0.02 per offboarding check (parallel sub-agents, minimal tokens) |

#### End-to-End Steps

**Step 1 — Fetch termination events (last 24 hours)**
Supervisor agent calls `postgres_query` (`app/mcp/servers/postgres_server.py`):
```sql
SELECT e.employee_id, e.email, e.github_username, e.slack_user_id,
       te.termination_date, te.reason, te.systems_to_revoke
FROM termination_events te
JOIN employees e ON e.employee_id = te.employee_id
WHERE te.termination_date >= NOW() - INTERVAL '24 hours';
```
Returns list of recently terminated employees with their known system identifiers.

**Step 2 — Parallel access checks (three sub-agents)**
Supervisor dispatches simultaneously:

*Sub-agent A — Database access:*
`postgres_query` against `pg_roles` and `information_schema.role_table_grants`
for each employee's DB username. Checks for any live `GRANT` on PHI or production tables.

*Sub-agent B — GitHub access:*
`github_list_org_members` (`app/mcp/servers/github_server.py`) for each GitHub username.
If found in org: `github_get_user` to confirm active membership and fetch team/repo permissions.

*Sub-agent C — Slack access:*
`slack_list_users` (`app/mcp/servers/slack_server.py`) filtered by email.
`slack_get_user_info` to check `is_active`, workspace role, and channel memberships.

**Step 3 — Policy SLA retrieval (hybrid RAG)**
`hybrid` strategy retrieves the access termination policy from the KB:
"All system access must be revoked within 4 hours of HR termination event."
SLA threshold is extracted and used to compute `hours_since_termination` vs `access_still_active`.

**Step 4 — HITL gate for critical active access**
For any employee where an active access record is found on a CRITICAL system,
`HITLGateway.request_approval()` (`app/governance/hitl.py`) fires immediately with:
`{ employee_id, system, access_type, termination_date, hours_elapsed, sla_breached }`.
Approval triggers the automated revocation workflow (separate goal).
Rejection (if the finding is a false positive) logs `CLEARED` with approver note.

**Step 5 — Compliance evidence assembly**
Supervisor reconciles sub-agent results into a unified access matrix:
| Employee | DB | GitHub | Slack | Verdict | SLA Met |
The PII detection pass (`GuardrailEnforcer.check_output()`) redacts email addresses
in the exported report while preserving employee IDs.

**Step 6 — Immutable audit records**
`AuditLog.record()` (`app/governance/audit.py:58`) writes one `AuditEvent` per system per
employee checked. Fields: `tool_name` (e.g. `"github_list_org_members"`), `outcome`
(`"ACCESS_FOUND"` or `"ACCESS_CLEAR"`), `connector_id`, `approver` (if HITL triggered).
Evidence bundle (`compliance_evidence_YYYYMMDD.json`) embeds all `event_id` values
for external audit submission.

**Expected Output**
Access matrix spreadsheet (JSON + CSV), HITL decision log, compliance evidence bundle,
SLA breach report (systems where access survived > 4h post-termination).

---

## Category: Legal / Policy Review (UC25–UC28)

---

### UC25 — Review SaaS Vendor Contract for Non-Standard Terms

| Field | Value |
|---|---|
| **Use Case ID** | UC25 |
| **Category** | Legal / Policy Review |
| **Business Goal** | Ingest a 1000+ page SaaS vendor agreement (MSA + SOW + DPA + exhibits), identify every clause deviating from the company's standard playbook, rank by legal risk, and route critical items to counsel. |
| **MCP Servers** | `app/mcp/servers/confluence_server.py` — `confluence_search` + `confluence_get_page` to retrieve the company's standard contract playbook and fallback positions |
| **Ingestion / Chunking** | `app/ingestion/parsers/pdf_parser.py` — `PDFParser.parse_bytes()` with `pdfminer.six` backend for layout-aware extraction (tables, footnotes, exhibit headers); `app/rag/parent_child_chunker.py` — `ParentChildChunker(parent_chunk_size=1500, child_chunk_size=400, child_overlap=50)` — child chunks indexed, parent chunks returned for generation; `PDFLayoutChunker` respects section headings to prevent cross-section merging |
| **RAG Strategy** | `colbert` late-interaction reranking — ColBERT-style token-level similarity between query terms and contract clause tokens; superior to bi-encoder for "non-standard indemnification" queries where exact term overlap matters |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — `REGULATED` bundle; injection detection on all PDF content before processing (adversarial contracts may embed prompt injection in footnotes) |
| **Agent Pattern** | `app/agent/patterns/peer_review.py` — `PeerReviewPattern`; primary agent identifies deviations; peer reviewer checks for missed high-risk clauses (false negatives are more costly than false positives in contract review) |
| **LLM Model** | `gpt-5.2` (high tier) — handles 1000+ page documents via multi-chunk synthesis; used for both primary analysis and peer review |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` for any clause rated `CRITICAL_RISK` (e.g., perpetual IP licence grant, uncapped liability, governing law in adversarial jurisdiction); response SLA 4 hours |
| **Audit Trail** | `app/governance/audit.py` — per-clause analysis records, ColBERT retrieval confidence scores, peer review scores, HITL decisions |
| **Cost Estimate** | ~$1.20 per 1000-page contract (PDF parsing + ColBERT reranking + peer review double-pass) |

#### End-to-End Steps

**Step 1 — PDF ingestion (1000+ pages)**
`PDFParser.parse_bytes(pdf_bytes, source_name="saas_vendor_msa.pdf")`
(`app/ingestion/parsers/pdf_parser.py:48`) invokes `pdfminer.six` for layout-aware
extraction, preserving table structure and footnote text.
`PDFParseResult.to_chunks()` returns page-level chunks with `page_number` metadata.
`ParentChildChunker.chunk(content, document_id="msa_v3")` (`app/rag/parent_child_chunker.py:50`)
creates ~2,000 parent chunks and ~6,000 child chunks. Child chunks are loaded into
the vector store with parent-link metadata for context expansion at generation time.

**Step 2 — Playbook retrieval from Confluence**
`confluence_search` (`app/mcp/servers/confluence_server.py`) with CQL:
`space = LEGAL AND type = page AND title ~ "contract playbook"`.
`confluence_get_page` fetches the standard positions for: indemnification, liability cap,
IP ownership, data processing, governing law, audit rights.
Playbook clauses are stored as the reference KB against which vendor terms are compared.

**Step 3 — ColBERT late-interaction reranking**
For each of 12 risk categories (indemnification, liability, IP, termination, data residency,
audit rights, SLA penalties, assignment, change of control, governing law, warranty disclaimers,
limitation of liability), the `colbert` strategy performs token-level late-interaction scoring
between the query (`"vendor indemnification obligations"`) and all child chunk embeddings.
Top-5 child chunks per category are retrieved; parent chunks are loaded for full-context
generation. ColBERT token overlap ensures "indemnify" ≠ "indemnification" false misses are caught.

**Step 4 — Injection detection on PDF content**
`GuardrailEnforcer.check_tool_args()` (`app/security_runtime/guardrail_enforcer.py:51`)
scans all extracted text against `_INJECTION_PATTERNS` before the LLM processes it.
Contracts embedding `"ignore previous instructions"` in white-text footnotes are blocked
and flagged to the audit trail before any LLM call proceeds.

**Step 5 — Primary analysis + peer review**
Primary agent (gpt-5.2) produces per-clause deviation report:
`{ clause_id, page, risk_level: CRITICAL|HIGH|MEDIUM|LOW|STANDARD, deviation_from_playbook, playbook_position, recommendation }`.
`PeerReviewPattern` (`app/agent/patterns/peer_review.py`) runs an independent review pass:
the reviewer is specifically prompted to look for *missed* risks.
`PeerReviewResult.quality_score < 0.75` → primary re-analyses the flagged sections.
Final approved output requires `approved=True`.

**Step 6 — HITL for CRITICAL_RISK clauses**
All `CRITICAL_RISK` clauses queue via `HITLGateway.request_approval()`
(`app/governance/hitl.py`) with: clause text, page reference, playbook position, peer review notes.
Counsel approves (adds to negotiation list) or accepts as-is.
`AuditLog.record()` captures each decision with `approver`, `note`, and `event_id`.
Final deliverable: risk-ranked clause summary + negotiation redline document.

**Expected Output**
Risk-ranked clause table (CRITICAL/HIGH/MEDIUM/LOW), redline negotiation document,
peer review quality scores, HITL approval log with counsel notes, Confluence page published
via `confluence_create_page`.

---

### UC26 — Compare Two Policy Versions for Material Changes

| Field | Value |
|---|---|
| **Use Case ID** | UC26 |
| **Category** | Legal / Policy Review |
| **Business Goal** | Compare an incoming revised vendor privacy policy (v2) against the previously accepted version (v1), identify material changes to obligations, data rights, or liability, and output a structured change matrix for legal sign-off. |
| **MCP Servers** | None — both PDFs supplied as uploads; `app/mcp/servers/confluence_server.py` to publish the final change matrix |
| **Ingestion / Chunking** | `app/ingestion/parsers/pdf_parser.py` — `PDFParser.parse_bytes()` on both documents independently; section headings preserved via `app/ingestion/chunkers/heading.py` — `HeadingChunker` splits each policy into named sections (`_HEADING_PATTERN`) for aligned comparison |
| **RAG Strategy** | `sentence_window` — retrieves 3 sentences before/after each changed clause to capture context that determines whether a change is material (e.g., "except as required by law" added to a restriction clause) |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — standard bundle; PII detection on both PDFs |
| **Agent Pattern** | `app/agent/patterns/self_consistency.py` — `SelfConsistencyPattern(n_samples=3, temperature=0.5)` — three independent passes on each section-pair comparison; majority vote determines whether a change is `MATERIAL` or `EDITORIAL`; eliminates false positives from ambiguous legal language |
| **LLM Model** | `gpt-4o` (standard / medium tier) — sufficient for section-level diff with `sentence_window` context |
| **HITL** | Not required by default; HITL triggered if agent identifies > 5 `MATERIAL` changes in core obligation sections (data deletion, liability, governing law) |
| **Audit Trail** | `app/governance/audit.py` — records both PDF ingestion events, each section-pair comparison, self-consistency votes, and the final change matrix generation |
| **Cost Estimate** | ~$0.06 per policy comparison (two PDFs, 3-pass self-consistency on ~30 sections) |

#### End-to-End Steps

**Step 1 — Dual PDF ingestion and heading-based sectioning**
`PDFParser.parse_bytes(v1_bytes, "policy_v1.pdf")` and
`PDFParser.parse_bytes(v2_bytes, "policy_v2.pdf")` (`app/ingestion/parsers/pdf_parser.py:48`)
extract text from both documents with page metadata.
`HeadingChunker.chunk(content)` (`app/ingestion/chunkers/heading.py:9`) applies
`_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)` to split both
documents into named sections. Section names are normalised (lowercase, stripped)
to enable aligned matching: `"Data Retention"` in v1 matches `"Data Retention Policy"` in v2.

**Step 2 — Section alignment**
Agent builds an alignment map: for each section heading in v1, find the best-matching
section in v2 by heading string similarity (Levenshtein ≤ 0.3 edit distance).
Unmatched sections are flagged as `ADDED` (v2 only) or `REMOVED` (v1 only) — both are
`MATERIAL` by default.

**Step 3 — Sentence-window retrieval for context**
`sentence_window` strategy retrieves ±3 sentences around each differing clause to provide
the LLM with full context. This is critical for changes like:
- v1: "We will not sell your data."
- v2: "We will not sell your data **to third parties without consent**." ← material change

The window prevents the model from classifying this as `EDITORIAL`.

**Step 4 — Self-Consistency material change classification (3 passes)**
`SelfConsistencyPattern(n_samples=3, temperature=0.5)` (`app/agent/patterns/self_consistency.py:42`)
runs three independent comparisons per section pair.
Each pass outputs:
`{ "section": "...", "verdict": "MATERIAL|EDITORIAL|UNCHANGED", "change_type": "OBLIGATION_ADDED|OBLIGATION_REMOVED|SCOPE_EXPANDED|SCOPE_NARROWED|CLARIFICATION", "impact": "..." }`.
`_most_common()` returns majority verdict. Sections with 3-way disagreement are flagged `AMBIGUOUS`.

**Step 5 — Change matrix assembly**
Agent assembles the full change matrix (one row per section):
| Section | v1 Summary | v2 Summary | Verdict | Change Type | Impact |
MATERIAL rows are sorted to the top. All `OBLIGATION_REMOVED` rows for data deletion
or user rights are escalated to `HIGH_IMPACT`.

**Step 6 — HITL trigger and publication**
If `MATERIAL` changes ≥ 5 in core obligation sections, `HITLGateway.request_approval()`
(`app/governance/hitl.py`) is triggered with the full change matrix.
On approval, `confluence_create_page` (`app/mcp/servers/confluence_server.py`) publishes
the matrix under the LEGAL space with a compliance review timestamp.

**Expected Output**
Structured change matrix (JSON + markdown table), section alignment map,
self-consistency confidence scores, Confluence page with legal review status.

---

### UC27 — Extract NDA Obligations and Create Task List

| Field | Value |
|---|---|
| **Use Case ID** | UC27 |
| **Category** | Legal / Policy Review |
| **Business Goal** | Parse a signed NDA, extract every time-bound obligation (notice periods, destruction timelines, reporting requirements, non-solicitation durations), structure them as actionable tasks, and create tracked Jira tickets for each obligation. |
| **MCP Servers** | `app/mcp/servers/jira_server.py` — `jira_create_issue` for each extracted obligation-task; `jira_search_issues` to check for duplicates before creating |
| **Ingestion / Chunking** | `app/ingestion/parsers/pdf_parser.py` — `PDFParser.parse_bytes()` → `PDFParseResult.to_chunks()`; `app/ingestion/chunkers/heading.py` — `HeadingChunker` splits NDA into sections (Definitions, Term, Obligations, Remedies, Governing Law) |
| **RAG Strategy** | `app/rag/agentic/patterns/raptor.py` — `RAPTORPattern(cluster_size=4, max_levels=3)`; RAPTOR builds a tree of summaries across NDA sections; at query time ("what are all time-bound obligations?") retrieves from all tree levels to capture both explicit obligations (leaf level) and implicit ones surfaced only in summaries (parent level) |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — standard bundle; PII detection on NDA parties before Jira ticket creation |
| **Agent Pattern** | Plan-Execute — planner generates a structured extraction plan (list of obligation types to search for); executor applies each extraction step in sequence; verifier confirms all mandatory NDA obligation types were covered |
| **LLM Model** | `gpt-4o` (standard tier) — structured extraction with RAPTOR tree context |
| **HITL** | Not required; Jira tickets created automatically; assignee set to legal@company.com |
| **Audit Trail** | `app/governance/audit.py` — PDF ingestion, RAPTOR tree construction, each Jira issue creation event |
| **Cost Estimate** | ~$0.04 per NDA (PDF parsing + RAPTOR 3-level tree + Jira ticket creation calls) |

#### End-to-End Steps

**Step 1 — NDA ingestion and heading-based chunking**
`PDFParser.parse_bytes(nda_bytes, "nda_vendorX_2024.pdf")` (`app/ingestion/parsers/pdf_parser.py:48`)
extracts text preserving page numbers.
`HeadingChunker.chunk(full_text)` (`app/ingestion/chunkers/heading.py:9`) applies
`_HEADING_PATTERN` to split the NDA into named sections with level metadata:
- Level 1: `"Article 3: Obligations of Receiving Party"` → `metadata={"heading": "Article 3...", "level": 1}`
- Level 2: `"3.2 Confidentiality Period"` → child section

**Step 2 — RAPTOR tree construction**
`RAPTORPattern(cluster_size=4, max_levels=3)` (`app/rag/agentic/patterns/raptor.py:35`)
clusters the NDA sections into groups of 4, summarises each cluster into a parent `TreeNode`,
and recursively builds a 3-level hierarchy:
- Level 0 (leaves): individual NDA sections
- Level 1: summaries of grouped sections (e.g., "Obligations + Remedies cluster summary")
- Level 2: document-level summary ("NDA between Party A and Party B, 3-year term, mutual, governing NY law")

At query time ("extract all time-bound obligations"), retrieval spans all levels,
surfacing obligations that are only implicit in cross-section context.

**Step 3 — Obligation extraction (Plan-Execute)**
Planner generates an extraction checklist:
1. Confidentiality term duration
2. Return/destruction of materials timeline
3. Breach notification period
4. Non-solicitation duration (if any)
5. Non-compete duration (if any)
6. Audit rights notice period
7. Agreement termination notice period

Executor applies each checklist item as a targeted RAPTOR query,
retrieving the relevant tree nodes and extracting structured obligation data:
`{ obligation_type, duration, trigger_event, responsible_party, deadline_clause, page_ref }`.

**Step 4 — Duplicate check in Jira**
Before creating each Jira issue, agent calls `jira_search_issues`
(`app/mcp/servers/jira_server.py`) with JQL:
`project = LEGAL AND summary ~ "NDA obligation" AND labels = "nda-vendorX-2024"`.
If a matching ticket exists, agent updates it rather than creating a duplicate.

**Step 5 — Jira issue creation per obligation**
`jira_create_issue` creates one issue per obligation:
- **Summary**: `"NDA Obligation: Return confidential materials within 30 days of termination"`
- **Description**: Full extracted text with page reference and RAPTOR source node citation
- **Due Date**: Computed from `trigger_event` (e.g., termination date) + `duration`
- **Labels**: `["nda-obligation", "nda-vendorX-2024", obligation_type]`
- **Assignee**: `legal@company.com`
- **Priority**: `HIGH` for obligations with durations ≤ 30 days

**Step 6 — Audit trail and summary**
`AuditLog.record()` (`app/governance/audit.py:58`) records: PDF ingestion, RAPTOR tree depth
and node count, each `jira_create_issue` call with `connector_id="jira"`.
Summary report: obligation count, Jira ticket keys created, any unresolved RAPTOR tree nodes.

**Expected Output**
Jira epic with linked obligation tickets (due dates, assignees, labels),
obligation extraction report (JSON), RAPTOR tree summary statistics.

---

### UC28 — Draft GDPR Data Processing Agreement Clause

| Field | Value |
|---|---|
| **Use Case ID** | UC28 |
| **Category** | Legal / Policy Review |
| **Business Goal** | Generate a GDPR-compliant data processing agreement (DPA) clause for a new vendor engagement, grounded in Confluence DPA templates and the internal GDPR knowledge base, peer-reviewed for legal accuracy, and self-refined until quality bar is met. |
| **MCP Servers** | `app/mcp/servers/confluence_server.py` — `confluence_search` + `confluence_get_page` to retrieve approved DPA templates and GDPR Article 28 guidance pages; `confluence_create_page` to publish the approved draft |
| **Ingestion / Chunking** | Confluence pages retrieved as structured text — no PDF parser needed; GDPR regulation text chunked with `app/ingestion/chunkers/heading.py` — `HeadingChunker` for article-level retrieval |
| **RAG Strategy** | `hybrid` (dense + BM25) against the GDPR knowledge base — Articles 28, 29, 32 (processor obligations, security measures, sub-processor requirements); high recall needed for compliance, hybrid outperforms pure dense on legal terminology |
| **Guardrails** | `app/security_runtime/guardrail_enforcer.py` — `REGULATED` bundle; output checked for hallucinated GDPR article numbers (a known LLM failure mode in legal drafting); injection detection on all Confluence content |
| **Agent Pattern** | `app/agent/patterns/peer_review.py` — `PeerReviewPattern` for legal accuracy review; combined with Self-Refine loop: if `PeerReviewResult.quality_score < 0.85`, the primary agent receives the critique and generates a revised clause |
| **LLM Model** | `gpt-5.2` (high tier) — legal drafting requires maximum precision and long-context DPA template processing |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` after peer review `approved=True`; DPO (Data Protection Officer) must approve before Confluence publication |
| **Audit Trail** | `app/governance/audit.py` — Confluence retrieval events, hybrid RAG queries, each draft version, peer review scores, DPO approval |
| **Cost Estimate** | ~$0.22 per DPA clause draft (hybrid RAG + peer review + up to 3 self-refine iterations) |

#### End-to-End Steps

**Step 1 — DPA template retrieval from Confluence**
`confluence_search` (`app/mcp/servers/confluence_server.py`) with CQL:
`space = LEGAL AND type = page AND (title ~ "DPA template" OR title ~ "data processing agreement")`.
`confluence_get_page` retrieves the top 3 matching pages (body.storage format).
Templates are parsed to extract: subject matter, duration, nature/purpose, type of personal data,
categories of data subjects — the mandatory Article 28 elements.

**Step 2 — GDPR knowledge base retrieval (hybrid RAG)**
`hybrid` strategy queries the GDPR KB with:
- `"Article 28 processor obligations"` → returns Art. 28 full text + recitals
- `"sub-processor requirements written agreement"` → Art. 28(2), 28(4)
- `"technical organisational measures security"` → Art. 32
- `"data subject rights processor"` → Art. 28(3)(e)–(f)

Dense + BM25 hybrid ensures that both semantic matches ("security safeguards") and
lexical matches ("pseudonymisation", "encryption") are retrieved.

**Step 3 — Primary DPA clause drafting (gpt-5.2)**
LLM generates a structured DPA clause covering:
- Processor instructions and documented authority
- Confidentiality obligations for authorised personnel
- Technical and organisational security measures (Art. 32)
- Sub-processor engagement conditions and written agreement requirement
- Data subject rights assistance obligations
- Data deletion/return on termination
- Audit cooperation and information provision

Clause is grounded strictly in retrieved GDPR text and Confluence templates.
`REGULATED` guardrail bundle checks output for hallucinated article references.

**Step 4 — Peer review for legal accuracy**
`PeerReviewPattern` (`app/agent/patterns/peer_review.py`) evaluates the drafted clause:
- `accuracy`: Are all cited GDPR articles correctly referenced?
- `completeness`: Are all Article 28(3) mandatory elements present?
- Reviewer LLM outputs `quality_score`, `critique`, `suggestions`, `approved`.

If `quality_score < 0.85`, the Self-Refine loop feeds `critique` + `suggestions` back to
the primary agent for a revised draft. Maximum 3 iterations before escalation.

**Step 5 — DPO HITL approval**
Once `PeerReviewResult.approved=True`, `HITLGateway.request_approval()`
(`app/governance/hitl.py`) queues the clause for DPO review with:
- Full drafted clause text
- GDPR article citations
- Peer review quality score and critique
- Template deviation notes (where the draft differs from standard templates)

DPO approves → clause is finalised. DPO rejects with notes → one final revision pass.

**Step 6 — Confluence publication and audit trail**
`confluence_create_page` (`app/mcp/servers/confluence_server.py`) publishes the approved
DPA clause under `space=LEGAL, parent_page="Vendor DPA Library"` with metadata:
vendor name, approval date, DPO approver, peer review score.
`AuditLog.record()` (`app/governance/audit.py:58`) records all events:
Confluence retrieval, RAG queries, each draft version, peer review scores, DPO HITL decision.

**Expected Output**
Published Confluence DPA clause page (GDPR Art. 28 compliant), peer review quality score
(≥ 0.85), DPO approval record, audit trail with immutable event log.

---

## Category: Customer Support (UC29–UC30)

---

### UC29 — Auto-Triage Incoming Zendesk Tickets

| Field | Value |
|---|---|
| **Use Case ID** | UC29 |
| **Category** | Customer Support |
| **Business Goal** | Process every new Zendesk ticket in real-time, classify it into a support category, assign priority, route to the correct team queue, and apply standard tags — at a cost target of ~$0.003 per ticket. |
| **MCP Servers** | `app/mcp/servers/zendesk_server.py` — `zendesk_list_tickets` (bulk fetch of new tickets), `zendesk_get_ticket` (per-ticket detail), `zendesk_update_ticket` (write classification, priority, tags, assignee group) |
| **Ingestion / Chunking** | Ticket subject + description treated as short document; no chunking needed for typical ticket length (< 2,000 chars) |
| **RAG Strategy** | Dual-strategy: (1) `lexical` (BM25 keyword) for category keyword matching (`billing`, `outage`, `feature_request`, `security_incident`, `account_access`); (2) `corrective_rag` for edge cases where keyword matching score < 0.5 — falls back to semantic policy retrieval to determine correct routing |
| **Guardrails** | `app/guardrails_v2/engine.py` — `GuardrailsEngine` PII scan on all ticket content before LLM processing; `_PII_PATTERNS` detects SSN, credit card, email, phone — PII-containing tickets are tagged `contains-pii` and routed to the privacy queue |
| **Agent Pattern** | ReAct — single reasoning loop: read ticket → classify category → determine priority → check routing policy → apply Zendesk update; minimal overhead for < $0.003/ticket target |
| **LLM Model** | `gpt-4o-mini` (low tier) — cost-optimised; structured classification output; sufficient accuracy for triage decisions |
| **HITL** | Not required for standard triage; HITL triggered only for `security_incident` category (potential breach reports) |
| **Audit Trail** | `app/governance/audit.py` — triage decisions logged with `tool_name="zendesk_update_ticket"`, `outcome` = classification result, `connector_id="zendesk"` |
| **Cost Estimate** | **~$0.003 per ticket** (gpt-4o-mini: ~750 input tokens + 150 output tokens) |

#### End-to-End Steps

**Step 1 — Bulk new ticket fetch**
Agent calls `zendesk_list_tickets` (`app/mcp/servers/zendesk_server.py:39`)
with `sort_by="created_at"`, `sort_order="desc"`, `per_page=100`.
Filters to tickets where `status="new"` and `tags` does not include `av-triaged`.
For each ticket, `zendesk_get_ticket` fetches full description, requester info, and
any existing tags.

**Step 2 — PII pre-scan**
`GuardrailsEngine.evaluate_content(ticket_subject + " " + ticket_description)`
(`app/guardrails_v2/engine.py:47`) runs `_PII_PATTERNS` (SSN, Visa/Mastercard, email, phone).
PII-positive tickets: add tag `contains-pii`, set `assignee_group="privacy-team"`,
skip LLM classification. `AuditLog.record()` with `outcome="PII_DETECTED"`.
PII-negative tickets proceed to classification.

**Step 3 — Lexical category matching**
`lexical` (BM25) strategy scores the ticket text against category keyword sets:
- `billing`: ["invoice", "charge", "payment", "refund", "subscription", "overcharged"]
- `outage`: ["down", "unavailable", "error 500", "cannot connect", "not working", "outage"]
- `feature_request`: ["please add", "would be great if", "feature", "enhancement", "request"]
- `security_incident`: ["breach", "hacked", "unauthorized", "compromised", "phishing"]
- `account_access`: ["locked out", "reset password", "cannot login", "two-factor", "MFA"]

Score ≥ 0.5 → category assigned directly without LLM call (saves ~40% of LLM calls).

**Step 4 — Corrective RAG fallback for ambiguous tickets**
For tickets where BM25 score < 0.5 (ambiguous content), `corrective_rag` retrieves
the routing policy from the support KB (`"what ticket types go to tier-2 engineering?"`).
Retrieved policy context + ticket text is passed to gpt-4o-mini for classification.
LLM output: `{ "category": "...", "priority": "urgent|high|normal|low", "routing_team": "...", "tags": [...] }`.

**Step 5 — Priority determination**
Priority rules (applied after category):
- `security_incident` → always `urgent` + HITL trigger
- Subject contains "production down" or "all users" → `urgent`
- Requester is a paying enterprise customer (checked via `zendesk_search_users`) → `high`
- Default → `normal`

**Step 6 — Zendesk update and audit**
`zendesk_update_ticket` (`app/mcp/servers/zendesk_server.py`) writes:
`{ status: "open", priority: ..., tags: [..., "av-triaged"], assignee_group: ..., custom_fields: { "av_category": ..., "av_confidence": ... } }`.
`AuditLog.record(AuditEvent(tool_name="zendesk_update_ticket", outcome=category, connector_id="zendesk"))`
(`app/governance/audit.py:58`).
Throughput: ~200 tickets/minute at $0.003/ticket.

**Expected Output**
All `new` Zendesk tickets updated with category, priority, routing team, tags,
and `av-triaged` label. PII tickets segregated. Security incidents escalated via HITL.
Audit log records for SOC2 compliance.

---

### UC30 — Draft Personalized Complaint Response

| Field | Value |
|---|---|
| **Use Case ID** | UC30 |
| **Category** | Customer Support |
| **Business Goal** | For a customer escalation complaint, retrieve full ticket history and CRM account data, draft a personalised, empathetic response that addresses every raised issue, follows the company's tone and compensation policy, and passes peer review for empathy and policy compliance before sending. |
| **MCP Servers** | `app/mcp/servers/zendesk_server.py` — `zendesk_get_ticket` (full ticket + thread history), `zendesk_search_tickets` (prior tickets from same requester), `zendesk_add_comment` (post final approved response) |
| **Ingestion / Chunking** | Ticket thread + CRM account data loaded as structured context; response policy PDF ingested via `app/ingestion/parsers/pdf_parser.py` if not yet in KB |
| **RAG Strategy** | `corrective_rag` — retrieves tone guidelines, compensation policy limits, and prior resolution patterns from the support policy KB; if initial retrieval confidence < 0.65, expands search with customer segment and issue type before drafting |
| **Guardrails** | `app/guardrails_v2/engine.py` — `GuardrailsEngine` PII detection on draft output before posting; `_PII_PATTERNS` ensures no other customer's data is accidentally included; `app/security_runtime/guardrail_enforcer.py` — injection detection on all CRM data |
| **Agent Pattern** | `app/agent/patterns/peer_review.py` — `PeerReviewPattern`; reviewer LLM evaluates draft on: (1) empathy score, (2) completeness (all issues addressed?), (3) policy compliance (compensation within approved limits?); requires `quality_score ≥ 0.80` before sending |
| **LLM Model** | `gpt-4o` (standard tier) — empathetic, nuanced language; better tonal control than mini models for escalation responses |
| **HITL** | `app/governance/hitl.py` — `HITLGateway.request_approval()` if compensation offered exceeds $500 OR if peer review flags `policy_compliance=False` |
| **Audit Trail** | `app/governance/audit.py` — ticket retrieval, CRM query, RAG queries, each draft version, peer review scores, HITL decisions, final `zendesk_add_comment` event |
| **Cost Estimate** | ~$0.018 per complaint response (full ticket history + peer review double-pass) |

#### End-to-End Steps

**Step 1 — Full ticket and history retrieval**
`zendesk_get_ticket` (`app/mcp/servers/zendesk_server.py:50`) fetches the escalation ticket
with full comment thread (all prior agent responses and customer replies).
`zendesk_search_tickets` queries: `requester:customer@email.com type:ticket` to retrieve
the customer's last 90 days of support history.
CRM data: account tier (enterprise/growth/starter), tenure, MRR, open issues, prior credits.
This context is assembled into the generation prompt to enable genuine personalisation.

**Step 2 — Issue extraction from thread**
Agent reads the full comment thread and extracts a structured issue list:
`[{ "issue_id": 1, "issue_description": "...", "first_raised": "...", "still_open": true/false }]`.
All issues raised by the customer (including those mentioned in prior tickets this week)
are included. Missing an issue is a peer review failure condition.

**Step 3 — Policy retrieval (corrective RAG)**
`corrective_rag` retrieves from the support policy KB:
- Tone guidelines for enterprise complaint escalations
- Compensation authority matrix (what support agents can offer without manager approval)
- Standard resolution templates for the identified issue types (billing, outage, data loss)
- Prior successful resolutions for similar issue patterns

If retrieval confidence < 0.65, the strategy reformulates the query using
`customer_segment="enterprise"` + `issue_type="billing_dispute"` and retries.

**Step 4 — PII scan on CRM data**
`GuardrailEnforcer.check_tool_args()` (`app/security_runtime/guardrail_enforcer.py:51`)
scans all CRM field values before they enter the generation context.
Injection patterns (rare but possible in CRM notes) are detected and sanitised.
`GuardrailsEngine.evaluate_content()` (`app/guardrails_v2/engine.py:47`) scans
the assembled context for any cross-contamination of other customers' PII.

**Step 5 — Draft generation and peer review**
gpt-4o generates a personalised response:
- Opening: acknowledges the specific issues by name (using issue extraction from Step 2)
- Body: addresses each issue with resolution/explanation, grounded in policy context
- Compensation offer (if applicable): within policy limits from Step 3
- Closing: personal apology, direct contact for follow-up

`PeerReviewPattern` (`app/agent/patterns/peer_review.py`) evaluates:
- `empathy_score` (custom criterion): Does the response acknowledge customer frustration?
- `completeness`: Are all extracted issues addressed?
- `policy_compliance`: Is any compensation offer within the authority matrix?
- `quality_score < 0.80` → primary agent receives critique and generates revised draft.

**Step 6 — HITL for high-value compensation, then send**
If compensation > $500 OR `PeerReviewResult.policy_compliance=False` even after revision,
`HITLGateway.request_approval()` (`app/governance/hitl.py`) queues for manager review.
On approval (or if HITL not triggered), `GuardrailsEngine.evaluate_content()` does a
final PII scan on the outbound draft to ensure no other customer data is included.
`zendesk_add_comment` (`app/mcp/servers/zendesk_server.py`) posts the approved response
as a public reply and updates ticket status to `pending`.
`AuditLog.record()` captures: `tool_name="zendesk_add_comment"`, `outcome="RESPONSE_SENT"`,
`approver` (if HITL), `connector_id="zendesk"`.

**Expected Output**
Personalised complaint response posted to Zendesk thread (all issues addressed, empathy
score ≥ 0.80, compensation within policy limits), peer review scorecard, audit trail
with all draft versions and approval decisions.

---

## Summary Table — UC21–30

| UC | Title | Category | Model | Pattern | RAG | Guardrails | HITL | Est. Cost |
|---|---|---|---|---|---|---|---|---|
| 21 | GDPR Jira Ticket Audit | Compliance | gpt-4o-mini | ReAct | hybrid | PII detect (engine.py) | On confirmed violations | $0.004/ticket |
| 22 | Vendor Contract Compliance Review | Compliance | gpt-5.2 | Peer Review | corrective_rag | REGULATED | High-risk clauses | $0.18/doc |
| 23 | HIPAA Access Control Report | Compliance | gpt-4o | Self-Consistency (3×) | hybrid | COMPLIANCE + PII redact | Anomaly threshold | $0.12/500 users |
| 24 | Terminated Employee Access Check | Compliance | gpt-4o | Supervisor (parallel) | hybrid | COMPLIANCE + PII | Active critical access | $0.02/check |
| 25 | SaaS Vendor Contract Non-Standard Terms | Legal | gpt-5.2 | Peer Review | colbert | REGULATED + injection | Critical risk clauses | $1.20/1000p |
| 26 | Policy Version Diff | Legal | gpt-4o | Self-Consistency (3×) | sentence_window | Standard | >5 material changes | $0.06/compare |
| 27 | NDA Obligation Extraction → Jira | Legal | gpt-4o | Plan-Execute | raptor | Standard + PII | None | $0.04/NDA |
| 28 | GDPR DPA Clause Drafting | Legal | gpt-5.2 | Peer Review + Self-Refine | hybrid | REGULATED | DPO approval | $0.22/clause |
| 29 | Zendesk Auto-Triage | Customer Support | gpt-4o-mini | ReAct | lexical + corrective_rag | PII detect (engine.py) | Security incidents | $0.003/ticket |
| 30 | Personalized Complaint Response | Customer Support | gpt-4o | Peer Review | corrective_rag | PII detect + injection | Compensation >$500 | $0.018/response |

---

## Key Files Referenced

| File | Purpose in These Use Cases |
|---|---|
| `agent-verse-backend/app/mcp/servers/jira_server.py` | Bulk JQL export (`jira_search_issues`), issue creation (`jira_create_issue`), comment annotation (`jira_add_comment`) — UC21, UC27 |
| `agent-verse-backend/app/mcp/servers/zendesk_server.py` | Ticket list/fetch/update/comment — UC29, UC30 |
| `agent-verse-backend/app/mcp/servers/confluence_server.py` | Policy page retrieval (`confluence_search`, `confluence_get_page`), draft publication (`confluence_create_page`) — UC22, UC25, UC26, UC28 |
| `agent-verse-backend/app/mcp/servers/postgres_server.py` | Access log and HR data queries — UC23, UC24 |
| `agent-verse-backend/app/mcp/servers/github_server.py` | Org membership check — UC24 |
| `agent-verse-backend/app/mcp/servers/slack_server.py` | Workspace user check — UC24 |
| `agent-verse-backend/app/ingestion/parsers/pdf_parser.py` | `PDFParser.parse_bytes()` → `PDFParseResult` — UC22, UC25, UC26, UC27, UC28, UC30 |
| `agent-verse-backend/app/ingestion/chunkers/heading.py` | `HeadingChunker` — article/section-level splits for policies and NDAs — UC26, UC27, UC28 |
| `agent-verse-backend/app/rag/parent_child_chunker.py` | `ParentChildChunker` — precise child retrieval with parent context return — UC22, UC25 |
| `agent-verse-backend/app/rag/agentic/patterns/raptor.py` | `RAPTORPattern` — hierarchical NDA summarisation and multi-level retrieval — UC27 |
| `agent-verse-backend/app/agent/patterns/peer_review.py` | `PeerReviewPattern` — independent quality/accuracy review; `PeerReviewResult.quality_score` — UC22, UC25, UC28, UC30 |
| `agent-verse-backend/app/agent/patterns/self_consistency.py` | `SelfConsistencyPattern(n_samples=3)` — majority-vote for high-accuracy classification — UC23, UC26 |
| `agent-verse-backend/app/governance/hitl.py` | `HITLGateway.request_approval()` — human gate for confirmed violations, high-risk clauses, critical access, large compensation — UC21–25, UC28, UC29, UC30 |
| `agent-verse-backend/app/governance/audit.py` | `AuditLog.record(AuditEvent(...))` — immutable append-only compliance audit trail — all UC21–30 |
| `agent-verse-backend/app/guardrails_v2/engine.py` | `GuardrailsEngine` — PII pattern detection (`_PII_PATTERNS`) on ticket/contract/response content — UC21, UC29, UC30 |
| `agent-verse-backend/app/security_runtime/guardrail_enforcer.py` | `GuardrailEnforcer` — REGULATED/COMPLIANCE bundles, injection detection, output PII redaction — UC22–28, UC30 |
