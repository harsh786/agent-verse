# AgentVerse Core Dynamic Orchestration — Master Prompt Plan

**Date:** 2026-07-07  
**Status:** Architecture prompt plan, ready for implementation planning  
**Scope:** Backend core runtime, frontend visibility surfaces, infra/runtime gates, tests, and agentic coding support

---

## 1. Copy-Paste Master Prompt

Use this prompt when asking an implementation agent to transform AgentVerse into a generic, world-class, dynamically orchestrated autonomous-agent platform.

```markdown
You are the chief architect, backend runtime engineer, agentic systems researcher, RAG architect, model orchestration engineer, embeddings architect, multimodal ingestion architect, security architect, eval engineer, and platform test architect for AgentVerse.

Product context:
AgentVerse is a vendor-agnostic, multi-tenant operating system for autonomous AI agents. It must support creation and safe execution of any type of agent: research agents, DevOps agents, coding agents, business workflow agents, RPA agents, governance agents, multimodal document agents, and domain-specialist agents.

Mission:
Redesign the AgentVerse core runtime so no strategy is hardcoded at goal submission time. Every goal must be dynamically analysed and routed through the right combination of agentic pattern, agentic RAG strategy, retrieval source mix, chunking strategy, embedding model, reranker, prompt-building strategy, model-routing strategy, tool-use strategy, guardrail profile, governance policy, memory policy, cache policy, eval profile, self-improvement policy, and cost/latency/quality optimisation policy.

Non-negotiable architecture principles:
- Everything is tenant-scoped.
- Every runtime decision is explicit, inspectable, auditable, and testable.
- Every strategy is selected dynamically from goal properties, tenant policy, agent config, data availability, risk level, and budget.
- No subsystem silently returns empty context; every degraded path returns a structured reason and fallback chain.
- The platform must support no-knowledge, thin-knowledge, large-knowledge, stale-knowledge, and multimodal-knowledge scenarios.
- The same core runtime must work for text, PDF, DOCX, web pages, code repos, images, audio, video, browser automation, APIs, databases, and MCP tools.
- Safety and governance override all optimisation choices.
- High-risk actions require HITL, consensus, rollback, audit, and policy enforcement.
- Model, embedding, retriever, chunker, reranker, prompt builder, and guardrail choices must be pluggable registries, not if/else sprawl.
- Every implemented subsystem must include tests, live-testability, observability, and failure-mode behaviour.

Target design:
Create a new core orchestration layer under `agent-verse-backend/app/orchestration/` that coordinates existing subsystems and routes every goal through a `GoalRuntimeProfile`:
1. Classify goal properties.
2. Build source inventory.
3. Select agentic patterns.
4. Select RAG strategy and retrieval sources.
5. Select chunking, embedding, reranking, and context budget policies.
6. Select model plan per role.
7. Select safety/governance policy profile.
8. Select memory/cache policy.
9. Execute through dynamically assembled LangGraph nodes.
10. Score, evaluate, and learn from results.

Implementation requirements:
- Keep existing working modules where possible.
- Do not rewrite the whole platform at once.
- Add bounded, well-named layers and adapters around existing implementations.
- Start with architecture, contracts, registries, and tests before deep behaviour changes.
- Preserve backward compatibility for existing goal submission APIs.
- Add feature flags for new runtime orchestration until stable.
```

---

## 2. Target Layered Architecture With Clear Folders

The platform must move from feature modules calling each other ad hoc to a layered runtime with explicit orchestration contracts.

### Layer 0: Platform Kernel

**Purpose:** Settings, app assembly, dependency wiring, runtime feature flags.

**Existing folders:**
- `agent-verse-backend/app/core/`
- `agent-verse-backend/app/main.py`
- `agent-verse-backend/app/main_services.py`

**Target additions:**
- `app/core/runtime_flags.py`
- `app/core/runtime_profiles.py`

**Responsibilities:** load environment and tenant configuration, expose feature flags, build the dependency graph once at startup, and prevent unsafe defaults in production.

### Layer 1: Tenant, Identity, Trust, and Policy Boundary

**Purpose:** Guarantee every runtime decision is tenant-scoped and policy-bound.

**Existing folders:**
- `app/tenancy/`
- `app/auth/`
- `app/governance/`
- `app/guardrails_v2/`
- `app/db/rls.py`

