# Use Cases 1–10: Software Engineering & DevOps/SRE

---

## Use Case 1: GitHub PR Code Review

**Goal:** Analyze a GitHub pull request and post a detailed code review comment covering correctness, security, performance, and style.

**Business problem:** Engineers spend hours reviewing PRs; automated triage of obvious issues (security holes, N+1 queries, missing tests) frees reviewers for high-level design critique.

**Actors:** Developer (submits PR), AgentVerse agent, GitHub MCP server

**Inputs:** PR diff, repository source files, coding standards Confluence page

**Agent pattern:** ReAct — iterative reasoning with tool calls to fetch diff, context, and post review

**RAG pattern:** `corrective_rag` — fetches relevant source files from KB; falls back to web if confidence < 0.5 for third-party library patterns

**Memory used:** Execution memory (past review decisions for this repo), Procedural memory (learned review tool sequence)

**Ingestion path:** GitHub repo → `ASTChunker` → `text-embedding-3-small` → `knowledge_chunks_1536`

**Retrieval path:** PR diff → `hybrid` (lexical for function names + vector for semantic similarity) → ColBERT reranking → citations to relevant lines

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- `GuardrailChecker.check_goal()` screens for injection in PR description
- PolicyEngine: GitHub write tools require OPERATOR role scope
- Audit trail: every review comment logged

**End-to-end flow:**
1. `POST /goals` → GoalService queues to `goals.professional` Celery queue
2. `_node_initialize`: RuntimeProfileBuilder selects `corrective_rag` strategy; SSE `pattern_assembled`
3. `_node_rag_retrieval`: fetches repo structure via `github_server.py:search_code`; embeds and indexes changed files
4. `_node_plan`: ContextPipeline builds planner context with diff + coding standards; OutputContractBuilder detects structured output needed
5. `_node_execute`: calls `github_server.py:get_pr_diff`, `github_server.py:get_file_content` for each changed file; runs security pattern matching; calls `github_server.py:post_review_comment`
6. `_node_verify`: verifier LLM checks review is factual and references actual diff lines; RuntimeScorecard scores grounding dimension

**Observability:** SSE `step_complete` per tool call; `TOOL_CALL_TOTAL` counter; cost breakdown tracks reviewer LLM tokens; `GOAL_DURATION` histogram

**Eval path:** `grounding` (citations match actual diff lines), `tool_success_rate` (GitHub API success), `goal_success` (review posted)

**Expected output:** GitHub review comment with inline annotations and summary

**Failure modes:** GitHub rate limit → circuit breaker opens → retry with exponential backoff; diff too large → chunk and summarize; invalid repo permissions → DENY from PolicyEngine

**Code references:** `app/mcp/servers/github_server.py`, `app/agent/graph.py:_node_execute`, `app/rag/engine.py:retrieve`, `app/reliability/circuit_breaker.py`

---

## Use Case 2: Auto-Generate pytest Unit Tests

**Goal:** Generate a comprehensive pytest test suite for a specified Python module including happy paths, edge cases, and error conditions.

**Business problem:** Test coverage gaps lead to production bugs; generating initial test scaffolding accelerates TDD adoption.

**Actors:** Engineer, AgentVerse agent, GitHub MCP server

**Inputs:** Python source file, existing tests in the repo, project's `pyproject.toml`

**Agent pattern:** Tree of Thoughts — generates N test approaches (boundary value analysis, equivalence partitioning, property-based), evaluates each, expands the best

**RAG pattern:** `colbert` — late-interaction reranking on similar test files in the codebase for style consistency

**Memory used:** Procedural memory (learned testing patterns from past test generation goals), Execution memory

**Ingestion path:** Python files → `ASTChunker` (function/class boundaries) → `voyage-code-3` (1024-dim code embeddings) → `knowledge_chunks_1024`

**Retrieval path:** Target function signature → `colbert` strategy → MaxSim token-level scoring → top-5 similar tested functions as examples

**Model routing:** planner: `gpt-5.2`, executor: `gpt-5.2`, verifier: `gpt-4o`, embedder: `voyage-code-3`

