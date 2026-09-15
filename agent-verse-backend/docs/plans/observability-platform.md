# World-Class Observability for AgentVerse — Master Plan

> One goal: **every feature, big to small, and every call is traceable end-to-end.**
> From an inbound request (API / WhatsApp / Telegram) → chat turn → goal → each LangGraph
> node → every LLM call (prompt, tokens, cost, latency) → every tool / MCP / connector call →
> RAG retrieval → DB query → HITL / guardrail / cost decision — all stitched into **one trace**,
> visible LLM-natively (Langfuse) and as distributed traces (OTel → Jaeger/Tempo), correlated
> to metrics (Prometheus/Grafana) and logs. Deployed as **infra services** (docker-compose for dev).

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done (tested).

---

## 0. Where we are (grounding — verified in code)

**Already strong (keep, extend):**
- OTel bootstrap `app/observability/tracing.py` (OTLP gRPC exporter → collector); dev infra
  ships **otel-collector + Jaeger + Prometheus + Grafana** (`infra/docker-compose.yml`,
  `infra/otel/otel-collector-config.yaml`, `infra/prometheus/*`, `infra/grafana/*`).
- Prometheus metrics `app/observability/metrics.py` (goal/tool/llm-token/cost/queue/phase/
  strategy/coordination) at `GET /metrics`.
- Cost roll-up per role/model/goal `app/observability/cost_breakdown.py` (`GET /goals/{id}/cost-metrics`).
- SLO burn-rate `slo_tracker.py`, health `health.py`, alerts `alert_router.py`, structured logs
  `logging.py`, DB-computed p50/p95/p99 `app/api/observability.py`.
- Live decision SSE `runtime_decision_trace.py` (routing / RAG strategy / pattern / guardrail).
- In-process sub-agent trace propagation works (`executor_mixin.py:204`).

**The gaps that block "see how my agents work" (verified):**
1. **No per-LLM-call observation.** Providers emit only aggregate Prometheus counters
   (`anthropic_provider.py:128`, `openai_compatible.py:308`) — no span/record with prompt,
   completion, model, tokens, latency, cost tied to a goal/step/trace. **This is the Langfuse
   "generation" primitive and it does not exist.** No GenAI semantic conventions anywhere.
2. **Trace backbone is severed.** `tracing.py` never calls `FastAPIInstrumentor` (its docstring
   lies); no HTTPX/asyncpg/SQLAlchemy/Celery instrumentation; **no trace-context propagation into
   Celery** (`scaling/tasks.py`) — which is where goals actually run. So the request → worker →
   goal.run span tree is broken. Only in-process sub-agent propagation works.
3. **Decision/step trace is live-only or coarse.** `AgentState.run_trace` is a `None` placeholder;
   `AgentRunTrace` API is in-memory + empty; `decision_traces` is one reasoning string per goal
   (`graph.py:946`). No persisted, queryable step timeline.
4. **Spans not durable / not per-tenant** (`get_recent_spans` is a process buffer, empty when
   OTLP export is on; `analytics.py:212` unscoped).
5. **Logs not correlated to traces** (no trace_id processor in `logging.py`); Prometheus has no
   tenant/exact-model labels (by design), so slicing is ad-hoc SQL.
6. **No LLM-observability backend** — zero Langfuse/LangSmith/Phoenix/OpenLLMetry references.
   Clean field to add one.

---

## 1. Architecture — one instrumentation, three backends

**Principle: instrument once (OpenTelemetry, with GenAI semantic conventions), fan out.**

```
  App (FastAPI + Celery + AgentGraph + providers + tools + RAG + DB)
        │  OTLP (traces + metrics + logs), W3C tracecontext propagated everywhere
        ▼
   otel-collector  ──►  Jaeger / Tempo        (distributed traces, span tree)
        ├───────────►  Langfuse (OTLP ingest) (LLM-native: generations, sessions, evals, cost)
        ├───────────►  Prometheus (metrics + exemplars → link metric to trace)
        └───────────►  Loki (logs, trace-correlated)     [add Loki for the 3rd pillar]
   Grafana  ──►  unified dashboards over Prometheus + Tempo + Loki (+ Langfuse links)
```

- **Langfuse** (self-hosted, infra service) is the LLM-observability product surface: trace tree,
  per-generation prompt/completion/token/cost/latency, sessions (= conversation/goal), users
  (= tenant/principal), prompt management, evals/scores. Langfuse ingests **via OpenTelemetry
  OTLP**, so our single GenAI span layer feeds it *and* Jaeger with no double instrumentation.
