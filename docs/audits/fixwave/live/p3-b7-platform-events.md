# P3 / B7: platform-event triggers on the live stack (2026-10-06)

Branch `live/p3-b7-platform-events`, from `main` @ `1d81f2cdc` (B7 code fixes B7-1..B7-5 already merged,
`fcf68e1c9`); `main` merged once (`220e97372`, deferred-code DEF-1..5 incl. DEF-5 vendor-signed webhook dedup).
One alembic head, **`b8d0f2a4c6e7`** (no new migration). Nothing pushed.

Raw live output is in `p3-b7-platform-events/` next to this file (`results.jsonl` + `summary.txt` per run, and the
hand probes). Scanned for the tenant keys, every PASS/SECRET/KEY/TOKEN value of `/private/tmp/claude-501/rw/**/*.env`
and the `av_` / `nvapi-` / `sk-` / `AKIA` patterns: 0 hits.

| Run | What | Images |
|---|---|---|
| `probe1-chain.out` | hand probe: goal A → trigger → goal B → self trigger → goal C, guard | main (redeployed from this worktree) |
| `probe3-goal-failed.out` | hand probe: a goal the worker fails fires goal_failed once | main |
| `probe-dimension.out` | `POST /triggers` goal_score_below with `score_dimension: "acuracy"` → **201** | main |
| `baseline/` | PLATFORM-* on main images: **6 pass, 2 fail** (SELF-TRIGGER-DEPTH, SCORE-BELOW), 1 skip | main |
| `final/` | PLATFORM-* on the fixed images: 7 pass, 1 fail (MEMORY: the source goal itself failed the grounding check, scenario text fixed), 1 skip | B7-L1 … L4 + main |
| `final-mem/` | PLATFORM-MEMORY with a plain acknowledgement goal: **1/1** | B7-L1 … L4 + main |
| `final-x/` | PLATFORM-MULTI-REPLICA + INGRESS-EVENT-MULTI-REPLICA with a 2nd API + 2nd worker replica: **2/2** | B7-L1 … L4 + main |
| `regress/` | INGRESS-* (all), TRIGGER-CHAIN, TRIGGER-SIGNED-WEBHOOK, TIME-*: **20 passed**, 3 skipped (§4) | B7-L1 … L4 + main |

## 1. Verdicts

| Type | Verdict | Live evidence (`final*/`) |
|---|---|---|
| **goal_completed** | **COMPLETE (fixed: B7-L3)** — chain, source filter, self-loop guard, isolation already worked (B7-1); the depth cap stopped the chain without any audit row | PLATFORM-GOAL-CHAIN, PLATFORM-SELF-TRIGGER-DEPTH, PLATFORM-QUOTA-RATE, PLATFORM-MULTI-REPLICA |
| **goal_failed** | **COMPLETE (already, B7-2)** — worker, stuck-goal detector, stale-runner watchdog and expired-approval sweep each fire once | PLATFORM-GOAL-FAILED (baseline and final) |
| **goal_score_below** | **COMPLETE (fixed: B7-L2, B7-L4)** — threshold 0..1 required (B7-5) | PLATFORM-SCORE-BELOW |
| **hitl_approved** | **COMPLETE (fixed: B7-L1 = B7-NEW-1)** — goal + workflow gates fire once, relayed duplicate deduped (already) | PLATFORM-HITL-GOAL, PLATFORM-HITL-WORKFLOW |
| **hitl_rejected** | **COMPLETE (already, B7-1/B7-3)** | PLATFORM-HITL-GOAL, PLATFORM-HITL-WORKFLOW |
| **memory_created** | **COMPLETE (already, B7-1/B7-4)** — fires once, own goal's learning audited `self_trigger`, relayed duplicate deduped | PLATFORM-MEMORY |
| cross-cutting | tenant isolation, plan quota, hourly cap + DLQ, audit row per fire / suppression, exactly-once with 2 API + 2 worker replicas: **COMPLETE** | see §1.1 |

### 1.1 Requirement by requirement

