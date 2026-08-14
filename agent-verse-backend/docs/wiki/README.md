# AgentVerse Wiki

> **AgentVerse** is a vendor-agnostic, multi-tenant operating system for autonomous AI agents. An agent receives a natural-language goal, plans its own execution, calls real-world tools via the Model Context Protocol (MCP), verifies the result, and replans on failure — with zero hardcoded workflows.

**Repository:** https://github.com/harsh786/agent-verse  
**Branch:** `main`  
**Scale:** 1,227 Python files · 69 backend packages · React 19 frontend · 4 SDKs  
**Stack:** Python 3.12 · FastAPI · LangGraph · Celery · PostgreSQL+pgvector · Redis

---

## Onboarding Guides

| Guide | Audience | Focus |
|-------|----------|-------|
| [Contributor Guide](onboarding/contributor-guide.md) | New engineers, OSS contributors | Environment setup, architecture walkthrough, first PR |
| [Staff Engineer Guide](onboarding/staff-engineer-guide.md) | Staff / principal engineers | Deep architecture, design decisions, failure modes |
| [Executive Guide](onboarding/executive-guide.md) | VP / Director / PM | Capability map, risk, investment thesis |

---

## Deep-Dive Technical Documentation

## 📚 Deep-Dive Topic Libraries (Folder-Based)

> Each category below is a fully-expanded folder with 4–6 deep-dive files covering architecture, workflow diagrams, real-world examples, scalability at millions of requests, latency analysis, and cross-component integration.

| Category | Folder | Files | Description |
|---|---|---|---|
| Agent Memories | [agent-memories/](agent-memories/) | 6 | 11 memory types, scoping, write/recall flows, safety, scalability |
| Knowledge & KG | [knowledge-and-kg/](knowledge-and-kg/) | 6 | Collections, graph nodes/edges, vector+lexical indexes, graph vs vector decision |
| Ingestion | [ingestion/](ingestion/) | 6 | All parsers, connectors, media, pipeline, failure handling |
| Retrieval Strategies | [retrieval-strategies/](retrieval-strategies/) | 6 | Hybrid search, advanced retrieval, reranking, multi-hop, evaluation |
| Embeddings | [embeddings/](embeddings/) | 5 | Provider abstraction, types, drift detection, scalability |
| Prompt Builder | [prompt-builder/](prompt-builder/) | 4 | Planner/executor/verifier prompts, context injection, safety |
| Agent Improvement | [agent-improvement/](agent-improvement/) | 5 | Eval-driven, prompt optimization, reflexion, regression detection |
| Evals | [evals/](evals/) | 4 | Goal/retrieval/safety evals, judge models, online vs offline |
| Observability | [observability/](observability/) | 5 | Logging, metrics/SLOs, distributed tracing, cost, incident workflows |
| Guardrails | [guardrails/](guardrails/) | 4 | Injection detection, content safety, output validation, HITL |
| Governance | [governance/](governance/) | 6 | RBAC, policy engine, cost control, audit trail, enterprise controls |
| Scopes | [scopes/](scopes/) | 3 | Scope types, enforcement layers, data-leakage prevention |
| Multimodal | [multimodal/](multimodal/) | 5 | Text/PDF/code, visual, audio/video/browser, visual context in planning |
| Multi AI Model Router | [multi-ai-model-router/](multi-ai-model-router/) | 5 | Role-based routing, cost/latency/quality, provider fallback, scale |
| Chunking Strategies | [chunking-strategies/](chunking-strategies/) | 6 | Fixed/token, semantic/heading, code/table, specialized, selection guide |
| Hallucination Handling | [hallucination-handling/](hallucination-handling/) | 6 | Grounding, tool validation, NLI, consensus, observability feedback loop |
| Core Platform Workflows | [core-platform-workflows/](core-platform-workflows/) | 6 | Startup, queue routing, AgentGraph execution, tool execution, SSE |
| Other Core Concepts | [other-core-platform-concepts/](other-core-platform-concepts/) | 6 | MCP, providers, reliability, Celery/Postgres/Redis, SDKs |
| RPA Automation | [rpa-automation/](rpa-automation/) | 6 | Playwright sessions, 13 RPA tools, credential injection, artifact storage, security |
| Token & Cost Optimization | [token-optimization/](token-optimization/) | 6 | Token budgeting, model selection, caching, latency optimizer, A/B testing |
| OCR Engine | [ocr/](ocr/) | 6 | Tesseract pipeline, 13 doc types, Aadhaar masking, Verhoeff/GSTIN validation, global LLM extraction |

**Total: 21 categories · 140 files · ~53,200 lines of documentation**

---

## Reference Documentation (Single-Page)