- **Jaeger/Tempo** is the engineering trace view (full span tree incl. DB/HTTP/Celery).
- **Prometheus/Grafana** is the aggregate RED/USE + SLO view, with **exemplars** linking a metric
  spike to an exact trace.
- **Loki** (new) gives log↔trace correlation in one place (optional; Redis log store stays).

Decision: **do not** adopt a vendor LLM SDK (LangSmith/Helicone) — stay OTel-native so the same
spans serve all backends and we keep multi-tenant control. Use `openinference`/`openllmetry`
GenAI conventions as the attribute vocabulary.

---

## 2. Traceability coverage matrix (the acceptance target — every call, big → small)

Every row must produce a span that is a child of the goal-run trace and carries the standard
attributes (tenant.id, principal.id, goal.id, conversation.id, step.id where applicable).

| Layer (big → small) | Span name | Key attributes | Phase |
|---|---|---|---|
| Inbound HTTP request | `http.server` (auto) | route, status, `http.*` | P0 |
| Inbound channel (WA/TG/webhook) | `chat.inbound` | channel, principal | P0 |
| Chat turn | `chat.turn` | intent, session | P0 |
| Goal run | `agentverse.goal.run` (exists) | goal.id, tenant, org | P0 (fix propagation) |
| Celery task boundary | `celery.run_goal` | goal.id (context extracted) | P0 |
| LangGraph node (plan/execute/verify/rag/route) | `agentverse.<node>` (partial) | node, iteration | P1 |
| **LLM call (generation)** | `gen_ai.<role>` | gen_ai.system/request.model, prompt, completion, input/output tokens, cost, latency, cache_hit | **P1 (core)** |
| Tool / MCP / connector call | `agentverse.tool.call` (exists) | tool, server, args_hash, status, risk | P1 (enrich) |
| RAG retrieval | `rag.search` (exists) | strategy, top_k, citations, latency | P1 (enrich) |
| DB query | `db.query` (auto: SQLAlchemy/asyncpg) | statement, rows, latency | P0 |
| Outbound HTTP (connectors, webhooks) | `http.client` (auto: HTTPX) | host, status | P0 |
| HITL / guardrail / cost decision | `agentverse.decision.*` | rule, verdict, wait | P3 |
| Sub-agent (goal-tree/org-team) | child trace (propagation exists) | parent goal | P0 (verify) |
| Scheduled/trigger fire → delayed goal | `trigger.fire` → linked goal trace | trigger.id | P0/P3 |

If any row lacks a span, e2e traceability fails — the §9 test battery asserts each one.

---

## 3. Phase 0 — Trace backbone & propagation (make EVERYTHING e2e-traceable) `[foundational]`

**Goal:** a single trace spans inbound → worker → sub-agent → LLM → tool → DB. This alone
delivers the user's core ask; LLM detail (P1) then hangs off a correct tree.

- [ ] Auto-instrument: `FastAPIInstrumentor.instrument_app` (real, fix the docstring lie),
  `HTTPXClientInstrumentor`, `SQLAlchemyInstrumentor`/`AsyncPGInstrumentor`, `RedisInstrumentor`,
  `CeleryInstrumentor` — wired in `tracing.py`/`main.py`. Add the OTel deps to `pyproject.toml`.
- [ ] **Celery propagation** (the critical break): inject W3C `traceparent` into the task
  headers on `.delay()`/`.apply_async` in `scaling/tasks.py` submit paths and extract+attach at
  task start, so `celery.run_goal` is a child of the submitting request span.
- [ ] **SSE/stream propagation:** carry trace ids on chat/goal SSE so the frontend can deep-link a
  message → its trace.
- [ ] **Channel propagation:** start `chat.inbound`/`chat.turn` spans at the gateway so WhatsApp/
  Telegram turns are traced identically to web.
- [ ] **Log↔trace correlation:** structlog processor stamping `trace_id`/`span_id` from the active
  span (`logging.py`); collector routes logs to Loki keyed by trace id.
- [ ] Standard span attributes helper: `tenant.id`, `principal.id`, `goal.id`, `conversation.id`,
  `step.id` set once on the root and inherited (baggage).
- [ ] **Sampling & PII:** tail-based sampling in the collector (keep all errors + slow + sampled
  success); a span processor that redacts prompts/outputs using existing `sanitization.py` before
  export (configurable full-capture per tenant/plan).

Tests: span-tree unit tests (a submitted goal produces request→celery→goal.run→node spans with a
shared trace id); Celery header inject/extract round-trip; log record carries the active trace id.

---

## 4. Phase 1 — The GenAI "generation" span (LLM-native observability core)

