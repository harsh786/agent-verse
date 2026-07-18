# AgentVerse Full-Product Live Certification Design

**Date:** 2026-07-19  
**Status:** Approved  
**Scope:** AgentVerse backend, frontend, Python SDK, TypeScript SDK, GitHub Action, infrastructure, configured model providers, configured external connectors, and all documented product domains

## 1. Objective

Certify AgentVerse as a complete, production-shaped autonomous-agent product by combining exhaustive automated checks with traceable live scenarios. The certification must demonstrate that implemented capabilities work together through real HTTP, SSE, WebSocket, database, Redis, model-provider, worker, vector-search, governance, and user-interface paths.

The run must not equate a mocked unit test with a live certification. Every result will identify its evidence level:

- **Static:** source/configuration inspection, lint, type-check, contract validation.
- **Isolated:** unit or component test with controlled dependencies.
- **Integrated:** real process and real infrastructure with a fake or local external boundary.
- **Live:** configured real model or external service used successfully.
- **Blocked:** required credential/service/capability is unavailable, with the exact boundary stated.

“All real-world scenarios” means every implemented product capability and every one of the 37 documented industry domains receives at least one attributable scenario. It does not mean that unconfigured third-party accounts are claimed as live. Those integrations remain integrated/contract-tested and are reported honestly.

## 2. Starting State

The initial repository and environment census found:

- Five independently deployable projects in one git repository.
- 61 backend API modules and more than 70 backend subsystem packages.
- 770 backend test files, including committed real-OpenAI suites.
- 57 frontend routes and 169 frontend unit/E2E test files.
- A configured OpenAI provider, PostgreSQL/pgvector, Redis, and Jira credentials.
- Running Postgres, Redis, PgBouncer, MinIO, Mailpit, SearXNG, Prometheus, Grafana, and Loki services.
- Unhealthy or restarting Keycloak, OpenTelemetry Collector, and Jaeger services.
- Backend, frontend, worker, beat, Kong, backup, and promtail services not running at census time.
- User-owned untracked files that must be preserved.

## 3. Certification Principles

1. **Evidence before claims.** A feature passes only when its defined observable outcome is captured.
2. **No unexplained exclusions.** Skips, xfails, missing credentials, and unsupported platforms are reported as first-class results.
3. **Production-shaped paths.** Tests use the application lifespan and DB/Redis-backed service swap where production does.
4. **Tenant safety.** Generated tenants, goals, knowledge, agents, and connector objects use a unique run identifier.
5. **Persistent external evidence.** Jira records created by the run are retained and listed in the final report. Jira deletion and cleanup tests are not executed.
6. **Controlled model cost.** Every live prompt is uniquely attributable, bounded, deterministic where possible, and recorded by role/pattern.
7. **Failure is data.** A failing check is diagnosed and rerun after a narrowly scoped fix when the correction is safe and unambiguous.
8. **No unrelated mutation.** Existing user changes and unrelated code remain untouched.

## 4. Run Identity and Evidence

Each certification run uses an identifier with the form `AVCERT-YYYYMMDD-HHMMSS`. It is included in:

- tenant, agent, goal, workflow, collection, and schedule names;
- Jira issue summaries and comments;
- model prompts where safe;
- log and report filenames;
- screenshots, traces, and performance samples;
- database and audit queries used for verification.

Evidence is stored under `artifacts/certification/<run-id>/` and summarized in `docs/testing/<run-id>-full-product-certification-report.md`. Sensitive values, authorization headers, API keys, and connector tokens must never be written to evidence files.

## 5. Execution Architecture

The run is a layered certification pipeline. A later layer may continue after a failure when continuing is safe and helps identify independent defects.

### Layer A: Repository and Contract Baseline

- Capture commit, dirty-worktree paths, tool versions, dependency-lock state, and Docker image state.
- Validate backend formatting/lint, strict typing, imports, OpenAPI generation, migration graph, and configuration security rules.
- Validate frontend lint, type-check, production build, API types, route loading, and package-lock consistency.
- Build and test the Python SDK, TypeScript SDK, and GitHub Action image/entrypoint.
- Compare frontend API usage and both SDKs against the generated backend OpenAPI contract.

### Layer B: Infrastructure and Persistence

- Start the complete Compose topology required by the product.
- Diagnose Keycloak, Jaeger, and OpenTelemetry health before application tests.
- Apply every migration to the real local Postgres instance and verify pgvector/trigram extensions.
- Verify Redis commands, pub/sub, checkpoint persistence, rate-limit primitives, and Celery broker behavior.
- Verify PgBouncer, MinIO artifact operations, Mailpit delivery, SearXNG search, Prometheus scraping, Grafana datasource health, Loki ingestion, and Jaeger trace visibility.
- Exercise process restart and persisted-state recovery without deleting existing volumes.

### Layer C: Backend Automated Certification

- Run the complete backend suite with warnings treated as errors.
- Run integration-marked tests with the configured Colima socket and Ryuk setting.
- Run slow and real-provider suites separately so their cost and failures remain attributable.
- Validate application lifespan behavior, DB/Redis-backed service replacement, route registration, middleware ordering, authentication, and security headers.
- Validate Postgres row-level security across distinct tenants and system sessions.
- Validate Celery queue routing for free, starter, professional, and enterprise plans.

