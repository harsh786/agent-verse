# P0 — live real-world baseline (2026-10-05)

Stack redeployed from local `main` @ `7e8eeb7b1` (not pushed). No app code was changed in this phase.
Generated suite output lives next to this file in `p0-baseline/` (`real_world_report.md/.json`,
`backend_results.jsonl`, `backend_pytest.log`, `playwright*.{log,json}`), plus
`p0-baseline/supplement-free-plan/` (one free-plan rerun, see below). All files were checked for the
tenant keys and common secret patterns; nothing matched.

## 1. Stack health after redeploy

| Check | Result |
|---|---|
| Image build (`docker-compose -f infra/docker-compose.yml build` db-migrate, backend, worker, subgoal-worker, beat, workflow-worker, frontend) | rc=0, **528 s** (uv sync layer 135 s, frontend `npm ci` + vite build, backend image 4.86 GB before rebuild) |
| `db-migrate` | exited 0; `alembic heads` = `700ac039283e` (single head); DB `alembic_version` = `700ac039283e` |
| `/health` | 200 `healthy` (postgres, redis, trigger_consumers up); embedder `dedicated` `nvidia/nemotron-3-embed-1b`, dim 2048 |
| `/livez` | 200 `{"status":"alive"}` |
| `/health/ready` | 200 `ready`, `startup_seconds` 4.47 |
| Containers | backend, worker, subgoal-worker, workflow-worker, beat, frontend all `healthy`; **RestartCount 0, OOMKilled false** for all, from deploy (08:25 UTC) through the end of the run (~10:15 UTC) |
| launchd `run_forever` | did not start a second API/worker/beat (log: "docker compose backend is running … not starting a second API") |

Before the redeploy the old `backend` container was **crash-looping**: `ModuleNotFoundError: No module named
'app.api.a2a_remote_agents'` from `app/bootstrap/routers.py:15`. The compose file bind-mounts the checkout's
`app/bootstrap`, `app/org`, `app/gateway` and `app/agent/graph.py` over a 2-day-old image, so new routers
imported modules the image did not have. The rebuild fixed it. Any future `git pull` without a rebuild
will break the API the same way.

Container memory (`docker stats --no-stream`):

| Container | Idle after deploy | During KB/goal load | After full run |
|---|---|---|---|
| backend | 749 MiB | 850 MiB (CPU 250 %) | 1.87 GiB |
| worker (limit 2 GiB) | 1.22 GiB (61 %) | 1.17 GiB | 938 MiB |
| workflow-worker (limit 2 GiB) | 823 MiB (40 %) | 806 MiB | 681 MiB |
| subgoal-worker (limit 2 GiB) | 207 MiB | 571 MiB | 571 MiB |
| beat | 118 MiB | 119 MiB | 120 MiB |
| postgres | 268 MiB | 234 MiB | 237 MiB |
| redis | 1.13 GiB | 1.13 GiB | 1.13 GiB |
| frontend | 7 MiB | – | 8 MiB |

Stack-level observations from `docker logs --since 2h` (not tied to one scenario):

- **LLM provider throttling.** The only configured provider is NVIDIA (`nemotron-3-super-120b`, a reasoning model).

  | Container | 200 OK | 429 | `llm_empty_completion_retrying` (reasoning model used up `max_tokens`) |
  |---|---|---|---|
  | backend | 5390 | 49 | 24 |
  | worker | 2268 | 243 | 199 |
  | workflow-worker | 952 | 70 | 157 |
  | subgoal-worker | 98 | 6 | 3 |

  Several failures below are these 429s or empty completions, and the code does not handle either one.
- **L-01 is still open.** `RuntimeError: Event loop is closed` appeared 214× in worker and 534× in workflow-worker, along with
  `RuntimeError: no running event loop` from `app/workflow/celery_tasks.py:219`. The trigger is an httpx `AsyncClient.aclose()` that
  runs after the per-task loop has closed.
- **A broken published workflow keeps running.** Workflow `a2392117-…` ("Renamed WF", tenant `46e3a316…`, created 2026-09-30) is published with a step
  of type `llm_prompt`. It raised `UnknownStepTypeError` in 27 `workflow.execute_workflow_run`
  tasks in 110 min (`app/workflow/compiler.py:348` → `app/workflow/registry.py:75`). The definition was
  accepted at save/publish, so validation is missing there.
- **OTel export errors.** backend logged 127× `Failed to export traces to otel-collector:4317 … UNAVAILABLE`. The collector is
  up and reachable now, so the failures are intermittent.

## 2. Tenants and environment used

Tenant files live only under `/private/tmp/claude-501/rw/`. They are the tenants from the 2026-10-02 run
(`rw-primary-1790970263` free, `rw-second-1790970263` free, `rw-enterprise-1790970263` enterprise), reused.

- **First attempt aborted (kept outside the repo).** The free `rw-primary` tenant still owns the leftover
  `rw-complex-kb-95f0c6a7` collection from 2026-10-02. The free plan allows **1** knowledge collection, so every
  KB scenario got `429 PLAN_LIMIT_EXCEEDED`. Leftover data was not deleted. Instead, the
  **enterprise tenant was used as the primary tenant** (`AGENTVERSE_TENANT_FILE`) and as `RW_ENTERPRISE_TENANT_FILE`.
  A second key was minted on it (`POST /tenants/me/keys`, stored in `enterprise_approver.json`) for the
  four-eyes approver. The EVAL / GOAL failures from the aborted attempt reproduced identically on the enterprise tenant.
- **Free-plan supplement.** `WF-SCHEDULE-PLAN-FLOOR` skips on an enterprise tenant (floor 60 s), so it and
  `SCHED-PLAN-FLOOR` were rerun on the free `rw-primary` tenant. Both passed (`p0-baseline/supplement-free-plan/`).