**Target folder:** `app/security_runtime/`

**Target files:**
- `app/security_runtime/identity_profile.py`
- `app/security_runtime/governance_profile.py`
- `app/security_runtime/action_safety_profile.py`
- `app/security_runtime/policy_bundle_selector.py`
- `app/security_runtime/guardrail_profile.py`

**Responsibilities:** resolve tenant identity, agent identity, delegated permissions, compliance policy, action safety profile, HITL profile, RLS scope, audit requirements, and cost boundary.

### Layer 2: Goal Intake and Runtime Profile Assembly

**Purpose:** Convert raw goal text into a complete execution profile.

**Existing folders:**
- `app/api/goals.py`
- `app/services/goal_service.py`
- `app/agent_runtime/`

**Target folder:** `app/orchestration/`

**Target files:**
- `app/orchestration/goal_classifier.py`
- `app/orchestration/runtime_profile.py`
- `app/orchestration/runtime_profile_builder.py`
- `app/orchestration/pattern_selector.py`
- `app/orchestration/strategy_registry.py`
- `app/orchestration/decision_trace.py`

**Responsibilities:** classify complexity/risk/domain/latency/data requirements, assemble `GoalRuntimeProfile`, serialize decisions into `execution_context`, and emit `runtime_profile_selected` SSE events.

### Layer 3: Agentic Pattern Runtime

**Purpose:** Dynamically assemble the right agent pattern stack per goal.

**Existing folders:**
- `app/agent/`
- `app/agent_runtime/`

**Target folder:** `app/agent/patterns/`

**Target files:**
- `app/agent/patterns/base.py`
- `app/agent/patterns/react.py`
- `app/agent/patterns/plan_execute.py`
- `app/agent/patterns/loop_engineering.py`
- `app/agent/patterns/reflection.py`
- `app/agent/patterns/reflexion.py`
- `app/agent/patterns/self_refine.py`
- `app/agent/patterns/self_consistency.py`
- `app/agent/patterns/tree_of_thoughts.py`
- `app/agent/patterns/supervisor.py`
- `app/agent/patterns/debate.py`
- `app/agent/patterns/goal_tree.py`
- `app/agent/patterns/consensus.py`
- `app/agent/patterns/dynamic_graph_assembler.py`

**Responsibilities:** convert `GoalRuntimeProfile.agent_patterns` into LangGraph nodes and routing rules. A new pattern must be addable without editing the entire `graph.py` monolith.

### Layer 4: Agentic RAG Runtime

**Purpose:** Make retrieval explicit, dynamic, agent-owned, and failure-transparent.

**Existing folders:**
- `app/rag/`
- `app/rag_platform/`
- `app/knowledge/`
- `app/knowledge_graph/`
- `app/tools/web_search.py`

**Target folder:** `app/rag/agentic/`

**Target files:**
- `app/rag/agentic/retriever_tool.py`
- `app/rag/agentic/source_inventory.py`
- `app/rag/agentic/query_reformulator.py`
- `app/rag/agentic/query_expander.py`
- `app/rag/agentic/retrieval_policy.py`
- `app/rag/agentic/fallback_chain.py`
- `app/rag/agentic/citation_threader.py`
- `app/rag/agentic/context_gap_detector.py`
- `app/rag/agentic/rag_trace.py`

**Responsibilities:** select source mix, reformulate queries, expand queries, run corrective RAG, trigger `rag_remediate`, return structured context, and never return silent empty strings.

### Layer 5: Ingestion, Chunking, and Document Intelligence

**Purpose:** Convert any content type into tenant-scoped, retrievable, grounded knowledge.

**Existing folders:**
- `app/multimodal/`
- `app/content/`
- `app/perception/`
- `app/knowledge/`
- `app/rag/chunker.py`

**Target folder:** `app/ingestion/`

**Target files:**
- `app/ingestion/orchestrator.py`
- `app/ingestion/content_classifier.py`
- `app/ingestion/parser_registry.py`
- `app/ingestion/chunking_strategy_selector.py`
- `app/ingestion/embedding_policy_selector.py`
- `app/ingestion/modality_pipeline.py`
- `app/ingestion/provenance_builder.py`
- `app/ingestion/quality_checks.py`

