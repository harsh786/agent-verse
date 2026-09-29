# Typed-Decision Framework: Implementation Roadmap and Quality Matrices

> **For agentic workers:** this is the index for a series of implementation plans. Execute each
> plan with superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.
> Every plan inherits the **Global Constraints**, **TDD Protocol** and **quality matrices** below.
> A task isn't done until every matrix row that references it is green.

**Spec:** `docs/superpowers/specs/2026-09-29-typed-decision-framework-design.md` (read it first; the
plans argue from it).

**Why a series:** the spec spans 11 milestones and several independent subsystems (core engine,
persistence and serving, call sites, console, workflows, learning loop). One plan per milestone
keeps each plan reviewable, and each one produces working, tested software on its own.

---

## Global constraints (apply to every plan and task)

- Backend: Python 3.12 via `uv run`; ruff line-length 100, rules `E,F,I,N,UP,B,A,C4,SIM,RUF`; mypy
  `strict` (pydantic plugin). Config lives in `pyproject.toml` only.
- pytest: `asyncio_mode = "auto"`, `filterwarnings = ["error"]`, `--strict-markers`. Markers:
  `integration` (testcontainers), `slow` (real engines/LLMs), `e2e_full`.
- Integration tests on this machine: `colima start`,
  `DOCKER_HOST="unix:///Users/harsh/.colima/default/docker.sock"`, `TESTCONTAINERS_RYUK_DISABLED=true`.
- **No new runtime dependencies in the backend image** (spec I13). No new dev dependencies unless a
  plan says so explicitly. Available: `respx`, `fakeredis`, `testcontainers`, `pytest-asyncio`,
  `httpx2`.
- `DECISION_ENGINE_ENABLED` defaults to `false`. Every `DecisionPoint.default_mode` is `off`
  (spec I1).
- The framework never raises into a call site. Incumbent exceptions propagate unchanged (parity).
- S2/S3 points never exceed `augment` (spec I5); `stricter(incumbent, engine)` semantics.
- Additive persistence only; **single Alembic head** (currently `c8d2f4a6b1e3`); rechain at merge.
- Additive APIs only; the OpenAPI diff must be additive (`uv run python scripts/export_openapi.py`).
- Jev model pinned to `jev-1.13.0` (never an alias). Laya pinned to `laya[serve]==0.3.21` plus
  `LAYA_REVISION` and SHA-256 digests.
- External engines receive state only when the tenant's residency allows it (spec I11).
- Commits: conventional (`feat(decisions): …`, `test(decisions): …`), ending with the repo's
  attribution trailer.
- After code changes: `graphify update .` (repo convention, AST-only).

---

## Plan series

