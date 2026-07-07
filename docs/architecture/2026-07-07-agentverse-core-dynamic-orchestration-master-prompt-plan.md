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

**Important clarification:** semantic cache, session memory, long-term memory, knowledge bases, and knowledge graph are not only self-improvement internals. They are first-class **context suppliers** into `app/context/prompt_builder.py`.

The prompt builder must receive structured context from all state sources:

```text
StateRuntimeContext
  ├─ session_memory              # current conversation / current goal state
  ├─ execution_memory            # prior successful plans and failures
  ├─ long_term_memory            # durable tenant/agent learnings
  ├─ semantic_cache              # deterministic prior step/LLM outputs when safe
  ├─ knowledge_base              # tenant documents / chunks
  ├─ knowledge_graph             # entities, edges, paths, communities
  ├─ web_context                 # current external context if selected
  └─ reflexion_lessons           # persistent failure lessons
        ↓
app/context/context_budget.py
        ↓
app/context/rerank_policy.py
        ↓
app/context/citation_manager.py
        ↓
app/context/prompt_builder.py
        ↓
planner / executor / verifier prompts
```

**Prompt-builder contract:**

```python
PromptContextBundle(
    goal_context="...",
    session_memory=[...],
    execution_memory=[...],
    long_term_memory=[...],
    semantic_cache_hits=[...],
    knowledge_chunks=[...],
    graph_facts=[...],
    web_results=[...],
    reflexion_lessons=[...],
    citations=[...],
    source_inventory={...},
    degradation_notes=[...],
)
```

**Semantic cache rule:** semantic cache may provide context only when the cached value is deterministic, non-error, tenant-scoped, policy-safe, and compatible with the current goal/runtime profile. It must never silently override fresher RAG, memory, or tool evidence.

**Long-term memory rule:** long-term memory contributes context and self-improvement signals. It must be classified, scored, and cited before entering planner/executor/verifier prompts.

**Knowledge base / graph rule:** knowledge base chunks and graph facts are primary grounding sources. They feed prompt building, citation verification, provenance, eval scoring, and self-improvement.

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

### 3.6 Mandatory Contract: Capability, Trust, Provenance, Sandbox, and Recovery Runtime

A world-class agent platform needs additional core layers beyond model/RAG/guardrail selection. These are required to make the platform generic, safe, explainable, and dynamically adaptable.

#### Capability Registry

**Purpose:** Normalize what every agent, model, tool, skill, retriever, embedder, parser, chunker, guardrail, and workflow can do.

**Target folder ownership:**
- `app/capabilities/`
- adapters from `app/tools/`, `app/mcp/`, `app/skills_runtime/`, `app/providers/`, `app/embedding/`, `app/rag/`, `app/multimodal/`

**Target files:**
- `app/capabilities/registry.py`
- `app/capabilities/schema.py`
- `app/capabilities/resolver.py`
- `app/capabilities/compatibility.py`
- `app/capabilities/capability_trace.py`

**Runtime contract:**
```python
CapabilityProfile(
    capability_id="tool:postgres.query",
    kind="tool|model|agent|skill|retriever|embedder|parser|chunker|guardrail",
    tenant_scope="platform|tenant|agent",
    input_modalities=["text", "sql"],
    output_modalities=["json", "table"],
    risk_level="low|medium|high|critical",
    cost_class="free|low|medium|high",
    latency_class="realtime|interactive|batch",
    reliability_score=0.0,
    required_permissions=[...],
)
```

**Acceptance criteria:** orchestration never selects a tool/model/skill by name alone; it selects by declared capability and compatibility with goal/runtime profile.

#### Runtime Policy Compiler

**Purpose:** Compile tenant policy, org policy, compliance policy, risk policy, and cost policy into executable runtime constraints before graph execution starts.

**Target folder ownership:**
- `app/policy_runtime/`
- adapters from `app/governance/`, `app/tenancy/`, `app/security_runtime/`

**Target files:**
- `app/policy_runtime/compiler.py`
- `app/policy_runtime/constraint_model.py`
- `app/policy_runtime/policy_trace.py`
- `app/policy_runtime/runtime_enforcer.py`

