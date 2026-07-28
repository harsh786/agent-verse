# 06 — Agent Improvement Loops, Eval Systems, and Observability

This document covers the complete feedback loop that makes AgentVerse self-improving: how every goal execution is scored across nine quality dimensions, how those scores drive six categories of automated improvement actions, and how the platform exposes its internals to operators through structured logs, Prometheus metrics, OpenTelemetry traces, and real-time SSE events.

---

## Part A: Agent Improvement Loops

### 1. RuntimeScorecard (`app/evals/runtime_scorecard.py`)

`RuntimeScorecard` produces a nine-dimension quality report for every completed goal. It is the entry point into the entire self-improvement pipeline — without scores, there is nothing to improve.

#### Activation

`RuntimeScorecard` fires when either environment variable is truthy:

```
DYNAMIC_ORCHESTRATION=true   # enables the full orchestration and scoring stack
ENABLE_RUNTIME_SCORECARD=true  # enables scoring only, without full orchestration
```

It is called inside `_node_verify()` in `app/agent/graph.py`, immediately after `EvalRunner.score_and_persist()`. The result is persisted to the `eval_scorecards` table via `OrchestrationPersistence.persist_scorecard()`.

#### The Nine Dimensions and Their Scorers

Each dimension is computed by a dedicated scorer class. All return a `float` in `[0.0, 1.0]`.

| Dimension | Weight | Scorer class | Source file |
|-----------|--------|-------------|-------------|
| `goal_success` | **0.30** | `GoalScorer` | `app/evals/goal_score.py` |
| `rag_quality` | **0.15** | `RAGScorer` | `app/evals/rag_score.py` |
| `safety` | **0.15** | `SafetyScorer` | `app/evals/safety_score.py` |
| `grounding` | **0.10** | `AgentScorer` | `app/evals/agent_score.py` |
| `tool_success_rate` | **0.10** | `AgentScorer` | `app/evals/agent_score.py` |
| `citation_quality` | **0.05** | `AgentScorer` | `app/evals/agent_score.py` |
| `retrieval_confidence` | **0.05** | Direct from `RetrievalResult` | `app/evals/runtime_scorecard.py` |
| `latency` | **0.05** | `ModelScorer` | `app/evals/model_score.py` |
| `cost_efficiency` | **0.05** | `ModelScorer` | `app/evals/model_score.py` |

Goal success dominates with 30% of the weight because it is the clearest proxy for user value. Safety and RAG quality each carry 15% — a goal that retrieves nothing or violates a guardrail is nearly as bad as a goal that fails entirely.

#### Weighted Overall Score Formula

```python
# From app/evals/runtime_scorecard.py:72
overall = (
    goal_success        * 0.30 +
    rag_quality         * 0.15 +
    safety              * 0.15 +
    grounding           * 0.10 +
    tool_success_rate   * 0.10 +
    citation_quality    * 0.05 +
    retrieval_confidence * 0.05 +
    latency             * 0.05 +
    cost_efficiency     * 0.05
)
```

The `ScorecardResult` dataclass also carries `improvement_suggestions` — human-readable strings generated when any dimension falls below its warning threshold (e.g., "High hallucination rate — improve grounding or retrieval" when `grounding < 0.7`).

---

#### GoalScorer Detail (`app/evals/goal_score.py`)

`GoalScorer.score(state)` maps terminal goal status to a base score and applies an efficiency penalty:

| `GoalStatus` | Base score | Efficiency penalty |
|-------------|-----------|-------------------|
| `COMPLETE` | 1.0 | `min(0.3, max(0, iterations − 5) × 0.01)` |
| `FAILED` | 0.0 | none |
| `WAITING_HUMAN` | 0.5 | none |
| Other | 0.3 | none |

A goal completing in exactly 5 iterations scores 1.0. One completing in 35 iterations scores `1.0 − min(0.3, 30 × 0.01) = 0.70`. The efficiency penalty is capped at 0.3 so even a 100-iteration goal cannot score below 0.7 on this dimension if it completes.

---

#### RAGScorer Detail (`app/evals/rag_score.py`)

The retrieval source type is more informative than confidence alone:

| `retrieval_result.source` | Score formula | Rationale |
|--------------------------|--------------|----------|
| `None` | 0.5 | No retrieval attempted — neutral |
| `none_available` | 0.1 | Store is empty — near-failure |
| `parametric` | 0.3 | LLM's unverifiable internal knowledge |
| `memory` | 0.5 | Episodic/procedural memory — useful but not curated |
| `web` | `0.6 + confidence × 0.2` | Live web — reliable but uncontrolled |
| `knowledge_base` | `min(1.0, 0.5 + confidence × 0.5)` | Curated KB — highest trust source |
| Other | `max(0.0, min(1.0, confidence))` | Unknown source — raw confidence |

A knowledge-base retrieval with confidence 0.9 scores `min(1.0, 0.5 + 0.45) = 0.95`. A parametric-only answer (the LLM just made it up from training) is capped at 0.3 regardless of apparent confidence.

---

#### SafetyScorer Detail (`app/evals/safety_score.py`)

Penalties are additive and compound:

```python
# From app/evals/safety_score.py:8
penalty = (
    guardrail_violations * 0.20 +   # each injection/dangerous-command detection
    hitl_bypasses        * 0.30 +   # each time a high-risk step skipped approval
    audit_gaps           * 0.10     # each missing audit event (tamper signal)
)
score = max(0.0, 1.0 - penalty)
```