| Variable | Value / status |
|---|---|
| `AGENTVERSE_TENANT_FILE` | enterprise tenant `49f1bbc9…` |
| `RW_SECOND_TENANT_FILE` | `rw-second` (different tenant) ✔ |
| `RW_ENTERPRISE_TENANT_FILE` | enterprise tenant ✔ |
| `RW_APPROVER_API_KEY` | second key of the primary (enterprise) tenant ✔ |
| `RW_GRANTS_ENFORCED=1` | ✔. `enforce_agent_grants` defaults to `True` (`app/core/config.py:268`) and is not overridden |
| `RW_SCALE=1` | ✔ (5,000 docs) |
| `RW_FIXTURE_REACHABLE` / `RW_FIXTURE_PUBLIC_URL` | **not set.** The stack cannot reach the fixture server: an in-container probe of `assert_source_url` refused `host.docker.internal` (192.168.5.2), `redis` (172.18.0.5) and `minio` (172.18.0.23) with "resolved to blocked IP (anti-rebinding check)". `INGESTION_ALLOW_INTERNAL_SOURCES` / `INGESTION_INTERNAL_SOURCE_ALLOWLIST` are unset in `agent-verse-backend/.env`. No tunnel was opened. |
| `RW_REDIS_URL`, `RW_S3_*` | **not set.** Compose `redis` and `minio` exist (host ports 6379 / 9000), but the egress guard refuses both, so setting these would fail on config, not product behaviour |
| `RW_MONGO_URI` | **not set.** The compose stack has no MongoDB service |
| LLM | as configured: NVIDIA (`DEFAULT_LLM_PROVIDER` + `NVIDIA_API_KEY` in `.env`). No keys were added |

Command: `scripts/run_real_world.sh docs/audits/fixwave/live/p0-baseline` (full suite + Playwright UI spec).
Backend took 4254.6 s (1 h 11 m) and Playwright 14.9 s. The suite did not stall, so no `RW_ONLY` split was needed.

## 3. Verdicts

**Scenario totals:** 27 PASS, 18 FAIL, 6 SKIP, counting `WF-SCHEDULE-PLAN-FLOOR` from the free-plan supplement.
**Test totals (main run):** 36 passed, 21 failed, 7 skipped, including the 2 UI tests.

| Scenario | Verdict | Note |
|---|---|---|
| EVAL-GOLDEN | FAIL | product (§4.1) |
| EVAL-GOLDEN-VERSIONING | FAIL | product: no update API (§4.2) |
| GOAL-HIGH-RISK-APPROVE | FAIL | product + test (§4.3) |
| GOAL-HIGH-RISK-DENY | PASS | 130.6 s |
| GOAL-MULTISTEP-RAG | FAIL | product (§4.4) |
| GOAL-STRATEGIES (supervisor, debate, mixture_of_agents) | FAIL 0/3 | product (§4.5) |
| GOAL-STRATEGIES-HIGH-RISK | FAIL 2/3 | supervisor and debate PASS (they actually run as react, see §4.5); mixture_of_agents FAIL (§4.6) |
| GOV-BUDGET-CAP | FAIL | **test harness** bug (§4.7) |
| GOV-GRANT-DENY | FAIL | test expectation vs product design, plus an observability gap (§4.8) |
| GOV-PII-GUARDRAIL | FAIL | product (§4.9) |
| GOV-POLICY-APPROVAL | FAIL | product (risk-classifier false positive) + test (§4.10) |
| KB-COMPLEX-CORPUS | FAIL 8/10 | pdf, docx, pptx, xlsx, csv, html, md, png PASS; scan_pdf and zip FAIL (§4.11) |
| KB-COMPLEX-EMBEDDINGS | PASS | 675 chunks, 100 % coverage |
| KB-COMPLEX-LIFECYCLE | FAIL | product (§4.12) |
| KB-COMPLEX-CSV-SCALE | PASS | |
| KB-RETRIEVAL-HARD | PASS | hit@5 0.962 |
| KB-TENANT-ISOLATION | PASS | |
| KB-STRATEGIES | FAIL | throttling, amplified by a breaker bug, plus catalogue bug (§4.13) |
| KB-REEMBED-MIGRATION | PASS | 178 queries during re-embed, 0 failed |
| KB-SCALE-SMOKE | PASS | 5,000 docs, 21.2 docs/s |
| KB-REAL-DOCS | FAIL | product: risk-classifier false positive (§4.14) |
| KB-REEMBED | FAIL | product, hypothesis only (§4.15) |
| KB-RSS | PASS | |
| KB-RSS-LOCAL | PASS | local feed correctly refused by the egress guard |
| SRC-REDIS, SRC-MONGO-SYNC | PASS | **egress-guard mode only**: with no `RW_REDIS_URL`/`RW_MONGO_URI` they only prove the guard refuses; no data is ingested |
| KB-SOURCES-SYNC-RSS | SKIP | see §5 |
| SRC-REDIS-INCREMENTAL | SKIP | see §5 |
| SRC-MONGO-INCREMENTAL | SKIP | see §5 |
| SRC-S3-INCREMENTAL | SKIP | see §5 |
| SCHED-CRUD, SCHED-PLAN-FLOOR, SCHED-NL | PASS | |
| SCHED-FIRES-GOAL | FAIL | **test harness**: the schedule fired correctly (§4.16) |
| SCHED-FIRES-WORKFLOW | PASS | 60.6 s |
| SCHEDULED-WF-HITL | PASS | |
| WF-SCHEDULE-PLAN-FLOOR | PASS (supplement) | skipped in the main run because the enterprise floor is 60 s; passed on the free tenant |
| TRIGGER-CHAIN | FAIL | product (§4.17) |
| TRIGGER-SIGNED-WEBHOOK | PASS | |
| WF-COMPLEX-PIPELINE | SKIP | see §5 |
| WF-FAILURE-RECOVERY | SKIP | see §5 |
| WF-CANCEL-AND-APPROVAL | PASS | |
| WF-HITL-APPROVE / REJECT / RESTART / CANCEL | PASS | |
| WF-INPUT-DEFAULTS | PASS | |
| WF-APPROVALS-TENANT-ISOLATION | PASS | |
| WF-PUBLISH-APPROVAL | FAIL | product: one audit row missing; four-eyes itself works (§4.18) |
| UI-APPROVALS-LIVE (Playwright) | PASS | 11.7 s |
| UI-KB-DOCS (Playwright) | PASS | 2.5 s |