**Responsibilities:** detect content type, select parser, select chunker, select embedding, attach provenance, and handle PDF, DOCX, HTML, text, code, image, audio, video, CSV, JSON, API docs, and web pages.

### Layer 6: Embedding and Vector Orchestration

**Purpose:** Select embedding models and indexing strategy dynamically.

**Existing folder:** `app/embedding/`

**Target additions:**
- `app/embedding/orchestrator.py`
- `app/embedding/model_registry.py`
- `app/embedding/dimension_policy.py`
- `app/embedding/reembedding_policy.py`
- `app/embedding/drift_monitor.py`
- `app/embedding/vector_index_policy.py`

**Responsibilities:** choose embedding model by modality, tenant budget, collection size, query type, and dimension compatibility; schedule re-embedding when drift or model changes occur.

### Layer 7: Reranking, Context Budget, and Prompt Building

**Purpose:** Convert retrieved facts, tools, memory, and policies into a safe, compact, model-specific prompt.

**Existing folders:**
- `app/rag_platform/reranker.py`
- `app/agent/prompts.py`
- `app/agent/prompt_compressor.py`
- `app/agent/tokenizer.py`

**Target folder:** `app/context/`

**Target files:**
- `app/context/prompt_builder.py`
- `app/context/context_budget.py`
- `app/context/rerank_policy.py`
- `app/context/citation_manager.py`
- `app/context/prompt_variant_selector.py`
- `app/context/tool_prompt_builder.py`
- `app/context/output_contract_builder.py`

**Responsibilities:** rerank chunks, deduplicate context, enforce token budgets, build model-specific prompts, thread citations, and select prompt variants for A/B tests.

### Layer 8: Model Orchestration

**Purpose:** Select the right model per role at runtime based on quality, cost, latency, risk, and provider health.

**Existing folders:**
- `app/ai_router/`
- `app/agent/model_router.py`
- `app/providers/`

**Target additions:**
- `app/ai_router/model_orchestrator.py`
- `app/ai_router/role_policy.py`
- `app/ai_router/provider_health_policy.py`
- `app/ai_router/cost_latency_quality_policy.py`

**Responsibilities:** choose planner, executor, verifier, judge, embedder, and reranker; fail over providers; enforce tenant budgets; downgrade by complexity and spend.

### Layer 9: Memory, Cache, and Knowledge State

**Purpose:** Make memory and cache use dynamic, safe, tenant-scoped, and cost-aware.

**Existing folders:**
- `app/memory/`
- `app/memory_v2/`
- `app/rag/semantic_cache.py`
- `app/rag/llm_response_cache.py`
- `app/knowledge/`
- `app/knowledge_graph/`

**Target folder:** `app/state_runtime/`

**Target files:**
- `app/state_runtime/memory_policy.py`
- `app/state_runtime/cache_policy.py`
- `app/state_runtime/knowledge_policy.py`
- `app/state_runtime/session_memory.py`
- `app/state_runtime/reflexion_store.py`

**Responsibilities:** decide memory recall strategy, cache only deterministic safe results, avoid error cache poisoning, and store failure lessons as Reflexion memory.

### Layer 10: Eval, Scoring, and Self-Improvement

**Purpose:** Every goal produces learning signals.

**Existing folders:**
- `app/ai_ops/`
- `app/intelligence/`
- `app/observability/`

**Target folder:** `app/evals/`

**Target files:**
- `app/evals/goal_score.py`
- `app/evals/agent_score.py`
- `app/evals/rag_score.py`
- `app/evals/safety_score.py`
- `app/evals/model_score.py`
- `app/evals/runtime_scorecard.py`
- `app/evals/regression_gate.py`

**Responsibilities:** score goal success, grounding, citations, safety, latency, cost, token efficiency, tool success, and user feedback; drive self-improvement policies.

### Layer 11: Optimisation Plane

**Purpose:** Continuously optimise cost, latency, quality, token use, prompts, model choice, and caching.

**Existing folders:**
- `app/intelligence/cost_optimizer.py`
- `app/intelligence/prompt_optimizer.py`
- `app/intelligence/self_optimizer_v2.py`
- `app/rag/semantic_cache.py`

**Target folder:** `app/optimization/`

