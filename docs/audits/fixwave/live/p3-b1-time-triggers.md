# P3 / B1: time triggers and scheduling on the live stack (2026-10-06)

Branch `live/p3-b1-time-triggers`, from `main` @ `1010edf83`.
- `main` moved during the phase (CHAT-KB, the run_goal heartbeat fix, CHAT-SEC, chat durability). It was merged in three times (`106dd7371`, `648d33c60`, `15c79286c`) and once more for docs at the end.
- The branch holds **17 fixes (B1-1 … B1-17)**, three no-op merge migrations, the TIME-\* scenarios and this report.
- There is one alembic head, **`b8d0f2a4c6e7`**. It merges main's `c3e8a1f5b7d2` / `e9a3c5d7f1b2` / `a6c2e8f4b0d3` with this branch's `d4f6b8a0c2e3` → `e5a7c9b1d3f4`. The live database is at that head.
- Nothing was pushed.

The raw output of the live runs is in `p3-b1-time-triggers/` next to this file (`results.jsonl` + `summary.txt` per run, plus `beatwatch.log`). The copies were scanned for the tenant keys and for the `av_` / `nvapi-` / `sk-` / `AKIA` patterns. There were 0 hits.

| Run | What | Images |
|---|---|---|
| `regress1/` | regression subset: SRC-OBJ-FILTERS, SRC-DB-TABLE-RETRY (pg), SRC-MONGO-HOST-CHANGE, WEB-URL-BOILERPLATE, AGK-WORKFLOW | built from this worktree at `1010edf83` (= main) |
| probes (not files) | every type, by hand, before any fix | same |
| `run1/` | TIME-\* on B1-1 … B1-11 | rebuilt |
| `run2/` | TIME-CONDITION, TIME-PLAN-FLOOR, SCHED-\*, SCHEDULED-WF-HITL | B1-1 … B1-11 |
| `final/` | TIME-\* + SCHED-\* + SCHEDULED-WF-HITL | B1-1 … B1-14 + main |
| `final2/` | TIME-SCALE-DUE-INDEX + the regression subset | + B1-15 |
| `x1/` | TIME-EXACTLY-ONCE (two beats, two schedule workers) | + B1-15 |
| `final3/` | TIME-CRON-TZ, -ONE-SHOTS, -CONDITION, -INTERVAL, -CATCH-UP, SCHED-CRUD | + B1-16 + main |
| `final4/` | TIME-INTERVAL, TIME-CATCH-UP | final images (B1-1 … B1-17) |

## 1. Verdicts per type

Every type was checked on the live stack first. The fixes are listed per type.

| Type | Verdict | Live evidence |
|---|---|---|
| **cron** | **COMPLETE (fixed: B1-1, -2, -3, -4, -5, -7, -11, -12, -13, -14, -15, -16)** | TIME-CRON-TZ (`final3/`): an Asia/Kolkata (+05:30) cron and an America/New_York cron for the same instant each fired **once**, 10.6 s / 10.7 s after the UTC minute (1.5–1.9 s with on-the-minute ticks in `run1/`). Neither fired the previous day's slot. Goals ran `plan=enterprise` and completed with "ACK"; the other tenant gets 404. The next run is the slot + 1 day. Also TIME-LIFECYCLE, TIME-CONDITION, SCHED-CRUD, and DST (unit, §1.1) |
| **interval** | **COMPLETE (fixed: B1-3, -4, -9, -17)** | TIME-INTERVAL (`final4/`): 3 fires, 59.8 s and 58.9 s apart, one goal each, `plan=enterprise`. UI: an untouched interval is now submitted with the 3600 s it shows |
| **once** | **COMPLETE (fixed: B1-3, -4, -5, -9, -10, -13, -16)** | TIME-ONE-SHOTS (`final3/`): fired once, 5.1 s after a mid-minute due time, and never again (next run: none). A once 1 h in the past fired late once with `catch_up=all` and never with `none`. UI: a time picked in the browser's zone is stored as that UTC instant |
| **relative_delay** | **COMPLETE (fixed: B1-8, + B1-3/4/5/16)** | Fixed base: TIME-ONE-SHOTS (once, 5.1 s late). **Relative to the triggering event** (new): TIME-RELATIVE-EVENT, see §2 |
| **deadline** | **COMPLETE (fixed: B1-3, -4, -9, -16)** | TIME-ONE-SHOTS: a deadline with a 120 s warning fired once at deadline − 120 s (5.1 s late), never again |
| **business_calendar** | **COMPLETE (fixed: B1-6, + B1-1/2/5/13)** | TIME-BUSINESS-CALENDAR: the open calendar fired every minute. The same calendar with today as a holiday fired 0 times, as did one where today is not a business day. Invalid holidays and empty business days are 422 |

