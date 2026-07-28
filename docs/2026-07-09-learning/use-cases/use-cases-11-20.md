# Use Cases 11–20: Infrastructure / DevOps (cont.) & Security Operations & Compliance

---

## Use Case 11: Terraform Plan Review for Destructive Changes

**Goal:** Parse a `terraform plan` output, flag all destructive operations (destroy, force-replace, in-place mutations on stateful resources), and block the pipeline until a human approves or rejects.

**Business problem:** Engineers accidentally destroy production databases or drop critical cloud resources via `terraform apply`; automated pre-flight review with mandatory HITL prevents irreversible cloud incidents.

**Actors:** Platform/infra engineer, AgentVerse agent, GitHub MCP server, Terraform plan artifact

**Inputs:** `terraform plan -out=tfplan.json` JSON artifact, Terraform standards Confluence page, past incident reports

**Agent pattern:** Peer Review — two independent analysis passes: (1) technical correctness pass, (2) blast-radius / operational-risk pass; scores are averaged and gate HITL

**RAG pattern:** `hybrid` — lexical search for `destroy`, `forces replacement`, `must be replaced` keywords in plan JSON + semantic search against company Terraform standards and past incident KB

**Memory used:** Execution memory (past Terraform incident patterns for this repository), Reflexion memory (lesson: "always check `lifecycle.prevent_destroy` is set on stateful resources before approving")

**Ingestion path:** Terraform plan JSON → `SemanticChunker` (by resource block) → `text-embedding-3-small` → `knowledge_chunks_1536`; Confluence runbooks → `HeadingChunker` → same index

**Retrieval path:** Resource type + action → `hybrid` (FTS for `"actions": ["delete"]` + vector for semantically similar past incidents) → ColBERT reranking → top-8 context chunks

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-5.2` (high-stakes infrastructure review), embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: scans plan JSON for `destroy` count > 0 → emits HIGH RISK signal
- HITL: **mandatory** for any plan containing `destroy` or `force_replace` on `aws_rds_instance`, `aws_s3_bucket`, `google_sql_database_instance`, or any persistent storage resource
- PolicyEngine: `terraform.apply` tool requires ADMIN scope + HITL approval token
- Audit trail: every plan review decision (approve/reject/defer) logged with operator identity and risk justification
- Governance bundle: REGULATED (full audit + cost + policy chain)

**End-to-end flow:**
1. CI pipeline posts `terraform plan` JSON to `POST /goals` with attachment; GoalService queues to `goals.enterprise` Celery queue
2. `_node_initialize`: RuntimeProfileBuilder selects `hybrid` RAG; PatternConfig detects EXPERT complexity → enables Peer Review; REGULATED governance bundle activated
3. `_node_rag_retrieval`: hybrid retrieval surfaces past incidents involving `aws_rds_instance` destruction; HeadingChunker segments from Terraform standards page
4. `_node_plan`: ContextPipeline assembles resource-level change table; OutputContractBuilder detects structured risk report output
5. `_node_execute (Pass 1 — technical)`: parses JSON `resource_changes[]`; classifies each: SAFE / MODIFY / DESTROY / FORCE_REPLACE; checks `lifecycle.prevent_destroy`
6. `_node_peer_review (Pass 2 — operational)`: independent reviewer LLM scores blast radius (0–1), reversibility (0–1), dependency impact (0–1); aggregates to overall risk score
7. HITL gate fires for any DESTROY resource: approval request posted to `#infra-approvals` Slack and Jira ticket; 30-min timeout → pipeline blocked
8. On approval: `github_server.py:post_review_comment` with risk matrix; on rejection: pipeline fails with detailed rejection reasoning

**Observability:** `peer_review_score` metric emitted; HITL SSE event with resource list; `GOAL_DURATION` histogram; cost breakdown per review pass; `TOOL_CALL_TOTAL` for each GitHub API call

**Eval path:** `safety` (no DESTROY resources missed), `grounding` (risk claims reference actual plan JSON fields), `goal_success` (HITL resolved and pipeline decision posted)

**Expected output:** GitHub PR comment with risk matrix (resource | action | risk level | reversible | recommendation), HITL approval/rejection decision logged, CI pipeline status updated

**Failure modes:** Plan JSON malformed → GuardrailChecker raises parse error → fail fast with clear message; HITL timeout (30 min) → auto-reject and fail pipeline; ColBERT reranker unavailable → fallback to vector-only retrieval with lower confidence; verifier score < 0.6 → inject critique and replan

**Code references:** `app/mcp/servers/github_server.py`, `app/agent/patterns/peer_review.py`, `app/governance/hitl.py`, `app/rag/engine.py:retrieve_hybrid`, `app/governance/audit.py`

---

## Use Case 12: Deployment Runbook from CI/CD Config

**Goal:** Read a GitHub Actions workflow YAML and linked Confluence runbooks, then generate a comprehensive step-by-step human-readable deployment runbook for a service.

**Business problem:** Deployment runbooks drift from actual CI/CD configuration, causing on-call engineers to follow outdated procedures during incidents.

**Actors:** Platform engineer, AgentVerse agent, GitHub MCP server, Confluence MCP server

