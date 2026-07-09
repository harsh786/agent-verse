# AgentVerse Agent Patterns — Complete Reference

This document covers every agent pattern shipped in AgentVerse, explaining what each one does, why it exists, when to use it, exactly how it is implemented, and how to monitor it in production. Read this alongside `app/agent/graph.py`, `app/agent/pattern_assembler.py`, and the individual files under `app/agent/patterns/`.

---

## How Patterns Are Selected

Before executing a single LLM call, AgentVerse automatically selects which patterns to activate. The entry point is `PatternAssembler.assemble()` in `app/agent/pattern_assembler.py:200`.

### GoalProperties → PatternConfig

`PatternAssembler` receives a `GoalProperties` struct (complexity, domain, risk level, reversibility, multi-step, is_generative, requires_web, time_sensitivity) plus the agent's stored config dict. It walks a priority-ordered list of `Rule` objects at `app/agent/pattern_assembler.py:34` — there are currently **19 rules** spanning four priority tiers: CRITICAL, HIGH, MEDIUM, and LOW.

Each rule has a predicate lambda. When the predicate matches it appends patterns to one of four buckets:

| Bucket | Examples |
|--------|----------|
| `reasoning_patterns` | react, chain_of_thought, reflection, self_refine, self_consistency, tree_of_thoughts, peer_review |
| `rag_patterns` | hybrid_rag, agentic_rag, fusion_rag, flare, raptor, corrective_rag |
| `multi_agent_patterns` | single_agent, goal_tree, supervisor |
| `safety_patterns` | hitl, rollback, guardrails, consensus_verification |

**CRITICAL rules are inviolable**: the `force_no_hitl` agent config key is intentionally ignored when risk is CRITICAL or HIGH (`app/agent/pattern_assembler.py:245`). Safety patterns can only be added, never removed.

`react` is always forced to position zero in `reasoning_patterns` (`pattern_assembler.py:247–251`). Every goal therefore always runs ReAct as the base loop.

### DynamicGraphAssembler

`DynamicGraphAssembler.assemble()` in `app/agent/dynamic_graph.py:14` translates the resulting `PatternConfig` into Boolean flags on `AgentGraph`:

```python
graph = AgentGraph(
    enable_cot="chain_of_thought" in reasoning,
    enable_reflection="reflection" in reasoning,
    enable_self_refine="self_refine" in reasoning,
    enable_self_consistency="self_consistency" in reasoning,
    enable_tree_of_thoughts="tree_of_thoughts" in reasoning,
    enable_peer_review="peer_review" in reasoning,
    enable_goal_tree="goal_tree" in multi_agent,
    ...
)
```

The `get_active_nodes()` helper (`dynamic_graph.py:44`) returns the list of graph nodes that will be wired for this specific goal — useful for observability and debugging.

### Default Rule Summary

| Trigger | Added Patterns | Priority |
|---------|---------------|----------|
| `risk=CRITICAL` | hitl, rollback, guardrails, consensus_verification; autonomy=supervised | CRITICAL |
| `risk=HIGH` | hitl, rollback, guardrails; autonomy=supervised | CRITICAL |
| `reversibility=irreversible` | rollback | CRITICAL |
| `complexity=EXPERT` | chain_of_thought, reflection, self_refine, goal_tree; max_iter=50 | HIGH |
| `complexity=COMPLEX` | chain_of_thought, reflection; max_iter=25 | HIGH |
| `expert+multi_step` | goal_tree | HIGH |
| `expert+analytical` | self_consistency, tree_of_thoughts | HIGH |
| `critical/high risk+expert` | peer_review | HIGH |
| `requires_web or realtime` | web_augmented_rag, flare; web_auto_activate=True | MEDIUM |
| `domain=TECHNICAL, not SIMPLE` | reflection | MEDIUM |
| `COMPLEX or EXPERT` | agentic_rag | MEDIUM |
| `generative or CREATIVE` | self_refine | MEDIUM |
| `expert+analytical+multi_step` | supervisor | MEDIUM |
| `domain=ANALYTICAL, not SIMPLE` | fusion_rag | MEDIUM |
| `domain=ANALYTICAL, COMPLEX/EXPERT` | raptor | MEDIUM |
| `analytical+HIGH/CRITICAL risk` | corrective_rag | MEDIUM |
| Always | react, guardrails | LOW |

---

## The Base Graph Topology

```
START → initialize → rag_retrieval → [think?] → [tree_of_thoughts?] → plan
      → execute → [refine?] → verify
      → (complete → END | replan → plan | max_iter → END | waiting_human → END
         | rag_remediate → plan | reflect → plan)
```

The topology is defined in `app/agent/dynamic_graph.py:62` and the concrete node implementations live in `app/agent/graph.py`. Five nodes are always present: `initialize`, `rag_retrieval`, `plan`, `execute`, `verify`. All other nodes are conditionally wired based on flags.

---

## Pattern 1: ReAct (Reasoning + Acting)

**File:** `app/agent/patterns/react.py` | **Node:** `execute`

### What it is

ReAct interleaves reasoning with action: the LLM generates a thought, selects a tool call, observes the result, then reasons again. This think→act→observe loop repeats until the step is done. It is named for the 2022 Yao et al. paper.

### Why it exists

Without an explicit reasoning step, LLMs jump straight to tool calls and miss multi-step logic. ReAct forces the model to narrate its reasoning inline, which significantly improves tool selection accuracy and enables debugging via the reasoning trace.

### When to use / not use

Use: always — this is the default for every goal. It cannot be disabled.  
Do not stack multiple ReAct loops (nesting increases cost with no benefit); use Plan-and-Execute instead when the task has many independent steps.

### Implementation

`_node_execute()` in `app/agent/graph.py` is the runtime. For each step in the plan:

1. Builds a prompt from `EXECUTOR_SYSTEM` + step description + RAG context + tool list.
2. Calls the executor LLM via `call_with_circuit_breaker`.
3. Parses the response with `extract_tool_call()` (`app/agent/tool_calls.py`); falls back to `repair_tool_call_arguments()` on malformed JSON.
4. Dispatches to `MCPClient.call_tool()`.
5. Stores the `StepResult` in `AgentState.steps`.
6. Classifies tool risk via `classify_tool_risk()` (`app/agent/tool_risk.py`); high-risk steps may trigger HITL before execution.

The loop continues until all steps complete or a step produces a permanent error.

### Failure modes

- **Tool parse failure**: `repair_tool_call_arguments()` attempts recovery; if it fails the step is marked `STEP_ERROR` and execution continues to the next step.
- **Tool timeout/network error**: circuit breaker opens; the goal moves to `max_iterations_exceeded` if retries exhaust.
- **Stagnation**: if verification feedback is identical for 3 consecutive iterations, the graph sets `terminal_reason=stagnation` and moves to `END`.

### Monitoring

- `agent.tool_call.duration` — per-tool latency histogram.
- `agent.step.status` — counts of COMPLETE / TOOL_FAILED / STEP_ERROR.
- `agent.stagnation` — fires when stagnation is detected.

---

## Pattern 2: Plan-and-Execute

**File:** `app/agent/patterns/plan_execute.py` | **Node:** `plan`

### What it is

The agent first produces a full structured plan (a list of `StructuredStep` objects), then executes steps sequentially or in parallel waves. Planning and execution are separated — the planner LLM never sees real tool results.

### Why it exists

For multi-step tasks, ReAct can get lost mid-execution. Upfront planning creates a verifiable contract: you can inspect the plan before running it, reject it in HITL, and debug failures step-by-step.

### Implementation

`_node_plan()` in `graph.py` calls the planner LLM with `PLANNER_SYSTEM` or `STRUCTURED_PLANNER_SYSTEM` (when goal-tree is enabled). The response is parsed into a `StructuredPlan` containing `StructuredStep` items, each with `description`, `tool_hint`, `depends_on`, and optionally a `loop_until` condition.

