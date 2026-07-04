# Phase 2: Token Cost & Million-Scale Latency — Detailed Plan

> **REQUIRED SUB-SKILL:** Use `superpowers:subagent-driven-development` to implement this plan task-by-task in the current session (or `superpowers:executing-plans` across sessions). Each task is one bite-sized TDD cycle: write a failing test (real code) → run and confirm FAIL → implement (real code) → run and confirm PASS → `ruff`+`mypy` → commit.

---

## Goal

Cut token spend and tail latency of the agent loop to world-class levels **without** changing behavior:

1. **Per-goal tool retrieval** — stop injecting every connector tool + full JSON schema into every planner/executor prompt; embed the goal, select top-k tools (`CapabilitySearch`), boost ranking by historical reliability (`ToolReliabilityStore`), and include browser/RPA tools **only when the goal needs them**.
2. **Tiered, compact schema injection** — full JSON schema only for selected tools; one-line signatures for the next tier; names-only beyond. Eliminate the duplicate `discover_all_tools` injection in `graph.py`.
3. **Anthropic prompt caching** — `cache_control: ephemeral` breakpoints on the stable prefix (system prompt + skill pack + tool block), prompts ordered stable-prefix-first.
4. **Honest compression** — replace char-based regex compression with a real tokenizer (`tiktoken`), scoped strictly to RAG-context truncation, never over JSON-format instructions; log `agentverse_prompt_tokens_saved_total`.
5. **Semantic-cache L2 at scale** — replace the O(n) Python scan with pgvector HNSW ANN (or Redis-Stack `FT.SEARCH` when available), per-tenant partitioned, keeping L1 LRU.
6. **SSE resume-from-sequence** — reconnect with `Last-Event-ID` resumes from the `EventStore` sequence without replay storms.
7. **Scale hardening** — pool sizes from env; per-plan Celery worker autoscaling signal derived from `record_queue_depths`.
8. **Load profile** — a k6 + locust suite in `infra/loadtest/` with enforced p95 targets, plus a before/after token-measurement harness on a golden-goal set.

**KPIs (must be measured, not asserted):** ≥60% reduction in mean planner input tokens per iteration on the golden-goal set; L2 semantic-cache lookup p95 < 20 ms at 10k entries/tenant; p95 goal-submission < 300 ms and p95 SSE event latency < 500 ms at 10k concurrent streams.

## Architecture

Keep the existing FastAPI + LangGraph + Celery + Postgres(pgvector) + Redis backbone and the two-phase `create_app()` → `app.state` → `lifespan`-upgrade wiring. New primitives:

- **`ToolSelector`** (`app/agent/tool_selector.py`) — goal-aware tool retrieval + reliability re-ranking + RPA gating + tiered schema rendering. Called from `GoalService._build_tool_context`; the resulting `ToolContext` is what the graph already consumes at `graph.py:558/566`, so the graph's own ad-hoc `discover_all_tools` injection is deleted.
- **`Tokenizer`** (`app/agent/tokenizer.py`) — thin `tiktoken` wrapper with a byte-length fallback; used by `PromptCompressor` and the measurement harness.
- **Semantic-cache L2 backends** (`app/rag/vector_cache_backend.py`) — `PgVectorCacheBackend` (HNSW) and `RedisStackCacheBackend` (`FT.SEARCH`), selected by capability probe; `SemanticCache` delegates L2 to a backend instead of scanning.
- **SSE resume** — `GoalService.subscribe_events` gains a `since_sequence` cursor served from `EventStore`; the SSE endpoint reads `Last-Event-ID` and emits `id:` lines.
- **Autoscale signal** — `record_queue_depths` publishes a per-plan desired-replica gauge (`agentverse_desired_workers{plan=...}`) consumed by HPA/KEDA.

New tables carry tenant RLS + isolation tests, `created_at`/`updated_at`, UUID PKs (per Global Constraints). All new checks fail closed: a retrieval/cache/backend error falls back to the safe (larger but correct) path and logs, never to a silent wrong answer.

## Tech Stack

Python 3.12 · FastAPI · LangGraph · SQLAlchemy 2 async + asyncpg · Alembic · Celery · Redis (+ optional Redis-Stack) · Postgres + pgvector (HNSW) · `tiktoken` · React 19 / Vite / TanStack Query / Vitest / Playwright · k6 + locust for load.

## Global Constraints (inherited)

- Backend: `uv run` everything. `uv run ruff check .` (line-length 100, target py312), `uv run mypy app` (strict), `uv run pytest` (`filterwarnings=error`). Tests mirror `tests/<package>/`; markers `integration` (testcontainers) and `slow` (real LLM). Integration tests need `DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock` and `TESTCONTAINERS_RYUK_DISABLED=true` with `colima start` first.
- Migrations forward-only/additive; next revision after the highest existing (`grep -RhoE "revision = ['\"][0-9a-f]+" app/db/migrations/versions | ...` to confirm; this plan uses `0073`/`0074`, adjust to the true head).
- New services constructed in `create_app()`, bound to `app.state`, DB/Redis-upgraded in `lifespan`.
- Fail-closed: every new perf shortcut degrades to the correct full path on error and logs; never `except Exception: pass` around a correctness signal.
- Frontend: no new component libraries; extend `src/components/ui/` and Tailwind tokens; WCAG 2.2 AA; loading/empty/error states.
- **Standard deliverable:** backend (typed services on `app.state`, OTel spans, Prometheus metrics) + UI slice + tests (unit ≥80% new-code coverage, integration for DB/Redis, Playwright e2e + axe) + load profile for hot-path endpoints.
- Assumes **Phase 0** is merged (caches wired; `SemanticCache.warm` signature reconciled; `LLMResponseCache` injected; `ModelRouter.with_override`; cross-model verifier in both paths). Where a Phase 2 task touches a Phase 0 seam, it is noted.