**Inputs:** GitHub Actions workflow YAML files, existing Confluence runbook page (may be stale), service architecture Confluence page

**Agent pattern:** Plan-Execute — structured phases: (1) discover CI/CD steps from YAML, (2) cross-reference Confluence for context, (3) generate runbook, (4) publish to Confluence

**RAG pattern:** `multi_hop` — decomposes into: "what are all job/step definitions in the workflow?", "what environment variables and secrets are required?", "what are the rollback steps documented in Confluence?"

**Memory used:** Execution memory (previously generated runbooks for similar services), Procedural memory (learned sequence: always document rollback before deploy steps)

**Ingestion path:** YAML workflow files → `HeadingChunker` (by job name) → `text-embedding-3-small` → `knowledge_chunks_1536`; Confluence pages → `HeadingChunker` → same index

**Retrieval path:** Workflow job names → `multi_hop`: (1) fetch all job/step definitions, (2) fetch environment configuration, (3) fetch rollback procedures → assembled context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: Confluence write requires `confluence:write` scope; GitHub read is read-only (no write risk)
- No HITL required (documentation is non-destructive)
- OutputContractBuilder: "runbook" in goal → structured markdown output contract with required sections

**End-to-end flow:**
1. `POST /goals` with workflow file URL; GoalService queues to `goals.professional` Celery queue
2. `_node_initialize`: multi_hop strategy selected; OutputContractBuilder detects "runbook" → structured markdown contract
3. `_node_rag_retrieval`: `github_server.py:get_file_content` fetches all YAML files in `.github/workflows/`; indexes with `HeadingChunker`; fetches linked Confluence pages
4. `_node_plan`: identifies phases: pre-deploy checks, deploy steps, verification, rollback; estimates 4 sub-goals
5. `_node_execute`: multi-hop queries extract (a) job sequence and dependencies, (b) required secrets and env vars, (c) deployment gate conditions, (d) rollback trigger conditions
6. Assembles runbook with sections: Prerequisites, Pre-Deploy Checks, Deployment Steps, Verification, Rollback Procedure, Contacts
7. `confluence_server.py:update_page` updates existing runbook page with timestamp and diff
8. `_node_verify`: verifier cross-checks all workflow steps appear in runbook; grounding score > 0.85 required

**Observability:** `multi_hop` SSE with sub-query count; `GOAL_DURATION` target < 3min; token cost ~$0.06; `tool_success_rate` for Confluence update

**Eval path:** `grounding` (runbook steps match actual workflow YAML), `goal_success` (Confluence page updated), `tool_success_rate`

**Expected output:** Updated Confluence runbook page with accurate step-by-step deployment procedure, environment requirements, and rollback steps matching the current CI/CD configuration

**Failure modes:** Workflow YAML uses reusable workflows → agent recursively fetches called workflows; Confluence page locked for editing → retry after 60s; multi-hop retrieval returns conflicting information between YAML and Confluence → conflict flagged in runbook for human review

**Code references:** `app/mcp/servers/github_server.py`, `app/mcp/servers/confluence_server.py`, `app/rag/engine.py:retrieve_multi_hop`, `app/ingestion/chunkers/heading_chunker.py`, `app/context/output_contract_builder.py`

---

## Use Case 13: Auto-Scale Kubernetes from Datadog Alert

**Goal:** When Datadog fires a high-CPU alert on a Kubernetes deployment, analyze current metrics and resource utilization, decide on appropriate scaling action, and execute with HITL for scale-down operations.

**Business problem:** Manual scaling responses to traffic spikes are slow; automated scaling with intelligent thresholds prevents SLA breaches while HITL on scale-down prevents premature capacity reduction.

**Actors:** SRE, AgentVerse agent, Kubernetes MCP server, Datadog MCP server

**Inputs:** Datadog alert payload (service, metric, threshold, current value), Kubernetes deployment manifest, historical traffic patterns

**Agent pattern:** ReAct — iterative tool calls: fetch metrics → analyze trend → decide action → execute (with HITL gate for scale-down)

**RAG pattern:** `self_rag` — agent self-evaluates whether retrieved scaling thresholds and SLO documents are relevant before using them; regenerates retrieval query if relevance < 0.6

**Memory used:** Execution memory (past scaling decisions for this deployment), Episodic memory (time-of-day traffic patterns → avoid scaling down during business hours), Reflexion memory (lesson: "check HPA config before manual scaling to avoid conflict")

**Ingestion path:** Datadog alert JSON → structured; Kubernetes manifests → `SemanticChunker` → `text-embedding-3-small`; SLO documentation → `HeadingChunker`

**Retrieval path:** Service name + metric → `self_rag`: retrieves SLO targets + HPA config + past scaling events → self-evaluates relevance → confirmed context fed to planner

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: **mandatory for scale-down** (replica count reduction) — scale-up is auto-approved up to 2× current replicas
- PolicyEngine: `kubernetes.scale_deployment` requires OPERATOR scope; scale > 3× requires ADMIN scope
- Audit trail: every scaling action logged with metric values, decision rationale, and operator
- BulkheadLimiter: max 3 concurrent scaling operations per tenant to prevent cascade