| Plan | File | Milestone | Scope | Depends on | Produces |
|---|---|---|---|---|---|
| 0 | (background task chip already queued: "Route decision LLM calls through cost + breaker") | M0 | Route `LLMJudge`, `grade_evidence`, `AgentRouter._score_by_llm`, `IntentRouter._llm_disambiguate`, `EvalRunner` scoring, and the `guardrails_v2` toxicity LLM through `ChargingProvider` + circuit breaker | — | governed incumbents |
| **1** | `2026-09-29-decisions-p1-core.md` | M1 | `app/decisions` core: types, spec and hashing, linter, state builder, backends (Fake, SystemOne HTTP, LLM), normaliser, combine, executor, router, config resolver, shadow dispatcher, logger and metrics, rules, point registry, service, settings, `create_app` wiring. **Zero call sites.** | 0 (soft) | a working, fully tested engine with no behaviour change |
| 2 | `…-decisions-p2-persistence-serving.md` | M2 | Alembic migrations (§24), Postgres stores (log partitioned monthly, config, calibration, labels, heads, rollouts), Redis config bus + shared circuit state + Redis bulkhead, lifespan swap, health poller, `runtime_readiness`, model-registry capability, `MODEL_PRICING`, CostPort → `CostController`, `laya-serve` compose profile + Helm + wrapper, Jev configuration | 1 | a production-grade backend |
| 3 | `…-decisions-p3-wave1-shadow.md` | M3 | Wave-1 call sites in SHADOW (C01, C04, K01–K04, C06), outcome joiners, HITL label capture, parity and differential tests | 2 | shadow data flowing |
| 4 | `…-decisions-p4-console-config-api.md` | M4 | `/api/v1/decisions/*` routes, scopes, audit, tenant config API, Decision Console (points, stats, disagreements, review queue, engines) | 2 | operators can observe and label |
| 5 | `…-decisions-p5-calibration-gates.md` | M5 | Calibration fitting, thresholds, EvalGate reports, gate-enforced mode transitions (ASSIST/AUGMENT/CANARY/LIVE), automatic rollback triggers | 3, 4 | controlled promotion |
| 6 | `…-decisions-p6-workflows-compiler.md` | M6 | `decide` step, DSL fields, validator and linter integration, low-confidence routing, `foreach` batching, builder node, conversion suggestion, Decision Compiler (LLM) with lint auto-fix and synthetic tests, spec lifecycle, generator rule behind its flag | 5 | dynamic workflows |
| 7 | `…-decisions-p7-wave2-protective.md` | M7 | S01–S06, S08, S09, G01, G02, G05, C02 in SHADOW → AUGMENT; red-team corpora | 5 | stricter-of protection |
| 8 | `…-decisions-p8-mcp-wave3.md` | M8 | `decisions` MCP server (W05); wave-3 call sites (R01–R07, A01, A03, A04, K07, K09, G03 assist, G04, C05, C07, C08, W02) | 5 | routing and cost wins |
| 9 | `…-decisions-p9-learning-loop.md` | M9 | Dataset builder, HeadTrainer (GPU queue), EvalGate automation, head registry, promotion and rollback, drift monitors, leader-elected nightly jobs | 5 | self-improving decisions |
| 10 | `…-decisions-p10-wave4-5.md` | M10 | E01–E03, K05, K06, K08, A02, A05–A07, S10, S11, W08, P01–P03, C03, S07 | 9 | full catalogue |
| 11 | `…-decisions-p11-product.md` | M11 | Decision packs, tenant-trained heads, sovereign packaging, template and connector assists (W03, W04), GitHub Action lint (X04) | 9 | tenant-facing product |

Each follow-on plan is written when its predecessor merges, so it can cite real, merged interfaces.
Plans 2–11 **must** include every matrix row below that names them.

---

## TDD protocol (mandatory for every task in every plan)

1. **Red:** write the smallest failing test that states the behaviour, named
   `test_<unit>_<behaviour>_<condition>`. Run it and confirm it fails for the *right reason*
   (assertion or missing symbol, not an import typo).
2. **Green:** write the minimal code to pass. No speculative options (YAGNI).
3. **Refactor:** remove duplication and keep functions under ~40 lines, with tests green.
4. **Gate:** `uv run ruff check app/decisions tests/decisions && uv run mypy app/decisions` plus the
   task's tests, then commit.
5. **Test design rules:**
   - One behaviour per test. Arrange-act-assert. No sleeps: use injected clocks, events or tiny
     delays bounded by `asyncio.timeout`.
   - Deterministic: seed any randomness. Hash-based sampling and bucketing are asserted with exact
     values.
   - Test **public interfaces**, not private helpers (except pure functions with a dedicated
     contract).
   - Every bug fix starts with a regression test that reproduces it.
   - **Finite domains are tested exhaustively** with `pytest.mark.parametrize` over the full cross
     product (combine semantics, mode × safety class). This is stronger than random property testing
     and needs no new dependency.
   - Each backend gets the shared **contract suite** (`tests/decisions/backend_contract.py`).
   - Security tests assert *absence*: no state text in logs or metrics, no egress when residency
     forbids it.
6. **Coverage gate:** `app/decisions/**` at ≥ 95% lines and ≥ 90% branches. CI runs
   `uv run pytest tests/decisions --cov=app/decisions --cov-branch --cov-fail-under=95`.
7. **Definition of done (task):** tests red then green in the commit history; lint, mypy and
   coverage gates green; matrix rows referencing the task are satisfied; no new warnings; the full
   `uv run pytest -m "not slow and not integration"` passes before the plan's final task.

