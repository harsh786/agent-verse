# AgentVerse Registry-Complete Live Certification Design

**Status:** Approved  
**Date:** 2026-07-29  
**Branch:** `codex/agentverse-complete`

## Purpose

Bring AgentVerse to a registry-complete, production-certifiable state:

- every canonical agent strategy is genuinely implemented and user-selectable;
- every canonical RAG strategy is genuinely implemented against persisted, tenant-scoped data;
- the certification report's security, runtime, API, SDK, frontend, accessibility, and
  operational defects are fixed;
- final acceptance is performed through real user interactions with the running product,
  not by presenting newly written test cases as proof.

Internal regression tests remain mandatory for safe implementation. They are development
guards, not the final live-certification evidence.

## Completion definition

The canonical source of truth is `app/orchestration/strategy_registry.py`.

Completion requires:

1. All 38 agent strategy entries report `IMPLEMENTED`, resolve to a real adapter, and emit
   strategy-specific execution evidence.
2. All 18 RAG strategy entries report `IMPLEMENTED`, dispatch through one canonical runtime,
   and produce strategy-specific evidence against persisted data.
3. A strategy must not count as implemented if it silently falls back to another strategy.
4. Live agent runs must end in the canonical `complete` state. A `failed` terminal state is
   not a passing outcome.
5. Backend, frontend, SDK, action, Compose, lint, type, component, integration, and browser
   gates must have no unexplained failures.
6. Two live tenants must remain isolated through both the API and a restricted PostgreSQL
   runtime role.
7. The finished implementation must be merged into `main` while preserving unrelated
   pre-existing workspace changes.

## Delivery approach

Work proceeds on a global isolated worktree and `codex/agentverse-complete` branch. The dirty
`main` worktree remains untouched during implementation. Before the final merge, its
uncommitted changes will be stored recoverably, the completed branch will be merged, and the
unrelated changes will be restored and reconciled.

The work is decomposed into six independently verifiable programs. Each program uses
test-first implementation, focused commits, specification review, code-quality review, and
fresh verification before the next program starts.

## Program 1: Canonical persisted RAG runtime

### One execution core

`app/rag/engine.py` becomes the sole RAG execution core behind a tenant-aware
`RetrievalGateway`.

The gateway owns:

- canonical strategy parsing and compatibility aliases;
- `TenantContext` and collection authorization;
- RLS context creation;
- database session lifecycle;
- independent sessions for concurrent retrieval operations;
- provider, embedder, knowledge-graph, web, and memory dependencies;
- citations, provenance, retrieval-leg metadata, and strategy trace output.

`KnowledgeStore` remains responsible for collections and ingestion. Tenantless, in-memory
search is removed from production API paths. `app/rag_platform/retriever.py` becomes a thin
answer-synthesis and citation-verification layer over the gateway. AgentGraph, `/rag/query`,
knowledge chat, and federated search all call the same gateway.

### Canonical strategy IDs

One enum defines all public IDs. Compatibility aliases are accepted only at API boundaries:

- `fusion_rag` → `fusion`
- `corrective_rag` → `corrective`
- `speculative_rag` → `speculative`
- `colbert_late_interaction` → `colbert`
- `multi_hop_rag` → `multi_hop`
- `graph_rag` → `graph`

The trace records both the requested public ID and resolved implementation ID. Unknown or
unavailable strategies fail explicitly instead of falling back silently.

### Query-time strategies

- **Naive RAG:** persisted vector-only retrieval.
- **Hybrid RAG:** pgvector, PostgreSQL FTS, trigram, and BM25/RRF fusion.
- **HyDE:** generate a hypothetical document, embed that document, then retrieve.
- **Multi-hop RAG:** decompose, embed every hop, retrieve independently, aggregate evidence,
  and optionally expand through the knowledge graph.
- **Graph RAG:** seed vector retrieval, entity/path/community graph traversal, and
  provenance-preserving evidence merge.
- **Corrective RAG:** retrieve, grade, filter, reformulate, retry, and use policy-controlled
  web fallback when persisted evidence remains insufficient.
- **Adaptive RAG:** choose a capability-available strategy and expose the selection reason.
- **Modular RAG:** execute a validated pipeline of retriever, expander, reranker, grader,
  fallback, and synthesizer modules.
- **Speculative RAG:** run draft generation and retrieval concurrently, then verify and
  reconcile claims.
- **Agentic RAG:** let the agent select retrieval, reformulation, fallback, and stopping
  actions with bounded iterations and traceable decisions.
- **Web-augmented RAG:** combine policy-authorized web and persisted evidence with source
  provenance and freshness.
