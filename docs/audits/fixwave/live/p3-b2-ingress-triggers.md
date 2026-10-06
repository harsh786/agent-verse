# P3 / B2: generic ingress triggers (webhook, rest, event) on the live stack (2026-10-06)

Branch `live/p3-b2-ingress-triggers`, from `main` @ `8f6621d5e`; `main` merged twice (`e7600b67e`, then
`1fbdb302b` with the B7 code fixes). One alembic head, **`b8d0f2a4c6e7`** (no new migration). Nothing pushed.

Raw live output is in `p3-b2-ingress-triggers/` next to this file (`results.jsonl` + `summary.txt` per run, and the
pre-fix probes `probe1.out` / `probe1-dedup.out`). Scanned for the tenant keys, every PASS/SECRET/KEY/TOKEN value of
`/private/tmp/claude-501/rw/**/*.env` and the `av_` / `nvapi-` / `sk-` / `AKIA` patterns: 0 hits.

| Run | What | Images |
|---|---|---|
| `regress1/` | regression: SRC-DB-TABLE-RETRY (pg), WEB-URL-BOILERPLATE, TIME-ONE-SHOTS | main (redeployed from this worktree) |
| `probe1*.out` | hand probe of every B2 requirement, before any fix | main |
| `run1/` | INGRESS-* (first run) | B2-1 … B2-7 |
| `run2/` | INGRESS-EVENT-MULTI-REPLICA with a second API replica | B2-1 … B2-7 |
| `run3/` | TRIGGER-CHAIN + TRIGGER-SIGNED-WEBHOOK (found B2-9) | B2-1 … B2-7 |
| `final/` | INGRESS-* + INGRESS-QUOTA + TRIGGER-CHAIN + TRIGGER-SIGNED-WEBHOOK: **10/10** | B2-1 … B2-9 |
| `final-x/` | INGRESS-EVENT-MULTI-REPLICA: **1/1** | B2-1 … B2-9 |
| `regress2/` | the regression subset again: **3/3** | B2-1 … B2-9 |

## 1. Verdicts

| Type | Verdict | Live evidence (`final/`, `final-x/`) |
|---|---|---|
| **webhook** | **COMPLETE (fixed: B2-1, -2, -3, -4, -5, -6, -7, -9)** | INGRESS-WEBHOOK-SIGNED, -FILTER-RATE, -ROTATION, -LOOKUP, INGRESS-QUOTA, TRIGGER-CHAIN, TRIGGER-SIGNED-WEBHOOK |
| **rest** | **COMPLETE (fixed: B2-1, -5, -7)** | INGRESS-REST |
| **event** | **COMPLETE (fixed: B2-1, -4, -8; consumer reconnect and multi-replica already worked, TRG-17/18)** | INGRESS-EVENT, -EVENT-RECONNECT, -EVENT-MULTI-REPLICA |

### 1.1 Requirement by requirement (webhook)

