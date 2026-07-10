# Agent Improvement, Evals, and Observability

**Date:** 2026-07-08

## Purpose

This document explains how AgentVerse measures goal quality, observes runtime behavior, records traces, and improves agents over time.

## Improvement Loop

```text
Goal run
  -> execution trace
  -> verifier result
  -> eval scorecard
  -> memory write
  -> prompt/model optimization
  -> future goal improves
```

## EvalRunner

Implemented in `app/intelligence/eval_runner.py`.

Scores completed goals on 7 dimensions:

```text
task_completion
efficiency
accuracy
safety
coherence
sla
```

### Score Meaning

| Dimension | Meaning |
|---|---|
| task_completion | 1.0 if goal status is COMPLETE |
| efficiency | fewer iterations and lower cost score higher |
| accuracy | verifier success and feedback quality |
| safety | no denied/unsafe events |
| coherence | logical step-to-step consistency |
| SLA | completed within timeout/SLA |
| tool_relevance | right tools, low failed/redundant calls |

## Tool Relevance Score

The system checks:

```text
success_rate of tool calls
average calls per step
failed tool calls
```

Formula:

```text
tool_relevance = 0.6 * success_rate + 0.4 * efficiency
```

## Retrieval Evals

Implemented in `app/rag/evaluation.py`.

Metrics:

```text
Precision@K
Recall@K
MRR
Overall score
```

Used to evaluate knowledge collections and retrieval quality.

## Prompt Optimizer

Implemented in `app/intelligence/prompt_optimizer.py`.

Supports:

- prompt variants
- tenant-scoped A/B tests
- run count tracking
- eval score tracking
- automatic promotion of winning variant
- archive losers instead of deleting them

Workflow:

```text
PromptVariant A and B registered
  -> each goal tagged with variant_id
  -> EvalRunner records score
  -> after min runs, optimizer compares variants
  -> winner promoted to active
```

## Model Optimization

Model routing optimizes quality/cost/latency:

```text
Planner: strong model
Executor: cheaper tool model
Verifier: cheapest structured model
Embedder: dedicated embedding model
```

Future path: `ModelOrchestrator` can downgrade models when budget spent ratio crosses thresholds:

```text
budget < 75% -> high/medium models
budget > 75% -> downgrade to medium
budget > 90% -> downgrade to low-cost models
```

## Observability Logging

Implemented in `app/observability/logging.py`.

Uses `structlog` with context propagation:

```text
tenant_id
request_id
tool_name
server_id
status
latency
```

## Metrics

Implemented in `app/observability/metrics.py`.

Metric categories:

```text
goal counters
tool call counters
LLM token counters
latency histograms
cost counters
queue depth gauges
in-flight goal gauges
```

Examples:

```text
agentverse_goals_total{status, priority}
agentverse_tool_calls_total{tool, status}
agentverse_llm_tokens_total{provider, model, type}
agentverse_goal_duration_seconds{status, priority}
agentverse_cost_usd_total{scope}
```

## Tracing

Implemented in `app/observability/tracing.py`.

Trace hierarchy:

```text
agentverse.goal.run
  -> agentverse.rag.retrieval
  -> agentverse.plan
  -> agentverse.step.execute
       -> agentverse.tool.call
  -> agentverse.verify
```

When OTLP endpoint is configured, traces export to Jaeger/collector. Otherwise, an in-memory exporter is used for local replay/debugging.

## Cost Breakdown

Implemented in `app/observability/cost_breakdown.py`.

Tracks cost by:

```text
planner
executor
verifier
workflow
queue
```

## Runtime Decision Trace

`app/observability/runtime_decision_trace.py` records why an agent chose:

- a model
- a tool
- a plan
- a retrieval strategy
- a policy path
- a replan route

This allows post-run replay and debugging.

## RAG Trace

`app/observability/rag_trace.py` records:

- query
- retrieval strategy
- source collection
- returned chunk IDs
- scores
- retrieval legs that contributed

This is critical for diagnosing bad answers:

```text
Was answer bad because retrieval failed?
Was right chunk retrieved but ignored?
Was query expansion wrong?
Was reranker bad?
```

## Real Example: Jira Debugging Session

The observability stack exposed every failure in the Jira tool-chain:

```text
No call_tool_entry -> tool never called
call_tool_entry but no HTTP -> dispatch path broken
HTTP 400 -> JQL problem
HTTP 200 but total=0 -> credential resolution problem
tool_call_complete with real issues -> tool chain fixed
verification_done success=true -> goal complete
```

This is the value of structured events and traces.