- **Fusion RAG:** expand multiple queries and execute each through a separate DB session
  before RRF merging.
- **Self-RAG:** retrieve, critique relevance/support/usefulness, retry when required, and
  persist critique metadata.
- **FLARE:** detect uncertain spans, generate and embed follow-up queries, retrieve from the
  configured sources, and continue generation.
- **ColBERT:** perform late-interaction reranking with blocking inference off the event loop.

### Ingestion-time strategies

- **RAPTOR:** construct and persist hierarchical summary nodes during indexing, then retrieve
  across leaf and summary levels.
- **Agentic chunking:** extract propositions during ingestion, embed them, and persist their
  parent relationships so propositions are searchable.
- **RAFT:** build a provider-neutral lifecycle for dataset generation, distractor creation,
  training-example validation, fine-tuning job submission/status, and evaluation. A real
  paid fine-tuning job requires action-time user confirmation because it creates external
  state and material cost.

Parent-child and sentence-window structures are created during ingestion and work for every
supported embedding dimension.

## Program 2: Runtime security and durable queued execution

### Restricted PostgreSQL role

Two database identities are mandatory:

- owner/migrator: schema ownership and Alembic only;
- `agentverse_runtime`: `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  NOREPLICATION`, with only required schema, table, and sequence privileges.

`MIGRATION_DATABASE_URL` is used solely by bootstrap/migration services. `DATABASE_URL` is
runtime-only for backend, worker, and beat. Bootstrap is idempotent for fresh and existing
volumes, without embedding plaintext secrets in migrations.

Application startup validates `current_user`, `rolsuper`, and `rolbypassrls`. Production
fails closed when the runtime identity is privileged.

Cross-tenant maintenance no longer depends on `row_security=off`. Tenant-known operations use
the normal RLS context. Cross-tenant jobs enumerate tenants and process one tenant context at
a time, or use narrowly scoped `SECURITY DEFINER` functions with fixed `search_path`.

### Async Redis checkpointing

Redis is upgraded to a checkpoint-compatible Redis Stack/Redis 8 image.

Each Celery prefork child owns:

- one persistent asyncio event loop;
- one entered `AsyncRedisSaver` context;
- completed async saver setup/index creation;
- deterministic shutdown of saver, Redis connections, and loop.

Synchronous `RedisSaver` is never passed to async AgentGraph. Production worker startup fails
if configured durable checkpointing cannot initialize. MemorySaver remains an explicit
development-only fallback.

## Program 3: Registry-complete agent patterns

Every agent strategy receives:

- a typed adapter with stable ID and capability metadata;
- explicit input/output contracts;
- bounded execution and cancellation;
- governance, budget, audit, memory, and checkpoint propagation;
- strategy-specific SSE and persisted trace evidence;
- API and UI selection.

Existing strategies are tightened so `IMPLEMENTED` means production-wired rather than
adapter-present.

The incomplete strategies are implemented as follows:

- **Chain/zero/few-shot CoT:** explicit prompting modes with trace-safe rationale summaries.
- **Graph of Thoughts:** thought graph generation, dependency edges, scoring, pruning, and
  synthesis.
- **Least-to-Most:** ordered subproblem decomposition and answer accumulation.
- **ReWOO:** plan variable-bound tool steps before execution, then resolve observations.
- **Program of Thought:** generate a constrained program, execute it in the governed code
  interpreter, and synthesize the result.
- **CodeAct:** interleave governed code actions and observations.
- **Mixture of Agents:** parallel proposer layer(s) followed by an aggregator.
- **CAMEL:** bounded role-playing agents with a task-completion protocol.
- **BabyAGI:** task creation, prioritization, bounded execution, and memory feedback.
- **AutoGPT:** long-horizon plan/action/critic loop with budgets and stopping criteria.
- **LATS:** bounded Monte Carlo tree search over actions with value/reflection scoring.
- **LLM Compiler:** build a dependency DAG, run ready tasks concurrently, and join results.
- **Meta-agent planner:** generate, validate, and optionally persist a complete agent config.
- **Voyager:** skill proposal, governed practice, evaluation, and reusable skill promotion.
- **Generative Agents:** observation, memory importance, reflection, planning, and bounded
  simulation.

Supervisor, true parallel multi-agent, debate, goal-tree, consensus, peer review,
self-consistency, reflection, reflexion, self-refine, Tree-of-Thoughts, workflow DAG,
loop-engineering, persistence, scratchpad, intent routing, and skill selection receive live
completion evidence through the same product path.