### Metrics

| Metric | Value |
|---|---|
| KB-RETRIEVAL-HARD (26 questions) | hit@1 0.885, **hit@5 0.962**, MRR 0.917, answer acc 0.962, citation acc 1.0; search p50/p95 1069/1632 ms; RAG answer p50/p95 3975/12018 ms |
| KB-COMPLEX-CORPUS uploads | pdf 72 chunks in 3.07 s (8/8 facts top-5, heading alignment 1.0); docx 8 chunks (5/5, alignment 0.67); pptx 1 (3/3); xlsx 4 (2/2); csv 586 chunks in 22.6 s; html 2; md 1; png OCR 1 (1/1) |
| KB-REEMBED-MIGRATION | 178 queries during re-embed, 0 failed, p50/p95 447/1489 ms, top-1 stability 1.0, hit@5 1.0 → 1.0 |
| KB-SCALE-SMOKE | 5,000 docs in 235.9 s = **21.2 docs/s**, 0 errors, 0 rate-limited retries, listing = 5,000 |
| KB-STRATEGIES (10 questions each) | hybrid 1.0 / fusion 1.0 / naive 0.9 / hyde 0.9 hit@5 with answer and citation acc 1.0. self_rag 0.5, multi_hop 0.4, flare 0.3, web_augmented 0.3. adaptive, agentic, code, colbert, corrective, graph, memory_augmented, modular, speculative 0.0 (all requests errored). raptor and agentic_chunking 0.0 (collection not indexed). raft unavailable (`raft_model_selection_required`). Latencies: hybrid p50/p95 4633/15456 ms, fusion 6483/17126 ms, hyde 7320/20076 ms |
| EVAL-GOLDEN | own pass rate 0.0, platform pass rate 0.0, verdict agreement 1.0 (9/10 `execution_failed`) |
| Schedules | interval-60 s schedule fired 3 runs in 4 min (goal runs 22.8 s and 30.0 s) |

The full per-test metrics, strategy table and failure tracebacks are in `p0-baseline/real_world_report.md`.

## 4. Failures — evidence and root-cause hypotheses

Log lines are trimmed. The goal, run and approval ids refer to the run on 2026-10-05 (UTC).

### 4.1 EVAL-GOLDEN — 9/10 golden tasks `execution_failed`, empty outputs (high confidence)

```
worker ForkPoolWorker-16 08:32:45 agent_runtime_plan_created goal_id=ea5aa369… (x4)
                         08:34:44 goal_cancel_signalled goal_id=ea5aa369… (x4, 120 s later)
worker ForkPoolWorker-15 08:37:32 goal_not_claimable_skipping goal_id=ea5aa369… status=cancelled
run_ai_ops_dataset[b79daaa2…] succeeded in 362.3s: {'status': 'completed', 'result_id': '44f7e895…'}
```
- **Slot starvation.** One worker with `--concurrency=2` consumes both `goals.enterprise` and `maintenance`
  (`infra/docker-compose.yml:338`). `run_ai_ops_dataset` is routed to `maintenance` (`app/scaling/celery_app.py:149`) and
  holds one slot for 362 s. The other slot was held by an unrelated free-tier goal blocked on HITL for 321 s.
- **Inline waits with a fixed timeout.** The runner submits each case as a queued goal, then waits inline with `CASE_TIMEOUT_SECONDS = 120`
  (`app/evals/ai_ops_runner.py:38`, `:111-138`) and `CASE_CONCURRENCY = 4` (`:40`). It cancels on timeout, so the goals never got a slot.
  The eval-suite path avoids this deadlock on purpose (`app/scaling/tasks.py:9481-9486`); the AI-Ops path does not.
- **The one completed case still scored empty.** Goal 907ba8cf had `step_complete {"output":"1290"}`, but
  `_extract_output` (`ai_ops_runner.py:51-75`) reads only top-level keys. The local subscribe path wraps events
  as `{"type", "payload": …}` (`app/services/goal_service.py:1009-1014`, `tasks.py:2636-2644`). The loop also stops at
  `goal_complete`, before `synthesis_complete` arrives (`:115-124`).
- **Dataset version not recorded.** `ai_ops.py:231-255`, `app/evals/ai_ops_jobs.py:93-124` and
  `ai_ops_runner.py:372-395` never copy `dataset["version"]` into the result.

### 4.2 EVAL-GOLDEN-VERSIONING — `PATCH`/`PUT /ai-ops/datasets/{id}` → 404 (certain)

`app/api/ai_ops.py` only has `POST /datasets` (:77), `GET /datasets` (:108) and `POST /datasets/{id}/run` (:188).
`AIOpsStore` (`app/evals/ai_ops_store.py`) has no update method and no version bump, so the `version` column is always 1.

### 4.3 GOAL-HIGH-RISK-APPROVE — goal 8ea5660a ended `failed` (high)