---

## File Structure

**Create**
| Path | Responsibility |
|---|---|
| `app/agent/tool_selector.py` | `ToolSelector`: goal-aware top-k tool retrieval, reliability re-ranking, RPA gating, tiered schema rendering. |
| `app/agent/tokenizer.py` | `Tokenizer`: `tiktoken`-backed `count(text)` / `encode` with byte fallback + singleton. |
| `app/rag/vector_cache_backend.py` | `PgVectorCacheBackend`, `RedisStackCacheBackend`, `select_cache_backend()` capability probe, `CacheBackend` protocol. |
| `app/db/migrations/versions/0073_semantic_cache_entries.py` | `semantic_cache_entries` table (pgvector HNSW), tenant RLS. |
| `app/db/models/semantic_cache.py` | ORM model `SemanticCacheEntry`. |
| `infra/loadtest/goal_submission.js` | k6 script: goal submit p95 < 300 ms. |
| `infra/loadtest/sse_stream.js` | k6 script: SSE event latency p95 < 500 ms @ 10k streams. |
| `infra/loadtest/locustfile.py` | locust equivalent (submit + stream + cost read). |
| `infra/loadtest/README.md` | how to run, thresholds, CI smoke profile. |
| `scripts/measure_token_cost.py` | before/after token/cost harness over a golden-goal set. |
| `tests/agent/test_tool_selector.py` | unit tests for `ToolSelector`. |
| `tests/agent/test_tokenizer.py` | unit tests for `Tokenizer`. |
| `tests/rag/test_vector_cache_backend.py` | unit + integration tests for L2 backends. |
| `tests/services/test_sse_resume.py` | SSE resume-from-sequence tests. |
| `tests/scaling/test_autoscale_signal.py` | autoscale gauge tests. |
| `agent-verse-frontend/src/features/observability/CostBreakdown.tsx` | per-goal token/cost-by-role panel. |
| `agent-verse-frontend/e2e/cost-breakdown.spec.ts` | Playwright e2e + axe assertion. |

**Modify**
| Path | Change |
|---|---|
| `app/agent/tool_context.py` | `to_prompt_block(selection)` renders tiered output; add `ToolSelection` dataclass fields. |
| `app/services/goal_service.py` | `_build_tool_context` delegates to `ToolSelector`; construct/bind selector. |
| `app/agent/graph.py` | delete duplicate `discover_all_tools` injection (632–652); scope compressor to RAG blocks; keep single tiered tool block. |
| `app/agent/prompt_compressor.py` | tokenizer-based, RAG-scoped only; savings metric. |
| `app/providers/anthropic_provider.py` | `cache_control: ephemeral` breakpoints; stable-prefix ordering. |
| `app/providers/base.py` | `CompletionRequest.cache_prefix: str \| None`; `system_blocks` support. |
| `app/rag/semantic_cache.py` | delegate L2 to `CacheBackend`; keep L1. |
| `app/core/pools.py` | pool sizes from `Settings`. |
| `app/core/config.py` | add pool/env knobs (`redis_max_connections`, `http_max_connections`, `db_pool_max`). |
| `app/scaling/tasks.py` | `record_queue_depths` emits per-plan desired-replica gauge. |
| `app/observability/metrics.py` | `record_prompt_tokens_saved`, `record_desired_workers`, `record_cache_lookup_latency`. |
| `app/services/goal_service.py` | `subscribe_events(since_sequence=...)`; `_events_for_replay` cursor. |
| `app/api/goals.py` | SSE endpoint reads `Last-Event-ID`, emits `id:` lines. |
| `app/main.py` | construct `ToolSelector`, `CapabilitySearch`, cache backend; bind to `app.state`; lifespan upgrade. |
| `agent-verse-frontend/src/lib/sse/useGoalStream.ts` | send/track `Last-Event-ID`; resume without dup. |
| `pyproject.toml` | add `tiktoken` dependency (pinned). |

---

## Group A — Per-goal tool retrieval & compact schema (largest single win)

### Task 1: `ToolSelector` — goal-aware top-k retrieval

**Files:**
- Create `app/agent/tool_selector.py`
- Test `tests/agent/test_tool_selector.py`
- Consumes `app/mcp/capability_search.py::CapabilitySearch.search`, `app/agent/tool_context.py::ToolRef`

**Interfaces:**
- Produces
  ```python
  @dataclass
  class ToolSelection:
      selected: list[ToolRef]      # full schema tier
      signature: list[ToolRef]     # one-line tier
      names_only: list[ToolRef]    # name+desc tier
      rpa_included: bool

  class ToolSelector:
      def __init__(self, *, capability_search: CapabilitySearch,
                   reliability: ToolReliabilityStore | None = None,
                   top_k: int = 12, signature_k: int = 20,
                   min_tools_for_retrieval: int = 15) -> None: ...
      async def select(self, *, goal: str, tools: list[ToolRef],
                       tenant_ctx: TenantContext) -> ToolSelection: ...
  ```