**Runtime contract:**
```python
CompiledRuntimePolicy(
    allowed_capabilities=[...],
    denied_capabilities=[...],
    required_approvals=[...],
    max_cost_usd=...,
    max_latency_ms=...,
    data_classes_allowed=[...],
    audit_level="standard|full|forensic",
    compliance_constraints=["gdpr", "soc2"],
)
```

**Acceptance criteria:** policy compilation happens before pattern/model/tool/RAG selection, and every downstream selector receives the compiled policy.

#### Tool Reliability and Trust Scoring

**Purpose:** Choose tools dynamically based on historical reliability, latency, safety incidents, tenant success rate, and current circuit-breaker state.

**Target folder ownership:**
- `app/tool_runtime/`
- adapters from `app/tools/`, `app/mcp/`, `app/reliability/`, `app/observability/`

**Target files:**
- `app/tool_runtime/tool_score.py`
- `app/tool_runtime/tool_ranker.py`
- `app/tool_runtime/tool_trust_store.py`
- `app/tool_runtime/tool_policy.py`
- `app/tool_runtime/tool_trace.py`

**Runtime contract:**
```python
ToolTrustProfile(
    tool_name="github_search_issues",
    success_rate=0.98,
    p95_latency_ms=430,
    safety_incidents=0,
    tenant_success_rate=0.94,
    circuit_state="closed|open|half_open",
    trust_score=0.91,
)
```

**Acceptance criteria:** `ToolSelector` ranks tools by semantic relevance plus trust/reliability, not relevance alone.

#### Data Classification Layer

**Purpose:** Classify every input, retrieved chunk, tool output, artifact, memory, and final answer by sensitivity and regulatory class.

**Target folder ownership:**
- `app/data_classification/`
- adapters from `app/guardrails_v2/`, `app/governance/`, `app/ingestion/`, `app/context/`

**Target files:**
- `app/data_classification/classifier.py`
- `app/data_classification/schema.py`
- `app/data_classification/policy.py`
- `app/data_classification/redaction.py`
- `app/data_classification/classification_trace.py`

**Runtime contract:**
```python
DataClassification(
    data_id="chunk:...",
    classes=["public|internal|confidential|secret|pii|phi|pci|source_code"],
    detected_entities=[...],
    retention_policy="default|short|regulated|legal_hold",
    allowed_sinks=["tenant_user", "audit_log"],
    blocked_sinks=["external_tool", "webhook"],
)
```

**Acceptance criteria:** no retrieved chunk or tool output enters prompt/context building until classified or explicitly marked classification-unavailable with safe fallback.

#### Provenance Ledger

**Purpose:** Make every final claim traceable to source documents, tools, models, prompts, retrieval calls, and agent steps.

**Target folder ownership:**
- `app/provenance/`
- adapters from `app/rag/agentic/`, `app/context/`, `app/agent_runtime/`, `app/governance/`

**Target files:**
- `app/provenance/ledger.py`
- `app/provenance/claim_trace.py`
- `app/provenance/source_ref.py`
- `app/provenance/provenance_verifier.py`
- `app/provenance/export.py`

**Runtime contract:**
```python
ProvenanceRecord(
    claim_id="...",
    claim_text="...",
    supporting_sources=[SourceRef(...)],
    generated_by_step="step_3",
    generated_by_model="gpt-5.2",
    prompt_hash="...",
    confidence=0.87,
    verification_status="supported|unsupported|contradicted|unknown",
)
```

**Acceptance criteria:** final answers can be exported with a claim-level provenance chain, not just citations.

#### Sandbox Runtime

**Purpose:** Execute code, shell, browser/RPA, file operations, MCP tools, and destructive simulations in isolated, policy-bound environments.

**Target folder ownership:**
- `app/sandbox_runtime/`
- adapters from `app/tools/`, `app/rpa/`, `app/enterprise/simulation.py`, `app/reliability/rollback.py`

**Target files:**
- `app/sandbox_runtime/profile.py`
- `app/sandbox_runtime/executor.py`
- `app/sandbox_runtime/network_policy.py`
- `app/sandbox_runtime/filesystem_policy.py`
- `app/sandbox_runtime/simulation_runner.py`
- `app/sandbox_runtime/sandbox_trace.py`

