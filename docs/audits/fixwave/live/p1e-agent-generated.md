# P1e: agent-generated knowledge on the live stack (A12, 2026-10-06)

Branch `live/p1e-agent-generated` (from `main` @ `3761259f4`; `main` did not move, so there was no merge). It holds 5 fixes in
7 fix commits, 4 scenario commits and this report. There is one alembic head, `a12c4e6f8b10`. Nothing was pushed.

The raw output of the live runs is in `p1e-agent-generated/` next to this file (`results.jsonl` + `summary.txt` per run).
The copies were scanned for the tenant keys and for the `av_` / `nvapi-` / `sk-` / `AKIA` patterns. There were 0 hits.

| Run | What | Code |
|---|---|---|
| `regress1/` | P1b + P1c + P1d regression: SRC-OBJ-\*, SRC-DB-\*, SRC-MONGO-\*, SRC-REDIS-\*, SRC-ES-\*, WEB-\* | images built from this worktree at `3761259f4` (= `main`) |
| `baseline/` | The 5 new AGK-\* scenarios on the unchanged code | same images |
| `run1/` … `run6/` | AGK-\* while fixing | images rebuilt from the branch |
| `final/` | AGK-\* + the full P1b / P1c / P1d regression | images rebuilt from the final branch |
| `final2/` | AGK-GOAL-OUTPUT, AGK-GOVERNANCE again (provider throttling / planner phrasing in `final/`) | same images |

## 1. What "agent-generated knowledge" was, before

Verified on the live stack first, as the owner rule requires.
- **Live probe.** I created an `agent_generated` Source with `source_types` `[goal_output, hitl_decision]` and a minimum score of 0, then completed a goal.
  - Result: **0 documents**.
  - A manual sync failed with `agent_generated sources are push-only…`.
- **Code review** (with a subagent sweep of every producer).
  - `AgentGeneratedConnector.on_webhook` had no caller anywhere.
  - Nothing published goal outputs to it. The `goal.completed` chain event carries no output text.
  - The `knowledge.ingest` tool is never registered.
  - The chat `ingest_knowledge` skill is never dispatched. If it were, it would call `ingest_document` without an embedder and skip redaction.
  - HITL decisions, reflexion lessons, memory consolidations, workflow results and chat transcripts never reach a collection.
  - `RPAExecutor.scrape_to_kb` has no caller. The only platform-to-KB path was `POST /knowledge/ingest/rpa-url`, which scrapes a URL the user supplies.

**Baseline** (`baseline/`, unchanged images): 0 of 5 AGK-\* scenarios passed.
- The empty sync `failed` "push-only".
- An approval decision never became a document (90 s timeout).
- Unknown kinds were accepted on save (201).
- The other two baseline failures were scenario issues, both fixed later:
  - The baseline shared its tenant with the concurrent regression session, whose end-of-session sweep deleted a collection mid-test.
  - A goal fact about money was refused as a high-risk step.

## 2. Verdicts (per sub-type)