- [ ] Failing test: 30 `ToolRef`s, goal "search github issues"; assert `select(...).selected` has ≤ `top_k`, contains the github tool, and that `len(selected)+len(signature)+len(names_only) == 30` (nothing dropped). With only 10 tools total (< `min_tools_for_retrieval`), assert `selected == all 10` (fallback to full).
- [ ] Run `uv run pytest tests/agent/test_tool_selector.py -v` → expect FAIL (module missing).
- [ ] Implement: call `await capability_search.search(goal, tools=[t as dict], tenant_ctx=..., top_k=top_k)`, map `ToolMatch.(server_id,tool_name)` back to `ToolRef`; the next `signature_k` by score go to `signature`; remainder to `names_only`. Below `min_tools_for_retrieval`, put all in `selected`.
- [ ] Run → expect PASS. Then `uv run ruff check . && uv run mypy app`.
- [ ] Commit `feat(perf): add ToolSelector for goal-aware top-k tool retrieval`.

### Task 2: Reliability-boosted ranking

**Files:** Modify `app/agent/tool_selector.py`; test `tests/agent/test_tool_selector.py`. Consumes `app/memory/tool_reliability.py::ToolReliabilityStore.get_reliability`.

**Interfaces:** internal `_boost(match_score, success_rate) -> float`; final rank = `capability_score * (0.5 + 0.5 * success_rate)` (a tool with 0% success rate is halved, not eliminated — still selectable, fail-open on ranking).

- [ ] Failing test: two tools with equal capability score; tool A `get_reliability -> {"success_rate": 0.2, "success_count": 10, ...}`, tool B `0.95`. Assert B ranks above A in `selected`. With `reliability=None`, assert order equals pure capability order (no crash).
- [ ] Run → expect FAIL.
- [ ] Implement: after capability search, for each candidate `await reliability.get_reliability(tenant_id=..., tool_name=ref.name)`, compute boosted score, re-sort. Guard the reliability call in `try/except` that logs and falls back to capability score only (fail-open on a *perf* signal is correct here — reliability is advisory, not a correctness gate).
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): reliability-boosted tool ranking in ToolSelector`.

### Task 3: RPA tools included only when the goal needs them

**Files:** Modify `app/agent/tool_selector.py`; test `tests/agent/test_tool_selector.py`. Consumes `app/rpa/tools.py::RPA_TOOLS`.

**Interfaces:** `_needs_rpa(goal: str, agent_capabilities: set[str]) -> bool`; `select(..., agent_capabilities: set[str] | None = None)`.

- [ ] Failing test: goal "summarize my Jira board" with no browser signal → `select(...).rpa_included is False` and no `server_id=="rpa"` tool in any tier. Goal "navigate to example.com and click login" → `rpa_included is True` and RPA tools present. Agent with capability tag `"browser"` forces inclusion regardless of goal text.
- [ ] Run → FAIL.
- [ ] Implement: keyword+capability heuristic (`navigate|browser|screenshot|click|scrape|fill form|download page|url` OR `"browser" in agent_capabilities`). When false, exclude the always-injected `RPA_TOOLS` from the tool set before ranking.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): gate RPA tools behind goal/agent browser signal`.

### Task 4: Tiered `to_prompt_block` rendering

**Files:** Modify `app/agent/tool_context.py`; test `tests/agent/test_tool_context.py` (extend). Consumes `app/mcp/tool_intelligence.py::SchemaAwarePromptInjector`.

**Interfaces:** `ToolContext.to_prompt_block(selection: ToolSelection | None = None) -> str`. Backward compatible: `selection=None` renders the current full behavior.

- [ ] Failing test: build a `ToolSelection` (2 selected, 3 signature, 5 names_only); assert output contains full `input_schema` JSON only for the 2 selected, a one-line `name(param*, param) — desc` for the 3 signature tools (no JSON), and bare `name: desc` for the 5 names-only. Assert no full JSON appears for signature/names tiers (`assert '"properties"' occurs exactly twice`).
- [ ] Run → FAIL.
- [ ] Implement: selected tier reuses `SchemaAwarePromptInjector.build_tool_schema_block`; signature tier renders `f"- {name}({', '.join(req+opt)}): {desc[:80]}"`; names tier `f"- {name}: {desc[:60]}"`. Sections headed `Available tools (full schema)`, `More tools (signatures)`, `Other tools (names)`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): tiered tool-schema rendering in ToolContext.to_prompt_block`.

### Task 5: Wire `ToolSelector` into `_build_tool_context` + `app.state`

**Files:** Modify `app/services/goal_service.py:878-942`, `app/main.py` (construct + bind), test `tests/services/test_goal_service.py`.

**Interfaces:** `GoalService._build_tool_context(agent_id, tenant_ctx) -> ToolContext` unchanged externally; internally attaches a `ToolSelection` computed for the goal. Because `_build_tool_context` has no goal today, add `goal: str | None = None` param (default None preserves callers) and pass `goal` from the two call sites (`goal_service.py:1366`, `:1656`).

- [ ] Failing test: a fake `CapabilitySearch` + 25 tools; `svc._build_tool_context(agent_id, tenant_ctx, goal="search github")` returns a `ToolContext` whose `to_prompt_block()` shows the github tool in the full tier and does NOT emit 25 full schemas (assert prompt length below a threshold, e.g. `< 4000` chars vs the old ~unbounded).
- [ ] Run → FAIL.
- [ ] Implement: build/reuse `self._tool_selector` (from `app.state.tool_selector`, fallback to a keyword-mode `ToolSelector(capability_search=CapabilitySearch())`), call `select(...)`, store the selection on the `ToolContext` (add field), pass `goal` through. In `main.py` construct `CapabilitySearch(embedder=app.state.embedder)` and `ToolSelector(...)` in `create_app()`, re-wire with the real embedder in `lifespan`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): wire ToolSelector into GoalService._build_tool_context`.