**Runtime contract:**
```python
SandboxRuntimeProfile(
    sandbox_type="none|python|browser|shell|mcp|simulation",
    network_policy="none|allowlist|tenant_connectors_only",
    filesystem_policy="read_only|workspace|ephemeral",
    timeout_seconds=...,
    requires_dry_run=True,
    rollback_required=True,
)
```

**Acceptance criteria:** high-risk external effects must support sandbox/dry-run/simulation before production execution or require explicit HITL override.

#### Plan Verification Before Execution

**Purpose:** Verify the generated plan before executing any step.

**Target folder ownership:**
- `app/plan_runtime/`
- adapters from `app/agent/`, `app/security_runtime/`, `app/evals/`

**Target files:**
- `app/plan_runtime/plan_verifier.py`
- `app/plan_runtime/plan_risk_analyzer.py`
- `app/plan_runtime/plan_cost_estimator.py`
- `app/plan_runtime/plan_feasibility.py`
- `app/plan_runtime/plan_trace.py`

**Runtime contract:**
```python
PlanVerificationResult(
    feasible=True,
    risk_level="low|medium|high|critical",
    estimated_cost_usd=...,
    missing_permissions=[...],
    missing_context=[...],
    requires_hitl=True,
    safe_to_execute=True,
)
```

**Acceptance criteria:** no high-risk, high-cost, missing-permission, or missing-context plan executes without verification outcome recorded.

#### Failure Taxonomy and Recovery Policy

**Purpose:** Classify failures and select recovery dynamically.

**Target folder ownership:**
- `app/recovery/`
- adapters from `app/agent/errors.py`, `app/reliability/`, `app/agent/persistence.py`

**Target files:**
- `app/recovery/failure_classifier.py`
- `app/recovery/recovery_policy.py`
- `app/recovery/retry_strategy_selector.py`
- `app/recovery/escalation_policy.py`
- `app/recovery/recovery_trace.py`

**Failure classes:**
- auth/permission failure
- missing credential
- tool unavailable
- provider unavailable
- context gap
- policy rejection
- rate limit
- timeout
- code/test failure
- user ambiguity
- safety violation

**Acceptance criteria:** recovery is never generic `retry`; every retry has a classified reason and selected strategy.

#### Human Collaboration Layer

**Purpose:** Go beyond approve/reject HITL. Let the agent ask clarifying questions, request missing credentials, get preference input, and explain tradeoffs.

**Target folder ownership:**
- `app/collaboration_runtime/`
- adapters from `app/governance/hitl.py`, `app/collab/`, `app/notifications/`

**Target files:**
- `app/collaboration_runtime/clarification.py`
- `app/collaboration_runtime/missing_input_request.py`
- `app/collaboration_runtime/preference_capture.py`
- `app/collaboration_runtime/human_decision_trace.py`

**Acceptance criteria:** ambiguous or blocked goals can pause for human clarification instead of failing or hallucinating.

#### Environment Awareness and Readiness Gate

**Purpose:** Detect whether the runtime environment is capable of safely executing the selected profile.

**Target folder ownership:**
- `app/runtime_readiness/`
- adapters from `app/observability/health.py`, `app/main_services.py`, `app/scaling/`, `app/providers/`

**Target files:**
- `app/runtime_readiness/environment_profile.py`
- `app/runtime_readiness/readiness_gate.py`
- `app/runtime_readiness/dependency_health.py`
- `app/runtime_readiness/degraded_mode_policy.py`

**Acceptance criteria:** the platform blocks or downgrades goals when Redis, Postgres, Celery, provider, embedding, or required tool dependencies are unavailable.

#### Workload Scheduler and QoS Layer

**Purpose:** Prioritize execution by tenant plan, SLA, risk, queue depth, cost, retry count, and enterprise isolation.

**Target folder ownership:**
- `app/qos/`
- adapters from `app/scaling/`, `app/services/goal_queue.py`, `app/governance/cost.py`