**End-to-end flow:**
1. Datadog webhook → `POST /webhooks/alerts/datadog` → Redis → `fire_due_schedules`
2. `_node_initialize`: ReAct pattern selected; self_rag strategy for SLO retrieval
3. `_node_execute (step 1)`: `datadog_server.py:get_metrics` fetches CPU, memory, request rate for last 30min
4. `_node_execute (step 2)`: `kubernetes_server.py:get_deployment` fetches current replica count and resource limits
5. Self-RAG retrieves SLO targets (p99 latency < 200ms); self-evaluates relevance → confirmed
6. Decision logic: CPU > 80% sustained 5min → scale up 50%; CPU < 30% sustained 15min → HITL gate for scale-down
7. Scale-up: `kubernetes_server.py:scale_deployment` executes immediately; SSE `action_taken`
8. Scale-down: HITL request to `#sre-approvals` Slack; 15-min window; on approval → `kubernetes_server.py:scale_deployment`
9. `_node_verify`: `datadog_server.py:get_metrics` after 5min → confirms CPU returned to target range

**Observability:** `TOOL_CALL_TOTAL` per Kubernetes API call; HITL SSE event; `GOAL_DURATION` target < 8min (including HITL wait); cost ~$0.03/alert; `eval_score_recorded` for scaling accuracy

**Eval path:** `goal_success` (scaling action taken or HITL resolved), `grounding` (decision references actual metric values), `tool_success_rate`

**Expected output:** Kubernetes deployment scaled to new replica count, Slack notification with before/after metrics, HITL approval log for scale-down events

**Failure modes:** HPA already managing replicas → agent detects conflict → recommends HPA parameter adjustment instead of manual scaling; Kubernetes API timeout → circuit breaker opens → alert SRE directly; metrics unavailable → Datadog fallback to Kubernetes resource metrics; HITL timeout → maintain current replica count and escalate alert

**Code references:** `app/mcp/servers/kubernetes_server.py`, `app/mcp/servers/datadog_server.py`, `app/rag/agentic/patterns/self_rag.py`, `app/governance/hitl.py`, `app/reliability/bulkhead.py`

---

## Use Case 14: Audit AWS IAM for Excessive Permissions

**Goal:** Query AWS IAM policies attached to all users and roles, identify principals with excessive permissions (admin wildcards, unused permissions), and generate a remediation report with least-privilege recommendations.

**Business problem:** IAM permission sprawl is a leading cause of cloud security breaches; systematic auditing enables least-privilege enforcement before permissions are exploited.

**Actors:** Cloud security engineer, AgentVerse agent, postgres_server.py (IAM audit log storage), AWS IAM policy documents

**Agent pattern:** Peer Review — two-pass analysis: (1) technical IAM policy analysis, (2) compliance-framework review against CIS AWS Benchmark and company IAM standards

**RAG pattern:** `corrective_rag` — retrieves CIS AWS IAM controls and company policy from KB; web fallback for AWS documentation on specific IAM action semantics when confidence < 0.55

**Memory used:** Execution memory (previously audited IAM configurations for this account), Reflexion memory (lesson: "wildcard `*` on `iam:*` actions is always critical severity, no exceptions")

**Ingestion path:** IAM policy JSON → `SemanticChunker` (by policy statement) → `text-embedding-3-small`; CIS benchmarks → `HeadingChunker` → `knowledge_chunks_1536`; audit logs → `postgres_server.py` → structured SQL query

**Retrieval path:** IAM action + resource ARN → `corrective_rag`: retrieves CIS control definitions + past audit findings → correctness check → web fallback for unfamiliar IAM actions

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: IAM read tools require `aws:iam:read` scope; no write operations executed (read-only audit)
- GuardrailChecker: output sanitization ensures no real ARNs or account IDs in unredacted logs
- HITL: not required (read-only audit); remediation tickets require OPERATOR approval before execution
- Governance bundle: REGULATED (full audit chain + compliance mapping)
- Audit trail: complete log of every IAM policy fetched and analyzed

**End-to-end flow:**
1. `POST /goals` with AWS account ID; GoalService queues to `goals.enterprise`
2. `_node_rag_retrieval`: `corrective_rag` retrieves CIS AWS Benchmark IAM controls (1.1–1.22); `postgres_server.py:query` fetches 90-day IAM access advisor data (last used dates)
3. `_node_plan`: lists all IAM principals (users, roles, groups); plans analysis in batches of 20
4. `_node_execute (Pass 1)`: for each principal — fetch attached policies via `aws_iam_server.py:list_attached_policies`; parse `Statement[]` for wildcards, admin actions, cross-account trust
5. `_node_peer_review (Pass 2)`: independent compliance reviewer maps each finding to CIS control; assigns severity (CRITICAL/HIGH/MEDIUM/LOW)
6. Access advisor data correlation: flags permissions unused for > 90 days as "can be removed"
7. Generates remediation report: finding | severity | CIS control | recommended least-privilege replacement | Jira ticket
8. `jira_server.py:create_issue` per CRITICAL/HIGH finding; `confluence_server.py:create_page` for full report

**Observability:** `peer_review_score` per IAM principal batch; `GOAL_DURATION` for full account audit; cost ~$0.25 for 200-principal account; `TOOL_CALL_TOTAL` for IAM API calls