Steps with no `depends_on` entries in the same wave can be dispatched in parallel. The execution node groups steps by dependency and runs independent waves with `asyncio.gather`.

### Failure modes

- Empty plan returned by the LLM → the graph replans with a clarifying prompt.
- All steps in a plan fail → verification marks the plan as failed → replan loop.
- Plan contains a cycle in dependencies → detected and flattened to sequential.

### Monitoring

- `agent.plan.duration` — time from plan node entry to exit.
- `agent.plan.step_count` — plan size histogram.
- `agent.plan.parallel_waves` — how many parallel execution waves ran.

---

## Pattern 3: Reflection

**File:** `app/agent/patterns/reflection.py` | **Node:** `_node_reflect`

### What it is

After a verification failure, Reflection runs a separate LLM call using `REFLECTION_SYSTEM` to diagnose *why* the plan failed. The diagnosis is injected as a context block into the next plan iteration.

### Why it exists

The standard replan loop simply retries with "try harder." Reflection gives the planner a structured failure diagnosis — pinpointing which step failed, what output was wrong, and what to try differently.

### When to activate

Enabled by:
- `complexity=EXPERT` (PatternAssembler HIGH rule)
- `domain=TECHNICAL, not SIMPLE` (PatternAssembler MEDIUM rule)
- `agent_config.enable_reflection=True`

### Implementation

`_node_reflect()` receives the current `AgentState` (goal, failed steps, verification feedback). It calls the planner LLM with `REFLECTION_SYSTEM` — a prompt that asks for a structured diagnosis. The diagnosis string is stored in `agent_state.context["reflection_diagnosis"]`. On the next `_node_plan()` call, the diagnosis is prepended to the planning prompt as:

```
[Reflection from previous failure:]
<diagnosis>
```

### Failure modes

- If the reflection LLM call fails, the node returns an empty diagnosis and the graph replans without it (graceful degradation).
- If reflection produces an inaccurate diagnosis the next plan may go in the wrong direction — monitor `agent.replan.count` for divergence.

### Monitoring

- `agent.reflect.invoked` — counter.
- `agent.reflect.diagnosis_length` — empty diagnosis signals LLM failure.

---

## Pattern 4: Reflexion

**File:** `app/agent/patterns/reflexion.py`

### What it is

Reflexion extends Reflection with *persistent episodic memory*: failure lessons are stored across goals, not just within a single goal run. When the agent plans for a new goal, it first recalls relevant past lessons and injects them into the planning prompt.

### Why it exists

Reflection is stateless — it forgets every lesson when the goal ends. Reflexion builds a long-term "mistake library" so the agent improves over time across sessions.

### Implementation

`ReflexionPattern` wraps `ReflexionStore` (backed by Postgres via `app/state_runtime/reflexion_store.py`). The two key operations:

1. **Store**: `maybe_store_async()` is called in the failure branch of `_node_verify`. It calls `ReflexionPattern.store_lesson()` which persists:
   ```
   "For goal '<first 60 chars>': <feedback first 200 chars>"
   ```
   tagged with `failure_class` (e.g., `tool_error`, `plan_error`, `timeout`).

2. **Recall**: At planning time, `initial_context["_reflexion_lessons"]` is loaded from `ReflexionStore.recall(tenant_id=..., limit=5)`. The planner receives a block formatted by `format_for_context()`:
   ```
   [Reflexion lessons from past failures — avoid these mistakes:]
     1. ... (class: tool_error)
     2. ...
   ```

Both sync (`record`) and async (`record_async`) paths are supported; the async path is used when a DB session factory is available.

### When to activate

Reflexion runs automatically alongside Reflection when `enable_reflection=True`. There is no separate flag.

### Failure modes

- Postgres unavailable → falls back to in-memory `ReflexionStore` (lessons lost on restart).
- Noisy lessons (bad feedback text) → `format_for_context()` limits to 5 lessons, each capped at 200 chars.

---

## Pattern 5: Self-Refine

**File:** `app/agent/patterns/self_refine.py` | **Node:** `_node_refine`

### What it is

After `execute` but before `verify`, the agent asks the LLM to critique its own output and produce a better version — up to `max_refine_iterations` times (default: 2, configurable in `PatternConfig`). If the output is already good, the LLM responds with the sentinel `NO_CHANGES_NEEDED` to stop early.

### Why it exists

ReAct optimizes for completing steps; Self-Refine optimizes the *quality* of each step's output. It is most effective for writing, analysis, and any task where subjective quality matters.

### When to activate

Enabled when:
- `complexity=EXPERT` (PatternAssembler HIGH rule)
- `is_generative=True` or `domain=CREATIVE` (MEDIUM rule)
- `agent_config.enable_self_refine=True`

### Implementation

`_node_refine()` in `graph.py` calls `SelfRefinePattern.execute()`:

```python
async def execute(self, *, last_output, task, provider, current_iteration, ...) -> str:
    # Calls LLM with _SELF_REFINE_SYSTEM prompt
    # Returns improved text, or original if LLM returns NO_CHANGES_NEEDED
```

The system prompt (`_SELF_REFINE_SYSTEM`) explicitly instructs: "Return the improved output OR exactly `NO_CHANGES_NEEDED` if it is already excellent." Temperature is set to 0.0 for deterministic refinements.

### Failure modes

- LLM fails during refinement → returns original output (never blocks execution).
- Runaway refinement if sentinel is never returned → `max_refine_iterations` cap prevents infinite loops.

### Monitoring

- `agent.refine.no_changes` — how often the LLM decided output was already good.
- `agent.refine.improved` — how often the output changed.

---

## Pattern 6: Self-Consistency

**File:** `app/agent/patterns/self_consistency.py` | **Node:** `self_consistency`

### What it is

The same prompt is executed N times in parallel (default N=3) at `temperature=0.7`. Responses are normalized and majority-voted using `Counter`. The most common answer wins.

Based on Wang et al. 2022, "Self-Consistency Improves Chain of Thought Reasoning."

### Why it exists

LLMs are stochastic at `temperature>0`. For complex analytical problems, the most common answer across independent samples is more reliable than a single high-temperature sample.

### When to activate

PatternAssembler adds `self_consistency` when `complexity=EXPERT and domain=ANALYTICAL` (HIGH priority). Also gated by `settings.enable_self_consistency`.

### Implementation

```python
async def execute(self, *, prompt, provider, ...) -> str:
    # Runs N samples in parallel via asyncio.gather
    responses = await asyncio.gather(*[_one_sample() for _ in range(self._n)])
    valid = [r for r in responses if r]
    return _most_common(valid)
```

`_most_common()` normalizes responses to lowercase, strips whitespace, and compares only the first 200 chars for the vote (avoiding formatting noise). The original un-normalized winning response is returned.

### Trade-offs

- **Cost**: 3× the token usage of a single sample.
- **Latency**: parallel execution keeps wall-clock time close to a single sample.
- **Best for**: multi-choice reasoning, code logic, mathematical reasoning.
- **Avoid for**: creative writing, open-ended generation, or any task where diversity is valuable.

### Monitoring

- `agent.self_consistency.agreement_rate` — how often all N samples agree.
- `agent.self_consistency.winner_sample` — which sample index won (detects bias).

---

## Pattern 7: Tree of Thoughts

**File:** `app/agent/patterns/tree_of_thoughts.py` | **Node:** `tree_of_thoughts`

### What it is

Implements Yao et al. 2023 "Tree of Thoughts" as a BFS search over the reasoning space. Before planning, the agent generates N candidate reasoning approaches, evaluates them, prunes to the top-K most promising, and expands the best path over `max_depth` levels.

Default parameters: `n_thoughts=3`, `max_depth=2`, `beam_width=2`.

### When it fires

Tree of Thoughts inserts **before planning** (after `rag_retrieval`). It is activated when:
- `complexity in (COMPLEX, EXPERT)` and `multi_step=True`
- `domain not in (CREATIVE, CONVERSATIONAL)` (PatternAssembler HIGH rule)
- `settings.enable_tree_of_thoughts=True`