**Target files:**
- `app/qos/scheduler.py`
- `app/qos/priority_policy.py`
- `app/qos/queue_policy.py`
- `app/qos/backpressure.py`
- `app/qos/qos_trace.py`

**Acceptance criteria:** enterprise tenants, high-priority goals, and retry storms are handled by explicit queue/backpressure policy.

#### Data Lifecycle and Retention Layer

**Purpose:** Manage retention, deletion, archiving, legal holds, and export for all runtime data.

**Target folder ownership:**
- `app/lifecycle/`
- adapters from `app/memory_v2/`, `app/governance/legal_holds.py`, `app/services/result_artifacts.py`, `app/knowledge/`

**Target files:**
- `app/lifecycle/retention_policy.py`
- `app/lifecycle/deletion_orchestrator.py`
- `app/lifecycle/archive_policy.py`
- `app/lifecycle/legal_hold_policy.py`
- `app/lifecycle/export_policy.py`

**Acceptance criteria:** audit, traces, artifacts, memory, embeddings, knowledge, screenshots, and videos all have tenant-scoped retention policy.

#### Evaluation Dataset Builder

**Purpose:** Convert important or failed goals into reusable golden tasks.

**Target folder ownership:**
- `app/evals/dataset_builder.py`
- adapters from `app/ai_ops/`, `app/intelligence/eval_suite.py`, `app/services/goal_service.py`

**Acceptance criteria:** critical failures automatically create regression candidates with sanitized inputs and expected behaviours.

#### Explainability and Decision UI Layer

**Purpose:** Make dynamic orchestration understandable to users and operators.

**Target folder ownership:**
- Backend: `app/explainability_runtime/`
- Frontend: `agent-verse-frontend/src/features/observability/`, `src/features/goals/`

**Target files:**
- `app/explainability_runtime/decision_explainer.py`
- `app/explainability_runtime/runtime_profile_explainer.py`
- `app/explainability_runtime/source_explainer.py`
- frontend panel: `RuntimeDecisionPanel.tsx`

**Acceptance criteria:** every goal detail page can answer: why this model, why this RAG strategy, why this tool, why this guardrail, why this fallback.

---

### 3.7 Mandatory Contract: Complete Pattern Coverage Registry

Every pattern documented in the architecture markdown files must be represented as a selectable strategy in the platform registry, even if the first implementation is a no-op, advisory, or planned adapter. This prevents patterns from being trapped in documentation without a runtime contract.

**Source documents that must be covered:**
- `docs/architecture/2026-07-07-agentverse-agentic-patterns-catalogue.md`
- `docs/architecture/2026-07-07-agentverse-agentic-rag-design.md`
- `docs/architecture/2026-07-07-agentverse-core-patterns-deep-dive.md`
- `docs/architecture/2026-07-07-agentverse-dynamic-pattern-orchestration.md`

**Target folder ownership:**
- `app/orchestration/strategy_registry.py` owns the canonical registry.
- `app/agent/patterns/` owns agent pattern adapters.
- `app/rag/agentic/` owns RAG pattern adapters.
- `app/optimization/` owns optimisation/self-improvement pattern adapters.
- `app/security_runtime/` owns safety/governance pattern adapters.

**Required registry categories:**

```python
StrategyRegistry(
    agent_patterns={
        "react", "plan_execute", "chain_of_thought", "zero_shot_cot",
        "few_shot_cot", "reflection", "reflexion", "self_refine",
        "self_consistency", "tree_of_thoughts", "graph_of_thoughts",
        "least_to_most", "rewoo", "program_of_thought", "codeact",
        "goal_tree", "supervisor", "debate", "mixture_of_agents",
        "consensus", "peer_review", "camel", "babyagi", "autogpt",
        "lats", "llm_compiler",
    },
    rag_patterns={
        "naive_rag", "hybrid_rag", "hyde", "multi_hop_rag",
        "graph_rag", "corrective_rag", "adaptive_rag", "modular_rag",
        "speculative_rag", "agentic_rag", "web_augmented_rag",
        "fusion_rag", "self_rag", "flare", "raptor",
        "agentic_chunking", "colbert_late_interaction",
    },
    safety_patterns={
        "guardrails", "hitl", "consensus_verification", "exfiltration_guard",
        "permission_matrix", "policy_compiler", "sandbox", "plan_verification",
        "data_classification", "provenance_verification",
    },
    memory_patterns={
        "working_memory", "session_memory", "execution_memory",
        "long_term_memory", "semantic_memory", "prospective_memory",
        "reflexion_memory", "knowledge_graph_memory",
    },
    optimisation_patterns={
        "model_routing", "embedding_routing", "token_optimisation",
        "cost_optimisation", "latency_optimisation", "semantic_cache",
        "llm_response_cache", "prompt_ab_testing", "model_ab_testing",
        "prompt_compression", "context_budgeting",
    },
)
```