**Eval path:** `safety` (no CRITICAL findings missed), `grounding` (recommendations reference actual policy documents), `goal_success` (report published and Jira tickets created)

**Expected output:** Confluence audit report with IAM findings table, severity classifications, CIS benchmark mappings, and least-privilege recommendations; Jira tickets per critical finding

**Failure modes:** IAM policy uses Service Control Policies → agent fetches SCP context from organization root; policy document too large (> 6KB) → chunked analysis with RAPTOR summarization; AWS API rate limit → exponential backoff with circuit breaker; access advisor data unavailable → analysis based on policy content only with lower confidence

**Code references:** `app/mcp/servers/postgres_server.py`, `app/agent/patterns/peer_review.py`, `app/rag/agentic/patterns/corrective_rag.py`, `app/governance/audit.py`, `app/mcp/servers/jira_server.py`

---

## Use Case 15: Triage Trivy CVE Scan Report

**Goal:** Parse a Trivy container image vulnerability scan report, prioritize CVEs by exploitability and business criticality, and create individual Jira tickets for every critical and high-severity finding.

**Business problem:** CVE scan reports contain hundreds of findings; automated triage with contextual prioritization ensures critical vulnerabilities are tracked and assigned within minutes of detection.

**Actors:** Security engineer, AgentVerse agent, Jira MCP server

**Inputs:** Trivy JSON scan report, service criticality tier mapping (Confluence), known false positive list (PostgreSQL)

**Agent pattern:** ReAct — iterative: parse report → prioritize → deduplicate → create tickets

**RAG pattern:** `lexical` — CVE IDs and package names are exact identifiers; BM25 full-text search against known false positive DB and existing Jira CVE tickets for deduplication

**Memory used:** Execution memory (CVEs already ticketed for this service in previous scans), Reflexion memory (lesson: "always check false positive list before creating ticket")

**Ingestion path:** Trivy JSON → `SemanticChunker` (by vulnerability finding) → structured CVE metadata; false positive list → `postgres_server.py` → SQL lookup; Confluence tier map → `HeadingChunker`

**Retrieval path:** CVE ID + package name → `lexical` (BM25 search against existing Jira issues + false positive DB) → exact match deduplication → novel CVEs passed to creation step

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: `jira.create_issue` requires `jira:write` scope
- Deduplication: `RedisDeduplicationCache` prevents same CVE being ticketed twice per service per sprint
- Rate limiting: max 50 Jira tickets per scan run to prevent ticket storm
- Audit trail: all created tickets logged with scan report SHA

**End-to-end flow:**
1. CI pipeline posts Trivy JSON report to `POST /goals` with service name parameter
2. `_node_initialize`: lexical strategy for CVE lookup; `gpt-4o-mini` for cost efficiency (high-volume operation)
3. `_node_rag_retrieval`: BM25 search fetches existing Jira issues tagged with CVE IDs from this service; `postgres_server.py:query` checks false positive table
4. `_node_plan`: filters CRITICAL + HIGH findings; removes false positives; removes already-ticketed CVEs; estimates N new tickets
5. `_node_execute`: for each novel CVE — (a) classify business impact based on service tier from Confluence, (b) fetch `cvss_v3_score`, affected package, fixed version, (c) generate ticket description with: CVE ID, CVSS score, affected package, fix action, SLA
6. `jira_server.py:create_issue` per CVE with priority mapped from CVSS: 9.0+ → Critical, 7.0–8.9 → High; labels: `security`, `cve`, service tier
7. Summary comment posted to `#security-alerts` Slack
8. `_node_verify`: checks ticket count matches expected novel CVE count

**Observability:** `TOOL_CALL_TOTAL` for Jira creates; deduplication hit rate metric; cost ~$0.001/CVE; `GOAL_DURATION` target < 5min per scan

**Eval path:** `goal_success` (tickets created for all untracked critical CVEs), `tool_success_rate` (Jira API success rate), deduplication accuracy

**Expected output:** Jira tickets per critical/high CVE with CVSS score, affected package version, fix version, SLA deadline, and service tier; Slack summary with total counts

**Failure modes:** Trivy JSON malformed → schema validation fails → alert security team with raw report; Jira project not found → fallback to email notification; deduplication cache miss → verifier cross-checks for duplicate ticket titles before creating; CVSS data missing → default to HIGH priority with note

**Code references:** `app/mcp/servers/jira_server.py`, `app/mcp/servers/postgres_server.py`, `app/rag/engine.py:retrieve_lexical`, `app/reliability/dedup.py`, `app/mcp/servers/slack_server.py`

---

## Use Case 16: Investigate Anomalous Login Pattern

**Goal:** Analyze a SIEM alert for anomalous login activity (impossible travel, credential stuffing, or brute force pattern), correlate with user activity logs in PostgreSQL, and recommend action including optional account suspension via HITL.

**Business problem:** Security analysts are overwhelmed by SIEM alerts; automated correlation reduces triage time from 45 minutes to under 5 minutes and ensures no critical account compromise goes unreviewed.

**Actors:** SOC analyst, AgentVerse agent, SIEM server, postgres_server.py (user activity logs)

**Inputs:** SIEM alert JSON, user account history (PostgreSQL), IP reputation service, geography data