### Core Agent System
| Page | What it covers |
|---|---|
| [Agent Patterns Overview](agent-patterns.md) | All 28 patterns summary with selection guide |
| **[Agent Patterns Deep-Dive →](agent-patterns/README.md)** | **Complete sub-document library — real-world examples + full ecosystem integration** |
| ↳ [Core Execution Patterns](agent-patterns/01-core-execution-patterns.md) | Plan-Execute, ReAct, Workflow — with real DevOps/customer-support examples |
| ↳ [Self-Improvement Patterns](agent-patterns/02-self-improvement-patterns.md) | Reflection, Reflexion, Self-Refine, Self-Consistency — with learning loops |
| ↳ [Multi-Agent Patterns](agent-patterns/03-multi-agent-patterns.md) | Supervisor, Debate, Consensus, Peer Review — with investment analysis examples |
| ↳ [Tree & Search Patterns](agent-patterns/04-tree-and-search-patterns.md) | ToT, GoT, LATS, Goal Tree — with pricing strategy and migration examples |
| ↳ [Code & Execution Patterns](agent-patterns/05-code-and-execution-patterns.md) | CodeAct, Program of Thought, Loop Engineering — with data analysis examples |
| ↳ [Decomposition Patterns](agent-patterns/06-decomposition-and-planning-patterns.md) | ReWOO, LLM Compiler, Least-to-Most, Few-Shot CoT — with parallel planning |
| ↳ [Autonomous Patterns](agent-patterns/07-autonomous-and-constitutional-patterns.md) | AutoGPT, BabyAGI, Voyager, Constitutional AI — with skill libraries |
| [Platform Workflows](platform-workflows.md) | Full request lifecycle (submit → queue → execute → SSE), two-phase service wiring, Celery topology |
| [Multi-Agent & Civilization](multi-agent-civilization.md) | A2A dispatch, HMAC signing, W3C trace propagation, coordination patterns (CAMEL/Swarm/GroupChat/MAGENTIC/MOA/Auction) |

### Knowledge & Retrieval
| Page | What it covers |
|---|---|
| [RAG System](rag-system.md) | All 20+ RAG patterns (Corrective, Self-RAG, FLARE, RAPTOR, Fusion, Adaptive, ColBERT…) + retrieval engine |
| [Knowledge & Knowledge Graph](knowledge-and-kg.md) | KnowledgeStore, KGStore, entity extraction, multi-hop reasoning, citation management |
| [Chunking Strategies](chunking-strategies.md) | All 15 chunking strategies (semantic, AST, heading, parent-child, sentence-window, contextual enricher, late chunker…) |
| [Ingestion Pipeline](ingestion-pipeline.md) | Full pipeline: parsers (PDF/DOCX/image/audio/video/email), connectors (Notion/GDrive/SharePoint), quality checks, dedup |

### Intelligence Layer
| Page | What it covers |
|---|---|
| [Memory System](memory-system.md) | All 9 memory types: working, execution, episodic, long-term, reflexion, procedural, prospective, consolidation, salience |
| [Prompt Builder](prompt-builder.md) | PromptContextBundle assembly, auto-compress, token budgets, citation injection, variant A/B |
| [Embedding System](embedding-system.md) | Model registry, fallback chain, batch embedding, drift monitor, re-embedding policies |
| [AI Model Router](ai-model-router.md) | Multi-model routing (planner/executor/verifier/judge), complexity scorer, shadow router, circuit breaker |
| [Hallucination Handling](hallucination-handling.md) | 9-layer defense: NLI checker, claim decomposer, attribution verifier, CRAG, Self-RAG, HITL escalation |

### Quality & Safety
| Page | What it covers |
|---|---|
| [Evals & Improvement](evals-and-improvement.md) | Goal/RAG/agent/safety scores, regression gate, self-improvement engine, multi-turn eval, attribution verifier |
| [Guardrails](guardrails.md) | Prompt injection, encoding attacks, indirect injection, exfiltration guard, streaming guard, toxicity classifier |
| [Governance & Security](governance-and-security.md) | RBAC, RLS, audit trail, cost control, HITL, policy engine, SIEM, compliance (SOC2/GDPR/PCI/HIPAA) |

### Observability & Infrastructure
| Page | What it covers |
|---|---|
| [Observability](observability.md) | Structured logging, metrics, OTEL tracing, RAG/pattern traces, alert router, SLO tracker, SSE event flow |
| [Reliability & Infrastructure](reliability-and-infrastructure.md) | Circuit breakers, bulkheads, dedup, distributed locks, rollback engine, DB schema, Redis patterns |
| [Multimodal](multimodal.md) | Text/PDF/image/audio/video/email processing, RPA browser automation (Playwright), visual perception |

### Document Intelligence
| Page | What it covers |
|---|---|
| [OCR Engine](ocr-engine.md) | Tesseract + LLM vision pipeline, 13 document types, Aadhaar masking (Verhoeff), GSTIN validation, global any-doc LLM extraction |

### Integration & Deployment
| Page | What it covers |
|---|---|
| [SDK & Integrations](sdk-and-integrations.md) | Python SDK, TypeScript SDK, GitHub Action, REST API reference, webhook events |
| [Configuration & Deployment](configuration-and-deployment.md) | All env vars, Docker Compose, K8s Helm, production checklist, health checks, monitoring |

---


### II. Core Subsystems