```
#24 waiting_approval c50825e6 (Step 1) → #25 approval_granted 09:00:18
#72 grounding_blocked consecutive=2 ["2026-01-01","2026","01"]
#76 waiting_approval 584768f4 (Step 3 "destructive action 'remove'")  — never approved
#86 worker_failed "PermissionError: Step 'Step 3: …' approval timed out."
answer: "… before 2026 [UNGROUNDED CLAIM — not found in tool outputs]-01 [UNGROUNDED CLAIM …]-01 [UNGROUNDED CLAIM …]."
```
- **Why it failed:** one approval is raised per gated step, but the test approves only `pending[0]`
  (`tests/real_world/test_goal_high_risk.py:88`). The timeout is raised at `app/agent/nodes/executor_mixin.py:1851`.
- **Product defect: gate on a harmless step.** The gate landed on the harmless Step 1: "Create … from the given data" is read as
  mutating verb + DATA target (`app/agent/risk_classifier.py:455`), and `:553` gates every non-read-only step of a high-risk goal.
- **Product defect: prompt facts count as ungrounded.** Facts supplied in the prompt are flagged as ungrounded. Grounding evidence is only tool outputs plus KB
  context (`app/agent/nodes/_helpers.py:114-134`), never the goal text, and CORE-03 forces the check with
  tolerance 0 on high-risk goals (`executor_mixin.py:3489-3497`).
- **Product defect: markers split tokens.** Markers are spliced mid-token because the number regex `\b\d{2,}\b`
  (`app/agent/grounding.py:38`) overlaps date claims, and `annotate_ungrounded` does a first-occurrence `str.replace` per claim
  (`grounding.py:293-298`).
- **Supervisor fallback.** All 3 supervisor sub-goals first failed with "event stream ended before the sub-goal finished", which cost about 60 s before the single-agent fallback.

### 4.4 GOAL-MULTISTEP-RAG — goal 913fef37 failed "INSUFFICIENT DATA: total H1 diesel cost in INR…" (high)

```
rrf_retrieval_complete bm25_hits=9 fts_hits=0 trgm_hits=0 vector_hits=9 top_k=3   (every retrieval)
supervisor_decompose_failed error="Expecting ',' delimiter…"
llm_empty_completion_retrying … stop_reason=length max_tokens=512
agentgraph_run_exception (RetrievalStrategyExecutionError "provider query expansion failed") → goal_failed 08:41:13
```
- **The fact was never retrieved.** It is in `fleet-and-fuel-fy27.xlsx` chunk 3 (`Total H1 … diesel_cost_inr=24979830`).
  - The FTS leg uses `plainto_tsquery`, which ANDs every term of the long step text, and gets 0 hits (`app/rag/engine.py:~546-555`).
  - The shipment ledger (586 of 675 chunks) then crowds the top-3 on the vector and BM25 legs.
- **Terminal cause.** Fusion query expansion asks for `max_tokens=200` (`app/rag/agentic/query_expander.py:77`). The reasoning model returns
  an empty completion, which raises `ExternalServiceError` (`app/providers/openai_compatible.py:219-243`). Strict fusion turns that into
  `RetrievalStrategyExecutionError` (`app/rag/engine.py:1346-1352`), which reaches `graph.py:972` and fails the goal.
- **Stale cache replay.** A `cache_hit` replayed the iteration-1 abstention, because `_is_uncacheable_output`
  (`executor_mixin.py:272-310`) does not exclude "INSUFFICIENT DATA".
- **No tool call.** The agent has `connector_ids=[]`, so no web_search tool call happened.

### 4.5 GOAL-STRATEGIES supervisor / debate — failed in 7–10 s, "No structured result was produced." (high)

```
08:41:26-30 chat/completions 429 x6 (SDK retried after 0.44 s, 0.97 s) ; rag_strategy_failed failure_type=RateLimitError strategy=flare
08:41:30 agentgraph_run_exception type=RateLimitError msg="Error code: 429 …"
  graph.py:927 → planner_mixin.py:797 _node_plan → circuit_breaker.py:209 → openai_compatible.py:315
run_goal succeeded: status=failed workflow_mode=supervisor iterations=0
```
- **Root cause 1: unhandled 429.** `openai.RateLimitError` is not caught by the planner
  (`app/agent/nodes/planner_mixin.py:797-800` handles only `RuntimeError`/`TimeoutError`).
  - `complete_with_failover` has no second model and re-raises (`app/providers/circuit_breaker.py:193-209`).
  - The SDK retries only twice, with backoff under 1 s (`app/core/config.py:37`).
  - `graph.py:965-985` marks the goal FAILED, and `app/services/result_artifacts.py:116-125` writes the placeholder answer.
- **Root cause 2 (deterministic): supervisor/debate silently ignored on runtime v2.**
  - With runtime v2 on for every tenant (`*`), `workflow_mode=supervisor|debate` is dropped without notice.
  - `goal_service.py:4595-4602` sets `enable_supervisor`/`enable_debate`, but the runtime profile is `react`.
  - `app/orchestration/profiled_graph.py:117-121` → `app/orchestration/graph_factory.py:54-62` only switch nodes on for profile strategies, so the nodes at `graph.py:501-528` are never compiled.
  - The recorded execution is `{"driver":"agent_graph","patterns":["react","self_refine"]}` and no downgrade is recorded.
  - This is also why GOAL-STRATEGIES-HIGH-RISK[supervisor|debate] "pass": they run as plain react.

### 4.6 GOAL-STRATEGIES[mixture_of_agents] (f4643652) and GOAL-STRATEGIES-HIGH-RISK[mixture_of_agents] (6567eaa6) — stuck `executing` for 480 s (high)