| Sub-type | Verdict | Evidence (live) |
|---|---|---|
| **Goal outputs** | **COMPLETE (fixed: P1e-1, P1e-3, P1e-5)** | AGK-GOAL-OUTPUT, details below this table |
| **HITL decisions: goal approvals** | **COMPLETE (fixed: P1e-1)** | AGK-APPROVAL: a supervised agent's approved gate and rejected gate each became a document **6.1 s** after the decision, both rank 1. Each document carries the decision, the authenticated approver, the note, the goal id and an origin `{approval_id, goal_id, decision}`. A pending approval is never indexed. The reviewer's e-mail in the note is redacted. |
| **HITL decisions: workflow gates** | **COMPLETE (fixed: P1e-1, P1e-3)** | AGK-WORKFLOW: the decision document cites `agentverse://workflow-approvals/<id>` with origin `{approval_id, workflow_run_id, workflow_id}`, rank 1, **9.1 s** after the run completed. Before P1e-3 the decision's notify never left the API ("Connection refused"). |
| **Workflow run results** | **COMPLETE (fixed: P1e-1, P1e-4)** | AGK-WORKFLOW: the run document `agentverse://workflow-runs/<id>` is built from the step results, rank 1. The e-mail and AWS key in the outputs are not indexed (`[REDACTED]`). A disabled Source runs no sync. Before P1e-4: `outputs={}` gave no document, and a workflow-scoped Source backfilled 48 goal approvals. |
| **Agent learnings (reflexion lessons)** | **COMPLETE (fixed: P1e-1)** | AGK-GOAL-OUTPUT: the agent's 2 lessons became `agentverse://memories/<id>` documents. Only active, non-expired lessons of the allowed classifications (default public / internal) are indexed. Sealed (confidential / restricted) lessons never are. A lesson that leaves `active` drops out of the live listing, so reconcile removes it. |
| **Memory consolidations** | **OPEN (no content to ingest)** | `consolidate_long_term_memory` only de-duplicates and deletes rows in `long_term_memory`; it produces no new text. Its effect on agent-generated knowledge is deletion, which the live listing and reconcile cover for reflexion lessons. Indexing `long_term_memory` as such is a P6 decision. |
| **Chat transcripts** | **OPEN (owner decision: privacy)** | They are stored in `chat_messages`. Indexing a user's private conversations into a tenant-wide collection needs a product and privacy decision (per-user ACL, consent). `chat_transcript` is refused on save with the list of supported kinds. |
| **PII / secret screening before indexing** | **COMPLETE (already, via the pipeline)** | Agent-generated documents go through the Source pipeline's stage 6 (`pii_action=redact` by default). The P8b redaction is on main. Generated e-mail, mobile number, AWS key and reviewer e-mail were 0 hits in every probe (AGK-GOAL-OUTPUT, -APPROVAL, -WORKFLOW). A goal asked to repeat a key is refused as high-risk in bounded-autonomous mode, by design. |
| **Tenant isolation** | **COMPLETE** | Every query has an explicit `tenant_id` predicate on top of RLS (integration tests run as the NOBYPASSRLS app role). Live: tenant B gets 404 on A's collection search and on A's Source. B's own Source indexes none of A's goals. |
| **Dedup on re-run / incremental sync** | **COMPLETE** | There is a per-stream keyset cursor with a 2-minute overlap; unchanged documents are skipped by content hash. Live re-sync: 0 indexed, 4 skipped. A later goal was added with the earlier documents unchanged (same ids, same chunk counts). A burst of events costs 1–2 syncs (lock plus run-once-more marker). |
| **Honest failures** | **COMPLETE (fixed: P1e-1)** | AGK-FAILURES: 6 invalid configs refused 422 on save with the reason (unknown kind, empty kinds, score > 1, bad `since`, ids not a list, bound > 10,000). A Source without a collection gets sync 422. An empty first sync is `completed` with 0 documents and cursor `{"v": 1}`; it used to be `failed` "push-only". `validate` reports connection ok. A DB outage during a sync is reported as `ConnectorFetchError` (failed job), never as a "completed" job. |
| **Governance: legal hold blocks replacement** | **COMPLETE** | AGK-GOVERNANCE, under a collection hold: reindex (delete + re-sync) is refused **409** "would delete held data", and a re-sync replaces nothing (0 indexed, 2 skipped). The P1d-5 store guard refuses any replacement of a held document. Documents are immutable per record and the evaluation score moved to the origin map, so they do not drift. |
| **Governance: deleting a goal propagates** | **COMPLETE (fixed: P1e-2, P1e-5)** | AGK-GOVERNANCE, details below this table |

Goal outputs (AGK-GOAL-OUTPUT):
- Two real goals (configured LLM) became documents through an **event-triggered** sync. They were present by the time the second goal was read as complete (`seconds_after_last_goal` 0.0); the event jobs ran 1–2 s after completion.
- Search ranks: rank 1 for both facts. Each citation has `source_url` `agentverse://goals/<id>` and origin `{kind: goal_output, goal_id}`.
- `/rag/query` answered "5622 kg … [1]" citing the goal.
- The generated e-mail and mobile number were not indexed.
- Re-sync: 0 indexed / 4 skipped. A third goal was added incrementally and the earlier documents were unchanged.
- Tenant B: search 404, source 404, and its own Source indexed nothing of A.
- Erased goals are no longer served (P1e-5).