**Agent pattern:** Supervisor — main coordinator spawns 3 parallel sub-agents: (1) IP reputation analyzer, (2) user behavior analyzer, (3) login timeline correlator

**RAG pattern:** `raptor` — hierarchical analysis of potentially large user activity logs: raw login events → per-hour summaries → daily patterns → anomaly detection at each level

**Memory used:** Execution memory (past incidents for this user account), Episodic memory (known threat patterns: credential stuffing signatures, brute force velocity), Reflexion memory (lesson: "always check VPN/proxy flag before flagging impossible travel")

**Ingestion path:** SIEM alert JSON → structured parsing; PostgreSQL user activity → `postgres_server.py:query` → `TimestampChunker` → `text-embedding-3-small`; threat intelligence feed → `HeadingChunker`

**Retrieval path:** User ID + IP + timestamp → `raptor`: (1) cluster login events by time window, (2) summarize per-hour login counts, (3) detect velocity anomalies and geo-jumps at summary level, (4) drill into raw events for confirmed anomaly

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: **mandatory before any account suspension action** — analyst must approve via Jira ticket or Slack
- PolicyEngine: `account.suspend_user` requires ADMIN scope + HITL approval; SIEM tools are read-only
- GuardrailChecker: PII redaction on user emails/names in all logs (show as `user_***` in audit trail)
- Audit trail: every data access and decision logged with classification label

**End-to-end flow:**
1. SIEM webhook fires → `POST /webhooks/alerts/siem` → Redis → Supervisor initializes
2. Supervisor spawns 3 parallel sub-agents via Goal Tree
3. Sub-agent 1 (IP reputation): queries IP reputation service → classifies IP: tor exit node, datacenter, residential
4. Sub-agent 2 (user behavior): `postgres_server.py:query` fetches 30-day login history; RAPTOR hierarchical analysis → baseline profile (usual hours, usual countries)
5. Sub-agent 3 (timeline): maps alert event to preceding login sequence; calculates travel time between geo-locations → impossible travel detection (< 3h between continents)
6. Supervisor aggregates findings; risk score calculation: IP_risk × 0.3 + geo_anomaly × 0.4 + velocity_anomaly × 0.3
7. Risk > 0.7: HITL request to `#security-triage` Slack with evidence summary; analyst approves/denies suspension within 15min
8. On approval: `account_server.py:suspend_user` + Jira incident ticket + PagerDuty alert

**Observability:** Supervisor SSE showing 3 parallel sub-agents; RAPTOR SSE summarization levels; HITL SSE event; `GOAL_DURATION` target < 5min (excluding HITL); cost ~$0.15/investigation

**Eval path:** `grounding` (risk score references actual log evidence), `safety` (no account suspension without HITL), `goal_success` (HITL resolved and Jira ticket created)

**Expected output:** Jira security incident ticket with: risk score, evidence (IP reputation, geo timeline, velocity), HITL decision log, recommended action (suspend/monitor/close as false positive)

**Failure modes:** PostgreSQL query returns > 100K rows → RAPTOR pagination with summary chain; IP reputation service unavailable → lower confidence flag + manual review; HITL timeout (15min) → auto-escalate to security manager; impossible travel false positive (user on VPN) → Reflexion stores VPN ASN list lesson

**Code references:** `app/agent/patterns/supervisor.py`, `app/mcp/servers/postgres_server.py`, `app/rag/agentic/patterns/raptor.py`, `app/governance/hitl.py`, `app/agent/patterns/goal_tree.py`

---

## Use Case 17: Detect Exposed GitHub Secrets

**Goal:** Search a GitHub organization's repositories for exposed API keys, credentials, and tokens using pattern matching, triage findings by severity, and create immediate remediation tickets.

**Business problem:** Accidentally committed secrets are a leading source of cloud account breaches; automated continuous scanning and instant ticketing reduces exposure window from days to minutes.

**Actors:** Security engineer, AgentVerse agent, GitHub MCP server, Jira MCP server

**Inputs:** GitHub organization name, secret pattern library (regex patterns for AWS keys, GCP credentials, Stripe tokens, etc.), known-safe patterns exclusion list

**Agent pattern:** ReAct — iterative scan: search code → validate finding → deduplicate → remediate → ticket

**RAG pattern:** `lexical` — secret patterns are exact regex; BM25 search for known false-positive strings (test fixtures, example values) in exclusion list KB

**Memory used:** Execution memory (secrets already reported in previous scans), Reflexion memory (lesson: "check if file is in test fixtures directory before reporting")

**Ingestion path:** GitHub code search results → `SemanticChunker` (by file + line range) → `text-embedding-3-small`; exclusion pattern list → `postgres_server.py` → exact match lookup

**Retrieval path:** Code snippet → `lexical` (BM25 against false positive patterns: placeholder values like `EXAMPLE_KEY`, test fixture paths) → novel secrets passed to triage

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- GuardrailChecker: injection detection on repository names and search queries (prevent prompt injection via malicious repo content)
- PolicyEngine: `github.search_code` requires `github:read` scope; `github.create_pr` for secret rotation requires `github:write` scope
- Output sanitization: actual secret values never logged in plaintext — redacted as `sk_live_***` in all audit records
- Audit trail: every found secret logged with file path, commit SHA, and discovery timestamp