**Target files:**
- `app/optimization/token_optimizer.py`
- `app/optimization/cost_optimizer.py`
- `app/optimization/latency_optimizer.py`
- `app/optimization/prompt_optimizer.py`
- `app/optimization/model_optimizer.py`
- `app/optimization/cache_optimizer.py`
- `app/optimization/ab_testing.py`

**Responsibilities:** select cheaper models when safe, compress prompts, decide cache use, run A/B tests, and feed eval outcomes back into routing.

### Layer 12: Observability and Runtime Explainability

**Purpose:** Make every dynamic decision visible to users and operators.

**Existing folders:**
- `app/observability/`
- `agent-verse-frontend/src/features/observability/`

**Target additions:**
- `app/observability/runtime_decision_trace.py`
- `app/observability/rag_trace.py`
- `app/observability/pattern_trace.py`
- `app/observability/model_trace.py`

**Required SSE events:**
- `runtime_profile_selected`
- `pattern_assembled`
- `rag_strategy_selected`
- `embedding_strategy_selected`
- `chunking_strategy_selected`
- `model_route_selected`
- `guardrail_profile_selected`
- `eval_score_recorded`
- `self_improvement_suggested`

---

## 3. End-to-End Dynamic Runtime Flow

Before runtime flow, these subsystems are **mandatory first-class dynamic contracts**. They must not be hidden implementation details.

### 3.1 Mandatory Contract: Multimodal and Multi-Model Data Runtime

AgentVerse must support multiple data types and choose the right model and processing strategy dynamically per input, not per hardcoded endpoint.

**Target folder ownership:**
- `app/ingestion/` owns content detection and parser/chunker selection.
- `app/multimodal/` owns modality-specific extraction pipelines.
- `app/ai_router/` owns model selection for modality-specific reasoning.
- `app/embedding/` owns embedding model selection per modality.
- `app/context/` owns multimodal context packaging for planner/executor/verifier.

**Required strategy selectors:**
- `app/ingestion/content_classifier.py` — detects `text | pdf | docx | html | markdown | code | image | audio | video | csv | json | web_page | mixed`.
- `app/ingestion/parser_registry.py` — maps content type to parser implementation.
- `app/ingestion/chunking_strategy_selector.py` — selects chunking strategy.
- `app/embedding/orchestrator.py` — selects embedding model by modality and collection policy.
- `app/ai_router/model_orchestrator.py` — selects LLM/vision/audio/video model by task and modality.

**Dynamic decision matrix:**

| Data Type | Parser | Chunking Strategy | Embedding Strategy | Model Strategy | RAG Strategy |
|---|---|---|---|---|---|
| Plain text | text parser | semantic / sentence | text embedding | text LLM | hybrid RAG |
| PDF | layout parser + OCR | page + section + table-aware | text + optional vision | text/vision LLM | layout-aware RAG |
| DOCX | document parser | heading + paragraph | text embedding | text LLM | hybrid RAG |
| HTML/web | readability parser | DOM/header aware | text embedding | text LLM + web verifier | web-augmented RAG |
| Code repo | AST + file parser | symbol/function/class chunks | code/text embedding | code-capable LLM | code RAG + graph RAG |
| Image | vision extractor | region/object captions | multimodal/image embedding | vision model | visual RAG |
| Audio | STT pipeline | timestamp chunks | transcript embedding | audio/STT + text LLM | transcript RAG |
| Video | scene detector + ASR + frame OCR | scene + timestamp + transcript | multimodal + transcript | vision/audio/text models | video RAG |
| CSV/table | schema/table parser | row group + column summary | table/text embedding | data reasoning model | table RAG + code tool |
| Mixed | composite pipeline | per-modality chunks + cross-links | per-modality embeddings | model per subtask | multimodal RAG |

**Runtime profile output:**

```python
MultimodalRuntimeProfile(
    content_type="pdf|image|audio|video|code|mixed",
    parser="layout_pdf|ocr|asr|vision_caption|ast|html_readability",
    chunking_strategy="semantic|layout|ast|timestamp|scene|table",
    embedding_model="text-embedding-3-small|voyage|multimodal",
    model_roles={"extractor": "vision/audio/text model", "reasoner": "gpt-5.2"},
    provenance_required=True,
)
```

