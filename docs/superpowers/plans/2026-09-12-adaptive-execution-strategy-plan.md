# Adaptive, Model-Aware Execution Strategy — Implementation Plan

**Date:** 2026-09-12
**Status:** Deferred / design ready.
**Supersedes framing of:** `2026-09-12-parallel-execution-latency-plan.md` (options A/B/C are folded in here as *capability-gated strategies*, not a fixed sequence).

## 0. Guiding principle

> **The execution strategy is chosen per-model from that model's capabilities — no single strategy is applied to every model.** A strong model that reliably emits dependency graphs runs a parallel-wave strategy; a weak model runs the safe sequential strategy; a fast cheap model is routed to latency-sensitive roles. Every strategy carries a safe fallback, and the engine *learns* from observed behavior.

This turns A/B/C from "which one do we build" into "which ones does *this* model qualify for, right now."

---

## 1. What exists to build on (verified)

- **`app/ai_router/models.py`** — `ModelEndpoint` already carries `capabilities: list[ModelCapability]`, `supports_tools`, `supports_structured_output`, `supports_vision`, `avg_latency_ms`, `context_window`. `ModelCapability` enum (`STRUCTURED_OUTPUT`, `TOOL_USE`). `ModelRoutePolicy` for routing.
- **`app/agent/model_router.py`** — `ModelRouter.model_for(task_type)` already routes per role (planning/execution/verification).
- **`app/providers/base.py`** — providers expose `supports_structured_output()`, `supports_tool_use()`, `supports_vision()`.
- **`app/agent/nodes/executor_mixin.py`** — dependency-**wave** execution + `asyncio.gather` already implemented (`execution_waves()` ~L281, parallel branch ~L393). Uses only `resp.tool_calls[0]` today (~L1148).
- **`app/agent/structured_plan.py`** — `StructuredPlan.from_llm_response` parses both structured (dependency graph) and plain (sequential) plans.
- **`app/agent/prompts.py`** — both `PLANNER_SYSTEM` (plain) and `STRUCTURED_PLANNER_SYSTEM` (`depends_on`) exist.

So the **mechanisms already exist**; this plan adds the **capability model + resolver + adaptivity** that decides which to use per model.

## 2. Architecture

### 2.1 Extend the capability profile (`ModelEndpoint`)
Add strategy-relevant, per-model fields (static seed values, refined at runtime — §2.4):

| Field | Meaning | Gates |
|---|---|---|
| `structured_planning: bool` | reliably emits `{steps:[{id,description,depends_on}]}` | Strategy **A** |
| `parallel_tool_calls: bool` | emits & benefits from multiple tool calls per turn | Strategy **B** |
| `json_reliability: "high"\|"medium"\|"low"` | how often JSON parses cleanly | fallback aggressiveness |
| `latency_tier: "fast"\|"medium"\|"slow"` | derived from `avg_latency_ms` | Strategy **C** routing |
| `strict_schema_enforced: bool` | endpoint truly enforces `json_schema` | trust structured output |

Existing `supports_*` bools stay authoritative for hard capabilities; the new fields capture *reliability*, which is the real differentiator (e.g. gpt-oss-20b `supports_structured_output=True` at the API but `structured_planning=False` in practice).

### 2.2 The Strategy Resolver (new: `app/agent/execution_strategy.py`)
Pure, testable function: `resolve(profile_by_role) -> ExecutionStrategy`.

```
ExecutionStrategy:
  plan_mode:  STRUCTURED | SEQUENTIAL          # Strategy A
  tool_mode:  PARALLEL   | SINGLE              # Strategy B
  role_models: {planner, executor, verifier}   # Strategy C
  wave_width_cap: int
  fallback: always-safe (SEQUENTIAL + SINGLE)
```