**End-to-end flow:**
1. NLScheduler triggers daily scan goal via `fire_due_schedules`
2. `_node_initialize`: lexical strategy; GuardrailChecker activated for injection detection on search results
3. `_node_execute (search phase)`: `github_server.py:search_code` with regex patterns for 15 secret types (AWS `AKIA*`, GCP service account JSON, Stripe `sk_live_*`, JWT `eyJ*`, etc.)
4. `_node_execute (validation phase)`: for each candidate — (a) check file path against test fixtures exclusion, (b) validate format with stricter regex, (c) check entropy (Shannon entropy > 4.5 for random secrets)
5. Deduplication: `RedisDeduplicationCache` filters already-reported secrets by file path + line number
6. Triage: severity based on secret type (cloud credentials → CRITICAL, internal API keys → HIGH, third-party tokens → HIGH)
7. Remediation: for CRITICAL secrets → `github_server.py:create_pr` to delete/rotate; `jira_server.py:create_issue` per finding with SLA (CRITICAL: 2h, HIGH: 24h)
8. `_node_verify`: scan completeness check; confirms no pattern type was skipped

**Observability:** `TOOL_CALL_TOTAL` for GitHub search API; deduplication hit rate; false positive rate tracked per pattern type; `GOAL_DURATION` target < 15min per org scan; cost ~$0.05/scan

**Eval path:** `goal_success` (all CRITICAL secrets ticketed), `tool_success_rate` (GitHub API success), secret detection precision (tracked via feedback loop)

**Expected output:** Jira tickets per exposed secret with file path, commit SHA, secret type, severity, SLA, and remediation steps; daily summary report to `#security-alerts`

**Failure modes:** GitHub API secondary rate limit → chunked search with 10s delays between patterns; false positive storm from test files → exclusion list updated via Reflexion lesson; secret already revoked → verifier detects inactive credential → ticket created as informational; private fork with secrets → access denied → logged as "unable to scan, manual review required"

**Code references:** `app/mcp/servers/github_server.py`, `app/rag/engine.py:retrieve_lexical`, `app/intelligence/guardrails.py`, `app/reliability/dedup.py`, `app/governance/audit.py`

---

## Use Case 18: Generate Security Hardening Guide

**Goal:** Analyze an organization's infrastructure configuration (Terraform, Kubernetes manifests, Docker configs) and generate a prioritized security hardening guide mapped to CIS benchmarks and OWASP Top 10.

**Business problem:** Security teams lack the bandwidth to manually assess infrastructure against hundreds of benchmark controls; automated analysis with prioritized remediation accelerates security posture improvement.

**Actors:** CISO, security engineer, AgentVerse agent, Confluence MCP server, GitHub MCP server

**Inputs:** Terraform configurations, Kubernetes manifests, Dockerfile collection, CIS Benchmark PDFs (AWS/Kubernetes), OWASP Top 10 documentation

**Agent pattern:** Peer Review — (1) technical analysis pass maps configurations to benchmark controls, (2) risk prioritization pass scores business impact and remediation effort

**RAG pattern:** `raptor` — hierarchical processing of large benchmark documents: individual controls → benchmark sections → overall compliance posture; enables efficient retrieval from 400+ page CIS PDF

**Memory used:** Execution memory (past hardening assessments for this infrastructure), Procedural memory (learned: always check network security groups before compute hardening)

**Ingestion path:** CIS Benchmark PDFs → `PDFParser` + `HeadingChunker` (by control number) → `text-embedding-3-large` → `knowledge_chunks_3072`; Terraform/K8s files → `SemanticChunker` → `voyage-code-3`; both indexed in `knowledge_chunks_1024`

**Retrieval path:** Infrastructure resource type → `raptor`: (1) retrieve applicable benchmark section, (2) summarize control requirements, (3) match against actual configuration → gap analysis

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `text-embedding-3-large`

**Guardrails and governance:**
- PolicyEngine: GitHub read and Confluence write required; no infrastructure mutations (read-only assessment)
- GuardrailChecker: output must not expose internal IP ranges, account IDs, or access keys in generated docs
- Governance bundle: REGULATED (full audit trail, compliance mapping, evidence chain)
- Audit trail: all configuration files accessed logged with SHA and timestamp

**End-to-end flow:**
1. `POST /goals` with repository list and target standards (CIS Level 2, OWASP); GoalService queues to `goals.enterprise`
2. `_node_initialize`: REGULATED bundle activated; RAPTOR strategy for benchmark PDFs; Peer Review pattern
3. `_node_rag_retrieval`: RAPTOR indexes CIS Benchmark PDF (400+ controls) into hierarchical summaries; fetches all Terraform + K8s files from GitHub
4. `_node_plan`: maps resource types to benchmark control categories (Network, Identity, Logging, Encryption, Compute)
5. `_node_execute (Pass 1 — Technical)`: for each control — (a) identify applicable resources, (b) check configuration against control requirement, (c) classify: PASS / FAIL / NOT_APPLICABLE / MANUAL_REVIEW
6. `_node_peer_review (Pass 2 — Risk)`: independent reviewer scores each FAIL by business impact × remediation effort → priority matrix (Quick wins vs Strategic vs Deferred)
7. Generates guide: Executive Summary → Priority Matrix → Control-by-Control findings → Remediation Playbook
8. `confluence_server.py:create_page` publishes guide; `github_server.py:create_issue` per HIGH priority finding