Deleting a goal (AGK-GOVERNANCE): the only goal deletion is the data-subject erasure (`/compliance/dpdp/erasure/{id}/execute`).
- **Under a collection hold.** The goal row is deleted (`goals: 1`) and the held knowledge is kept and reported (`knowledge_chunks_held` note). `GET /goals/{id}` is now 404; before P1e-5 it was 200 from the API's memory.
- **After the hold is released.** Reconcile removes the erased goal's document, because the live listing no longer contains it.
- **Without a hold.** The erasure removes the goal's knowledge at once: `knowledge_chunks_goal_derived: 3`, `verified: true`, and the erased text is no longer served.
- **Other deletions.** Workflow-run retention and lesson expiry propagate through reconcile the same way.

## 3. How it works now (P1e-1)

- **Pull connector over the platform's own tables**, as the least-privilege app role under the tenant's RLS context, with explicit `tenant_id` predicates. Streams:

  | Stream | Rows read |
  |---|---|
  | `goal_output` | completed, non-dry-run, top-level goals; the final answer comes from the terminal `goal_complete` / last `step_complete` event, the same reader as `GET /goals/{id}` |
  | `hitl_decision` | approved or rejected `approval_requests` |
  | `workflow_decision` | decided `workflow_approvals` |
  | `workflow_output` | completed non-test `workflow_runs`; outputs, else the latest attempt of each completed step |
  | `learning` | active reflexion `memory_records` |
- **Keyset pages and cursor.** Pages are keyset on `(timestamp, id)`, backed by 4 partial indexes built CONCURRENTLY (migration `a12c4e6f8b10`). The cursor is JSON, one entry per stream, with a 2-minute overlap. Each sync is bounded by `max_items_per_sync` (default 1,000, at most 10,000). `since` is an optional floor.
- **Scoping.** `agent_ids` scopes what goals produce: goal outputs, decisions on those agents' goals, and lessons. `workflow_ids` scopes what workflows produce. A Source scoped to one kind of producer reads nothing of the other. `min_eval_score` / `require_eval_score` filter goal outputs by their latest scorecard.
- **Documents.** Each is markdown with a stable id per record and an `agentverse://goals|approvals|workflow-approvals|workflow-runs|memories/<id>` URL. Its `origin` map (kind plus the producing ids) is stored by the pipeline on every chunk (`chunk_origin`, a small flat string map). `/knowledge/search` and `/knowledge/.../rag` citations return it.
- **Live listing.** `iter_live_doc_ids` lists what is still eligible, so reconcile (KB-44) removes the knowledge of deleted or no-longer-eligible records. Held documents are excepted.
- **Events → immediate sync.** These events call `notify_agent_generated`, which does one cached query to find whether a Source listens for that kind (10 s per process; invalidated on Source create, update and delete):
  - goal completion (API and worker paths)
  - goal-approval and workflow-gate decisions
  - completed workflow runs
  - new active lessons

  `ingestion.agent_generated_notify` then waits until the record is committed (5 s retries, up to 6 attempts). It either queues `ingestion.sync_source` (`triggered_by=event`) or, when a sync holds the lock, sets a run-once-more marker that the sync honours when it ends. The notify never raises into the producer, and a lost event is read by the next sync anyway.

## 4. Fixes (TDD: failing unit / integration tests first, then the live scenario)