---

## Test-case catalogue (IDs referenced by plans)

Prefixes:
- `UT` unit · `CT` contract · `XT` exhaustive-property · `IT` integration · `DT` differential/parity
- `CH` chaos · `LD` load · `SEC` security · `FE` frontend · `E2E` end-to-end · `DS` distributed-systems

| ID | Case | Plan · Task |
|---|---|---|
| UT-TYP-01..08 | Question validation: Noul criteria keys, labels distinct and non-empty; Choice 2–255 options, non-empty keys; Score 2–10 levels, non-empty; discriminated-union parse; frozen models; JSON round-trip | P1·T1 |
| UT-SPEC-01..07 | Spec hash stable under whitespace, key order and question-ID renames; changes on wording, option or level change; excludes policy, rule and engine_order; ID and qid patterns; 1–64 questions | P1·T2 |
| UT-LINT-L001..L016 | A positive and a negative case per linter rule; report `ok` only with zero errors | P1·T3 |
| UT-STATE-01..09 | Template path resolution (dict, attribute, missing → None); token estimate is conservative; head/tail/middle truncation; water-filling budget across keys; redaction only for external backends when `redact_for_external`; `state_fn` override; string states | P1·T4 |
| CT-BE-01..10 | Backend contract: an answer for every qid; types match; distributions sum to 1 ± 1e-6; choice value = argmax; Noul keys `{false,true}`; batch length and order; only `BackendError` escapes; latency ≥ 0; health shape; capabilities immutable | P1·T5, T6, T7 |
| UT-HTTP-01..16 | SystemOne HTTP: dialect rendering (Laya labels auto, Jev drops labels, `max_len` only for Laya); bearer header; SSRF blocked unless `allow_internal`; 200 parse per type; missing qid → non-retryable; probabilities renormalised; server choice ≠ argmax → argmax; 401/403 non-retryable and counted; 413/422 non-retryable and not counted; 429/503/529 retryable with `Retry-After` (seconds and HTTP-date, capped at 60); response size cap; timeout → retryable; health per dialect; no state text in exception messages | P1·T6 |
| UT-LLM-01..06 | LLM backend: schema per question type; untrusted-state system prompt; parse with and without probabilities; enum violation → error; provider exception → retryable error; `calibrated=False` | P1·T7 |
| UT-NORM-01..07 | `top_probability` = max; Jev vs Laya `confidence` fields ignored for thresholds; temperature scaling (T=1 identity, T>1 flattens, T<1 sharpens, clamped); Score expected value recomputed; Noul value = P(true); `calibrated` flag | P1·T8 |
| XT-COMB-01..06 | Exhaustive: FLAG OR, SEVERITY max, ORDERED max, SET union (order-preserving), ALLOW AND; mode semantics per mode × safety class × engine_ok; protective never less strict than incumbent | P1·T9 |
| UT-EXEC-01..11 | Chain fallback order; overall deadline respected; per-attempt timeout; breaker opens after threshold and half-open probe; non-retryable 422 not counted; `Retry-After` within budget retried once; `Retry-After` beyond budget skipped; tenant bulkhead rejects without waiting; in-flight counter cleans up; unexpected exception → next backend; attempts recorded | P1·T10 |
| UT-ROUTE-01..12 | Eligibility filters: residency, health, revision, options, levels, questions, state tokens, language (protective exclude / S1 deprioritise), zero-shot (weak excluded outside shadow unless standard_task, head or evaluated), pin; ordering: head first, engine_order, latency preference, LLM last; exclusion reasons reported | P1·T13 |
| UT-CFG-01..12 | Precedence (default → `*` override → point override); cap by point `max_mode` and platform floor; protective hard cap ≤ augment; kill switch → off; canary bucket deterministic and uniform (±3% over 10k tenants); canary→live in cohort / shadow outside; threshold = max of layers; residency intersection; cache TTL ≤ 30 s; bus invalidation; store failure → last known, else off | P1·T14 |
| UT-SHD-01..08 | `submit` never blocks (slow handler); drop on full queue counted; deterministic sampling (exact rate ±1% over 10k keys); workers process; handler exception counted and worker survives; lazy start; stop drains within timeout then cancels; not started without a running loop → counted drop | P1·T15 |
| UT-LOG-01..06 | Record fields; state never stored (hash only); logger never raises (store failure counted); metrics labels bounded (unknown point → `tenant_spec`, unknown backend → `other`); latency histogram observed; ring bound | P1·T16 |
| UT-RULE-01..07 | Boolean rule over answers and code inputs; numeric coercion of code inputs; unknown name → RuleError; blocked constructs (`__`, import, attribute) → RuleError; non-bool results coerced; multiple outputs; `ExpressionEngine.evaluate` unchanged (regression) | P1·T11 |
| UT-PT-01..06 | DecisionPoint: protective with `max_mode > augment` rejected; `default_mode != off` rejected; ID mismatch rejected; registry duplicate rejected; registry-wide invariants (lint clean, off, caps) | P1·T12 |
| UT-SVC-01..14 | Service: kill switch → incumbent, zero backend calls; off → incumbent; shadow → incumbent + job submitted; assist → incumbent + answers attached; augment → stricter; augment + engine error → incumbent; live → engine; live + low confidence → incumbent; live + engine error → incumbent; incumbent exception propagates unchanged; framework exception at every stage → incumbent; budget denied → incumbent; decision_id unique; log record written | P1·T17 |
| UT-WIRE-01..04 | Settings defaults; `create_app()` exposes `app.state.decision_service`; no backends registered by default (safe); `app.decisions.evaluate` public API | P1·T18 |
| DT-INV-01..06 | Invariants I1, I3, I4, I5, I12 and the registry, as a standing suite | P1·T19 |
| IT-PG-01..10 | Postgres stores under RLS (NOBYPASSRLS role), monthly partition creation and routing, retention batch deletes, label upsert idempotency, config optimistic versioning, single Alembic head, downgrade drops only own objects | P2 |
| IT-RDS-01..06 | Redis config bus across two app instances (≤ 30 s propagation; ≤ 1 s typical); bus loss → TTL fallback; shared breaker state across replicas; Redis bulkhead per tenant; fakeredis unit plus testcontainers integration | P2 |
| IT-SRV-01..05 | `laya-serve` (CPU, multilingual) via testcontainers: health, revision, one call per question type, 503 + `Retry-After` under `LAYA_MAX_CONCURRENT`, wrapper `LAYA_MODELS_MAP` | P2 (`slow`) |
| DT-PAR-* | One parity test per point: off/shadow output byte-identical to the pre-framework behaviour | P3, P7, P8, P10 |
| DT-DIFF-01 | Differential run of the integration and e2e suites with the framework off vs all points shadow (Fake): identical outcomes, events (excluding `decision.*`), costs (excluding decision lines), responses | P3 onward (CI job) |
| CH-01..08 | Backend down; slow (timeout); 503 storm; revision flip mid-flight; Redis loss; Postgres slow (log writer backpressure); shadow flood; Laya pod restart (thundering herd) | P2, P5 |
| LD-01..05 | Hot-path points at target rps with p95 ≤ budget; bulkhead isolation between tenants; shadow at 100% sample under peak; Jev token bucket under 1,200 rpm; log writer throughput ≥ 5k rows/s | P2, P5, P10 |
| SEC-01..14 | See the security matrix | various |
| FE-01..08 | Console components; gate-blocked transition; compile → publish; review queue; token meter; decision node; residency settings; RBAC hiding | P4, P6 |
| E2E-01..05 | Playwright: shadow → evaluate → augment flow; spec compile/publish; workflow with a decide step and HITL low-confidence path; rollback within 30 s; review queue labelling | P4–P6 |

