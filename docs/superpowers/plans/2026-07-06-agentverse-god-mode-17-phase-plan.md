# AgentVerse God Mode 17-Phase Plan

> This plan intentionally excludes the dedicated sandbox/isolation execution-environment workstream. Sandboxing remains a required launch-readiness pillar, but it is tracked separately.

**Goal:** Make AgentVerse a 10/10 world-class, vendor-agnostic, multi-tenant autonomous agent platform across backend, frontend, AI routing, multimodal intelligence, RAG, GraphRAG, governance, memory, observability, testing, and live QA.

**Architecture:** Build generic platform primitives first: model registry, routing policies, multimodal ingestion, knowledge graph, GraphRAG, agent runtime, governance, observability, memory, and skills. Every capability must be tenant-scoped, auditable, observable, testable, and extensible.

**Tech Stack:** FastAPI, LangGraph, Celery, PostgreSQL/pgvector, Redis, MCP, React, Vite, TanStack Query, Zustand, Tailwind, Playwright, pytest, OpenTelemetry, Prometheus.

---

## Non-Negotiable Principles

- Every feature must be tenant-scoped.
- Every model, tool, memory, RAG retrieval, graph hop, and action must be observable.
- Every important decision must be explainable.
- Every risky action must pass policy, guardrails, audit, cost control, and HITL where required.
- Every capability must be provider-agnostic.
- Every backend feature must have backend tests.
- Every user-facing flow must have Playwright coverage.
- Every critical flow must be testable against the real live platform.
- Frontend must feel like a premium AI operations command center, not a generic CRUD dashboard.
- Secrets must never appear in logs, traces, screenshots, videos, reports, commits, or test artifacts.

## Target Platform Architecture

| Layer | Target Capability |
| --- | --- |
| AI Router | Generic model routing for text, vision, OCR, speech, video, embeddings, rerankers, and judges |
| Model Registry | Tenant/provider/model capability catalog with health, cost, latency, and quality scores |
| Agent Runtime | Planner, executor, verifier, critic, judge, reflector, subagent, and synthesizer roles |
| RAG Platform | Hybrid RAG, GraphRAG, multimodal RAG, grounding, citations, evals, and drift detection |
| Knowledge Graph | Tenant-scoped graph from docs, tools, goals, memory, artifacts, agents, and workflows |
| Memory | Execution memory, long-term semantic memory, graph memory, provenance, lifecycle, and governance |
| Skills Runtime | Platform and tenant skills with triggers, permissions, versions, audit, and execution traces |
| Governance | HITL, policies, guardrails, audit, budgets, RBAC, compliance bundles, and evidence export |
| Observability | Traces, logs, evals, LLM-as-judge, regression testing, quality alerts, and drift detection |
| Frontend | Mission-control UX with live execution graph, model health, trust center, and AI Ops center |
| Tests | Backend, security, frontend, Playwright live, provider-gated, regression, and eval tests |

## Phase 1: Fix Existing Product And Contract Gaps

### Scope

Fix known current gaps before adding larger systems.

### Work

| Area | Backend Work | Frontend Work | Tests |
| --- | --- | --- | --- |
| LLM config | Standardize `model` versus `default_model`, preserve compatibility, and ensure `GoalService` uses saved tenant model | Update Settings page to save/read the canonical field | Backend API test, frontend unit test, Playwright settings flow |
| Multimodal goals | Main `/goals` API must apply image/PDF attachment fields when submitting | Add attachment controls to goal composer | Backend goal submit test, Playwright multimodal submit |
| Guardrails | Normalize singular `layer` and list `layers`, persist multi-layer semantics | Keep multi-layer UI and align payload | Backend CRUD tests, Playwright guardrail creation |
| Dry-run vs simulation | Keep dry-run as preview and simulation as mocked execution | Fix copy so users know what executed | API tests and Playwright copy assertions |
| Provider catalog | Return provider capabilities and safe config state | Show live provider cards | Backend provider tests, frontend provider UI tests |

### Acceptance Criteria

- Settings saves a provider and model that are used by goal execution.
- Goal submission with image or attachment enriches execution context.
- Guardrail creation works for multiple layers.
- Dry-run and simulation are not misrepresented.
- Provider catalog exposes no secrets.

## Phase 2: AI Router And Model Registry

### Scope

Build a generic model routing layer for all model types.

### Backend Work