## Program 4: Product contract and user-experience closure

### Backend and knowledge

- Knowledge source forms call the correct source-specific ingestion routes and payloads.
- Knowledge chat uses the active provider, constructs valid completion requests, and fails
  visibly when synthesis fails.
- `/rag/query` never returns a successful-looking ungrounded answer when a required retrieval
  leg crashed.
- Goal detail responses expose canonical status, iterations, errors, artifacts, strategy
  traces, and pattern traces consistently.
- Provider testing resolves the selected provider/model rather than using whichever provider
  started first.

### Frontend

- Mission composer exposes all supported execution modes and advanced pattern/RAG selection.
- Parallel mode submits multiple explicit agent IDs; supervisor mode has its own contract.
- Knowledge ingestion, search, Ask AI, citations, and strategy traces use real backend
  routes.
- Landing and protected pages satisfy landmarks, accessible names, keyboard operation, and
  WCAG AA contrast.
- Mocked Playwright helpers are isolated from live browser projects.

### SDKs, CLI, and GitHub Action

Python and TypeScript use the canonical:

- `complete` status;
- `/analytics/costs`;
- `/goals/{id}/eval`;
- `/governance/approvals/{id}/approve|reject`;
- goal response and artifact shapes;
- knowledge `top_k` parameter.

The CLI and GitHub Action return concise bounded failures without raw tracebacks or secret
leakage.

### Operations

- Kong's intended admin/proxy listeners are explicit.
- MCP health rejects or clearly classifies malformed connector URLs.
- Goals, schedules, and maintenance use separately scalable workers/queues.
- Kubernetes/Helm receive the same migration/runtime credential split as Compose.

## Program 5: Quality-gate closure

Failures are fixed by subsystem and root cause. Existing user changes are preserved. Bulk
autofix is not used across the repository.

Required zero-unexplained-failure gates:

- Ruff;
- strict mypy;
- backend non-slow suite;
- container-backed integration suite under the restricted runtime role;
- frontend lint, typecheck, build, and component suite;
- Python SDK;
- TypeScript SDK;
- GitHub Action image and bounded error path;
- real browser accessibility and functional journeys.

Warnings-as-errors remain enabled. Any intentional skip must have a documented environmental
reason and must not conceal a supported product path.

## Program 6: Real user-style live acceptance

Acceptance uses a dedicated tenant and no request interception.

### Required journeys

1. Sign in through `/auth` with a real tenant/API key and verify `/tenants/me`.
2. Select and test the configured OpenAI provider/model.
3. Create agents through the UI and exercise every agent strategy through real goal
   submission, SSE, Celery, Redis checkpoints, Postgres persistence, and final artifacts.
4. Create a knowledge collection, ingest a uniquely identifiable corpus through the UI,
   restart services, then exercise every RAG strategy. Each run must cite the persisted
   corpus and show its strategy-specific trace.
5. Exercise single-agent, parallel multi-agent, supervisor, debate, and goal-tree modes with
   distinct persisted evidence.
6. Configure Jira read-only tools, run project/issue searches, and verify the audit trail
   contains no write tool.
7. Perform submit, stream/wait, get, list, evaluate, and artifact retrieval through the
   Python SDK, TypeScript SDK, CLI, and GitHub Action.
8. Use two real tenants to prove API and restricted-role DB isolation for goals, knowledge,
   agents, events, and artifacts.
9. Restart backend/worker during an in-progress safe goal and prove checkpoint recovery.
10. Inspect Prometheus, Jaeger, Loki, Grafana, and audit records for the live executions.

Every run requires canonical `complete`, expected citations/artifacts, no hidden fallback
warnings, no secret leakage, and no unexpected error logs.

## Error handling and observability

- Unsupported strategies return explicit typed errors.
- Provider, retrieval, tool, checkpoint, and persistence failures retain their causal error
  chain and appear in the goal/API response in sanitized form.
- Broad exception handlers may add context but cannot convert functional failures into
  successful empty responses.
- Strategy selection, alias resolution, retrieval legs, citations, pattern phases, model
  choice, token/cost usage, and fallback decisions are recorded in structured events and
  traces.

## Merge and completion policy

Each program is committed independently after review and verification. The final branch is
reviewed as a whole. Before merging:

1. rerun every required quality gate;
2. complete the real user-style journeys;
3. confirm the main worktree's pre-existing changes are recoverable;
4. merge `codex/agentverse-complete` into `main`;
5. restore/reconcile unrelated main-worktree changes;
6. rerun smoke verification on merged `main`;
7. update the certification report with fresh evidence and the final verdict.