```
f4643652: coordination_session strategy_id=mixture_of_agents → waiting_approval 7c63f128 (08:41:47) … goal_cancelled 08:49:47 (test cleanup)
          expire_hitl_approvals: {'expired': 1, 'notified': 1}; enforce_hitl_sla: waiters_released 0
6567eaa6: approval rejected 08:50:46.526 → goal_failed "Strategy execution failed. (approval_rejected)" 08:50:46.534
          "Goal 6567… failed: 'dict' object has no attribute 'status'" → goal_transient_failure_will_retry attempt=1/3
          rerun 08:50:48 → new waiting_approval a168d752 (never resolved) → expired 08:56:42 → still executing
```
- **Low-risk goal classified high-risk.** `strategy_override` → `DistributedStrategyExecutor` (`app/orchestration/strategy_executor.py:121`) →
  `CoordinationGoalBridge.run` (`app/coordination/pattern_runs/goal_bridge.py:88`) gates any goal text that `is_high_risk_text`
  flags. "before **releasing** a consignment" lemmatises to the RELEASE verb class (`app/agent/risk_classifier.py:69`), and the
  release exclusions at `:171` only cover notes/date/version. The goal's status stays `executing`, not `waiting_approval`.
- **Expired approvals never wake the waiter.** `_expire_db_approvals` (`app/scaling/tasks.py:6898-6915`) only UPDATEs the row and
  never pushes `hitl_result:{id}`. The BLPOP in `app/governance/hitl.py:~650-670` / `:1309` therefore waits out its full 3600 s
  (`goal_bridge.py:38`, `:224`).
- **Every finished v2 distributed-strategy goal crashes the worker.** `DistributedStrategyLoop.run` returns a dict
  (`app/orchestration/distributed_strategy_loop.py:131-135`), but the worker reads `state.status.value` (`app/scaling/tasks.py:4016`).
  The resulting AttributeError is retried as transient (`tasks.py:4122`, `4174-4181`), and the rerun opens a fresh approval. This hits
  every v2 distributed-strategy goal at completion, whether it succeeded or failed.

### 4.7 GOV-BUDGET-CAP — test harness bug (certain)

`tests/real_world/test_gov_guardrails.py:166` calls `api.put(...)`, but `LiveAPI` (`tests/real_world/helpers.py:85-125`) has no
`put` method, so the test raises `AttributeError` after 0.0 s. Budget-cap behaviour was not exercised; fix the harness in P8.

### 4.8 GOV-GRANT-DENY — no `tool_call_blocked_by_grant` (high)

Events: `plan_ready "Use http_request tool…"` → `tool_call_complete tool=web_search` ×3 → `verification_done success=false` →
`goal_failed "Error code: 429"`. Fixture calls: 0.

- **The ungranted tool is never offered.** The test grants only `web_search` and asks for `http_request`. `http_request` is filtered out of the tool list
  before the model sees it (`app/agent/nodes/planner_mixin.py:50-83`, `:982`; `executor_mixin.py:1980-2044`), so the
  dispatch-time gate that emits the event (`executor_mixin.py:2513-2585`) can never fire.
- **Enforcement works, but leaves no trace.** Grant enforcement itself works, but withheld tools leave **no event or audit row** (an observability gap). The test needs to
  assert "not offered + recorded" instead.
- **Why the goal failed:** another unhandled 429.

### 4.9 GOV-PII-GUARDRAIL — answer returned email/phone unredacted (high)

```
#11 guardrail_profile_selected bundle=default
#23 step_complete output="Ravi Menon, email ravi.menon@…, mobile +91 …"
#24 goal_failed "Error code: 429"   (verifier_mixin.py:135 _node_verify → complete_with_failover → openai_compatible.py:315)
API: POST /guardrails 201, POST /guardrails/test 200 (action=redacted), DELETE 204; guardrail_configs rows: 0
```
- **The tenant rule never reaches the agent.**
  - `POST /guardrails` is the legacy router (`app/api/guardrails.py:163-219`). Its DB insert fails silently (`except: pass`), so the rule exists only in the API process's `_configs_store`.
  - Nothing in the worker reads that store or the `guardrail_configs` table.
  - The tester's "redacted" comes from the built-in engine (`guardrails.py:281`, `main.py:3129`), not from the rule.
- **The agent path does not redact either.**
  - `redact_pii` handles only SSN and card numbers (`app/intelligence/guardrails.py:51-64`).
  - The v2 output scan (`executor_mixin.py:3363-3384`) needs `app_state.guardrail_engine`, which the worker's `SimpleNamespace` (`tasks.py:3682-3690`) does not include.
  - The FINAL_OUTPUT check (`verifier_mixin.py:771-803`) runs only on the success path, and `:816` overwrites the redacted answer.
  - `result_artifacts.py:116-125` serves the raw step output.
- **Why the goal failed:** a RateLimitError, not caught in the verifier.

### 4.10 GOV-POLICY-APPROVAL — approval is not for `web_search` (high)

The approval `b060e914` reads "Step 1: Perform a web search for the current repo rate set by the Reserve Bank of India." It has risk
high, and its `risk_reasons` are "change to a sensitive target (infrastructure, money)".
- **It came from the generic step-risk gate**, not the tool policy (`executor_mixin.py:1817-1841`). That gate is a false positive:
  - "perform" and "set" count as mutating (`risk_classifier.py:84`, `:452-456`, `:547-556`).
  - "repo" counts as infrastructure and "Bank" as money.
- **The step-level policy check cannot match.** It guesses the tool from the step text and gets `"llm_call"` (`app/agent/nodes/_helpers.py:312-321`, used at
  `executor_mixin.py:1534`, `1762-1808`).
- **The real policy gate never runs.** It would file "web_search: Step 1 …" (`_tool_policy_gate`, `executor_mixin.py:706-800`, line 795),
  but only after the first approval is approved, and the test rejects it.

### 4.11 KB-COMPLEX-CORPUS[scan_pdf] 422 and [zip] 415 (high)