### Task 6: Delete the duplicate `discover_all_tools` injection in the graph

**Files:** Modify `app/agent/graph.py:632-652` (and the `all_tools[:20]` block); test `tests/agent/test_graph_tool_injection.py` (new small module).

**Interfaces:** none changed; the single source of tool context becomes `agent_state.context["tool_prompt"]` / `["tool_context"]` produced by `ToolSelector`.

- [ ] Failing test: run `_node_plan` with a spy `mcp_client` whose `discover_all_tools` records call count; assert it is **not** called during planning (tool context already supplied), and that the planner system prompt still contains the tiered tool block from `tool_prompt`.
- [ ] Run → FAIL (currently `discover_all_tools` is invoked at 636).
- [ ] Implement: remove lines 632–652 (`tool_context_text` via `discover_all_tools`); rely on the injected `tool_prompt`/`SchemaAwarePromptInjector` path at 558–578, now fed the tiered selection. Keep the schema-injection try/except but let it consume `ToolSelection`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `perf(agent): remove duplicate full-tool-list injection in plan node`.

---

## Group B — Provider prompt caching & honest compression

### Task 7: `CompletionRequest` carries a cache prefix

**Files:** Modify `app/providers/base.py`; test `tests/providers/test_base.py`.

**Interfaces:**
```python
@dataclass
class CompletionRequest:
    ...
    cache_prefix: str | None = None   # stable prefix to mark cacheable
```

- [ ] Failing test: `CompletionRequest(messages=[...], model="x", system="sys", cache_prefix="sys")` constructs and round-trips; default is `None`.
- [ ] Run → FAIL.
- [ ] Implement the field.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(providers): add cache_prefix to CompletionRequest`.

### Task 8: Anthropic `cache_control: ephemeral` on the stable prefix

**Files:** Modify `app/providers/anthropic_provider.py:44-95`; test `tests/providers/test_anthropic_provider.py`.

> Consult `claude-api` skill / current Anthropic docs before writing: prompt caching passes `system` as a list of blocks with `{"type":"text","text":..., "cache_control":{"type":"ephemeral"}}` on the last stable block; minimum cacheable prefix length applies.

**Interfaces:** `complete()` builds `system` as a block list when `request.cache_prefix` is set; the stable prefix block gets `cache_control`.

- [ ] Failing test: patch `self._client.messages.create` with an `AsyncMock`; call `complete(CompletionRequest(system="STABLE\n\nVOLATILE", cache_prefix="STABLE", ...))`; assert the `system` kwarg passed to `create` is a list where the block containing `STABLE` has `cache_control == {"type": "ephemeral"}` and the volatile remainder has none. Assert usage/cost recording still runs (existing metrics block).
- [ ] Run → FAIL.
- [ ] Implement: when `cache_prefix` present and `system.startswith(cache_prefix)`, emit `[{"type":"text","text":cache_prefix,"cache_control":{"type":"ephemeral"}}, {"type":"text","text":remainder}]`; else pass `system` as today. Read `usage.cache_read_input_tokens` / `cache_creation_input_tokens` when present and record via new metric labels.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): Anthropic ephemeral prompt caching on stable prefix`.

### Task 9: Order the planner/executor prompt stable-prefix-first + pass `cache_prefix`

**Files:** Modify `app/agent/graph.py` (plan node ~605-652 and execute node prompt assembly); test `tests/agent/test_graph_cache_prefix.py`.

**Interfaces:** system content assembled as `stable = agent_system_prompt + planner_prompt + tool_block`; `volatile = feedback + rejection + cot`; request gets `cache_prefix=stable`.

- [ ] Failing test: run `_node_plan` twice for the same agent/tools with a provider spy capturing the last `CompletionRequest`; assert `cache_prefix` is set and equals the stable segment, and that verification-feedback text is NOT inside `cache_prefix`.
- [ ] Run → FAIL.
- [ ] Implement the ordering + `cache_prefix` on the `CompletionRequest`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `perf(agent): stable-prefix-first prompt ordering with cache_prefix`.

### Task 10: `Tokenizer` (real token counting)

**Files:** Create `app/agent/tokenizer.py`; test `tests/agent/test_tokenizer.py`; add `tiktoken` to `pyproject.toml`.