| Requirement | Before (probe, main) | After (live) |
|---|---|---|
| Per-trigger signed URL/token | token per trigger (32-byte urlsafe), resolved pre-auth | unchanged |
| HMAC, constant-time; bad / missing signature 401 | already 401 | 401 (unsigned, bad, stale timestamp) |
| Replay: timestamp window | `X-Webhook-Timestamp` ignored; a 1 h-old delivery fired | signature covers `"{ts}.{body}"`; outside 300 s → 401 (B2-4) |
| Replay: delivery-id dedup, same delivery runs once | dedup by payload hash only | delivery id (`Idempotency-Key`, `X-Delivery-Id`, `webhook-id`, `X-GitHub-Delivery`, Stripe `evt_` id) is the firing identity; a redelivery is one goal + a `dedup` audit row (B2-4) |
| An identical body as a NEW delivery | **dropped forever** (live: `dedup` 65 s later, durable gate) | fires again (B2-4) |
| Size cap | 413 at 1 MiB (global middleware) | streamed cap in the endpoint too; plan payload limit → 413 (was "accepted") |
| Content types JSON / form / text | form and text became `{}` (and deduplicated against each other) | form → dict, `text/*` → `{"text": …}`, bad JSON 400 (B2-2) |
| Mapping (templates / paths) | `{{payload.ticket.id}}` rendered empty | dotted paths + list indexes; live goal text "Support ticket TCK-… from Asha Rao priority P1 first tag billing" (B2-5) |
| Filters | CEL `condition_cel` worked (P3 skipped) | 2 of 3 deliveries `condition_false`, audited, final 200 |
| Tenant isolation | token → owner tenant; other tenant 404 | other tenant: `/webhooks/{token}` 404, fire 404, GET 404, events `[]`; event on the same channel fires nothing |
| Rate limits | over the cap answered **200 "accepted"** (lost) | **429 + Retry-After**, audited `rate_limit`, never dedup-claimed (B2-1, B2-7) |
| Plan quotas | — | free tenant: 5 × 201 then 403 (INGRESS-QUOTA) |
| Audit row per delivery | fired / dedup / condition_false / rate_limit rows | unchanged; skip rows never block a legitimate redelivery |
| Dead-letter + retry | GOAL_ENQUEUE_FAILED and gate-unavailable → DLQ | + throttled bus events → `RATE_LIMITED` / `BULKHEAD_FULL` DLQ rows (replayable via `POST /triggers/dlq/{id}/retry`), live in INGRESS-EVENT |
| Token rotation | **no way to rotate a leaked URL** | `POST /triggers/{id}/rotate-token`: old URL 404 at once; secret rotation keeps the old secret only in its grace window (B2-6) |
| Indexed lookup | pre-auth lookup indexed; then every trigger of the type loaded and compared in Python | one read on `uq_schedules_webhook_token`; EXPLAIN (live) = Index Scan for both queries (B2-3) |
| Fires a goal and a workflow | goal: yes; workflow (`/wf-hooks`) with `auth: hmac`: **unsigned / forged deliveries started runs** | TRIGGER-CHAIN: bad signature 401, unsigned 401, forged token 401, replayed delivery id → the same run, 1 producer run, consumer resumed (B2-9) |

### 1.2 rest
`POST /triggers/{id}/fire` (API key, trigger RBAC role): the `Idempotency-Key` header is the firing identity (a retry
→ `dedup`; two calls without a key → two goals, B2-7), nested mapping (B2-5), over `max_firings_per_hour` → 429
(B2-7), other tenant 404, no key 401. Live: `[200, 200 dedup, 200, 200, 429]`, 3 goals, text "Invoice INV-… for
1830 EUR". The manual fire passes the caller's real role (`trigger_role`); the dispatcher's default is `system`, never
`operator` (already fixed, TRG-12).

### 1.3 event
`POST /triggers/events/{channel}` → Redis Stream → `EventTriggerConsumer` (one consumer group per consumer type).
- INGRESS-EVENT: 5 publishes → exactly 2 goals (O1 once although published twice with the same `event_id`; O3); the
  `total > 1000` filter skipped O2 (`condition_false`); the other tenant's event on the same channel fired nothing;
  a client `tenant_plan` / `tenant_id` is stripped. A second event over a 1/h cap → `RATE_LIMITED` DLQ row (B2-1).
- INGRESS-EVENT-RECONNECT: `CLIENT KILL` of all 6 XREADGROUP connections, three times; the consumers logged
  `Connection closed by server` → `trigger_consumer_restarting … in=1.00s` and rejoined; the next event fired once
  9.1 s later, an event published while they were down was delivered after the restart, `/health` `trigger_consumers: up`.
  **Already fixed** (TRG-17 supervisor restart + TRG-18 streams) — verified live, no change.
- INGRESS-EVENT-MULTI-REPLICA: a second API container (`agentverse-rw-backend2`, removed afterwards) joined the
  groups; 12 events → 12 goals, each once (the replicas shared the work). Found B2-8: the group held **45** consumer
  names (one per past process); after B2-8 it held 7.