A single guardrail violation gives a safety score of 0.80. Two violations plus one HITL bypass gives `1.0 − (2×0.2 + 1×0.3) = 0.30`. This feeds directly into the `SelfImprovementEngine`'s `safety < 1.0` suggestion and the audit trail.

---

#### ModelScorer — Cost and Latency (`app/evals/model_score.py`)

**Cost efficiency scoring** (`score_cost`) bands the actual-to-budget ratio:

| Cost ratio (`actual / max_cost_usd`) | Score |
|--------------------------------------|-------|
| ≤ 0.30 | 1.0 — well within budget |
| 0.30 – 0.60 | 0.8 — moderate spend |
| 0.60 – 1.00 | 0.6 — near budget |
| > 1.00 | `max(0.0, 1.0 − (ratio − 1.0) × 0.5)` — over budget, decreasing |

If `actual_cost ≤ 0` (no cost data in `state.context["total_cost_usd"]`), a neutral score of 0.8 is returned rather than 0 or 1.

**Latency scoring** (`score_latency`) bands milliseconds:

| Latency | Score |
|---------|-------|
| < 5 s | 1.0 |
| 5 – 15 s | 0.8 |
| 15 – 30 s | 0.6 |
| 30 – 60 s | 0.4 |
| > 60 s | 0.2 |

If `_latency_ms = 0` (no data), a neutral 0.75 is returned. The latency timer starts when `run()` begins and stops when `_node_verify` finishes — it captures the full plan + execute + verify cycle.

---

#### AgentScorer Detail (`app/evals/agent_score.py`)

**Tool success rate** defaults to 0.7 (neutral) when no tool calls exist. When calls exist:

```python
failed = sum(1 for tc in all_calls
             if isinstance(tc, dict) and not tc.get("success", True))
score = max(0.0, 1.0 - failed / len(all_calls))
```

**Grounding** penalises each ungrounded claim at −0.20:

```python
score = max(0.0, 1.0 - len(state.ungrounded_claims) * 0.2)
```

**Citation quality** checks `state.provenance` for average confidence. If no provenance is recorded but the `cited_answer` contains bracket references (`[1]`), it returns 0.8 (citations present but unverified). If no citations at all, 0.4.

---

### 2. SelfImprovementEngine (`app/evals/self_improvement_engine.py`)

`SelfImprovementEngine.decide_actions(scorecard, profile, state)` translates scorecard numbers into actionable `ImprovementDecision` objects. It only fires when `overall_score < profile.eval_config.score_threshold`.

#### The Six Action Types and Their Thresholds

| `ImprovementAction` | Trigger condition | What changes |
|---------------------|------------------|-------------|
| `UPDATE_RAG_STRATEGY` | `rag_quality < 0.5` OR `retrieval_confidence < 0.4` | RAG strategy selector told to prefer higher-confidence sources |
| `STORE_REFLEXION_LESSON` | `goal_success < 0.7` AND `verification_feedback` non-empty | Lesson persisted to `reflexion_lessons` for future plan context |
| `UPDATE_PROMPT_VARIANT` | `goal_success < 0.7` OR `tool_success_rate < 0.5` | `prompt_optimizer.record_result()` updates arm performance |
| `BLACKLIST_TOOL_PATTERN` | `tool_success_rate < 0.3` (critically low) | `ToolReliabilityStore.record()` marks tool unreliable |
| `UPDATE_MODEL_ROUTING` | `cost_efficiency < 0.3` OR `latency < 0.3` | `agent_store.update_config()` adjusts model plan |
| `CREATE_REGRESSION_CASE` | `overall_score < 0.4` | `RegressionGate` persists goal as a regression test case |

Multiple actions can be returned simultaneously. A goal with goal_success=0.4, rag_quality=0.3, tool_success_rate=0.2, and overall_score=0.3 would trigger all six actions.

**Important order:** `UPDATE_RAG_STRATEGY` is evaluated first. The full list is returned in detection order, not priority order — dispatchers iterate the list and apply all actions.

#### The ImprovementDecision dataclass

```python
@dataclass
class ImprovementDecision:
    action_type: ImprovementAction
    reason: str          # human-readable explanation (e.g., "rag_quality=0.32 below 0.5")
    metadata: dict       # action-specific data (e.g., {"current_rag_strategy": "hybrid"})
```

The `reason` field is stored in the SSE event and the audit trail, giving operators a traceable explanation for every automated config change.

---

### 3. RegressionGate (`app/evals/regression_gate.py`)

`RegressionGate` creates structured regression test cases from badly-failing goals. The regression threshold is **0.6** (`_FAILURE_THRESHOLD`):

```python
def maybe_create_regression(*, state, scorecard, profile) -> dict | None:
    if scorecard.overall_score >= self._threshold:   # 0.6 by default
        return None
    if state.status not in (GoalStatus.FAILED, GoalStatus.COMPLETE):
        return None
    return {
        "goal_id": state.goal_id,
        "goal_text": state.goal[:200],
        "tenant_id": state.tenant_ctx.tenant_id,
        "overall_score": scorecard.overall_score,
        "scores": scorecard.scores,
        "status": state.status.value,
        "improvement_suggestions": scorecard.improvement_suggestions,
        "profile_id": profile.profile_id,
    }
```

Goals that score below 0.6 are persisted via `OrchestrationPersistence.persist_regression_case()` into the `eval_scorecards` table with a `regression_case` tag. `EvalSuiteRunner` uses this table as a golden dataset for offline regression testing — each time a prompt variant is promoted or a model is switched, the suite re-runs against all tagged regression cases to verify the change does not cause regressions.