**Interfaces:**
```python
class Tokenizer:
    def __init__(self, encoding: str = "cl100k_base") -> None: ...
    def count(self, text: str) -> int: ...
    def truncate_to_tokens(self, text: str, max_tokens: int) -> str: ...
_default_tokenizer: Tokenizer
```

- [ ] Add `tiktoken` (pinned exact version) to `pyproject.toml` deps; `uv sync`.
- [ ] Failing test: `Tokenizer().count("hello world")` returns > 0 and `<= len("hello world")`; `truncate_to_tokens(long, 5)` yields text with `count(...) <= 5`. With `tiktoken` import forced to raise (monkeypatch), `count` falls back to `len(text)//4` (byte heuristic) without error.
- [ ] Run → FAIL.
- [ ] Implement with lazy `import tiktoken`, cached encoder, byte-heuristic fallback.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): tiktoken-backed Tokenizer with byte fallback`.

### Task 11: Scope `PromptCompressor` to RAG blocks, real tokens, savings metric

**Files:** Modify `app/agent/prompt_compressor.py`; modify `app/agent/graph.py:688-694`; add metric to `app/observability/metrics.py`; test `tests/agent/test_perf_optimizations.py` (extend existing `PromptCompressor` tests).

**Interfaces:** `PromptCompressor.compress_rag_context(text: str) -> str` (truncates only `[Relevant context]` / `[Knowledge base context]` / `[Visual context]` blocks by token count). `graph.py` calls `compress_rag_context` on the RAG parts **only**, never over the full `system_content`/`user_content` (which contains JSON tool schemas).

- [ ] Failing test: a system prompt containing a JSON tool schema `{"properties": {...}}` and a 5000-token `[Relevant context]` block → after compression the JSON schema is byte-for-byte intact, and the context block is truncated to `<= max_rag_tokens` (measured with `Tokenizer`). Assert `record_prompt_tokens_saved` was called with `saved > 0`.
- [ ] Run → FAIL.
- [ ] Implement: token-based truncation via `Tokenizer`; drop the regex filler-phrase rewriting from the live LLM path (retain as opt-in); emit `agentverse_prompt_tokens_saved_total{stage}`. Update `graph.py` to compress only RAG segments before assembly.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `refactor(perf): token-accurate RAG-scoped compression; stop rewriting JSON prompts`.

---

## Group C — Semantic-cache L2 at scale

### Task 12: `semantic_cache_entries` table + ORM + RLS migration

**Files:** Create `app/db/models/semantic_cache.py`, `app/db/migrations/versions/0073_semantic_cache_entries.py`; test `tests/rag/test_vector_cache_backend.py` (integration-marked RLS test).

**Interfaces:** table `semantic_cache_entries(id uuid pk, tenant_id text, query_hash text, query text, embedding vector(N), response text, created_at, updated_at)` + HNSW index `USING hnsw (embedding vector_cosine_ops)` + tenant RLS `ENABLE`+`FORCE`+policy on `app.tenant_id`.

- [ ] Failing integration test (`integration` marker, testcontainers): insert a row for tenant A under `rls_context("A")`; a read under `rls_context("B")` returns 0 rows; read under `"A"` returns 1.
- [ ] Run `uv run pytest tests/rag/test_vector_cache_backend.py -m integration -v` → FAIL.
- [ ] Implement ORM + migration (dimension parametrized via a settings default, HNSW `m=16, ef_construction=64`), RLS policy mirroring `app/db/rls.py`. Confirm revision `down_revision` = current head.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): semantic_cache_entries pgvector table with tenant RLS`.

### Task 13: `PgVectorCacheBackend` (HNSW ANN)

**Files:** Create `app/rag/vector_cache_backend.py`; test `tests/rag/test_vector_cache_backend.py`.

**Interfaces:**
```python
class CacheBackend(Protocol):
    async def lookup(self, embedding: list[float], tenant_id: str,
                     threshold: float) -> tuple[str, float] | None: ...
    async def store(self, embedding: list[float], query: str,
                    response: str, tenant_id: str, ttl: int) -> None: ...

class PgVectorCacheBackend:
    def __init__(self, session_factory: Any, dim: int) -> None: ...
```

- [ ] Failing integration test: store 3 responses with distinct embeddings for tenant A; `lookup(near_vec, "A", threshold=0.9)` returns the closest response with similarity ≥ 0.9; a far vector returns `None`. Query uses ORDER BY `embedding <=> :q LIMIT 1` and converts cosine distance to similarity (`1 - distance`).
- [ ] Run → FAIL.
- [ ] Implement using parameterized SQL under `sqlalchemy_rls_context`; store with `ON CONFLICT (tenant_id, query_hash) DO UPDATE`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): PgVectorCacheBackend HNSW ANN L2 cache`.

### Task 14: `RedisStackCacheBackend` + capability probe

**Files:** Modify `app/rag/vector_cache_backend.py`; test `tests/rag/test_vector_cache_backend.py`.

**Interfaces:** `RedisStackCacheBackend(redis, dim)` using `FT.CREATE`/`FT.SEARCH` KNN; `async def select_cache_backend(*, redis, session_factory, dim) -> CacheBackend | None` — probes `FT._LIST` (Redis-Stack), else pgvector, else `None` (L1-only).

- [ ] Failing test: with a fake redis that raises on `FT._LIST`, `select_cache_backend` returns a `PgVectorCacheBackend` when a session factory is given, else `None`. With a fake redis exposing `ft(...).search`, it returns `RedisStackCacheBackend`.
- [ ] Run → FAIL.
- [ ] Implement probe + Redis-Stack KNN query (`*=>[KNN 1 @vec $q AS score]`), vector packed as float32 bytes (reuse `_pack_embedding`).
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(perf): RedisStackCacheBackend + backend capability probe`.