### Layer D: Agent Runtime and Pattern Certification

The live model matrix covers planner, executor, verifier, reflection, synthesis, and routing roles. It includes:

- sequential plan-and-execute;
- ReAct;
- chain-of-thought behavior without exposing hidden reasoning to clients;
- self-refine;
- reflexion after forced verification failure;
- tree of thoughts;
- self-consistency;
- peer review;
- debate and voting;
- supervisor/delegation;
- goal-tree parallel subagents;
- structured DAG/wave execution;
- loop-until/polling;
- dynamic pattern assembly;
- multi-agent consensus and synthesis;
- long-term memory recall;
- Redis checkpoint interruption/resume;
- model routing and provider fallback;
- budget/cost termination;
- maximum-iteration termination;
- HITL wait/approve/reject paths;
- tool failure, replan, retry, deduplication, circuit breaker, bulkhead, and rollback registration.

Every pattern must assert final state, event sequence, model/tool attribution, audit events, token/cost accounting, and failure semantics—not merely non-empty text.

### Layer E: Agentic RAG and Knowledge Certification

The live Postgres/pgvector and OpenAI embedding/model path covers:

- content ingestion, parser selection, chunking, provenance, quality checks, and embeddings;
- vector, lexical/trigram, BM25-style, hybrid, and reciprocal-rank-fusion retrieval;
- query rewriting and multi-query Fusion RAG;
- Corrective RAG;
- Adaptive RAG;
- FLARE;
- Self-RAG and critique metadata;
- Speculative RAG;
- RAPTOR hierarchy construction and summary retrieval;
- ColBERT-style reranking;
- agentic proposition chunking;
- knowledge graph creation/traversal where implemented;
- semantic cache reuse and invalidation;
- tenant isolation and deletion semantics for internal test data;
- evidence propagation into citations and final answers.

RAG tests include answerable, partially answerable, unanswerable, contradictory, stale, adversarial, and cross-tenant queries.

### Layer F: Hallucination, Guardrail, Security, and Governance Certification

The adversarial corpus includes:

- unsupported factual requests;
- conflicting retrieved sources;
- missing evidence and forced-answer prompts;
- forged tool output and instruction injection in tool results;
- prompt injection in ingested documents;
- indirect exfiltration attempts;
- PII, secrets, payment-card patterns, and regulated data;
- unsafe shell/tool requests;
- high-risk deploy/delete/production language;
- malformed structured model output;
- excessive model confidence;
- citation/source mismatch;
- tenant-boundary and privilege-escalation attempts.

Assertions cover grounding decisions, citations, uncertainty/refusal behavior, sanitization, redaction, policy evaluation, guardrail decisions, scope enforcement, HITL, audit hashing, legal holds, compliance exports, and eval safety scores.

### Layer G: Evals, Improvement, and Observability

- Execute goal, agent, model, RAG, runtime, and safety scorecards.
- Build and run a golden dataset containing success, failure, ambiguity, safety, and regression cases.
- Validate regression gates block a deliberately degraded candidate.
- Validate feedback, replay, prompt-optimization, self-improvement, benchmark, and experiment flows.
- Confirm traces join API request, goal, agent step, model call, tool call, and persistence events.
- Confirm metrics for latency, tokens, cost, failures, queue depth, and provider health.
- Confirm logs are queryable by run ID without secrets.

### Layer H: Real External Connector Certification

Jira is the configured live external connector. The run will:

- discover the Jira project and capabilities;
- create persistent run-tagged issues;
- fetch each created issue and verify fields;
- search for the run ID;
- add and verify run-tagged comments;
- invoke Jira through both direct connector/MCP paths and an autonomous-agent goal;
- record issue keys and URLs in the final report;
- never transition into a delete or cleanup step.

GitHub, Slack, Linear, Notion, and other catalog connectors without credentials receive schema, registry, OAuth/permission, error-contract, and simulated MCP tests. They are marked “integrated, not live-certified.”

### Layer I: Frontend and Transport Certification

- Run all Vitest component/unit tests.
- Run the complete Playwright suite in desktop and mobile projects.
- Run accessibility, security, failure-state, eval, RAG, governance, observability, provider, and multimodal Playwright projects.
- Load all 57 routes and detect crashes, missing chunks, console errors, unhandled requests, and inaccessible navigation.
- Exercise sign-up/login/MFA/SSO boundary behavior, onboarding, agent creation, goal creation, live execution, approvals, knowledge ingestion/search, workflows, schedules, connectors, evals, governance, observability, marketplace, civilization, collaboration, RPA, memory, artifacts, tools, training export, and administration.
- Verify SSE goal events, token streaming, reconnection, terminal state, and duplicate suppression.
- Verify WebSocket collaboration, reconnect, optimistic concurrency, and multi-client updates.
- Capture screenshots at critical live milestones and traces/videos for failures.

### Layer J: Resilience, Performance, and Recovery