---

## Security matrix (STRIDE)

| # | Threat (STRIDE) | Control | Test | Plan · Task |
|---|---|---|---|---|
| SEC-01 | **I**nformation disclosure: state content in logs, metrics or traces | Logger stores a state hash only; metrics have bounded labels; exception messages exclude state | UT-LOG-02, UT-HTTP-16 | P1·T6, T16 |
| SEC-02 | **I**: egress to external engines despite residency | Router residency filter; tests assert zero HTTP calls to external backends | UT-ROUTE-01, SEC-02 test in P2 | P1·T13, P2 |
| SEC-03 | **I**: PII to external engines | StateBuilder redaction for external backends when `redact_for_external` | UT-STATE-06 | P1·T4 |
| SEC-04 | **S**poofing/SSRF via engine URLs | `assert_public_url` unless `allow_internal`; tenant-hosted endpoints need operator approval | UT-HTTP-03, P2 tenant endpoint tests | P1·T6, P2 |
| SEC-05 | **T**ampering: injection in state lowers protection | Monotonic `stricter()`; protective hard cap; incumbent layers retained | XT-COMB-06, DT-INV-04 | P1·T9, T19 |
| SEC-06 | **E**levation: agent-authored (source D) decisions gating governance | Linter L012: AGENT + protective → error; points accept only declared sources | UT-LINT-L012, UT-PT-05 | P1·T3, T12 |
| SEC-07 | **E**: compiled rule executing code | `simpleeval` with a names map (no string interpolation), blocked patterns, no attributes or calls beyond the allowlist; linter L011 AST check | UT-RULE-04, UT-LINT-L011 | P1·T3, T11 |
| SEC-08 | **I**: credentials leaked | Keys from env/ExternalSecret or vault; never logged; `repr` of backends masks keys | UT-HTTP-02 (masked repr) | P1·T6, P2 |
| SEC-09 | **D**enial of service via decision floods | Per-tenant bulkhead (reject, no queue); bounded shadow queue; deadlines; response size cap; per-plan `decide_batch` caps | UT-EXEC-08, UT-SHD-02, UT-HTTP-14, P8 | P1·T10, T15, T6, P8 |
| SEC-10 | **T**: supply chain (model weights) | Revision + SHA-256 pinning; baked images; `HF_HUB_OFFLINE=1`; revision mismatch → shadow | IT-SRV-02, CH-04 | P2, P5 |
| SEC-11 | **R**epudiation: unaudited mode changes | Audit entries for every mode, threshold, spec and promotion change | P4 audit tests | P4, P5 |
| SEC-12 | **I**: cross-tenant data access | RLS + `FORCE ROW LEVEL SECURITY` on all tenant tables; NOBYPASSRLS tests | IT-PG-01 | P2 |
| SEC-13 | **E**: tenant loosening safety below the platform floor | ConfigResolver floors; API validation | UT-CFG-03/04, P4 API tests | P1·T14, P4 |
| SEC-14 | **S**: laya-serve exposed | ClusterIP + NetworkPolicy + bearer key; request caps | P2 Helm tests (template render asserts) | P2 |