### Implementation

Three parallel LLM call phases, all run with `asyncio.gather`:

**Phase 1 — Generate** (`_generate_thoughts`): N independent calls with `temperature=0.8`, each using `_GENERATE_SYSTEM`: "Generate a distinct reasoning approach as a single paragraph."

**Phase 2 — Evaluate** (`_evaluate_thoughts`): N parallel calls with `temperature=0.0` using `_EVALUATE_SYSTEM`. Response schema enforces: `{"score": float, "promising": bool, "reason": str}`. Thoughts with `score >= 0.6` are marked promising.

**Phase 3 — Expand** (`_expand_thought`): The best promising thought is expanded to a full reasoning chain and answer using `_EXPAND_SYSTEM` at `temperature=0.3`.

The final expanded answer is stored in `agent_state.context["tree_of_thoughts_result"]` and prepended to the planner prompt.

### Failure modes

- If all thoughts are pruned as unpromising, the best-scoring one is kept regardless.
- If ToT generation fails entirely, `_direct_answer()` falls back to a single LLM call.

### Monitoring

- `agent.tot.thoughts_generated` — N per invocation.
- `agent.tot.promising_fraction` — what fraction of thoughts survived pruning.
- `agent.tot.depth_reached` — how many expansion levels ran.

---

## Pattern 8: Peer Review

**File:** `app/agent/patterns/peer_review.py` | **Node:** `peer_review`

### What it is

A separate reviewer LLM evaluates the agent's final output **after** `verify`. The reviewer scores quality on a 0–1 scale, provides a critique, and marks the output approved or rejected. Rejection injects the critique into `verification_feedback` which triggers a replan.

### When to activate

PatternAssembler adds `peer_review` when `risk in (CRITICAL, HIGH) and complexity=EXPERT` (HIGH priority). Also gated by `settings.enable_peer_review`.

### Implementation

`PeerReviewPattern.execute()`:

```python
async def execute(self, *, output, goal, provider, ...) -> PeerReviewResult:
    # Calls LLM with _PEER_REVIEW_SYSTEM prompt
    # Returns PeerReviewResult(quality_score, critique, suggestions, approved)
```

The system prompt (`_PEER_REVIEW_SYSTEM`) asks for a JSON response with `quality_score`, `critique`, `suggestions[]`, and `approved`. The approval threshold is `quality_score >= 0.7` (configurable, default in `PeerReviewPattern(quality_threshold=0.7)`).

`PeerReviewResult.from_raw()` includes a graceful fallback parser: if the LLM does not return valid JSON, it scans the text for a `N/10` score pattern or sentiment keywords ("excellent" → 0.85, "poor" → 0.3).

If `approved=False`, `_node_peer_review()` in `graph.py` appends the critique to `agent_state.verification_feedback` and routes back to `plan`.

### Failure modes

- Reviewer LLM call fails → `PeerReviewResult(quality_score=0.5, approved=False)` — conservative: triggers replan.
- Reviewer scores too harshly → infinite replan loop; mitigated by the global `max_iterations` cap.

### Monitoring

- `agent.peer_review.quality_score` — distribution of scores.
- `agent.peer_review.rejection_rate` — how often peer review triggers a replan.

---

## Pattern 9: Supervisor

**File:** `app/agent/patterns/supervisor.py` | **Node:** `supervisor`  
**Implementation:** `app/agent/supervisor.py`

### What it is

A supervisor agent decomposes a complex task into sub-tasks and spawns independent sub-agents to complete them. Results are aggregated by the supervisor.

### When to activate

PatternAssembler adds `supervisor` when `complexity=EXPERT and domain=ANALYTICAL and multi_step=True` (MEDIUM priority). Set `agent_config.enable_supervisor=True` to force it for any goal.

### When NOT to use

- Goals that are strictly sequential (each step depends on the previous).
- Low-complexity goals (overhead exceeds benefit).
- When tools have shared mutable state that concurrent sub-agents could corrupt.

### Failure modes

- Sub-agent timeout → supervisor marks that sub-task as failed and proceeds with partial results.
- All sub-agents fail → supervisor returns failure to parent goal.

### Monitoring

- `agent.supervisor.sub_agents_spawned` — how many sub-agents were created.
- `agent.supervisor.sub_agent_success_rate` — fraction that completed.

---

## Pattern 10: Debate

**File:** `app/agent/patterns/debate.py` | **Node:** `debate`  
**Implementation:** `app/agent/debate.py`

### What it is

Two agents argue opposing positions (pro and con) on the proposed action or output. A third judge agent synthesizes both arguments and delivers a verdict.

### Why it exists

For controversial or high-stakes decisions, a single agent perspective is susceptible to bias and overconfidence. Adversarial debate surfaces hidden assumptions and risks.

### When to activate

Explicitly configured via `multi_agent_patterns=["debate"]` or in `agent_config`. Not automatically activated by PatternAssembler (no current rule adds it by default).

### Best suited for

- Security/architecture decisions where trade-offs matter.
- Legal or compliance interpretations.
- Business decisions with significant downside risk.

### Failure modes

- Judge makes a poor synthesis → peer review after debate can catch this.
- Debate adds 3× the token cost; avoid for routine tasks.

---

## Pattern 11: Goal Tree

**File:** `app/agent/patterns/goal_tree.py` | **Node:** `goal_tree_plan`  
**Implementation:** `app/agent/goal_tree.py`

### What it is

Complex goals are decomposed into a tree of parallel sub-goals, each processed by an independent agent run. Results are merged and summarized by the root agent.

### Why it exists

For goals with 4+ independent steps, sequential execution wastes wall-clock time. Goal Tree enables true parallelism at the goal level, not just the step level.

### When to activate

PatternAssembler adds `goal_tree` when:
- `complexity=EXPERT` (HIGH rule)
- `multi_step=True and complexity=EXPERT` (HIGH rule)
- `agent_config.enable_goal_tree=True`

Also gated by `goal_tree_threshold=4` in `AgentGraph` — decomposition only fires when the structured plan has ≥4 steps.

### Implementation

`_node_goal_tree_plan()` in `graph.py`:

1. Uses `STRUCTURED_PLANNER_SYSTEM` prompt to produce a `StructuredPlan` with a `SubGoal[]` list.
2. Each `SubGoal` has: `title`, `description`, `tools`, `depends_on`.
3. Independent sub-goals (empty `depends_on`) are dispatched in parallel via `asyncio.gather`.
4. Results are collected and the root agent synthesizes a final answer.

### Failure modes

- One sub-goal fails → partial results; root agent notes the gap.
- Circular dependencies → detected and serialized.

---

## Pattern 12: Consensus

**File:** `app/agent/patterns/consensus.py` | **Node:** `consensus`  
**Implementation:** `app/agent/consensus.py`

### What it is

Three independent verifier LLM calls vote on whether the goal output is acceptable. The majority vote (2-of-3) determines success or failure.

### When to activate

PatternAssembler adds `consensus_verification` to safety patterns when `risk=CRITICAL` (CRITICAL priority rule). This is inviolable.

### Why it exists

A single verifier call can be misled by a cleverly-worded but incorrect output. Three independent verifiers voting on the same output dramatically reduces false-positive acceptance of bad outputs.

### Failure modes

- 1-of-3 verifiers fail to respond → treated as a "fail" vote; 2 remaining votes decide.
- All 3 verifiers disagree → treated as failure (conservative).

---

## Pattern 13: Loop Engineering

**File:** `app/agent/patterns/loop_engineering.py` | **Node:** `_execute_step_with_loop`

### What it is

For steps that need to retry until a condition is met, `StructuredStep.loop_until` declares a termination condition as a natural-language string. The executor retries the step in a loop, evaluating the condition after each attempt.

### Implementation