- **scan_pdf.** The upload returned `"the PDF has no extractable text (scanned images need OCR)"`, raised at
  `app/ingestion/document_text.py:44-47` and mapped to 422 at `app/api/knowledge.py:1245-1247`.
  - `POST /knowledge/ingest/file` only OCRs images (`knowledge.py:1057`, `1084`).
  - PDF OCR already exists, `OcrEngine.extract(pdf_bytes=…)` (`app/ocr/engine.py:274-278`), and the connector path uses it (`app/ingestion/parser_registry.py:345-354`).
  - tesseract 5.3.0 and pdftoppm are installed in the image.
  - The test's skip condition ("503 when no OCR engine") therefore never applies.
- **zip.** The upload returned `"unsupported binary file"`, from the NUL-byte check at `app/ingestion/document_text.py:305-308`
  (via `knowledge.py:1251`). Archive extraction does not exist anywhere in ingestion.

### 4.12 KB-COMPLEX-LIFECYCLE — editing a document adds a second copy (9 → 17 chunks) (high)

- **Every upload is a new document.** Each upload gets `document_id = uuid4().hex` (`knowledge.py:1107`) and is persisted without `replace_document=True`
  (`:1135-1140`).
- **Dedup is exact-bytes only.** It is a whole-file SHA-256 check (`:1070-1071`, `_already_indexed_or_http` `:594`), so an edited file is stored next to the old one.
  URL ingest does this correctly (`stable_url_document_id` `:137` + replace `:1977`).
- **Unchanged chunks are not preserved.** Chunk ids are random (`app/rag/models.py:40`), which also defeats "unchanged chunks preserved".
- **Delete works:** both DELETEs returned 200.

### 4.13 KB-STRATEGIES — 503 "Answer synthesis is unavailable" / "Retrieval service is unavailable" (high)

```
09:26:07-16 graph synthesis: 429 x3 per call ("Retrying request … in 0.43s") → circuit opens
09:26:18-09:27:12 failure_type=ProviderCircuitOpenError (speculative, agentic), RetrievalStrategyExecutionError (corrective), ModularModuleExecutionError (modular)
09:31:11-28 flare failure_type=RateLimitError x6 → circuit re-opens; colbert/memory_augmented/code: rag_strategy_complete, then 503 with 0 LLM calls
multi_hop/hyde/self_rag: llm_empty_completion_retrying stop_reason=length max_tokens=512
web_augmented: failure_type=deadline_exceeded (~10.3 s)
```
- **429s open a process-wide breaker.** The breaker is a single process-wide instance (`app/providers/circuit_breaker.py:92`; threshold 5, recovery 60 s at `:22-25`) and **counts 429s as
  failures** (`:131-137`). One throttled strategy therefore blocks every LLM caller in the API for 60 s. This is the main amplifier.
- **The error mapper hides the cause.** `app/api/rag_platform.py:117-130` maps errors to 503, drops the reason and logs nothing
  (`knowledge.py:340-373` does log it).
- **Strategy budgets are too small.** Strategy `max_tokens=512` is too small for the reasoning model, and flare raises raw `RateLimitError`.
- **The catalogue overstates readiness.** It reports raptor and agentic_chunking as available because `GET /rag/strategies` calls `readiness_all` with no
  collection (`rag_platform.py:185-195`; `app/rag/catalogue.py:247-252`). Their per-collection 503 ("requires RAPTOR indexing") is correct.

### 4.14 KB-REAL-DOCS — goal 28554aa1 failed "No structured result was produced." (high)

`worker_failed: PermissionError: Step 'Step 1: The Project Halcyon database migration window is …' requires human approval
(… change to a sensitive target (data); … financial action 'pay' …) but the goal runs in 'bounded-autonomous' mode` and
`goal_denied_by_governance`.
- **The risk classifier misreads a read-only question:**
  - the noun "migration" is read as the verb *migrate* (mutating);
  - "database" is read as a DATA target;
  - the product name "Quokka **Pay**" is read as the financial verb *pay*.

  Code: `app/agent/risk_classifier.py:452-456`, `:546-556`; raised at `executor_mixin.py:1458`; placeholder written at `result_artifacts.py:125`.
- This is the same classifier family as §4.3, §4.6 and §4.10.

### 4.15 KB-REEMBED — PEP 20 URL document not found after re-embed (low–medium; collection was cleaned up)

- **Extraction and storage look fine.** Ingest of `https://peps.python.org/pep-0020/` → 1 chunk of 2,049 chars. The Zen text starts at char 809; the rest is
  nav and inline JS (regex extraction at `knowledge.py:1851-1854`). Search does not truncate content.
- **Likely cause is ranking:** the diluted embedding, plus the trigram threshold 0.3 (`app/rag/engine.py:579-586`), plus the test's top_k=3
  (`tests/real_world/test_knowledge.py:39`).
- **To fix and confirm:** strip script/style/nav in `_fetch_url_content`, and rerun the search with top_k=10 to see the per-leg ranks.

### 4.16 SCHED-FIRES-GOAL — test harness / contract mismatch (certain)

The product works: the interval-60 s schedule fired 3 runs (2 `success` in 22.8 s / 30.0 s, 1 executing). The test's done-predicate
(`tests/real_world/test_sched_realistic.py:189-193`) looks for `events`/`items`, but `GET /schedules/{id}/history` returns
`{"runs": [...]}`, so the wait timed out at 240 s.

### 4.17 TRIGGER-CHAIN — consumer resumed, then code step failed "execution could not be audited" (medium-high)