### 1.4 Dispatcher order (cross-cutting)
**B2-1.** The Redis dedup key (SET NX, 60 s) was claimed before the rate limit, circuit and bulkhead. A throttled
original released it afterwards, but a redelivery arriving meanwhile was dropped as a duplicate and then the original
was dropped as throttled. Now: a non-claiming dedup peek (a replay never spends a rate token) → rate limit → circuit →
bulkhead → atomic claim. A throttled firing never touches the key; it is dead-lettered unless the caller answers 429.
A gate-unavailable skip no longer deletes a key it never claimed. Caller role: default `system` (verified).

## 2. Fixes (TDD: a failing unit test first, then the live scenario)

| Commit | Fix |
|---|---|
| `b9d300f24` B2-1 | gates before the dedup claim; throttled firings dead-lettered (`dead_letter_throttled`) — `test_dispatcher_gate_order.py` (6) |
| `5a3c7d23f` B2-2 … B2-7 | `app/triggers/webhooks/ingress.py` (capped body, JSON/form/text, delivery id, signed timestamp, skip → 429/503/413); indexed `find_by_webhook_token_async`; push/event keys on the delivery id (`dedup.py`); nested `{{payload.a.b.0}}`; `POST /triggers/{id}/rotate-token`; REST `Idempotency-Key` — `test_webhook_ingress_b2.py` (16); 5 older webhook tests' fakes accept the new kwargs |
| `eb8578cf9` B2-8 | prune group consumers idle > 1 h with nothing pending — `test_bus_stale_consumers.py` (2) |
| `45b784b7a` B2-9 | `/wf-hooks` enforces a declared `auth: hmac` (fails closed without a secret) — `test_webhook_hmac_auth.py` (3) |
| `bfe063714`, `69f3ec762` | `tests/real_world/test_ingress_triggers.py`: 10 INGRESS-* scenarios |

Tests after the last merge of main: triggers + workflow + schedules API + B7 scaling/evals: **2,280 passed**;
`test_trigger_replay_dedup.py` (testcontainers) 2/2; `ruff check .` clean; `mypy app` (strict) clean on 1,944 files.

## 3. Deployment, infra, env
- App services (backend, worker, subgoal-worker, workflow-worker, schedule-worker, beat) and
  `agentverse-rw-ingestion-worker` were recreated from **this worktree** (scripts `/private/tmp/claude-501/rw/p3b2/`),
  rebuilt twice (after B2-7 and after B2-9). `db-migrate` ran (head unchanged). The coordinator recreated them once
  more for an operator allowlist change (same code).
- A temporary second API replica `agentverse-rw-backend2` was started for INGRESS-EVENT-MULTI-REPLICA and removed.
- No env or volume change; `.env` is a symlink to main's. Docker build cache 25 GB (20 GB reclaimable), not pruned.
- Scenario triggers / goals were deleted / cancelled by the tests.

## 4. Regression
`regress1/` (main images) and `regress2/` (final images): SRC-DB-TABLE-RETRY (postgresql), WEB-URL-BOILERPLATE,
TIME-ONE-SHOTS — **3/3 both times**.

## 5. Open items (routed)
1. **Workflow webhook secret at rest (P4).** `trigger.webhook.hmac_secret` lives in the workflow definition JSON
   (returned by the workflow APIs) rather than encrypted like trigger secrets.
2. **No-delivery-id dedup window (doc).** Without a delivery id, an identical body is one delivery within its 300 s
   window; a sender retry that crosses a window boundary can run twice. Senders should send a delivery id.
3. **DLQ retry identity (P4).** A DLQ retry uses its own key (`dlq-retry:<id>:<n>`), so retrying a throttled delivery
   that the sender also redelivered successfully can run it twice.
4. **Typed vendor webhooks** (GitHub/Stripe/Jira/Teams…) share the new lookup, delivery-id and 429 behaviour; their
   live verification stays with B3 (deferred by the owner).