Note: `SelfImprovementEngine` triggers `CREATE_REGRESSION_CASE` at the stricter threshold of **0.4**. Goals scoring between 0.4 and 0.6 are captured by `RegressionGate` but are not forced into the improvement pipeline by `SelfImprovementEngine` — they are monitored, not immediately acted upon.

---

### 4. PromptOptimizer (`app/intelligence/prompt_optimizer.py`)

`PromptOptimizer` manages A/B prompt variant experiments per tenant. It is 397 lines and implements the full experiment lifecycle: registration → selection → result recording → statistical significance testing → auto-promotion.

#### Variant Selection: Epsilon-Greedy (70/30)

`select_variant(prompt_key, tenant_id)` uses epsilon-greedy exploration:

```python
# From app/intelligence/prompt_optimizer.py:267
if not challengers or random.random() < 0.70:
    return control or key_variants[0]   # 70%: exploit best-known variant
return random.choice(challengers)        # 30%: explore challengers
```

This is simpler and more robust than pure Thompson sampling for small variant pools. The control variant is served 70% of the time; a random challenger is served 30% of the time.

For **deterministic** variant selection (reproducible test environments), `PromptVariantSelector` from `app/context/prompt_variant_selector.py` uses a hash-based assignment: `hash(goal_id) % len(variants)`. This ensures the same goal always gets the same variant within an experiment window.

#### Integration with the Agent Loop

- `_node_plan()` calls `optimizer.select_variant("planner_prompt", tenant_id)` and stores `state.context["planner_variant_id"]` for downstream tracking.
- After `_node_verify()`, `optimizer.record_result(variant_id, scorecard.overall_score)` updates the variant's `eval_scores` list and `run_count`.
- `persist_outcome(variant_id, won=True/False, db)` writes a `win_count`/`loss_count` increment to the `prompt_variants` table (fire-and-forget async task).

#### Auto-Promotion with Mann-Whitney U Test

`maybe_promote(prompt_key, tenant_id)` runs after each `record_result` call:

1. Both control and challenger must have at least `min_runs_for_promotion = 100` runs.
2. `_is_significant(control_scores, challenger_scores)` runs a one-sided Mann-Whitney U test (`alternative="greater"`) using scipy if available, falling back to a 5% mean improvement check.
3. If the challenger's mean score is statistically higher (p < 0.05), the challenger is promoted: it becomes `is_control = True`, the old control is archived (`is_active = False`).
4. A Redis invalidation message is published via `invalidate_cache()` so all replicas reload variants from DB.

#### DB Persistence and Cross-Tenant Isolation

Variants are scoped to `tenant_id`. The `_variants` dict is `dict[tenant_id → dict[variant_id → PromptVariant]]`. The module-level `_default_optimizer` singleton uses `"global"` as a fallback scope for backward compatibility, but production code always passes `tenant_id` explicitly.

---

### 5. SelfOptimizerV2 (`app/intelligence/self_optimizer_v2.py`)

`SelfOptimizerV2` sits above `PromptOptimizer` as the Bayesian experiment manager. Called in `_node_verify`:

```python
await self_optimizer_v2.on_goal_completed(
    tenant_id=state.tenant_ctx.tenant_id,
    agent_id=state.agent_id,
    goal_id=state.goal_id,
    eval_score=scorecard.overall_score,
    cost_usd=state.context.get("total_cost_usd", 0.0),
    latency_ms=state.context.get("_latency_ms", 0.0),
)
```

Internally:
- Bayesian **Thompson sampling** maintains a Beta(α, β) distribution per experiment arm. Arm selection draws from each arm's distribution and picks the maximum — arms with high observed rewards have high α and tend to be selected more.
- `apply_suggestion()` writes a real `UPDATE agents SET config = :new_config WHERE id = :agent_id` to the `agents` table.
- `maybe_conclude_experiment()` compares the posterior distributions of all arms; if one arm's 95th percentile is above another arm's 5th percentile, the experiment is concluded.
- `_maybe_start_experiment()` is called after conclusion to automatically start the next experiment variant.

`SelfOptimizerV2` and `PromptOptimizer` are complementary: `PromptOptimizer` manages prompt text variants; `SelfOptimizerV2` manages broader agent configuration changes (model selection, tool sets, execution patterns).

---

### 6. ABTestingEngine (`app/optimization/ab_testing.py`)

`ABTestingEngine` is the persistence layer for all A/B experiments. It is a module-level singleton wired in `main.py`:

```python
# main.py (lifespan):
from app.optimization.ab_testing import ab_testing_engine
ab_testing_engine.set_db_factory(db_factory)
```

`record_result_async(experiment_id, variant_id, metric, value)` writes to the `ab_test_results` table — one row per goal, per experiment, per metric. Results are queryable via:

```
GET /analytics/ab-tests/{experiment_id}/results
GET /analytics/ab-tests/{experiment_id}/summary
```

The summary endpoint returns mean, median, p95, and statistical significance per variant, enabling operator review before manual promotion decisions.

---

### 7. The Reflexion Feedback Loop (Critical Path)

Reflexion is the mechanism by which a failed goal's diagnostic information surfaces in future plans, closing the learning loop across goal boundaries.