`_execute_step_with_loop()` in `graph.py`:

1. Executes the step normally.
2. Evaluates `loop_until` against the step output by calling the verifier LLM.
3. If condition not met: re-executes (up to a sub-loop limit).
4. If condition met: marks the step complete and continues.

### Example use cases

- "Poll the CI status until build is green."
- "Retry the API call until the response contains `status: ready`."
- "Keep summarizing until the word count is under 200."

### Failure modes

- The loop condition is never satisfied → `max_loop_attempts` prevents infinite loops.
- Condition evaluation is ambiguous → defaults to "not met" (conservative).

---

## Pattern 14: Human-in-the-Loop (HITL)

**File:** `app/governance/hitl.py` | **Class:** `HITLGateway`

### What it is

The agent pauses at a pending action and waits for a human to approve or reject it before proceeding. The goal enters `waiting_human` status; an SSE event is emitted to the frontend.

### Two trigger modes

**Mode 1 — High-risk keywords**: `_HIGH_RISK_KEYWORDS` in `graph.py:87` is a frozen set:
```python
{"deploy", "delete", "drop", "rm ", "prod", "production", "destroy", "wipe", "truncate"}
```
If a planned step description or proposed tool call contains any of these, HITL fires automatically regardless of configuration.

**Mode 2 — Governance policy**: If the tenant's `PolicyEngine` has a rule with `PolicyResult.REQUIRE_APPROVAL` for the relevant tool or domain, HITL is triggered via `_node_execute()` before dispatching the tool call.

### Runtime flow

1. `HITLGateway.request_approval()` creates an `ApprovalRequest` (dual-mode: asyncio.Event in-process, or Redis BLPOP for cross-replica).
2. Goal status set to `waiting_human`; SSE event emitted.
3. Human approves/rejects via `POST /goals/{id}/approve`.
4. `signal_resume()` publishes to the Redis list; `_wait_for_result()` unblocks via BLPOP.
5. If `APPROVED` → execution continues. If `REJECTED` → goal fails with reason. If `TIMED_OUT` (default: 24h) → goal fails.

### When HITL is always on

When PatternAssembler assigns `risk=CRITICAL` or `risk=HIGH`, `hitl` is added to `safety_patterns` at CRITICAL priority. This cannot be overridden.

### Failure modes

- Redis unavailable → falls back to in-process asyncio.Event (single-replica only).
- Human never responds → `TIMED_OUT` after the configured deadline.

### Monitoring

- `agent.hitl.approval_wait_seconds` — time waiting for human.
- `agent.hitl.rejection_rate` — fraction of requests rejected.

---

## Pattern 15: Recovery, Rollback, and Retry

**Files:** `app/reliability/rollback.py`, `app/reliability/tool_inverses.py`

### RollbackEngine

`RollbackEngine` in `app/reliability/rollback.py:33` is a LIFO stack of `(action_description, inverse_fn)` pairs. Every time `_node_execute()` dispatches a tool call that succeeds, it registers the inverse:

```python
rollback_engine.register(
    action="create_file: main.py",
    inverse=lambda: mcp_client.call_tool("delete_file", {"path": "main.py"})
)
```

When a goal fails irreversibly, `rollback_engine.rollback_all()` pops and executes inverses in LIFO order — ensuring that later actions (which may depend on earlier state) are undone first.

### tool_inverses.py

`app/reliability/tool_inverses.py` defines compensating action mappings for well-known tools:

| Tool | Inverse |
|------|---------|
| `create_branch` | `delete_branch` |
| `create_file` | `delete_file` |
| `create_pr` | `close_pr` |
| `create_ticket` | `close_ticket` |
| `send_message` | (not reversible — logged as warning) |

### Stagnation Detection

`_node_verify()` tracks consecutive identical `verification_feedback` strings. When the same feedback appears 3 times in a row, the graph sets `terminal_reason=stagnation` and terminates rather than looping forever.

### Retry Semantics

The verifier LLM response can include a `retry: true` flag (indicating the failure is transient — e.g., a network timeout) or no retry flag (permanent failure). The graph routes:

- `retry=true` → `replan` edge — tries a different approach.
- `retry=false` or absence after `max_iterations` → `max_iterations_exceeded` edge → `END`.

`_DEFAULT_MAX_ITERATIONS = 100` (`graph.py:86`). EXPERT-complexity goals override this to `max_iterations=50` via PatternAssembler.

### Monitoring

- `agent.rollback.invoked` — how often rollback fires.
- `agent.rollback.actions_undone` — stack depth at rollback time.
- `agent.goal.stagnation` — count of stagnation terminations.
- `agent.goal.max_iterations` — count of goals hitting the iteration cap.

---

## Pattern Interaction Map

The following table summarizes which patterns interact at runtime:

| Pattern | Interacts with RAG? | Interacts with Memory? | Interacts with Evals? |
|---------|--------------------|-----------------------|-----------------------|
| ReAct | Yes — uses rag_context injected by rag_retrieval | Yes — reads exec_memory | Yes — EvalRunner scores final output |
| Plan-and-Execute | Yes — plan prompt includes rag_context | Yes — Reflexion injects lessons | Yes |
| Reflection | Indirect (diagnosis uses rag_context) | Yes — Reflexion stores diagnosis | No |
| Reflexion | No | Yes — ReflexionStore | No |
| Self-Refine | No | No | Yes — EvalRunner |
| Self-Consistency | No | No | No |
| Tree of Thoughts | No | No | No |
| Peer Review | No | No | Yes — quality_score → EvalRunner |
| Supervisor | Yes — each sub-agent has RAG | Yes — each sub-agent has memory | Yes |
| Debate | No | No | No |
| Goal Tree | Yes — each sub-goal has RAG | Yes | Yes |
| Consensus | No | No | No |
| Loop Engineering | Depends on step | No | No |
| HITL | No | Yes — approval logged to audit trail | No |
| Rollback | No | No | No |

---

## Tests

Unit tests for patterns live in `tests/agent/`. Integration tests use the `integration` marker and require Docker/testcontainers.

| Test | What it verifies |
|------|-----------------|
| `tests/agent/test_loop.py` | Full ReAct + Plan-Execute loop, replan on failure |
| `tests/agent/test_patterns.py` | Self-Refine `NO_CHANGES_NEEDED` sentinel, Self-Consistency majority vote |
| `tests/agent/test_reflexion.py` | Lesson storage, recall, format_for_context |
| `tests/agent/test_tree_of_thoughts.py` | BFS phases, pruning, fallback to direct answer |
| `tests/agent/test_peer_review.py` | Approval/rejection flow, fallback parser |
| `tests/agent/test_pattern_assembler.py` | Rule priorities, CRITICAL safety cannot be overridden |
| `tests/governance/test_hitl.py` | Approval flow, timeout, Redis vs in-memory paths |
| `tests/reliability/test_rollback.py` | LIFO order, typed inverses, noop warning |

Run the full agent suite:
```bash
uv run pytest tests/agent/ -v
uv run pytest tests/agent/ -m integration  # needs Docker
```

---

## Operational Reference

### Key settings (app/core/config.py)

| Setting | Default | Effect |
|---------|---------|--------|
| `enable_self_consistency` | False | Gates SelfConsistencyPattern.is_compatible() |
| `enable_tree_of_thoughts` | False | Gates TreeOfThoughtsPattern.is_compatible() |
| `enable_peer_review` | False | Gates PeerReviewPattern.is_compatible() |
| `enable_self_rag` | False | Gates SelfRAGPattern.is_compatible() |
| `enable_flare` | False | Gates FLAREPattern.is_compatible() |
| `enable_raptor` | False | Gates RAPTORPattern.is_compatible() |

### Default model routing (PatternConfig defaults)

```python
model_planner   = "gpt-5.2"
model_executor  = "gpt-5.2"
model_verifier  = "gpt-5.2"
model_classifier = "gpt-4o-mini"
```

