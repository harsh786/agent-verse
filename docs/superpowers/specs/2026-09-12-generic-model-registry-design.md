# Generic, Cost-Aware Model Registry — Design

**Date:** 2026-09-12
**Status:** Approved (design decisions locked); phased implementation.
**Goal:** Every capability (reasoning, embeddings, vision, OCR/image/multimodal,
reranker) selects its model through **one** generic, cost-aware registry, driven
by *actually configured* models — never a hardcoded/always-the-same model. When
multiple models are configured for the same capability, the **lowest-cost**
qualifying one is used.

## 1. Problem (verified across the codebase)

The platform already contains a complete cost-aware routing framework
(`app/ai_router/`: `ModelEndpoint`, `ModelRegistry`, `AIRouter.select_model` with
`RoutingMode.CHEAPEST`, capabilities `VISION/OCR/EMBEDDING`) — but it is dormant:

- **Fed a static cloud catalog** (`BUILTIN_MODELS`: Anthropic/OpenAI/Gemini/Groq/
  Voyage), not the tenant's configured models.
- **Bypassed on every live path** — `AIRouter.select_model` is only called to *log*
  selections (`goal_service._select_models_for_tenant`), never to drive a call.
- **Each capability resolves exactly one fixed model** via its own resolver:
  - Reasoning: Celery → `agent/model_router.py` env single-model; in-process →
    `ModelOrchestratorAdapter` hardcoded `_TIER_MODELS` (`gpt-*` slugs — a latent
    bug on non-OpenAI providers).
  - Embeddings: one startup embedder (key-precedence if/elif), reused everywhere;
    cost-aware `EmbeddingOrchestrator` exists but is ingestion-only.
  - Vision/OCR/multimodal: `configured_vision_model()` (env) + hardcoded
    `_MULTIMODAL_MODELS`.
  - Reranker: single hosted model; `RERANK` capability/`TaskType.RERANK` absent.

Result: the same model is picked for everything; "cheapest of several configured"
can never happen because no candidate set is ever built at runtime.

## 2. Decisions (locked)

1. **Registry source:** auto-seed from config (env/`Settings`/`LLMConfigStore`) at
   startup, PLUS a per-capability registry UI/API to add more models or override
   cost. Multiple models per capability supported.
2. **Cost tie-break:** unpriced/self-hosted models count as cost `0.0` (prefer own
   infra); ties broken by `quality_score`, then `avg_latency_ms`.
3. **Rollout:** reasoning first (verify live), then embeddings → vision/OCR →
   reranker.

## 3. Design

### 3.1 Registry as source of truth (`app/ai_router/registry.py`)
- Keep `ModelRegistry` + `AIRouter`, but stop relying on the static cloud
  `BUILTIN_MODELS` as the operative set. Add a **`RegistrySeeder`** that, at
  startup, registers a `ModelEndpoint` per *configured* model:
  - Reasoning: the resolved provider model(s) (`NVIDIA_MODEL`/`OPENAI_MODEL`/
    `DEFAULT_*_MODEL`/tenant `LLMConfigStore`) → capabilities `TEXT_GENERATION`
    (+`TOOL_USE`/`STRUCTURED_OUTPUT` from the provider's `supports_*`).
  - Embeddings: `configured_embed_model()` → `EMBEDDING`.
  - Vision: `configured_vision_model()` → `VISION` (+`OCR`).
  - Reranker: `rag_hosted_reranker_model` (when configured) → `RERANK`.
  - Cost: from a small known-price table for cloud slugs; **`0.0` for
    self-hosted/unknown** (per decision 2).
- Per-tenant custom models via existing `add_custom_model` + the model-registry
  REST API (`app/api/model_registry.py`) for UI overrides.

### 3.2 Add missing capability
- `ModelCapability.RERANK` already exists; add **`TaskType.RERANK`**
  (`ai_router/models.py`) so the reranker can route through the same selector.

### 3.3 Cost-aware selection (`AIRouter.select_model`)
- Default `routing_mode` for auto-seeded selection = **`CHEAPEST`**.
- Refine `CHEAPEST`: `min` by `cost_per_1k_input`, tie-broken by higher
  `quality_score`, then lower `avg_latency_ms` (decision 2).
- Keep capability/health/`require_*`/`max_cost` filtering as-is.

### 3.4 Wire-in seams (all identified)
- **Reasoning (P1):** replace the body of `ModelOrchestratorAdapter.model_for`
  and `agent/model_router.py:ModelRouter.model_for` with a call to
  `ai_router.select_model(TaskType.<role>, tenant_id, require_tools=...,
  routing_mode=CHEAPEST)`, falling back to today's env/tier resolution when the
  registry has no candidate (safety).
- **Embeddings (P2):** the `main.py` embedder build + RAG embed path selects via
  `select_model(TaskType.EMBEDDING, CHEAPEST)`.
- **Vision/OCR (P3):** `model_orchestrator.select_for_content_type`,
  `ocr/engine._ocr_model`, `vision_parser` → `select_model(TaskType.OCR /
  require_vision, CHEAPEST)`.
- **Reranker (P4):** `rag/rerank_stage` + `RerankPolicy` consult
  `select_model(TaskType.RERANK, CHEAPEST)` when a hosted reranker is registered.

### 3.5 Safety / non-regression
- The seeder registers the currently-configured single model, so with ONE model
  per capability the selector returns exactly today's model — behavior-preserving.
- Cheapest-wins only changes behavior once a SECOND model is configured for a
  capability.
- Every seam keeps its existing resolver as a fallback when the registry yields
  no candidate (empty registry, startup race), so nothing dead-ends.

## 4. Phases & tests
- **P1 Reasoning:** seeder + RERANK task type + CHEAPEST tie-break + wire the two
  reasoning routers. Tests: seeding from env, single-model → same model, two
  models → cheapest chosen, capability/tool filtering, fallback when empty. Live:
  gpt-oss goal still runs; add a 2nd cheaper reasoning model and observe switch.
- **P2 Embeddings**, **P3 Vision/OCR**, **P4 Reranker:** same pattern, each with
  unit tests + a live check, each behind the safety fallback.

## 5. Out of scope
- Retiring the dormant `providers/model_router.py` / `routing_runtime/*` (dead)
  can be a later cleanup; this design routes through `ai_router` and leaves them
  untouched to limit blast radius.