**Observability:** RAPTOR SSE with summarization hierarchy levels; `peer_review_score` per benchmark section; `GOAL_DURATION` target < 20min; cost ~$0.80 for full assessment; compliance score metric emitted

**Eval path:** `grounding` (findings reference actual configuration lines), `safety` (no CRITICAL controls missed), `goal_success` (Confluence guide published)

**Expected output:** Confluence security hardening guide with: compliance score (X/100), priority matrix, per-control findings with code references, remediation scripts where available

**Failure modes:** PDF parsing extracts garbled text → fallback to manual section extraction with `HeadingChunker`; infrastructure configuration uses proprietary modules → agent flags as "requires manual inspection"; benchmark version mismatch → Reflexion stores "always verify benchmark version against cloud provider version" lesson

**Code references:** `app/ingestion/parsers/pdf_parser.py`, `app/rag/agentic/patterns/raptor.py`, `app/agent/patterns/peer_review.py`, `app/mcp/servers/confluence_server.py`, `app/governance/audit.py`

---

## Use Case 19: Correlate SIEM Events to MITRE ATT&CK

**Goal:** Analyze a batch of SIEM security events, correlate them into attack sequences, map each sequence to MITRE ATT&CK tactics and techniques, and produce an intelligence report with threat actor attribution hypotheses.

**Business problem:** Individual SIEM alerts are noise; correlating them into attack sequences and mapping to ATT&CK framework provides actionable threat intelligence for SOC analysts.

**Actors:** Threat intelligence analyst, AgentVerse agent, SIEM server

**Inputs:** SIEM event batch (JSON, up to 10K events), MITRE ATT&CK knowledge graph (STIX 2.1 format), threat actor playbooks from Confluence

**Agent pattern:** Debate — two adversarial agents debate attribution hypotheses: Agent A argues for Threat Actor X based on TTPs, Agent B challenges with alternative explanations; mediator synthesizes final assessment

**RAG pattern:** `graph` — MITRE ATT&CK STIX knowledge graph enables relationship traversal: Event → Technique → Tactic → Group → Mitigation; graph retrieval surfaces related techniques and known threat actor TTPs

**Memory used:** Execution memory (past correlated attack sequences for this environment), Episodic memory (known threat actor TTPs observed in previous incidents), Long-term memory (ATT&CK technique evolution over time)

**Ingestion path:** MITRE ATT&CK STIX 2.1 → `KnowledgeGraphIngester` → graph nodes/edges in `knowledge_graph` table; SIEM events → `TimestampChunker` → `text-embedding-3-small`; threat actor playbooks → `HeadingChunker`

**Retrieval path:** SIEM event type → `graph`: (1) map event to ATT&CK technique via graph node lookup, (2) traverse technique → tactic chain, (3) retrieve related threat groups using this TTP combination, (4) graph-expand to related techniques for pattern completion

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- PolicyEngine: SIEM read tools require `siem:read` scope; all operations are read-only analysis
- GuardrailChecker: attribution statements must include confidence levels (HIGH/MEDIUM/LOW) to prevent overconfident false attribution
- OutputContractBuilder: "MITRE" in goal → structured report with ATT&CK navigator layer JSON
- Audit trail: full reasoning chain logged for each attribution decision

**End-to-end flow:**
1. Analyst uploads SIEM event batch; `POST /goals` with time range; queues to `goals.enterprise`
2. `_node_rag_retrieval`: graph retrieval indexes SIEM events; maps event types to ATT&CK technique IDs (e.g., failed logins → T1110 Brute Force)
3. `_node_plan`: identifies event clusters by source IP, target system, and 2-hour time windows → N attack sequences
4. `_node_execute (correlation)`: for each sequence — graph-traverse technique chain → identify tactic progression (Reconnaissance → Initial Access → Privilege Escalation → Lateral Movement)
5. `_node_debate (Agent A)`: constructs attribution hypothesis — "TTPs match APT29 based on technique T1078 + T1021.001 + T1560"; cites 3 specific evidence points
6. `_node_debate (Agent B)`: challenges — "T1078 is generic; T1021.001 seen in 8 other groups; could be opportunistic actor"; proposes alternative or "insufficient evidence"
7. Mediator synthesizes: consensus attribution with confidence + alternative hypotheses; generates ATT&CK navigator layer JSON
8. Publishes intelligence report to Confluence with navigator visualization link

**Observability:** Debate SSE events showing both agent positions; graph traversal depth metric; `GOAL_DURATION` target < 10min per batch; cost ~$0.50 for 10K events; attribution confidence distribution

**Eval path:** `grounding` (technique mappings reference actual ATT&CK technique IDs), `goal_success` (report published), technique precision vs SOC analyst ground truth (A/B eval)

**Expected output:** Threat intelligence report with: attack sequence timelines, ATT&CK TTP mapping table, attribution hypotheses with confidence, ATT&CK navigator layer JSON, recommended detections