```
Goal fails at step 3
       │
       ▼
_node_verify: state.verification_feedback = "Query failed: column 'user_id' not found"
       │
       ▼
SelfImprovementEngine → STORE_REFLEXION_LESSON
  reason: "goal failed with actionable feedback"
  metadata: {"feedback": "Query failed: column 'user_id' not found"}
       │
       ▼
reflexion_wirer.maybe_store_async(state) → INSERT INTO reflexion_lessons
  (tenant_id, agent_id, lesson_text, goal_id, created_at)
       │
       ▼
(Next goal submitted by same tenant, same agent)
       │
       ▼
_node_plan: initial_context["_reflexion_lessons"] = store.recall(tenant_id, agent_id, limit=5)
  → ["Query failed: column 'user_id' not found — verify schema before constructing queries"]
       │
       ▼
Planner LLM system prompt includes:
  "Lessons from previous executions:
   - Query failed: column 'user_id' not found — verify schema before constructing queries"
       │
       ▼
Better plan produced: planner adds "verify_schema" step before SQL construction
```

Additionally, `episodic_memory.recall()` recalls up to 3 similar past executions (by semantic similarity of goal text), and `procedural_memory.recall()` recalls learned procedures (e.g., "when creating a Jira ticket, always check for duplicates first"). All three sources are injected as system context before the planner LLM call.

---

## Part B: Eval Systems

### 1. EvalRunner (`app/intelligence/eval_runner.py` and `app/evals/eval_runner.py`)

`EvalRunner` runs on every completed goal. It scores seven dimensions that are distinct from `RuntimeScorecard`'s nine — they focus on output quality rather than execution quality:

| Dimension | Method | Notes |
|-----------|--------|-------|
| `task_completion` | `GoalStatus.COMPLETE` check | Binary: done or not |
| `efficiency` | Iteration count vs expected_steps from profile | Lower is better |
| `accuracy` | LLM-as-judge against goal intent | Model call required |
| `safety` | Guardrail violation count from `SafetyScorer` | Shared with RuntimeScorecard |
| `coherence` | LLM-as-judge for output readability | Model call required |
| `sla` | Actual latency vs `profile.sla_ms` budget | Measured in `_latency_ms` |
| `tool_relevance` | Were tools called actually relevant to goal? | Heuristic score |

`score_and_persist()` writes to the `eval_results` table with composite key `(tenant_id, goal_id, agent_id, dimension)`. This enables aggregate queries like "what is my agent's average accuracy over the last 30 days?"

**Online vs offline:** Online evaluation runs on every goal immediately after completion. Offline evaluation (`EvalSuiteRunner`) runs a batch of dimensions against a golden dataset (built by `RegressionGate`) and is typically run before deploying a new prompt variant or model.

### 2. EvalSuiteRunner

`EvalSuiteRunner` orchestrates multiple `EvalRunner` instances against a curated dataset. It:
1. Loads regression cases from `eval_scorecards` where `tag = 'regression_case'`.
2. Re-runs each case through the agent with the new configuration.
3. Compares the new scores against the stored baseline scores.
4. Reports regressions (any dimension that drops more than 0.05) and improvements.

This is gating: a configuration change that would cause a regression on even one stored test case is flagged before promotion. Operators can override, but the evidence is explicit.

### 3. Judge Models

For `accuracy` and `coherence`, `EvalRunner` calls an LLM judge. The prompt structure:

```
System: You are an expert evaluator. Rate the following response on a scale of 0.0 to 1.0.

Goal: {goal_text}
Retrieved context: {retrieved_chunks}
Agent response: {final_answer}

Rate accuracy (does the response correctly answer the goal using the provided context?): [0.0-1.0]
Rate coherence (is the response well-structured, clear, and complete?): [0.0-1.0]
Provide one-line rationale for each score.
```

The judge model is configurable per tenant (`llm_config_store.get_judge_model(tenant_id)`), defaulting to the same provider as the executor. Using the same model as judge and executor is a potential bias source — production deployments are encouraged to use a different model family for judging.

### 4. The Full Feedback Loop

```
Goal submitted
       │
       ▼
_node_plan  ←──────────────────────────────────────────────────────────────────────┐
(planner_variant_id logged)                                                         │
       │                                                                             │ STORE_REFLEXION_LESSON
       ▼                                                                             │ UPDATE_PROMPT_VARIANT
_node_execute                                                                        │ UPDATE_MODEL_ROUTING
       │                                                                             │ BLACKLIST_TOOL_PATTERN
       ▼                                                                             │ UPDATE_RAG_STRATEGY
_node_verify                                                                         │
       │                                                                             │
       ├──► EvalRunner.score_and_persist()  → eval_results table                    │
       │                                                                             │
       ├──► RuntimeScorecard.score()  → ScorecardResult (9 dims)                    │
       │                                                                             │
       ├──► SelfImprovementEngine.decide_actions()  → [ImprovementDecision, ...] ───┘
       │
       ├──► RegressionGate.maybe_create_regression()  → eval_scorecards table
       │
       ├──► SelfOptimizerV2.on_goal_completed()  → Bayesian arm update
       │
       ├──► ABTestingEngine.record_result_async()  → ab_test_results table
       │
       └──► RuntimeSSEEmitter.eval_score_recorded()  → SSE stream
```

---

## Part C: Observability

### 1. Structured Logging (`app/observability/logging.py`)

AgentVerse uses **structlog** rather than the standard library `logging` module. This is a critical distinction for debugging: `pytest`'s `caplog` fixture captures stdlib logging but **not** structlog output. Tests that need to assert on log output must use `structlog.testing.capture_logs()` instead.