---

## Scalability matrix

| Dimension | Design | Limit / target | Verified by | Plan |
|---|---|---|---|---|
| Hot-path latency | Inline only in assist/augment/live; per-point `latency_budget_ms`; GPU Laya for budgets < 150 ms | p95 ≤ budget (guard points ≤ 80 ms on Laya GPU) | LD-01 | P2, P5 |
| Throughput per engine | Multi-question single call; `evaluate_batch`; Laya replicas (single worker each) behind a Service; HPA on queue depth | T4 ≈ 100–330 q/s; Jev 1,200 rpm/account | LD-01, LD-04 | P2 |
| Tenant isolation | Per-tenant bulkhead (local in P1, Redis-shared in P2); per-plan caps | no tenant > configured in-flight | UT-EXEC-08, LD-02 | P1·T10, P2 |
| Shadow load | Deterministic sampling; bounded queue; worker pool; drop-and-count | 0 hot-path impact; drops < 0.5% at steady state | UT-SHD-*, LD-03 | P1·T15, P2 |
| Decision log volume | Monthly range partitions; async batched writer; indexes `(tenant_id, point_id, created_at)`, `(spec_hash, created_at)`; retention batch deletes | ≥ 5k rows/s writer; queries < 200 ms p95 for 30-day windows | LD-05, IT-PG-02/03 | P2 |
| Config reads | Resolver cache (TTL ≤ 30 s) + bus invalidation; no DB read per decision | ≥ 99% cache hit | UT-CFG-10 | P1·T14, P2 |
| Metrics cardinality | Bounded labels (registered points and backends; tenant specs collapsed) | < 5k series added | UT-LOG-04 | P1·T16 |
| Training jobs | Dedicated Celery queue `decisions.training`; GPU worker; one job per spec hash (advisory lock) | ≤ N concurrent (config) | P9 tests | P9 |
| Horizontal scale of API | Stateless service; all shared state in Postgres/Redis | linear to replica count | LD-01 at 1 vs 3 replicas | P2 |

