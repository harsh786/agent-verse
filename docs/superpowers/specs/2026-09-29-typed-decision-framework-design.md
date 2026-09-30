# Typed-Decision Framework (System-One Decision Engines) — Design Specification

> **Status note (2026-09-30, CORE-23):** this document predates the removal of unwired modules it references — `app/agent/pattern_assembler.py`, `app/agent/goal_classifier.py`, `app/agent/semantic_entropy.py`, `app/agent/errors.py`, `app/agent/patterns/dynamic_graph_assembler.py` and `app/orchestration/workflow_compatibility.py` no longer exist. The live equivalents are `app/orchestration/goal_classifier.py`, `app/orchestration/pattern_selector.py` and `app/agent/dynamic_graph.py`.

**Status:** Proposed — for review. No code merged yet.
**Date:** 2026-09-29
**Scope:** `agent-verse-backend` (primary), `agent-verse-frontend` (console + builder), `infra` (serving).
**Related specs:** `2026-09-12-generic-model-registry-design.md` (the framework registers its engines
there), `2026-08-20-hitl-gap-analysis.md` (low-confidence review path),
`2026-08-18-agentverse-world-class-engineering-specification.md` (engineering standards).

**One-line summary:** a vendor-agnostic framework that turns every narrow, closed-set decision in
AgentVerse (classify, detect, score, route, verify) into a **typed, calibrated, auditable decision**.
The framework evaluates each decision on a pluggable **System-One engine**: Laya (self-hosted),
TypeSafe Jev (hosted), an LLM emulation, or any future engine. Each decision moves from **shadow to
live** under measured gates, with **zero change to existing behaviour until a decision point is
explicitly promoted**.

---

## Table of contents