### Task 15: `SemanticCache` delegates L2 to the backend

**Files:** Modify `app/rag/semantic_cache.py:236-254,541-554`; test `tests/rag/test_semantic_cache.py` (extend). Add `record_cache_lookup_latency` to metrics.

**Interfaces:** `SemanticCache(..., backend: CacheBackend | None = None)`; `get_similar` checks L1 → `backend.lookup(...)` (ANN) → miss; `store_async` writes L1 + `backend.store(...)`. The legacy `_redis_load_all_entries` O(n) scan remains only as the fallback when `backend is None` and raw `redis` is set (documented as small-scale path).

- [ ] Failing test: inject a spy backend; `get_similar` on L1 miss calls `backend.lookup` exactly once and returns its hit as `_CacheHit(source="l2")`; `store_async` calls `backend.store` once. Assert `record_cache_lookup_latency` observed a value.
- [ ] Run → FAIL.
- [ ] Implement delegation; keep L1 promotion; wire `main.py` to build a backend via `select_cache_backend` in `lifespan` and pass it to `SemanticCache`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `perf(cache): O(log n) ANN L2 for SemanticCache, L1 unchanged`.

---

## Group D — SSE resume-from-sequence

### Task 16: `EventStore.list_events_since(sequence)`

**Files:** Modify `app/services/event_store.py`; test `tests/services/test_event_store.py` (extend). The stored events already carry `sequence` (see `append_event`).

**Interfaces:** `async def list_events_since(self, goal_id: str, after_sequence: int, *, tenant_ctx) -> list[tuple[int, dict]]` returning `(sequence, payload)` ordered by sequence.

- [ ] Failing test: append 5 events; `list_events_since(goal_id, 2, tenant_ctx=...)` returns sequences `[3,4,5]` with payloads.
- [ ] Run → FAIL.
- [ ] Implement `select(GoalEvent).where(sequence > after_sequence).order_by(sequence)` under RLS, returning `(seq, payload)`.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(sse): EventStore.list_events_since for resume cursor`.

### Task 17: `subscribe_events(since_sequence=...)` resume path

**Files:** Modify `app/services/goal_service.py:2128-2185` and `_events_for_replay`; test `tests/services/test_sse_resume.py`.

**Interfaces:** `subscribe_events(self, goal_id, tenant_ctx, *, since_sequence: int | None = None)`. When set, replay starts from `EventStore.list_events_since(since_sequence)` instead of full replay; each yielded event dict includes its `sequence` (for the endpoint to emit as `id:`).

- [ ] Failing test: a goal with persisted events 1..5; `subscribe_events(..., since_sequence=3)` yields only events with sequence > 3 (no duplicates of 1–3), preserving live-queue behavior for events > 5.
- [ ] Run → FAIL.
- [ ] Implement cursor branch; annotate each event with `_seq`. Preserve existing dedupe of in-memory vs persisted.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(sse): resume-from-sequence in GoalService.subscribe_events`.

### Task 18: SSE endpoint reads `Last-Event-ID`, emits `id:` lines

**Files:** Modify `app/api/goals.py:298-337`; test `tests/api/test_goals_sse.py` (extend/create).

**Interfaces:** endpoint parses `request.headers.get("Last-Event-ID")` (int, else `None`), passes `since_sequence`; generator writes `id: {seq}\ndata: {json}\n\n`.

- [ ] Failing test (TestClient streaming): connect with header `Last-Event-ID: 2`; assert the stream begins after sequence 2 and each chunk carries an `id:` line matching the event's sequence.
- [ ] Run → FAIL.
- [ ] Implement header parse (fail-closed to full replay on non-int) + `id:` emission.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(sse): honor Last-Event-ID for gap-free stream resume`.

### Task 19: Frontend `useGoalStream` resume

**Files:** Modify `agent-verse-frontend/src/lib/sse/useGoalStream.ts`; Vitest `agent-verse-frontend/src/lib/sse/useGoalStream.test.ts`.

**Interfaces:** `EventSource` natively replays `Last-Event-ID` from received `id:` lines on reconnect; hook must dedupe by sequence and cap the retained event array (`slice(-N)`), and not retry on 401/403 (aligns with Phase 0C.5).

- [ ] Failing Vitest: mock EventSource emitting events with ids 1,2,3 then a reconnect; assert no duplicate events surface and the hook exposes the last-seen id. Assert a 401 error stops reconnection.
- [ ] Run `npm run test -- useGoalStream` → FAIL.
- [ ] Implement dedupe-by-id + cap + 401 short-circuit.
- [ ] Run → PASS; `npm run lint && npm run typecheck`.
- [ ] Commit `feat(sse): client resume-by-id, dedupe, cap in useGoalStream`.

---

## Group E — Scale hardening & load profile

### Task 20: Pool sizes from env

**Files:** Modify `app/core/config.py`, `app/core/pools.py:22-42`; test `tests/core/test_pools.py`.

**Interfaces:** `Settings` adds `db_pool_max: int = 20`, `db_pool_min: int = 5`, `redis_max_connections: int = 50`, `http_max_connections: int = 100`, `http_keepalive_connections: int = 20`. `_default_pg_factory`/`_default_redis_factory`/`_default_http_factory` read them.

- [ ] Failing test: construct `ConnectionPools(settings=Settings(db_pool_max=40, redis_max_connections=99))` with a stub asyncpg factory capturing kwargs; assert `create_pool` received `max_size=40` and redis `max_connections=99`.
- [ ] Run → FAIL.
- [ ] Implement settings-driven factories (keep current values as defaults).
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(scale): connection pool sizes from Settings/env`.