- Add model registry primitives: `ModelProvider`, `ModelEndpoint`, `ModelCapability`, `ModelRoutePolicy`.
- Support capabilities: `text_generation`, `structured_output`, `tool_use`, `vision`, `embedding`, `rerank`, `ocr`, `speech_to_text`, `text_to_speech`, `video_understanding`, `llm_judge`.
- Add first-class providers: OpenAI, Anthropic, Gemini, OpenRouter, Azure OpenAI, Groq, Ollama, Hugging Face, custom OpenAI-compatible, and custom self-hosted endpoint.
- Add routing modes: cheapest, fastest, highest quality, compliance required, fallback chain, tenant default, model-pinned.
- Add health scoring: latency, error rate, token cost, context window, streaming support, tool-use support, structured output support.
- Add tenant-scoped encrypted provider secrets.
- Add provider failover and circuit breaker integration.
- Add model selection trace to goal execution context.

### Frontend Work

- Replace simple provider form with Model Control Center.
- Show provider health, model capabilities, configured status, fallback chain, last error, and cost tier.
- Add model test button for each capability.
- Add route policy editor for planning, execution, verifier, judge, embedding, OCR, speech, and video models.
- Add model selection explanation in goal detail.

### Tests

- Model selection by task type.
- Provider fallback on failure.
- Tenant isolation for provider config.
- Secret redaction.
- Model health update.
- Playwright provider config, health test, and goal explanation model trace.

## Phase 3: Embedding Platform

### Scope

Build first-class embedding routing and operations.

### Backend Work

- Add `EmbeddingRouter`.
- Support OpenAI, Voyage, Gemini, sentence-transformers, Hugging Face, and custom embedding endpoints.
- Store collection embedding provider, model, dimension, and metadata.
- Validate dimension compatibility before ingesting or searching.
- Add collection re-embedding jobs.
- Add embedding health and drift metrics.
- Add lexical fallback when embeddings are unavailable.

### Frontend Work

- Add Embedding Providers section.
- Show embedding coverage per collection.
- Show dimension, provider, cost, latency, and failed chunk count.
- Add re-embed action.
- Add collection embedding health panel.

### Tests

- Provider selection per collection.
- Dimension mismatch rejection.
- Empty embedding fallback to lexical search.
- Re-embedding updates metadata.
- Tenant isolation for embedding configs.
- Playwright collection creation, document ingest, and embedding coverage flow.

## Phase 4: Multimodal Intelligence

### Scope

Build universal multimodal ingestion and reasoning.

### Backend Work

- Add `app/multimodal/`.
- Add modality pipeline for text, image, PDF, OCR, audio, and video.
- Add `AssetIngestionJob`.
- Add `ExtractedSpan` with source page, frame, timestamp, bounding box, and confidence.
- Add OCR provider abstraction.
- Add speech-to-text provider abstraction.
- Add text-to-speech provider abstraction.
- Add video processing: audio transcript, keyframes, scene summaries, visual entities.
- Add document parser for PDF layout, tables, forms, and images.
- Add multimodal embeddings when provider supports them.
- Add artifact-to-knowledge ingestion.

### Frontend Work

- Add universal ingest page with drag/drop.
- Add extraction preview for documents, images, audio, and video.
- Add page/frame/timestamp citation display.
- Add Ask This Document and Ask This Video flows.
- Add progress, retry, failure recovery, and confidence indicators.

### Tests

- PDF extraction.
- OCR fake provider.
- Image extraction.
- Audio transcription fake provider.
- Video fake provider.
- Citation span preservation.
- Tenant boundary enforcement.
- Playwright upload, extraction preview, and ask flow.

## Phase 5: Tenant Knowledge Graph

### Scope

Build a tenant-scoped knowledge graph foundation.

### Backend Work

- Add graph tables: `knowledge_nodes`, `knowledge_edges`, `knowledge_communities`, `knowledge_graph_jobs`.
- Support node types: document, chunk, entity, concept, goal, tool, memory, artifact, agent, workflow.
- Support edge types: mentions, supports, contradicts, caused_by, depends_on, used_tool, produced_artifact, similar_to, parent_of.
- Add deterministic entity extraction.
- Add LLM relationship extraction with confidence and provenance.
- Add graph community detection.
- Add graph query API.
- Add graph path API.
- Add graph export API.
- Add graph rebuild jobs per tenant.

### Frontend Work

- Upgrade `KnowledgeGraph.tsx` into full Graph Explorer.
- Add filters by node type, confidence, source, and date.
- Add entity detail drawer.
- Add provenance chain view.
- Add graph path to answer view inside RAG answers.
- Add graph health score.

