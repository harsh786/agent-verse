# AgentVerse Dynamic Orchestration — Full Audit Report

**Date:** 2026-07-07  
**Auditor:** Deep cross-reference of all 4 architecture docs vs. Plan Parts 1–5  
**Result:** 22 categories of gaps found. Part 6 plan (attached) fills all of them.

---

## How To Read This Audit

- ✅ = Covered in Parts 1–5 with full TDD tasks
- ⚠️ = Partially covered (exists but incomplete/stub)
- ❌ = Missing entirely from the plan — must be in Part 6

---

## GAP CATEGORY 1 — Layer 3 (Agentic Pattern Adapters): ENTIRE LAYER MISSING

**Spec says** (master prompt §3 Layer 3):
```
app/agent/patterns/
  base.py, react.py, plan_execute.py, loop_engineering.py,
  reflection.py, reflexion.py, self_refine.py, self_consistency.py,
  tree_of_thoughts.py, supervisor.py, debate.py, goal_tree.py,
  consensus.py, dynamic_graph_assembler.py
```

**Plan has:** Nothing. Zero pattern adapter files planned.

**Impact:** Without pattern adapters, the DynamicGraphAssembler (spec §3.8) cannot exist. The entire "no hardcoded workflows" goal fails.

**Status:** ❌ ENTIRE DIRECTORY MISSING

---

## GAP CATEGORY 2 — Doc 4 Core Files: ALL 4 MISSING

**Doc 4** (`2026-07-07-agentverse-dynamic-pattern-orchestration.md`) defines 4 new files that MUST be created in `app/agent/`:

| File | What it contains | Plan status |
|------|-----------------|-------------|
| `app/agent/pattern_config.py` | `GoalProperties` + `PatternConfig` dataclasses (exact schema from doc 4) | ❌ MISSING |
| `app/agent/goal_classifier.py` | Two-tier `GoalClassifier` using doc 4's exact keyword sets | ❌ MISSING (plan has `app/orchestration/goal_classifier.py` — DIFFERENT!) |
| `app/agent/pattern_assembler.py` | `PatternAssembler` with CRITICAL/HIGH/MEDIUM/LOW priority rules | ❌ MISSING |
| `app/agent/dynamic_graph.py` | `DynamicGraphAssembler` with full LangGraph node wiring | ❌ MISSING |

**Critical conflict:** The plan's `app/orchestration/goal_classifier.py` is a simplified version that doesn't use doc 4's exact `GoalProperties` dataclass fields (`knowledge_requirement`, `time_sensitivity` as string not enum). Doc 4's `PatternConfig` is distinct from `GoalRuntimeProfile`. Both must exist.

**Doc 4 exact `GoalProperties`:**
```python
@dataclass
class GoalProperties:
    complexity: Complexity = Complexity.MEDIUM
    domain: Domain = Domain.TECHNICAL
    risk: RiskLevel = RiskLevel.LOW
    time_sensitivity: str = "normal"          # realtime | normal | batch
    knowledge_requirement: str = "kb_only"    # none | kb_only | web_required | expert_domain
    reversibility: str = "reversible"
    multi_step: bool = True
    is_generative: bool = False
    requires_web: bool = False
    estimated_steps: int = 3
    confidence: float = 0.8
```

**Doc 4 exact `PatternConfig`:**
```python
@dataclass
class PatternConfig:
    reasoning_patterns: list[str] = ["reflection"]
    rag_patterns: list[str] = ["hybrid_rag"]
    multi_agent_patterns: list[str] = ["single_agent"]
    safety_patterns: list[str] = ["guardrails"]
    model_planner: str = "gpt-5.2"
    model_executor: str = "gpt-5.2"
    model_verifier: str = "gpt-5.2"
    model_classifier: str = "gpt-4o-mini"
    max_iterations: int = 15
    max_refine_iterations: int = 2
    persistence_mode: bool = False
    max_persistence_attempts: int = 3
    autonomy_mode: str = "bounded-autonomous"
    web_auto_activate: bool = False
    goal_properties: GoalProperties | None = None
    selection_reason: dict[str, str] = {}
    assembly_latency_ms: float = 0.0
```

**Module-level singletons (also missing):**
```python
goal_classifier = GoalClassifier()   # in app/agent/goal_classifier.py
pattern_assembler = PatternAssembler()  # in app/agent/pattern_assembler.py
```