**Guardrails and governance:**
- GuardrailChecker screens generated code for dangerous patterns (`exec(`, `eval(`, `subprocess` without allowlist)
- PolicyEngine: GitHub write requires scope `code:write`
- `BLACKLIST_TOOL_PATTERN` fires if test generation consistently fails quality gate

**End-to-end flow:**
1. Goal submitted; `enable_tree_of_thoughts=True` from PatternConfig (complexity=EXPERT + domain=CODE)
2. `_node_rag_retrieval`: fetches target module + similar tested modules via `colbert` retrieval
3. `_node_tree_of_thoughts`: generates 3 testing strategies; evaluates each (BVA score=0.9, EP score=0.7, PBT score=0.8); expands BVA branch
4. `_node_plan`: ContextPipeline assembles test structure; procedural memory injects "always mock external I/O" skill
5. `_node_execute`: generates test file, validates syntax via `python -c` check, posts to GitHub
6. `_node_verify`: verifier checks test coverage completeness; `enable_peer_review` scores quality 0-1

**Observability:** `tree_of_thoughts` SSE events; token cost per strategy branch; `eval_score_recorded` with `goal_success` and `citation_quality`

**Eval path:** `grounding` (tests reference actual code), `goal_success` (tests pass syntax check), `tool_success_rate`

**Expected output:** `test_module_name.py` with 15-30 test functions

**Failure modes:** Target module uses heavy C extensions → fallback to stub-based tests; test file too large → split into test classes; pytest import errors → reflexion stores "check import paths" lesson

**Code references:** `app/agent/patterns/tree_of_thoughts.py`, `app/rag/agentic/patterns/colbert.py`, `app/ingestion/chunkers/ast_chunker.py`, `app/mcp/servers/github_server.py`

---

## Use Case 3: Find TODOs → Create Jira Tickets

**Goal:** Search a GitHub repository for all TODO/FIXME/HACK comments, categorize them by severity, and create corresponding Jira tickets with appropriate priority and labels.

**Business problem:** Technical debt accumulates invisibly in code comments; systematic tracking enables planning and prioritization.

**Actors:** Tech lead, AgentVerse agent, GitHub and Jira MCP servers

**Inputs:** GitHub repository, Jira project configuration, severity classification rules

**Agent pattern:** Plan-Execute with Goal Tree — parallel sub-goals: one per repository section