### Tests

- Entity extraction stores nodes.
- Relationship extraction stores edges.
- Same-tenant graph query works.
- Cross-tenant graph query is blocked.
- Graph rebuild is idempotent.
- Low-confidence edges are flagged.
- Playwright graph explorer, entity search, relationship inspection, and provenance view.

## Phase 6: GraphRAG And Multimodal RAG

### Scope

Upgrade RAG into a full retrieval platform.

### Backend Work

- Keep current hybrid vector plus FTS plus trigram RRF retrieval.
- Add graph expansion after vector/lexical retrieval.
- Add multimodal retrieval across text, OCR spans, image descriptions, transcripts, and video frames.
- Add query planner strategies: direct, multi-hop, HyDE, graph, multimodal, auto.
- Add reranker abstraction.
- Add citation verifier.
- Add answer synthesizer that refuses unsupported claims.
- Add RAG eval dataset support.

### Frontend Work

- Add RAG mode selector: hybrid, graph, multimodal, auto.
- Show retrieval legs: vector, lexical, trigram, graph, multimodal.
- Show confidence and citations.
- Add why retrieved panel.
- Add answer quality feedback.

### Tests

- GraphRAG expands from seed chunks to related nodes.
- Multimodal RAG searches OCR, transcript, and image descriptions.
- Citation verifier rejects unsupported answer.
- Reranker changes order deterministically in tests.
- Retrieval falls back safely when graph is empty.
- Playwright RAG, GraphRAG, multimodal RAG, and citation inspector tests.

## Phase 7: Agent Runtime 2.0

### Scope

Formalize agent orchestration and runtime modes.

### Backend Work

- Formalize roles: planner, executor, verifier, critic, judge, reflector, synthesizer.
- Add `AgentExecutionPlan` with typed steps, dependencies, risk, expected evidence, and output contract.
- Add `AgentRunTrace`.
- Add strategy engine: single-agent, multi-agent fanout, debate, supervisor, goal-tree, persistence.
- Add explicit subagent lifecycle: spawn, assign, monitor, cancel, retry, synthesize.
- Add agent capability profiles.
- Add agent performance history into routing.
- Add parent-child lineage and cost attribution.

### Frontend Work

- Add runtime mode selector.
- Add live execution timeline.
- Add execution graph with planner, executor, verifier, judge, and tool nodes.
- Add subagent tree.
- Add per-step evidence, model, tool, cost, and duration.
- Add retry, replan, cancel, and human-guidance controls.

### Tests

- Plan schema validation.
- Multi-agent fanout creates child goals.
- Supervisor decomposes and synthesizes.
- Debate produces winning proposal.
- Failed step triggers replan.
- Subagent lineage recorded.
- Cost attributed to parent and child.
- Playwright single-agent, debate, supervisor, and subagent-tree flows.

## Phase 8: Guardrails 2.0

### Scope

Make guardrails universal across agent execution.

### Backend Work

- Normalize guardrail API and persistence.
- Add layers: goal, plan, step, tool_args, tool_output, final_output, memory_write, rag_ingest, graph_extract.
- Add actions: log, warn, redact, block, require_hitl, quarantine.
- Add categories: PII, PHI, PCI, secrets, prompt injection, tool injection, data exfiltration, toxicity, copyright, jailbreak, unsafe code, unsafe finance/legal/medical advice.
- Add guardrail simulation API.
- Add violation persistence.
- Add guardrail bundle versioning.
- Add tenant compliance profiles.

### Frontend Work

- Redesign Guardrail Center as governance cockpit.
- Add visual policy pipeline.
- Add rule tester with before/after redaction.
- Add violations timeline.
- Add compliance bundles: GDPR, SOC2, HIPAA, PCI, DPDP, SOX.
- Add what would happen simulation.

### Tests

- PII redaction.
- Prompt injection block.
- Tool argument injection block.
- Output redaction.
- HITL action returned.
- Violation persisted.
- Tenant isolation.
- Playwright rule creation, live tester, bundle enablement, and violation filtering.

## Phase 9: Trust And Governance 2.0

### Scope

Make governance enterprise-grade.

### Backend Work

- Make Audit v3 canonical.
- Ensure every sensitive action writes an audit record.
- Add hash-chain verification API.
- Add governance policy versioning.
- Add policy simulation before execution.
- Add RBAC enforcement tests for all admin routes.
- Add cost and budget policy audit.
- Add approval delegation and multi-approver flows.
- Add compliance evidence export.

