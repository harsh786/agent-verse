# Worktree salvage, 2026-10-06

All committed branch work was already on main (checked by patch-id and subject).
This pass covers the **uncommitted** edits left in 17 old agent worktrees. Most
were killed mid-edit by the Mac restart around 2026-10-02 10:40; a few stopped on
10-05 01:1x; the four `wf_*` audit worktrees date from 09-27. The source
worktrees were only read. Backups of their diffs and untracked files are in
`/private/tmp/claude-501/wtcheck/`.

Branch: `fix/worktree-salvage`, from main `fba9cf5b8`. Alembic still has one
head (`b8d0f2a4c6e7`), and no migration was added or copied.

## Verdicts

| Worktree | Branch | Intended change | Verdict | Evidence (main) | Salvage commit |
|---|---|---|---|---|---|
| agent-a0a0da5120f3bd136 | worktree-agent-a0a0da5120f3bd136 | SAML-01/SSO: SAML ACS starts a user session (`user_sessions`, one-time code exchanged at `POST /auth/session/exchange`), public SAML login and ACS routes, `SSOCompletePage` | ALREADY-ON-MAIN | `2572a5ba7`, `03ae9519a`, `0d071bb8e`, `3d516b75a`; migration `a7c3e9f1b2d4_user_sessions.py`; `app/api/auth.py:221`, `app/api/enterprise.py:2510,2534`; main's `user_sessions.py` and tests are later supersets | none |
| agent-a32d3365d274c1a3c | worktree-agent-a32d3365d274c1a3c | OPS-37: streamed training export, aggregate preview, durable export jobs (`training_export_jobs`, Celery task, routes), frontend jobs API | ALREADY-ON-MAIN, and main goes further | `03a897c8c` (untracked files byte-identical); `a6bcdea03` (fenced claims, active-job cap); `cf9507e73` (NF-17 expiry). The worktree's migration `b8d5f0e3c2a4` has a stale `down_revision` | none |
| agent-a3c87f9f0b77b9eec | (detached) b44c155ee | real-world suite fixtures: `fixture_server`, `corpus`, JSON fixtures, conftest fixtures | SUPERSEDED | `199cec12a`, `8f3630f09`. Main's `fixture_server.py` is a superset; main's `corpus.py` fixes the `{v:g}` XLSX precision bug the worktree still has; the worktree's deletion of `helpers.py` was a half-finished rewrite (main's `helpers.py` has `key_from_env`) | none |
| agent-a5eba6cf1a2bea89a | worktree-agent-a5eba6cf1a2bea89a | SVC-05: SSE resume cursor uses durable event `_seq`; keyset replay; subscribe before replay; frontend drops replayed events | ALREADY-ON-MAIN / SUPERSEDED | `15be56b1c` (backend: `_DeliveredEvents` tolerates out-of-order events, where the patch used `seq <= last`), `fb988562f` (frontend uses a per-goal seen-set where the patch used a running max); `tests/services/test_sse_resume_cursor*.py` has 10 tests to the worktree's 5 | none |
| agent-a696307d9338941d4 | worktree-agent-a696307d9338941d4 | SAML-01: `TenantContext.user_id` plus the `user_sessions` table (migration `b5f8d2c0e3a4`) | ALREADY-ON-MAIN | `2572a5ba7`; table created by `a7c3e9f1b2d4_user_sessions.py`; `app/tenancy/context.py:82` `user_id`. The stale migration must not be copied (it would be a second `user_sessions` create and a second head) | none |
| agent-a737d3e48c33e7b25 | worktree-agent-a737d3e48c33e7b25 | CORE-18: durable strategy-run checkpoints (`strategy_run_checkpoints`, `PostgresStrategyCheckpointStore`) | ALREADY-ON-MAIN, and main goes further | `fd531b0bf`; `app/orchestration/strategy_checkpoint_store.py`, migration `c1e5a7b9d3f2` re-parented on main; main adds voyager resume and fails loudly on a capped answer | none |
| agent-a815bab79cb97d102 | worktree-agent-a815bab79cb97d102 | WF-TIMEOUT-MISREPORT: an inner timeout keeps its cause and elapsed time instead of reporting "exceeded step timeout" | ALREADY-ON-MAIN | `18a6a0482`; `app/workflow/compiler.py:107-120,462-483`; `tests/workflow/test_run_failure_error.py:217`; `tests/e2e_full/test_workflow_timeout_cause_e2e.py` | none |
| agent-a895c7a18bd42c3cf | worktree-agent-a895c7a18bd42c3cf | CHAT-CHANNEL-DEAD: remove the dead sync channel-session path; tests move to the async path | ALREADY-ON-MAIN | `ac6458380` (same 5 files; main keeps `test_channel_message.py`, rewritten to cover the live path) | none |
| agent-a9c06085c934d55b1 | worktree-agent-a9c06085c934d55b1 | RV-08 (a09-F212-01/-03): GDPR export complete or failed, never partial (`app/enterprise/gdpr_export.py`, shared by the sync and async paths) | PARTIAL: core ALREADY-ON-MAIN; 4 gaps MISSING and ported; 2 items NEEDS-OWNER | core: `2e4f13753` (sync, `compliance.py:267-338`), `90450073a` (async keyset/ceiling, `tasks.py` `_gdpr_export_section`), frontend failed state. Gaps were: (1) the async job exported only goals and audit, not agents, schedules or knowledge collections; (2) a "ready" sync export was saved best-effort, so its link 404'd on other replicas; (3) the async payload and the job's `complete` ran in two transactions; (4) the failure to mark a job failed was swallowed | `4a8bed96a` (1, 3, 4), `0048fd34d` (2), `080571bb5` (real-PG test) |
| agent-aada7be70ab209938 | worktree-agent-aada7be70ab209938 | RV-03: MCP health beat reads the Postgres `mcp_servers` registry (cursor, bounded concurrency, pinned probes); partial index `b3d9f1a7c2e5` | SUPERSEDED | `3e31d3df8` (a02-F034-N1) `app/mcp/health_sweep.py` does multi-page sweeps within a time budget plus a per-probe timeout; pinned/SSRF tests on main. The index was deliberately skipped (`leftovers-small.progress.json` `MCP-HEALTH-PROBE-INDEX`: the planner could not use it with main's predicate). `connector_health.py` is replaced by `health_sweep.py` | none |
| agent-abe3355f78e78c215 | worktree-agent-abe3355f78e78c215 | KB-44: bounded upstream-deletion reconcile (streamed live listings into `ingestion_live_listings`, keyset anti-join, holds per page, delete cap) | ALREADY-ON-MAIN, and main is stronger | `969c2eeef`, `806a103d2`; `app/ingestion/scheduler.py:158-480`, `app/rag/store.py:1158-1259` (adds an `indexed_before` guard against a concurrent sync). The migration `e3d7f9b1c5a2` is on main, re-parented. The per-Source `reconcile_interval_seconds` override is SUPERSEDED by the global `ingestion_reconcile_interval_seconds` | none |
| agent-g08be | fix-g08be-org | ORG-32: chat search and session summary read Postgres in DB mode | ALREADY-ON-MAIN | `af1e9bb43`; `app/chat/repository.py:682`, `app/chat/service.py:2644,2666`, `app/chat/router.py:721,733`; `tests/chat/test_durable_search_summary.py` | none |
| g08fe | fix/g08-org-frontend | FE-01 / a08-F202-01: emergency-stop counts and status come from the server; the store is no longer persisted; "status unknown" banner | ALREADY-ON-MAIN | `9477db129`; `app/governance/emergency_stop.py:242` `record_stop_outcome`; `AppLayout.tsx`, `stores/emergency.ts`, `GovernancePage.tsx` | none |
| wf_01f33451-68a-1 | audit/system-jobs-scaling | cross-tenant system jobs use the maintenance-role factory; per-tenant writes run under RLS | ALREADY-ON-MAIN | `1e0942ab6` (same 15 files; main has the fixed `timedelta` interval binds) | none |
| wf_01f33451-68a-2 | audit/system-jobs-ingestion | ingestion scheduler, job tracker and DLQ under RLS and the maintenance role | ALREADY-ON-MAIN | `826f92c7c` (the patch is byte-identical to that commit's diff) | none |
| wf_01f33451-68a-3 | audit/governance-startup | HITL, guardrails, audit_v3 and usage startup and writes respect RLS | ALREADY-ON-MAIN | `fdb076128` (audit_v3 later SUPERSEDED by CHAIN-01 `e1a940bb5`) | none |
| wf_01f33451-68a-4 | audit/request-paths | civilization, notification channels and skills_runtime writes work under the app role | ALREADY-ON-MAIN / SUPERSEDED | `bd64943cf` (civilization still exists; not obsolete); skills cache SUPERSEDED by OPS-34 `649c529fd` (`app/skills_runtime/tenant_store.py`) | none |

## What was ported (agent-a9c06085, RV-08)

The module `gdpr_export.py` was not copied, because main's design (a sync export
capped at 10k rows, an async export keyset-paged up to 100k rows) is the one to
keep. The missing behaviour was ported onto main's code instead:

- **`4a8bed96a`**: changes to `run_gdpr_export` in `app/scaling/tasks.py`:
  - It also exports agents (including inactive ones), schedules and knowledge
    collections, using main's keyset paging and ceiling, plus a `counts` map.
    The sync export tells large tenants to use the async export, which was
    missing those three sections.
  - The payload upsert and the job's `complete` UPDATE now run in one
    tenant-RLS transaction.
  - A failure to mark the job failed is logged as
    `gdpr_export_mark_failed_failed` instead of `except: pass`.
- **`0048fd34d`**: `_db_save_request(strict=)`.
  - A `ready` sync export is now recorded strictly. If it can't be recorded, the
    controller raises `ExportNotRecordedError` and doesn't cache the export.
    `GET /enterprise/compliance/export` then answers 503 with `Retry-After`.
  - A `failed` export (which has no link) is still reported when it cannot be
    recorded. The in-memory path with no database is unchanged.
- **`080571bb5`**: the real-Postgres test runs the export under a
  NOBYPASSRLS role and asserts the new sections.

## NEEDS-OWNER

- **a9c06085, API-key metadata and the real tenant profile in the GDPR export.**
  The worktree exported `api_keys` metadata (never the hash) and
  `tenants.name/email/plan_tier`. Main deliberately exports `api_keys: []` and a
  context-only profile. Including them is a product/privacy call.
- **a9c06085, "no database means the export fails".** Main deliberately keeps the
  in-memory export when no database is configured, and its tests rely on that.
  Changing it conflicts with main's design.

Optional, not ported, because the owner's triage already decided:
- aada7be7's partial health-probe index (`MCP-HEALTH-PROBE-INDEX` was skipped).
- abe3355f's per-Source reconcile interval (main uses a global setting).

## Verification

- `uv run ruff check .`: all checks passed.
- `uv run mypy app` (strict): no issues in 1944 source files.
- Unit tests:
  - `uv run pytest tests/enterprise tests/scaling tests/api/test_enterprise_coverage_boost.py tests/api/test_enterprise_extra3.py -m "not integration and not slow"`: 1782 passed.
  - The other export callers (`tests/compliance/test_async_gdpr.py`, `tests/api/test_enterprise_comprehensive2.py`, `tests/e2e/test_full_stack_e2e.py`, `tests/e2e/test_agent_graph_e2e.py`): 219 passed, 2 skipped (SDK absent).
- Integration tests (testcontainers, real Postgres):
  - `tests/scaling/test_gdpr_export_download.py` and `tests/enterprise/test_compliance_tables_rls_integration.py -m integration`: 14 passed.
- `uv run alembic heads`: single head `b8d0f2a4c6e7`.
- No frontend files were touched.