### 1.1 Edge cases

| Edge case | Result (live unless marked) |
|---|---|
| Timezone | Kolkata and New York crons fire at the right UTC minute. Unknown zones (`America/New_Yrok`, `Asia/Kolkatta`, `IST`) are 422 on both `/schedules` and `/triggers` (B1-2). NL maps "IST" to Asia/Kolkata (B1-10) |
| DST | Unit, simulating the beat minute by minute (`test_cron_dst.py`). A fall-back fixed-hour job fires once (it fired twice, B1-11). A wildcard-hour job runs through both hours. A slot in the skipped spring-forward hour fires once. 09:00 EDT → 09:00 EST. Australia/Sydney is handled too |
| Plan floor (free vs enterprise) | TIME-PLAN-FLOOR. **Free:** 899 s → 422, 900 s → 201; an every-minute cron or business_calendar → 422; a PATCH to 60 s → 422; NL "every minute" → 422 naming "the free plan … at most every 15 min". **Enterprise:** 59 s → 422, 60 s → 201, an every-minute cron → 201 |
| Pause / resume / update / delete | TIME-LIFECYCLE (`final/`). Paused: 0 fires in 150 s. Resumed: 1 fire in the next 80 s, not the 3 slots of the pause (B1-1). After an edit to `*/2`: only even minutes. After a delete: 0 fires in 150 s, the history is kept, and a queued fire is suppressed (B1-3) |
| Missed fires after a beat restart | TIME-CATCH-UP (`final4/`). The trigger loop was down for 3 minutes and the beat was restarted. No fire happened during the outage. The first tick after it fired **3** slots for `all`, **1** for `latest` and **1** for `none` (the on-time slot only) (B1-5). The policy is described in §4 |
| Exactly once with 2 beats / 2 workers | TIME-EXACTLY-ONCE (`x1/`). A second beat and a second schedule worker ran next to compose's. Over 250 s, a cron and an interval each made 4 goals in 4 distinct minutes, with no duplicates and no dedup skips needed. RedBeat kept one beat active: 14 ticks from compose's beat, 0 from the standby |
| business_calendar weekends + holidays | TIME-BUSINESS-CALENDAR, and unit tests: the local date decides, and a custom Sun–Thu week works |
| deadline fires once | TIME-ONE-SHOTS |
| relative_delay relative to the event | TIME-RELATIVE-EVENT (§2) |

### 1.2 Known issues from the brief

Each was verified on the live stack first.

| Issue | Verdict |
|---|---|
| Every beat-fired trigger runs on the free plan | **Already fixed** (TRG-06, `20fc20a24`). Every fired goal in every TIME-\* run logged `run_goal_queue_selected … plan=enterprise queue=goals.enterprise` |
| Beat fires drop CEL `condition_cel` | **Already fixed** (TRG-07, `7b6d33fc8`). In TIME-CONDITION, `payload.tenant_id == "nobody"` was skipped (`condition_false`, audited) and `payload.goal_text != ""` fired. An event-relative delay fired for the P1 event and skipped the P3 event. **New defect, fixed in B1-12:** a condition the evaluator cannot run was accepted on save |
| RRULE / SOLAR branches unreachable | **Already fixed** (`34673a99c`). The branches were removed and no `rrule` / `solar` code is left in the beat |
| Every tick SCANs all schedule keys | **Already fixed** (TRG-15, `3aa7e5d33`). Each tick is one indexed claim on `ix_schedules_due_global`; the plan with seqscan off is Index Scan + LockRows. TIME-SCALE-DUE-INDEX (`final2/`): with 300 extra yearly schedules, one tick evaluated the new rows and every other tick checked 12–31 rows |
| Goal lock not released on early returns | **Already fixed** (WF-19, `3ee973778`). Every early return of `run_goal` releases the lock; main's heartbeat fix `a93a62308` is merged on top |
| NL schedule creation (SCHED-NL) | SCHED-NL passes. Harder sentences failed, **fixed in B1-10**. TIME-NL: "9:30 IST" → `30 9 * * 1-5` Asia/Kolkata; "in 20 minutes" → a once ~20 min ahead; "tomorrow at 8am UTC" → tomorrow 08:00Z |

## 2. Event-relative delays (B1-8)

Before this phase, `relative_delay` only had a fixed `fire_at_iso` base. Now a `relative_delay` with an `event_channel` fires `relative_offset_seconds` after **each event** published on that channel (`POST /triggers/events/{channel}`), or after the timestamp at `relative_to_field` in the event.