---

## Reliability matrix

| Failure mode | Detection | Mitigation | Test | Plan · Task |
|---|---|---|---|---|
| Engine timeout | per-attempt `asyncio.timeout` | next backend, else incumbent | UT-EXEC-03 | P1·T10 |
| Engine down | breaker (5 failures → open 60 s; half-open probe) + health poller | skip while open; fallback | UT-EXEC-04, CH-01 | P1·T10, P2 |
| Rate limiting (429/529) and busy (503) | status codes | `Retry-After` within budget once, else next backend | UT-EXEC-06/07, UT-HTTP-12 | P1·T6, T10 |
| Invalid engine output | strict parse (qids, options, sums) | non-retryable error → next backend | UT-HTTP-06..08 | P1·T6 |
| Framework bug | catch-all around each pipeline stage | incumbent, error counted | UT-SVC-11, DT-INV-03 | P1·T17, T19 |
| Budget exhausted | CostPort `allow()` | incumbent | UT-SVC-12 | P1·T17, P2 |
| Config store outage | store exception | last known config; none → off | UT-CFG-12 | P1·T14 |
| Config bus loss (Redis) | subscriber error | TTL expiry bounds staleness to ≤ 30 s | UT-CFG-10, IT-RDS-02 | P1·T14, P2 |
| Log store outage or slowness | writer queue depth | bounded async writer; drop-and-count; never blocks decisions | UT-LOG-03, CH-06 | P1·T16, P2 |
| Model revision change | health reports the revision | backend drops to shadow for calibrated points | CH-04 | P2, P5 |
| Laya GPU → CPU fallback | health reports the device | alert; latency budget excludes Laya for hot-path points | P2 health tests | P2 |
| Thundering herd after a Laya restart | breaker half-open allows 1 probe per replica | probes, then close | CH-08 | P2 |
| Shadow overload | queue-full counter | drop, never block | UT-SHD-02 | P1·T15 |
| Deploy with mixed versions | — | spec hash stable; config schema versioned; unknown fields ignored; wire protocol unchanged | DS-07 | P2 |
| Rollback need | alerts / guard bands | mode change ≤ 30 s, no deploy | IT-RDS-01, E2E-04 | P2, P4 |

---

## Distributed-systems matrix