```python
from app.observability.logging import get_logger

logger = get_logger(__name__)
logger.info("goal_started", goal_id=goal_id, tenant_id=tenant_id, complexity=complexity)
logger.warning("tool_call_failed", tool=tool_name, error=str(exc), attempt=attempt)
logger.error("circuit_open", tool=tool_name, tenant_id=tenant_id)
```

`configure_logging()` is called once at startup:

```python
configure_logging(level="INFO", json_logs=True)   # production: JSON to stdout
configure_logging(level="DEBUG", json_logs=False)  # dev: human-readable ConsoleRenderer
```

Context variables bound via `structlog.contextvars.bind_contextvars(request_id=..., tenant_id=...)` in `TenantMiddleware` flow through every log line for that request without explicit passing. The bound context is cleared at request teardown via `clear_contextvars()`.

**Key structured log events by phase:**

| Phase | Event name | Key fields |
|-------|-----------|-----------|
| Request | `request_received` | `method`, `path`, `tenant_id` |
| Planning | `goal_started` | `goal_id`, `tenant_id`, `complexity` |
| Planning | `plan_produced` | `goal_id`, `step_count`, `planner_variant_id` |
| Execution | `step_executing` | `goal_id`, `step_index`, `step_description` |
| Execution | `tool_call_attempted` | `tool_name`, `connector`, `goal_id` |
| Execution | `tool_call_succeeded` | `tool_name`, `duration_ms`, `goal_id` |
| Execution | `tool_call_failed` | `tool_name`, `error`, `attempt`, `goal_id` |
| Execution | `circuit_open` | `tool_name`, `tenant_id`, `failure_count` |
| Verification | `verification_result` | `goal_id`, `success`, `retry`, `feedback_preview` |
| Scoring | `scorecard_computed` | `goal_id`, `overall_score`, `scores` (9 dims) |
| Improvement | `self_improvement_dispatched` | `goal_id`, `actions` (list) |
| Completion | `goal_completed` | `goal_id`, `iterations`, `total_cost_usd` |
| Failure | `goal_failed` | `goal_id`, `reason`, `iterations` |

---

### 2. Prometheus Metrics (`app/observability/metrics.py`)

All metrics are in a dedicated `CollectorRegistry` (not the global Prometheus default) for test isolation. Namespace: `agentverse_`. 

**Label cardinality control:** Raw strings are never used as label values. All labels are normalised to bounded sets before recording. For example, a tool name `"atlassian_create_issue"` maps to the label bucket `"jira"`. This prevents unbounded label cardinality that would OOM the Prometheus server.

#### Core Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `agentverse_goal_duration_seconds` | Histogram | `status`, `priority` | End-to-end goal duration |
| `agentverse_goal_total` | Counter | `status`, `priority` | Goals by terminal status |
| `agentverse_tool_call_total` | Counter | `tool`, `connector`, `status` | Tool invocations |
| `agentverse_tool_call_duration_seconds` | Histogram | `tool`, `connector`, `status` | Tool execution latency |
| `agentverse_queue_depth` | Gauge | `queue` | Current Celery queue depth |
| `agentverse_llm_tokens_total` | Counter | `provider`, `model`, `type` | LLM token consumption |
| `agentverse_cost_usd_total` | Counter | `scope` | Estimated cost by scope |
| `agentverse_approval_wait_seconds` | Histogram | — | HITL approval wait time |
| `agentverse_plan_duration_seconds` | Histogram | `iteration` | Planning phase latency |
| `agentverse_verify_duration_seconds` | Histogram | — | Verification phase latency |
| `agentverse_queue_wait_seconds` | Histogram | `priority` | Submission-to-execution latency |
| `agentverse_schedule_fire_total` | Counter | `status` | Scheduled trigger events |
| `agentverse_desired_workers` | Gauge | `plan` | Autoscaling signal (KEDA/HPA) |
| `agentverse_prompt_tokens_saved_total` | Counter | — | Tokens saved by RAG compression |

#### Dynamic Orchestration Metrics

| Metric | Labels | Emitted from |
|--------|--------|-------------|
| `agentverse_orchestration_profile_built_total` | `complexity`, `risk`, `tenant_plan` | `RuntimeProfileBuilder.build_with_trace()` |
| `agentverse_orchestration_profile_latency_ms` | — | `RuntimeProfileBuilder.build_with_trace()` |
| `agentverse_orchestration_pattern_selected_total` | `pattern_id`, `category` | `PatternSelector.select()` |
| `agentverse_orchestration_rag_strategy_total` | `strategy` | `RAGStrategySelector.select()` |
| `agentverse_orchestration_readiness_gate_blocked_total` | `blocking_dep` | `ReadinessGate.check()` |

#### Label Normalization Examples

```python
# Status aliases:
"executing" → "started"
"completed" → "complete"
"pending_approval" → "approval_required"
"unreachable" → "error"

# Priority aliases:
"background" → "low"
"critical" → "urgent"

# Provider aliases:
"azure" → "azure_openai"
"azure-openai" → "azure_openai"
"ollama" → "local"

# Model hints (substring matching):
("claude" AND "haiku") → "claude-haiku"
("gpt-4o" AND NOT "mini") → "gpt-4o"
```

#### Recording Helpers

The module provides convenience functions wrapping label normalisation:

```python
from app.observability.metrics import (
    record_goal_duration,     # record_goal_duration("complete", 45.2, priority="high")
    record_tool_call,         # record_tool_call("jira_create", "jira", "success", 1.2)
    record_llm_tokens,        # record_llm_tokens("anthropic", "claude-3-5-sonnet", "prompt", 1500)
    record_cost_usd,          # record_cost_usd("goal", 0.0045)
    record_queue_wait,        # record_queue_wait("high", 2.3)
    record_approval_wait,     # record_approval_wait(180.0)
)
```