| Requirement | Before (baseline, main images) | After (live) |
|---|---|---|
| goal_completed chain A→B fires exactly once | yes (probe1, baseline) | T1 1 goal, payload = A, depth 1 |
| B's completion does not re-fire (no self-loop) | yes: T2 (watches dst, runs dst) fired once on B → C; C's completion audited `self_trigger` | unchanged |
| Source-agent filter (`watch_agent_id`) | T1 never fired for B / C | unchanged |
| Chain depth cap 10 + audit row on suppression | **10 goals (depth 1..10), then silence: no `trigger_events` row** (consumers returned early with a log line) | 10 goals + exactly one `chain_depth_exceeded` row whose payload is the depth-10 goal (B7-L3) |
| `allow_self_trigger` opt-in | works (the 10-link chain above) | unchanged |
| goal_failed via the worker | a goal the agent loop fails (unfetchable data, `max_iterations: 1`) → 1 firing | unchanged |
| goal_failed via stuck-goal detector / watchdog / expired approval | rows placed in the three states (executing + `updated_at` 3 days old; executing + heartbeat 1 h old + 1 requeue used; `waiting_human` + an approval past `expires_at`) → the beats failed all three and each fired once | unchanged |
| score_below: 0..1 threshold required | none / 0 / 1.5 → 422 | + an unknown dimension → 422 (was 201, never fired; B7-L4) |
| score_below fires below, not above; dimension respected | **every completed goal scored `task_completion: 0.0`** (scored before the COMPLETE transition): a task_completion trigger fired on every successful goal, the overall average was ~0.81 for a perfect answer | `task_completion: 1.0`; 5 triggers (overall 0.97 / 0.05, tool_relevance 0.9, accuracy 0.5, task_completion 0.5): fired exactly where the goal's real score was below the threshold (B7-L2) |
| hitl_approved / rejected, goal level | approve ×3 (one per gate of a supervised high-risk goal) → 3 firings, each its request id; reject → 1 | unchanged + the event is published before the decision call returns (B7-L1) |
| hitl, workflow level | approve → 1, repeated decide (200, idempotent) → nothing, reject on a 2nd run → 1 | unchanged |
| relayed duplicate decision | the stream entry re-XADDed → `dedup` audit row, no goal | unchanged |
| memory_created fires once, no loop | 1 firing; the trigger goal's own learning → `self_trigger`; a re-XADDed memory event → `dedup` | unchanged |
| Tenant isolation | the free tenant's unfiltered goal_completed trigger: 0 events from this tenant's goals, fires on its own tenant's goal | unchanged |
| Plan quota | free tenant: 5 × 201 then 403 (goal_completed / memory_created / hitl_rejected) | unchanged |
| Hourly cap | `max_firings_per_hour: 1`, two completions → 1 goal + `rate_limit` row + `RATE_LIMITED` DLQ row | unchanged |
| Audit row per fire | every fire is a `trigger_events` row (goal id, payload, depth); every suppression too | + the depth-cap suppression (B7-L3) |
| Exactly-once across replicas | — | `agentverse-rw-backend2` + `agentverse-rw-worker2` (removed afterwards): 6 completions → 6 firings, each once; both API replicas fired some (backend2: 3, backend1: the rest), worker2 ran 8 goals; chain group had 3 consumers |

## 2. Fixes (TDD: a failing unit test first, then the live scenario)

| Commit | Fix |
|---|---|
| `86796d80e`, `b90dd9288` B7-L1 (B7-NEW-1) | `HITLGateway.approve_async` (every request path) awaits the `hitl.approved` trigger event and the cross-replica resolution instead of `loop.create_task` fire-and-forget (lost when the process stopped right after answering); a publish error never undoes the committed decision; the sync `approve()` still schedules them — `tests/governance/test_b7_approve_publish_awaited.py` (3) |
| `f44d3fd49` B7-L2 | the verifier marks a successful goal COMPLETE **before** scoring it: `task_completion` was 0.0 on every completed worker-run goal (live evaluations rows), so goal_score_below on task_completion fired on every success and the overall average was capped at ~0.86 — `tests/agent/test_b7_completed_goal_scorecard.py` |
| `79dde146f` B7-L3 | the chain / HITL / memory consumers no longer drop a firing at MAX_CHAIN_DEPTH with a log line; the dispatcher's loop guard refuses and audits it (`chain_depth_exceeded`) per matching trigger — `tests/triggers/test_b7_depth_cap_audited.py` (7); two older tests that asserted the silent drop now assert the hand-off |
| `7e2f82725` B7-L4 | `validate_spec` refuses a goal_score_below `score_dimension` that is not blank / `overall` / one of `EvalRunner.DIMENSIONS` (422; was stored and never fired) — `tests/triggers/test_b7_score_dimension_validation.py` (14) |
| `79929e3d0`, `54c6aaa53`, `6372cb6f3`, `392ce2775` | `tests/real_world/test_platform_event_triggers.py`: 9 PLATFORM-* scenarios + evidence |

