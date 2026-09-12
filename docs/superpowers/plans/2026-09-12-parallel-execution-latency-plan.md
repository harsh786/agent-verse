# Deferred: Parallel Execution & Per-Iteration Latency — Implementation Plan

**Date:** 2026-09-12
**Status:** Deferred (not started) — captured so it isn't lost.
**Owner:** TBD
**Related fix already shipped:** `2d9b095c` (bounded plan/execute loop — tool-call budget 12 + `max_iterations` 15→6). That removed the *runaway* (26 min / 34 searches / 11 replans). This plan targets the *remaining* per-iteration latency.

---

## 1. Context & problem

A goal's agent loop is `plan → execute (steps) → verify → replan`. For research-style goals it is slow: each iteration issues several web searches and LLM turns **sequentially**, and each gpt-oss-20b call is ~10–40 s. A "top 5 stocks" goal took minutes even after the loop was bounded.

**Root findings (verified in code + live):**
1. **The parallel machinery already exists.** The executor runs independent plan steps concurrently in dependency *waves* via `asyncio.gather` — `app/agent/nodes/executor_mixin.py` (`execution_waves()` loop, ~line 281; parallel branch ~line 393).
2. **Nothing ever parallelizes**, because plans arrive as **plain strings** (`{"steps": ["Step 1…", "Step 2…"]}`). `StructuredPlan.from_llm_response` treats plain-string steps as **fully sequential** (each depends on the previous) — `executor_mixin.py` ~line 193.
3. **Structured plans** (steps with `depends_on`) *would* parallelize, but `STRUCTURED_PLANNER_SYSTEM` is gated behind `self._enable_goal_tree`, which **also** turns on heavier sub-agent decomposition — `app/agent/nodes/planner_mixin.py:293`.
4. **gpt-oss-20b emits structured plans unreliably** — tested directly against the NVIDIA endpoint; it returned **malformed JSON**. So naively enabling structured planning would frequently fall back to a single lumped step (losing the clean multi-step plan) and could emit wrong dependency graphs.
5. **Within a step, only the first tool call is used.** `executor_mixin.py:1148` reads `resp.tool_calls[0]` and ignores the rest — so even a model that emits parallel tool calls runs them one-per-turn.

**Conclusion:** true search parallelism is gated on either (a) a planner that reliably emits dependency graphs, or (b) executing multiple tool calls per turn concurrently. Neither is safe to force onto the current stable loop without care.

## 2. Goals / non-goals

**Goals**
- Cut wall-clock latency for research goals by running genuinely-independent searches concurrently.
- Keep the loop **stable** — no regression to the runaway behavior; correctness (data-flow ordering) preserved.
- Degrade safely: if the model can't produce a parallelizable plan, fall back to today's sequential behavior with no worse outcome.

**Non-goals**
- Faster single LLM calls (that's a model-choice/infra concern, out of scope here).
- Full multi-agent goal-tree decomposition (separate, heavier feature).

## 3. Options (in recommended order)

### Option A — Reliable structured planning (unlocks the existing wave parallelism)
Make the planner emit `{"steps":[{"id","description","depends_on":[...]}]}` reliably, decoupled from goal-tree decomposition.

- **A1. Decouple** structured planning from `_enable_goal_tree`: introduce `_enable_structured_planning` (default off until A2 lands). Change `planner_mixin.py:292-294` to select `STRUCTURED_PLANNER_SYSTEM` on that flag. Sub-agent decomposition stays gated on `_enable_goal_tree` (`executor_mixin.py:91`), so this does **not** trigger decomposition.
- **A2. Reliability:** structured planning is only worth enabling with a planner model that reliably emits the dependency-graph JSON. Either:
  - point the **planner role** at a stronger instruction model (keep gpt-oss for executor/verifier), or
  - enforce strict `json_schema` for the planner and validate: on parse failure or a dependency cycle, **fall back to the plain sequential plan** (never a lumped single step).
- **A3. Guard:** validate the returned graph — reject/repair cycles, and cap wave width (e.g. ≤ 5 parallel steps) to bound burst load.
- **Effort:** M. **Risk:** low-med (safe fallback). **Payoff:** high when the planner cooperates.

### Option B — Parallel tool calls within a step
Run *all* tool calls from one executor LLM response concurrently instead of just the first.

- **B1.** In `executor_mixin.py` (~1146), when `resp.tool_calls` has >1 entry, dispatch them via `asyncio.gather`, each through the existing single-call path (guardrail arg-check → approval → `mcp_client.call_tool` → output sanitize → `StepResult.tool_calls.append`).
- **B2.** Preserve per-call guardrails/approvals/dedup/rollback — refactor the single-call block into a reusable coroutine and gather over it; do **not** bypass those checks.
- **B3.** Respect the tool-call budget across the batch (don't exceed `_tool_call_budget` mid-batch).
- **B4.** Deterministic result ordering + partial-failure handling (one tool fails → record its error, keep the others).
- **Effort:** M-L. **Risk:** med (touches the executor hot path). **Payoff:** high when the model emits parallel tool calls (OpenAI-style; gpt-oss supports it).

### Option C — Faster planner/verifier model (latency, not parallelism)
Route the planner/verifier roles to a lower-latency model; keep quality where it matters. Complementary to A/B. **Effort:** S (config). **Risk:** low.

## 4. Recommended path
1. **A1** (decouple, flag off) — safe prep, no behavior change.
2. **A2 + A3** with a strict-schema planner and hard sequential fallback — turn the flag on only once a parallelizable goal is verified to parallelize *and* still complete.
3. Then **B** as the deeper win if per-step multi-search is still the bottleneck.

## 5. Testing / acceptance
- **Unit:** `StructuredPlan.from_llm_response` builds correct waves from a dependency graph; cycle → sequential fallback; malformed JSON → plain plan (never single lumped step).
- **Live:** re-run the "top 5 stocks table" goal; assert independent research steps land in one wave (`steps_parallel_start` event fires) and wall-clock drops vs the sequential baseline, with the goal still completing (a real table).
- **Regression:** the bounded-loop guarantees hold — ≤ `max_iterations` plans and ≤ `_tool_call_budget` tool calls; no runaway.
- **Correctness:** a goal whose steps genuinely depend on each other still executes in order (no data-flow break).

## 6. Risks & mitigations
- **Wrong dependency graph → parallel dependent steps → bad output.** Mitigate: schema validation + cycle check; verifier rejection is bounded by `max_iterations=6`; keep wave width capped.
- **Model unreliability (gpt-oss malformed structured JSON).** Mitigate: strict fallback to the plain sequential plan; only enable structured planning with a model proven to emit it.
- **Executor hot-path regression (Option B).** Mitigate: reuse the existing per-call path verbatim inside the gather; add tests around guardrails/approval still firing per call.

## 7. Related deferred items (tracked, not in this plan's scope)
- **Todoist connector** uses a deprecated Todoist API endpoint (HTTP 410) — update the endpoint before offering it.
- **Telegram delivery** end-to-end test is blocked on a `chat_id` (user must message @HarshVABot once); code path is ready.
- **Per-step resolved input** already shipped (`a4d4c2a3`); typed/structured result rendering (charts/grids beyond markdown) remains a possible future enhancement.
