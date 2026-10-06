# Backlog: `governance` (2026-10-06)

Source: `docs/audits/fixwave/pending-all-2026-10-05.json`, the 15 items with `area == "governance"`
(trust approvals 3, audit trail 2, cost budgets 1, grants 2, guardrails 2, HITL 2, permission matrix 1,
policy engine 2). Branch `backlog/bl-governance`, not pushed.

Each item was re-checked against the current code (this branch already includes the cost-ledger,
vision-budget, workflow-HITL-queue and QA-3..21 policy/guardrail fixes). None of the 15 was fixed by
those changes. QA-8 (policy hours as a set) touched F060-03's code, but the timezone was still dropped.

**Counts:** FIXED (was OPEN or PARTIAL) 14 · ALREADY-FIXED 0 · OBSOLETE 0 · NEEDS-OWNER 1.

No alembic revision was added. There is still one head (`a7c9e1f3b5d7`). `openapi.json` was
regenerated (`001a2fa92`).

| Item | Status | Reason | Commit / test |
|---|---|---|---|
| a03-F055-02 | FIXED | New `POST /grants/{id}/delegate` (admin role). It mints a narrowed child through `mint_delegation`. Scopes, window and cost cap can only narrow; anything wider is a 400. Fields left out take the parent's scopes, expiry and remaining budget. The grantee must be an agent of the tenant. The delegation is written to the audit chain. New `GET /grants/{id}/chain` returns the grant, its ancestors up to the root and its direct delegations. Both grant stores have a new `list_children` (tenant predicate plus RLS). | `f21f5a753` · `tests/api/test_grants_delegation_api.py` (3), `tests/governance/test_grant_delegation_children_pg.py` (real PG, app role) |
| a03-F055-07 | FIXED | New helper `_worker_grant_governance`. It decides grant enforcement first (`_agent_grants_enforced` enforces when it hits an error). If anything later fails, the autonomy mode is clamped to `supervised` and no grant store is set, so every tool call is denied under enforcement. Before, the flag started as False and stayed False when the ceiling lookup raised. | `7057c65dc` · `tests/scaling/test_worker_governance_wiring.py` |
| a03-F056-06 | FIXED | `ChatHITLCard` no longer builds the unsigned `/hitl/{id}/approve?token=<uuid>` link, which the signed (sig+exp) decision page rejects. The dead `stream_hitl` (no caller; the only source of `approval_token`) and its test are removed. Approve and Reject still go through the authenticated approvals API. Signed links are still minted on the server for email. | `ae1d57ede` · `ChatHITLCard.test.tsx` "offers no unsigned magic link…" (fails before) |
| a03-F056-07 | FIXED | New `HITLGateway.approve_async_outcome` returns accepted / resolved / vote count, with the count taken from the Postgres vote table. `approve_async` wraps it. `POST /governance/approvals/{id}/approve` and the signed email link now answer from that outcome, not from the cache local to the process. Behaviour change on purpose: `test_hitl_fail_closed.py` now also patches `approve_async_outcome`. | `76002df87` · `tests/api/test_hitl_quorum_response.py::test_below_quorum_vote_on_a_replica_that_never_cached_the_gate` (fails before) |
| a03-F057-01 | NEEDS-OWNER | Still true: `TrustApprovalStore` is read only by `/trust/approvals`. No agent, tool gate or worker reads it. Making these approvals gate execution changes security semantics, and it duplicates the real HITL gateway (`/governance/approvals`, durable, quorum across replicas). Options: **(A) retire** `/trust/approvals` and point the GovernancePanel's pending list at `/governance/approvals` (recommended: one approval system). **(B) pre-gate:** a trust approval for (goal_id, tool_name) becomes a gate the executor checks. Pending holds or denies the tool, rejected denies it, approved allows it and satisfies HITL for that call. **(C) pre-approval only:** an approved trust approval satisfies the HITL gate for that goal and tool. This lowers friction and needs a security sign-off. | — |
| a03-F057-03 | FIXED | `GET /trust/compliance-bundles` now lists the governance catalogue that enable and disable use (`pci_dss`, `india_dpdp`; no `sox`). Each entry has autonomy ceiling, required HITL tools, retention and residency, plus the guardrails-v2 rule bundle (if any) that holds its content rules. Before, it listed the guardrails-v2 catalogue, so enabling `pci`/`dpdp` answered 400. The old test asserting `pci` was updated. | `c078b5ad9` · `tests/api/test_phase8_9_guardrails_trust.py::test_every_listed_compliance_bundle_can_be_enabled` (fails before) |
| a03-F057-04 | FIXED | The `_approvals` module dict fallback is removed. A missing store now gives 503. `InMemoryTrustApprovalStore` (same contract: distinct approvers, threshold) is wired by `create_app` only for the in-memory build (`manage_pools=False`). A pooled app has no fallback until the lifespan wires the Postgres store. | `091045797` · `test_trust_approvals_are_503_without_a_wired_store`, `test_in_memory_app_build_wires_the_in_memory_store_and_pooled_does_not` |
| a03-F058-01 | FIXED | When a `record()` write exhausts its retries, the row is now parked in the Redis `audit_write_outbox` (wired in the API lifespan and the worker goal task) instead of only being counted as lost. The new beat task `drain-audit-write-outbox` (maintenance queue, every 30 s) replays rows oldest first through the same idempotent INSERT (`ON CONFLICT (id) DO NOTHING`, SIEM copy in the same transaction) and stops at the first failure. Rows are never dropped: a full outbox refuses new rows (counted as lost, as before), and a row that keeps failing is moved to a dead-letter list. The executor still uses the sync `record()`, which now has the outbox behind it. Immutability triggers are untouched. | `39748858c` · `tests/governance/test_audit_write_outbox.py` (5 unit, 2 fail before; 1 real PG app role + real Redis) |
| a03-F058-02 | FIXED | `audit_admin_action` now attributes the event from the `TenantContext`. It used to read `tenant.id` / `request.state.api_key`, which would have raised in `finally` on a real route. It records HTTP errors as `HTTP_<code>`, and an audit write failure never changes the route's answer. Applied to: policy create/delete/rollback, budget set, notification channel create/delete, guardrails-v2 rule create/update/delete and bundle enable, compliance bundle enable/disable. | `95a90c2c1` · `tests/governance/test_admin_action_audit_applied.py` (2 of 3 fail before) |
| a03-F060-02 | FIXED (removed) | `app.governance.time_policy` was imported nowhere. Its platform-wide "no destructive tools 22-06 UTC" and "no deploys at weekends" rules never ran. Time restrictions are tenant policy time windows (now timezone-aware, F060-03). Wiring platform-wide default blocks for every tenant would be a new product behaviour, so the dead module and its tests were removed. If the owner wants platform default time rules or blackout windows, add them as tenant policy templates. | `701ee2b77` |
| a03-F060-03 | FIXED | `POST /governance/policies` accepts an IANA `timezone` (validated, default UTC). It is stored in the version snapshot, returned by `GET /governance/policies`, restored by rollback and carried through the strict reload (`_time_windows_by_name` now returns hours, weekdays and timezone). The frontend list shows the policy's own zone. | `510742ea1` · `tests/governance/test_policy_timezone_reload.py` (fails before), `tests/api/test_governance_policies_pg.py` (real PG, app role) |
| a03-F061-04 | FIXED | The in-process `_LOCAL_DAILY` counter (used only without Redis) drops counters from past days when a new key is added, and has a key cap. | `8611a2f32` · `tests/governance/test_agent_permissions_daily_limit.py` (2 new, fail before) |
| a03-F062-04 | FIXED | New helper `_worker_cost_controller`. It uses the shared worker Redis client (`REDIS_URL` or the Celery broker URL, like `_worker_async_redis`) for `RedisCostController`, logs the in-process fallback instead of swallowing it with `except: pass`, and always binds `budget_configs`. | `7057c65dc` · `tests/scaling/test_worker_governance_wiring.py` |
| a03-F063-03 | FIXED | `POST /guardrails-v2/bundles/{name}` derives rule ids from (tenant, bundle, rule name). It loads the tenant's persisted rules first, keeps existing rules (re-enabling one that was disabled) and writes new ones through `add_rule_durable`. A failed write gives 503, and retrying is safe. | `d73f36917` · `tests/api/test_guardrails_v2_rule_management.py` (3 new, fail before; plus a real-PG two-replica test) |
| a03-F063-06 | FIXED | A RAG_INGEST `REQUIRE_HITL` hit now withholds the document (`guardrail_review_required`: skipped on connector ingestion, 422 on direct ingest routes), the same way workflow guardrails treat `hitl_required`. A redacting rule's redaction is applied to the PII-redacted text with `GuardrailsEngine.redact_text`, without a second `evaluate()`, so violations are no longer recorded twice and LLM-judge rules are no longer charged twice. | `8a626c03f` · `tests/ingestion/test_rag_ingest_hitl_and_single_evaluate.py` (3, fail before) |