**Status:** ❌ ALL 4 FILES MISSING

---

## GAP CATEGORY 3 — Doc 2 Agentic RAG Files: 7 of 9 MISSING

**Master prompt §3 Layer 4 target files:**

| File | Plan status |
|------|-------------|
| `app/rag/agentic/retriever_tool.py` | ✅ Part 3 Task 13 |
| `app/rag/agentic/source_inventory.py` | ✅ Part 3 Task 13 |
| `app/rag/agentic/query_reformulator.py` | ❌ MISSING |
| `app/rag/agentic/query_expander.py` | ❌ MISSING |
| `app/rag/agentic/retrieval_policy.py` | ❌ MISSING |
| `app/rag/agentic/fallback_chain.py` | ❌ MISSING |
| `app/rag/agentic/citation_threader.py` | ❌ MISSING |
| `app/rag/agentic/context_gap_detector.py` | ❌ MISSING |
| `app/rag/agentic/rag_trace.py` | ❌ MISSING |

**Also missing from RetrieverTool (doc 2 §Phase B):**
- `[SEARCH:kb:"..."]` directive parsing in `_node_execute`
- Query reformulation on empty results (2 alternate phrasings)
- Confidence-gated fallback chain (exact order: HYBRID→GRAPH→HYDE→WEB→LTM→parametric)
- Context gap signal detection (12 signal phrases)

**Also missing from graph.py (doc 2 §4.2):**
- `_node_rag_prime` (replaces `_node_rag_retrieval`)
- `_node_rag_remediate` (new node triggered on context gap)
- Updated `_route()` to detect context-gap signals

**Status:** ❌ 7 FILES MISSING + 2 NEW GRAPH NODES

---

## GAP CATEGORY 4 — Layer 8 Model Orchestration Additions: ALL MISSING

**Spec §3 Layer 8 target additions:**

| File | Plan status |
|------|-------------|
| `app/ai_router/model_orchestrator.py` | ❌ MISSING |
| `app/ai_router/role_policy.py` | ❌ MISSING |
| `app/ai_router/provider_health_policy.py` | ❌ MISSING |
| `app/ai_router/cost_latency_quality_policy.py` | ❌ MISSING |

**Status:** ❌ ALL 4 FILES MISSING

---

## GAP CATEGORY 5 — Sandbox Runtime: ENTIRE LAYER MISSING

**Spec §3.6 Sandbox Runtime target files:**

| File | Plan status |
|------|-------------|
| `app/sandbox_runtime/__init__.py` | ❌ MISSING |
| `app/sandbox_runtime/profile.py` | ❌ MISSING |
| `app/sandbox_runtime/executor.py` | ❌ MISSING |
| `app/sandbox_runtime/network_policy.py` | ❌ MISSING |
| `app/sandbox_runtime/filesystem_policy.py` | ❌ MISSING |
| `app/sandbox_runtime/simulation_runner.py` | ❌ MISSING |
| `app/sandbox_runtime/sandbox_trace.py` | ❌ MISSING |

**Required runtime contract:**
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

**Status:** ❌ ENTIRE DIRECTORY MISSING

---

## GAP CATEGORY 6 — Human Collaboration Layer: ENTIRE LAYER MISSING

**Spec §3.6 Human Collaboration Layer target files:**

| File | Plan status |
|------|-------------|
| `app/collaboration_runtime/__init__.py` | ❌ MISSING |
| `app/collaboration_runtime/clarification.py` | ❌ MISSING |
| `app/collaboration_runtime/missing_input_request.py` | ❌ MISSING |
| `app/collaboration_runtime/preference_capture.py` | ❌ MISSING |
| `app/collaboration_runtime/human_decision_trace.py` | ❌ MISSING |

**Status:** ❌ ENTIRE DIRECTORY MISSING

---

## GAP CATEGORY 7 — Explainability Runtime + Frontend: MISSING

**Spec §3.6 Explainability target files:**

| File | Plan status |
|------|-------------|
| `app/explainability_runtime/__init__.py` | ❌ MISSING |
| `app/explainability_runtime/decision_explainer.py` | ❌ MISSING |
| `app/explainability_runtime/runtime_profile_explainer.py` | ❌ MISSING |
| `app/explainability_runtime/source_explainer.py` | ❌ MISSING |
| Frontend: `RuntimeDecisionPanel.tsx` | ❌ MISSING |