**RAG pattern:** `lexical` — exact pattern matching for TODO/FIXME/HACK/XXX strings (structured IDs don't need semantic search)

**Memory used:** Execution memory (past TODO-to-ticket mapping), Reflexion memory (lesson: "always check for duplicates before creating ticket")

**Ingestion path:** GitHub code search → text results → `SemanticChunker` → `text-embedding-3-small`

**Retrieval path:** `lexical` strategy → grep-style FTS for `TODO|FIXME|HACK` → no reranking needed (exact match)

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- PolicyEngine: `jira.create_issue` requires `jira:write` scope
- HITL: production-affecting components require human approval before ticket creation
- Deduplication: `RedisDeduplicationCache` prevents same TODO being ticketed twice
- Audit trail: all created tickets logged with source file:line

**End-to-end flow:**
1. Plan-Execute: agent plans 4 sub-goals (search backend, search frontend, search infra, search tests)
2. Goal Tree spawns 4 parallel `github_server.py:search_code` calls with `TODO|FIXME|HACK` regex
3. Results merged; LLM classifies each: P1 (security/data loss), P2 (functionality), P3 (quality)
4. Dedup check against existing Jira tickets via `jira_server.py:search_issues`
5. `jira_server.py:create_issue` called per TODO (capped at 50/run to avoid spam)
6. Summary report posted as Confluence page

**Observability:** `TOOL_CALL_TOTAL` for Jira creates; cost per ticket ~$0.002; `step_complete` SSE per batch

**Eval path:** `goal_success` (tickets created), `tool_success_rate` (Jira API success rate)

**Expected output:** N Jira tickets with title, description, severity, source file:line, and component label

**Failure modes:** Jira rate limit → bulkhead slows creation rate; duplicate detection fails → reflexion lesson stored; GitHub search quota → chunked search with delay

**Code references:** `app/mcp/servers/github_server.py`, `app/mcp/servers/jira_server.py`, `app/agent/patterns/goal_tree.py`, `app/reliability/dedup.py`

---

## Use Case 4: Debug Failing CI Build

**Goal:** Analyze a failing GitHub Actions CI build log, identify the root cause, and either automatically fix it or create a Jira ticket with root cause analysis and proposed fix.

**Business problem:** Build failures block entire teams; automated diagnosis reduces mean time to resolution from hours to minutes.

**Actors:** Engineer, AgentVerse agent, GitHub + Jira MCP servers

**Inputs:** CI build log URL, repository source, test results XML

**Agent pattern:** Reflection — iterates on failure diagnosis when initial fix hypothesis is wrong

**RAG pattern:** `corrective_rag` — fetches error context from KB; web fallback for third-party library errors with confidence < 0.5

**Memory used:** Execution memory (past build failures and their fixes), Reflexion memory (common build failure patterns)

**Ingestion path:** CI logs → `TimestampChunker` → `text-embedding-3-small`; error messages → keyword extraction

**Retrieval path:** Error message → `hybrid` (exact error string via FTS + semantic for similar errors) → parent-child expansion for full stack trace context

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- GuardrailChecker: auto-fix PRs require `code:write` scope + OPERATOR role
- HITL: production branch fixes require human approval
- PolicyEngine: `github.merge_pr` is REQUIRE_APPROVAL for main branch

**End-to-end flow:**
1. `_node_rag_retrieval`: fetches CI log, indexes with `TimestampChunker`; retrieves past similar failures from execution memory
2. `_node_plan`: Reflexion lessons ("check dependency version mismatches first") in context
3. `_node_execute`: parses log for error signatures; calls `github_server.py:get_file_content` for failing test; diagnoses: import error, test assertion, resource limit
4. If fixable: generates patch, creates PR via `github_server.py:create_pr`
5. If complex: creates Jira ticket via `jira_server.py:create_issue` with full RCA
6. `_node_verify`: checks diagnosis is supported by log evidence (grounding check)
7. On wrong diagnosis: `_node_reflect` generates revised hypothesis; replans

**Observability:** Reflection SSE events when diagnosis fails; `GOAL_DURATION` tracks diagnosis time; reflexion lesson stored on pattern discovery

**Eval path:** `grounding` (diagnosis references actual log lines), `goal_success` (ticket created or PR opened), `rag_quality`

**Expected output:** Either a PR with fix or Jira ticket with root cause, fix proposal, and log excerpt

**Failure modes:** Log too large → chunked analysis with RAPTOR summarization; flaky test → detected by historical pattern in execution memory; credentials error in CI → flagged as security issue for security team

**Code references:** `app/agent/patterns/reflection.py`, `app/mcp/servers/github_server.py`, `app/rag/agentic/retriever_tool.py:retrieve_corrective`, `app/ingestion/chunkers/timestamp.py`

---

## Use Case 5: Generate OpenAPI Documentation

**Goal:** Analyze FastAPI source code and generate comprehensive OpenAPI 3.0 documentation including descriptions, examples, error responses, and authentication requirements.

**Business problem:** API documentation is perpetually out of date; auto-generating from source ensures accuracy.

**Actors:** Platform engineer, AgentVerse agent, Confluence MCP server

**Inputs:** FastAPI application source (`app/api/*.py`), existing docstrings, authentication configuration

**Agent pattern:** Plan-Execute — structured plan: discover routes → analyze each → generate schema → publish

**RAG pattern:** `multi_hop` — decomposes into sub-queries: "what are all route definitions?" + "what authentication does each use?" + "what are the request/response models?"

**Memory used:** Execution memory (previously documented endpoints), Procedural memory (learned: always check `Depends()` for auth requirements)

**Ingestion path:** Python API files → `ASTChunker` (function-level) → `voyage-code-3` → `knowledge_chunks_1024`

**Retrieval path:** `multi_hop`: (1) scan for `@router.get/post/put/delete` decorators, (2) fetch Pydantic model definitions, (3) fetch auth middleware config → final context assembly

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`, embedder: `voyage-code-3`

**Guardrails and governance:**
- PolicyEngine: Confluence write requires `confluence:write` scope
- No HITL required (documentation is non-destructive)
- OutputContractBuilder: detects "OpenAPI" in goal → JSON output contract

**End-to-end flow:**
1. AST-chunk all files in `app/api/`; index with code embeddings
2. Multi-hop: find all route handlers → fetch Pydantic models → fetch auth decorators
3. Plan: list all 40 endpoints with their models
4. Execute: for each endpoint, generate OpenAPI path object with descriptions, examples, 4xx/5xx responses
5. Assemble full `openapi.json`; validate against OpenAPI 3.0 schema
6. Publish to Confluence via `confluence_server.py:create_page`

**Observability:** `multi_hop` SSE with sub-query count; token cost scales with endpoint count (~$0.15 for 40 endpoints); `GOAL_DURATION`

**Eval path:** `grounding` (all documented endpoints exist in code), `tool_success_rate` (Confluence publish success)

**Expected output:** Complete `openapi.json` and Confluence page with rendered API docs

**Failure modes:** Missing Pydantic model definition → grounding check fails → reflexion notes gap; Confluence space permission denied → retry with different space or Jira attachment fallback

**Code references:** `app/ingestion/chunkers/ast_chunker.py`, `app/rag/engine.py:retrieve_multi_hop`, `app/mcp/servers/confluence_server.py`, `app/context/output_contract_builder.py`

---

## Use Case 6: Migrate Deprecated Library Usage

**Goal:** Find all usages of deprecated `urllib2` and `httplib` across a Python codebase and migrate them to the `requests` library, creating a PR with all changes.

**Business problem:** Deprecated Python 2 libraries in Python 3 codebases cause maintenance burden and security gaps.

**Actors:** Platform engineer, AgentVerse agent, GitHub MCP server

**Inputs:** GitHub repository, migration guide documentation

**Agent pattern:** Plan-Execute with Goal Tree — parallel sub-goals per module, then final PR creation

**RAG pattern:** `fusion_rag` — multi-query expansion: "urllib2 usage patterns", "httplib usage patterns", "requests migration examples" → parallel retrieval → RRF fusion

**Memory used:** Procedural memory (migration tool sequence: find → analyze → replace → test → PR), Episodic memory (past migration outcomes)

**Ingestion path:** Python files → `ASTChunker` → `voyage-code-3`; migration guide → `HeadingChunker` → `text-embedding-3-small`

**Retrieval path:** `fusion_rag`: 3 query variants → parallel `hybrid_search` → `rrf_fuse` → top-10 examples of migration patterns

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- PolicyEngine: `github.create_pr` requires OPERATOR scope
- HITL: PRs to production branches require human approval
- RollbackEngine: registers PR creation as compensating action (can close PR if migration fails)

**End-to-end flow:**
1. Fusion RAG retrieves urllib2 patterns from codebase + migration examples from docs
2. Goal Tree spawns parallel agents per module (backend/, tests/, scripts/)
3. Each agent: finds all `import urllib2` / `from httplib import` → rewrites to `import requests` with equivalent HTTP methods
4. Parallel agents merge results; conflict resolution if same file edited by multiple
5. Generates unified diff → creates PR via `github_server.py:create_pr`
6. Verifier checks: all urllib2 references removed, requests correctly used, no syntax errors

**Observability:** Goal Tree SSE showing parallel agent progress; cost per module (~$0.01); `eval_score_recorded`

**Eval path:** `goal_success` (PR created), `grounding` (changes reference actual deprecated usages), `tool_success_rate`

**Expected output:** GitHub PR with all urllib2/httplib → requests migrations

**Failure modes:** File has complex urllib2 streaming usage → agent flags for manual review and skips; merge conflict between parallel agents → serializes conflicting modules; test failures → reflexion lesson stored

**Code references:** `app/agent/patterns/goal_tree.py`, `app/rag/engine.py:retrieve_fusion`, `app/rag/agentic/query_expander.py`, `app/mcp/servers/github_server.py`

---

## Use Case 7: Review Database Migration for Safety

**Goal:** Analyze a pending Alembic migration script and flag any operations that could cause data loss, downtime, or irreversible changes before it reaches production.

**Business problem:** Database migrations that drop columns, change types, or lock tables cause production incidents; automated review catches issues before deploy.

**Actors:** DBA/Backend engineer, AgentVerse agent, GitHub MCP server

**Agent pattern:** Peer Review — two-pass: first analyzes technically, second reviews from operations perspective with independent scoring

**RAG pattern:** `hybrid` — retrieves company DB migration standards from Confluence + known risky SQL patterns from KB

**Memory used:** Execution memory (past migration incidents and their patterns), Reflexion memory (lesson: "always check if column has NOT NULL constraint before adding it")

**Ingestion path:** SQL migration files → `SemanticChunker` (by DDL statement) → `text-embedding-3-small`; migration standards → `HeadingChunker`

**Retrieval path:** Migration SQL → `hybrid` (FTS for `DROP`, `TRUNCATE`, `ALTER` keywords + vector for semantic match to risky patterns) → sorted by risk score

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-5.2` (high stakes)

**Guardrails and governance:**
- GuardrailChecker: detects `DROP TABLE`, `TRUNCATE`, `DELETE FROM` patterns → logs HIGH RISK
- HITL: **always** triggered for migrations containing DROP/TRUNCATE operations
- PolicyEngine: `github.approve_pr` requires ADMIN scope for DB migration PRs
- Audit trail: every review decision logged with risk reasoning

**End-to-end flow:**
1. `_node_rag_retrieval`: fetches migration script + past incident reports + migration standards
2. `_node_plan`: identifies DDL operations: ADD COLUMN, DROP COLUMN, CREATE INDEX, ALTER TYPE
3. `_node_execute (Peer Review pass 1)`: technical analysis — NOT NULL without default? Table lock? Backward compatible?
4. `_node_peer_review`: independent reviewer scores quality 0-1; low quality → inject critique → replan
5. `_node_execute (Peer Review pass 2)`: operations analysis — zero-downtime compatible? Rollback possible? Estimated lock duration?
6. Posts review as GitHub PR comment with risk matrix

**Observability:** `peer_review_score` in context; HITL SSE event for DROP operations; `eval_score_recorded` with `safety` dimension

**Eval path:** `safety` (no dangerous patterns missed), `grounding` (risk claims reference actual SQL), `goal_success`

**Expected output:** GitHub PR review comment with risk matrix: operation | risk level | recommendation | rollback plan

**Failure modes:** Complex migration with multiple operations → RAPTOR for hierarchical analysis; verifier disagrees with peer reviewer → consensus vote; migration too long → chunked analysis

**Code references:** `app/agent/patterns/peer_review.py`, `app/intelligence/guardrails.py`, `app/governance/hitl.py`, `app/mcp/servers/github_server.py`

---

## Use Case 8: Generate CHANGELOG from Git Commits

**Goal:** Analyze all git commits since the last release tag, categorize them (features, fixes, breaking changes, deprecations), and generate a formatted CHANGELOG entry.

**Business problem:** Manually curating CHANGELOG is tedious and error-prone; automated generation from commit history saves time and ensures completeness.

**Actors:** Release engineer, AgentVerse agent, GitHub MCP server, Confluence MCP server

**Agent pattern:** ReAct — iterative: fetch commits → categorize → format → publish

**RAG pattern:** `lexical` — commit messages are structured text; exact keyword matching for `feat:`, `fix:`, `BREAKING CHANGE:` (Conventional Commits format)

**Memory used:** Procedural memory (learned: "check for revert commits to cancel out features"), Session memory (commit list within this execution)

**Ingestion path:** GitHub commit history → `TimestampChunker` (by day) → `text-embedding-3-small`

**Retrieval path:** `lexical` strategy → FTS for conventional commit prefixes → sorted by date → no reranking needed

**Model routing:** planner: `gpt-4o-mini`, executor: `gpt-4o-mini`, verifier: `gpt-4o-mini`

**Guardrails and governance:**
- PolicyEngine: Confluence write requires `confluence:write` scope
- No HITL (documentation, non-destructive)
- OutputContractBuilder: "CHANGELOG" in goal → markdown output contract

**End-to-end flow:**
1. `github_server.py:list_commits` since last tag → fetch 200 commits
2. Lexical retrieval extracts feat/fix/chore/docs/BREAKING commits
3. LLM groups related commits (e.g., 5 commits for same feature → one entry)
4. Generates CHANGELOG entry with sections: Breaking Changes, New Features, Bug Fixes, Deprecations
5. Posts to Confluence via `confluence_server.py:create_page`
6. Creates GitHub release draft via `github_server.py:create_release`

**Observability:** Token cost ~$0.03 for 200 commits; `GOAL_DURATION`; `tool_success_rate` for both GitHub + Confluence

**Eval path:** `goal_success` (CHANGELOG created), `grounding` (entries reference actual commits), `tool_success_rate`

**Expected output:** Formatted CHANGELOG.md section + GitHub release draft + Confluence release notes page

**Failure modes:** Non-conventional commit messages → LLM infers intent (lower confidence); merge commits with no useful message → skipped; Confluence rate limit → retry with backoff

**Code references:** `app/mcp/servers/github_server.py`, `app/mcp/servers/confluence_server.py`, `app/ingestion/chunkers/timestamp.py`, `app/context/output_contract_builder.py`

---

## Use Case 9: Triage PagerDuty Alert → Jira Incident Ticket

**Goal:** When a PagerDuty alert fires, automatically fetch alert details, correlate with recent deployments and Datadog metrics, and create a comprehensive Jira incident ticket with runbook suggestions.

**Business problem:** On-call engineers waste critical minutes gathering context; automated triage provides a 2-minute head start on diagnosis.

**Actors:** On-call engineer, AgentVerse agent, PagerDuty + Datadog + Jira + GitHub MCP servers

**Agent pattern:** Supervisor — main agent coordinates 3 parallel sub-agents: alert analyzer, metrics analyzer, deployment correlator

**RAG pattern:** `flare` — uncertainty-driven: initial alert analysis may be uncertain; FLARE triggers targeted retrieval when confidence < threshold

**Memory used:** Execution memory (past incidents for this alert type), Reflexion memory (lesson: "check deployment timestamp before alert timestamp"), Episodic memory (similar past incidents with outcomes)

**Ingestion path:** PagerDuty alert JSON → `SemanticChunker`; runbooks (Confluence) → `HeadingChunker` → `text-embedding-3-small`

**Retrieval path:** Alert name → `flare` (uncertainty on root cause triggers retrieval of runbooks + past incidents) → ColBERT reranking for precision

**Model routing:** planner: `gpt-5.2`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: P0 incidents always require human acknowledgment before agent takes remediation actions
- PolicyEngine: PagerDuty acknowledge/resolve requires `pagerduty:write` scope
- Audit trail: all incident actions logged with agent decisions

**End-to-end flow:**
1. Trigger: PagerDuty webhook → `POST /webhooks/alerts/pagerduty` → Redis cache → `fire_due_schedules` picks up
2. Supervisor spawns 3 parallel agents: (a) alert details via `pagerduty_server.py:get_incident`, (b) metrics via `datadog_server.py:get_metrics` for affected service, (c) recent deployments via `github_server.py:list_commits` in last 2h
3. FLARE detects uncertainty in root cause → fetches relevant runbook from Confluence
4. HITL gate for P0: human acknowledges before agent proceeds
5. Jira ticket created via `jira_server.py:create_issue` with severity, timeline, metrics charts, runbook link
6. Slack notification to `#incidents` channel via `slack_server.py:send_message`

**Observability:** Supervisor SSE showing parallel agent progress; HITL SSE event; `GOAL_DURATION` (target: < 90s); cost ~$0.08/incident

**Eval path:** `goal_success` (Jira ticket created), `grounding` (ticket references actual metrics), `tool_success_rate`

**Expected output:** Jira P1 ticket with: alert details, affected service, timeline, recent deployments, relevant metrics, runbook link, Slack notification

**Failure modes:** PagerDuty API down → fallback to webhook payload in Redis; Datadog metrics query times out → ticket created without metrics (noted as gap); HITL timeout (5min) → auto-escalate to P0 bridge

**Code references:** `app/agent/patterns/supervisor.py`, `app/mcp/servers/pagerduty_server.py`, `app/mcp/servers/datadog_server.py`, `app/rag/agentic/patterns/flare.py`, `app/governance/hitl.py`

---

## Use Case 10: Diagnose Kubernetes Pod OOMKilled

**Goal:** Investigate a Kubernetes pod that was OOMKilled, identify memory leak or misconfiguration, and recommend resource limit adjustments or code fixes.

**Business problem:** OOMKilled pods cause service degradation; manual diagnosis requires correlating Kubernetes events, container metrics, and application logs.

**Actors:** SRE, AgentVerse agent, Kubernetes + Datadog + GitHub MCP servers

**Agent pattern:** Reflection — iterates when initial memory analysis doesn't explain the OOM

**RAG pattern:** `raptor` — hierarchical analysis of large log files: leaf nodes (individual log lines) → summaries (per-minute → per-hour → session) → top-level context for planning

**Memory used:** Execution memory (past OOM incidents for this service), Episodic memory (OOM resolution patterns), Reflexion memory (lesson: "check for memory leak in connection pools first")

**Ingestion path:** Kubernetes logs → `TimestampChunker` → `text-embedding-3-small`; Datadog memory metrics → structured JSON

**Retrieval path:** `raptor` strategy: cluster logs by time period → LLM summarizes each cluster → multi-level retrieval → final context includes both detail and summary

**Model routing:** planner: `gpt-4o`, executor: `gpt-4o`, verifier: `gpt-4o`, embedder: `text-embedding-3-small`

**Guardrails and governance:**
- HITL: `kubernetes.scale_deployment` and `kubernetes.delete_pod` require human approval
- PolicyEngine: pod restart allowed without approval; resource limit changes require OPERATOR scope
- Audit trail: all Kubernetes mutations logged

**End-to-end flow:**
1. `kubernetes_server.py:get_pod_events` → fetch OOM event with timestamp
2. `datadog_server.py:get_metrics` → memory usage trend for 24h before OOM
3. RAPTOR analysis of pod logs → identifies memory growth pattern (linear leak vs spike)
4. `github_server.py:search_code` → find recent memory-related commits
5. Diagnosis: (a) memory leak in object cache → recommend cache eviction fix; (b) undersized limit → recommend new limit; (c) data volume spike → recommend HPA
6. On reflection failure (diagnosis doesn't match metrics): `_node_reflect` generates alternative hypothesis (e.g., check for goroutine leak in sidecar)
7. Creates Jira ticket + Slack alert with diagnosis and recommended fix

**Observability:** RAPTOR SSE showing summarization levels; reflection SSE when replanning; `GOAL_DURATION` target < 5min; cost ~$0.12 for 24h log analysis

**Eval path:** `grounding` (diagnosis references actual log patterns), `rag_quality` (RAPTOR retrieval quality), `goal_success`

**Expected output:** Jira ticket with root cause (memory leak / undersized limits / data spike), evidence from logs/metrics, recommended fix (code change or limit adjustment), and monitoring suggestion

**Failure modes:** Logs too large for RAPTOR context window → further hierarchical chunking; Datadog API down → diagnosis based on Kubernetes events only (lower confidence noted); no recent commits found → external dependency suspected

**Code references:** `app/agent/patterns/reflection.py`, `app/rag/agentic/patterns/raptor.py`, `app/mcp/servers/kubernetes_server.py`, `app/mcp/servers/datadog_server.py`, `app/ingestion/chunkers/timestamp.py`