Tests: triggers + services + governance + agent + workflow + scaling + evals + memory + intelligence **9,035 passed**
(after the merge of main); `test_b7_chain_loop_integration.py` (testcontainers) 1/1; `ruff check .` clean;
`mypy app` (strict) clean on 1,944 files.

## 3. Deployment, infra, env
- App services (backend, worker, subgoal-worker, workflow-worker, schedule-worker, beat) and
  `agentverse-rw-ingestion-worker` were recreated from **this worktree** (scripts `/private/tmp/claude-501/rw/p3b7/`):
  once on main (baseline), once after B7-L1..L4 + the merge of main (DEF-1..5). `db-migrate` ran (head unchanged).
- The launchd `run_forever.py` did not start a beat during either redeploy (its last beat line is 09:58, "docker
  compose runs it now"). Its log is 2.5 GB (`~/.local/state/agentverse_run_forever/run_forever.log`, not rotated).
- Temporary `agentverse-rw-backend2` + `agentverse-rw-worker2` (label `p3b7=live-test`) for the replica runs; removed.
- PLATFORM-GOAL-FAILED writes three goal rows + one approval row with `docker exec` psql (superuser) to put them in the
  stuck / stale-runner / expired-approval states the beats look for; it deletes them afterwards.
- No env or volume change; `.env` is a symlink to main's. Scenario triggers / agents / goals are deleted / cancelled.

## 4. Regression
`regress/` on the final images (incl. DEF-5 from main): INGRESS-WEBHOOK-SIGNED, -FILTER-RATE, -ROTATION, -LOOKUP,
INGRESS-REST, INGRESS-EVENT, -EVENT-RECONNECT, INGRESS-QUOTA, TRIGGER-CHAIN, TRIGGER-SIGNED-WEBHOOK: **10/10**;
INGRESS-EVENT-MULTI-REPLICA **1/1** in `final-x/`. TIME-CRON-TZ, -INTERVAL, -ONE-SHOTS, -RELATIVE-EVENT, -BUSINESS-CALENDAR, -LIFECYCLE, -PLAN-FLOOR, -NL,
-SCALE-DUE-INDEX, -CONDITION: **10/10** (20 passed in one run). Skipped as in B2's regression: TIME-CATCH-UP
(stops the live beat ~4 min, `RW_ALLOW_BEAT_RESTART`) and TIME-EXACTLY-ONCE (needs a 2nd beat + schedule worker). No
regression from B7-L1..L4 or from DEF-5 (signed-webhook dedup / TRIGGER-SIGNED-WEBHOOK / TRIGGER-CHAIN all pass).

## 5. Open items (routed)
1. **Final-answer grounding on plain statements (P5).** A goal "Note for the record: supplier SUP-x prefers invoices by
   email. Reply ACK" was failed by the NLI claim-grounding check as a *high-risk* goal (claim support 0.00 < 0.70,
   "stagnated" after 3 replans) — the answer "ACK" was right. Not a trigger defect; the scenario uses a neutral goal.
2. **Workflow approvals and queue filters (doc).** Workflow approval events carry no goal-derived queue
   (`hitl_queue_ids: []`), so a hitl_* trigger with `hitl_queue_id` never fires for a workflow gate; scope those with
   `condition_cel` on `payload.workflow_id` (as PLATFORM-HITL-WORKFLOW does).
3. **Accuracy dimension is a live LLM judgement (P7).** The same "ACK" goal scored `accuracy` 1.0 in the baseline and
   0.0 in the final run; dimension triggers on judged dimensions fire non-deterministically for borderline answers.
4. **`run_forever.log` is 2.5 GB** and never rotated (owner, local ops).
5. Agent `timeout_seconds` looks unused by `run_goal` (only the plan's `goal_timeout_seconds` was found); noticed while
   looking for a cheap failing goal, not verified live (P5).