**Goal:** one span per LLM call with the full Langfuse/LangSmith detail, as a child of the current
node span — the thing that's entirely missing today.

- [ ] A single instrumentation seam at `app/providers/base.py` (wrap `complete`/`stream_tokens`/
  `embed`) so **every** provider (anthropic, openai_compatible, voyage, gemini, fake) is covered
  once. Emit `gen_ai.*` semantic-convention attributes: `gen_ai.system`, `gen_ai.request.model`,
  `gen_ai.request.temperature/max_tokens`, `gen_ai.usage.input_tokens/output_tokens`,
  `gen_ai.response.finish_reason`, plus prompt & completion as span events (redaction-gated),
  computed `gen_ai.usage.cost_usd`, latency, `cache_hit`, role (planner/executor/verifier).
- [ ] Tie each generation to goal.id/step.id/role so Langfuse groups them under the goal trace
  (session = conversation/goal, user = principal/tenant).
- [ ] Keep the existing `record_llm_tokens`/`record_role_cost` (metrics + budget) — the span is
  additive, and reconcile so cost is computed once.
- [ ] Streaming: measure TTFT (time-to-first-token) and inter-token latency; emit as span attrs.
- [ ] Enrich existing spans: `agentverse.tool.call` (args hash, result size, risk, status),
  `rag.search` (strategy, candidates, citations, rerank latency, binary-prefilter hit).

Tests: a FakeProvider call emits a `gen_ai.*` span with tokens/cost/latency under the active node
span; redaction on/off; cost computed once (no double count vs metrics).

---

## 5. Phase 2 — Langfuse as an infra service

**Goal:** a self-hosted LLM-observability product plane, fed by the P1 spans over OTLP.

- [ ] `infra/docker-compose.yml` (dev) + `docker-compose.prod.yml`: add Langfuse v3 stack —
  `langfuse-web`, `langfuse-worker`, its `postgres`, `clickhouse`, `redis`, `minio` (object store),
  networked to the collector. Health-checked, volumes, `.env` for keys.
- [ ] `otel-collector-config.yaml`: add a Langfuse OTLP exporter alongside Jaeger/Prometheus so the
  same spans fan out. Tail-sampling policy applied before both.
- [ ] Provisioning: seed a Langfuse project + API keys via env; wire `LANGFUSE_HOST/PUBLIC_KEY/
  SECRET_KEY` (dev defaults, prod secrets). Feature-flag `observability_langfuse_enabled`.
- [ ] Map our domain onto Langfuse: trace = goal run, session = conversation, user = principal,
  tags = tenant/org/plan/strategy, scores = eval results (wire `EvalRunner`/scorecards → Langfuse
  scores), prompts = managed prompt versions (optional, later).

Tests: an e2e_full goal run produces a Langfuse trace with ≥1 generation (asserted via Langfuse
API/OTLP capture); collector fan-out to both Jaeger and Langfuse verified.

---

## 6. Phase 3 — Persisted, queryable agent run-trace (step timeline)

**Goal:** turn the live-only decision SSE + spans into a durable, queryable per-goal timeline the
product/UI can render without depending on Jaeger/Langfuse retention.

- [ ] Real `AgentRunTrace`: populate `role_calls`, `model_selections`, `patterns_used`,
  `rag_strategy_used`, per-step plan/tool/routing/grounding from the loop (today empty at
  `agent_runtime.py:114`); persist to a DB table (RLS-scoped), not an in-memory dict.
- [ ] Persist `RuntimeSSEEmitter` decision events (routing/RAG/pattern/guardrail/eval) as trace
  rows keyed by goal + step + trace_id (today ephemeral).
- [ ] Replace the one-row `decision_traces` per goal with per-step rows; keep the replay API.
- [ ] `GET /observability/goals/{id}/trace` → the step timeline (plan → steps → tool calls → LLM
  generations with tokens/latency/cost → citations → decisions), each row carrying its `trace_id`/
  `span_id` for deep-linking into Jaeger/Langfuse.

Tests: a goal run yields a persisted timeline with N steps, each with its generations + tool calls;
survives restart; per-tenant isolation (RLS).

---

## 7. Phase 4 — Metrics completeness + exemplars

- [ ] RED for HTTP (from FastAPI instrumentation): request rate/errors/duration histogram by route.
- [ ] **Exemplars** on latency/cost histograms linking a bucket to an exact `trace_id` (Prometheus
  exemplars → Grafana → Tempo/Jaeger jump).