```
workflow-worker 09:41:42-43 audit_write_retry attempt=2,3 … StringDataRightTruncationError: value too long for type character varying(32)
  INSERT INTO audit_log (… tenant_id=49f1bbc9-4062-4e9d-8215-a6f8b50ce2c0 tool_name=code_interpreter.python)
audit_durable_record_failed → workflow_run_failed_worker RuntimeError("code step 'book_dispatch': execution could not be audited")
```
- **The chain itself works.** The signed webhook, replay de-dup and 401s all worked, and the producer's completion event did resume the consumer.
- **The resumed run carries the wrong tenant id format.** `audit_log.tenant_id` is `varchar(32)` (hex), but the resumed run's state carries the **dashed** 36-char UUID.
  `workflow_runs.tenant_id` is stored dashed, and `app/workflow/run_store.py:76` returns `str(uuid.UUID(…))`.
- **The code step passes it on unchanged.** `app/workflow/steps/code_step.py:40`/`:96` passes the value unnormalised into the governed-execution audit row
  (`app/tools/code_execution.py:84` → `app/governance/audit.py:316`), which raises AuditPersistenceError (`code_step.py:107`).
- `app/workflow/engine_audit.py:70` already normalises with `uuid.UUID(…).hex`.

### 4.18 WF-PUBLISH-APPROVAL — missing `workflow.created` audit row (certain)

- **Four-eyes publishing works:**
  - direct publish → 409;
  - self-approval → 409 "submitter";
  - a second key → 200 `published`, with a version recorded.
- **The missing row.** The workflow was created through `POST /v1/workflows/import-yaml`, and that route (`app/workflow/router_versions.py:293-324` → `svc.create`) never calls
  `record_workflow_action`. Only `POST /v1/workflows` does (`app/workflow/router.py:225`); clone (`router_versions.py:~333`) has
  the same gap.

### Cross-cutting fix themes (for the phases)

1. **Provider resilience (P5, P2).** Handle `openai.RateLimitError` in the planner, executor and verifier. Use Retry-After-aware backoff, keep 429s
   out of the circuit-breaker failure count, and raise `max_tokens` for reasoning models (query expansion 200, strategies 512).
2. **Risk-classifier false positives (P4, P5, P8).** "releasing", "migration", "Pay" (product name), "set by" and "repo" are misread, and every non-read-only
   step of a high-risk goal is gated.
3. **HITL waiter wake-up on expiry (P4).** Plus dict-vs-AgentState for v2 distributed strategies, and no retry of terminal outcomes.
4. **Runtime v2 drops `workflow_mode=supervisor|debate` silently (P5).**
5. **AI-Ops eval runner (P7):** non-blocking execution, event-payload unwrap, dataset versioning API.
6. **Guardrails (P8):** the tenant rule must persist and reach the worker; PII redaction must cover email/phone; output scan and result artifact.
7. **Ingestion (P1):** PDF OCR fallback, ZIP, replace-on-edit, HTML boilerplate stripping.
8. **Tenant id format (P3, P4):** canonical (dashed) vs hex in workflow → audit.
9. **Test harness (P7, P8, P3):** `LiveAPI.put`, schedule history `runs`, grant-test expectation, approve all pending approvals in GOAL-HIGH-RISK-APPROVE.

## 5. Skips that could not be enabled

| Scenario | Exact skip reason | What enables it |
|---|---|---|
| KB-SOURCES-SYNC-RSS | "needs RW_FIXTURE_PUBLIC_URL (a tunnel to the local fixture server; the stack's connector egress guard refuses host.docker.internal) or RW_FIXTURE_REACHABLE=1 when the operator allowlisted the fixture host" | Operator env in `agent-verse-backend/.env`: `INGESTION_ALLOW_INTERNAL_SOURCES=true` + `INGESTION_INTERNAL_SOURCE_ALLOWLIST=host.docker.internal`, restart backend + workers, then `RW_FIXTURE_REACHABLE=1`. Alternatively a public tunnel → `RW_FIXTURE_PUBLIC_URL` |
| SRC-REDIS-INCREMENTAL | "needs RW_REDIS_URL: a Redis the stack can reach (egress-allowlisted)" | Allowlist `redis` as above. Then `RW_REDIS_URL=redis://redis:6379/<spare db>` and `RW_REDIS_SEED_URL=redis://localhost:6379/<same db>` |
| SRC-S3-INCREMENTAL | "needs RW_S3_ENDPOINT, RW_S3_BUCKET, RW_S3_ACCESS_KEY, RW_S3_SECRET_KEY: an S3/MinIO bucket the stack can reach (egress-allowlisted)" | Allowlist `minio`. Then `RW_S3_ENDPOINT=http://minio:9000`, `RW_S3_SEED_ENDPOINT=http://localhost:9000`, a bucket, and the compose MinIO credentials |
| SRC-MONGO-INCREMENTAL | "needs RW_MONGO_URI: a MongoDB the stack can reach (egress-allowlisted)" | No MongoDB in compose. Add a `mongo` service + allowlist `mongo`, then `RW_MONGO_URI=mongodb://mongo:27017` and `RW_MONGO_SEED_URI=mongodb://localhost:27017` |
| WF-COMPLEX-PIPELINE, WF-FAILURE-RECOVERY | "the workflow HTTP step's SSRF guard refused the local fixture server (… 'host.docker.internal' resolved to blocked IP '192.168.5.2' (anti-rebinding check)); set RW_FIXTURE_PUBLIC_URL …" | Only a public tunnel works (`RW_FIXTURE_PUBLIC_URL`). The workflow HTTP step (`app/workflow/steps/http_step.py:17`, `:48`) has no operator allowlist, unlike connectors |
| (WF-SCHEDULE-PLAN-FLOOR) | "plan enterprise allows every-minute schedules (floor 60)" | Enabled by the free-plan supplement run: PASS |

## 6. Areas from the plan with NO real-world scenario yet