### Frontend Work

- Rename Security Center to Trust Center where appropriate.
- Add audit integrity status.
- Add approval queue with context and evidence.
- Add policy simulator.
- Add budget governance panel.
- Add compliance evidence export.

### Tests

- Audit hash chain valid.
- Tamper detection.
- Multi-approver approval.
- Policy version snapshot.
- RBAC denies unauthorized user.
- Cost budget enforcement.
- Playwright approval, audit export, policy simulation, tamper warning, and budget UI tests.

## Phase 10: Observability, Evals, Regression, Drift

### Scope

Build an AI operations center.

### Backend Work

- Add unified trace model for goal runs.
- Add redacted model call logs.
- Add eval datasets and golden tasks.
- Add LLM-as-judge registry.
- Add judge calibration.
- Add regression detection.
- Add drift detection for model output, retrieval, embedding, prompt, tool reliability, and guardrail violations.
- Add alert events.
- Add nightly eval runner.

### Frontend Work

- Transform Observability into AI Ops Center.
- Add trace waterfall.
- Add model usage and cost charts.
- Add eval trend radar.
- Add drift alerts.
- Add regression dashboard.
- Add golden dataset manager.
- Add compare two goal runs.

### Tests

- Trace created for every model, tool, and RAG step.
- Eval suite persists result.
- Regression detected when score drops.
- Drift score computed.
- Alerts emitted.
- Playwright trace explorer, eval suite, regression alert, and drift UI flows.

## Phase 11: Agent Memory 2.0

### Scope

Upgrade memory into governed intelligence.

### Backend Work

- Add memory provenance.
- Add lifecycle states: active, stale, disputed, deleted.
- Add memory consolidation job.
- Add memory conflict detection.
- Add memory confidence updates.
- Add memory privacy class.
- Add graph memory links.
- Add memory recall trace.
- Add memory export and delete for compliance.

### Frontend Work

- Add memory graph.
- Add provenance drawer.
- Add conflict resolution UI.
- Add confidence tuning.
- Add stale memory review.
- Add memory used-in-run links.

### Tests

- Memory write with provenance.
- Semantic recall.
- Conflict detection.
- Stale memory flag.
- Memory deletion respects tenant.
- Memory appears in graph.
- Playwright memory create, recall, edit, conflict resolution, delete, and goal evidence tests.

## Phase 12: Skills Runtime

### Scope

Make skills first-class platform capabilities.

### Backend Work

- Add platform skill registry.
- Add tenant skill registry.
- Add skill versioning.
- Add skill permission scopes.
- Add skill trigger matching.
- Add skill execution traces.
- Add skill output contracts.
- Add built-in skills: Graphify, Headroom, frontend design, security review, QA, RAG eval, prompt optimizer, code review, and test writer.
- Add skill marketplace integration.

### Frontend Work

- Add Skills Center.
- Add skill detail pages.
- Add install, enable, and disable controls.
- Add skill trigger simulator.
- Add skills used in this goal timeline.
- Add skill quality metrics.

### Tests

- Skill selection by trigger.
- Skill permission enforcement.
- Skill version pinning.
- Skill audit record.
- Skill output injected into plan safely.
- Playwright enable skill, trigger skill, submit goal, and inspect skill trace.

## Phase 13: Premium Frontend Redesign

### Design Direction

- Theme: Agent Mission Control.
- Tone: precise, powerful, operational, trustworthy.
- Signature element: live Goal DNA graph as the spine of goal detail, observability, memory, and governance.
- Palette: `#080A12` command black, `#111827` panel graphite, `#7C3AED` neural violet, `#06B6D4` telemetry cyan, `#22C55E` verified green, `#F59E0B` risk amber.
- Layout: cockpit shell with left nav, top operational status bar, center live canvas, right inspector drawer.
- Motion: one coordinated live execution pulse, reduced-motion safe.
- UX principle: every screen answers what is happening, why, and what can be done next.

### Pages To Upgrade