- Measure cold/warm API health, goal submission, retrieval, and UI load latency.
- Exercise concurrent tenants and per-plan queue routing.
- Interrupt Redis, worker, provider, and API paths individually and verify bounded failure/recovery.
- Verify checkpoint resume, deduplication, retry ceilings, circuit transitions, bulkhead isolation, and idempotency.
- Verify rate limiting, budget enforcement, and backpressure under controlled load.
- Confirm no cross-tenant data appears under concurrency.

## 6. Real-World Domain Scenario Matrix

Each domain receives a run-tagged goal with domain-appropriate context, evaluation criteria, hallucination checks, and governance classification:

1. HR — policy-grounded onboarding plan with PII redaction.
2. Software engineering — Jira defect triage, implementation plan, and peer review.
3. DevOps — incident analysis, risky production remediation, and HITL.
4. Sales/CRM — lead prioritization with unsupported-claim prevention.
5. Operations — multi-step SOP execution with exception recovery.
6. Legal — contract-risk summary with mandatory citations and uncertainty.
7. GST/tax — evidence-grounded classification with refusal on missing facts.
8. Invoicing/finance — invoice anomaly investigation and approval policy.
9. Government portal — form workflow planning with PII controls.
10. E-commerce — order/refund support flow and tool deduplication.
11. Education — curriculum recommendation grounded in supplied material.
12. Healthcare — non-diagnostic information response with safety boundaries.
13. Real estate — property comparison without inventing unavailable attributes.
14. Marketing — campaign plan with claim and brand guardrails.
15. Cybersecurity — alert triage with malicious-input isolation.
16. Logistics — shipment exception plan with loop-until polling.
17. Insurance — claim-document reasoning with missing-evidence handling.
18. Customer support — cited resolution and escalation decision.
19. Manufacturing — maintenance analysis with human approval for shutdown.
20. Banking/fintech — PCI-aware payment investigation with redaction.
21. Agriculture — advisory grounded only in the supplied local dataset.
22. Hospitality/travel — itinerary disruption replanning without fabricated availability.
23. Media/publishing — research synthesis with source attribution.
24. Pharmaceutical — regulated-content summary with strict uncertainty.
25. Telecom — service-outage diagnosis and customer-impact prioritization.
26. Construction — dependency-aware project delay recovery plan.
27. Accounting/CA — reconciliation anomaly analysis with audit trail.
28. Food/restaurant — supply and allergen workflow with safety rules.
29. Recruitment/staffing — candidate workflow with bias/PII guardrails.
30. Energy/utilities — outage response with critical-action HITL.
31. Automobile — warranty/service triage grounded in supplied documentation.
32. Nonprofit/NGO — grant evidence synthesis and budget constraints.
33. Events management — dependency-aware event contingency workflow.
34. Wealth management — educational risk explanation without personal financial advice.
35. Fashion/apparel — inventory and trend synthesis without fabricated demand data.
36. Architecture/interior design — constraints-based proposal with provenance.
37. Public health — evidence-grounded outreach planning with privacy controls.

The scenario result is accepted only if the agent reaches the expected lifecycle state, cites or refuses correctly, produces required audit/eval records, and respects the domain’s governance policy.

## 7. Defect Handling

For each failure:

1. Preserve the failing command, minimal log, service health, and run ID.
2. Reproduce with the narrowest relevant test.
3. Identify whether the cause is product code, test defect, configuration, dependency, credential, provider behavior, or unavailable capability.
4. Apply a minimal fix only when it is in certification scope, technically unambiguous, and does not overwrite user work.
5. Add or strengthen a regression test before or with the implementation change.
6. Rerun the focused check and its containing layer.
7. Record the original failure, fix, and verification result.

Unrelated refactoring, destructive volume resets, external-record deletion, and credential changes are prohibited.

## 8. Acceptance Criteria

Certification is complete when:

- every discovered backend subsystem and frontend route appears in the evidence matrix;
- all five projects have recorded build/test results;
- all configured infrastructure services have recorded health and functional checks;
- all listed agent patterns and RAG strategies have an observable pass/fail/blocked result;
- all 37 domain scenarios have an observable result;
- hallucination, guardrail, security, governance, eval, and resilience matrices are complete;
- real OpenAI, Postgres/pgvector, Redis, and Jira evidence is captured;
- persistent Jira identifiers are reported and no Jira cleanup is performed;
- frontend desktop/mobile/accessibility and live transport results are recorded;
- no skip, xfail, timeout, or unavailable dependency remains unexplained;
- every applied fix has a regression test and rerun evidence;
- residual risks distinguish product defects from unavailable external credentials.

Passing certification does not mean every catalog connector was invoked live. It means every implemented feature was tested at the strongest available evidence level and every live-capable configured boundary was exercised honestly.

## 9. Deliverables

- Approved design specification (this document).
- Command-level execution plan.
- Per-layer machine-readable summaries and retained failure artifacts.
- Full-product certification report.
- Capability/evidence matrix.
- Real-world domain scenario matrix.
- Infrastructure health report.
- Jira record ledger.
- Model-use and cost summary.
- Defect and remediation ledger.
- Final launch-readiness decision with explicit residual risks.