**Acceptance criteria:**
- Same ingestion API accepts all supported content types.
- Chunks include modality, source, page/time/region metadata, and tenant ID.
- RAG can cite a PDF page, video timestamp, audio timestamp, image region, code symbol, or web URL.

---

### 3.2 Mandatory Contract: Self-Improvement Runtime

Self-improvement must be a core runtime loop, not a post-hoc dashboard. Every goal must produce signals that can improve future routing, prompts, retrieval, tools, and models.

**Target folder ownership:**
- `app/evals/` owns scoring.
- `app/optimization/` owns policy changes and A/B experiments.
- `app/state_runtime/reflexion_store.py` owns persistent lessons.
- `app/intelligence/` existing modules are adapters until migrated.

**Required files:**
- `app/evals/runtime_scorecard.py`
- `app/evals/rag_score.py`
- `app/evals/model_score.py`
- `app/evals/safety_score.py`
- `app/optimization/ab_testing.py`
- `app/optimization/prompt_optimizer.py`
- `app/optimization/model_optimizer.py`
- `app/state_runtime/reflexion_store.py`

**Self-improvement loop:**

```text
Goal completes/fails
  ↓
RuntimeScorecard
  ├─ success score
  ├─ grounding score
  ├─ citation score
  ├─ retrieval confidence
  ├─ tool success rate
  ├─ cost/token efficiency
  ├─ latency score
  └─ safety score
  ↓
Improvement decision
  ├─ update prompt variant
  ├─ update model routing policy
  ├─ update RAG strategy policy
  ├─ store Reflexion failure lesson
  ├─ blacklist fragile tool pattern
  └─ create eval regression case
```

**Runtime profile output:**

```python
SelfImprovementProfile(
    enabled=True,
    eval_suite="default|security|rag|coding|ops",
    score_threshold=0.72,
    reflexion_enabled=True,
    prompt_ab_test_enabled=True,
    model_ab_test_enabled=True,
    creates_regression_case_on_failure=True,
)
```

**Acceptance criteria:**
- Any failed goal creates either a failure lesson or an explicit reason why no lesson is safe to store.
- Low RAG score can change future RAG strategy.
- Low model score can change future model route.
- Prompt variants are gated by eval regression checks before promotion.

---

### 3.3 Mandatory Contract: Reranking and Context Quality Runtime

Agentic RAG is incomplete without reranking, filtering, deduplication, citation threading, and context budget enforcement.

**Target folder ownership:**
- `app/context/` owns reranking policy and final context construction.
- `app/rag_platform/reranker.py` remains the reranker implementation adapter.
- `app/rag/agentic/` owns retrieval before reranking.

**Required files:**
- `app/context/rerank_policy.py`
- `app/context/context_budget.py`
- `app/context/citation_manager.py`
- `app/context/prompt_builder.py`

**Reranking strategies:**

| Strategy | Use Case | Cost | Notes |
|---|---|---|---|
| Score-based | Fast default for simple goals | Low | Uses existing retrieval scores |
| RRF fusion | Multi-source retrieval | Low | Combines vector/FTS/web/graph rankings |
| Cross-encoder | High precision search | Medium | Best for technical docs/code |
| LLM reranker | Complex semantic relevance | High | Use only for expert goals |
| Diversity reranker | Avoid duplicate chunks | Low | Maximal marginal relevance |

**Context pipeline:**

```text
raw retrieval results
  ↓
deduplicate chunks
  ↓
rerank by selected strategy
  ↓
filter below relevance threshold
  ↓
enforce source diversity
  ↓
apply token budget
  ↓
thread citations
  ↓
build planner/executor/verifier-specific context
```

**Runtime profile output:**

```python
ContextRuntimeProfile(
    reranker="score|rrf|cross_encoder|llm|diversity",
    max_context_tokens=6000,
    min_relevance_score=0.35,
    max_chunks_per_source=5,
    citation_required=True,
    deduplication_enabled=True,
)
```

**Acceptance criteria:**
- Planner receives strategic context, not raw chunk dumps.
- Executor receives per-step context, not all retrieved context.
- Verifier receives citations and source confidence.
- Unsupported claims are refused or marked low confidence.

---

### 3.4 Mandatory Contract: Guardrails and Governance Runtime

Guardrails and governance must be dynamically selected but never optional for unsafe flows.