**Failure modes:** STIX knowledge graph not loaded → fallback to text-based ATT&CK lookup; events span multiple tenants → isolation enforced, cross-tenant correlation blocked by RLS; debate agents reach impasse → mediator votes for "insufficient evidence" with confidence LOW; graph traversal too deep → depth limit 5 hops with cost tracking

**Code references:** `app/rag/agentic/patterns/graph_retrieval.py`, `app/agent/patterns/debate.py`, `app/ingestion/parsers/knowledge_graph_ingester.py`, `app/governance/audit.py`, `app/mcp/servers/confluence_server.py`

---

## Use Case 20: Generate SOC2 Type II Evidence Package

**Goal:** Collect, organize, and validate all evidence required for a SOC2 Type II audit across the trust service criteria (Security, Availability, Confidentiality, Processing Integrity, Privacy) and compile into a structured evidence package.

**Business problem:** SOC2 Type II audit evidence collection is a months-long manual process; automated evidence collection from live systems reduces preparation time from 8 weeks to 3 days and ensures completeness.

**Actors:** Compliance officer, security team, AgentVerse agent, audit.py (AgentVerse audit trail), postgres_server.py, Confluence MCP server

**Agent pattern:** Goal Tree — decomposes into 5 parallel sub-goals, one per trust service criteria, each collecting and validating its own evidence domain

**RAG pattern:** `raptor` — hierarchical synthesis of large evidence sets: individual log entries → per-control summaries → criteria-level attestation → overall SOC2 compliance narrative

**Memory used:** Execution memory (evidence collected in previous audit cycles for gap analysis), Long-term memory (prior audit findings and remediation commitments), Procedural memory (SOC2 evidence checklist sequence)

**Ingestion path:** AgentVerse audit logs → `audit.py:export_audit_trail` → `TimestampChunker`; Confluence policies → `HeadingChunker`; PostgreSQL access logs → `postgres_server.py:query`; all → `text-embedding-3-large` → `knowledge_chunks_3072`

**Retrieval path:** SOC2 control ID → `raptor`: (1) retrieve applicable audit events for control period (12 months), (2) summarize per-month, (3) synthesize criteria-level narrative, (4) cross-reference with policy documentation

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-5.2`, embedder: `text-embedding-3-large`

**Guardrails and governance:**
- HITL: **mandatory for each trust criteria attestation** — compliance officer must review and attest before evidence is finalized
- Peer Review: independent reviewer validates evidence completeness and control mapping for each criteria
- PolicyEngine: audit log export requires ADMIN scope; all evidence access logged
- Governance bundle: REGULATED (immutable audit trail, full cost tracking, policy chain)
- GuardrailChecker: PII in evidence → auto-redact before packaging per privacy policy
- Audit trail: every evidence artifact accessed, processed, and included is logged with hash

**End-to-end flow:**
1. `POST /goals` with audit period (12-month range); GoalService queues to `goals.enterprise`; REGULATED bundle activated
2. `_node_initialize`: Goal Tree spawns 5 parallel sub-goals (CC1–CC9 Security, A1 Availability, C1 Confidentiality, PI1 Processing Integrity, P1–P8 Privacy)
3. Per sub-goal: RAPTOR ingests 12 months of relevant logs → hierarchical summaries per control period
4. Security criteria: `audit.py:export_audit_trail` → access reviews, change management, incident response evidence; `postgres_server.py:query` → access log completeness
5. Availability criteria: Datadog uptime metrics → SLA evidence; incident postmortems from Confluence
6. `_node_peer_review`: independent reviewer validates evidence → Peer Review score > 0.85 required per criteria; gaps trigger targeted retrieval
7. HITL gate per criteria: compliance officer reviews evidence package section → attests or requests additional evidence
8. Final assembly: `confluence_server.py:create_page` structured evidence package with control cross-reference matrix; evidence artifact hashes for tamper-evidence

**Observability:** Goal Tree SSE showing 5 parallel criteria agents; HITL SSE events × 5; RAPTOR summarization depth; `GOAL_DURATION` target < 4h for full package; cost ~$5.00 for 12-month evidence package; compliance coverage percentage metric

**Eval path:** `safety` (all required controls have evidence), `grounding` (attestations reference actual audit events with timestamps), `goal_success` (all 5 HITL attestations completed and package published)

**Expected output:** SOC2 evidence package in Confluence with: control cross-reference matrix, per-criteria evidence inventory (artifacts with hashes), gap analysis, compliance officer attestation log, audit-ready formatted export

**Failure modes:** 12-month log gap detected → Reflexion lesson + gap flagged as audit finding; HITL attestation timeout → evidence section marked "pending attestation" and escalated; PII found in audit logs → redacted copy created with redaction log; evidence volume too large for single session → chunked processing across multiple Goal Tree iterations with state checkpointing via `AsyncRedisSaver`

**Code references:** `app/governance/audit.py`, `app/mcp/servers/postgres_server.py`, `app/agent/patterns/goal_tree.py`, `app/rag/agentic/patterns/raptor.py`, `app/agent/patterns/peer_review.py`, `app/governance/hitl.py`, `app/mcp/servers/confluence_server.py`