The `track_tool_call` async context manager combines success/failure recording with duration:

```python
async with track_tool_call(tool_name="jira_create_issue", connector_name="jira"):
    result = await mcp_client.call_tool("jira_create_issue", args)
# Records tool_call_total + tool_call_duration_seconds on exit (success or failure)
```

#### Scrape Endpoint

```
GET /metrics
Content-Type: text/plain; version=0.0.4
```

`render_metrics()` returns `(bytes_body, content_type_string)` from `generate_latest(REGISTRY)`.

---

### 3. OpenTelemetry Tracing (`app/observability/tracing.py`)

`configure_tracing(service_name, otlp_endpoint)` is called at startup from `lifespan()`:

- **With `OTEL_EXPORTER_OTLP_ENDPOINT` set**: `OTLPSpanExporter` (gRPC, insecure) with `BatchSpanProcessor` exports to Jaeger, Tempo, or an OTel Collector. Errors during setup fall back to in-process mode with a warning log.
- **Without an endpoint**: `InMemorySpanExporter` with `SimpleSpanProcessor` stores spans in-process. These are accessible via `get_recent_spans(limit=100)` and the `GET /debug/spans` replay API. This is always active in local dev and test environments.

```python
from app.observability.tracing import get_tracer

tracer = get_tracer("app.agent.graph")

with tracer.start_as_current_span("agent.plan") as span:
    span.set_attribute("goal_id", state.goal_id)
    span.set_attribute("tenant_id", state.tenant_ctx.tenant_id)
    span.set_attribute("iteration", state.iterations)
    plan = await llm.call(planner_prompt)
    span.set_attribute("step_count", len(plan.steps))
```

`get_tracer()` returns a `_NoOpTracer` when OpenTelemetry is unavailable. This is a silent no-op — no import errors, no runtime crashes. This makes tracing instrumentation safe to add throughout the codebase without adding `opentelemetry` as a hard dependency.

#### Instrumented Spans

| Span name | Line (approx) | Key attributes |
|-----------|--------------|----------------|
| `goal.run` | 3382 | `goal_id`, `tenant_id`, `status` |
| `agent.plan` | 1102 | `goal_id`, `iteration`, `step_count` |
| `agent.execute` | 1377 | `goal_id`, `step_index`, `step_description` |
| `agent.tool_call` | 2465 | `tool_name`, `connector`, `success` |
| `agent.verify` | 2895 | `goal_id`, `success`, `retry` |

The `goal.run` span is the root span; all others are children. Distributed traces link to structured logs via the `goal_id` attribute — search Jaeger for `goal_id=<id>` to see the full execution trace.

---

### 4. RuntimeSSEEmitter (`app/observability/runtime_decision_trace.py`)

`RuntimeSSEEmitter` produces real-time orchestration transparency. Every significant decision made before or during a goal execution is emitted as a structured SSE event.

#### The Nine Event Types

| Event type constant | Enum value | Fires when |
|--------------------|-----------|-----------|
| `RAG_STRATEGY_SELECTED` | `rag_strategy_selected` | Always — at profile build time |
| `MODEL_ROUTE_SELECTED` | `model_route_selected` | Always — at profile build time |
| `PATTERN_ASSEMBLED` | `pattern_assembled` | `DYNAMIC_ORCHESTRATION=true` |
| `EVAL_SCORE_RECORDED` | `eval_score_recorded` | `DYNAMIC_ORCHESTRATION=true`, after `_node_verify` |
| `RUNTIME_PROFILE_SELECTED` | `runtime_profile_selected` | `DYNAMIC_ORCHESTRATION=true` |
| `GUARDRAIL_PROFILE_SELECTED` | `guardrail_profile_selected` | `DYNAMIC_ORCHESTRATION=true` |
| `SELF_IMPROVEMENT_SUGGESTED` | `self_improvement_suggested` | After `SelfImprovementEngine` dispatch |
| `CHUNKING_STRATEGY_SELECTED` | `chunking_strategy_selected` | At ingestion — `ChunkingStrategySelector` call |
| `EMBEDDING_STRATEGY_SELECTED` | `embedding_strategy_selected` | At embedding — `EmbeddingRouter` call |

#### Example SSE Event Payloads

```json
// rag_strategy_selected — always fires
{"type": "rag_strategy_selected", "goal_id": "g_abc123",
 "strategy": "hybrid", "sources": ["knowledge_base", "web"], "reranker": "cross-encoder"}

// eval_score_recorded — after every verification
{"type": "eval_score_recorded", "goal_id": "g_abc123",
 "overall_score": 0.847,
 "scores": {
   "goal_success": 1.0, "rag_quality": 0.75, "safety": 1.0, "latency": 0.8,
   "cost_efficiency": 1.0, "grounding": 0.8, "citation_quality": 0.7,
   "retrieval_confidence": 0.6, "tool_success_rate": 0.857
 }}

// self_improvement_suggested — after improvement dispatch
{"type": "self_improvement_suggested", "goal_id": "g_abc123",
 "suggestions": ["Consider switching RAG strategy — low retrieval confidence"]}
```

#### Emit Path

```
RuntimeSSEEmitter.emit(event_dict)
  → event_callback(event_dict)      ← registered in GoalService when goal starts
  → GoalService._dispatch_event()
  → Redis PUBLISH goal_events:{goal_id}   (or in-memory queue in dev)
  → GET /goals/{goal_id}/stream
      → text/event-stream response to browser/SDK
```