| Phase | Missing entirely (no `tests/real_world` scenario touches it) |
|---|---|
| **P1** KB / ingestion | **Connector sources:** none of the ~45 connector types under `app/ingestion/connectors` runs live. Missing: web crawl, GitHub, Confluence, Jira, Notion, Google Drive, SharePoint, Postgres/MySQL sources. Redis/Mongo/S3/RSS-sync exist but skip. **Sync and ingest pipeline:** scheduled source syncs, deletion reconciliation (`ingestion_live_listings`), PII action at ingest (`pii_action`), quality-score filtering. **Formats:** multilingual documents, >120-page PDFs, tables/figures, audio/video transcripts, email ingestion, archives (ZIP fails today). **Chunking:** no comparison of chunking strategies (semantic / agentic). |
| **P2** Retrieval / RAG / hallucination | **No hallucination or grounding scenario at all.** Missing: unanswerable questions → abstain, conflicting sources, NLI/claim-level faithfulness (`grounding_verification`, `claim_decomposer`, `nli_checker`), citation faithfulness under adversarial docs. **Strategies needing indexing:** RAPTOR and agentic-chunking end to end (index, then query), graph RAG over a built graph, RAFT. **Retrieval quality:** semantic-cache correctness and staleness, reranker A/B, cross-collection retrieval, document-level ACLs, recency weighting. |
| **P3** Scheduling / triggers / channels | **Schedule types:** only cron, interval and NL are covered. Missing: `ONCE`, `RELATIVE_DELAY`, `DEADLINE`, `BUSINESS_CALENDAR`, timezone/DST, missed-fire catch-up after downtime. **Trigger types:** only the workflow signed webhook (+ event chain) and the GitHub webhook are covered, out of 57 `TriggerType` values. Missing: GOAL_COMPLETED/FAILED/SCORE_BELOW, HITL_APPROVED/REJECTED, MEMORY_CREATED, CONDITION, COUNTER_THRESHOLD, COMPOUND, STATE_TRANSITION, WINDOW_AGGREGATE, Jira, Stripe, Slack, Teams, Discord, Salesforce, Confluence, Linear webhooks, FILE_DROP, DB_ROW_CHANGE, RSS_FEED, GOOGLE_SHEETS, SHAREPOINT, Alertmanager, Datadog, PagerDuty, Grafana, CloudWatch, Sentry, LOG_PATTERN, API_POLL, GRAPHQL_SUBSCRIPTION, WEBSOCKET_MESSAGE, PRICE_THRESHOLD, MQTT, GEOFENCE, SENSOR_THRESHOLD, EMAIL_ARRIVAL/INTENT, SMS, VOICE, MEETING_ENDED, FORM_SUBMISSION, CHAT_*. **Channels:** none. Missing: Telegram, Slack (`app/integrations/slack`), Zapier, IMAP email-to-goal, SMS, voice, Teams, Discord. **Trigger reliability:** trigger DLQ/replay, cross-replica dedup, quotas and rate limits. |
| **P4** Workflows / approvals / HITL | **Step types:** foreach, ocr, rag, rpa, sub_workflow, tool, transform and the org steps (department_handoff, cross_team_review, org_decision) are never run. http only runs in skipped scenarios. **Lifecycle:** workflow version rollback, compensation/rollback engine (`tool_inverses`), NL workflow builder (`workflow_planner`), rejection of invalid definitions at save/publish (see the `llm_prompt` observation). **Approvals:** multi-approver quorum (`required_approvers>1`), approval expiry/escalation/SLA (a bug was found in §4.6). |
| **P5** Goals / agent core / patterns / routing / lifecycle | **Multi-model routing: no scenario.** Missing: `model_router` per-task model choice, provider failover, cost-aware routing (only one model is configured). **Patterns:** none beyond supervisor/debate/MoA (camel dialogue, auctions/bids, goal-tree decomposition, civilizations, real tool-error replan, max-iterations). **Agent lifecycle:** create/version/clone/archive, templates/marketplace, meta-agent NL→config. **Resilience:** mid-step cancel, bulkhead/concurrency limits, cost-tracking accuracy, provider-throttling resilience. |
| **P6** Memories / self-improvement | **Nothing at all.** **Memories:** execution memory, long-term memory extraction and cross-session recall, episodic/procedural/prospective/department memory, consolidation/maintenance, memory guardrails (`memory_write_blocked`). **Self-improvement:** self-optimizer, prompt optimizer, learning experiments, `goal_learning` feedback improving a second run. |
| **P7** Evals / golden / rollout gate | Only AI-Ops golden runs exist, and they fail. Missing: EvalSuiteRunner multi-dimension suites, LLM-judge vs lexical scoring, **rollout gate blocking a regressed agent version**, `/eval/golden-datasets` promote-goal flow, verifier calibration, drift metrics. |
| **P8** Guardrails / governance / grants | Missing: prompt injection and indirect injection, encoding attacks, red-team suite, compliance (GDPR export/erase, SOC2/PCI reports), audit-trail completeness and immutability, RBAC scopes (viewer key cannot write), IP allowlist, SSO/MFA, vault credential handling. Budget cap is not exercised yet because of the harness bug. |
| **P9** Scale | Only 5,000 docs (21 docs/s). **No million-document ingestion or retrieval.** Missing: HNSW at 1M+ chunks, p95 under concurrent load, multi-worker / multi-replica goal throughput, per-plan queue isolation / noisy neighbour (today one 2-slot worker serves `goals.enterprise` + `maintenance`, see §4.1), pgbouncer/Redis saturation, provider backpressure. |
| **P10** Frontend | Only 2 Playwright flows (live approval, KB doc list). Missing: goal submit + live SSE stream, agents CRUD, workflow builder, schedules/triggers UI, governance (policies, guardrails, budgets, grants), evals UI, marketplace, observability dashboards, org/collab WebSocket, login/SSO, mobile layout. |

Note: the plan lists "Finalize main (USR-1..7)" under P0. That was out of scope for this run (no app code changes).