Models are resolved from environment keys at startup. `FakeProvider` is used in tests and when no API keys are configured.

### Assembly latency

`PatternAssembler.assemble()` measures its own execution time:
```python
latency_ms = (time.perf_counter() - t0) * 1000
```
This is stored in `PatternConfig.assembly_latency_ms`. Typical values are <1 ms. Alert if it exceeds 50 ms (indicates rule evaluation is unexpectedly slow).

---

## End-to-End Runtime Flows

Understanding the exact sequence of events during a goal run connects all patterns together.

### Flow A: Simple single-step goal (default patterns)

**Patterns active**: react, guardrails, hybrid_rag

```
POST /goals  {goal: "What is the capital of France?"}
    │
    ▼
PatternAssembler.assemble()
    complexity=SIMPLE, domain=CONVERSATIONAL, risk=LOW
    → reasoning_patterns=["react"], rag_patterns=["hybrid_rag"], safety_patterns=["guardrails"]
    → max_iterations=15, autonomy_mode="bounded-autonomous"
    │
    ▼
DynamicGraphAssembler.assemble()
    → AgentGraph(enable_cot=False, enable_reflection=False, ...)
    │
    ▼
_node_initialize()
    → creates AgentState(goal_id, goal, tenant_ctx, status=RUNNING)
    → loads ExecutionMemory (similar past goals)
    → loads LongTermMemory lessons for this tenant
    │
    ▼
_node_rag_retrieval()
    → embeds the goal text
    → hybrid_search(collection_ids from tenant, top_k=10)
    → stores context in agent_state.rag_context
    │
    ▼
_node_plan()
    → PLANNER_SYSTEM prompt + goal + rag_context
    → LLM returns: ["Step 1: Answer the question directly."]
    → stores in agent_state.plan
    │
    ▼
_node_execute()  [ReAct loop for each step]
    → EXECUTOR_SYSTEM + step + tools + context
    → LLM returns tool call or direct answer
    → dispatches via MCPClient, records StepResult
    │
    ▼
_node_verify()
    → VERIFIER_SYSTEM + goal + step summary
    → LLM returns: {"success": true, "reason": "answered correctly"}
    → agent_state.verification_success = True
    → ExecutionMemory.record() (sync)
    → LongTermMemory.extract_from_goal() (sync) + async DB persist
    → EvalRunner.score_and_persist()
    → emit goal_complete SSE event
    │
    ▼
goal_complete → END
```

### Flow B: Expert analytical multi-step goal (all patterns)

**Patterns active**: react, chain_of_thought, reflection, self_refine, self_consistency, tree_of_thoughts, peer_review, goal_tree, supervisor, fusion_rag, raptor, corrective_rag, hitl, rollback, guardrails, consensus_verification

```
POST /goals  {goal: "Analyze our Q3 sales data against industry benchmarks and produce a strategic recommendation"}
    │
    ▼
PatternAssembler.assemble()
    complexity=EXPERT, domain=ANALYTICAL, risk=HIGH, multi_step=True
    → full pattern set assembled, max_iterations=50, autonomy_mode="supervised"
    │
    ▼
_node_initialize()
    → GoalProperties detected
    → PatternConfig.assembly_latency_ms recorded (e.g., 0.3 ms)
    → ReflexionStore.recall(tenant_id, limit=5) → "reflexion_lessons" in context
    │
    ▼
_node_rag_retrieval()
    → retrieve_fusion(): 3 query variants → 3 parallel hybrid_search calls → rrf_fuse
    → RAPTORPattern: build summary tree from top-20 results → root summary prepended
    │
    ▼
_node_tree_of_thoughts()  [if enable_tree_of_thoughts=True]
    → 3 parallel LLM calls to generate approaches
    → 3 parallel eval calls → prune to top-2
    → expand best path
    → store result in context["tree_of_thoughts_result"]
    │
    ▼
_node_think()  [chain_of_thought]
    → CHAIN_OF_THOUGHT_SYSTEM prompt
    → LLM narrates step-by-step reasoning
    → result injected into planner context as context["cot_reasoning"]
    │
    ▼
_node_plan()
    → STRUCTURED_PLANNER_SYSTEM (because enable_goal_tree=True)
    → produces StructuredPlan with SubGoals
    → dependency analysis → independent sub-goals identified
    │
    ▼
_node_goal_tree_plan()
    → asyncio.gather for independent sub-goals
    → each sub-goal runs its own mini ReAct loop
    → results merged
    │
    ▼
_node_execute()
    → for each step: classify_tool_risk() → HIGH risk tool triggers HITL check
    → HITLGateway.request_approval() → waiting_human status
    → human approves → signal_resume() → execution continues
    → RollbackEngine.register(action, inverse) for each side-effecting tool call
    │
    ▼
_node_refine()  [self_refine, after execute]
    → SelfRefinePattern.execute(last_output, task)
    → up to 2 refinement iterations
    → NO_CHANGES_NEEDED stops early
    │
    ▼
_node_self_consistency()  [self_consistency]
    → SelfConsistencyPattern.execute(prompt): 3 parallel samples → majority vote
    │
    ▼
_node_verify()
    → VERIFIER_SYSTEM + goal + full step summary
    → ConsensusVerifier (3-way vote, because risk=CRITICAL/HIGH)
    → if disagreement: HITLGateway.request_approval() again
    → success → EvalRunner.score_and_persist() → eval_scorecard in context
    → LongTermMemory.extract_from_goal_async() (background task)
    │
    ▼
_node_peer_review()  [peer_review]
    → PeerReviewPattern.execute(output, goal)
    → quality_score=0.85, approved=True → continue to complete
    → quality_score=0.45, approved=False → inject critique → replan
    │
    ▼
goal_complete → emit SSE → END
```

### Flow C: Failed goal with Reflexion memory

```
Goal fails after 3 replan iterations:
    verify → failure → reflect → _node_reflect:
        REFLECTION_SYSTEM prompt → diagnosis
        Reflexion.store_lesson(tenant_id, goal, feedback, failure_class="tool_error")
        → stored in ReflexionStore (in-memory + DB background task)

Next goal by same tenant:
    _node_initialize():
        ReflexionStore.recall(tenant_id, limit=5)
        → lessons injected as context["_reflexion_lessons"]
    _node_plan():
        planner prompt includes:
        "[Reflexion lessons from past failures — avoid these mistakes:]
         1. For goal 'analyze sales...': tool 'export_csv' failed due to missing auth header
            (class: tool_error)"
```

---

## The Verifier: What It Actually Does

The verifier is not a simple pass/fail check. `_node_verify()` in `graph.py` performs a rich assessment.

### Step summary construction (`_build_verifier_summary`)

Before calling the verifier LLM, `_build_verifier_summary()` (`graph.py:116`) constructs a rich summary that:

1. Collects **all failed/ungrounded steps** anywhere in the run (not just the last 5).
2. Appends the **last 5 steps** for recency context.
3. Deduplicates so failed steps in the last 5 aren't repeated.
4. Tags each step with `[UNGROUNDED CLAIM]`, `[TOOL FAILED]`, or `[STEP ERROR]` markers.

This means the verifier always sees every failure, regardless of how many steps ago it occurred — preventing the LLM from hallucinating success because only recent steps are visible.

### Stagnation detection

At the top of `_node_verify()`, before calling the LLM, the code checks:

```python
_feedback_history = agent_state.context.get("_feedback_history", [])
if (len(_feedback_history) >= 3
        and len(set(_feedback_history[-3:])) == 1):
    # Three identical feedbacks → stagnation
    terminal_reason = "stagnation"
```

This fires before the LLM call, saving the token cost of a pointless verification round.

### Post-success memory writes

When `success=True` (graph.py:3560–3611), the verifier node triggers a cascade of memory writes, all as fire-and-forget `asyncio.create_task` background tasks:

1. **ExecutionMemory.record()** (sync, immediate) — records the winning plan for same-session recall.
2. **ExecutionMemory.record_async()** (async DB task) — persists the plan to Postgres.
3. **LongTermMemory.extract_from_goal()** (sync, immediate) — extracts learnings in-memory.
4. **LongTermMemory.extract_from_goal_async()** (async DB task with embedder) — persists pgvector-indexed learnings.
5. **EvalRunner.score_and_persist()** (awaited inline) — multi-dimension scoring, stored in DB.
6. **RuntimeScorecard.score()** — dynamic orchestration scoring for runtime optimization.

If any background task fails, it logs a warning but never blocks the goal completion event.

### 3-way consensus invocation

`ConsensusVerifier.verify()` is invoked only when: (a) the primary verifier said `success=False`, AND (b) `requires_consensus()` returns True based on the tool risks and goal domain. This avoids the 3× cost when the primary verifier already agrees.

If the consensus verifiers **disagree** (split vote), `consensus_result.requires_hitl=True` and HITL fires again — a second human review is requested (`graph.py:3525`).

---

## Tool Call Mechanics

### Tool risk classification (`app/agent/tool_risk.py`)

Before dispatching any tool call in `_node_execute()`, `classify_tool_risk(tool_name, arguments)` is called. It returns a risk level: LOW, MEDIUM, HIGH, or CRITICAL. Classification is based on:

1. **Keyword matching** on the tool name: tools containing `delete`, `drop`, `deploy`, `destroy`, `write`, `create`, `modify` are elevated.
2. **Argument inspection**: arguments containing `"prod"`, `"production"`, `"--force"`, or path patterns like `/etc/`, `/var/` are elevated.
3. **Static registry**: a per-tool risk map covers known MCP tool names.

HIGH and CRITICAL risk tools trigger HITL (when the HITL gateway is wired). The risk level is stored in `StepResult.tool_calls[].risk_level` for audit.

### Tool call parsing and repair

The executor LLM is expected to return structured tool calls. `extract_tool_call()` in `app/agent/tool_calls.py` parses the response using a multi-strategy approach:

1. Native structured output (when `supports_structured_output()` is True).
2. JSON code block extraction (`\`\`\`json ... \`\`\``).
3. Raw JSON object search.
4. `repair_tool_call_arguments()` — attempts to fix common JSON errors: trailing commas, unquoted keys, single quotes instead of double quotes, truncated JSON.

If all repair attempts fail, the step is marked `STEP_ERROR` and the error is appended to `verification_feedback` for the next plan iteration.

### MCPClient dispatch

Once a valid `ToolCall(tool_name, arguments)` is parsed, `MCPClient.call_tool()` dispatches it to the registered MCP server. The client:

1. Looks up the tool registration in `MCPRegistry` (per-tenant connector map).
2. Sends an HTTP POST to the MCP server's tool execution endpoint.
3. Returns the raw JSON output.
4. `sanitize_tool_raw_output()` strips PII, credentials, and oversized responses before storing in `StepResult.output`.

RPA tools (`rpa_*`) bypass MCPClient and go directly to the Playwright-based RPA module (`app/rpa/`). On success, RPA tool outputs are stored in `LongTermMemory` for future goals to recall (fire-and-forget, `graph.py:2787`).

---

## Guardrails

`GuardrailChecker` (`app/intelligence/guardrails.py`) runs at two points:

1. **Pre-plan**: the goal text is checked for policy violations, jailbreaks, and harmful intent before planning begins.
2. **Pre-tool**: each tool call's arguments are checked before dispatch.

When Guardrails v2 is available (`app/guardrails_v2/`), `guardrails_engine` provides more sophisticated content and policy checks across `GuardrailLayer` definitions. The v2 engine is loaded at import time with graceful fallback (`graph.py:76–83`).

Guardrail violations result in the goal being immediately failed with `terminal_reason=guardrail_violation` — no replan, no retry.

---

## Cost Control and Model Routing

### Dynamic model downgrade

`_node_plan()` at `graph.py:1430` implements cost-aware model downgrade:

```python
cost_tier = await cost_controller.get_cost_tier(goal_id, tenant_ctx)
if cost_tier == "economy":
    planning_model = model_router.model_for("verification")  # cheaper model
elif cost_tier == "standard":
    planning_model = model_router.model_for("execution")
```

This silently swaps to cheaper models when a goal has consumed >60% of its budget — maintaining completion over perfection.

### LLM Response Cache

`LLMResponseCache` (`app/intelligence/`) caches identical `(system_prompt, user_prompt, model, tenant_id)` tuples for planning calls. Cache hits skip the LLM call entirely (`_llm_plan_cached = True` in graph.py). This is especially effective for parameterized goals that differ only in data values but share the same planning structure.

### Semantic cache

`SemanticCache` (`app/rag/semantic_cache.py`) deduplicates LLM calls by *embedding similarity*, not exact match. If a nearly identical goal was recently executed with the same embedding, the cached response is returned without a new LLM call.

---

## AgentState: The Complete Runtime Object

`AgentState` (`app/agent/state.py`) is the single source of truth for a running goal. Key fields:

| Field | Type | Purpose |
|-------|------|---------|
| `goal_id` | str | Unique ID, used as LangGraph thread_id |
| `goal` | str | Original natural-language goal text |
| `status` | GoalStatus | RUNNING / COMPLETE / FAILED / WAITING_HUMAN |
| `steps` | list[StepResult] | All executed steps with tool calls and outputs |
| `plan` | list[str] | Current plan step descriptions |
| `iterations` | int | Replan counter |
| `verification_success` | bool | Last verifier verdict |
| `verification_feedback` | str | Last verifier reason |
| `rag_context` | str | Retrieved knowledge injected at planning |
| `cited_answer` | str | Final answer with inline `[N]` citations |
| `provenance` | list[dict] | Source attribution: chunk_id, source_url, page |
| `context` | dict | General key-value bag (reflexion_lessons, cot_reasoning, etc.) |
| `events` | list[dict] | Append-only SSE event log |

**LangGraph checkpointing**: `AgentState` is serialized at every state transition via the configured checkpointer. With Redis available (production), this is `AsyncRedisSaver` — goal state survives server restarts and can be resumed by re-invoking with the same `goal_id` as `thread_id`.

`StepResult` records:

| Field | Purpose |
|-------|---------|
| `description` | The plan step text |
| `output` | Sanitized tool/LLM output |
| `status` | StepStatus: COMPLETE / TOOL_FAILED / STEP_ERROR / UNGROUNDED |
| `tool_calls` | list[ToolCallRecord] with tool_name, arguments, success, error, risk_level |
| `error` | Error message if status != COMPLETE |

`StepStatus.UNGROUNDED` is assigned by the grounding checker when a step output contains claims not supported by the retrieved context. The `[UNGROUNDED CLAIM]` marker in `_build_verifier_summary()` highlights these to the verifier.

---

## Pattern Selection Recipes

### Recipe: High-throughput automation (cost-optimized)

For simple, repetitive automation goals where quality is less critical than throughput:

```python
agent_config = {
    "enable_cot": False,
    "enable_reflection": False,
    "max_iterations": 5,
}
# GoalProperties: complexity=SIMPLE, risk=LOW
# PatternAssembler activates: react, guardrails, hybrid_rag only
```

Cost reduction: ~70% vs EXPERT mode. Latency: ~2× faster.

### Recipe: Research synthesis (quality-optimized)

For deep research, multi-source synthesis, and strategic recommendations:

```python
agent_config = {
    "enable_cot": True,
    "enable_reflection": True,
    "enable_goal_tree": True,
}
# GoalProperties: complexity=EXPERT, domain=ANALYTICAL, multi_step=True
# PatternAssembler activates: everything
# settings: enable_self_consistency=True, enable_raptor=True
```

### Recipe: Production deployment (safety-critical)

For any goal that touches production systems:

```python
# GoalProperties: risk=CRITICAL, reversibility="irreversible"
# PatternAssembler CRITICAL rules activate:
#   - hitl, rollback, guardrails, consensus_verification (inviolable)
#   - autonomy_mode="supervised"
#   - persistence_mode=False (no auto-retry without human review)
```

No configuration can override CRITICAL safety rules. This is enforced in `PatternAssembler.assemble()` via the `critical_safety` set — even if `force_no_hitl=True` is in `agent_config`, it is silently ignored (`pattern_assembler.py:245`).

---

## Debugging Guide

### Which patterns fired?

Every completed goal stores `PatternConfig.selection_reason` — a dict mapping each activated pattern to the rule that triggered it:

```python
{
    "react": "default reasoning loop",
    "chain_of_thought": "complexity=expert",
    "reflection": "technical domain — reflection improves quality",
    "hitl": "risk=critical — HITL inviolable",
    "goal_tree": "expert+multi_step → parallel sub-goals",
}
```

Access via `GET /goals/{id}` → `pattern_config.selection_reason`.

### Why did the goal replan?

`agent_state.context["_feedback_history"]` contains the last N verification feedback strings. If identical entries appear, stagnation was triggered. If they differ but the goal keeps failing, examine the pattern diagnoses in `agent_state.context["reflection_diagnosis"]`.

### Why did HITL fire unexpectedly?

1. Check whether the step description or tool arguments contained any of `_HIGH_RISK_KEYWORDS` (`graph.py:87`): `{"deploy", "delete", "drop", "rm ", "prod", "production", "destroy", "wipe", "truncate"}`.
2. Check `PolicyEngine` rules for the tenant — `REQUIRE_APPROVAL` policies trigger HITL independently of the keyword list.

### Performance hotspots

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Goal takes >60s to plan | ToT + Self-Consistency both active | Check if EXPERT/ANALYTICAL triggered both; consider disabling ToT for lower-complexity |
| 3× expected token cost | Peer Review + Self-Consistency + Self-Refine all active | These stack; tune complexity thresholds |
| Goal always hits max_iterations | Stagnation not firing | Check if feedback varies slightly each time; consider lowering `max_iterations` |
| HITL fires on every deploy step | HIGH_RISK_KEYWORDS match | Add a tenant-level policy exception for specific tools |

---

## Pattern Interaction Deep-Dive

Some patterns only show their value when they interact with each other. This section traces the most important combined behaviors.

### ReAct + Reflection + Reflexion: the learning loop

These three patterns form the agent's core improvement mechanism across goal boundaries.

**Within a goal run**:
1. ReAct executes a step → tool call fails.
2. `verification_feedback` = "File not found: /repo/main.py — check if path is correct."
3. Reflection fires: `_node_reflect()` diagnoses "Executor assumed root-relative path; must use absolute path from tool output."
4. Diagnosis stored in `context["reflection_diagnosis"]`.
5. Next plan iteration: planner prompt includes the diagnosis → planner generates step with absolute path.

**Across goal runs** (Reflexion):
1. The goal ultimately fails (max iterations hit).
2. In the failure branch of `_node_verify()`: `ReflexionPattern.store_lesson(tenant_id, goal, feedback, failure_class="tool_error")`.
3. Lesson: `"For goal 'Deploy to staging...': tool 'write_file' failed due to path error. Always use absolute paths."`.
4. Next time any goal for this tenant hits `_node_plan()`:
   - `ReflexionStore.recall(tenant_id, limit=5)` returns this lesson.
   - `format_for_context()` prepends it to the planner prompt.
   - The planner avoids the same mistake without needing to fail first.

**The feedback loop**: Reflection provides immediate single-run improvement. Reflexion provides cross-run improvement. Together they implement a lightweight form of continual learning without any model fine-tuning.

### Self-Consistency + Peer Review: double-checking analytical outputs

These two patterns address the same problem from different angles — both are triggered for `EXPERT+ANALYTICAL` goals.

**Self-Consistency** operates *during* execution:
- 3 parallel samples of the reasoning step at `temperature=0.7`.
- Majority vote selects the most common answer.
- Reduces per-step variance.

**Peer Review** operates *after* verification:
- A separate reviewer LLM evaluates the complete output.
- Provides a structured quality score and critique.
- Can force a replan with specific improvement guidance.

**Combined flow**:
1. Step produces 3 samples: `["GDP growth was 2.4%", "GDP growth was 2.4%", "GDP growth was 2.1%"]`.
2. Self-Consistency votes for `"GDP growth was 2.4%"`.
3. Goal completes → verification passes.
4. Peer Review scores the full analysis: `quality_score=0.72, approved=True` (barely passes).
5. Peer Review critique: "Missing comparison to previous year's 3.1% growth."
6. `approved=True` → no replan. Critique is logged but not acted on.

If `approved=False`: the critique is injected as `verification_feedback` and the graph routes back to `plan`. The next plan iteration includes the Reflexion lesson "Reviewer noted missing YoY comparison" — both Peer Review and Reflexion collaborate.

### HITL + Rollback: safe production operations

These two safety patterns are designed to work together for irreversible operations.

**HITL** fires first — before execution — giving the human a chance to review the planned action. **Rollback** fires after — registering inverse operations as each tool call completes — so if a later step fails, all earlier steps can be undone.

**Timeline**:
```
t=0: plan approved by HITL ("Deploy v1.2.3 to production")
t=1: step 1: create_deployment(env="prod") → SUCCEEDS
     RollbackEngine.register(action="create_deployment:prod", inverse=delete_deployment)
t=2: step 2: run_health_check() → FAILS ("503 Service Unavailable")
t=3: verification: failure
t=4: rollback_engine.rollback_all() in LIFO order:
     → delete_deployment(env="prod") → SUCCEEDS
     → goal status: FAILED_WITH_ROLLBACK
t=5: SSE event: "goal_failed_rolled_back" with rollback_actions=["delete_deployment:prod"]
```

The key invariant: rollback always runs in LIFO order. If step 3 depends on step 2 which depends on step 1, undoing step 3 first preserves the dependency order.

**Persistence mode**: when `persistence_mode=True` (set for EXPERT complexity by PatternAssembler), the agent attempts a fixed number of replans (`max_persistence_attempts=5`) before giving up. Each replan attempt is preceded by rollback of the previous attempt's side effects.

### Tree of Thoughts + Chain of Thought: two-level reasoning

ToT and CoT serve different purposes in the reasoning hierarchy and compose naturally:

**Chain of Thought** (`_node_think`): generates a linear reasoning chain — "Step 1: identify the variables. Step 2: apply the formula. Step 3: verify units." This is fast (one LLM call) and improves structured reasoning.

**Tree of Thoughts** (`_node_tree_of_thoughts`): generates multiple *approaches* and picks the best. This is slower (N+N+1 LLM calls) but avoids committing to a poor strategy.

**Execution order** (when both active):
1. ToT fires first (after `rag_retrieval`, before `plan`).
2. ToT selects the best reasoning approach: "Use discounted cash flow analysis."
3. CoT fires next (`_node_think`): narrates the steps within that approach.
4. Plan uses both: the ToT approach guides *what* to plan; the CoT narration guides *how* to plan.

ToT handles **approach selection** (macro-level). CoT handles **step elaboration** (micro-level). They are complementary, not redundant.

---

## Supervisor and Goal-Tree: Multi-Agent Patterns Compared

Both Supervisor and Goal Tree decompose complex goals, but they differ in architecture:

| Aspect | Goal Tree | Supervisor |
|--------|-----------|-----------|
| Decomposition unit | Sub-goal (whole mini-goal) | Sub-task (bounded instruction) |
| Sub-agent lifecycle | Full AgentGraph per sub-goal | Lighter execution loop |
| Dependency handling | Explicit `depends_on` DAG | Supervisor decides sequencing |
| When to use | Parallel, independent domains | Sequential, interdependent steps |
| PatternAssembler trigger | `expert + multi_step` | `expert + analytical + multi_step` |
| Graph node | `goal_tree_plan` | `supervisor` |
| Token cost | High (N full agents) | Medium (supervisor + lean sub-tasks) |