6. [Agent Package — 25+ Execution Patterns](#6-agent-package)
7. [RAG Package — 18 Retrieval Strategies](./rag-system.md)
8. [Memory System — 11 Memory Types](./memory-system.md) ✨
9. [Knowledge & Knowledge Graph](./knowledge-and-kg.md) ✨
10. [Ingestion Pipeline](./ingestion-pipeline.md) ✨
11. [Embedding System](./embedding-system.md) ✨
12. [Providers — LLM Abstraction](#12-providers)

### III. Infrastructure & Operations

13. [Governance — Audit, Cost, HITL, Policy](#13-governance)
14. [Guardrails 2.0](#14-guardrails-20)
15. [Reliability — Circuit Breakers, Rollback, Bulkheads](#15-reliability)
16. [Observability — Logging, Metrics, Tracing](#16-observability)
17. [Scaling — Celery Task Queues](#17-scaling)
18. [Database — SQLAlchemy + RLS + Alembic](#18-database)

### IV. Coordination & Multi-Agent

19. [Civilization — A2A Dispatch & Society](#19-civilization)
20. [Coordination — CAMEL, Swarm, MAGENTIC, MOA, Auction](#20-coordination)
21. [AI Router — Multi-Model Routing](#21-ai-router)
22. [Intelligence — Self-Optimizer, Eval Runner, Prompt Optimizer](#22-intelligence)

### V. Platform Services

23. [MCP — Model Context Protocol](#23-mcp)
24. [GoalService — Lifecycle & SSE Streaming](#24-goalservice)
25. [Context — PromptBuilder, ContextBudget, CitationManager](#25-context)
26. [Auth — API Keys, MFA, SAML, Keycloak](#26-auth)
27. [Enterprise — Marketplace, Red Team, Compliance, Simulation](#27-enterprise)
28. [RPA & Perception — Browser Automation](#28-rpa--perception)

### VI. SDKs & Interfaces

29. [Python SDK (`agent-verse-sdk-python/`)](#29-python-sdk)
30. [TypeScript SDK (`agent-verse-sdk-typescript/`)](#30-typescript-sdk)
31. [Frontend — React 19 Application](#31-frontend)
32. [GitHub Action](#32-github-action)

### VII. Operations & Reference

33. [Configuration Reference](#33-configuration-reference)
34. [API Surface — 40+ Routers](#34-api-surface)
35. [Database Migrations (0001→0104)](#35-database-migrations)
36. [Deployment — Docker, Helm, Celery Worker](#36-deployment)

---

## 1. Monorepo Layout

```
agent-verse/
├── agent-verse-backend/        ← Python 3.12 · FastAPI · LangGraph (1,227 files)
│   ├── app/                    ← 69 packages (the core OS)
│   ├── tests/                  ← mirrors app/ layout
│   ├── infra/                  ← docker-compose, Helm charts
│   └── docs/wiki/              ← you are here
├── agent-verse-frontend/       ← React 19 · Vite · TanStack Query · Zustand
├── agent-verse-sdk-python/     ← Official Python client + CLI
├── agent-verse-sdk-typescript/ ← Official TypeScript client (zero deps)
└── agent-verse-github-action/  ← Docker-based GitHub Action
```

**Source:** [`app/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app)

---

## 2. Application Assembly

**File:** [`app/main.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/main.py)

`create_app()` is a factory that wires 28+ services onto `FastAPI.state` and registers ~40 API routers. It uses **two-phase initialization**:

**Phase 1 — synchronous wiring (always):** All services are constructed with in-memory backends. This is what tests use.

**Phase 2 — lifespan upgrade (production only, when `manage_pools=True`):** `ConnectionPools` starts async DB/Redis pools; all in-memory services are swapped for DB/Redis-backed equivalents and re-hydrated via `sync_from_db()`.

This split means the app is always testable without infrastructure while being fully persistent in production. See the [Staff Engineer Guide](onboarding/staff-engineer-guide.md#two-phase-service-wiring) for detailed swap mechanics.

---

## 3. The Agent Loop

**File:** [`app/agent/graph.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py)

The core execution engine is a **LangGraph StateGraph** with five nodes:

```
START → initialize → rag_retrieval → plan → execute → verify
                                              ↓            ↓
                                          replan ←──── (failed)
                                              ↓
                                           END (complete | max_iter | waiting_human)
```

Each node is an async function receiving `GraphState` and returning partial updates (LangGraph's reducer pattern merges them). Three distinct LLM roles are used:

| Role | Prompt | Task |
|------|--------|------|
| **Planner** | `PLANNER_SYSTEM` | Goal + context → ordered step list |
| **Executor** | `EXECUTOR_SYSTEM` | One step → tool calls |
| **Verifier** | `VERIFIER_SYSTEM` | Step + result → success / failure + score |

High-risk steps (keywords: `deploy`, `delete`, `drop`, `prod`, `destroy`, `truncate`, `rm`) are routed through the **HITL Gateway** before execution.

**State:** [`app/agent/state.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/state.py) — `AgentState` dataclass, fully serializable for LangGraph checkpointing.

---

## 4. Service Wiring

All 28 services registered on `app.state`:

| Service | Class | Location |
|---------|-------|----------|
| `tenant_service` | `TenantService` | `app/services/tenant_service.py` |
| `goal_service` | `GoalService` | `app/services/goal_service.py` |
| `mcp_registry` | `MCPRegistry` | `app/mcp/registry.py` |
| `mcp_client` | `MCPClient` | `app/mcp/client.py` |
| `oauth_manager` | `OAuthFlowManager` | `app/mcp/oauth.py` |
| `agent_store` | `AgentStore` | `app/api/agents.py` |
| `meta_agent` | `MetaAgentPlanner` | `app/intelligence/meta_agent.py` |
| `hitl_gateway` | `HITLGateway` | `app/governance/hitl.py` |
| `audit_log` | `AuditLog` | `app/governance/audit.py` |
| `cost_controller` | `CostController` | `app/governance/cost.py` |
| `policy_engine` | `PolicyEngine` | `app/governance/policies.py` |
| `schedule_store` | `ScheduleStore` | `app/triggers/` |
| `nl_scheduler` | `NLScheduler` | `app/triggers/` |
| `knowledge_store` | `KnowledgeStore` | `app/rag/store.py` |
| `semantic_cache` | `SemanticCache` | `app/rag/semantic_cache.py` |
| `long_term_memory` | `LongTermMemoryStore` | `app/memory/long_term.py` |
| `eval_runner` | `EvalRunner` | `app/intelligence/eval_runner.py` |
| `compliance_controller` | `ComplianceController` | `app/enterprise/compliance.py` |
| `simulation_runner` | `SimulationRunner` | `app/enterprise/simulation.py` |
| `red_team_runner` | `RedTeamRunner` | `app/enterprise/red_team.py` |
| `marketplace` | `Marketplace` | `app/enterprise/marketplace.py` |
| `self_optimizer` | `SelfOptimizer` | `app/intelligence/self_optimizer_v2.py` |

---

## 5. Multi-tenancy & Plan Tiers

**File:** [`app/tenancy/context.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/tenancy/context.py)

All authenticated requests carry an immutable `TenantContext`:

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    plan: PlanTier        # free | starter | professional | enterprise
    api_key_id: str
    roles: tuple[str, ...]
```

Plan-level limits are enforced at three layers:

| Layer | Mechanism |
|-------|-----------|
| **API** | `TenantMiddleware` rate-limiter checks `requests_per_minute` |
| **Agent** | `GoalService` enforces `goals_per_day` |
| **DB** | PostgreSQL RLS policies filter by `app.tenant_id` GUC |

| Plan | RPM | Goals/day | Agents | Goal timeout |
|------|-----|-----------|--------|-------------|
| Free | 60 | 25 | 3 | 1 h |
| Starter | 120 | 100 | 10 | 2 h |
| Professional | 600 | 1,000 | 50 | 8 h |
| Enterprise | 10,000 | 50,000 | 1,000 | 24 h |

---

## 6. Agent Package

**Directory:** [`app/agent/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/)

The agent package contains the execution engine and 25+ agent patterns:

| Module | Purpose |
|--------|---------|
| [`graph.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py) | Core LangGraph state machine (5 nodes) |
| [`state.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/state.py) | `AgentState`, `GoalStatus`, `StepResult`, `SubGoal` |
| [`prompts.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/prompts.py) | Planner/Executor/Verifier/Reflexion/CoT system prompts |
| [`supervisor.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/supervisor.py) | Supervisor pattern: routes sub-tasks to specialist agents |
| [`debate.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/debate.py) | Multi-agent debate to reach consensus |
| [`consensus.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/consensus.py) | Voting-based consensus across agent responses |
| [`goal_tree.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/goal_tree.py) | Hierarchical goal decomposition |
| [`workflow_planner.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/workflow_planner.py) | Builds static workflow DAGs from goals |
| [`workflow_executor.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/workflow_executor.py) | Executes workflow DAGs with dependency tracking |
| [`model_router.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/model_router.py) | Routes to best model per task type |
| [`tool_risk.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/tool_risk.py) | Classifies tool calls by risk level |
| [`sanitization.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/sanitization.py) | Strips sensitive data from events before emission |
| [`router.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/router.py) | Auto-routes a goal to the best agent |
| [`reflexion_wirer.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/reflexion_wirer.py) | Wires reflexion lessons back into planning |
| [`pattern_assembler.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py) | Assembles multi-agent patterns from config |
| [`dynamic_graph.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/dynamic_graph.py) | Runtime-configurable graph topology |

**Autonomy modes:** `supervised` (HITL on every step), `bounded-autonomous` (HITL on high-risk only), `fully-autonomous` (no HITL).

---

## 7. RAG Package

**Directory:** [`app/rag/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/)

AgentVerse implements 18 RAG strategies as interchangeable units behind a unified contract:

```python
class RAGStrategy(StrEnum):
    NAIVE | HYBRID | HYDE | MULTI_HOP | GRAPH | CORRECTIVE | ADAPTIVE |
    MODULAR | SPECULATIVE | AGENTIC | WEB_AUGMENTED | FUSION | SELF_RAG |
    FLARE | RAPTOR | AGENTIC_CHUNKING | COLBERT | RAFT
```

**Strategy Selection:** `app/rag/contracts.py` — `resolve_rag_strategy()` maps aliases and validates strategy names.

**Core Store:** [`app/rag/store.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/store.py) — `KnowledgeStore` provides hybrid pgvector + trigram search.

**Semantic Cache:** [`app/rag/semantic_cache.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/semantic_cache.py) — Deduplicates LLM calls by embedding similarity, reducing cost.

**Advanced modules:**
- [`app/rag/raft.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/raft.py) — RAFT: Retrieval-Augmented Fine-Tuning dataset generation
- [`app/rag/contextual_enricher.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/contextual_enricher.py) — Adds document-level context to each chunk
- [`app/rag/parent_child_chunker.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/parent_child_chunker.py) — Retrieves parent context for child chunks
- [`app/rag/late_chunker.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/late_chunker.py) — Embeds documents whole, chunks after retrieval
- [`app/rag/sentence_window.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rag/sentence_window.py) — Expands retrieved sentences to surrounding window

---

## 8. Memory Package

**Directory:** [`app/memory/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/memory/)

Nine memory types provide a complete cognitive architecture:

| Memory Type | Class | Scope | Persistence |
|-------------|-------|-------|-------------|
| Execution | `ExecutionMemory` | Per-goal | In-memory |
| Long-term | `LongTermMemoryStore` | Cross-session | PostgreSQL |
| Reflexion | `ReflexionMemory` | Per-agent | PostgreSQL |
| Episodic | `EpisodicMemory` | Per-agent | PostgreSQL |
| Procedural | `ProceduralMemory` | Tenant-wide | PostgreSQL |
| Prospective | `ProspectiveMemory` | Per-agent | PostgreSQL |
| Working | `WorkingMemory` | Per-step | In-memory |
| Consolidation | `MemoryConsolidation` | Batch | PostgreSQL |
| Salience | `SalienceScorer` | Cross-type | — |

**Consolidation** ([`app/memory/consolidation.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/memory/consolidation.py)) runs nightly via Celery beat to merge episodic memories into long-term storage, preventing unbounded growth.

---

## 9. Knowledge Graph

**Directory:** [`app/knowledge_graph/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/knowledge_graph/)

Entities and relationships extracted from ingested documents stored as a graph for multi-hop reasoning.

| Module | Purpose |
|--------|---------|
| `kg_store.py` | CRUD for nodes/edges, tenant-isolated |
| `extractor.py` | LLM-based entity + relation extraction |
| `community_detection.py` | Louvain clustering for topic summarization |
| `ingestion_hook.py` | Auto-extracts KG on document ingest |
| `multi_hop_reasoner.py` | Traverses graph to answer multi-hop questions |

**Integration:** The `GRAPH` RAG strategy uses `multi_hop_reasoner.py` to answer questions that require traversing multiple relationship hops.

---

## 10. Ingestion Pipeline

**Directory:** [`app/ingestion/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/ingestion/)

Full document processing pipeline from raw file to searchable vectors:

```
Source → Parser → Chunker → Quality Check → Dedup → Embedder → KG Extraction → Store
```

**Parsers:** PDF, DOCX, email, vision (images), audio (transcription), video  
**Connectors:** Notion, Google Drive, SharePoint  
**Chunkers:** semantic, heading-based, AST (code), PDF layout, timestamp (video), scene (video), table-aware

**Quality Gate** ([`app/ingestion/quality_checks.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/ingestion/quality_checks.py)):
- Minimum chunk length
- Language detection
- Content type validation
- Duplicate detection (minhash)

---

## 11. Embedding Orchestration

**Directory:** [`app/embedding/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/embedding/)

**File:** [`app/embedding/orchestrator.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/embedding/orchestrator.py)

The `EmbeddingOrchestrator` selects the optimal embedding model based on:
1. **Content type** (code uses code-optimized models, images use multimodal)
2. **Plan tier** (free tenants restricted to low-cost models)
3. **Provider availability** (fallback chain: `anthropic → openai → voyage → gemini → fake`)

**Drift Monitor** ([`app/embedding/drift_monitor.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/embedding/drift_monitor.py)): Detects embedding distribution shifts that indicate the model was swapped; triggers automatic re-embedding.

---

## 12. Providers

**Directory:** [`app/providers/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/providers/)

Vendor-agnostic LLM abstraction via the `LLMProvider` structural Protocol:

```python
@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]: ...
```

**Providers:** Anthropic (claude-*), OpenAI-compatible (any endpoint), Voyage (embeddings), Gemini, Fake (deterministic, zero cost — used in all tests)

**Circuit Breaker** ([`app/providers/circuit_breaker.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/providers/circuit_breaker.py)): Wraps `call_with_circuit_breaker()` — opens after 5 consecutive failures, half-opens after 60 s.

**Vault** ([`app/providers/vault.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/providers/vault.py)): Encrypted per-tenant API key storage. Keys are decrypted at goal submission, never stored in plaintext.

---

## 13. Governance

**Directory:** [`app/governance/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/governance/)

| Module | Class | Purpose |
|--------|-------|---------|
| `audit.py` | `AuditLog`, `AuditEvent` | Append-only SOC2 event trail |
| `audit_v2.py` | `AuditLogV2` | Structured audit with SIEM export |
| `audit_v3.py` | `AuditLogV3` | Tamper-evident chained audit log |
| `cost.py` | `CostController` | Per-goal/per-tenant budget enforcement |
| `hitl.py` | `HITLGateway` | Human-in-the-loop approval queue |
| `policies.py` | `PolicyEngine` | Tool-level allow/deny policy evaluation |
| `permissions.py` | `PermissionMatrix`, `ActionLevel` | RBAC action classification |
| `legal_holds.py` | `LegalHoldController` | Freeze tenant data for legal discovery |
| `compliance_bundles.py` | `ComplianceBundleRegistry` | GDPR/SOC2/PCI-DSS policy packs |
| `siem_adapters.py` | Splunk/Datadog/QRadar adapters | Forward audit events to SIEM |
| `time_policy.py` | `TimePolicyEngine` | Restrict tool execution to business hours |

**Audit immutability:** The production PostgreSQL table has an `BEFORE UPDATE OR DELETE` trigger that raises an exception — no code-level deletion is possible.

---

## 14. Guardrails 2.0

**Directory:** [`app/guardrails_v2/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/guardrails_v2/)

Multi-layer content safety system applied at both input and output:

| Layer | Detection |
|-------|-----------|
| PII | SSN, credit cards, emails, phone numbers |
| Secrets | OpenAI/Anthropic/GitHub API keys, passwords |
| Prompt Injection | "ignore previous instructions", "act as", "jailbreak", DAN mode |
| Toxicity | Hate speech, violence, NSFW content |
| Encoding Attacks | Base64/hex obfuscation of malicious instructions |
| Indirect Injection | Malicious instructions embedded in retrieved documents |

**Streaming Guard** ([`app/guardrails_v2/streaming_guard.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/guardrails_v2/streaming_guard.py)): Applies guardrails mid-stream, blocking or redacting before tokens reach the client.

---

## 15. Reliability

**Directory:** [`app/reliability/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/reliability/)

| Module | Mechanism |
|--------|-----------|
| `circuit_breaker.py` | In-memory circuit breaker with exponential backoff |
| `redis_circuit_breaker.py` | Redis-backed circuit breaker (survives restarts) |
| `bulkhead.py` | Per-tenant concurrency limits to prevent noisy-neighbour |
| `dedup.py` | Content-hash deduplication of duplicate goal submissions |
| `distributed_lock.py` | Redis Redlock for cross-replica critical sections |
| `idempotency.py` | Idempotency keys for safe retries |
| `rollback.py` | `RollbackEngine` — reverses completed tool calls on failure |
| `tool_inverses.py` | Compensating actions for each reversible tool |

---

## 16. Observability

**Directory:** [`app/observability/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/observability/)

| Module | What it records |
|--------|----------------|
| `logging.py` | Structured JSON logs with `tenant_id`, `goal_id`, `request_id` |
| `metrics.py` | Prometheus counters/histograms (goal start/complete/fail, tool calls, plan duration) |
| `tracing.py` | OpenTelemetry spans for every node transition |
| `rag_trace.py` | Per-strategy retrieval traces (query, chunks, scores) |
| `pattern_trace.py` | Multi-agent pattern execution traces |
| `cost_breakdown.py` | Per-goal token cost breakdown by LLM call |
| `alert_router.py` | Routes metric threshold breaches to PagerDuty/Slack |
| `slo_tracker.py` | Tracks p95 latency and error rate SLOs |

---

## 17. Scaling

**Directory:** [`app/scaling/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/scaling/)

**File:** [`app/scaling/celery_app.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/scaling/celery_app.py)

Per-plan Celery queue routing prevents noisy-neighbour effects:

| Queue | Tenants | Priority |
|-------|---------|---------|
| `goals.enterprise` | Enterprise | Highest |
| `goals.professional` | Professional | High |
| `goals.starter` | Starter | Normal |
| `goals.free` | Free | Low |
| `goals_dlq` | All (dead letter) | — |
| `schedules` | All (cron triggers) | Normal |
| `maintenance` | System | Low |

Redis Sentinel support (`sentinel://` scheme) and Redis Cluster support are built in via environment variables.

---

## 18. Database

**Directory:** [`app/db/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/db/)

- **ORM:** SQLAlchemy 2 async + asyncpg
- **Migrations:** Alembic (104 revisions: `0001` → `0104`)
- **Models:** 20+ tables — agents, goals, governance, knowledge, memory, MCP, auth, scheduling, etc.
- **RLS:** [`app/db/rls.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/db/rls.py) — `SET LOCAL app.tenant_id` per transaction, automatically reverted on commit/rollback

---

## 19. Civilization

**Directory:** [`app/civilization/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/)

Agent-to-agent dispatch with society-level governance:

| Module | Purpose |
|--------|---------|
| `a2a_dispatch.py` | HMAC-signed internal A2A dispatch via GoalService |
| `society.py` | Multi-agent society membership management |
| `blackboard.py` | Shared blackboard for agent communication |
| `constitution.py` | Governance rules for society-level decisions |
| `governor.py` | Enforces constitution rules on agent actions |
| `learning.py` | Society-level knowledge sharing |
| `bus.py` | Event bus for civilization-wide events |

W3C traceparent propagation ensures every A2A dispatch is traceable end-to-end across agents.

---

## 20. Coordination

**Directory:** [`app/coordination/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/coordination/)

Six multi-agent coordination patterns, each with a dedicated API router:

| Pattern | Directory | Mechanism |
|---------|-----------|-----------|
| CAMEL | `camel/` | Role-playing collaborative problem solving |
| Swarm | `swarm/` | Emergent swarm intelligence with stigmergy |
| Group Chat | `group_chat/` | LLM-moderated group discussion |
| MAGENTIC | `magentic/` | Magnetic task assignment to optimal agents |
| MOA | `moa/` | Mixture-of-Agents aggregation |
| Auction | `auction/` | Incentive-compatible task auction |
| Handoffs | `handoffs/` | Explicit agent-to-agent handoffs with context |

---

## 21. AI Router

**Directory:** [`app/ai_router/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/ai_router/)

Multi-dimensional routing for model selection:

| Policy | Optimizes |
|--------|-----------|
| Role policy | Task type → model family |
| Cost policy | Minimize token cost per quality target |
| Latency policy | p95 latency budget compliance |
| Quality policy | Maximize EvalRunner score |
| Provider health | Avoids unhealthy/rate-limited providers |
| Complexity scorer | Classifies task complexity to right-size model |
| Shadow router | A/B routes traffic to experimental models |

---

## 22. Intelligence

**Directory:** [`app/intelligence/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/intelligence/)

| Module | Purpose |
|--------|---------|
| `eval_runner.py` | 7-dimension goal scoring (task_completion, efficiency, accuracy, safety, coherence, sla, tool_relevance) |
| `eval_suite.py` | Suite runner for batched evaluation |
| `self_optimizer_v2.py` | Analyzes failed evals → suggests agent config improvements |
| `prompt_optimizer.py` | Optimizes prompts using DSPy-style gradient descent |
| `meta_agent.py` | NL → agent config (`MetaAgentPlanner`) |
| `nli_checker.py` | Natural language inference for claim verification |
| `claim_decomposer.py` | Decomposes complex claims into atomic facts |
| `output_anomaly.py` | Detects anomalous LLM outputs (hallucination signals) |
| `guardrails.py` | High-level guardrail orchestration |
| `explainability.py` | `DecisionTrace` for audit-grade decision explanations |

---

## 23. MCP

**Directory:** [`app/mcp/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/mcp/)

Model Context Protocol implementation:

| Module | Purpose |
|--------|---------|
| `registry.py` | Redis-backed per-tenant MCP server registry |
| `client.py` | HTTP client for `tools/list` + tool execution |
| `oauth.py` | PKCE OAuth 2.0 flow manager |
| `capability_search.py` | Semantic search over registered tool capabilities |
| `tool_intelligence.py` | Tool usage analytics and recommendation |
| `code_interpreter.py` | Sandboxed code execution tool |

**Auth types supported:** bearer, api_key, OAuth 2.0 AC, OAuth 2.0 CC, PKCE, basic, custom header, mTLS, HMAC, none.

---

## 24. GoalService

**File:** [`app/services/goal_service.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/services/goal_service.py)

Central orchestrator for goal lifecycle:

1. Accepts goal submission → creates `GoalRecord`
2. Launches `AgentGraph` as an `asyncio` background task
3. Receives events from the agent via callback → appends to `GoalRecord.events`
4. Fans out events to all live SSE subscribers via per-goal `asyncio.Queue`
5. Evicts completed goals from in-memory cache after 1 hour (TTL)

**SSE streaming:** Every goal event is immediately pushed to subscribers. The poison-pill sentinel `None` signals end-of-stream.

**Token streaming:** Partial tokens from the LLM executor are surfaced as `token` events for real-time display.

---

## 25. Context

**Directory:** [`app/context/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/context/)

| Module | Purpose |
|--------|---------|
| `prompt_builder.py` | Assembles `PromptContextBundle` → model-ready prompt with auto-compression |
| `prompt_compressor.py` | Token-budget-aware context compression |
| `citation_manager.py` | Tracks chunk citations; generates footnotes |
| `context_pipeline.py` | Orchestrates multi-source context assembly |
| `prompt_variant_selector.py` | A/B selects prompt variants for optimization |
| `context_budget.py` | Per-model token budget enforcement |

**Auto-compress threshold:** When assembled context exceeds 85% of context window, `PromptCompressor` is invoked automatically before sending to the LLM.

---

## 26. Auth

**Directory:** [`app/auth/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/auth/)

| Module | Mechanism |
|--------|-----------|
| `api_key_auth.py` | Bearer token validation against hashed DB records |
| `mfa.py` | TOTP-based MFA |
| `saml.py` | SAML 2.0 IdP integration |
| `keycloak.py` | Keycloak OIDC integration |
| `scim.py` | SCIM 2.0 user provisioning |
| `delegation.py` | Scoped API key delegation |
| `scope_enforcement.py` | `ScopeEnforcementMiddleware` validates API key scopes |
| `agent_identity.py` | `AgentIdentityService` for agent-to-agent auth |
| `scope_seeder.py` | Seeds default scopes for new tenants |

---

## 27. Enterprise

**Directory:** [`app/enterprise/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/enterprise/)

| Module | Purpose |
|--------|---------|
| `marketplace.py` | Template gallery: browse, deploy, fork agent templates |
| `marketplace_v2.py` | Enhanced marketplace with versioning and ratings |
| `compliance.py` | `ComplianceController`: GDPR/SOC2/PCI-DSS reporting |
| `compliance_v2.py` | `ComplianceChecker`: real-time policy validation |
| `simulation.py` | `SimulationRunner`: mock-tool sandbox for safe testing |
| `red_team.py` | `RedTeamRunner`: automated adversarial test scenarios |

---

## 28. RPA & Perception

**Directories:** [`app/rpa/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/rpa/), [`app/perception/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/perception/)

RPA enables agents to automate browser-based tasks:

- **Browser automation:** Playwright-backed session management
- **Page analysis:** DOM extraction, element identification, form filling
- **Vision:** Screenshot analysis via multimodal LLM
- **Artifact storage:** Screenshots, PDFs, downloaded files stored in MinIO/S3

---

## 29. Python SDK

**Directory:** [`agent-verse-sdk-python/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-sdk-python/)

```bash
pip install agentverse-sdk
agentverse goals submit --goal "Summarize recent news" --tenant-key <key>
```

- Full async client via `httpx`
- Pydantic models for all request/response types
- CLI (`agentverse`) for goal submission, status polling, and history

---

## 30. TypeScript SDK

**Directory:** [`agent-verse-sdk-typescript/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-sdk-typescript/)

```typescript
import { AgentVerseClient } from 'agentverse-sdk';
const client = new AgentVerseClient({ apiKey: '<key>' });
const goal = await client.goals.submit({ goal: 'Summarize recent news' });
```

Zero runtime dependencies. Types generated from the OpenAPI spec.

---

## 31. Frontend

**Directory:** [`agent-verse-frontend/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-frontend/)

Feature-sliced architecture under `src/features/`:

| Feature | Purpose |
|---------|---------|
| `goals/` | Goal submission form, live execution stream (SSE) |
| `agents/` | Agent CRUD, config editor |
| `governance/` | Audit log viewer, cost dashboard, HITL approval UI |
| `rpa/` | Browser automation session viewer |
| `marketplace/` | Template gallery |
| `workflow-builder/` | Visual DAG workflow editor |
| `observability/` | Metrics, traces, SLO dashboard |

Transport: `src/lib/api/client.ts` (HTTP), `src/lib/sse/useGoalStream.ts` (SSE), `src/lib/ws/useCollabSocket.ts` (WebSocket collaboration)

---

## 32. GitHub Action

**Directory:** [`agent-verse-github-action/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-github-action/)

```yaml
- uses: harsh786/agent-verse/agent-verse-github-action@main
  with:
    api-key: ${{ secrets.AGENTVERSE_KEY }}
    goal: "Run security audit on changed files"
    wait: true
```

Docker-based action. `entrypoint.py` submits a goal and optionally polls until completion, failing the CI step if the goal fails.

---

## 33. Configuration Reference

**File:** [`app/core/config.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/core/config.py)

All configuration via environment variables (12-factor):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse` | Async DB DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Single-node Redis |
| `REDIS_SENTINEL_URLS` | `""` | Comma-separated Sentinel nodes |
| `REDIS_CLUSTER_NODES` | `""` | Comma-separated cluster nodes |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `ANTHROPIC_API_KEY` | `""` | Enables Anthropic provider |
| `OPENAI_API_KEY` | `""` | Enables OpenAI-compatible provider |
| `VOYAGE_API_KEY` | `""` | Enables Voyage embedding provider |
| `GOOGLE_API_KEY` | `""` | Enables Gemini provider |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | OpenTelemetry collector endpoint |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `DB_POOL_SIZE` | `10` | SQLAlchemy pool size |
| `VAULT_MASTER_KEY` | — | **Required in production** |

> **Security:** Production refuses to start if `DATABASE_URL` still uses the default `agentverse:agentverse` credentials.

---

## 34. API Surface

~40 FastAPI routers, all versioned under `/api/v1/`:

`/goals` · `/agents` · `/tenants` · `/knowledge` · `/memory` · `/governance` · `/mcp` · `/schedules` · `/tools` · `/templates` · `/marketplace` · `/compliance` · `/intelligence` · `/coordination` · `/coordination/camel` · `/coordination/swarm` · `/coordination/group-chat` · `/coordination/magentic` · `/coordination/moa` · `/coordination/auction` · `/coordination/handoffs` · `/civilization` · `/a2a` · `/observability` · `/analytics` · `/auth` · `/billing` · `/admin` · `/rpa` · `/perception` · `/artifacts` · `/costs` · `/guardrails` · `/lab` · `/skills` · `/workflows` · `/builder` · `/insights` · `/replay` · `/collab`

Full OpenAPI spec: [`openapi.json`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/openapi.json)

---

## 35. Database Migrations

104 Alembic migrations from `0001` through `0104`. Never edit a deployed migration — always add a new one.

```bash
uv run alembic upgrade head                                    # apply all
uv run alembic revision --autogenerate -m "add_index_goals"   # create new
uv run alembic downgrade -1                                    # roll back one
```

---

## 36. Deployment

**Local (development):**
```bash
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis
uv run uvicorn app.main:app --reload
```

**Production containers:**
```bash
# API
docker build -t agentverse-backend .
# Celery worker (all queues)
celery -A app.scaling.celery_app worker -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,goals_dlq,schedules,maintenance -c 4
# Celery beat (cron schedules)
celery -A app.scaling.celery_app beat --scheduler celery.beat.PersistentScheduler
```

**Helm:** [`agent-verse-backend/helm/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/helm/)

---

*Last updated: 2026-08-14 | Branch: `main` | 1,227 source files*
