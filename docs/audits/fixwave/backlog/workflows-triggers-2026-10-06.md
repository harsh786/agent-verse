# Backlog: `workflows-triggers` (2026-10-06)

Source: `docs/audits/fixwave/pending-all-2026-10-05.json`, the 20 items with `area == "workflows-triggers"`,
plus one item the coordinator added (`WF-REPLAY-1`). Branch `backlog/bl-workflows`, not pushed.
Owner scope rule: fix workflow and trigger items only. Items that are only about chat channels are
`DEFERRED (channels excluded by owner)` and were not changed. Org/collab features are parked.

**Counts:** fixed (were OPEN) 6 (5 audit items + WF-REPLAY-1) · ALREADY-FIXED 0 · OBSOLETE 2 ·
DEFERRED 13 (11 channels excluded, 2 need an owner decision).

New alembic revision: `a7c9e1f3b5d7` (workflow_webhook_replay_guard), chained on `f1a3c5e7b9d2`, so there is one head.

| Item | Status | Reason | Commit / test |
|---|---|---|---|
| a06-F099-03 | DEFERRED (owner decision) | Still true: kombu's Redis transport has one `visibility_timeout` per broker DB, and it is sized to the longest goal (25 h). Goals, workflow runs, ingestion, missions, AI-Ops, eval suites and pattern runs now have their own dead-worker sweepers. One-off tasks without a sweeper (for example `deliver_workflow_callback`, `publish_mission_deliverable`, training export) still wait about 25 h after a *whole-worker* crash. A child that dies is requeued at once (`task_reject_on_worker_lost`). A short per-queue timeout needs either a second broker DB/Celery app for the goal queues, or a restorer that checks which workers are alive. That is an architecture choice. | — |
| a06-F101-01 | OBSOLETE | The goal is crash-durable without a LangGraph saver. Every finished step is a `goal_checkpoints` row, and a redelivered goal resumes from it and fails closed if the row is unreadable (`checkpoint_resume.py`, `tests/agent/test_checkpoint_resume.py`). Calls that ran are kept in the OI-1 action ledger, and the call in flight now carries an idempotency key (a06-F101-04). A worker LangGraph saver would store the same state a second time, and `AsyncRedisSaver` cannot be shared across the per-task event loops. The SIGTERM log already names MemorySaver honestly (WF-15). | — |
| a06-F101-04 | FIXED | Every non-read MCP dispatch of the agent executor (approved, autonomous and parallel extra calls) now runs in `idempotency_scope("goal:<goal_id>:<call fingerprint>")`. The redelivered goal re-issues the in-flight call with the same `Idempotency-Key` / `_meta.idempotencyKey`. | `9427d9f46` · `tests/agent/nodes/test_executor_tool_idempotency_key.py` (3 of 4 fail before) |
| a06-F102-08 | FIXED | `ChatService._fulfill_schedule` (compound turns) now runs `creatable_error`, passes `quota_plan` (PLAN_MAX_TRIGGERS) and reports a refused or failed create as "could not schedule". It used to answer "I'll ...". | `31579425a` · `tests/chat/test_understanding.py::test_afulfill_schedule_*` (5 fail before) |
| a06-F103-02 | FIXED | The trigger bulkhead now counts the tenant's non-terminal trigger goals (`trigger_events` ⋈ `goals`, RLS plus explicit tenant predicates, inside the plan goal timeout + 1 h) on top of the dispatches running now. It bounds in-flight goals, as the spec (§13.7/§14) requires; past the cap the firing is `bulkhead_full` and dead-lettered. | `6a418c6b3` · `tests/triggers/test_bulkhead_in_flight.py`, `tests/triggers/test_trigger_persistence_integration.py::test_in_flight_trigger_goals_counted_on_the_app_role` (real PG) |
| a06-F105-01 | DEFERRED (owner decision) | Still true: no MQTT client is wired. MQTT is UNSUPPORTED in `dispatch_map`, so creating one is refused honestly (no silent dead trigger). Wiring it needs a design: which broker (per tenant or shared), tenant isolation of topics, credentials in the vault, and which process holds the subscriptions. `paho-mqtt` is available only as the `connectors` extra. | — |
| a06-F108-06 | DEFERRED (channels excluded by owner) | Gateway `legacy_unverified` bindings still route (`binding_store.py`). | — |
| a06-F108-07 | DEFERRED (channels excluded by owner) | Per-org `/v1/gateway/{org}/{channel}` still seeds `PlanTier.FREE` (`gateway/router.py:_tenant_ctx_for`). | — |
| a06-F130-03 | DEFERRED (channels excluded by owner) | `ROUTABLE_STATUSES` still includes `legacy_unverified` (`api/channels/verification.py:65`). Same root as F131–F137, F139, F174. | — |
| a06-F131-03 | DEFERRED (channels excluded by owner) | Same as F130-03 (channel mapping verification). | — |
| a06-F132-03 | DEFERRED (channels excluded by owner) | Same as F130-03. | — |
| a06-F133-01 | DEFERRED (channels excluded by owner) | Same as F130-03 (email channel mapping). | — |
| a06-F134-01 | DEFERRED (channels excluded by owner) | Same as F130-03 (email channel mapping). | — |
| a06-F135-01 | DEFERRED (channels excluded by owner) | Same as F130-03 (sms channel mapping). | — |
| a06-F137-01 | DEFERRED (channels excluded by owner) | Same as F130-03 (form channel mapping). | — |
| a07-F139-01 | DEFERRED (channels excluded by owner) | Discord: `legacy_unverified` still routes, and any guild member can post the code and displace it. | — |
| a07-F147-01 | OBSOLETE | REST is specified as the authenticated manual fire (`POST /triggers/{id}/fire`, "manual fire (REST type)", trigger spec §API). Token ingress is the `webhook` type. `create_trigger` issues no token for REST, and the UI offers "Fire now". The live B2 check passed: 401 without a key, Idempotency-Key, 429 (B2-7). | — |
| a07-F154-01 | FIXED | Alertmanager now answers 503 when *any* firing alert failed, not only when all failed. The retry re-sends the batch, and episodes that already became goals are deduplicated by fingerprint+startsAt. | `038093816` · `tests/integrations/test_alert_integrations_governed.py::test_partly_failed_alertmanager_batch_is_a_503_and_the_retry_fills_the_gap` |
| a07-F174-03 | DEFERRED (channels excluded by owner) | Slack `legacy_unverified` mapping displacement. | — |
| a07-F175-01 | DEFERRED (channels excluded by owner) | Teams replies: `_send_chat_reply` has no Teams branch. | — |
| WF-REPLAY-1 | FIXED | `POST /wf-hooks/{token}` with `auth: hmac` now takes the delivery identity from signed bytes only: `signed-body:` (also the run idempotency key, with no time window) or `signed-ts:` (the delivery id stays the run identity). Accepted deliveries go into the new `workflow_webhook_replay_guard` (FORCE RLS, tenant predicate, with an HMAC fingerprint of the secret, never the secret itself). A guard read error gives 503. Refused or throttled deliveries are not recorded. Retention: rows from an old secret are dropped on the first delivery under the new one; the retention job deletes rows whose workflow row is gone and `signed-ts:` rows past the window. Archived workflows keep their rows. | `1b3a80e56` · `tests/workflow/test_webhook_replay_guard.py` (4 fail before), `tests/workflow/test_webhook_replay_guard_pg.py` (real PG, app role) |

## Notes for the owner

- **Channels:** one decision closes 9 of the deferred items (F108-06, F130–F137, F139, F174): stop routing
  `legacy_unverified` mappings (or force a re-verification campaign) and require verification by a channel admin, not any member.
- **a06-F099-03 / a06-F105-01** need the decisions described in the table.
- **Suite:** `tests/scaling/test_worker_memory_budget.py::test_helm_worker_pools_fit_their_memory_limit[legacy]`
  already fails on this base (YAML parse of the legacy Helm chart). It is unrelated to these changes.