**Acceptance criteria:** every goal detail page can answer: why this model, why this RAG strategy, why this tool, why this guardrail, why this fallback.

**Status:** ❌ ALL MISSING

---

## GAP CATEGORY 8 — Data Lifecycle Layer: ENTIRE LAYER MISSING

**Spec §3.6 Data Lifecycle target files:**

| File | Plan status |
|------|-------------|
| `app/lifecycle/__init__.py` | ❌ MISSING |
| `app/lifecycle/retention_policy.py` | ❌ MISSING |
| `app/lifecycle/deletion_orchestrator.py` | ❌ MISSING |
| `app/lifecycle/archive_policy.py` | ❌ MISSING |
| `app/lifecycle/legal_hold_policy.py` | ❌ MISSING |
| `app/lifecycle/export_policy.py` | ❌ MISSING |

**Status:** ❌ ENTIRE DIRECTORY MISSING

---

## GAP CATEGORY 9 — Eval Dataset Builder: MISSING

**Spec §3.6 Evaluation Dataset Builder:**

| File | Plan status |
|------|-------------|
| `app/evals/dataset_builder.py` | ❌ MISSING |

**Acceptance criteria:** critical failures automatically create regression candidates with sanitized inputs and expected behaviours.

**Status:** ❌ MISSING

---

## GAP CATEGORY 10 — Strategy Registry: 15+ Patterns MISSING

Doc 1 defines patterns NOT in the current registry:

| Missing Pattern | Category | Doc 1 Source |
|----------------|----------|--------------|
| `grounding_checker` | safety | Doc 1 §4 Safety Patterns |
| `circuit_breaker` | safety/reliability | Doc 1 §5 Control Flow |
| `budget_control` | optimisation/safety | Doc 1 §5 Control Flow |
| `persistence_strategy` | agent | Doc 1 §5 Control Flow |
| `loop_engineering` | agent | Doc 1 §5 Control Flow + Doc 3 §3 |
| `loop_until` | agent | Doc 3 §3 Loop Engineering |
| `wave_execution` | agent | Doc 3 §3 Loop Engineering |
| `structured_planning` | agent | Doc 1 §3, Doc 3 §2 |
| `workflow_dag` | agent | Doc 1 §7 Orchestration |
| `skill_selector` | agent/orchestration | Doc 1 §7 |
| `intent_router` | agent/orchestration | Doc 1 §7 |
| `meta_agent_planner` | agent/orchestration | Doc 1 §7 |
| `event_driven_agent` | agent | Doc 1 §8 Long-Horizon |
| `voyager` | agent | Doc 1 §8 Long-Horizon |
| `generative_agents` | agent | Doc 1 §8 Long-Horizon |
| `scratchpad` | agent | Doc 1 §2 Reasoning |
| `self_play` | agent | Doc 1 §2 Reasoning |
| `constitutional_ai` | safety | Doc 1 §2 Reasoning |
| `toolformer` | agent | Doc 1 §8 Tool Use |
| `mrkl` | agent | Doc 1 §8 Tool Use |
| `knowledge_graph_memory` | memory | ✅ already there |
| `raft` | rag | Doc 1 §4 RAG |

**Current total: 73. Should be: 90+**

**Status:** ❌ 15+ PATTERNS MISSING from StrategyRegistry

---

## GAP CATEGORY 11 — Context Layer: 3 Files Missing

**Spec §3 Layer 7 target files:**

| File | Plan status |
|------|-------------|
| `app/context/prompt_builder.py` | ✅ Part 3 Task 14 |
| `app/context/context_budget.py` | ✅ Part 3 Task 14 |
| `app/context/rerank_policy.py` | ✅ Part 3 Task 14 |
| `app/context/citation_manager.py` | ✅ Part 3 Task 14 |
| `app/context/prompt_variant_selector.py` | ❌ MISSING |
| `app/context/tool_prompt_builder.py` | ❌ MISSING |
| `app/context/output_contract_builder.py` | ❌ MISSING |

**Status:** ❌ 3 FILES MISSING

---

## GAP CATEGORY 12 — Layer 9 State Runtime: knowledge_policy Missing

**Spec §3 Layer 9 target files:**