| Commit | Fix | Root cause |
|---|---|---|
| `dc735f125`, `508345584`, `a66df5943` P1e-1 | Agent-generated Sources index goal outputs, goal and workflow decisions, workflow results and lessons, triggered by the events. Save-time validation; `since`; agent scoping of decisions; score in the origin; deletion tracking declared in the catalogue | The connector was push-only and nothing pushed to it. Its `on_webhook` would have trusted a payload's `tenant_id` |
| `e5e0578a1` P1e-2 | Subject erasure deletes, in every per-dimension chunk table, the subject-tagged chunks and the chunks whose `origin.goal_id` is an erased goal. Held collections and documents are kept and reported; verification scans every table | Erasure touched `knowledge_chunks_768` only (the live embedder writes 2048) and knew nothing of goal-derived knowledge |
| `665222e4a` P1e-3 | The notify is sent by name through the configured Celery app | Live: the API enqueued from `asyncio.to_thread`, where the task proxy resolves an unconfigured default app (broker on localhost: "Connection refused"). Workflow-gate decisions were never notified |
| `10b5b4290` P1e-4 | Runs without declared outputs are indexed from their step results; filters scope their producer | Live: `workflow_runs.outputs` is `{}` for such runs, so there was no document. A `workflow_ids` Source backfilled every goal approval of the tenant (48 documents) |
| `72fcc305c` P1e-5 | A goal whose row is gone (with a task queue, non-dry-run: the row is written synchronously at submit) is evicted and answers 404 | Live: after the erasure, `GET /goals/{id}` still answered 200 from the API's in-memory record |
| `eaae3c3f4`, `e929a6338`, `512cd46bf` | AGK-\* scenarios and README | – |

New or changed tests:
- `tests/ingestion/connectors/test_agent_generated_connector_integration.py`: 5, real Postgres, NOBYPASSRLS role. Covers every stream, tenant B, cursor and overlap, the bound, the score floor, agent filter and `since`, step results, scoping and the live listing.
- `tests/ingestion/test_agent_generated_events.py`: 9. Covers notify, cache, task deferral, lock and rerun marker, the Celery app from a thread, chunk origin, and the goal-completion hook.
- `tests/lifecycle/test_deletion_agent_knowledge_integration.py`: 1, real Postgres.
- `tests/services/test_goal_erased_not_served.py`: 3.
- Rewrites: the on_webhook cases in `test_ingestion_framework.py`, `test_stable_doc_ids_connectors.py`, and the mocked orchestrator session.

## 5. Deployment, infra and env changes

- **Redeploy from this worktree** (step 1):
  - `docker-compose -f .claude/worktrees/p1e/agent-verse-backend/infra/docker-compose.yml build` for db-migrate, backend, worker, subgoal-worker, beat and workflow-worker.
  - The one-shot `db-migrate`: first a no-op at `e7b1c4d9a2f6`, later **`e7b1c4d9a2f6 -> a12c4e6f8b10`**. There is a single head.
  - Then `up -d --no-deps --force-recreate` for the app services, plus a re-created `agentverse-rw-ingestion-worker`.
  - The app containers now bind-mount **this** worktree (`app/{org,gateway,bootstrap,agent/graph.py}`, migrations). After merging, redeploy from `main` before removing the worktree.
- **`agentverse-rw-web` re-created** from this worktree's `tests/real_world` (it mounted the p1d worktree). It has the same aliases, port and labels (`p1d` + `p1e`).
- **Not touched.** The frontend container (no bind mount; its image was built from p1d at the same code). postgres / pgbouncer / keycloak still mount the p1a worktree's infra files: unchanged config, left alone.
- **No volume was dropped.** Docker disk stayed at 81 % (29 GB free). Build cache is 24.7 GB, of which 19.8 GB is reclaimable; it was not pruned.
- **Env:** none changed. The runner is `/private/tmp/claude-501/rw/p1e/rw2.sh` (+ `RW_TENANT_OVERRIDE`); `rebuild.sh` / `redeploy.sh` are next to it. A probe Source and collection created in the suite tenant were deleted again.

## 6. Regression (step 2)

- `regress1/` ran on images built from this worktree at `main`, before any P1e code: **38 of 38 passed** in 38.5 min.
  - SRC-OBJ-\* (9), SRC-DB-\* (6), SRC-MONGO-\* (7), SRC-REDIS-\* (3), SRC-ES-MAPPINGS / -AUTH-FAILURES, and WEB-\* (11).
  - The `-k "not pagination"` filter deselected SRC-ES-SYNC there; it runs in `final/`.
- `final/` (the final images, with the regression run as it stood before the fixes): **39 of 39**, see §7.

## 7. Final run

`final/` (images rebuilt from the final code, 54 min) passed **42 of 44**, then `final2/` covered the two goal scenarios:

- **All 39 P1b / P1c / P1d scenarios passed.** That covers SRC-OBJ-\* (9), SRC-DB-\* (6), SRC-MONGO-\* (7), SRC-REDIS-\* (3) and SRC-ES-\* (3), now including SRC-ES-SYNC, plus WEB-\* (11).
- **AGK-APPROVAL, AGK-WORKFLOW and AGK-FAILURES passed.**
- **AGK-GOAL-OUTPUT and AGK-GOVERNANCE failed on a goal, not on A12.**
  - In one, the planner phrased a no-tool text step so that the risk classifier asked for an approval ("sensitive target (infrastructure)"). This is open item 4.
  - In the other, the LLM provider rate-limited planning: "rate-limited … after 5 attempt(s)". That goal's `failure_reason` was `null` in `GET /goals/{id}` (open item 8).
  - The scenarios now resubmit a goal at most twice on exactly these two reasons and record each retry (`512cd46bf`).
- **`final2/`** (same images): **AGK-GOAL-OUTPUT and AGK-GOVERNANCE pass** with 0 retries needed.
  - Both facts ranked 1, nothing leaked, and RAG answered "7627 kg per pallet [1]" citing the goal.
  - Governance: reindex 409 under hold, the held erasure kept and reported the knowledge, reconcile removed it after release, and the unheld erasure removed 3 goal-derived chunks with `verified: true`.

**Result: 5 of 5 AGK-\* and 39 of 39 regression scenarios pass on the final images.**

Suites on the final tree:
- `ruff check .` clean, and `mypy app` strict (1,925 files) clean.
- Unit run of tests/ingestion, governance, workflow, memory, services, triggers, lifecycle, the knowledge / ingestion API tests, real_world_harness and no_provider_key_literals: **6,829 passed**, 246 skipped.
  - 3 errors came from `test_approval_store_mutation_pg.py`, which needs `DOCKER_HOST`. Re-run with it: 3/3 pass.
- Integration, one file at a time:

  | File | Result |
  |---|---|
  | `test_agent_generated_connector_integration.py` | 5/5 |
  | `test_deletion_agent_knowledge_integration.py` | 1/1 |
  | `test_deletion_cascade.py` | 3/3 |
  | `test_replace_legal_hold_integration.py` | 5/5 |
  | `test_approval_store_mutation_pg.py` | 3/3 |

## 8. Open items (routed, not fixed here)

1. **Memory consolidations (P6).** Consolidation produces no content. Whether `long_term_memory` facts should also be KB documents is a P6 decision.
2. **Chat transcripts (owner).** This needs a privacy and ACL decision before any indexing (per-user conversations in a tenant-wide collection). The chat `ingest_knowledge` skill is dead code: it calls `ingest_document` without an embedder and skips redaction. Remove or fix it (P8).
3. **`knowledge.ingest` tool / `RPAExecutor.scrape_to_kb` (P4/P5).** Both exist but are never registered or called: workflows and agents cannot write to the KB. This is a product decision about agent write access.
4. **Risk classifier false positives (P5/P8).** A no-tool goal that only *states* a price ("costs 9,083 INR per box"), or a dock-door rule ("sensitive target (infrastructure)", intermittently, by planner phrasing) is classed "change to a sensitive target (money)" and refused in bounded-autonomous mode.
5. **Erasure linkage (P8).** A goal is linked to a data principal only through `execution_context` text. The goal API cannot record a principal, so the scenario tags the goal with psql. The legal-hold API has no release route (also seen in P1d).
6. **UI (P10).** The Sources wizard has no form for the agent_generated kinds and filters (defaults apply). `types.ts` still describes the family as "Goal outputs, HITL decisions, learnings".
7. **Scale (P9).** The live listing streams every eligible id per reconcile, and a first sync of a large tenant is bounded to `max_items_per_sync` per sync. Event-triggered syncs continue a truncated backfill only while events keep arriving; otherwise the Source's interval does. `since` limits the backfill.
8. **`failure_reason` is null (P5).** A worker goal that failed with "Planning unavailable: LLM provider rate-limited" shows `failure_reason: null` in `GET /goals/{id}`. The reason exists only in the `goal_failed` event.