**Goal Tree** works best when sub-goals are genuinely independent — e.g., "analyze Q3 revenue AND analyze Q3 cost AND analyze Q3 customer satisfaction" can all run in parallel via `asyncio.gather`.

**Supervisor** works best when sub-tasks must be sequenced and the supervisor needs to adapt based on intermediate results — e.g., "first analyze the database schema, then design the migration script based on what you found."

In practice, PatternAssembler may activate **both** for `EXPERT+ANALYTICAL+multi_step` goals. In that case, the Supervisor pattern runs inside the Goal Tree — each sub-goal has a supervisor that manages its internal sub-tasks.

---

## EvalRunner and Goal Scoring

`EvalRunner.score_and_persist()` is called on every successful goal. It evaluates the goal on multiple dimensions and persists the scorecard to the DB.

### Scoring dimensions

| Dimension | What is measured |
|-----------|-----------------|
| Goal fulfillment | Did the output address the original goal? |
| Factual accuracy | Are claims supported by the retrieved context? |
| Completeness | Are all aspects of the goal covered? |
| Conciseness | Is the output appropriately brief without losing substance? |
| Tool efficiency | Were tools used with minimal redundancy? |
| Safety compliance | Did the goal respect all policy constraints? |

Each dimension is scored 0–1 by the verifier LLM. The aggregate score is stored as `eval_scorecard` in `agent_state.context` and persisted for analytics.

### Self-optimizer feedback

The `RuntimeScorecard` (invoked when `dynamic_orchestration=True`) uses the eval scorecard to update the pattern selection strategy. If COMPLEX goals consistently score poorly on factual accuracy, the self-optimizer may elevate the complexity threshold for activating CRAG, or increase `confidence_threshold` in CRAG from 0.5 to 0.6. This closes the feedback loop from goal quality back to pattern selection.

### Verifier calibration

`VerifierCalibrationStore` (`app/intelligence/verifier_calibration.py`) records each verifier verdict (goal_id, verifier_model, iteration, verdict). Over time, if a verifier model systematically marks correct outputs as failed, its calibration score drops and the model router may switch to a different verifier model. This is tracked at `graph.py:3540`.

---

## Pattern State Enum

Every pattern adapter defines a `state` property returning `PatternState`:

```python
class PatternState(str, enum.Enum):
    IMPLEMENTED = "implemented"   # fully working, used in production
    PARTIAL     = "partial"       # working but limited
    PLANNED     = "planned"       # not yet implemented
```

All 15 patterns documented here return `PatternState.IMPLEMENTED`. The strategy registry (`build_default_registry()`) tracks 90 strategies total — the implemented ones are the focus of this document. PLANNED strategies are listed in `docs/superpowers/plans/` and are not wired into the graph.

When adding a new pattern, the adapter's `is_compatible(goal_properties)` method is the per-goal compatibility gate. It can check both `settings` flags (global enable/disable) and `goal_properties` attributes (per-goal suitability). This separation means a pattern can be globally enabled but still skip incompatible goals without PatternAssembler changes.

---

## How to Add a New Pattern

This is the canonical procedure for shipping a new pattern to production.

### Step 1: Create the adapter (`app/agent/patterns/my_pattern.py`)

```python
"""My Pattern — brief description of what it does."""
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState

class MyPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "my_pattern"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED  # set PARTIAL during development

    @property
    def description(self) -> str:
        return "MyPattern: what it does and where the implementation lives."

    @property
    def node_name(self) -> str:
        return "_node_my_pattern"  # the graph.py node that runs this pattern

    def is_compatible(self, goal_properties: Any) -> bool:
        # Gate on a settings flag first
        try:
            from app.core.config import get_settings
            if not get_settings().enable_my_pattern:
                return False
        except Exception:
            pass
        # Gate on goal properties
        return str(getattr(goal_properties, "complexity", "")).lower() in ("complex", "expert")
```

### Step 2: Implement the node in `graph.py`

Add `_node_my_pattern` to `AgentGraph`:

```python
async def _node_my_pattern(self, state: GraphState) -> GraphState:
    if not self._enable_my_pattern:  # constructor flag
        return state
    agent_state = state["agent_state"]
    tenant_ctx = state["tenant_ctx"]
    # ... pattern logic ...
    # Return state updates (LangGraph merges these)
    return {**state, "agent_state": agent_state}
```

Wire the node into the graph in `_build_graph()`:

```python
if self._enable_my_pattern:
    graph.add_node("my_pattern", self._node_my_pattern)
    graph.add_edge("verify", "my_pattern")
    graph.add_edge("my_pattern", "complete")
```

### Step 3: Add the feature flag

In `app/core/config.py` (`Settings` class):

```python
enable_my_pattern: bool = Field(default=False, description="Enable MyPattern for eligible goals.")
```

### Step 4: Add the PatternAssembler rule

In `app/agent/pattern_assembler.py`, add to `_RULES`:

```python
Rule(
    condition=lambda p: p.complexity == Complexity.EXPERT and p.domain == Domain.ANALYTICAL,
    add_reasoning=["my_pattern"],
    reason_key="my_pattern",
    reason_value="expert analytical — my_pattern improves X",
    priority="MEDIUM",
),
```

### Step 5: Wire the DynamicGraphAssembler flag

In `app/agent/dynamic_graph.py:assemble()`:

```python
enable_my_pattern="my_pattern" in reasoning,
```

### Step 6: Write tests

```python
# tests/agent/test_my_pattern.py
async def test_my_pattern_fires_for_expert_analytical():
    props = GoalProperties(complexity=Complexity.EXPERT, domain=Domain.ANALYTICAL)
    config = pattern_assembler.assemble(props, agent_config={})
    assert "my_pattern" in config.reasoning_patterns

async def test_my_pattern_skips_when_disabled(settings):
    settings.enable_my_pattern = False
    pattern = MyPattern()
    assert not pattern.is_compatible(GoalProperties(complexity=Complexity.EXPERT))
```

### Step 7: Register in the strategy registry

`build_default_registry()` in `app/agent/` registers all patterns with their metadata. Add an entry for `my_pattern` so it appears in `GET /patterns` and observability dashboards.

---

## SSE Event Taxonomy

Every state transition in the agent emits an SSE event via `self._emit()`. These are consumed by the frontend (`src/lib/sse/useGoalStream.ts`) and by any webhook subscribers.

| Event type | When emitted | Key fields |
|-----------|-------------|-----------|
| `goal_started` | After initialize | `goal_id`, `goal`, `tenant_id` |
| `plan_created` | After `_node_plan` | `steps: list[str]` |
| `step_started` | Before each step execution | `step`, `step_index` |
| `tool_call_started` | Before MCPClient.call_tool | `tool`, `server_id`, `arguments` |
| `tool_call_complete` | After successful tool call | `tool`, `output`, `duration_ms` |
| `tool_call_failed` | After failed tool call | `tool`, `error` |
| `step_complete` | After step execution | `step`, `output` |
| `verification_done` | After `_node_verify` | `success`, `reason` |
| `reflection_complete` | After `_node_reflect` | `diagnosis` |
| `peer_review_complete` | After peer review | `quality_score`, `approved`, `critique` |
| `goal_complete` | On success | `cited_answer`, `eval_scorecard` |
| `goal_failed` | On permanent failure | `reason`, `rollback_actions` |
| `approval_requested` | HITL triggered | `request_id`, `action`, `risk_level` |
| `model_route_selected` | After model routing | `planner`, `executor`, `verifier`, `cost_class` |

The frontend uses these events to render the live execution trace, step-by-step, with tool call details and intermediate results. Every event is also appended to `AgentState.events` for post-hoc inspection via `GET /goals/{id}/events`.