| File | Plan status |
|------|-------------|
| `app/state_runtime/memory_policy.py` | ✅ Part 5 Task 22 |
| `app/state_runtime/cache_policy.py` | ✅ Part 5 Task 22 |
| `app/state_runtime/session_memory.py` | ✅ Part 5 Task 22 |
| `app/state_runtime/reflexion_store.py` | ✅ Part 5 Task 22 |
| `app/state_runtime/knowledge_policy.py` | ❌ MISSING |

**Status:** ❌ 1 FILE MISSING

---

## GAP CATEGORY 13 — Observability Additions: 3 Files Missing

**Spec §3 Layer 12 target additions:**

| File | Plan status |
|------|-------------|
| `app/observability/runtime_decision_trace.py` | ✅ Part 5 Task 24 |
| `app/observability/rag_trace.py` | ❌ MISSING |
| `app/observability/pattern_trace.py` | ❌ MISSING |
| `app/observability/model_trace.py` | ❌ MISSING |

**Status:** ❌ 3 FILES MISSING

---

## GAP CATEGORY 14 — Layer 0 Kernel: runtime_profiles.py Missing

**Spec §3 Layer 0 additions:**

| File | Plan status |
|------|-------------|
| `app/core/runtime_flags.py` | ✅ Part 1 Task 1 |
| `app/core/runtime_profiles.py` | ❌ MISSING |

**Status:** ❌ 1 FILE MISSING

---

## GAP CATEGORY 15 — SSE Events: pattern_assembled Missing

**Spec §3 Layer 12 required SSE events:**

| Event | Plan status |
|-------|-------------|
| `runtime_profile_selected` | ✅ Part 5 Task 24 |
| `pattern_assembled` | ❌ MISSING (doc 4 defines exact shape) |
| `rag_strategy_selected` | ✅ Part 5 Task 24 |
| `embedding_strategy_selected` | ✅ Part 5 Task 24 |
| `chunking_strategy_selected` | ❌ MISSING |
| `model_route_selected` | ✅ Part 5 Task 24 |
| `guardrail_profile_selected` | ✅ Part 5 Task 24 |
| `eval_score_recorded` | ✅ Part 5 Task 24 |
| `self_improvement_suggested` | ✅ Part 5 Task 24 |

**Doc 4 exact `pattern_assembled` event shape:**
```json
{
    "type": "pattern_assembled",
    "complexity": "expert",
    "risk": "low",
    "patterns_active": {
        "reasoning": ["cot", "reflection"],
        "rag": ["hybrid_rag", "agentic_rag", "graph_rag"],
        "multi_agent": ["goal_tree"],
        "safety": ["guardrails"]
    },
    "models": {"planner": "gpt-5.2", "executor": "gpt-5.2"},
    "selection_reasons": {"cot": "complexity=expert", ...},
    "assembly_latency_ms": 0.8
}
```

**Status:** ❌ 2 SSE EVENTS MISSING

---

## GAP CATEGORY 16 — RetrieverTool: Incomplete vs. Doc 2 Spec

**Doc 2 §4 requires the following that are absent from Part 3 Task 13:**

1. `allow_reformulation` kwarg used but reformulation logic not implemented
2. `step_context` and `goal_context` kwargs not used
3. Exact fallback chain order: HYBRID→GRAPH→HYDE→WEB→LTM→parametric
4. Query reformulation on empty results (2 alternate phrasings max)
5. Context gap signal list (12 phrases from doc 2 §4)
6. `RetrievalResult.fallback_chain_used` tracking
7. `[SEARCH:type:"query"]` directive parsing for `_node_execute`

**Status:** ⚠️ PARTIAL — exists but misses doc 2 Phase B behavior

---

## GAP CATEGORY 17 — Doc 2 LangGraph Nodes: Not Wired

**Doc 2 requires modifying `app/agent/graph.py`:**

| Node | Status |
|------|--------|
| `_node_rag_prime` (replaces `_node_rag_retrieval`) | ❌ NOT IN PLAN |
| `_node_rag_remediate` (new, triggered on context gap) | ❌ NOT IN PLAN |
| `_node_refine` (self-refine pattern) | ❌ NOT IN PLAN |
| Updated `_route()` to detect context-gap signals | ❌ NOT IN PLAN |

**Status:** ❌ ALL MISSING

---

## GAP CATEGORY 18 — GoalService Full Integration: Incomplete