### Task 21: Per-plan worker autoscaling signal

**Files:** Modify `app/scaling/tasks.py:1210` (`record_queue_depths`), `app/observability/metrics.py`; test `tests/scaling/test_autoscale_signal.py`.

**Interfaces:** `record_desired_workers(plan: str, desired: int)` gauge `agentverse_desired_workers{plan}`. `record_queue_depths` computes `desired = ceil(depth / TASKS_PER_WORKER)` clamped to `[MIN, MAX]` per plan (using `PLAN_QUEUE_MAP`) and records it.

- [ ] Failing test: fake redis reporting `llen("goals.enterprise")==50` with `TASKS_PER_WORKER=10` → `record_desired_workers("enterprise", 5)` called (assert via a metrics spy).
- [ ] Run → FAIL.
- [ ] Implement depth→replica math per plan queue; emit gauge (consumed by KEDA/HPA ScaledObject documented in `infra/loadtest/README.md`).
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(scale): per-plan desired-worker autoscale gauge from queue depth`.

### Task 22: k6 goal-submission profile (p95 < 300 ms)

**Files:** Create `infra/loadtest/goal_submission.js`, `infra/loadtest/README.md`.

**Interfaces:** k6 script POSTing `/goals` with an API key from env, `thresholds: { http_req_duration: ['p(95)<300'] }`, staged ramp to N VUs; a `SMOKE` env toggle lowers VUs for CI.

- [ ] Write the script; run `k6 run -e BASE_URL=... -e API_KEY=... -e SMOKE=1 infra/loadtest/goal_submission.js` against a locally running backend (`uv run uvicorn app.main:app` + `docker-compose up -d postgres redis`); confirm it executes and reports p95. (No unit test — this is an ops artifact; validation is a successful run.)
- [ ] Commit `test(load): k6 goal-submission profile with p95<300ms threshold`.

### Task 23: k6 SSE stream profile (p95 event latency < 500 ms @ scale)

**Files:** Create `infra/loadtest/sse_stream.js`.

**Interfaces:** k6 with `k6/experimental/streams` (or SSE via long-poll GET) opening many concurrent `/goals/{id}/stream` connections; measures inter-event latency; `thresholds: { sse_event_latency: ['p(95)<500'] }` custom Trend; VU target 10k in full profile, 50 in `SMOKE`.

- [ ] Write and run in `SMOKE` mode against local backend; confirm it opens streams and records the custom Trend.
- [ ] Commit `test(load): k6 SSE stream latency profile`.

### Task 24: locust equivalent + CI smoke wiring

**Files:** Create `infra/loadtest/locustfile.py`; update `infra/loadtest/README.md`.

**Interfaces:** `HttpUser` tasks: submit goal, read `/goals/{id}/cost-metrics`, open stream; documented run `locust -f infra/loadtest/locustfile.py --headless -u 50 -r 10 -t 1m`.

- [ ] Write; run headless smoke locally; confirm requests succeed and stats print.
- [ ] Commit `test(load): locust profile mirroring k6 scenarios`.

---

## Group F — Cost measurement & observability

### Task 25: Before/after token-measurement harness on a golden-goal set

**Files:** Create `scripts/measure_token_cost.py`; a small golden set `tests/fixtures/golden_goals.json` (10–15 representative goals across domains); test `tests/scripts/test_measure_token_cost.py` (asserts the harness parses and aggregates).

**Interfaces:** `measure_token_cost.py --mode {baseline|optimized} --out report.json` runs each golden goal through the plan node with a `FakeProvider` that records every `CompletionRequest`, counts input tokens via `Tokenizer.count(system+user)`, and aggregates mean/median/p95 per role (planner/executor/verifier). A `compare` subcommand diffs two reports and prints % reduction.

- [ ] Failing test: feed the harness two canned reports; assert `compare` computes `(baseline-optimized)/baseline` per role and flags KPI (`planner_input_reduction >= 0.60`).
- [ ] Run → FAIL.
- [ ] Implement the runner + aggregator + compare. Document usage in the file docstring.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] **Measurement gate:** run `--mode baseline` on `main~` (pre-Group-A) and `--mode optimized` now; record the % reduction in the commit body. This is the KPI evidence for "≥60% planner input-token reduction."
- [ ] Commit `test(perf): golden-goal token-cost measurement harness + baseline/optimized report`.

### Task 26: Cost-by-role persistence + metrics labels

**Files:** Modify `app/agent/graph.py` (attach `role` to token records), `app/observability/metrics.py` (`record_llm_tokens` add `role` label if not present), `app/governance/cost.py` (persist per-role subtotal on goal completion); test `tests/governance/test_cost.py`.

**Interfaces:** cost breakdown stored as `{planner, executor, verifier}` input/output tokens + usd; exposed on `GET /goals/{id}/cost-metrics` (already exists at `goals.py:213`).

- [ ] Failing test: run a goal through the loop with a counting fake provider; assert `cost-metrics` payload contains non-zero `by_role.planner.input_tokens` and a `cache_read_tokens` field.
- [ ] Run → FAIL.
- [ ] Implement per-role accumulation on `AgentState`/cost controller; surface in the endpoint.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `feat(observability): per-role token/cost breakdown on cost-metrics`.

### Task 27: Frontend cost-breakdown panel + e2e

**Files:** Create `agent-verse-frontend/src/features/observability/CostBreakdown.tsx`, `agent-verse-frontend/e2e/cost-breakdown.spec.ts`; Vitest `CostBreakdown.test.tsx`.

**Interfaces:** component consumes `GET /goals/{id}/cost-metrics` via TanStack Query; renders by-role bars, cache hit-rate, and an 80%-budget alert banner; semantic tokens; loading/empty/error states; WCAG 2.2 AA.

- [ ] Failing Vitest: given a mocked cost-metrics response, renders planner/executor/verifier rows and a "Cache hit rate: X%" line; shows the budget-alert region with `role="alert"` when `spent/budget >= 0.8`.
- [ ] Run `npm run test -- CostBreakdown` → FAIL.
- [ ] Implement component + hook.
- [ ] Playwright e2e `cost-breakdown.spec.ts`: submit a goal, open its detail, assert the cost panel appears; run axe and assert zero violations.
- [ ] Run `npm run test && npm run test:e2e -- cost-breakdown && npm run lint && npm run typecheck` → PASS.
- [ ] Commit `feat(ui): per-goal cost-breakdown panel with cache hit-rate and budget alert`.

---

## Wiring-integrity guard

### Task 28: Assert perf wiring by construction

**Files:** Extend `tests/test_wiring_integrity.py` (created in Phase 0B).

**Interfaces:** none.

- [ ] Failing test: build the app (in-memory path) and assert `app.state` exposes `tool_selector`, `capability_search`, and that the semantic cache has a non-None `backend` when a session factory/redis is configured; assert `_build_tool_context` accepts a `goal` kwarg and returns a tiered selection.
- [ ] Run → FAIL (until all wiring lands).
- [ ] Implement assertions.
- [ ] Run → PASS; `ruff`+`mypy`.
- [ ] Commit `test(wiring): assert Phase 2 perf services are wired on app.state`.

---

## Self-Review

- **Coverage vs brief:** (1) per-goal top-k + reliability boost + RPA gating → Tasks 1–3,5; (2) compact tiered schema → Tasks 4,6; (3) Anthropic prompt caching → Tasks 7–9; (4) real-tokenizer RAG-scoped compression + savings metric → Tasks 10–11; (5) semantic-cache L2 ANN (pgvector + Redis-Stack) → Tasks 12–15; (6) SSE resume-from-sequence → Tasks 16–19; (7) pools-from-env + per-plan autoscale signal → Tasks 20–21; (8) k6/locust load profile with p95 targets → Tasks 22–24; before/after golden-set token measurement → Task 25; cost observability + UI → Tasks 26–27; wiring guard → Task 28. **28 tasks total.**
- **TDD discipline:** every backend task is failing-test → FAIL → implement → PASS → `ruff`+`mypy` → conventional commit; frontend tasks run Vitest + Playwright + axe; load/measurement tasks validate by real runs.
- **Fail-closed vs fail-open judgment:** correctness gates (RLS on the new cache table, SSE resume cursor bounds, `Last-Event-ID` parse) fail closed. Purely advisory *perf* signals (reliability ranking, capability retrieval, cache lookup, compression) fail **open to the larger-but-correct path** — a retrieval error injects the full tool list rather than dropping tools, and a cache-backend error executes the real LLM call. This is deliberate and called out per task.
- **No behavior change:** tiered rendering keeps every tool reachable (nothing dropped); compression touches only RAG blocks; caching is transparent; ANN returns the same hits the O(n) scan would within threshold.
- **Risks / watch items:** (a) Anthropic cache-block shape and minimum-prefix length must be verified against current docs before Task 8 — the `claude-api` skill is the source of truth, not memory. (b) The pgvector dimension must match the configured embedder; Task 12 parametrizes it and Task 15 must guard a dimension mismatch (fall back to L1). (c) Migration `down_revision` must be reconciled with the true head (~0072+ after Phase 0). (d) k6 SSE at 10k VUs needs a load box, not a laptop — CI runs `SMOKE` only; the full profile is a manual/staged run. (e) `_build_tool_context` gains a `goal` param with a `None` default so existing callers (validation-only path) keep working.
- **Verification before done:** the phase is complete only when the Task 25 measurement gate shows ≥60% planner input-token reduction on the golden set AND the `SMOKE` load profiles pass their p95 thresholds locally.