## Notes for the owner

- **F057-01** is the only open decision (see options above). The recommendation is (A), because there
  are currently two approval systems and only `/governance/approvals` gates execution.
- **F063-06:** RAG_INGEST `REQUIRE_HITL` now means "do not index; needs review". There is no
  ingestion review queue, so a withheld document must be fixed and re-ingested (or the rule changed
  to block or redact).
- Noticed but out of scope: `compliance_autonomy_ceiling` docs mention a SOX bundle, but the
  governance catalogue has no `sox` (only guardrails-v2 has SOX rules). The Postgres
  `TrustApprovalStore.reject` also updates a request that was already resolved.

## Verification

- Focused unit tests for every touched module (governance, api, chat, scaling, ingestion,
  guardrails). Integration tests run one file at a time against testcontainers (colima):
  policies PG round trip, grant children PG, guardrail bundle two-replica PG, audit outbox PG+Redis,
  worker cost ledger PG, trust/AI-Ops persistence.
- Frontend: `vitest` for `src/features/chat/` and `GovernancePage`, `tsc --noEmit`, eslint on
  changed files.
- ruff and mypy (strict) are clean on every changed backend file. `test_no_provider_key_literals`
  passes. No provider-key patterns appear in the added lines.
- Not done: live-stack verification. The full suite was not run (focused tests only).