**What Part 5 Task 23 does:** adds `_build_runtime_profile()` helper
**What's still needed for full doc 4 integration:**
- Wire `PatternConfig` into `AgentGraph` or `DynamicGraphAssembler` constructor
- Emit `pattern_assembled` SSE event when PatternConfig is assembled
- Wire `DynamicGraphAssembler.assemble()` to produce the graph per PatternConfig
- Expose `runtime_profile` in goal response JSON

**Status:** ⚠️ PARTIAL

---

## GAP CATEGORY 19 — Acceptance Criteria Items Not Covered by Tests

**Spec §5 acceptance criteria vs. Part 5 tests:**

| Criterion | Test status |
|-----------|------------|
| Simple goal gets minimal low-cost profile | ✅ E2E test |
| Complex research gets CoT + agentic RAG + multi-hop + web | ✅ E2E test |
| High-risk destructive always gets HITL + consensus + rollback | ✅ E2E test |
| Empty KB never silently degrades; web/parametric fallback explicit | ✅ E2E test |
| PDF/code/audio/video choose different ingestion/chunking | ⚠️ ingestion tests in Part 3 but not E2E |
| Embedding model selected by modality and collection policy | ✅ embedding tests in Part 3 |
| Reranking and citation threading reach the verifier | ⚠️ tested in isolation, not wired to graph |
| Semantic cache, session memory, LTM, KB, KG all feed PromptContextBundle | ❌ integration test missing |
| Semantic cache only for deterministic, non-error, policy-safe results | ✅ state_runtime tests |
| Every pattern from docs in StrategyRegistry with state | ⚠️ partially — 15+ patterns missing |
| Every runtime decision in AgentRunTrace and SSE | ⚠️ SSE emitter exists, not wired to graph |
| Evals produce scorecards and drive self-improvement | ✅ Part 4 tests |
| Tests cover dynamic strategy selection for at least 10 goal archetypes | ✅ E2E tests |

**Status:** ⚠️ 3 CRITERIA PARTIALLY COVERED

---

## GAP CATEGORY 20 — Knowledge Graph Runtime Profile: Missing

**Spec §3.5 requires:**
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

This is separate from `RAGStrategyConfig` — it's the KG-specific runtime profile. Missing from plan.

**Status:** ❌ MISSING

---

## GAP CATEGORY 21 — Multimodal Runtime Profile: Missing

**Spec §3.1 requires:**
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

Not in plan as a standalone profile — `IngestionResult` covers some of this but not the model_roles or provenance_required.

**Status:** ❌ MISSING as a formal runtime contract

---

## GAP CATEGORY 22 — Self-Improvement Runtime Profile: Missing

**Spec §3.2 requires:**
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

This profile should be a separate contract (not just embedded in `EvalConfig`). `EvalConfig` in Part 1 is close but not the same type name. The spec mandates `SelfImprovementProfile`.

**Status:** ⚠️ PARTIALLY COVERED (EvalConfig exists but not named SelfImprovementProfile)

---

## AUDIT SUMMARY TABLE

| # | Gap Category | Severity | Files Missing | In Part 6? |
|---|-------------|----------|---------------|------------|
| 1 | Layer 3 Agent Pattern Adapters | CRITICAL | 14 files | ✅ Will add |
| 2 | Doc 4 Core Files (PatternConfig, GoalClassifier, PatternAssembler, DynamicGraphAssembler) | CRITICAL | 4 files | ✅ Will add |
| 3 | Doc 2 RAG Agentic Files | HIGH | 7 files | ✅ Will add |
| 4 | Layer 8 Model Orchestration additions | HIGH | 4 files | ✅ Will add |
| 5 | Sandbox Runtime | HIGH | 7 files | ✅ Will add |
| 6 | Human Collaboration Layer | HIGH | 4 files | ✅ Will add |
| 7 | Explainability Runtime + Frontend | HIGH | 4+1 files | ✅ Will add |
| 8 | Data Lifecycle Layer | MEDIUM | 5 files | ✅ Will add |
| 9 | Eval Dataset Builder | MEDIUM | 1 file | ✅ Will add |
| 10 | Strategy Registry: 15+ patterns missing | HIGH | registry.py edits | ✅ Will add |
| 11 | Context Layer: 3 files missing | MEDIUM | 3 files | ✅ Will add |
| 12 | State Runtime: knowledge_policy missing | MEDIUM | 1 file | ✅ Will add |
| 13 | Observability: 3 files missing | MEDIUM | 3 files | ✅ Will add |
| 14 | Layer 0: runtime_profiles.py missing | LOW | 1 file | ✅ Will add |
| 15 | SSE Events: 2 missing | MEDIUM | emitter edits | ✅ Will add |
| 16 | RetrieverTool: Phase B behavior incomplete | HIGH | edits to existing | ✅ Will add |
| 17 | Graph nodes: _node_rag_prime/_remediate/_refine | CRITICAL | graph.py edits | ✅ Will add |
| 18 | GoalService full integration | HIGH | service edits | ✅ Will add |
| 19 | 3 acceptance criteria missing E2E coverage | MEDIUM | test additions | ✅ Will add |
| 20 | KnowledgeRuntimeProfile missing | MEDIUM | 1 dataclass | ✅ Will add |
| 21 | MultimodalRuntimeProfile missing | MEDIUM | 1 dataclass | ✅ Will add |
| 22 | SelfImprovementProfile naming | LOW | rename EvalConfig | ✅ Will add |