- Dashboard: live command center, active goals, model/provider health, cost, alerts.
- Goals: better composer, attachments, workflow mode, model policy preview.
- Goal Detail: live timeline, Goal DNA, evidence, model/tool/cost trace.
- Agents: capability cards, performance, autonomy, skills, connectors.
- Knowledge: collections, RAG chat, graph explorer, ingestion jobs.
- Memory: memory graph, provenance, confidence, conflict review.
- Guardrails: policy pipeline, tester, violations, compliance bundles.
- Trust Center: audit chain, HITL, RBAC, scopes, compliance evidence.
- Observability: trace waterfall, evals, drift, regression, model health.
- Settings: provider/model/embedding/router config with health tests.
- Simulation: accurate copy, mock tool studio, policy impact preview.
- Marketplace: templates, skills, agents, connectors with install flow.
- Workflow Builder: reliable node editing, validation, run history, and failure paths.

### Tests

- Loading states.
- Empty states.
- Error states.
- Permission states.
- Mobile viewport.
- Keyboard navigation.
- Screen reader labels.
- Reduced motion.
- Route error boundaries.

## Phase 14: Backend Test Suite

### Scope

Implement deep backend coverage.

### Required Domains

- Auth: valid, invalid, revoked, expired API keys.
- Tenant isolation: all major domains enforce tenant boundary.
- RLS: DB queries cannot cross tenant boundary.
- AI router: model selection, fallback, provider health, tenant config.
- Embeddings: provider selection, dimension validation, fallback.
- Goals: submit, execute, cancel, pause, resume, feedback.
- Agent loop: plan, execute, verify, replan, max iteration failure.
- Multi-agent: fanout, debate, supervisor, child goal lineage.
- MCP: tool discovery, tool call, SSRF block, exfil block, circuit breaker.
- RAG: ingest, search, HyDE, multi-hop, rerank, citation validation.
- Knowledge graph: node extraction, edge extraction, graph query, tenant isolation.
- Multimodal: PDF, OCR, image, audio/video fake providers.
- Memory: create, recall, conflict, lifecycle, deletion.
- Guardrails: PII, prompt injection, tool args, output redaction, violation persistence.
- Governance: HITL approve/reject, policy simulation, audit write.
- Cost: budget enforcement, idempotent cost recording, budget exceeded.
- Observability: trace emission, log stream, metrics, eval result.
- Security: CORS, SSRF, secret redaction, RBAC, permission denied.
- Background jobs: Celery behavior, retries, idempotency, failure recovery.
- WebSocket/SSE: connect, event delivery, reconnect/resume, tenant isolation.

### Verification Commands

```bash
uv run ruff check .
uv run mypy app
uv run pytest
uv run pytest -m integration
uv run pytest -m "not slow"
```

## Phase 15: Playwright E2E Suite

### Scope

Build whole-platform E2E coverage.

### Required Projects

- `smoke-live`
- `full-live`
- `mobile`
- `accessibility`
- `security-smoke`
- `failure-states`
- `provider-live`
- `eval-regression`
- `multimodal-live`
- `rag-live`
- `governance-live`
- `observability-live`

### Required Coverage

- Auth: signup, API key login, invalid key, logout, expired key, missing key.
- Tenant isolation: Tenant A cannot see Tenant B goals, agents, memory, knowledge, audit, costs.
- Dashboard: live metrics, empty state, active goals, alerts, provider health.
- Goals: create, dry run, live run, cancel, retry, pause, resume, explain, feedback.
- Goal streaming: SSE connects, receives events, reconnects, resumes, handles failure.
- Goal detail: timeline, tool calls, model selections, citations, evidence, errors.
- Agent CRUD: create, edit, delete, duplicate, configure autonomy, model override.
- Agent routing: auto-route, low confidence, needs human choice, selected agent.
- Multi-agent: fanout, debate, supervisor, subagent tree, synthesis.
- MCP/tools: connector list, tool discovery, safe read tool, blocked write/destructive tool.
- RPA/perception: screenshot, extract text, analyze URL, batch analyze, failed URL.
- Knowledge: create collection, ingest file, ingest PDF, search, ask RAG, delete.
- GraphRAG: graph explorer, entity search, relationship view, graph answer citations.
- Multimodal: image goal, PDF goal, OCR result, transcript/video flow if implemented.
- Memory: create memory, recall memory, edit confidence, delete memory, used-in-goal evidence.
- Guardrails: create rule, test PII, test prompt injection, enable bundle, view violation.
- HITL: pending approval, approve, reject, timeout state, audit record.
- Governance: policy CRUD, policy simulation, deny tool, require approval.
- Audit: list audit events, verify integrity, export evidence.
- Costs: budget dashboard, model cost, budget exceeded path.
- Observability: health, metrics, traces, logs stream, eval scorecard, regression alert.
- Evals: create suite, add golden task, run suite, view failures.
- Skills: list skills, enable skill, trigger skill, inspect skill trace.
- Settings: provider config, model route policy, embedding config, API key rotation, MFA.
- Marketplace: browse templates, install template, instantiate goal/agent.
- Workflow builder: create workflow, add nodes, validate, run, failure path.
- Billing: plan view, invoices, payment mock/live-safe path.
- Security Center: agent identity, scopes, RBAC, IP allowlist, MFA.
- Mobile: dashboard, goals, approvals, knowledge, observability on mobile viewport.
- Accessibility: keyboard navigation, focus states, ARIA labels, axe checks.
- Failure states: API 500, network offline, provider down, Redis/stream unavailable, empty DB.
- Loading states: skeletons, streaming states, pending mutations.
- Empty states: no goals, no agents, no memory, no knowledge, no audit, no approvals.
- Permission states: unauthorized, forbidden, insufficient scope, admin-only denied.