**Pattern state values:**

Every registry item must declare one of:
- `implemented` — production code exists and is wired.
- `partial` — code exists but is not fully orchestrated.
- `planned` — contract exists but runtime adapter not built.
- `disabled` — not allowed for current tenant/policy/environment.

**Runtime contract:**

```python
StrategyCapability(
    strategy_id="agentic_rag",
    category="rag_patterns",
    state="implemented|partial|planned|disabled",
    adapter_path="app.rag.agentic.retriever_tool:RetrieverTool",
    required_dependencies=["knowledge_store", "embedder"],
    optional_dependencies=["web_search", "kg_store"],
    cost_class="low|medium|high",
    latency_class="realtime|interactive|batch",
    risk_class="low|medium|high",
    compatible_goal_properties={...},
)
```

**Acceptance criteria:**
- Every pattern named in the architecture docs has a registry entry.
- Dynamic orchestration selects from registry entries, not hardcoded booleans only.
- The goal detail page can show selected patterns, unavailable patterns, and why unavailable patterns were skipped.
- Missing implementations are visible as `planned`, not silently absent.

---

### 3.8 Mandatory Contract: Implementation Priority

The first build must not start by rewriting `graph.py`. It must establish contracts first.

| Priority | Layer | Why First |
|---|---|---|
| P0 | `app/orchestration/runtime_profile.py` | Everything depends on a unified profile |
| P0 | `app/orchestration/strategy_registry.py` | Prevents if/else sprawl |
| P0 | `app/capabilities/registry.py` | Select by declared capability, not hardcoded name |
| P0 | `app/policy_runtime/compiler.py` | Policy must constrain every downstream decision |
| P0 | `app/rag/agentic/retriever_tool.py` | Makes RAG agent-owned |
| P0 | `app/context/context_budget.py` + `rerank_policy.py` | Prevents raw chunk dumping |
| P0 | `app/security_runtime/guardrail_profile.py` | Safety must be universal |
| P0 | `app/data_classification/classifier.py` | No unclassified data enters prompts/tools |
| P0 | `app/plan_runtime/plan_verifier.py` | Plans must be checked before execution |
| P0 | `app/runtime_readiness/readiness_gate.py` | Blocks unsafe degraded execution |
| P1 | `app/ingestion/orchestrator.py` | Enables multimodal platform use |
| P1 | `app/embedding/orchestrator.py` | Enables modality-aware embeddings |
| P1 | `app/evals/runtime_scorecard.py` | Enables self-improvement |
| P1 | `app/optimization/model_optimizer.py` | Enables cost/latency routing |
| P1 | `app/tool_runtime/tool_score.py` | Tool choice uses reliability/trust, not only relevance |
| P1 | `app/provenance/ledger.py` | Final answers get claim-level traceability |
| P1 | `app/recovery/failure_classifier.py` | Recovery strategy follows failure type |
| P1 | `app/qos/scheduler.py` | Queueing and backpressure become explicit |

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
- Semantic cache, session memory, long-term memory, knowledge base, and knowledge graph all feed `PromptContextBundle` through `app/context/`, with classification, reranking, token budget, and citations.
- Semantic cache is used only when deterministic, tenant-scoped, non-error, policy-safe, and compatible with the current runtime profile.
- Every pattern from the agentic pattern, agentic RAG, core pattern, and dynamic orchestration docs exists in `StrategyRegistry` with state `implemented`, `partial`, `planned`, or `disabled`.
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