**Total additional files needed: ~65+**  
**Total test tasks added: ~15+**

---

## CORRECTED FILE INVENTORY (adding to existing plan)

### New Directories + Files (not in Parts 1–5)

```
app/agent/patterns/          (NEW DIRECTORY - 14 files)
  __init__.py
  base.py
  react.py
  plan_execute.py
  loop_engineering.py
  reflection.py
  reflexion.py
  self_refine.py
  self_consistency.py
  tree_of_thoughts.py
  supervisor.py
  debate.py
  goal_tree.py
  consensus.py

app/agent/
  pattern_config.py          (NEW - doc 4 exact GoalProperties + PatternConfig)
  goal_classifier.py         (NEW - doc 4 two-tier classifier with exact constants)
  pattern_assembler.py       (NEW - doc 4 PatternAssembler with CRITICAL rules)
  dynamic_graph.py           (NEW - doc 4 DynamicGraphAssembler)

app/rag/agentic/             (ADDITIONS to existing)
  query_reformulator.py
  query_expander.py
  retrieval_policy.py
  fallback_chain.py
  citation_threader.py
  context_gap_detector.py
  rag_trace.py

app/ai_router/               (ADDITIONS)
  model_orchestrator.py
  role_policy.py
  provider_health_policy.py
  cost_latency_quality_policy.py

app/sandbox_runtime/         (NEW DIRECTORY)
  __init__.py
  profile.py
  executor.py
  network_policy.py
  filesystem_policy.py
  simulation_runner.py
  sandbox_trace.py

app/collaboration_runtime/   (NEW DIRECTORY)
  __init__.py
  clarification.py
  missing_input_request.py
  preference_capture.py
  human_decision_trace.py

app/explainability_runtime/  (NEW DIRECTORY)
  __init__.py
  decision_explainer.py
  runtime_profile_explainer.py
  source_explainer.py

app/lifecycle/               (NEW DIRECTORY)
  __init__.py
  retention_policy.py
  deletion_orchestrator.py
  archive_policy.py
  legal_hold_policy.py
  export_policy.py

app/evals/
  dataset_builder.py         (NEW)

app/context/                 (ADDITIONS)
  prompt_variant_selector.py
  tool_prompt_builder.py
  output_contract_builder.py

app/state_runtime/           (ADDITION)
  knowledge_policy.py

app/observability/           (ADDITIONS)
  rag_trace.py
  pattern_trace.py
  model_trace.py

app/core/                    (ADDITION)
  runtime_profiles.py

Frontend:
  agent-verse-frontend/src/features/observability/RuntimeDecisionPanel.tsx

Modifications to existing files:
  app/orchestration/strategy_registry.py  (add 15+ missing patterns)
  app/orchestration/runtime_profile.py    (add MultimodalRuntimeProfile, KnowledgeRuntimeProfile, SelfImprovementProfile)
  app/observability/runtime_decision_trace.py  (add pattern_assembled, chunking_strategy_selected events)
  app/agent/graph.py                      (_node_rag_prime, _node_rag_remediate, _node_refine, updated _route)
  app/rag/agentic/retriever_tool.py       (Phase B: reformulation, directive parsing, full fallback chain)
  app/services/goal_service.py            (full DynamicGraphAssembler wiring)
```