## Phase 16: CI And Release Gates

### CI Gates

- Backend lint.
- Backend typecheck.
- Backend tests.
- Frontend lint.
- Frontend typecheck.
- Frontend unit tests.
- Frontend build.
- Playwright smoke.
- Security smoke.
- Contract tests.

### Nightly Gates

- Full Playwright.
- Integration tests.
- Eval regression.
- Drift checks.
- Provider-live if credentials are configured.
- Accessibility suite.
- Performance smoke.

### Launch Gate

- No critical issues.
- No high security issues.
- No broken core journey.
- Smoke live passes.
- Backend tests pass.
- Frontend tests pass.
- Provider tests are gated and documented.
- Audit, governance, and guardrail coverage is present.
- Known gaps are documented.

## Phase 17: Live Platform Testing And Complete Test Suite

### Scope

Live testing is mandatory. The test suite must run against the real AgentVerse platform locally or against the provided live/staging environment.

### Live Services

- Backend API.
- Frontend app.
- Postgres.
- Redis.
- Celery workers.
- SSE goal streams.
- WebSocket flows.
- RPA/browser flows where safe.
- MCP/tool flows where safe.
- Provider-live model tests gated by environment variables.

### Live Testing Rules

- Create a dedicated test tenant.
- Create dedicated API keys and users.
- Seed deterministic test data.
- Clean up all test data.
- Never use production user data.
- Never expose secrets in logs, screenshots, traces, videos, reports, or commits.
- Do not run expensive provider tests unless `LIVE_PROVIDER_TESTS=true`.
- Do not run destructive external tool actions unless they are mocked or explicitly safe.
- Prefer real platform services over mocks.
- Use mocks only for forced failures, unsafe destructive actions, expensive provider calls, and external services that cannot be safely controlled.

### Required Commands

```bash
# Backend
uv run ruff check .
uv run mypy app
uv run pytest
uv run pytest -m integration

# Frontend
npm run lint
npm run typecheck
npm run test
npm run build

# Playwright
npm run test:e2e -- --project=smoke-live
npm run test:e2e -- --project=full-live
npm run test:e2e -- --project=mobile
npm run test:e2e -- --project=accessibility
npm run test:e2e -- --project=security-smoke
npm run test:e2e -- --project=failure-states
LIVE_PROVIDER_TESTS=true npm run test:e2e -- --project=provider-live
```

### Deliverables

- Complete backend test plan.
- Complete Playwright live test plan.
- Backend tests implemented.
- Playwright fixtures implemented.
- Seed and cleanup utilities implemented.
- Live test tenant setup documented.
- CI smoke suite configured.
- Nightly full-live suite configured.
- Provider-live suite gated.
- Final coverage matrix by feature.
- Final report with commands run and results.

## Final Definition Of Done

- All critical gaps are fixed or explicitly documented as blocked.
- All high-priority gaps are fixed or planned with rationale.
- Missing launch-critical features are implemented or documented as requiring approval.
- Backend test suite covers core platform behavior.
- Security tests cover tenant isolation, auth, RBAC, secrets, guardrails, and SSRF surfaces.
- Playwright covers every critical user journey.
- Live smoke tests pass on the real platform.
- Provider-live tests exist and are gated.
- Frontend feels premium, coherent, and operationally trustworthy.
- Backend is generic, extensible, tenant-safe, observable, and testable.
- Final report proves readiness with evidence, not claims.