**Decision rules (per role's model profile):**
- `plan_mode = STRUCTURED` iff planner-model `structured_planning` **and** (`strict_schema_enforced` or `json_reliability=="high"`); else `SEQUENTIAL`.
- `tool_mode = PARALLEL` iff executor-model `parallel_tool_calls`; else `SINGLE`.
- `role_models`: for latency-sensitive roles (verifier, classifier), prefer a `latency_tier=="fast"` model when one is registered (Strategy C); keep the strong model where quality matters (planner/executor).
- `wave_width_cap`: smaller for `json_reliability<high` to bound blast radius of a bad graph.

No hard-coded model names in the loop — everything reads the profile.

### 2.3 Wire-in points (all read the resolved strategy)
- **`planner_mixin.py`** (~L292): pick `STRUCTURED_PLANNER_SYSTEM` vs `PLANNER_SYSTEM` from `strategy.plan_mode` (decoupled from `_enable_goal_tree`; sub-agent decomposition stays gated on that separate flag).
- **`executor_mixin.py`** (~L1146): if `strategy.tool_mode==PARALLEL` and `len(resp.tool_calls)>1`, dispatch all via `asyncio.gather` through the **existing** per-call path (guardrail arg-check → approval → `call_tool` → sanitize → `StepResult.tool_calls.append`); else single call as today.
- **`model_router.py`**: `model_for(role)` honors `strategy.role_models` (Strategy C).
- Wave execution already consumes structured plans — no change beyond feeding it a structured plan.

### 2.4 Adaptivity — the "intelligent" part
Static profiles are only a seed. The engine **learns** per (tenant, model):
- After each planner call, record structured-plan **parse success / cycle / fallback** → rolling success rate in Redis (`model:caps:{model}:structured_ok`).
- If a model's structured-plan success drops below a threshold, the resolver **auto-downgrades** it to `SEQUENTIAL` (and back up if it recovers). Same idea for `parallel_tool_calls` (did multi-call turns actually happen and help?).
- `avg_latency_ms` is updated from real calls → `latency_tier` re-derives → Strategy C routing adapts.

This is what makes it "different per model, and self-correcting" rather than a fixed config.

## 3. Strategy matrix (illustrative, not hard-coded)

| Model profile | plan_mode | tool_mode | Strategy C routing |
|---|---|---|---|
| gpt-oss-20b (json med, structured unreliable, no parallel calls, slow) | SEQUENTIAL | SINGLE | route verifier→fast model if available |
| Frontier instruct (structured reliable + parallel calls) | STRUCTURED (waves) | PARALLEL | planner/executor on it, verifier on fast |
| Fast small model (weak reasoning, fast) | SEQUENTIAL | SINGLE | used *as* the fast verifier/classifier |
| Vision model (llama-3.2-11b-vision) | n/a (not a planner) | n/a | OCR/vision role only |

Every cell falls back to `SEQUENTIAL + SINGLE` on any parse/schema/cycle/tool error.

## 4. Phases
1. **P1 — Profiles + resolver (no behavior change):** add fields to `ModelEndpoint`, seed known models, implement `execution_strategy.resolve()` + unit tests. Loop still runs today's path (resolver output ignored).
2. **P2 — Strategy A wired + gated:** planner reads `plan_mode`; enable STRUCTURED only for models flagged `structured_planning`. Safe fallback on malformed/cyclic → sequential. Verify a parallelizable goal actually parallelizes and still completes.
3. **P3 — Strategy B wired:** parallel tool-calls for `parallel_tool_calls` models, reusing the per-call path; budget-aware, partial-failure-safe, deterministic ordering.
4. **P4 — Strategy C:** role→model routing by `latency_tier`.
5. **P5 — Adaptivity:** Redis-backed success-rate tracking + auto up/down-grade of `plan_mode`/`tool_mode` per model.

## 5. Testing / acceptance
- **Resolver unit tests:** each profile → expected strategy; unknown model → safe default; degraded success rate → downgrade.
- **A:** structured plan → correct waves; malformed/cycle → sequential fallback (never a lumped single step); parallelizable goal shows `steps_parallel_start` and lower wall-clock, still completes.
- **B:** multi-tool response runs concurrently with per-call guardrails/approvals still firing; one tool failing doesn't sink the batch; tool-call budget respected across the batch.
- **C:** verifier/classifier calls hit the fast model; quality roles unchanged.
- **Regression:** bounded-loop guarantees hold (`max_iterations`, tool-call budget); no runaway; sequential-model goals behave exactly as today.
- **Adaptivity:** a model forced to malform structured JSON gets auto-downgraded within N goals.

## 6. Risks & mitigations
- **Wrong dependency graph → parallel dependent steps.** Schema + cycle validation, `wave_width_cap`, verifier catch bounded by `max_iterations`.
- **Bad static profile.** Adaptivity self-corrects from observed success rate; conservative defaults (unknown → SEQUENTIAL/SINGLE).
- **Executor hot-path regression (B).** Reuse the existing per-call coroutine verbatim inside `gather`; tests assert guardrails/approval still run per call.
- **Config sprawl.** One resolver, one profile source of truth (`ModelEndpoint`); no model names in loop code.

## 7. Out of scope / related
- Faster *raw* model inference (infra/model choice).
- Full goal-tree multi-agent decomposition (separate flag/feature).
- Todoist deprecated endpoint; Telegram `chat_id` delivery test (tracked separately).