0. [Executive summary](#0-executive-summary)
1. [Problem statement (verified in the codebase)](#1-problem-statement-verified-in-the-codebase)
2. [Goals and non-goals](#2-goals-and-non-goals)
3. [Non-negotiable invariants (nothing breaks)](#3-non-negotiable-invariants-nothing-breaks)
4. [Concepts and glossary](#4-concepts-and-glossary)
5. [Architecture](#5-architecture)
6. [The engine abstraction (generic: Laya, Jev, LLM, and more)](#6-the-engine-abstraction)
7. [Engine routing](#7-engine-routing)
8. [Where questions come from (no hand-written prompts)](#8-where-questions-come-from)
9. [DecisionSpec: format, identity, versioning](#9-decisionspec)
10. [State construction, budgets and redaction](#10-state-construction-budgets-and-redaction)
11. [The linter](#11-the-linter)
12. [Modes, safety classes and the incumbent pattern](#12-modes-safety-classes-and-the-incumbent-pattern)
13. [Confidence normalisation and calibration](#13-confidence-normalisation-and-calibration)
14. [Decision log, labels and outcomes](#14-decision-log-labels-and-outcomes)
15. [Learning loop: heads, evaluation gate, promotion](#15-learning-loop)
16. [Workflow integration (dynamic workflows)](#16-workflow-integration)
17. [Agent-runtime integration](#17-agent-runtime-integration)
18. [Model-registry integration](#18-model-registry-integration)
19. [Governance, cost, privacy, compliance](#19-governance-cost-privacy-compliance)
20. [Security](#20-security)
21. [Reliability and degradation](#21-reliability-and-degradation)
22. [Observability](#22-observability)
23. [Configuration](#23-configuration)
24. [Data model](#24-data-model)
25. [API surface (additive)](#25-api-surface-additive)
26. [Frontend](#26-frontend)
27. [Deployment and serving](#27-deployment-and-serving)
28. [Use-case catalogue (complete)](#28-use-case-catalogue)
29. [Rollout plan: shadow to live](#29-rollout-plan-shadow-to-live)
30. [Implementation milestones](#30-implementation-milestones)
31. [Testing strategy](#31-testing-strategy)
32. [Risks and mitigations](#32-risks-and-mitigations)
33. [Open decisions](#33-open-decisions)
34. [Appendices](#34-appendices)

---

## 0. Executive summary

AgentVerse makes **about 70 narrow structured decisions** today. They were mapped across the agent
loop, governance, guardrails, RAG, memory, channels, RPA, evals and workflows. Almost all of them are
implemented in one of three fragile ways:

- **keyword or regex lists**, e.g. `"prod" in step` for high-risk detection;
- **"return JSON" LLM prompts** with invented, uncalibrated confidence;
- **stubs**, e.g. `NLIntentClassifier` returns `"chat_keyword"` for nearly everything.

A new class of model, the **System-One decision model**, answers exactly these questions. It
evaluates typed questions (**Choice / Score / Noul**) over a state and returns **calibrated
probabilities** in a single forward pass. It never generates text. Two exist today and share one
wire protocol (`POST /v1/systemone`):

| | Laya (self-hosted, Apache-2.0) | TypeSafe Jev (hosted API) |
|---|---|---|
| Latency (1 question) | ~33 ms on a T4 GPU; 190–580 ms on CPU | ~236–276 ms p50 (third-party measurement) |
| Zero-shot quality | weak on custom decisions (near chance); good on standard tasks | strong |
| Fine-tunable | yes (RLCD; small per-decision heads) | no |
| Context for the state | ~320 tokens (English checkpoint) / ~768 tokens (multilingual; up to 8k) | 32k |
| Options per Choice | ~20 comfortably | 255 |
| Languages | 45 of 51 usable, with a script router | best in English |
| Data residency | stays in our infrastructure | leaves to a third party (ZDR on enterprise plans) |
| Cost | GPU only | $0.042 per million input tokens; output free |

**The framework** (`app/decisions/`) gives AgentVerse one generic way to:

1. **Declare** a decision: a `DecisionSpec` with typed questions, a state builder, a policy and a
   safety class.
2. **Obtain the questions** without anyone hand-writing prompts. There are four sources:
   platform-owned, derived from tenant metadata, compiled from the tenant's plain English, or
   authored by an agent at runtime.
3. **Evaluate** the decision on the best eligible engine (Laya, Jev, LLM, or future engines), chosen
   per call by policy: residency, option count, state size, language, trained head available,
   health, cost.
4. **Wrap the existing logic as the "incumbent"**, so the framework can observe (shadow), make the
   system *only stricter* (augment), assist, or take over (live), per decision point and per tenant.
   Each change is reversible in seconds.
5. **Learn**: every decision is logged with its eventual outcome and label. Small Laya heads are
   trained per decision, gated against the current engine on held-out data, and promoted
   automatically.

**Rollout** is per decision point, in six waves, through the stages
`off → shadow → evaluate → assist/augment → canary live → GA`, with explicit entry and exit criteria
and automatic rollback. Safety-critical decisions (guardrails, risk, consent) **never** go past
`augment`: the framework can add a block or a HITL step, but never remove one.

---

## 1. Problem statement (verified in the codebase)

Verified on `main` @ `f81876e14`. Representative examples; the full list is in §28.

| Pattern | Examples (file · symbol) | Consequence |
|---|---|---|
| Keyword/regex decisions | `app/agent/nodes/_helpers.py` · `_is_high_risk_step` (`deploy/delete/prod/…` + `\brm\b`); `app/rag/agentic/context_gap_detector.py` · `has_gap` (12 phrases); `app/gateway/telephony_consent.py` · `OPT_OUT_KEYWORDS` (English only); `app/voice/intent_router.py` (regex, fixed confidence 0.85); `app/recovery/failure_classifier.py` | Misses paraphrases ("push to the live cluster") and false-flags benign text ("delete the draft comment"). English-only. |
| LLM "return JSON" | `app/intelligence/guardrail_engine.py` · `LLMJudge`; `app/rag/agentic/patterns/corrective.py` · `grade_evidence`; `app/agent/router.py` · `AgentRouter._score_by_llm`; `app/intelligence/eval_runner.py` (coherence/accuracy); `app/workflow/steps/llm_step.py` (regex-extracted JSON) | Slow (hundreds of ms to seconds), costly, invented confidence, parsing failures. |
| Stubs and gaps | `app/triggers/channels/gateway.py` · `NLIntentClassifier.classify` (ignores its LLM and embedder); `app/gateway/router.py` · `_process_command` (every inbound message becomes a goal); `app/memory/long_term.py` · `extract_from_goal` (stores everything at confidence 0.8) | Wasted goals and LLM spend; memory bloat and poisoning risk. |
| Ungoverned decision calls | `LLMJudge`, `grade_evidence`, `AgentRouter`, `IntentRouter`, `EvalRunner` and the `guardrails_v2` toxicity check call `provider.complete` directly | These calls bypass cost charging (`charge_llm_call`), the circuit breaker and the timeout. Tracked as a prerequisite (§30, M0). |
| Dynamic workflows lack a decision primitive | `app/workflow/dsl.py` has 17 step types and **no decision step**; `app/agent/workflow_planner.py` produces tool steps only, with no branches | Generated workflows classify with `llm` + `conditional`: brittle and expensive. |

---

## 2. Goals and non-goals

### Goals
- **G1 Generic.** One framework, many engines (Laya, Jev, LLM emulation, future System-One or
  classical classifiers). Engines are plugins with declared capabilities.
- **G2 Non-breaking.** Merging the framework changes no behaviour. Every decision point starts in
  `off`. The existing logic remains the authoritative fallback forever (§3).
- **G3 Controlled rollout.** Per decision point, per tenant, per percentage, with measured gates,
  automatic rollback, and an audit record for every change.
- **G4 No hand-written prompts per use case.** Questions come from four governed sources (§8), all
  checked by the linter (§11).
- **G5 Calibrated decisions.** Every answer carries a normalised, calibrated top-probability.
  Thresholds are policy stored in config, never in prompt wording.
- **G6 Safety monotonicity.** For safety-critical decisions the framework can only make the system
  stricter.
- **G7 Learn from operation.** Labels come from HITL, outcomes and reviews. Heads are trained,
  evaluated and promoted with no code change.
- **G8 Data sovereignty.** A tenant can require that no decision state leaves AgentVerse
  infrastructure.
- **G9 Cover every catalogued use case** (§28) with an explicit safety class, maximum mode and wave.

### Non-goals
- Replacing LLMs for planning, execution or text generation. System-One models don't generate.
- Replacing deterministic controls that should stay deterministic: `PolicyEngine`
  (`governance/policies.py`), budgets (`governance/cost.py`), RLS, rate limiting, compliance DB
  checks (`enterprise/compliance*.py`), secret-pattern regex, and the statistical significance test
  in `prompt_optimizer.py`. The framework may *add* signals beside these; it never replaces them.
- Training foundation models. Only small heads on frozen encoders, or full RLCD fine-tunes of Laya.
- Running torch inside the API/worker processes by default (serving is a separate service, §27).

---

## 3. Non-negotiable invariants (nothing breaks)

These are acceptance criteria for every PR in this programme. A reviewer must reject a PR that
violates any of them.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | **Off by default.** `DECISION_ENGINE_ENABLED=false`. Every `DecisionPoint.default_mode="off"` until promoted through config. With the framework disabled, no call site does anything new. | Settings default; a unit test per point asserts `mode=off` returns exactly the incumbent result and makes zero engine calls. |
| I2 | **The incumbent is preserved.** Existing logic stays intact and becomes the `incumbent` callable. It's never deleted while its point is below GA+30 days, and deletion is a separate PR. | Code review checklist; the parity test suite (§31). |
| I3 | **No hot-path latency in shadow.** Shadow calls run off the hot path through a bounded async queue (drop on overflow, counted). They never await the engine inline. | `ShadowDispatcher` design; a latency regression test. |
| I4 | **Failure isolation.** The framework never raises into a call site. Any engine error, timeout, budget denial or invalid answer → the incumbent result (S0/S1), or the *stricter* of incumbent and fail-closed (S2/S3). | `DecisionService.evaluate` contract; chaos tests. |
| I5 | **Safety monotonicity.** For safety classes S2/S3, the effective result is `stricter(incumbent, engine)`. Removing a block or HITL requires an explicit per-tenant opt-in, and only for use cases the catalogue marks as eligible (G03 auto-approve for `write_low`). | `CombinePolicy`; property tests. |
| I6 | **Additive persistence.** Migrations only create new tables and indexes. No existing column changes. **Single Alembic head** is maintained (current head `c8d2f4a6b1e3`); each migration rechains onto the head at merge time. | CI `alembic heads` check. |
| I7 | **Additive APIs.** New routes only. Existing response shapes are unchanged (new optional fields at most). New RBAC scopes are registered in the fail-closed scope registry. | OpenAPI diff in CI (`scripts/export_openapi.py`). |
| I8 | **DSL backward compatibility.** The `decide` step type and its fields are optional. Every existing workflow validates and executes identically. The linter applies only to `decide` steps. LLM→decide conversion is a suggestion, never applied automatically. | DSL golden tests over all templates in `app/workflow/templates`. |
| I9 | **The test suite is unchanged and green.** Tests build the app without `manage_pools` and get `FakeDecisionBackend`. No new warnings (`filterwarnings=error`). No real engine is needed for the default test run. | CI (ruff, mypy strict, pytest). |
| I10 | **Default channel behaviour is unchanged.** Behaviour-changing actions (e.g. not submitting chit-chat as a goal, trigger filtering) are **opt-in per tenant or per channel**. The default preserves today's behaviour even when the point is `live`. | `on_<outcome>` action defaults = current behaviour. |
| I11 | **No silent data egress.** External engines (Jev, hosted LLMs) receive state only if the tenant's residency policy allows it. Regulated domains default to self-hosted only. | `DecisionRouter` eligibility; a residency test. |
| I12 | **Reversible in seconds.** Every mode change propagates cluster-wide within ≤30 s through Redis pub/sub (same mechanism as `governance/policies.py`). No deploy is needed to roll back. | An integration test with two app instances. |
| I13 | **No new heavy dependencies in the backend image.** The backend talks HTTP to engines. torch and model weights live only in the `laya-serve` image. | A dependency review. |
| I14 | **Every decision call is governed.** It is charged to the goal/tenant budget, traced, audited, and passes through a circuit breaker and bulkhead. Budget denial → incumbent. | `DecisionService` pipeline. |

---

## 4. Concepts and glossary

| Term | Meaning |
|---|---|
| **System-One engine** | A model that evaluates typed questions over a state and returns probabilities without generating text (Laya, Jev). |
| **Backend** | An adapter that makes an engine usable by the framework (`DecisionBackend`, §6). One engine may have several backends: base checkpoint, fine-tuned head, tenant-hosted endpoint. |
| **Question** | A typed primitive: **Choice** (pick one of N; returns a distribution), **Score** (ordinal levels; returns an expected level and distribution), **Noul** (yes/no; returns P(yes)). |
| **State** | The content the questions are evaluated against: string, object or array. Always untrusted data. |
| **DecisionSpec** | A versioned declaration: questions + state template + combination rule + policy (§9). |
| **DecisionPoint** | A named place in AgentVerse where a spec is evaluated, e.g. `guard.tool_output.injection`. It carries the safety class, maximum mode, incumbent adapter and defaults. |
| **Incumbent** | The existing implementation of a decision point (keyword/regex/LLM/rules), wrapped as a callable. |
| **Mode** | `off`, `shadow`, `assist`, `augment`, `canary`, `live` (§12). |
| **Safety class** | S0 advisory · S1 operational · S2 protective · S3 irreversible-action gating (§12). |
| **Gate policy** | Thresholds that turn probabilities into actions: act / review / reject. |
| **Head** | A small classifier trained for one spec on a frozen encoder (Laya), or a full fine-tuned checkpoint. |
| **Decision log** | An append-only record of every evaluation, including incumbent and engine results, used for evaluation, calibration and training. |
| **Promotion** | Moving a point to a higher mode, or moving a spec to a different backend, after passing its gate. |
| **Spec hash** | The identity of a decision's *meaning*: a hash of its normalised questions (§9.3). |

---

## 5. Architecture

### 5.1 Component view

```
 call sites (agent loop, guardrails, RAG, channels, workflows, MCP tool, …)
        │  decisions.evaluate(point | spec, state_ctx, tenant_ctx, incumbent=…)
        ▼
 ┌──────────────────────────── app/decisions ─────────────────────────────┐
 │ DecisionService                                                        │
 │  ├─ ConfigResolver    (Settings → point defaults → platform floor → tenant) │
 │  ├─ StateBuilder      (template/fn → budgeted, redacted state)         │
 │  ├─ Linter            (at spec save/compile; cached verdict)           │
 │  ├─ DecisionRouter    (eligible backends → ordered chain)              │
 │  ├─ BackendExecutor   (breaker, bulkhead, timeout, retry, charging)    │
 │  ├─ Normaliser        (answers → canonical, calibrated top-probability)│
 │  ├─ CombinePolicy     (mode × safety class × incumbent → effective)    │
 │  ├─ ShadowDispatcher  (bounded async queue for off-hot-path calls)     │
 │  └─ DecisionLogger    (decision_log + OTel + metrics + audit)          │
 │ Backends: SystemOneHttpBackend (Laya | Jev | any compatible)           │
 │           LLMDecisionBackend · FakeDecisionBackend · (future plugins)  │
 │ Learning: LabelCollector · Calibrator · HeadTrainer · EvalGate · Promoter │
 │ Authoring: PointRegistry · MetadataDerivers · DecisionCompiler · SpecStore │
 └────────────────────────────────────────────────────────────────────────┘
        │ HTTP /v1/systemone                     │ provider.complete (via ChargingProvider)
        ▼                                         ▼
 laya-serve (GPU svc) · api.typesafe.ai · tenant-hosted endpoint · tenant LLM provider
```

### 5.2 Package layout (new; nothing existing is moved)

```
app/decisions/
  __init__.py            # public API: evaluate(), evaluate_batch(), DecisionPoint, types
  types.py               # Question, Choice, Score, Noul, Answer, DecisionResult, SafetyClass, Mode
  spec.py                # DecisionSpec, identity hashing, versioning, JSON schema
  points/                # platform-owned points (source A), one module per area
    agent.py guard.py governance.py rag.py memory.py channels.py routing.py rpa.py evals.py
  derive/                # source B: build specs from tenant metadata
    agents.py skills.py tools.py guardrail_rules.py channel_intents.py knowledge_bases.py
  compiler/              # source C: plain English → spec (LLM), test-set generation
  lint.py                # §11
  state.py               # StateBuilder, budgets, truncation, redaction
  router.py              # DecisionRouter (§7)
  backends/
    base.py              # DecisionBackend protocol, BackendCapabilities
    systemone_http.py    # generic /v1/systemone client (Laya, Jev, stuntd, …)
    llm.py               # LLM emulation backend
    fake.py              # deterministic test backend
  normalise.py           # canonical answers, confidence, calibration application
  combine.py             # CombinePolicy, stricter(), mode semantics
  service.py             # DecisionService: the pipeline in §5.1
  shadow.py              # ShadowDispatcher
  log.py                 # DecisionLogger, stores (in-memory + Postgres)
  config.py              # ConfigResolver, tenant_decision_config store, pub/sub propagation
  learning/              # labels.py calibrate.py train.py evalgate.py promote.py
  mcp_server.py          # builtin MCP server "decisions" (§17)
  workflow_step.py       # DecideStepNode (§16)
```

### 5.3 Wiring (follows the two-phase pattern in `app/main.py`)

1. `create_app()` constructs `DecisionService` with in-memory stores, an in-memory config, and
   **no registered backends**, and binds it to `app.state.decision_service`. With no engine, every
   decision returns its incumbent even when the kill switch is on. Tests register
   `FakeDecisionBackend` explicitly.
2. `lifespan` (only when `manage_pools=True`) swaps in the Postgres/Redis stores. It registers the
   configured backends (Laya/Jev/LLM) after health probes (§21.2), subscribes to config pub/sub, and
   registers backends in the model registry (§18).
3. Call sites resolve the service dynamically from `app.state` (or an injected dependency), so the
   lifespan swap takes effect without re-registration. This matches how the tenant key resolver
   works today.

---

## 6. The engine abstraction

### 6.1 Canonical types (engine-independent)

```python
class Noul(Question):   type = "noul";   instructions: Text; criteria: NoulCriteria | None; labels: NoulLabels | None
class Choice(Question): type = "choice"; instructions: Text; criteria: dict[str, Text | None]      # option -> description
class Score(Question):  type = "score";  instructions: Text; criteria: list[Text]                  # ordered levels, 2..10
Text = str | dict | list      # structured instructions allowed (both engines accept them)

@dataclass(frozen=True)
class Answer:
    qid: str
    type: Literal["choice", "score", "noul"]
    value: str | float                  # choice label | expected score | P(yes)
    distribution: dict[str, float]      # choice/score: full distribution; noul: {"false": 1-p, "true": p}
    top_probability: float              # canonical confidence = max(distribution) (see §13)
    raw_confidence: float | None        # the engine's own confidence field, kept for audit only
    calibrated: bool                    # True if a fitted calibration was applied

@dataclass(frozen=True)
class DecisionResult:
    point_id: str; spec_hash: str; spec_version: int
    answers: dict[str, Answer]
    outputs: dict[str, Any]             # after the combination rule (e.g. {"escalate": True})
    effective: Any                      # what the call site acts on, after CombinePolicy
    source: Literal["incumbent", "engine", "combined"]
    mode: Mode; engine: str | None; model_revision: str | None
    low_confidence: bool; latency_ms: float; decision_id: str
```

### 6.2 `DecisionBackend` protocol

```python
class DecisionBackend(Protocol):
    name: str                                   # "laya-serve", "jev", "llm:<provider>", "laya-head:<spec>"
    def capabilities(self) -> BackendCapabilities: ...
    async def evaluate(self, state: State, questions: Mapping[str, Question],
                       *, options: EvalOptions) -> RawEvaluation: ...
    async def evaluate_batch(self, states: Sequence[State], questions: Mapping[str, Question],
                             *, options: EvalOptions) -> list[RawEvaluation]: ...   # may loop
    async def health(self) -> BackendHealth: ...   # reachable, device, model_revision, queue depth

@dataclass(frozen=True)
class BackendCapabilities:
    max_choice_options: int              # Laya ≈ 20 comfortably (server hard cap 100); Jev 255
    max_score_levels: int                # Jev 10; Laya server 32
    max_questions_per_call: int          # Laya server 64
    max_state_tokens: int                # Laya-en ≈ 320, Laya-ml ≈ 768 (8k with max_len); Jev 32k
    languages: LanguageSupport           # e.g. {"en": "strong", "*": "weak"} or multilingual set
    supports_noul_labels: bool           # Laya only
    supports_structured_instructions: bool
    confidence_semantics: Literal["entropy", "jev_linear", "none"]   # for audit; normalised anyway
    calibrated_by_default: bool
    data_residency: Literal["self_hosted", "tenant_hosted", "external"]
    cost_model: CostModel                # per_input_token | per_request | gpu_showback | llm_tokens
    fine_tunable: bool
    zero_shot_quality: Literal["strong", "standard_tasks_only", "weak"]
    max_rps_hint: float | None
```

### 6.3 Built-in backends

| Backend | Covers | Notes |
|---|---|---|
| `SystemOneHttpBackend` | **Laya `laya-serve`, TypeSafe Jev, stuntd, any `/v1/systemone`-compatible server** | Our own thin client on the existing `httpx`. No vendor SDK is needed (avoids coupling and warning risk under `filterwarnings=error`). The dialect is chosen by `capabilities` (§6.4). Honours `Retry-After` on 429/503/529 within the call's timeout budget. |
| `LLMDecisionBackend` | Any configured tenant LLM | Emulates the primitives with `response_schema`. Uses logprobs when the provider exposes them; otherwise the distribution is *reported* and marked `calibrated=False`. Always charged through `ChargingProvider`. It's the universal fallback, and the only backend allowed to answer when every System-One backend is ineligible. |
| `FakeDecisionBackend` | Tests only | Deterministic answers from a fixture map or hashing. Never registered in production; with no backend configured, the router returns an empty chain and the incumbent answers. |
| *(future)* `InProcessBackend` | Laya in-process (edge or single-node installs) | Not enabled by default (I13). |
| *(future)* `ClassicalHeadBackend` | sklearn/ONNX heads over embeddings | Same protocol. Useful for very high-volume binary decisions. |

**Adding a new engine** (e.g. a future vendor): implement `DecisionBackend`, or expose
`/v1/systemone`. Declare its capabilities. Pass the backend contract test suite (§31.3). Register it
in the model registry (§18). It enters every point in **shadow only** until promoted.

### 6.4 Dialects: rendering canonical questions for each backend

Questions are canonical; each backend renders them in its own dialect:

- **Laya:**
  - add neutral `labels: {"true":"A","false":"B"}` to Nouls on the English checkpoint (works around
    its yes/no label bias);
  - reject yes/no/true/false as Choice keys (the linter already prevents them);
  - set `model` to `english | multilingual | <head>` per the router decision;
  - send `max_len`/`head_max_len` when the state or option budget needs more room.
- **Jev:**
  - drop the `labels` field;
  - `model` is always a pinned version (`jev-1.13.0`), never an alias.
- **LLM:**
  - render to a JSON schema (enum / number / boolean) with the criteria in the schema descriptions;
    the system prompt forbids free text.

---

## 7. Engine routing

`DecisionRouter.route(spec, state, tenant_cfg, point) → [backend, …]` returns an **ordered chain**.
The executor tries each backend in turn. When the chain is exhausted, the call falls back to the
incumbent.

### 7.1 Eligibility filters (a backend that fails any filter is removed)

1. **Residency:** the tenant's `data_residency` excludes `external` (and `tenant_hosted` if the
   tenant has none).
2. **Health:** the circuit is open, or health reports the wrong `model_revision` → excluded
   (revision pinning, §13.4).
3. **Capacity:** options > `max_choice_options` (after optional shortlisting, §7.3), levels, question
   count, or estimated state tokens > `max_state_tokens` (after the StateBuilder budget) → excluded.
4. **Language:** detected language not supported, or only `weak`, for this backend → excluded for
   S2/S3; deprioritised for S0/S1.
5. **Zero-shot quality:** if the spec has no trained head and no passing evaluation for this backend,
   a backend with `zero_shot_quality="weak"` is excluded from `live`/`augment` (shadow is still
   allowed). Exception: specs tagged `standard_task` (topic, sentiment, NLI-style, language
   detection).
6. **Tenant or point pin:** an explicit `engine_order` in config restricts the chain.

### 7.2 Ordering (among the eligible backends)

1. A **promoted head** for this spec hash (passed the evaluation gate), if one exists.
2. The point's `engine_order` default (e.g. `["laya", "jev", "llm"]`), adjusted by:
   - **latency budget:** hot-path points whose p95 budget is under 150 ms prefer self-hosted GPU
     backends;
   - **cost:** ties broken by `AIRouter.select_model(TaskType.DECISION, CHEAPEST)` (§18).
3. `LLMDecisionBackend` last, when residency allows the tenant's LLM (an on-prem LLM counts as
   self-hosted).

### 7.3 High-cardinality Choices (shortlisting)

If a Choice has more options than the best backend's `max_choice_options`, the router can
**shortlist**: embed the options (cached) and the state with the existing embedder, then keep the
top-k (k ≤ 20) by cosine similarity. This is the same pattern as Laya's `predict_shortlist`. The
answer then records `shortlisted: true, k, dropped_count`. Shortlisting is allowed for S0/S1 only.
For S2/S3, use a backend that can take the full option set.

### 7.4 Batching
- **Many questions over one state:** one call (both engines evaluate the questions in parallel
  against one encoding).
- **Many states with the same questions** (`foreach`, ingestion, MCP `decide_batch`):
  `evaluate_batch`, chunked by `max_questions_per_call` and `max_rps_hint`.
- The per-stage agent-loop batteries (e.g. all TOOL_ARGS guard questions) are **one request**.

---

## 8. Where questions come from

Nobody hand-writes Laya or Jev prompts per use case. Every spec comes from one of four governed
sources. All of them pass the linter (§11).

| Source | Author | Storage | Examples |
|---|---|---|---|
| **A. Platform-owned** | The AgentVerse team, once | Code: `app/decisions/points/*` (versioned, reviewed like prompts) | Guardrail batteries, RAG relevance, step success, tool risk, chat intent, memory write |
| **B. Derived from tenant metadata** | The platform, automatically | Generated at runtime from DB records; cached by metadata version | Agent routing (criteria = agent descriptions), skill selection (`skill.description` + `trigger_hints`), tenant guardrail rules (one Noul per rule), channel intents, knowledge-base choice, tool risk (tool name + description + MCP schema as state) |
| **C. Compiled from the tenant's plain English** | An LLM compiler, reviewed by the tenant | `decision_specs` table (draft → published, immutable versions) | Workflow `decide` steps, trigger conditions, custom guardrails, decision packs |
| **D. Agent-authored at runtime** | The planner or executor LLM | Ephemeral; hashed; promoted to C when frequently reused | Bulk classification inside a goal via the `decide` MCP tool |

### 8.1 Source A rules
- One module per area. Each point declares `safety_class`, `max_mode`, `incumbent` adapter,
  `state_fn`, `default_policy` and `latency_budget_ms`.
- Wording changes bump `spec_version`. Calibration and heads are tied to the spec hash (§9.3).
- Training data: synthetic LLM-generated examples, red-team corpora, and tenant data **only from
  tenants who opt in** (`decision_data_sharing=true`). Nothing is pooled by default.

### 8.2 Source B rules
- **Derivers** read metadata (agents, skills, tools, guardrail rules, channel intents, knowledge
  bases) and emit a spec whose **instruction is fixed and platform-written** and whose **criteria are
  tenant data**.
- Criteria text is treated as data, never as instructions to the platform. It is bounded in length
  and passed through the linter.
- **Metadata quality lint:** empty, very short or duplicate descriptions produce a UI warning
  ("routing quality low: add a description"). The spec also falls back to Jev/LLM or the incumbent
  for that tenant.
- More than 20 options → shortlist (§7.3), or a backend with high option capacity.

### 8.3 Source C: the Decision Compiler

```
 plain English ─▶ ① compile (LLM) ─▶ ② lint + auto-fix ─▶ ③ synthetic test set
              ─▶ ④ tenant review/labels ─▶ ⑤ publish (immutable version) ─▶ shadow → …
```
1. **Compile.** An LLM (the tenant's configured provider, via `ChargingProvider`) produces:
   **atomic questions** + `state_fields` (chosen from the available variables) + `code_inputs`
   (numbers and dates, never sent to the model) + `rule` (a `simpleeval` expression evaluated by the
   existing `app/workflow/expression_engine.py`) + a suggested policy. The compiler prompt encodes
   the linter rules and the known engine weaknesses. It follows the same plain-English-to-structured
   pattern as `MetaAgentPlanner` and `NLScheduler`.
2. **Lint** (§11): errors block publishing; auto-fixes are shown as a diff.
3. **Test.** The LLM generates about 30 examples, including adversarial ones (negation, mixed
   language, borderline). The spec is evaluated on every eligible backend and the results are shown
   side by side.
4. **Review.** The tenant corrects labels. Corrections become the spec's first **gold evaluation
   set**.
5. **Publish.** Immutable `spec_version`. Referenced by workflows as `decision_ref: <id>@<version>`
   or inlined.
6. **Optimise (optional).** `prompt_optimizer.py` + `EvalSuiteRunner` A/B-test criteria wording on
   the gold set. A variant is promoted only on a significant win.

### 8.4 Source D rules
- The builtin MCP tool `decide` (§17) accepts agent-composed questions.
- Linted with **auto-fix only**: errors return a structured tool error the LLM can correct.
- **Capped at S0/S1.** An agent-authored decision can never gate a governance action (tool
  authorisation, HITL, consent, budgets). Its outputs are data for the agent.
- Default chain: Jev → LLM (strong zero-shot). Laya only for `standard_task` specs or specs with a
  promoted head.
- Frequently repeated ad-hoc specs (same spec hash above a threshold) are surfaced for promotion to
  source C.

---

## 9. DecisionSpec

### 9.1 Schema (abridged; the full JSON schema is in Appendix B)

```yaml
id: claim_escalation             # tenant-unique slug (source C) or point id (source A)
version: 3                       # immutable per publish
source: compiled                 # platform | derived | compiled | agent
safety_class: S1                 # S0..S3; S2/S3 require platform approval for tenant specs
standard_task: false             # true → base checkpoints eligible zero-shot
state:
  template:                      # variable paths → state keys (source C/D) …
    description: "{{claim.description}}"
    notes: "{{claim.adjuster_notes}}"
  budget_tokens: 700             # … or state_fn for source A
  truncate: {description: head, notes: tail}
  redact_for_external: true
questions:
  suspicious:
    type: noul
    instructions: "Does `description` show signs of fraud such as inconsistent dates, prior similar claims, or vague loss details?"
    criteria: {true: "one or more fraud signals", false: "consistent, specific account"}
  legal_threat:
    type: noul
    instructions: "Does the customer mention a lawyer, lawsuit, or legal action?"
    criteria: {true: "mentions legal action", false: "no legal action mentioned"}
code_inputs: {amount: "{{claim.amount}}"}
rule:
  escalate: "(suspicious > 0.7 or legal_threat > 0.6) and amount >= 10000"
policy:
  min_top_probability: 0.75      # below → low_confidence
  on_low_confidence: review      # review (HITL) | incumbent | default_branch | fail
engine_order: [laya, jev, llm]   # optional; the router may reorder (§7)
latency_budget_ms: 1500
```

### 9.2 Outputs
- `answers.<qid>`: canonical `Answer` objects.
- `outputs`: the rule results (`escalate: true`), plus `low_confidence` (bool) and `engine`.
- Exposed to workflows as variables: `{{step.escalate}}`, `{{step.suspicious}}`,
  `{{step.low_confidence}}`, `{{step.answers.suspicious.top_probability}}`.

### 9.3 Identity and versioning
- **`spec_hash`** = SHA-256 over the canonical JSON of `{questions (type, instructions, criteria,
  labels), state keys, schema_version}`. Whitespace is normalised and keys sorted. It excludes the
  policy, `engine_order`, question IDs and the rule.
- Changing **wording** → new hash → calibration reset, head invalidated, the point re-enters shadow
  for that spec.
- Changing **policy or rule** → same hash. The change is audited and the effect is visible
  immediately (still subject to the mode gate).
- The same hash across workflows or tenants shares evaluation history *within a tenant*. Cross-tenant
  sharing happens only for source A, or with an explicit opt-in.

---

## 10. State construction, budgets and redaction

- **Source A:** `state_fn(ctx)` per point selects only the fields the questions need. Engines lose
  accuracy on large, irrelevant state.
- **Sources C/D:** `state.template` resolved with the existing workflow `ContextResolver`.
- **Budgeting:** tokens are estimated conservatively as `ceil(chars / 3.5)`. This deliberately
  over-estimates and needs no tokenizer download, so it works in air-gapped installs. Per-field truncation strategy: `head | tail | middle | sentences`. When a state
  doesn't fit a backend, that backend is ineligible (§7.1). Laya's windowed `predict_long` is
  allowed only for S0 background jobs, because its probability is the deciding window's, not
  calibrated for the whole document.
- **Redaction for external backends:** when `redact_for_external` is set (the default for tenants
  with `pii_redaction=true`), state bound for an `external` backend passes through
  `app/agent/sanitization.py` + `app/ingestion/pii.py` `RegexPIIAnalyzer`, which replaces values
  with typed placeholders. Self-hosted backends get the raw state unless the tenant also requires
  redaction there.
- **Numbers and dates** never go into the state for judgement. They are `code_inputs` evaluated in
  the rule.
- **Language detection** is done once per state (Laya's script and language router logic, or a
  lightweight detector). It is stored for routing and for per-slice metrics.

---

## 11. The linter

It runs at spec compile, save and publish (C), at spec derivation (B, cached), at `decide` MCP calls
(D, auto-fix only), and as a CI test over all platform points (A).

| Code | Rule | Severity | Auto-fix |
|---|---|---|---|
| L001 | One judgement per question. No "and/or" joining separate conditions inside an instruction. | error | split into several questions + a rule |
| L002 | No arithmetic, counting or date comparison inside a question | error | move to `code_inputs` + rule |
| L003 | No negated instruction ("is NOT…", "isn't…") | error | rewrite positively; invert in the rule |
| L004 | A Noul must have `criteria` (true/false descriptions) | error | generate criteria |
| L005 | Choice keys must not be `yes/no/true/false` (in any language) | error | rename to semantic keys or `A`/`B` |
| L006 | Choice options ≤ the target backend's capacity (default 20) | error for S2/S3; warn otherwise | shortlist, or split into coarse → fine questions |
| L007 | Score has 2–10 levels, every level described, levels ordered | error | — |
| L008 | Criteria must not contradict the instruction (Noul true = "no") | error | align |
| L009 | Estimated state fits at least one eligible backend | error | reduce `state_fields` / truncate |
| L010 | Every referenced state key exists in the template | error | — |
| L011 | Rule expression parses in `ExpressionEngine`, referencing only question IDs and `code_inputs` | error | — |
| L012 | S2/S3 specs need a platform-approved safety class and `on_low_confidence ∈ {review, incumbent}` | error | — |
| L013 | Duplicate or near-duplicate options (embedding cosine > 0.95) | warn | merge |
| L014 | Instruction longer than 400 characters | warn | move data into structured instructions |
| L015 | Multi-hop or indirect phrasing ("a property of a property") | warn | rewrite to reference state keys directly |
| L016 | Non-English instructions | warn | translate (keep the state in its native language) |

---

## 12. Modes, safety classes and the incumbent pattern

### 12.1 Safety classes

| Class | Definition | Examples | Maximum mode |
|---|---|---|---|
| **S0 Advisory** | Output orders, suggests or reports. The user or a human acts on it. | HITL queue ordering, template suggestion, trace failure taxonomy, red-team reports | `live` |
| **S1 Operational** | Affects routing, quality or cost. Wrong answers degrade the result but are reversible. | Chat intent, model tier, RAG relevance, memory write gate, ingestion doc type | `live` (after canary) |
| **S2 Protective** | Guards against harm or data exposure | Guardrail batteries, toxicity, grounding gate, data classification, citation support | **`augment`** (stricter-of) |
| **S3 Irreversible-action gating** | Decides whether a side-effecting or irreversible action proceeds, or whether consent exists | Step/tool risk tier, exfiltration in args, telephony opt-out/consent, voice approve/reject | **`augment`** (stricter-of) |

### 12.2 Modes

| Mode | Engine called? | Authoritative result | Hot path |
|---|---|---|---|
| `off` | no | incumbent | unchanged |
| `shadow` | yes, async, sampled (`shadow_sample_rate`) | incumbent | unchanged (I3) |
| `assist` | yes, inline within the latency budget, or async if the UI can update later | incumbent; engine output attached as a suggestion (UI ordering, pre-filled forms) | + ≤ budget |
| `augment` | yes, inline | **`stricter(incumbent, engine)`** for S2/S3; for S1, engine adds signals (e.g. extra redaction) | + ≤ budget |
| `canary` | yes, inline | engine for the canary cohort (tenant list or % hash of `tenant_id`/`goal_id`); incumbent otherwise | + ≤ budget |
| `live` | yes, inline | engine, falling back to the incumbent on error, budget denial or ineligibility; low confidence → `on_low_confidence` | + ≤ budget |

`stricter()` is defined per output type and declared on the point:
- boolean flags: OR;
- severity/score: max;
- ordered enums (risk tiers `read < write_low < write_high < destructive`): max;
- sets (violations): union;
- "allowed?" decisions: AND.

### 12.3 The incumbent pattern (call-site integration)

```python
# Before (unchanged logic, still present):
violations = indirect_injection.scan_tool_output(output)

# After:
result = await decisions.evaluate(
    point=points.guard.TOOL_OUTPUT_INJECTION,
    state_ctx=StateContext(tool_output=output, tool_name=tool.name),
    tenant_ctx=tenant_ctx, goal_id=goal_id,
    incumbent=lambda: indirect_injection.scan_tool_output(output),   # existing code, verbatim
)
violations = result.effective          # same type the call site already uses
```
- `incumbent` is always executed in `off`, `shadow`, `assist` and `augment`. It is executed in
  `canary`/`live` when needed for fallback, or when the point sets `always_run_incumbent=true` (the
  default for S1 during the first 30 days of live, for continued comparison).
- An **incumbent mapper** converts the incumbent's native output into the point's canonical output
  so the two can be compared and logged.
- The call site's type contract is unchanged. `result.effective` has the same type as before.

### 12.4 Low confidence

`on_low_confidence`:
- `review`: create a HITL item through the existing `governance/hitl.py` / `workflow/hitl_extension.py`
  with the probabilities attached. The review outcome becomes a gold label.
- `incumbent`: use the incumbent result.
- `default_branch`: workflows only.
- `fail`: explicit failure; source C only, with tenant opt-in.

---

## 13. Confidence normalisation and calibration

### 13.1 Canonical confidence
Engines disagree on how they compute `confidence`:
- Jev: `(n·p_max − 1)/(n − 1)`;
- Laya: `1 −` normalised entropy (plus `answer_confidence = max p`).

The framework therefore **always computes `top_probability = max(distribution)`** from the returned
probabilities. For a Noul, `max(p, 1−p)`. Raw engine confidence is logged for audit only. All
thresholds use `top_probability` (after calibration), so they are comparable across engines.

### 13.2 Calibration
- Per `(backend, model_revision, spec_hash, question_id)`: temperature scaling fitted on gold
  (preferred) and silver labels (§14.2). Minimum sample: 200 for Choice/Score, 300 for Noul.
  Otherwise the answer is marked `calibrated=false`.
- **Uncalibrated answers can't drive `live` or `canary` for S1.** Only `shadow`, `assist`, or
  `augment` for S2/S3, where they can only add strictness.
- Laya's multilingual checkpoint ships with no fitted temperatures, and both Laya checkpoints are
  over-confident as shipped. Calibration is mandatory before promotion.
- The LLM backend is `calibrated=false` unless it's logprob-based and fitted.

### 13.3 Threshold selection
Thresholds are chosen per point from the **coverage/accuracy curve** on held-out data: the lowest
threshold whose accuracy at that coverage meets the point's `target_precision`. They are stored in
`decision_calibration` and are never carried over between backends, question types or model
revisions.

### 13.4 Revision pinning
- Jev: use the versioned ID only (`jev-1.13.0`), never `jev-latest`.
- Laya: `LAYA_REVISION` + SHA-256 digests (Laya's `revisions.py`).

If `health()` reports a different `model_revision` than the calibration's, the point **drops to
`shadow` for that backend automatically** and raises an alert. A model change never silently
inherits old thresholds.

---

## 14. Decision log, labels and outcomes

### 14.1 `decision_log` (append-only; monthly partitions, like `goal_events`)
Each row records:
- `decision_id`, `tenant_id`, `point_id`, `spec_hash`, `spec_version`, `mode`, `safety_class`;
- `backend`, `model_revision`, `answers` (with distributions), `outputs`, `effective`, `source`;
- `incumbent_output`, `agreement` (engine vs incumbent per output), `low_confidence`;
- `latency_ms`, `cost_usd`, `language`, `goal_id` / `workflow_run_id` / `step_id`, `trace_id`;
- `state_ref`: per the tenant's `decision_log_state` setting:
  - `none` (hash only);
  - `redacted` (the default: a redacted excerpt, needed for training);
  - `full` (opt-in).

### 14.2 Labels (`decision_labels`)

| Tier | Sources | Used for |
|---|---|---|
| **Gold** | HITL review of low-confidence items; the tenant's review in the compiler (§8.3 ④); explicit corrections in the console; red-team ground truth | Evaluation, calibration, training |
| **Silver** | Downstream outcomes: goal success or failure after a routing or model-tier choice; users overriding the agent router (`needs_human_choice` picks); ticket reopened; verifier outcome versus pre-verifier; retrieval accepted or rejected; RPA run success | Calibration, training (weighted) |
| **Bronze** | A teacher engine (Jev or LLM) in shadow | Distillation **only if the provider's terms permit it** (§33); never evaluation |

Outcome joiners are Celery tasks that connect later events (goal completion, HITL resolution,
override events) back to `decision_id` using `goal_id`/`trace_id`.

### 14.3 Retention and deletion
- Governed by the existing retention framework (batched deletes, short transactions). Default
  retention is 180 days for `decision_log`; labels are kept while their spec is active.
- DSAR and GDPR deletion hooks cascade to `state_ref` and labels.

---

## 15. Learning loop

```
 decision_log + labels ─▶ dataset builder (per spec_hash, gold/silver split, per-slice stratification)
   ─▶ HeadTrainer (Laya: frozen-encoder head, or full RLCD fine-tune) ─▶ temperature fit
   ─▶ EvalGate (held-out gold vs current engine) ─▶ register head (revision + SHA-256)
   ─▶ shadow the head ≥ 7 days ─▶ Promoter (canary → live, or augment for S2/S3) ─▶ monitor/drift
```
- **Trigger:** a nightly beat task selects specs with ≥ 500 labels (gold + silver, ≥ 200 gold) and no
  head trained in the last 14 days, or with drift alerts.
- **Training:** a dedicated Celery queue `decisions.training`, routed to a GPU worker or an external
  job runner. The script is derived from Laya's fine-tuning notebook. Frozen-encoder heads take
  minutes; full fine-tunes take hours (2× T4 ≈ 4–5 h for 30k questions).
- **EvalGate (all must pass):**
  1. Non-inferior to the current authoritative engine on held-out gold (one-sided 95% CI; margin
     2 points), and superior on at least one of: accuracy, latency, cost.
  2. ECE ≤ 0.08 after temperature fit (Noul: Brier ≤ the current engine's).
  3. No slice (language, tenant plan, channel) with ≥ 50 examples regresses by more than 3 points.
  4. p95 latency within the point's budget on production hardware.
  5. Uses `laya-evals run --min-accuracy … --max-ece … --slice language --baseline …` where
     applicable, and `EvalSuiteRunner` otherwise.
- **Promotion** is a config change plus an audit record. **Rollback** is one config change; the
  previous backend stays warm for ≥ 7 days.
- **Drift:** alert on a PSI > 0.2 shift in confidence histograms, agreement dropping > 5 points
  week over week, or label distribution shift. Drift triggers recalibration, then retraining.
- **Tenant-trained heads (product):** tenants upload labelled rows for their compiled specs. The same
  pipeline runs, and the head is scoped to that tenant.

---

## 16. Workflow integration

### 16.1 The `decide` step type
- Registered through `StepTypeRegistry` (the existing plugin point; it also fills the Visual Builder
  palette).
- `StepDefinition` gains **optional** fields (I8): `questions`, `state`, `decision_ref`
  (`<spec_id>@<version>`), `code_inputs`, `rule`, `min_confidence`, `on_low_confidence`,
  `engine_order`.
- **`DecideStepNode`** resolves `{{…}}` variables, calls `DecisionService.evaluate(spec=…)` (mode
  resolved per tenant/spec), and returns outputs as step variables. The existing `conditional` step +
  `ExpressionEngine` handle all branching unchanged.
- `on_low_confidence: review` (the default) routes to the existing HITL step machinery. The DSL
  validator requires every `decide` step to have either an `on_low_confidence` action or a downstream
  conditional referencing `{{id.low_confidence}}`. If both are missing, the validator auto-inserts a
  HITL review route and reports it.
- Inside `foreach`: the iteration is detected and states are batched (`evaluate_batch`).

### 16.2 Generator rule (dynamic workflows)
`WorkflowPlanner` (`/workflows/generate`), the agent planner and the Decision Compiler get one added
instruction:

> "If a step is a judgement over a fixed set of outcomes (category, yes/no, severity), emit a
> `decide` step with atomic questions and put the logic in a `conditional`. Keep numbers and dates
> in `code`/`transform` steps. Use `llm` steps only to generate text."

`_plan_to_canvas` maps `decide` to a new canvas node type `decision`. Existing node types are
unchanged. The generator is gated by the flag `decision_workflow_generation_enabled` (default off),
so generated output doesn't change until that flag is enabled.

### 16.3 Validation and suggestions
- The linter runs inside the DSL `model_validator` **only for `decide` steps** (I8).
- Builder suggestion (never applied automatically): "step `s3` is an `llm` step whose JSON output
  only feeds a `conditional`. Convert it to `decide`?" One click converts it; the user reviews before
  saving.

### 16.4 Workflow creation assists (S0)
- **Template selection:** a Choice over marketplace templates (shortlisted by pgvector). A confident
  match is suggested before generating from scratch.
- **Connector selection:** a Noul per candidate connector per step. This removes the planner's
  current "first 20 tool names" limit on what it sees.

---

## 17. Agent-runtime integration

- **Builtin MCP server `decisions`** registered in `app/mcp/servers/registry_wiring.py`, with tools
  `decide(state, questions, rule?)`, `decide_batch(states, questions, rule?)` and
  `list_decision_specs()`.
  - Risk tier `read` in `_BUILTIN_TOOL_RISK` (no HITL).
  - Requires grants like any other tool.
  - Charged to the goal budget.
  - `decide_batch` is capped per call (default 1,000 states; configurable per plan).
- Source D rules apply (§8.4). The tool's output is data for the agent. It can never authorise tools
  or bypass governance.
- **Agent-loop call sites** (verifier, executor, routing mixins) use `decisions.evaluate(point=…)`
  with the incumbent pattern (§12.3).

---

## 18. Model-registry integration

Per `2026-09-12-generic-model-registry-design.md`:
- Add `ModelCapability.TYPED_DECISION` and `TaskType.DECISION` to `app/ai_router/models.py`.
  Additive; existing enum values are unchanged.
- Each configured backend registers as a `ModelEndpoint` with its cost metadata (Jev per input token;
  Laya `gpu_showback`; LLM per its model) and capability `TYPED_DECISION`.
- The DecisionRouter applies decision-specific eligibility (§7.1), then uses
  `AIRouter.select_model(TaskType.DECISION, CHEAPEST)` only to order ties. The registry UI lists
  decision engines beside the other capabilities.
- `MODEL_PRICING` (`app/intelligence/cost_tracker.py`) gains `jev-1.13.0` (input $0.042/M, output
  $0) and `laya-*` ($0 plus an optional showback rate).

---

## 19. Governance, cost, privacy, compliance

- **Cost:**
  - Each engine call → `CostController.check_and_record` (per goal / per tenant per day) + ledger
    (`CostTracker.record_llm_usage`) + per-role breakdown (`role="decision:<point>"`).
  - Budget denial → incumbent (I14). Never a failure.
  - Laya calls are recorded at $0, with optional GPU showback (`laya_showback_usd_per_1k_requests`)
    for internal chargeback.
- **Audit** (`governance/audit.py`): mode changes, threshold changes, promotions and rollbacks, spec
  publishes, head registrations, and residency changes. Each entry records actor, before, after and
  reason.
- **RBAC** (new scopes, registered fail-closed): `decisions:read`, `decisions:write` (tenant
  specs/config), `decisions:review` (label queue), `decisions:admin` (platform floors, promotions),
  `decisions:evaluate` (direct API).
- **Residency:**
  - Tenant setting `data_residency ∈ {any, self_hosted_only, tenant_hosted_only}`.
  - Default `self_hosted_only` for tenants whose domain is in the regulated set already tracked by
    `policies.py` (`evaluate_with_domain_failsafe`).
  - `any` requires a DPA with the external engine provider (Jev offers ZDR on enterprise plans).
- **Regulated decision packs** (recruiting, credit/insurance underwriting, financial crime, medical):
  - marked `regulated: true`;
  - HITL is mandatory for adverse outcomes (`on_outcome: review`);
  - a bias/slice evaluation is required before publishing;
  - explanation logging (the probabilities and the questions asked) is retained for the regulatory
    period.
  - These decisions can fall into high-risk categories under the EU AI Act and similar laws.
- **Privacy:** state redaction for external engines (§10); `decision_log_state` controls (§14.1);
  DSAR deletion (§14.3); tenant data is never used to train platform heads without opt-in (§8.1).

---

## 20. Security

| Threat | Mitigation |
|---|---|
| Prompt injection **in the state** steers the decision (both engines document susceptibility) | State is data. Safety points stay `augment` (stricter-of), so injection can't lower protection below the incumbent. Regex, deobfuscation and encoding-attack layers remain. Laya's injection accuracy (0.698 held-out) is recorded as a known limit. |
| Injection **in tenant metadata** feeding criteria (source B) | Criteria are length-bounded, linted, and rendered as data. Platform instructions are fixed. |
| Agent-authored decisions (D) used to escalate privileges | D is capped at S0/S1. No governance gate may consume a D result (enforced by the point registry: governance points accept only A or B specs). |
| Compiled specs (C) executing code | Rules run in the hardened `simpleeval` `ExpressionEngine` (no builtins, no imports). Specs are data. |
| SSRF through engine URLs | Platform URLs from Settings: `*_allow_internal` requires explicit operator opt-in (same model as `rag_hosted_reranker_allow_internal`). Tenant-hosted endpoints must pass `assert_public_url` unless an operator approves an internal target. |
| Credential exposure | Engine API keys in env/ExternalSecret (platform) or `CredentialVault` (tenant). Never logged. Redacted in traces. |
| Model supply chain | Laya weights pinned by revision + SHA-256, baked into scanned images, `HF_HUB_OFFLINE=1` in production. Jev pinned by versioned model ID. |
| Engine service exposure | `laya-serve`: ClusterIP only, NetworkPolicy allowing backend/worker, bearer key required, request caps (64 questions, 50k characters, 2 MB body, 16 concurrent). |
| Denial of service through expensive decisions | Per-tenant bulkhead, per-plan caps on `decide_batch`, Redis token bucket for Jev (1,200 rpm account limit), `latency_budget_ms` timeouts. |

---

## 21. Reliability and degradation

### 21.1 Execution controls
- Per-backend circuit breaker (reuse `app/providers/circuit_breaker.py` semantics, keyed by
  `backend:model_revision[:tenant]`).
- Per-tenant bulkhead (`app/reliability/bulkhead.py`).
- Timeout: `min(point.latency_budget_ms, backend.timeout)`.
- Retry only on 429/503/529 with `Retry-After`, and only within the remaining budget. Then the next
  backend in the chain.

### 21.2 Health
- Startup and a periodic `health()` check: reachable, device (Laya silently falls back to CPU; alert
  if a GPU was expected), `model_revision` matches the calibration pin, queue depth.
- Exposed in `runtime_readiness` and `/ready` (e.g. "decisions: laya degraded → jev").

### 21.3 Degradation matrix

| Condition | S0/S1 | S2/S3 |
|---|---|---|
| Preferred backend down, others eligible | next backend | next backend; still stricter-of |
| All System-One backends down | LLM backend if residency allows, else incumbent | incumbent (never less strict); high-risk goals keep today's `_guardrail_should_fail_closed` behaviour |
| Budget exhausted | incumbent | incumbent |
| Revision mismatch | that backend drops to shadow; next backend | same |
| Shadow queue overflow | drop shadow samples (counted); no user effect | same |
| Config store unreachable | last known config (cached); if none, `off` | same |

---

## 22. Observability

- **Metrics (Prometheus):**
  - `agentverse_decision_requests_total{point,backend,mode,source,outcome}`
  - `agentverse_decision_latency_seconds{point,backend}` (histogram)
  - `agentverse_decision_fallback_total{point,from,to,reason}`
  - `agentverse_decision_low_confidence_total{point}`
  - `agentverse_decision_agreement_ratio{point,backend}` (gauge, rolling)
  - `agentverse_decision_shadow_dropped_total`
  - `agentverse_decision_cost_usd_total{point,backend}`
  - `agentverse_decision_backend_health{backend}`
  - `agentverse_decision_calibration_ece{point,backend}`
- **Traces (OTel):** span `decision.<point_id>` with attributes: spec hash and version, mode, backend,
  model revision, `low_confidence`, `source`. Never the state content.
- **Dashboards (Grafana):** per point (volume, agreement, fallback, latency, confidence
  distribution), per backend (health, rps, errors), rollout board (mode per point per cohort).
- **Alerts:**
  - backend down > 2 min;
  - fallback rate > 5% for 10 min;
  - p95 over budget for 15 min;
  - agreement drop > 5 points day over day;
  - HITL volume increase > the point's `max_review_increase`;
  - revision mismatch;
  - ECE > 0.12.
- **Runbook:** `infra/runbooks/decision-engine.md` (Appendix H outline).

---

## 23. Configuration

### 23.1 Precedence
```
Settings kill switch (DECISION_ENGINE_ENABLED)
  └─ point defaults (code: default_mode, policy, engine_order, max_mode)
      └─ platform floor (admin: minimum strictness for S2/S3, per-point max_mode caps)
          └─ tenant override (tenant_decision_config; may tighten S2/S3, never loosen below the floor)
              └─ per-spec/per-workflow overrides (source C only, within the tenant's limits)
```

### 23.2 Settings (`app/core/config.py`; the same group pattern as `rag_hosted_reranker_*`)

```python
decision_engine_enabled: bool = False
decision_default_shadow_sample_rate: float = 0.1
decision_shadow_queue_max: int = 10_000
decision_workflow_generation_enabled: bool = False
decision_mcp_tool_enabled: bool = False
decision_default_engine_order: str = "laya,jev,llm"
decision_log_default_state: str = "redacted"          # none|redacted|full
decision_log_retention_days: int = 180

laya_base_url: str = ""
laya_api_key: str = ""
laya_timeout_seconds: float = 2.0
laya_allow_internal: bool = False                     # required for cluster DNS (SSRF guard)
laya_expected_revision: str = ""
laya_showback_usd_per_1k_requests: float = 0.0

typesafe_base_url: str = "https://api.typesafe.ai"
typesafe_api_key: str = ""
typesafe_model: str = "jev-1.13.0"                    # pinned; never an alias
typesafe_timeout_seconds: float = 3.0
typesafe_rpm_limit: int = 1200
```
Secrets are read through `get_provider_env()`. All keys are documented in `.env.example`.

### 23.3 Tenant configuration (`tenant_decision_config`, §24)
Per `(tenant_id, point_id | "*")`:
- `mode`, `canary_percent`, `engine_order`, `thresholds`, `on_outcome` actions (e.g. channel
  chit-chat → `canned_reply`; default `submit_goal`);
- `data_residency`, `redact_for_external`, `decision_log_state`, `decision_data_sharing`;
- `custom_endpoint_ref` (a vault reference for a tenant-hosted engine).

### 23.4 Propagation
Writes publish on Redis channel `decisions:config`. Instances refresh their resolver cache (I12).

---

## 24. Data model

New tables only (I6). All tenant-scoped tables use RLS through `rls_context()` with `FORCE ROW LEVEL
SECURITY`, following the existing least-privilege conventions.

| Table | Key columns | Notes |
|---|---|---|
| `decision_specs` | `id`, `tenant_id`, `slug`, `source`, `status` (draft/published/archived), `created_by` | tenant-authored and derived specs |
| `decision_spec_versions` | `spec_id`, `version`, `spec_hash`, `body` (JSONB), `lint_report`, `published_at` | immutable |
| `tenant_decision_config` | `tenant_id`, `point_id`, `mode`, `canary_percent`, `engine_order`, `thresholds`, `actions`, `residency`, `flags`, `updated_by` | unique `(tenant_id, point_id)` |
| `decision_platform_floor` | `point_id`, `min_strictness`, `max_mode`, `updated_by` | admin-only, not tenant-scoped |
| `decision_log` | as §14.1 | **monthly range partitions** on `created_at`; indexes `(tenant_id, point_id, created_at)`, `(spec_hash, created_at)`, `(goal_id)` |
| `decision_labels` | `decision_id`, `tenant_id`, `tier`, `label` (JSONB), `source`, `created_by` | |
| `decision_calibration` | `backend`, `model_revision`, `spec_hash`, `question_id`, `temperature`, `thresholds`, `ece`, `n`, `fitted_at` | |
| `decision_heads` | `id`, `spec_hash`, `tenant_id` (nullable = platform), `backend`, `artifact_uri`, `revision`, `sha256`, `eval_report`, `status` (training/candidate/shadow/live/retired) | |
| `decision_rollouts` | `point_id`, `tenant_id` (nullable), `from_mode`, `to_mode`, `gate_report`, `actor`, `at` | append-only history; also mirrored to audit |

Migrations: one Alembic revision per milestone, rechained onto the current single head at merge time
(I6). Each includes a downgrade that drops only its own objects.

---

## 25. API surface (additive)

| Method & path | Scope | Purpose |
|---|---|---|
| `GET /api/v1/decisions/engines` | `decisions:read` | backends, capabilities, health, revisions |
| `GET /api/v1/decisions/points` | `decisions:read` | points, safety class, current mode per tenant, max mode |
| `GET/PUT /api/v1/decisions/points/{point_id}/config` | read / `decisions:write` | tenant config (validated against the floor) |
| `GET /api/v1/decisions/points/{point_id}/stats` | `decisions:read` | volume, agreement, confidence histogram, latency, fallback, calibration |
| `GET /api/v1/decisions/log` | `decisions:read` | filtered log (state per the `decision_log_state` policy) |
| `GET /api/v1/decisions/review-queue` · `POST …/labels` | `decisions:review` | gold labelling |
| `POST /api/v1/decisions/specs/compile` | `decisions:write` | plain English → draft spec + lint report + test set |
| `POST /api/v1/decisions/specs/{id}/lint` · `/test` · `/publish` | `decisions:write` | lifecycle |
| `GET /api/v1/decisions/specs` · `/{id}/versions` | `decisions:read` | |
| `POST /api/v1/decisions/evaluate` · `/evaluate-batch` | `decisions:evaluate` | direct tenant API (rate-limited, charged) |
| `POST /api/v1/admin/decisions/points/{id}/floor` | `decisions:admin` | platform floor |
| `POST /api/v1/admin/decisions/heads/{id}/promote` · `/rollback` | `decisions:admin` | learning-loop control |

The OpenAPI contract is regenerated (`scripts/export_openapi.py`), and CI checks that the diff is
additive only (I7).

---

## 26. Frontend

Behind the feature flag `decisions` and RBAC; existing pages are unchanged apart from additive panels.

- **`features/decisions/` (new) Decision Console:**
  - points table (safety class, mode per cohort, agreement, fallback, latency, calibration badge);
  - point detail (confidence histogram, coverage/accuracy curve, disagreements explorer with the
    incumbent side by side, rollout history, a mode control that shows its gate status and blocks
    transitions whose gate hasn't passed);
  - engines page (health, revisions, capacity);
  - review queue (gold labelling with keyboard shortcuts);
  - specs (compiler UI: plain-English input → questions, lint diff, test results per engine,
    publish).
- **`features/workflow-builder`:**
  - `decision` node from the registry palette (question editor, state field picker with a live token
    meter per engine, low-confidence route);
  - LLM→decide conversion suggestion chip.
- **`features/settings`:** tenant residency, data sharing and decision-log state settings.
- **`features/models/ModelControlCenter`:** the `TYPED_DECISION` capability rows (from §18).
- **`features/approvals`:** `assist` rendering for G03 (predicted approval probability, sort by
  predicted risk).
- Tests: Vitest for components; Playwright e2e for console flows (mode change blocked by gate; spec
  compile → publish; review queue).

---

## 27. Deployment and serving

- **Compose** (`agent-verse-backend/infra/docker-compose.yml`): a `laya` service under profile
  `decisions`:
  - build from `infra/laya/` (`pip install "laya[serve]==0.3.21"` plus our wrapper, below);
  - env `LAYA_DEVICE`, `LAYA_MODELS=english,multilingual`, `LAYA_MAX_LOADED=2`, `LAYA_REVISION`,
    `LAYA_API_KEY`, `LAYA_THREADS≤physical cores`, `LAYA_MAX_CONCURRENT=16`;
  - HF cache volume; healthcheck `GET /health`; no host ports.
- **Wrapper `infra/laya/serve.py` (about 40 lines):** stock `laya-serve` honours only its three
  built-in model names (`_KNOWN_MODELS`). The wrapper builds `Router(models=…, revisions=…)` from a
  `LAYA_MODELS_MAP` JSON variable so fine-tuned heads can be served, and passes it to
  `laya.serve.create_app(router=…)`. Upstreaming `LAYA_MODELS_MAP` to Laya is tracked in §33.
- **Helm** (`infra/helm/agentverse`): a `laya` workload in `templates/app-workloads.yaml`, values in
  `values-*.yaml`:
  - GPU node pool (`nvidia.com/gpu: 1`), tolerations, ≥ 2 replicas (single worker per pod), PDB;
  - ClusterIP, NetworkPolicy, ExternalSecret for the key;
  - image with baked weights, `HF_HUB_OFFLINE=1`;
  - HPA on queue depth or GPU utilisation.
- **Local dev on macOS:** colima containers can't use MPS. Run natively:
  `LAYA_DEVICE=mps LAYA_PORT=8090 uvx --from "laya[serve]==0.3.21" laya-serve`, with the backend run
  through `uv run uvicorn` (`LAYA_BASE_URL=http://localhost:8090`). The CPU container with the
  multilingual checkpoint is acceptable for background testing.
- **Sovereign / air-gapped profile (X01):** backends `laya` + an on-prem LLM (`providers/onprem.py`);
  `external` disabled platform-wide; weights baked into the image.
- **Capacity guide:** a T4 serves about 100–330 questions/s batched, 33 ms for a single question.
  CPU runs at about 190–580 ms per question (use it only for background/S0 work). Jev: 1,200 rpm /
  250k tokens per second account limit (subject to change by the provider). Batch questions per
  stage.
- **Cost guide:** one T4 at about $300 a month ≈ 7.1B Jev tokens. Below that volume, Laya's
  advantages are latency, residency, multilingual support and fine-tunability, not price.

---

## 28. Use-case catalogue

**Columns:**
- **Src:** question source (A platform / B derived / C compiled / D agent).
- **Cls:** safety class.
- **Max:** maximum mode.
- **Engine path:** J = Jev, L = Laya base, LH = Laya head (after training), LLM.
- **Labels:** where gold/silver labels come from.
- **W:** rollout wave (§29.3).

"Incumbent" is the existing code kept as the fallback (I2).

### 28.1 Agent loop (A)

| ID | Use case | Incumbent (call site) | Questions (sketch) | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| A01 | Step-success pre-verifier | `agent/nodes/verifier_mixin.py` `_node_verify` (LLM verdict) | Noul "did `result` accomplish `objective`"; Noul "is `result` an error or empty" | A | S1 | live* | J → LH | verifier verdicts, goal outcomes | 3 |
| A02 | Claim grounding / NLI | `intelligence/nli_checker.py`, `intelligence/grounding_verification.py`, `agent/grounding.py` | Choice entails / contradicts / neutral over (claim, evidence) | A | S2 | augment | L (short) / J (long) | citation reviews, verifier calibration | 4 |
| A03 | Tool-output quality | `executor_mixin._is_uncacheable_output` (keywords) | Choice real_result / error / empty / model_reasoning | A | S1 | live | L → LH | cache outcomes | 3 |
| A04 | Replan cause | `rag/agentic/context_gap_detector.has_gap` (keywords), `recovery/failure_classifier.py` (regex), `org/failure_manager.py` | Choice knowledge_gap / wrong_tool / bad_args / auth / rate_limit / ambiguous_goal / external_outage | A | S1 | live | J → LH | replan outcomes | 3 |
| A05 | Consensus trigger | `agent/consensus.requires_consensus` (domain set) | Score risk; Noul irreversible | A | S2 | augment (may only add consensus) | J → LH | consensus disagreements | 4 |
| A06 | Reasoning-pattern scoring | `agent/patterns/peer_review.py`, `tree_of_thoughts.py`, `graph_of_thoughts.py`, `lats.py` (LLM JSON) | Score quality (4 levels); Noul promising | A | S1 | live | J → LH | pattern outcomes | 4 |
| A07 | Debate vote | `agent/debate.vote` (free-text ID + Counter) | Choice over proposals (≤ 20) | B | S1 | live | J / L | debate outcomes | 4 |

\*A01 may only *skip* the full verifier for `read`-tier steps with calibrated success above the
threshold. Any predicted failure, and any step above `read` tier, always goes to the full verifier.

### 28.2 Governance and risk (G)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| G01 | Step high-risk detection | `agent/nodes/_helpers._is_high_risk_step` (keywords + `\brm\b`); duplicate constants in `graph.py` | Choice read_only / reversible_write / irreversible / production_impacting; Noul "affects production systems" | A | S3 | augment | J + LH | HITL outcomes | 2 |
| G02 | Tool risk tier | `agent/tool_risk.classify_tool_risk`, `security_runtime/action_safety_profile.py`, `rpa/tools.classify_rpa_tool_risk` | Choice read / write_low / write_high / destructive over (tool, description, schema, args) | A | S3 | augment (tier = max) | J → LH | HITL approvals, operator overrides | 2 |
| G03 | HITL approval triage | `governance/hitl.py` queue (FIFO) | Noul "would the approver approve"; Score urgency | A | S0 (assist) / S3 for auto-approve | assist; auto-approve **opt-in, `write_low` only** | LH | approve/reject history | 3 |
| G04 | Org mission risk and task type | `org/goal_refinement._determine_risk_level`, `org/meta_orchestrator.py` (keywords) | Score risk; Choice task type | A | S2 (risk) / S1 (type) | augment / live | J → LH | org outcomes | 3 |
| G05 | Exfiltration in tool args | `agent/exfil_guard.check_tool_args_for_exfil` (regex + sinks) | Noul "do args send internal or secret data to an external destination" | A | S3 | augment | J → LH | incidents, red-team | 2 |

### 28.3 Guardrails and data protection (S)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| S01 | Goal input battery (GOAL layer) | `intelligence/guardrail_engine.LLMJudge`, `guardrails_v2` GOAL, `intelligence/guardrails.check_goal`, `encoding_attacks.py` | Nouls jailbreak / exfiltration / harmful / self_harm / social_engineering; Score severity | A | S2 | augment | LH (hot) + J escalation | incidents, red-team | 2 |
| S02 | Tool args (TOOL_ARGS) | `guardrails_v2` TOOL_ARGS, `guardrail_engine.RecursiveArgScanner` | Nouls injection-in-args / destructive intent / PII | A | S2 | augment | L → LH | incidents | 2 |
| S03 | Tool output / indirect injection | `guardrails_v2` TOOL_OUTPUT, `intelligence/indirect_injection.scan_tool_output` / `scan_rag_chunks`, `exfil_guard.check_tool_output_for_injection` | Noul per chunk "contains instructions aimed at an AI" | A | S2 | augment | L → LH | red-team corpora | 2 |
| S04 | Final output | `guardrails_v2` FINAL_OUTPUT, `guardrail_engine.OutputScanner`, `intelligence/output_anomaly.py` | Nouls policy break / harmful help / personal medical-legal-financial advice / PII leak | A | S2 | augment | L / J | reviews | 2 |
| S05 | Memory write / poisoning | `guardrails_v2` MEMORY_WRITE (`memory/long_term.py`) | Noul "tries to change future behaviour to bypass controls"; Noul "contains secrets" | A | S2 | augment | L → LH | incidents | 2 |
| S06 | Toxicity | `guardrails_v2/toxicity.ToxicityClassifier`, `_check_toxicity_llm` | Nouls per category; Score severity | A | S2 | augment | L → LH | moderation reviews | 2 |
| S07 | Streaming guard | `guardrails_v2/streaming_guard.py` (regex window) | Noul per window | A | S2 | augment | LH only (latency) | incidents | 5 |
| S08 | Tenant guardrail rules | `db/models/guardrail_rule.py`, `governance/policy_rules.py` | Noul per rule text | B | S2 | augment | J → LH | rule-hit reviews | 2 |
| S09 | Data classification | `data_classification/classifier.py` (regex) | Choice public / internal / confidential / restricted; Nouls PII / PHI / PCI / secret | A | S2 | augment (may only raise the class) | L → LH | reviews | 2 |
| S10 | Marketplace template review | `enterprise/marketplace_v2.TemplateSecurityReviewer` | Nouls hidden instructions / exfiltration / over-broad tools | A | S2 | augment | J | review decisions | 4 |
| S11 | Red-team grading | `enterprise/red_team._analyze_events` (keywords) | Noul "reply complied with the attack"; Score severity | A | S0 | live | L / J | red-team ground truth | 4 |

### 28.4 Routing and selection (R)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| R01 | Goal classification (feeds `multi_agent_selector`, `pattern_selector`, runtime profile) | `orchestration/goal_classifier.classify_fast` / `classify_with_llm` | Choice complexity, domain; Nouls requires_web / code / vision; Score reversibility | A | S1 (reversibility → S2 augment) | live | J → LH | outcomes | 3 |
| R02 | Model-tier routing (cost) | `agent/model_router.complexity_tier`, `providers/model_router.py` | Choice simple / moderate / complex | A | S1 | canary → live, with a quality guard | J → LH | goal success per tier | 3 |
| R03 | Agent routing | `agent/router.AgentRouter` (keyword/connector/history + LLM JSON) | Choice over agent descriptions (shortlist ≤ 20); margin → `needs_human_choice` | B | S1 | live | J (>20) / L → LH | user overrides | 3 |
| R04 | Skill selection | `agent/skill_selector.SkillSelector.select` (substring) | Choice shortlist + Noul per candidate "fits" | B | S1 | live | J → LH | skill usage outcomes | 3 |
| R05 | Tool shortlisting | `agent/tool_selector.ToolSelector.select`, `mcp/capability_search.py`, `tool_runtime/tool_ranker.py` | Noul per tool relevance over the pgvector top-k | B | S1 | live (orders or adds; never removes granted tools from eligibility) | L → LH | tool usage | 3 |
| R06 | RAG strategy selection | `rag/agentic/patterns/adaptive.select_adaptive_strategy`, `rag_platform/query_planner.py`, `code_rag.has_code_intent` | Choice strategy | A | S1 | live | L → LH | retrieval quality | 3 |
| R07 | Knowledge-base selection | (none; searches all KBs) | Choice over KB names and descriptions | B | S1 | live | L | retrieval acceptance | 3 |

### 28.5 Knowledge, RAG and memory (K)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| K01 | Document type at ingestion | `ingestion/content_classifier.py` | Choice document type | A | S1 | live | L (standard task) → LH | corrections | 1 |
| K02 | Sensitivity / PII beyond regex | `ingestion/pii.RegexPIIAnalyzer` | Noul personal data present; Choice sensitivity | A | S2 | augment (may only add redaction or raise the class) | L → LH | reviews | 1 |
| K03 | Quality / boilerplate | `ingestion/quality_checks.py` (noise regex) | Noul boilerplate or navigation; Score informativeness | A | S1 | live (marks `low_value`, deprioritises embedding; **never deletes content**) | L | retrieval usage | 1 |
| K04 | RAG evidence grading | `rag/agentic/patterns/corrective.grade_evidence`, `self_rag._critique` / `_should_retrieve`, `speculative.py`, `flare._detect_uncertainty` | Score relevance per passage; Noul contradicts premise | A | S1 | live | J → LH | thumbs, citation checks | 1 |
| K05 | Reranking (`RerankerProtocol` implementation) | `rag_platform/reranker._llm_rerank`, `rag/cross_encoder.py`, `rag_platform/hosted_reranker.py` | Noul relevance per (query, passage) | A | S1 | live | L (short passages) | click / accept | 4 |
| K06 | Citation verification | `evals/attribution_verifier.py` (Jaccard ≥ 0.15), `rag_platform/reranker.CitationVerifier` | Choice supports / partial / unsupported | A | S2 | augment | L / J | reviews | 4 |
| K07 | Memory worth-storing | `memory/long_term.extract_from_goal` (stores everything at 0.8) | Noul reusable learning; Choice preference / fact / procedure / ephemeral | A | S1 | live (gates the write; reversible) | J → LH | recall usage | 3 |
| K08 | Memory consolidation / dedup | `memory/consolidation.py`, `memory_v2/consolidation.py` (Jaccard) | Noul same fact (pair) | A | S1 | live (soft merge; originals kept) | L | merge reversals | 4 |
| K09 | Semantic-cache hit verification | `rag/semantic_cache.py` (cosine ≥ 0.92) | Noul "cached answer fully answers the new query" | A | S1 | live (can only reject a hit) | L | cache feedback | 3 |

### 28.6 Channels, chat, voice, triggers, notifications (C)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| C01 | Inbound channel intent (WhatsApp / Telegram / Slack / Teams) | `gateway/router._process_command` (every message → goal), `triggers/channels/gateway.NLIntentClassifier` (stub) | Choice over the tenant's channel intents (default set: order_status / refund / complaint / chit_chat / human); Noul urgent; Noul wants a human | A + B | S1 | live; **actions opt-in** (default `submit_goal`, I10) | L multilingual → LH | corrections, handoffs | 1 |
| C02 | Telephony opt-out and consent | `gateway/telephony_consent.OPT_OUT_KEYWORDS`, `gateway/router._is_opt_out` / `_is_affirmative` | Noul opt-out; Noul affirmative consent | A | S3 | augment (opt-out = keyword **or** engine; consent requires keyword **and** engine) | L multilingual | compliance reviews | 2 |
| C03 | Voice intent | `voice/intent_router.classify_intent` (regex, fixed 0.85) | Choice create_mission / approve / reject / summarize / status / search | A | S1; approve/reject S3 | live with a mandatory read-back confirmation for approve/reject | L → LH | confirmations | 5 |
| C04 | Chat intent | `chat/intent.IntentRouter` | Choice QA / GOAL / CLARIFY / SCHEDULE | A | S1 | live | L → LH | corrections | 1 |
| C05 | Chat understanding | `chat/understanding._classify_clause`, `chat/personalization.py` | Choice clause type; Noul standing instruction | A | S1 | live | L → LH | corrections | 3 |
| C06 | Trigger relevance filter | keyword triggers in `triggers/`, `proactive/signals.py` | Tenant condition compiled to Nouls + Score severity | C | S1 | live, **opt-in per trigger** (default unchanged) | J → LH | goal usefulness | 1 |
| C07 | Natural-language schedule type | `triggers/nl_scheduler.parse` (LLM JSON → regex fallback) | Choice trigger type (field extraction stays with the LLM or regex) | A | S1 | live | L | parse corrections | 3 |
| C08 | Notification severity and dedup | `gateway/notification_router.EVENT_SEVERITY_MAP`, `gateway/dedup_scheduler.py` | Score severity (free-text alerts); Noul same incident (pair) | A | S1 | live (dedup opt-in) | L | acknowledgements | 3 |

### 28.7 RPA, OCR, perception (P)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| P01 | RPA next action | `rpa/runner.py` / `executor.py` (LLM decides each step) | Choice element among ≤ 20 candidates; Choice click / type / select / done | A | S1 (side effects still gated by G02) | live **after head only** | LH (zero-shot is weak) | successful runs | 5 |
| P02 | RPA page state | `rpa/executor._detect_captcha` and heuristics | Choice login / captcha / error / success / content | A | S1 (captcha → human; never bypassed) | live | L → LH | run outcomes | 5 |
| P03 | OCR document type and value selection | `ocr/classifier.py`, `ocr/extractors` | Choice document type; Choice among regex candidates per field (values normalised and validated in code) | A | S1 | live with validators | L → LH | AP corrections | 5 |

### 28.8 Evals and observability (E)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| E01 | LLM-as-judge dimensions | `intelligence/eval_runner.py` (coherence, accuracy), `intelligence/eval_suite.LLMJudge` | Score per dimension with written levels | A | S1 (feeds rollout gates) | assist (co-signal; the LLM judge stays authoritative) | J (Laya is weakest on Score) | human eval labels | 4 |
| E02 | Trace failure taxonomy | (none) event store / observability | Choice wrong_tool / missing_credential / hallucinated / looped / policy_blocked / external_error / other | A | S0 | live | L (typed-decisions checkpoint) → LH | engineer reviews | 4 |
| E03 | Multi-turn eval turns | `evals/multi_turn_eval._score_turn` / `_judge_goal` | Score per turn | A | S1 | assist | J | human eval | 4 |

### 28.9 Workflows and tenant product (W)

| ID | Use case | Incumbent | Questions | Src | Cls | Max | Engine path | Labels | W |
|---|---|---|---|---|---|---|---|---|---|
| W01 | `decide` workflow step (manual or generated) | `llm` step (`json_output`) + `conditional` | per spec | C | declared per spec (tenant specs S0/S1; S2/S3 need platform approval) | live per workflow (a new capability; opt-in) | J → LH | HITL low-confidence reviews | 1 |
| W02 | LLM-step → decide conversion suggestion | builder lint | — | — | S0 | suggestion only | — | acceptance | 3 |
| W03 | Template selection at generation | `workflows/generate` → `WorkflowPlanner` | Choice over the template shortlist | B | S0 | assist | J / L | acceptance | 6 |
| W04 | Connector selection at generation | `WorkflowPlanner` (first 20 tool names) | Noul per connector per step | B | S0 | assist | L | acceptance | 6 |
| W05 | Agent `decide` / `decide_batch` MCP tool | (none; the LLM classifies in context) | agent-authored | D | S0/S1 | live | J / LLM; LH for promoted specs | outcomes | 3 |
| W06 | Tenant-trained decision heads | — | tenant specs | C | per spec | per spec | LH (tenant-scoped) | tenant labels | 6 |
| W07 | Decision packs in the marketplace | `enterprise/marketplace.py` | per pack (§28.11) | C | per pack | per pack | J → LH | pack labels | 6 |
| W08 | Extraction verification cascade | `llm` extraction steps | Noul per field "value supported by the source"; Noul "escalate" | A / C | S1 | live | J → LH | escalation outcomes | 4 |

### 28.10 Cross-cutting (X)

| ID | Capability | Notes | W |
|---|---|---|---|
| X01 | Sovereign / on-prem decision profile | backends = Laya + on-prem LLM; `external` disabled; weights baked into the image (§27) | available from wave 1 as config; packaged in wave 6 |
| X02 | Multilingual routing | Laya's script and language router; tenant `default_language_route`; per-language metrics and gates | 1 |
| X03 | Cost showback and budgets | §19; per-point cost dashboards | 1 |
| X04 | Semantic lint in CI (GitHub Action) | `agent-verse-github-action` runs a published spec over diffs or docs | 6 |

### 28.11 Decision packs (tenant-facing, W07): starter specs, all editable copies

| Pack | Decisions | Regulated? |
|---|---|---|
| Customer-support triage | department, intent, urgency, frustration, churn risk, refund request, policy-compliant reply check | no |
| Trust and safety / moderation | toxicity, harassment, spam, fraud, unsafe advice, personal-data exposure, opt-out, severity → allow / warn / review / block | no |
| Lead qualification | ICP fit, company maturity, buyer relevance, pain points, purchase intent, routing | no |
| Recruiting screening | job-related competency evidence scores, role match | **yes**: HITL on adverse outcomes; bias evaluation |
| Insurance claims intake (FNOL) | claim type, complexity, missing information, fraud indicators, straight-through vs specialist | **yes** |
| Financial crime / KYC | suspicious narrative characteristics, entity match (pair), alert priority | **yes** |
| Legal and compliance | contract/policy classification, missing clauses, prohibited claims | yes (advice boundary) |
| E-commerce listings | category normalisation, attribute selection, prohibited or counterfeit signals | no |
| Advertising | brand safety, audience suitability, prohibited claims, ad-to-landing alignment | no |
| Gaming community | chat moderation, abuse, frustration, churn signals | no |
| Research screening | inclusion/exclusion criteria, theme labelling, citation support | no |
| Demand signals / ML features | purchase intent, urgency, product interest, competitive pressure (features for downstream models) | no |
| Code and writing lint | team conventions as Nouls, in CI | no |
| Laya presets | `triage`, `guard`, `moderation`, `router` imported as starting templates | — |

### 28.12 Explicitly deterministic (not replaced; the framework may add signals only)
`governance/policies.PolicyEngine`, `governance/policy_rules.py` (field/operator rules), budgets and
cost controls, RLS, rate limiting, `enterprise/compliance*.py` DB checks, secret-pattern regex
(exfiltration and sanitisation), the `prompt_optimizer.py` significance test, the `eval_suite`
rollout pass-rate gate, and `resolve_effective_tool_risk` operator opt-ins.

### 28.13 Traceability: every use case discussed maps to a catalogue ID

| Discussed as | Catalogue |
|---|---|
| Jev harness list #1 guardrail battery · #2 indirect injection · #3 RAG grading · #4 step/tool risk · #5 channel intent · #6 chat intent | S01–S04 · S03 · K04 · G01/G02 · C01 · C04 |
| #7 verifier pre-check · #8 grounding · #9 agent routing · #10 goal classification · #11 model routing · #12 skill selection | A01 · A02 · R03 · R01 · R02 · R04 |
| #13 tool shortlisting · #14 replan routing · #15 memory worth-storing · #16 tool-output quality · #17 LLM judge · #18 reranker | R05 · A04 · K07 · A03 · E01 · K05 |
| #19 citation verification · #20 debate/peer review · #21 ToT/LATS · #22 cheap-then-verify extraction · #23 trace classification · #24 red-team grading · #25 template review | K06 · A06/A07 · A06 · W08 · E02 · S11 · S10 |
| Real-world ① multilingual channels · ② opt-out · ③ voice · ④ tool-call guardrails · ⑤ indirect injection | C01/X02 · C02 · C03 · S01/S02 · S03 |
| ⑥ HITL fatigue · ⑦ step risk · ⑧ model tier · ⑨ pre-verifier · ⑩ ingestion | G03 · G01 · R02 · A01 · K01–K03 |
| ⑪ RAG evidence · ⑫ memory · ⑬ RPA · ⑭ OCR · ⑮ triggers · ⑯ chat intent | K04 · K07/S05 · P01/P02 · P03 · C06 · C04/C05 |
| ⑰ notifications · ⑱ on-prem tenant · ⑲ failure taxonomy · ⑳ decision node / tenant heads / packs | C08 · X01/S09 · E02/S11 · W01/W06/W07/S10 |
| Source B examples (agent / skill / tool / KB / guardrail rules / channel intents) | R03 / R04 / G02+R05 / R07 / S08 / C01 |
| Dynamic workflows: decide step, generator rule, linter, conversion, template and connector assist, agent tool | W01 (§16.1), §16.2, §11, W02, W03, W04, W05 |
| Additional points found in the codebase map | A05, G04, G05, S07, S09, R06, K08, K09, C07, C08 |
| Tenant product list (triage, moderation, leads, recruiting, claims, fin-crime, legal, e-commerce, ads, gaming, research, features, CI lint, big-data map-reduce) | §28.11 packs, W05 (bulk), X04 |

---

## 29. Rollout plan: shadow to live

### 29.1 Per-point stage machine

```
 OFF ──▶ SHADOW ──▶ EVALUATE ──▶ ASSIST / AUGMENT ──▶ CANARY ──▶ LIVE (GA)
  ▲        │           │               │                  │          │
  └────────┴───────────┴── rollback (config, ≤30 s, audited) ◀───────┘
 S2/S3 stop at AUGMENT. S0 may go ASSIST → LIVE directly after EVALUATE.
```

| Stage | Entry criteria | What happens | Exit criteria (all required) | Automatic rollback triggers |
|---|---|---|---|---|
| **0 OFF** | Code merged | Nothing new happens (I1). | Parity tests green; flags off in all environments. | — |
| **1 SHADOW** | Backend healthy; point registered; dashboards live | Async, sampled engine calls (1% → 10% → 100% in dev/staging/prod). Incumbent authoritative. | ≥ 2,000 decisions or 14 days, whichever is later; engine error rate < 1%; shadow drop rate < 0.5%; p95 latency within budget (measured in shadow); agreement and disagreement explorer reviewed by the point owner. | Error rate > 5% → back to OFF for that backend |
| **2 EVALUATE** | ≥ 200 gold labels (≥ 300 for Nouls), or a curated evaluation set | Calibration fitted; coverage/accuracy curve; threshold chosen; per-slice report; comparison vs incumbent on gold. | Engine ≥ incumbent on the gold set (S1: non-inferior + a win on quality, latency or cost; S2/S3: recall ≥ incumbent recall and false-positive budget respected); ECE ≤ 0.08; no slice regression > 3 points; signed off by the point owner and security (S2/S3). | — |
| **3a ASSIST** (S0/S1) | EVALUATE passed | Engine output shown or used as a suggestion. | ≥ 7 days; suggestion acceptance ≥ the target; no incident. | Acceptance collapses (> 20 points drop) |
| **3b AUGMENT** (S2/S3; optional for S1) | EVALUATE passed | `stricter(incumbent, engine)` inline. | ≥ 14 days; added blocks or HITL ≤ the point's `max_review_increase` (default +2% of volume); sampled false-positive review ≤ 10%; latency within budget. **S2/S3 remain here permanently.** | HITL volume > 2× budget, or p95 > 2× budget → SHADOW |
| **4 CANARY** (S0/S1) | ASSIST passed (or EVALUATE for S0) | Engine authoritative for the cohort: internal tenant → 5% → 25% → 50% of eligible tenants (hash-bucketed), each step ≥ 3 days. `always_run_incumbent=true`. | Per step: outcome metrics non-inferior to control (goal success, CSAT proxies, retrieval acceptance, cost); fallback < 2%; low-confidence rate within the forecast. | Any outcome metric regresses beyond its guard band → previous step |
| **5 LIVE (GA)** | 50% canary passed | Engine authoritative for all eligible tenants. The incumbent runs for fallback, plus continued comparison for 30 days. | 30 days stable → eligible for incumbent code retirement (a separate PR, I2). Continuous drift monitoring. | Drift alerts → recalibrate; severe → CANARY |
| **6 ENGINE MIGRATION** | A candidate head or backend passes the EvalGate (§15) | New backend shadowed against the current authoritative backend, then canaried. | Same as stages 1–4, but against the current engine instead of the incumbent. | Same |

**Per-tenant overrides:** tenants may stay at a lower mode (e.g. `shadow`) indefinitely, or join
canaries early (design partners). They can't exceed the platform `max_mode`.

### 29.2 Global prerequisites (before any point leaves OFF)
1. M0 prerequisites merged (§30): decision LLM calls go through charging and breakers.
2. The framework, stores and dashboards are deployed. The kill switch has been tested in staging
   (flip → all points fall back to the incumbent in ≤ 30 s).
3. `laya-serve` deployed with a pinned revision and a healthy `/health` (GPU device confirmed). The
   Jev key is configured (if used) with rpm limits.
4. Residency defaults applied to all existing tenants (`self_hosted_only` for the regulated set).
5. Runbook published; on-call briefed; alerts routed.
6. Differential shadow test green (§31.5).

### 29.3 Waves (ordered by value and risk; a wave starts when the previous wave's points are ≥ SHADOW)

| Wave | Points | Rationale | Target end-state |
|---|---|---|---|
| **1 Foundations + low-risk value** | C01, C04, K01, K02 (augment), K03, K04, C06 (opt-in), W01 (opt-in beta), X02, X03 | Fixes real gaps (every message → goal; the NL intent stub), high volume, reversible. Most work zero-shot on Laya multilingual or Jev. | C01/C04/K01/K03/K04 LIVE; K02 AUGMENT; W01 GA for opt-in tenants |
| **2 Protection (augment only)** | S01–S06, S08, S09, G01, G02, G05, C02 | Stricter-of can't reduce safety. Heads are trained on red-team corpora and incidents. | All AUGMENT |
| **3 Routing, cost, loop quality** | R01–R07, A01, A03, A04, K07, K09, G03 (assist), G04, C05, C07, C08, W02, W05 | Largest cost lever (R02) and quality gains. Needs labels from waves 1–2. | S1 LIVE; G03 ASSIST (auto-approve opt-in for `write_low` only) |
| **4 Evals, ranking, verification** | E01–E03, K05, K06, K08, A02, A05–A07, S10, S11, W08 | Depends on calibration maturity. Score-heavy points stay on Jev. | E01/E03 ASSIST; S10/K06/A02/A05 AUGMENT; others LIVE |
| **5 Perception and real-time** | P01–P03, C03, S07 | Needs fine-tuned heads (P01 is weak zero-shot) and latency validation. | LIVE / AUGMENT per class |
| **6 Product and packaging** | W03, W04, W06, W07, X01 packaging, X04 | Tenant-facing features built on proven infrastructure. | GA |

### 29.4 Point owner checklist (copy into each point's rollout ticket)
- [ ] Incumbent wrapped; parity test (off/shadow = incumbent) added.
- [ ] `state_fn` minimal; budget verified for each target backend; redaction verified for external
      backends.
- [ ] Safety class, max mode and `stricter()` semantics declared; security review (S2/S3).
- [ ] Linter clean; spec hash recorded.
- [ ] Dashboards and alerts present; latency budget set.
- [ ] Label sources wired (gold + silver) and the outcome joiner tested.
- [ ] EVALUATE report attached (calibration, curve, slices, gold comparison).
- [ ] Rollback rehearsed in staging.
- [ ] Audit entries verified for each transition.

### 29.5 Communication and support
- Tenant-visible changes (C01 actions, C06 filtering, W01, G03 suggestions) ship with changelog
  entries and console banners, and are opt-in.
- Design-partner tenants are enrolled in canaries with explicit consent.

---

## 30. Implementation milestones

| M | Deliverable | Tests / gate | Waves unlocked |
|---|---|---|---|
| **M0** | Prerequisites: route `LLMJudge`, `grade_evidence`, `AgentRouter._score_by_llm`, `IntentRouter`, `EvalRunner`, and the toxicity check through `ChargingProvider` + circuit breaker; register the `decisions:*` scopes. | Existing suites green; new charging tests | — |
| **M1** | `app/decisions` core: types, spec, hashing, linter, StateBuilder, router, `FakeDecisionBackend`, `SystemOneHttpBackend`, `LLMDecisionBackend`, normaliser, CombinePolicy, ShadowDispatcher, logger (in-memory), ConfigResolver + pub/sub, Settings, metrics, traces. **Zero call sites.** | Unit + contract + property tests; mypy strict | — |
| **M2** | Persistence (migrations for §24), Postgres stores, lifespan wiring, health, `runtime_readiness`, model-registry capability, `MODEL_PRICING` entries; `laya-serve` compose profile + Helm + wrapper; Jev configuration. | Integration (testcontainers), least-privilege RLS tests, alembic single head | — |
| **M3** | Wave 1 call sites in SHADOW (C01, C04, K01–K04, C06); outcome joiners; label capture from HITL. | Parity + differential shadow test | 1 |
| **M4** | Decision Console (points, stats, disagreements, review queue, engines); tenant config API/UI; audit. | Vitest + Playwright | 1 |
| **M5** | Calibration, thresholds, EvalGate reports; ASSIST/AUGMENT/CANARY/LIVE enforcement in the UI and API (gate-blocked transitions). | Gate enforcement tests | 1–2 |
| **M6** | Workflow `decide` step, DSL fields, validator + linter, low-confidence routing, `foreach` batching, builder node, conversion suggestion; Decision Compiler + spec lifecycle API/UI; generator rule behind its flag. | DSL golden tests (all templates unchanged), e2e builder flow | 1 (W01), 3 (W02) |
| **M7** | Wave 2 protective points in SHADOW → AUGMENT; red-team corpora ingestion. | Red-team regression suite; chaos | 2 |
| **M8** | MCP `decisions` server (W05); wave 3 call sites. | MCP e2e | 3 |
| **M9** | Learning loop: dataset builder, HeadTrainer (GPU queue), EvalGate automation, head registry, promotion/rollback, drift monitors. | End-to-end training on fixtures; promotion rollback test | 3–5 |
| **M10** | Waves 4–5 call sites; RPA/OCR heads; voice confirmations; streaming guard head. | Latency and load tests (`infra/loadtest`) | 4–5 |
| **M11** | Decision packs, tenant-trained heads, sovereign packaging, template/connector assists, GitHub Action lint. | Pack evaluations; regulated-pack controls | 6 |

---

## 31. Testing strategy

1. **Unit:** types, hashing stability, linter rules (a positive and negative example for each rule),
   StateBuilder budgets and truncation, CombinePolicy `stricter()` for every output type, router
   eligibility and ordering, normalisation (Jev vs Laya confidence → the same `top_probability`),
   config precedence and floors.
2. **Property-based:** safety monotonicity. For any S2/S3 point, incumbent result `x` and engine
   result `y`, `effective ⪰ x`. Mode `off`/`shadow` → `effective == incumbent`.
3. **Backend contract suite:** golden `/v1/systemone` request/response fixtures for Jev and Laya
   (including 401/422/429/503/529, `Retry-After`, malformed payloads, option collapse,
   `usage.options`). Every backend, including future ones, must pass.
4. **Parity tests:** one per point. With the framework disabled, and in shadow, the call site's
   output is byte-identical to the pre-framework behaviour.
5. **Differential shadow test (CI job):** run the existing integration and e2e suites twice, with
   framework off and with all points in SHADOW on `FakeDecisionBackend`. Assert identical goal
   outcomes, events (excluding `decision.*`), costs (excluding decision lines) and API responses.
6. **Integration (marker `integration`):** Postgres/Redis stores, RLS under the NOBYPASSRLS role,
   partitioning, pub/sub propagation across two app instances (≤ 30 s), HITL low-confidence routing.
7. **Real engine (markers `slow`/`integration`, opt-in):** a `laya-serve` CPU container
   (multilingual checkpoint) via testcontainers; a Jev sandbox key if available.
8. **Chaos (`infra/chaos`):** backend down, slow (timeout), 503 storms, revision change mid-flight,
   Redis loss (config cache), shadow queue flood. Assert invariants I3, I4 and I12.
9. **Load (`infra/loadtest`):** hot-path points at target rps; p95 within budget; bulkhead isolation
   between tenants.
10. **Frontend:** Vitest (console components, builder node, token meter); Playwright (mode transition
    blocked by gate; compile → publish; review queue labelling).
11. **Security:** injection-in-state corpora for S2/S3 (the effective result must never be less strict
    than the incumbent); SSRF tests for engine URLs; secret redaction in logs and traces; D-source
    decisions rejected by governance points.

---

## 32. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Laya zero-shot accuracy too low for custom decisions | High | Med | Zero-shot eligibility filter (§7.1.5); Jev/LLM first; heads before live |
| Laya is a very young project (created 2026-09-18, v0.3.x, fast-moving, 178 open issues) | High | Med | Pin version, revision and SHA; wrapper isolates the API; contract tests; Jev/LLM fallback; upgrades go through Stage 6 |
| Jev rate limits change ("adjusting dynamically") | Med | Med | Redis token bucket; batching; Laya/LLM fallback |
| Over-confident engines → wrong automated actions | Med | High | Mandatory calibration; S2/S3 never live; canary guard bands |
| Negation failures (Laya) in consent or risk decisions | Med | High | Linter L003; S3 augment only; C03 read-back confirmation |
| Injection in state lowers detection | Med | High | Monotonic safety; incumbent layers retained; red-team gates |
| HITL volume spikes in augment | Med | Med | `max_review_increase` gate + automatic rollback |
| Data-residency violations | Low | High | Router eligibility + residency tests; regulated-domain defaults |
| Tenant confusion from behaviour changes | Med | Med | I10 opt-in actions; banners; changelog |
| Cost regressions (Jev volume) | Low | Low | Budgets; showback; router cost ordering |
| Label scarcity blocks promotion | Med | Med | Low-confidence HITL review creates gold labels; synthetic plus curated sets; silver outcomes |
| Provider terms prohibit distillation | Med | Med | Bronze labels disabled unless legal approves (§33) |

---

## 33. Open decisions

1. **Distillation:** may Jev or LLM outputs be used to train Laya heads? Legal review of TypeSafe's
   and each LLM provider's terms is needed. Default: **no** (bronze labels disabled).
2. **GPU budget:** the number of T4/L4 replicas for production; a GPU worker for training versus an
   external job runner.
3. **Jev adoption:** an enterprise agreement (ZDR, higher rate limits) versus Laya plus LLM only.
4. **Platform head data:** whether to invite opt-in data sharing from design partners for source A
   heads.
5. **Upstream contribution:** propose `LAYA_MODELS_MAP` (custom checkpoints in `laya-serve`) upstream
   to remove the wrapper.
6. **Default channel intents:** the default intent set for C01 per vertical.
7. **Retention:** confirm 180 days for `decision_log` against the compliance programme.

---

## 34. Appendices

### Appendix A: `DecisionService.evaluate` pipeline (pseudocode)

```python
async def evaluate(point_or_spec, state_ctx, tenant_ctx, *, incumbent, goal_id=None):
    cfg = config.resolve(point_or_spec, tenant_ctx)             # precedence §23.1
    if not settings.decision_engine_enabled or cfg.mode is Mode.OFF:
        return passthrough(incumbent())                         # I1
    inc = await run_incumbent_if_needed(cfg, incumbent)         # §12.3
    if cfg.mode is Mode.SHADOW:
        shadow.submit(point_or_spec, state_ctx, tenant_ctx, inc)   # I3: async, bounded, sampled
        return passthrough(inc)
    try:
        spec = specs.materialise(point_or_spec, tenant_ctx)     # A/B/C/D; lint verdict cached
        state = state_builder.build(spec, state_ctx, tenant_ctx)
        chain = router.route(spec, state, cfg, tenant_ctx)      # §7
        raw = await executor.run(chain, spec, state, budget=cfg.latency_budget_ms,
                                 tenant_ctx=tenant_ctx, goal_id=goal_id)   # breaker, bulkhead, charging
        res = normaliser.apply(raw, calibration.lookup(raw.backend, raw.revision, spec.hash))
        res = rules.evaluate(spec, res, state_ctx)              # ExpressionEngine
        eff = combine.apply(cfg.mode, spec.safety_class, inc, res, cfg)   # I5
    except Exception as exc:                                    # I4: never raise into the call site
        eff = combine.on_error(spec.safety_class, inc, exc)
    logger.record(...)                                          # decision_log, metrics, traces
    return eff
```

### Appendix B: DecisionSpec JSON schema
Published at `app/decisions/spec.schema.json` (generated from the pydantic models). Fields as §9.1.
`additionalProperties: false`. `questions` has 1–64 entries; Choice has 2–255 options (the linter
narrows this per backend); Score has 2–10 levels.

### Appendix C: engine capability matrix (initial values; confirm at integration time)

| Capability | `laya-serve` english | `laya-serve` multilingual | Jev 1.13.0 | LLM backend |
|---|---|---|---|---|
| max Choice options (reliable) | ~20 (server cap 100) | ~20 (server cap 100) | 255 | ~50 |
| max Score levels | 32 (server) | 32 | 10 | 10 |
| max questions per call | 64 | 64 | (by token budget) | ~20 |
| state tokens | ~320 (512 context) | ~768 (1,024; 8k with `max_len`) | 32k | model context |
| languages | English | 45/51 usable | English best | model-dependent |
| Noul labels | yes (recommended) | yes | no | n/a |
| confidence field | 1 − normalised entropy | same | (n·pmax − 1)/(n − 1) | none |
| calibrated as shipped | over-confident | no temperatures shipped | yes (claimed) | no |
| residency | self-hosted | self-hosted | external | per provider |
| fine-tunable | yes | yes | no | no |
| p50 latency | ~40 ms (T4) | ~33 ms (T4) | ~236–276 ms | 0.5–3 s |

### Appendix D: wire-protocol normalisation
- Request: `{state, model, questions{qid: {type, instructions, criteria[, labels]}}}`.
- Response: `{model, answers{qid: {type, choice|score|noul, probabilities?, legend?, confidence?}}, usage{input_tokens, output_tokens}}`.
- Normalise:
  - Noul `p` → `distribution={"false": 1−p, "true": p}`;
  - Score `probabilities` keyed by level index → `distribution`; `value = score`;
  - Choice `probabilities` → `distribution`; `value = choice`;
  - `top_probability = max(distribution)`;
  - `model` → `model_revision`.

### Appendix E: call-site integration template

```python
from app.decisions import points, evaluate, StateContext
result = await evaluate(
    point=points.<area>.<POINT>,
    state_ctx=StateContext(**minimal_fields),
    tenant_ctx=tenant_ctx, goal_id=goal_id,
    incumbent=lambda: <existing_function>(<same_args>),
)
use(result.effective)     # same type as before
```

### Appendix F: example dynamically generated workflow

```yaml
name: support-email-triage
trigger: {type: event, event: {channel: email.support}}
steps:
  - id: classify
    type: decide
    state: {subject: "{{trigger.subject}}", body: "{{trigger.body}}"}
    questions:
      intent: {type: choice, instructions: "What is the sender asking for?",
               criteria: {refund: "wants money back or a return", faq: "a how-to or policy question", other: "anything else"}}
      angry:  {type: noul, instructions: "Is the sender angry or threatening to leave?",
               criteria: {true: "angry or threatening to churn", false: "calm or neutral"}}
    min_confidence: 0.75
    on_low_confidence: review
  - id: amount
    type: transform          # regex → number; arithmetic stays in code
    depends_on: []
  - id: route
    type: conditional
    depends_on: [classify, amount]
    branches:
      - {condition: "{{classify.intent}} == 'refund' and {{classify.angry}} > 0.7 and {{amount.value}} > 5000", next: jira}
      - {condition: "default", next: faq}
  - {id: jira, type: tool, tool: jira.create_issue, depends_on: [route]}
  - {id: faq, type: rag, depends_on: [route]}
```

### Appendix G: environment variables reference
All keys from §23.2 upper-cased (`DECISION_ENGINE_ENABLED`, `LAYA_BASE_URL`, `LAYA_API_KEY`,
`LAYA_ALLOW_INTERNAL`, `LAYA_EXPECTED_REVISION`, `TYPESAFE_API_KEY`, `TYPESAFE_MODEL`, …), plus
laya-serve's own variables (`LAYA_DEVICE`, `LAYA_MODELS`, `LAYA_MAX_LOADED`, `LAYA_REVISION`,
`LAYA_THREADS`, `LAYA_MAX_CONCURRENT`, `LAYA_MAX_TOKEN_BUDGET`, `LAYA_MODELS_MAP` (wrapper),
`HF_HUB_OFFLINE`).

### Appendix H: runbook outline (`infra/runbooks/decision-engine.md`)
1. **Kill switch:** set `DECISION_ENGINE_ENABLED=false` or set a point's mode to `off` in the
   console. Verify within 30 s on the `decision_requests_total{source="incumbent"}` panel.
2. **Backend down:** check `/health`, device and revision. The chain falls back automatically.
   Scale replicas or restart the pod. Confirm the circuit closes.
3. **Revision mismatch alert:** confirm the intended upgrade. If intended, run a Stage 6 migration;
   if not, redeploy the pinned image.
4. **HITL spike:** identify the point in the dashboard, roll back AUGMENT → SHADOW, open an incident.
5. **Jev 429 storm:** lower the rpm bucket, shift the order to Laya/LLM for S0/S1 points.
6. **Drift alert:** recalibrate (console action); if still failing, retrain the head, or roll back to
   the previous backend.
7. **Data-residency incident:** flip the tenant to `self_hosted_only`, audit the log for external
   calls, notify per the DPA.

### Appendix I: pre-existing issues surfaced (fix independently; the framework doesn't depend on deleting them)
- Decision LLM calls bypass charging and breakers (M0).
- `NLIntentClassifier` is a stub (replaced by C01 when promoted; kept as the incumbent until then).
- `gateway/router._process_command` submits every message as a goal (C01 actions, opt-in).
- `app/agent/goal_classifier.py` duplicates `app/orchestration/goal_classifier.py` and has no
  imports.
- `app/agent/graph.py` defines unused `_HIGH_RISK_KEYWORDS` / `_RM_COMMAND_PATTERN`.
- `SkillSelector` docstring claims embeddings; the code uses substring matching only.
- `app/ai_router/complexity_scorer.py` has no callers.