- [ ] Safe high-cardinality slicing: keep Prometheus low-card (current design), but add an
  aggregation job / ClickHouse (Langfuse's) for exact per-tenant/per-model cost & latency, exposed
  via `GET /observability/metrics` (extend the existing SQL path).
- [ ] Queue/worker saturation (USE), agent-loop phase histograms (exist), cache hit-rate, TTFT.

Tests: `/metrics` exposes an HTTP duration histogram; an exemplar carries a resolvable trace id.

---

## 8. Phase 5 — Product surfaces (frontend "Agent Run Inspector" + dashboards)

**Goal:** the user *sees* it — world-class UI, not just Jaeger.

- [ ] Frontend **Run Inspector** (in the chat Run panel + a dedicated observability page): live +
  historical timeline — plan → steps → tool calls → LLM generations (model, tokens, cost, TTFT,
  latency) → citations → guardrail/HITL/cost decisions; a waterfall (span timings), per-goal cost &
  latency, and a "open in Langfuse/Jaeger" deep-link. Consumes `GET .../trace` (P3) + live SSE.
- [ ] Platform dashboards: a tenant "Observability" page — RED (requests/latency/errors),
  throughput, per-model spend, agent success rate, SLO burn, top slow/expensive goals.
- [ ] Grafana provisioned dashboards (RED/USE, agent phases, cost, SLO) + Langfuse dashboards for
  LLM analytics; single-sign-in behind the app (Grafana already reverse-proxied at `/grafana`).

Tests: Playwright — a completed goal renders a timeline with generation rows + working deep-links;
responsive + dark-mode.

---

## 9. Phase 6 — SLO, alerting, retention, security, cost-of-observability

- [ ] Seed SLO + alert rules on startup (today in-memory, lost on restart); error-budget policy;
  alerts carry a trace deep-link.
- [ ] Retention & sampling tiers (hot traces N days in Jaeger/Langfuse, aggregates long-term);
  per-plan capture level (free = sampled + redacted, enterprise = full-capture opt-in).
- [ ] Multi-tenant access control on trace/observability APIs (RLS + per-tenant Langfuse project or
  tag-scoped access); PII redaction verified in exported spans.
- [ ] Guard the observability overhead (async export, batch, tail-sample) — assert < X% latency.

---

## 10. Infra services (deploy target)

Dev (`infra/docker-compose.yml`): **existing** otel-collector, jaeger, prometheus, grafana **+ new**
langfuse-web, langfuse-worker, clickhouse, langfuse-postgres, minio, (optional) loki + tempo.
Prod (`docker-compose.prod.yml` / Helm later): same, with secrets, persistence, resource limits,
network policies; collector as the single ingest with tail-sampling + fan-out.
All are **infra services** — the app only speaks OTLP to the collector and never depends on a
specific backend being up (fail-open export).

---

## 11. End-to-end traceability acceptance battery (must all pass)

1. **One trace, all layers:** submit a goal via HTTP → the trace in Jaeger shows
   `http.server → celery.run_goal → agentverse.goal.run → plan → gen_ai.planner → execute →
   agentverse.tool.call → db.query → verify → gen_ai.verifier → goal_complete`, one shared trace id.
2. **LLM detail:** the same run in Langfuse shows a session with generations carrying model,
   prompt, completion, input/output tokens, cost, latency, and role — grouped under the goal.
3. **Channel parity:** the same goal from a Telegram webhook produces an equivalent trace rooted at
   `chat.inbound`.
4. **Async/scheduled:** a scheduled trigger fires a goal whose trace **links back** to the creating
   request/trace (span link), and delivery-back is traced.
5. **Sub-agent:** an org-team/goal-tree mission shows child traces under the parent goal.
6. **Metrics↔trace:** a latency-spike exemplar in Grafana jumps to the exact slow trace.
7. **Logs↔trace:** a log line for the goal carries the run's trace_id and is findable in Loki.
8. **Cost reconciliation:** per-goal cost in Langfuse == `GET /goals/{id}/cost-metrics` == the sum
   of generation costs (single source of truth, no double count).
9. **Tenant isolation:** tenant A cannot read tenant B's traces via any observability API.
10. **Resilience:** with the collector/Langfuse down, the app still serves requests (export
    fails open) and buffers/drops per policy.

---

## 12. Execution order

P0 (backbone + propagation) → P1 (GenAI generation spans) → P2 (Langfuse infra) →
P3 (persisted step timeline) → P4 (metrics/exemplars) → P5 (frontend inspector + dashboards) →
P6 (SLO/retention/security). Each phase: TDD, run the matching §11 scenario as its e2e gate.
Every `[x]` requires the relevant traceability-matrix (§2) rows to emit their spans.