| # | Concern | Design decision | Test | Plan · Task |
|---|---|---|---|---|
| DS-01 | **Consistency of config across replicas** | Eventual consistency with bounded staleness: Redis pub/sub invalidation (typically < 1 s) + resolver TTL ≤ 30 s. Writes carry a monotonically increasing `version`; the resolver ignores older versions. | UT-CFG-10/11, IT-RDS-01 | P1·T14, P2 |
| DS-02 | **Deterministic cohorting across replicas** | Canary bucket = `sha256(point_id:tenant_id) % 100`; no RNG, no per-replica state | UT-CFG-06 | P1·T14 |
| DS-03 | **Deterministic sampling across replicas** | Shadow sampling = `sha256(sample_key)` threshold, so a goal is sampled consistently on every replica | UT-SHD-03 | P1·T15 |
| DS-04 | **Idempotency** | `decision_id` = UUID4 generated once per evaluation; the log insert is idempotent (`ON CONFLICT DO NOTHING`); label upsert unique on `(decision_id, tier, source)`; outcome joiners are replay-safe | IT-PG-04 | P1·T17, P2, P3 |
| DS-05 | **Circuit-breaker scope** | Per replica in P1 (fast, no network). P2 adds an optional shared Redis breaker state for engine-wide outages (open propagates cluster-wide), with local fallback when Redis is down | UT-EXEC-04, IT-RDS-03 | P1·T10, P2 |
| DS-06 | **Backpressure** | Reject rather than queue on the hot path (bulkhead); bounded queues for shadow and the log writer; deadlines everywhere; no unbounded retries (at most one retry, inside the deadline) | UT-EXEC-08, UT-SHD-02 | P1·T10, T15 |
| DS-07 | **Rolling deploys and schema evolution** | Expand/contract migrations (add tables and columns only; never rename in place); pydantic `extra="ignore"` on stored config payloads (`forbid` only on authoring APIs); spec hash independent of code version; wire protocol versionless and additive | P2 migration tests, DS-07 test (old/new resolver read the same row) | P2 |
| DS-08 | **Time** | Monotonic clock for deadlines, breakers and TTLs (`time.monotonic`); wall clock (UTC) only for records. Injected clocks in tests | UT-EXEC-02, UT-CFG-10 | P1·T10, T14 |
| DS-09 | **Leader election for periodic jobs** | Celery beat (single scheduler) plus a Postgres advisory lock per job key (calibration fit, head training, drift) to prevent duplicate runs after a failover | P9 tests (two workers, one runs) | P5, P9 |
| DS-10 | **Promotion races (split brain)** | Promotions and mode changes are single-row updates with optimistic concurrency (`version = version + 1 WHERE version = :expected`), mirrored to `decision_rollouts` in the same transaction | P4/P5 concurrency tests | P4, P5 |
| DS-11 | **Partition tolerance** | Redis down → local breakers, local bulkhead, TTL config; Postgres down → last-known config, log writer drops and counts; engine partition → fallback chain; the decision path never blocks on shared infrastructure | CH-05, CH-06 | P2 |
| DS-12 | **Tenant isolation at the data layer** | RLS on every tenant table; per-tenant bulkheads; tenant-scoped heads | IT-PG-01 | P2 |
| DS-13 | **Trace propagation** | OTel span `decision.<point>` as a child of the caller; `trace_id` stored on log rows; shadow jobs carry the parent context (linked span) | P2 tracing test | P2 |
| DS-14 | **Cache stampede** | Resolver cache per (tenant, point) with jittered TTL (25–30 s); single-flight per key | UT-CFG-10 | P1·T14 |
| DS-15 | **Exactly-once-ish outcome joins** | Joiners read events by cursor (created_at, id), upsert labels idempotently, and commit the cursor in the same transaction | P3 joiner tests | P3 |
| DS-16 | **Multi-region / data residency** | Engine endpoints configured per deployment region; residency enforced per tenant; no cross-region state in requests | P2 config tests | P2 |

---

## SLOs (targets for the production rollout, instrumented in P2)

| SLI | SLO |
|---|---|
| Decision availability (a result returned, engine or incumbent) | 99.99% (the incumbent fallback makes this equal to the call site's own availability) |
| Engine success rate (non-fallback) for live points | ≥ 99.0% over 30 days |
| Hot-path added latency p95 (augment/live guard points) | ≤ 80 ms (Laya GPU) / ≤ 400 ms (Jev) |
| Config propagation | p99 ≤ 5 s; hard bound 30 s |
| Shadow drop rate | ≤ 0.5% |
| Decision log write success | ≥ 99.9% (drops counted) |

---

## CI gates (added by P1, extended by later plans)

1. `uv run ruff check .` and `uv run mypy app` (strict).
2. `uv run pytest tests/decisions --cov=app/decisions --cov-branch --cov-fail-under=95`.
3. `uv run pytest -m "not slow and not integration"` (full suite unchanged, I9).
4. P2 onward: `uv run pytest -m integration tests/decisions`, `uv run alembic heads` shows exactly
   one head, and the OpenAPI additive-diff check.
5. P3 onward: DT-DIFF-01 differential job.
6. P4 onward: frontend `npm run lint && npm run typecheck && npm run test`; Playwright E2E on
   main.