TIME-RELATIVE-EVENT (run1, final), with two events on `rw.support.escalated.<tag>`, one of them delivered twice:
- **Trigger A** counts 60 s from the event. **Trigger B** counts 120 s from the payload's `opened_at`, which is 60 s after the event.
- **Arming.** Each trigger armed exactly 2 fires; the redelivery armed nothing. The fires were due at the expected instants and were listed by `GET /triggers/{id}/delayed-fires`.
- **Fires.** Each trigger made exactly 2 goals: "Follow up on support ticket TCK-4411 / TCK-4412", with the payload in the text.
- **Deleted trigger.** A third trigger, deleted before its fires were due, made 0 goals.
- **Other tenant.** The other tenant's event on the same channel name armed nothing here.
- **Refusal.** A relative_delay with neither base is 422.

## 3. The beat itself (B1-14 … B1-16)

The final runs exposed problems in the beat, not in the trigger code.

**Stalls.** The compose beat went silent for 5–15 minutes at a time: 01:56–02:01, 02:02–02:18, 02:19–02:34, 02:35–02:50 and 02:51–03:07 UTC. Each time it then crashed with `LockNotOwnedError` (its RedBeat lock had expired) and was restarted.
- Its result connection held a 229 KB unread backlog: every beat-sent task subscribed the beat to a result it never reads.
- Its maximum sleep (300 s) equalled the lock timeout (300 s).
- Neither Redis client had a socket timeout. The 15-minute gaps match the kernel's TCP give-up (one run showed a 924.5 s gap).
- **B1-14:** a scheduler that sends with `ignore_result`, and a 30 s maximum sleep. **B1-15:** socket / connect timeouts, keepalive and health checks on the broker and RedBeat clients.
- Since then (`beatwatch.log`, 03:38–05:04 UTC): **0 lock crashes**, and the tick was never missing outside the deliberate TIME-CATCH-UP outage.

**Tick timing.** With the 60 s interval the tick ran at second 43, so slots were up to 59 s late. B1-7 made it a crontab, but RedBeat keeps a crontab entry on the second of the beat's first run (second 07 after one restart). **B1-16:** the tick runs every 15 s, so a slot fires ≤ ~15 s late (final3: 10.6 s for cron, 5.1 s for one-shots).

**Environment note.** Stopping the compose beat is not an outage on this machine. The launchd runtime (`scripts/run_forever.py`) starts its own beat within a minute; this happened at 09:55 IST in the first TIME-CATCH-UP attempt, which therefore saw every slot fire. The scenario now takes the trigger loop down by holding the beat's own overlap guard in Redis, then restarts the beat.

## 4. Fixes (TDD: a failing unit or integration test first, then the live scenario)