Events are consumable from the Python SDK:

```python
for event in client.goals.stream("g_abc123"):
    if event["type"] == "eval_score_recorded":
        print(f"Scored: {event['overall_score']:.3f}")
    elif event["type"] == "self_improvement_suggested":
        print(f"Improvement: {event['suggestions']}")
```

---

### 5. Cost Breakdown (`app/observability/cost_breakdown.py`)

`GoalCostBreakdown` tracks per-role token usage and cost within a single goal execution. It lives in an in-process registry `_goal_breakdowns: dict[goal_id → GoalCostBreakdown]`.

```python
# Called immediately after each LLM response:
from app.observability.cost_breakdown import record_role_cost

record_role_cost(
    goal_id=state.goal_id,
    role="executor",         # "planner", "executor", or "verifier"
    model="gpt-4o-mini",
    input_tok=8400,
    output_tok=2100,
    cost=0.00820,
)
```

Entries for the same `(role, model)` pair are accumulated. After goal completion, `finalize_breakdown(goal_id)` removes the entry from the registry and returns:

```json
{
  "goal_id": "g_abc123",
  "total_cost_usd": 0.01240,
  "roles": [
    {"role": "planner",  "model": "gpt-4o",       "input_tokens": 1200,  "output_tokens": 450,  "cost_usd": 0.002850, "calls": 1},
    {"role": "executor", "model": "gpt-4o-mini",  "input_tokens": 8400,  "output_tokens": 2100, "cost_usd": 0.008200, "calls": 7},
    {"role": "verifier", "model": "claude-haiku",  "input_tokens": 1800,  "output_tokens": 320,  "cost_usd": 0.001350, "calls": 3}
  ]
}
```

Exposed via `GET /analytics/goals/{goal_id}/cost`. High executor cost typically indicates many tool calls or large tool outputs. High planner cost indicates complex goal decomposition or over-long system prompts.

---

### 6. End-to-End Debugging Workflow

When a goal behaves unexpectedly, follow this sequence:

**Step 1 — Get the `goal_id`** from the API response or SSE connection.

**Step 2 — Watch the SSE stream** (or replay it):
```
GET /goals/{goal_id}/stream
```
Look for `eval_score_recorded` to see which dimension failed, and `self_improvement_suggested` to see what the system decided to do about it.

**Step 3 — Filter structured logs** by `goal_id`:
```bash
grep '"goal_id":"g_abc123"' app.log | jq '{event: .event, tool: .tool_name, error: .error}'
```
The `tool_call_failed` events with `error` fields reveal which tool and which error class caused failures.

**Step 4 — Check OTel spans** in Jaeger by attribute `goal.id = g_abc123`. The waterfall shows which phase consumed the most time and where exceptions were recorded.

**Step 5 — Check cost breakdown**:
```
GET /analytics/goals/{goal_id}/cost
```
High `calls` on the executor role with short `output_tokens` often indicates repeated tool failures driving retries.

**Step 6 — Check circuit breaker state** if tools keep failing:
```
GET /debug/circuit-breakers/{tenant_id}
```
An OPEN circuit explains tool unavailability that appears as repeated tool call failures.

**Step 7 — Cross-reference the audit trail**:
```
GET /governance/audit?goal_id={goal_id}
```
Shows the complete sequence of actions taken on behalf of the tenant, including any guardrail violations or HITL escalations.

---

## Part D: Operational Reference

### Reading a ScorecardResult — Interpretation Guide

A `ScorecardResult` communicates not just scores but a prioritised diagnosis. Use this table when reviewing scorecard output in logs or the analytics API:

| Dimension | Score range | Interpretation | Action |
|-----------|------------|---------------|--------|
| `goal_success` | < 0.3 | Goal failed multiple times | Check `verification_feedback` log; consider goal decomposition |
| `goal_success` | 0.5 | WAITING_HUMAN (HITL pause) | Normal; no action unless approval is stuck |
| `goal_success` | 0.7–0.99 | Completed but took many iterations | Check `iterations` vs expected; consider prompt refinement |
| `rag_quality` | 0.1 | No knowledge available | Populate knowledge base for this topic |
| `rag_quality` | 0.3 | Answered from parametric memory | Enable RAG or add relevant documents |
| `rag_quality` | 0.5 | Memory/unknown source | Normal for procedural tasks; acceptable |
| `rag_quality` | > 0.8 | Good KB retrieval | No action |
| `safety` | < 0.8 | Multiple guardrail violations | Review guardrail logs; tighten policy |
| `safety` | 0.7 | One HITL bypass | Investigate; may indicate policy gap |
| `grounding` | < 0.6 | 2+ ungrounded claims | Switch to knowledge_base source; enable SelfRAG |
| `cost_efficiency` | < 0.3 | Severely over budget | Reduce `max_cost_usd` or switch to cheaper model |
| `tool_success_rate` | < 0.5 | Many tool failures | Check circuit breaker state; verify connector health |
| `retrieval_confidence` | < 0.4 | Low relevance retrieved | CRAG should have fired; verify CRAG is enabled |

The `improvement_suggestions` list on `ScorecardResult` provides pre-computed human-readable guidance for the most significant failures.

---

### ScorecardResult Persistence and Querying

`ScorecardResult.to_dict()` produces a JSON-serializable dict:

```json
{
  "goal_id": "g_abc123",
  "scores": {
    "goal_success": 0.970,
    "rag_quality": 0.850,
    "safety": 1.000,
    "latency": 0.800,
    "cost_efficiency": 1.000,
    "grounding": 1.000,
    "citation_quality": 0.780,
    "retrieval_confidence": 0.820,
    "tool_success_rate": 0.857
  },
  "overall_score": 0.921,
  "improvement_suggestions": []
}
```

Persisted by `OrchestrationPersistence.persist_scorecard()` to `eval_scorecards` table with columns: `goal_id`, `tenant_id`, `agent_id`, `scores` (JSONB), `overall_score`, `created_at`.

Queryable via:
```
GET /analytics/agents/{agent_id}/scorecard?days=30&dimension=grounding
```

This endpoint returns per-dimension trend data (mean, p25, p75, p95) and the list of goals below the 25th percentile for any dimension — useful for identifying systematic weaknesses in specific agents.

---

### Improvement Action Dispatch — Implementation Details

When `SelfImprovementEngine.decide_actions()` returns a non-empty list, the dispatcher in `_node_verify` processes each action:

```python
for decision in improvement_decisions:
    if decision.action_type == ImprovementAction.UPDATE_PROMPT_VARIANT:
        # Record the result against the active variant:
        prompt_optimizer.record_result(
            state.context.get("planner_variant_id"),
            eval_score=scorecard.overall_score,
        )
        # maybe_promote runs after enough data is accumulated:
        promoted = prompt_optimizer.maybe_promote(
            "planner_prompt",
            tenant_id=state.tenant_ctx.tenant_id,
        )
        if promoted:
            logger.info("prompt_variant_promoted",
                       variant_id=promoted.variant_id,
                       mean_score=statistics.mean(promoted.eval_scores))

    elif decision.action_type == ImprovementAction.BLACKLIST_TOOL_PATTERN:
        await tool_reliability_store.record_failure(
            tool_name=failed_tool,
            tenant_id=state.tenant_ctx.tenant_id,
            reason=decision.reason,
        )

    elif decision.action_type == ImprovementAction.STORE_REFLEXION_LESSON:
        await reflexion_wirer.maybe_store_async(state)

    elif decision.action_type == ImprovementAction.CREATE_REGRESSION_CASE:
        case = regression_gate.maybe_create_regression(
            state=state, scorecard=scorecard, profile=profile
        )
        if case:
            await persistence.persist_regression_case(case)
```

The `self_improvement_suggested` SSE event is emitted after all dispatches complete, carrying the list of `improvement_suggestions` strings from `ScorecardResult`.

---

### Configuring the Score Threshold

`profile.eval_config.score_threshold` is the minimum `overall_score` that must be reached before `SelfImprovementEngine` activates. It is set in `GoalRuntimeProfile` during profile build:

| Plan tier | Default score threshold |
|-----------|------------------------|
| `free` | 0.6 — any score below this triggers improvement |
| `starter` | 0.65 |
| `professional` | 0.70 |
| `enterprise` | Configurable (default 0.70) |

Higher thresholds mean more goals trigger improvement actions, leading to more aggressive self-optimisation but also more DB writes and LLM calls for model updates. Operators can override the threshold per agent via `PATCH /agents/{agent_id}/config`.

---

### Prometheus Alert Rules for the Improvement Loop

Recommended alert rules to monitor the eval and improvement system:

```yaml
groups:
  - name: agentverse_evals
    rules:
      # Overall score degradation across the fleet:
      - alert: LowOverallScore
        expr: |
          histogram_quantile(0.25,
            rate(agentverse_eval_score_bucket[30m])) < 0.5
        for: 10m
        severity: warning
        annotations:
          summary: "25th percentile eval score < 0.5"

      # Reflexion lessons accumulating faster than normal (many failures):
      - alert: HighReflexionRate
        expr: rate(agentverse_reflexion_lessons_total[10m]) > 0.5
        for: 5m
        severity: warning
        annotations:
          summary: "Reflexion lessons accumulating — agent struggling"

      # High A/B experiment failure rate:
      - alert: ABTestRegressionDetected
        expr: agentverse_ab_test_regression_total > 0
        for: 0m
        severity: critical
        annotations:
          summary: "A/B test detected regression in challenger variant"
```

---

### EvalRunner vs RuntimeScorecard — When Each is Used

A common confusion: the platform has two scoring systems. They serve different purposes and both run on every completed goal:

| Property | EvalRunner | RuntimeScorecard |
|----------|-----------|-----------------|
| Focus | Output quality | Execution quality |
| Dimensions | 7 (output-centric) | 9 (process-centric) |
| LLM judge calls | Yes (accuracy, coherence) | No (all algorithmic) |
| DB table | `eval_results` | `eval_scorecards` |
| Feeds improvement | No (read-only analytics) | Yes (feeds SelfImprovementEngine) |
| Offline use | EvalSuiteRunner (golden dataset) | RuntimeScorecard + RegressionGate |
| API endpoint | `GET /analytics/agents/{id}/eval` | `GET /analytics/goals/{id}/scorecard` |

The operational decision tree:
- "Was this goal's output correct and readable?" → `EvalRunner` accuracy/coherence dimensions
- "Why did this goal fail, and what automated action was taken?" → `RuntimeScorecard` + `SelfImprovementEngine`
- "Has this agent's quality improved after my configuration change?" → `EvalSuiteRunner` against regression cases
- "What did this cost and is it within budget?" → `GoalCostBreakdown` + `cost_efficiency` dimension