**Target folder ownership:**
- `app/security_runtime/` owns dynamic safety profile selection.
- `app/guardrails_v2/` owns scanner implementation.
- `app/governance/` owns audit, policies, HITL, cost, compliance, RBAC.
- `app/auth/` and `app/tenancy/` own identity and tenant boundary.

**Policy dimensions:**

| Dimension | Dynamic Inputs | Output |
|---|---|---|
| Prompt safety | goal text, retrieved context, user role | injection/toxicity policy |
| Tool safety | tool name, tool args, connector risk | allow/deny/HITL/rollback |
| Data safety | PII/PHI/PCI/secrets classification | redact/block/review |
| Agent identity | tenant, delegated identity, sponsor | allowed scopes |
| Compliance | tenant policy, domain, geography | GDPR/HIPAA/SOC2/PCI/DPDP bundle |
| Audit | action risk and data class | immutable audit event |

**Required runtime profile:**

```python
SecurityRuntimeProfile(
    guardrail_bundle="default|strict|regulated|developer|rpa",
    governance_bundle="free|enterprise|regulated",
    identity_scope="tenant|agent|delegated_agent",
    hitl_required=True,
    consensus_required=True,
    rollback_required=True,
    audit_level="standard|full|forensic",
    compliance_tags=["gdpr", "soc2"],
)
```

**Acceptance criteria:**
- No tool call bypasses tool-arg guardrails.
- No final output bypasses final-output guardrails.
- High-risk tool calls always create audit + HITL record.
- Cross-tenant access is denied at app and RLS layers.

---

### 3.5 Mandatory Contract: Knowledge and Knowledge Graph Runtime

Knowledge and graph are not passive stores. They are runtime decision surfaces.

**Target folder ownership:**
- `app/knowledge/` owns document collections and chunks.
- `app/knowledge_graph/` owns entities, relationships, paths, communities.
- `app/rag/agentic/` decides when and how to use them.
- `app/evals/rag_score.py` evaluates retrieval quality.

**Knowledge runtime responsibilities:**
- Build source inventory per tenant and agent.
- Detect empty, stale, sparse, or low-confidence knowledge.
- Route to graph expansion for relationship questions.
- Route to web when knowledge is absent or stale.
- Track citation coverage and retrieval confidence.

**Knowledge graph strategy matrix:**

| Query Type | Use KG? | Strategy |
|---|---|---|
| "related to" | Yes | entity expansion |
| "depends on" | Yes | path traversal |
| "impact of X" | Yes | neighbourhood + community |
| "who caused" | Yes | causal edge traversal |
| simple factual lookup | Maybe | use only if KB confidence low |
| code architecture | Yes | symbol graph + dependencies |

**Runtime profile output:**

```python
KnowledgeRuntimeProfile(
    kb_state="empty|sparse|healthy|stale",
    graph_state="empty|healthy|partial",
    selected_collections=[...],
    graph_strategy="none|entity|path|community|impact",
    web_fallback_required=True,
    citation_required=True,
)
```

**Acceptance criteria:**
- Empty KB triggers explicit `kb_empty` state, not silent failure.
- KG is used for relationship and impact queries.
- Retrieval traces include whether KB, KG, web, memory, or parametric source was used.

---

### 3.6 Mandatory Contract: Implementation Priority

The first build must not start by rewriting `graph.py`. It must establish contracts first.

| Priority | Layer | Why First |
|---|---|---|
| P0 | `app/orchestration/runtime_profile.py` | Everything depends on a unified profile |
| P0 | `app/orchestration/strategy_registry.py` | Prevents if/else sprawl |
| P0 | `app/rag/agentic/retriever_tool.py` | Makes RAG agent-owned |
| P0 | `app/context/context_budget.py` + `rerank_policy.py` | Prevents raw chunk dumping |
| P0 | `app/security_runtime/guardrail_profile.py` | Safety must be universal |
| P1 | `app/ingestion/orchestrator.py` | Enables multimodal platform use |
| P1 | `app/embedding/orchestrator.py` | Enables modality-aware embeddings |
| P1 | `app/evals/runtime_scorecard.py` | Enables self-improvement |
| P1 | `app/optimization/model_optimizer.py` | Enables cost/latency routing |