| Commit | Fix | Root cause (live evidence) |
|---|---|---|
| `c49dd6c69` B1-1 | A time trigger never fires a slot from before it was created, resumed or re-timed. Adds `schedules.armed_at` (migration `d4f6b8a0c2e3`, catalog-only) | A never-fired cron fired "the most recent slot ≤ now". A weekday-09:00 cron created at 00:08 UTC fired at once for Monday 09:00. A 05:41 IST cron created at 05:38 IST fired yesterday's 05:41. A resumed or edited cron replayed up to 60 slots |
| `aab98ad25` B1-2 | An unknown timezone is refused (422) on every path. `POST /schedules` keeps its `timezone` | `America/New_Yrok` was stored and run in UTC. `/schedules` had no timezone field, although the Schedules page sends one |
| `c5563b44b` B1-3 | A queued beat fire re-reads its schedule (RLS plus a tenant predicate): no goal after a delete or a pause | An interval deleted at 00:13:31 still created a goal at 00:14:11 |
| `3db92787a` B1-4 | A `schedule-worker` pool (schedules + triggers.poll, no goal queue) in dev and prod compose, k8s and both helm charts | The tick and the fires shared the 2-slot goal worker. The 00:10, 00:12 and 00:13 ticks did not run, 14 schedule tasks were queued, and a once fired 88 s late |
| `f0856cf1c` B1-5 | `catch_up` = all / latest / none: settable, persisted and honoured | The only switch (`coalesce_missed_runs`) was not a spec field |
| `bb78b5c2e` B1-6 | business_calendar takes `holidays`, `business_days` and `business_hours_start` / `_end`, evaluated in the trigger's zone | The calendar was a hard-coded Mon–Fri 09–17 with no holidays |
| `08be999ec` B1-7 | The tick runs on the minute and stale ticks expire (superseded for triggers by B1-16) | The tick ran at second 43, so slots were up to 59 s late, and queued ticks piled up |
| `f43176444` B1-8 | Event-relative relative_delay: the `trigger_delayed_fires` table (migration `e5a7c9b1d3f4`, FORCE RLS), a SKIP LOCKED claim, cascade on delete, and `GET /triggers/{id}/delayed-fires` | `relative_to_field` was refused, so the feature could not be built on the old model |
| `8cd4854ed` B1-9 | UI: local times are sent as UTC instants and shown back in local time; the defaults on screen are submitted; server refusals are shown for every family; new controls | Browser in Asia/Calcutta: "06:30" was stored as `2026-10-06T06:30`, which the backend reads as UTC (5 h 30 late). An untouched interval got 422 with nothing on screen. **Verified again on the rebuilt frontend:** stored `2026-10-06T01:20:00.000Z`, the card shows 06:50 local, and the untouched interval is 201 with 3600 s |
| `bf0a1361e` B1-10 | NL: the current time and rules for IANA zones, one-off times and intervals go into the prompt; abbreviations map to zones; a bare offset becomes a once | "9:30 IST", "in 20 minutes" and "tomorrow at 8am UTC" were all 422. Free "every minute" was refused for the wrong reason |
| `d0af18ab8` B1-11 | A fixed-hour cron fires once on a fall-back day | `30 1 * * *` in New York fired twice on 2026-11-01 |
| `23a8e3136` B1-12 | A condition the evaluator cannot run is refused on save | `payload.goal_text.contains("ACK")` was 201, then every fire was `condition_error` |
| `a196a0d01` B1-13 | The API's `next_fire_at` is the real next run (also the analytics `next_run_at`) | A new weekday-09:00 cron reported 01:40, the beat's claim lease. business_calendar showed holiday and night slots. Fired one-shots showed 9999-01-01 |
| `69b36604b` B1-14 | The beat sends with `ignore_result` (`AgentVerseRedBeatScheduler`) and sleeps at most 30 s | Beat stalls and `LockNotOwnedError`, see §3 |
| `c1effe61d` B1-15 | Socket / connect timeouts, keepalive and health checks for the broker and RedBeat Redis clients | An 86 s stall and the 15-min stalls, see §3 |
| `dffc12614` B1-16 | The trigger tick runs every 15 s (expires after 14 s) | RedBeat keeps a crontab entry on its start second |
| `e471335a2` B1-17 | Interval slots are anchored at `armed_at`, so fires are at least one interval apart | With 15 s ticks an every-60 s interval fired 29.9 s apart: epoch-aligned slots |
| `ae972dfc8`, `f57ded591`, `32a935c38` | No-op merge migrations `f6b8d0e2a4c5`, `a7c9e1f3b5d6`, `b8d0f2a4c6e7` | Two alembic heads after each merge of main |

**Catch-up policy.** It is documented in `TriggerSpec.catch_up` and in the UI ("Missed runs").
- `all` (default): replays every slot missed since the last fire, at most the 60 most recent, oldest first, each as its own goal.
- `latest`: fires only the most recent missed slot.
- `none`: skips a slot that is more than 90 s late.
- A paused, resumed or edited trigger never replays slots from before it was re-armed (B1-1).
- Interval triggers fire their current slot once and never replay a backlog.
- One-shots fire late once, unless the policy is `none`.

**New tests.**
- `tests/scaling`:

  | File | Tests |
  |---|---|
  | `test_time_trigger_armed_floor.py` | 10 |
  | `test_time_trigger_catch_up.py` | 9 |
  | `test_business_calendar_holidays.py` | 7 |
  | `test_cron_dst.py` | 5 |
  | `test_scheduled_fire_recheck.py` | 5 |
  | `test_interval_anchor.py` | 4 |
  | topology tests in `test_worker_queue_coverage.py` | 4 |
  | beat tests in `test_celery_app.py` | 3 |
- `tests/triggers`:

  | File | Tests |
  |---|---|
  | `test_validation_timezone.py` | 3 (parametrized) |
  | `test_relative_delay_event.py` | 8 |
  | `test_nl_scheduler_time_grounding.py` | 7 |
  | `test_condition_validated_on_save.py` | 3 |
  | `test_next_run_display.py` | 9 |
- 4 new Postgres integration tests in `test_trigger_persistence_integration.py`, on the NOBYPASSRLS app role.
- vitest for the time form, the router and the card.
- Live: `tests/real_world/test_time_triggers.py`, 12 scenarios.

## 5. Deployment, infra and env

