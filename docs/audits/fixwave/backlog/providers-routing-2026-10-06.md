# Backlog: `providers-routing` (2026-10-06)

Source: `docs/audits/fixwave/pending-all-2026-10-05.json`, the 3 items with `area == "providers-routing"`.
Branch `backlog/bl-core`, not pushed. Each item was checked again against the current code, including
today's Model Registry work (per-capability preference order, `role_preference.py`, `model_dispatch.py`).
None of that work touched these three items.

**Counts:** fixed (were OPEN / PARTIAL) 3 · ALREADY-FIXED 0 · OBSOLETE 0 · NEEDS-OWNER 0.

No alembic revision was added. New Redis keys: `agentverse:provider_cb:<breaker key>` (hash) and
`…:probe`. Both expire on their own.

| Item | Status | Reason | Commit / test |
|---|---|---|---|
| a01-F023-02 | FIXED | Was still true: `ProviderCircuitBreaker` state lived in process dicts, so every replica and worker child learned about a failing model on its own: N processes paid N × threshold failed calls and each ran its own half-open probe. The same circuit (same breaker key, so per-tenant BYOK scoping is kept) is now shared in Redis (`app/providers/shared_circuit.py`): a consecutive-failure count, a wall-clock `opened_at`, and ONE fleet-wide half-open probe (`SET NX EX recovery_timeout`, so a lost probe frees itself). A success anywhere closes the circuit, and a failed probe re-opens it. It fails open: a Redis error never refuses a call, and the per-process breaker keeps protecting each process. `call_with_circuit_breaker` (every `complete_with_failover` / `complete_decision` call) and the executor streaming path use it. It is wired in the API lifespan and in each worker child (`worker_process_init`, one client per task loop). This also closes the duplicate "Services & reliability" item 15 (`_provider_cb` per process). | `4af0b9932` · `tests/providers/test_shared_provider_circuit.py` (5 fail before; 10 total) |
| a01-F024-01 | FIXED | Was still true: only the graph roles and decision calls were wrapped in `TracedProvider`, so embeddings had no `gen_ai` span. `traced_embedder()` now wraps the ONE embedder each process resolves: the API query/ingestion embedder (create_app and the registry rebind), the D-10 per-provider embedders, the worker ingestion services and the re-embed task. `embed_batch` is traced too (and stays absent when the inner provider has none). The span names the embedder's model when the request has none. The `EmbedderResolution` keeps the real embedder, and `embedder_model_name` and the readiness probe look through the wrapper. | `2f9451f14` · `tests/observability/test_embedder_genai_spans.py` (all 6 fail before) |
| a01-F022-02 | FIXED | Was still true: with nothing configured in the registry (e.g. only `ANTHROPIC_API_KEY`, no `DEFAULT_MODEL`), the API path's `ModelOrchestratorAdapter` fell back to `_TIER_MODELS` (OpenAI slugs + `voyage-3-lite`) for every vendor, so an Anthropic, NVIDIA or self-hosted goal got `gpt-4o`. Now: OpenAI keeps the tier table; other vendors get their own provider profile (the one the Celery worker's `ModelRouter` uses, plus env role pins), or `""` (the provider's default model). Plan and budget caps clamp within the vendor; when the vendor has nothing within the cap, the pinned model is kept and `model_cap_unenforceable` is logged. `GoalService` passes the goal provider's vendor. Precedence above the fallback is unchanged (per-agent override > tenant policy pin > saved reasoning order > role map > cheapest configured). | `f1a9db50f` · `tests/ai_router/test_tier_fallback_vendor.py` (15 fail before) |

## Notes / not changed

- `ModelOrchestrator._with_failover` still fails over to other vendors' representative chat slugs
  (`claude-3-5-sonnet`, `gemini-2.5-pro`) when a provider's health circuit is open. That only works where
  the goal's provider can route those vendors. It is outside this item; flagged for a follow-up.
- The worker (queued goals, the production path) routes with `ModelRouter`, which was already
  provider-aware. The adapter fix aligns the in-process API path with it.
- Tests run (focused): `tests/providers`, `tests/ai_router`, circuit / stream / rate-limit agent tests,
  observability, embedding, embedder resolution / registry, ingestion worker, goal-service wiring. All
  green. Not verified on the live stack: Redis behaviour under real multi-replica load (fakeredis only)
  and the spans in Jaeger.