```text
submit_goal()
  ↓
GoalClassifier
  ↓
SourceInventoryBuilder
  ↓
StrategyRegistry
  ├─ AgentPatternSelector
  ├─ RAGStrategySelector
  ├─ IngestionStrategySelector
  ├─ ChunkingStrategySelector
  ├─ EmbeddingStrategySelector
  ├─ RerankStrategySelector
  ├─ ModelOrchestrator
  ├─ GuardrailPolicySelector
  ├─ MemoryCachePolicySelector
  ├─ EvalProfileSelector
  └─ OptimizationPolicySelector
  ↓
GoalRuntimeProfile
  ↓
DynamicGraphAssembler
  ↓
LangGraph execution
  ├─ rag_prime
  ├─ think? / self_consistency? / debate?
  ├─ plan
  ├─ execute with RetrieverTool + ToolUse + Guardrails
  ├─ verify with citations + consensus?
  ├─ rag_remediate? / reflect? / self_refine?
  └─ complete/fail/waiting_human
  ↓
Eval + Scorecard
  ↓
SelfImprovement + Memory + Cache + Audit
```

---

## 4. Dynamic Strategy Matrix

| Goal Signal | Agent Pattern | RAG | Chunking | Embedding | Model | Guardrail | Eval |
|---|---|---|---|---|---|---|---|
| Simple lookup | ReAct minimal | Hybrid fast | Existing | Existing | `gpt-4o-mini` | Basic | Latency + correctness |
| Complex research | CoT + Reflection + GoalTree | Agentic + Multi-hop + Web | Semantic | High quality | `gpt-5.2` | Grounding required | Citation + quality |
| High-risk operation | Plan+Execute + HITL | Hybrid | Existing | Existing | `gpt-5.2` verifier | Strict + rollback | Safety + audit |
| Coding task | ReAct + Self-Refine | KB + code search | AST/code chunking | Code embedding | `gpt-5.2` planner | Code security | Tests pass + review |
| Empty KB | ReAct + Agentic RAG | Web + parametric baseline | N/A | N/A | `gpt-5.2` | Grounding warning | Transparency |
| PDF-heavy tenant | Plan+Execute | Layout-aware RAG | PDF layout chunks | multimodal/text hybrid | `gpt-5.2` | PII redaction | Citation quality |
| Audio/video | Plan+Execute | Multimodal RAG | timestamp/scene chunks | multimodal | `gpt-5.2` | PII/PHI | Transcript grounding |
| Realtime status | ReAct minimal | Direct tool, no heavy RAG | N/A | N/A | low latency | Basic | Speed |

---

## 5. Acceptance Criteria

AgentVerse is not considered complete until:

- A simple goal gets a minimal low-cost runtime profile.
- A complex research goal gets CoT + agentic RAG + multi-hop + web fallback.
- A high-risk destructive goal always gets HITL + consensus + rollback.
- Empty KB never silently degrades; web/parametric fallback is explicit.
- PDF/code/audio/video choose different ingestion and chunking strategies.
- Embedding model is selected by modality and collection policy.
- Reranking and citation threading reach the verifier.
- Every runtime decision appears in AgentRunTrace and SSE.
- Evals produce scorecards and drive self-improvement suggestions.
- Tests cover dynamic strategy selection for at least 10 goal archetypes.

---

## 6. First Implementation Prompt After This Plan

```markdown
Implement Phase 1 of the AgentVerse Core Dynamic Orchestration plan.

Create `app/orchestration/` with:
- `runtime_profile.py`
- `goal_classifier.py`
- `pattern_selector.py`
- `strategy_registry.py`
- `decision_trace.py`

Do not change graph execution yet.
Add tests that classify 10 goal archetypes and produce the expected `GoalRuntimeProfile`.
The output must be tenant-scoped, serializable into goal `execution_context`, and eventually emitted as a `runtime_profile_selected` SSE event.

Verification:
- `uv run pytest tests/orchestration/ -v --no-cov`
- No production behaviour change except optional profile creation behind feature flag.
```

---

## 7. Final Principle

AgentVerse should not be a collection of hardcoded agent behaviours. It should be a runtime strategy orchestration platform. The goal enters once; the platform dynamically chooses the right patterns, retrieval sources, models, embeddings, chunking, guardrails, memory, cache, and eval policies for that goal, that tenant, that agent, and that moment.

This is the difference between an agent app and an agent operating system.