- **Redeployed from this worktree.** This was done in step 1 and again after every fix batch; the scripts are in `/private/tmp/claude-501/rw/p3b1/`.
  - Command: `docker-compose -f .claude/worktrees/p3b1/agent-verse-backend/infra/docker-compose.yml` build, then `up -d --no-deps --force-recreate`.
  - Services: backend, worker, subgoal-worker, workflow-worker, beat and the **new `schedule-worker`** (B1-4).
  - The one-shot `db-migrate` ran up to `b8d0f2a4c6e7`.
  - The `agentverse-rw-ingestion-worker` and the frontend (B1-9) were re-created.
  - The app containers no longer mount the p1e worktree.
- **`agentverse-rw-web`** was re-created from this worktree's `tests/real_world` (same aliases and port, plus a `p3b1` label).
- **TIME-EXACTLY-ONCE** used a temporary `agentverse-rw-beat2` and `agentverse-rw-schedule-worker2`. Both were removed afterwards.
- **TIME-CATCH-UP** holds `beat_guard:fire_due_schedules` for ~200 s and restarts the compose beat once.
- **`.env`.** `agent-verse-backend/.env` is a symlink to main's (as in p1e). No env changed.
- **Volumes and disk.** No volume was dropped. Docker disk was at 82 % (29 GB free) before the builds; the build cache was not pruned.
- **Cleanup.** Probe schedules were deleted. The two UI-created test triggers of the browser's (free) tenant were deleted with psql and Redis DEL.

## 6. Regression and suites

- **Regression subset, step 1** (`regress1/`, images = main): **5 of 5 passed.** SRC-OBJ-FILTERS, SRC-DB-TABLE-RETRY (postgresql), SRC-MONGO-HOST-CHANGE, WEB-URL-BOILERPLATE and AGK-WORKFLOW.
- **Same subset on the B1-15 images** (`final2/`): **5 of 5 passed.**
- **Existing schedule scenarios:** SCHED-CRUD, -PLAN-FLOOR, -NL, -FIRES-GOAL, -FIRES-WORKFLOW and SCHEDULED-WF-HITL all passed. WF-SCHEDULE-PLAN-FLOOR skips on the enterprise tenant (60 s floor).
  - SCHED-CRUD failed once in `run2/`, before B1-13, with the claim-lease `next_fire_at`. It passed in `final/` and `final3/`.
- **TIME-\*:** all 12 passed on the final images or the run before them. Notes:
  - TIME-INTERVAL and TIME-CATCH-UP were re-run in `final4/` after B1-16 / B1-17.
  - TIME-SCALE-DUE-INDEX failed once in `final/`. The scenario counted org-mission ticks; it was fixed and passed in `final2/`.
- **Backend suites** (after the last merge of main):
  - Unit tests, `-m "not slow and not integration"`: **2,753 passed** on scaling, triggers, db, infra, chat and the schedules API tests.
  - Before the last merge: 9,774 passed / 1 failed in the wide sweep over api, ingestion, services and lifecycle. The failure was `tests/ingestion/test_scheduler_jobs.py::test_sync_commits_cursor_every_100_docs`, under heavy load. Re-run alone: 23/23.
  - `ruff check .` is clean, and `mypy app` (strict) is clean on 1,942 files.
  - Integration `test_trigger_persistence_integration.py`: 12/12.
- **Frontend:** vitest 5,197/5,197 before the merge; 509/509 on triggers, chat and schedules after it. `tsc` and eslint are clean (0 errors).

## 7. Open items (routed)

1. **Workflow schedules (P4/P9).** `workflow.fire_due_workflow_schedules` still reads every published scheduled workflow each minute (GIN-indexed, in 500-row pages). It fires only within a 150 s grace, with no catch-up policy and no armed floor. Exactly-once holds: a Redis SET NX per occurrence, failing closed.
2. **Payload interpolation (P4).** `{{payload.x}}` in a goal template reads top-level keys only; `{{payload.ticket.id}}` renders empty.
3. **CEL (P8).** Without cel-python, conditions are limited to the safe subset (no `contains` / `size`). B1-12 now refuses the rest on save. Installing cel-python is an owner decision.
4. **Beat liveness (P9).** B1-14 and B1-15 remove the stall mechanisms that were found. What dropped the beat's Redis connection on the loaded colima VM was not isolated. P9 monitoring needs an alert for "no `fire_due_schedules` for more than 2 min".
5. **Dev runtime (owner).** The launchd `run_forever.py` starts its own beat whenever compose's beat is briefly absent, including during every `--force-recreate`. For up to a minute two code versions can then serve the same Redis. A redeploy should pause it, or it should wait longer before taking over.
