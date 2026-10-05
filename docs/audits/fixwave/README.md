# Fix wave (post-recert) — durable state

Source of open items: `docs/audits/2026-09-29-recert/<group>.json` (`still_open` + `new_defects` per feature).
Rules for every fix agent: `BRIEF.md`. Original per-agent scopes: `prompts/<agent>.txt`.
Progress (rewritten after every item): `progress/<pkg>.progress.json`.

## State at 2026-10-05 (session resumed after weekly limit)
- `main` = batch 8 merged locally at `05991cf3c` (73 commits ahead of origin, NOT pushed).
- Batch-8 full suite (2026-10-03): backend 23 failed / ~22k passed, chunk 3 did not run
  (`tests/mcp/test_code_interpreter.py` deleted by CODE-06), frontend vitest 1 failed / 5010, tsc clean.
  Must be green before pushing.
- Unmerged finished work: `fix/g08-org-frontend` (10 commits FE-04..FE-24), `fix-g08be-org` (6 commits ORG-35..ORG-42).
- In-flight packages (worktree branch → package): ae7c731 g04net (SSRF/A2A/rate limit), ad01d56 g04gov,
  a204a38 g03rt, a2088c4 g03mcp, adae33c g07mem, a18149053 g07evals (MEM-53 wip), ad5a044 live L-02/L-03.
  ae7c731 and a204a38 both started durable A2A tasks — g04net owns A2A; g03rt's A2A diff is parked as a patch.

## Update 2026-10-05 (later)
- The "Agent verse hadrensing audit" session (on the user's instruction) fast-forwarded local main to
  `c825b26f3`: both g08 branches, SSRF-02, OAUTH-05, PERC-01/02, POL-01, TRUST-05, MEM-40 and wip MEM-53
  cherry-picked + alembic merge `f1e790b4050a` (single head). Still NOT pushed; full suite not yet run on it.
- MEM-53 completion lands as a follow-up commit on top of the wip (no rewrite of main).
- That session is re-verifying all recert open gaps → `docs/audits/fixwave/reverify-2026-10-05.json`.

## Resume procedure
1. Stabilize main (fix suite failures), merge the two g08 branches, rerun suite, push.
2. Resume each in-flight package from its worktree: commit/finish uncommitted work, rebase on main,
   continue remaining items of its scope, update its progress file.
3. Merge finished packages in batches; full suite green; push; redeploy; live baseline.

## Update 2026-10-05 (ownership)
The user stopped the "hadrensing audit" session; THIS session owns every fix, merge and push.
- 10 reverify highs: wf-hooks RateLimiter → stabilizer (fixed on its branch); connector secrets + health → g03mcp;
  worker tool gate → g04gov; prospective scope → g07mem; AgentRouter, repo guardrail, GDPR export (+ org roles,
  get_metrics RLS, goal_lifecycle fail-open) → highs agent (`progress/highs-2026-10-05.progress.json`).
- a08 services/frontend/org backlog → own agent (`progress/a08.progress.json`).
- Integration + e2e_full tiers never ran after batch 8 (known: tenant_vault_keys not granted to app role) →
  integration stabilizer (`progress/integration-suite.progress.json`).
- Finished, waiting for the merge batch: g04net (SSRF-04, A2A-01; migration c8e41a2d9f37), livefix (L-02 c4e1a7b9d2f3, L-03).
  Both migrations chain after f1e790b4050a, so add a merge revision when merging both.
- Owner decisions open: A2A per-agent public directory exposure rule (A2A-03); Helm worker memory (8 children in 2Gi).

## Handoff 2026-10-05 ~01:30 (usage limit reached)
- Integration branch `integrate/2026-10-05` (worktree .claude/worktrees/integrate): main@9015740e8 + 31 picks
  (SECRET-01 000951fbb, SSRF-04, A2A-01, L-01..03, stabilizer fixes, NATIVE-01/04, RPA-07, OAUTH-04/06, MCPCLI-*,
  HITL-07/08/09, TRUST-02, INC-07, MEM-38/39/47) + alembic merge 1d53d25e0ea7. ruff/mypy clean; migrations from
  scratch OK; 27 real-PG tests pass. Full unit suite results: /private/tmp/claude-501/intsuite/out0{0..3}.txt (+fe_*).
- main moved to 6488dd528 (RV-02/05/06/09 by the "hadrensing audit" session). That session, on the user's direct
  instruction, is rebasing integrate onto main and pushing — check origin/main before pushing anything.
- Finished, not yet on main: g07mem branch worktree-agent-adae33c84289bd073 (7 commits incl. MEM-42, a05-F084-N1
  prospective scope; head b42d6e1f8a37). Owner decisions in progress/g07mem.progress.json.
- Still running when cut off (resume from their worktrees + progress files): stabilizer (unit), integration stabilizer
  (progress/integration-suite.progress.json), g04gov, g03mcp, g07evals (MEM-53 wip), highs (GDPR, org roles,
  get_metrics RLS, lifecycle fail-open), a08 services/frontend/org, a09 enterprise (worktree fix-a09).
- Not yet started: a10 critic backlog (95 open) — see reverify-2026-10-05.json.

## integrate/2026-10-05 suite result (NOT green — do not push as-is)
```
## 00 8 failed, 9182 passed, 161 skipped, 178 deselected in 1460.50s (0:24:20)
FAILED tests/knowledge/test_ingestors_coverage.py::TestConfluenceIngestor::test_ingest_space_empty_returns_no_chunks
FAILED tests/knowledge/test_ingestors_coverage.py::TestConfluenceIngestor::test_ingest_space_with_page_content
FAILED tests/knowledge/test_ingestors_coverage.py::TestConfluenceIngestor::test_ingest_space_skips_short_content
FAILED tests/knowledge/test_ingestors_coverage.py::TestJiraIngestor::test_ingest_project_empty
FAILED tests/knowledge/test_ingestors_coverage.py::TestJiraIngestor::test_ingest_project_with_issues
FAILED tests/knowledge/test_ingestors_coverage.py::TestJiraIngestor::test_ingest_project_adf_description
FAILED tests/knowledge/test_ingestors_coverage.py::TestJiraIngestor::test_ingest_project_includes_comments
FAILED tests/knowledge/test_ingestors_coverage.py::TestJiraIngestor::test_ingest_project_with_jql_extra
## 01 13 failed, 8260 passed, 115 skipped, 211 deselected in 1546.88s (0:25:46)
FAILED tests/api/test_enterprise_coverage_boost.py::test_run_eval_suite_success
FAILED tests/api/test_enterprise_coverage_boost.py::test_run_eval_suite_failure_is_recorded
FAILED tests/e2e/test_governance_e2e.py::test_audit_trail_records_all_tool_calls
FAILED tests/infra/test_docker_compose.py::test_dev_worker_concurrency_fits_its_memory_limit
FAILED tests/ingestion/test_repository_security.py::test_repository_source_resolves_once_and_pins_validated_ip
FAILED tests/intelligence/test_golden_dataset_versions.py::test_a_run_records_the_dataset_version_and_runs_that_versions_tasks
FAILED tests/knowledge/test_ingestors_extra2.py::TestConfluenceIngestor::test_fetch_pages_makes_request
FAILED tests/knowledge/test_ingestors_extra2.py::TestConfluenceIngestor::test_ingest_space_happy_path
FAILED tests/knowledge/test_ingestors_extra2.py::TestConfluenceIngestor::test_ingest_space_skips_short_pages
FAILED tests/knowledge/test_ingestors_extra2.py::TestJiraIngestor::test_ingest_project_happy_path
FAILED tests/knowledge/test_ingestors_extra2.py::TestJiraIngestor::test_ingest_project_with_adf_description
FAILED tests/knowledge/test_ingestors_extra2.py::TestJiraIngestor::test_ingest_project_with_adf_comment
FAILED tests/scaling/test_worker_rpa_executor.py::test_worker_goal_rpa_open_url_dispatches_to_the_rpa_executor
## 02 1 failed, 6998 passed, 79 skipped, 179 deselected, 8 errors in 1392.56s (0:23:12)
ERROR tests/mcp/test_builtin_credentials_guard.py::test_no_platform_credential_leaves_on_a_tenant_call[tenant-creds]
ERROR tests/mcp/test_builtin_credentials_guard.py::test_no_platform_credential_leaves_on_a_tenant_call[no-creds]
ERROR tests/mcp/test_builtin_credentials_guard.py::test_tenant_credentials_reach_the_vendor_request
ERROR tests/mcp/test_builtin_credentials_guard.py::test_aws_builtin_refuses_without_tenant_keys
ERROR tests/mcp/test_builtin_credentials_guard.py::test_client_dispatches_tenant_dsn_host_checked[postgresql://tenant:pw@8.8.8.8:5432/tenant_db-True]
ERROR tests/mcp/test_builtin_credentials_guard.py::test_client_dispatches_tenant_dsn_host_checked[postgresql://tenant:pw@10.0.0.5:5432/platform_db-False]
ERROR tests/mcp/test_builtin_credentials_guard.py::test_client_dispatches_tenant_dsn_host_checked[postgresql://tenant:pw@8.8.8.8:5432,127.0.0.1:5432/db-False]
ERROR tests/mcp/test_builtin_credentials_guard.py::test_client_refuses_connector_without_credentials
FAILED tests/enterprise/test_eval_suite_run_binding.py::test_run_against_an_unknown_agent_is_404
## 03 1 failed, 6618 passed, 83 skipped, 230 deselected, 2 errors in 1326.26s (0:22:06)
ERROR tests/rag/test_rag_e2e.py::test_retrieve_colbert_reranks_by_query_relevance
ERROR tests/rag/test_rag_e2e.py::test_retrieve_raptor_returns_summary_plus_detail
FAILED tests/enterprise/test_enterprise_intelligence_gaps.py::test_get_suite_results_returns_persisted_runs
EXIT 0
 Test Files  1 failed | 436 passed (437)
      Tests  1 failed | 5032 passed (5033)
 FAIL  src/features/org/OrgPage.test.tsx > OrgPage — no organization selected > renders a placeholder when orgId is missing

```

## Handoff 2026-10-06 (usage limit)
- origin/main = 077303128 (pushed, green). Local main = P8b merged (fast-forward of fix/p8-guardrail-gaps): NOT yet full-suite tested or pushed — run the 4-chunk suite (see /private/tmp/claude-501/vsuite/run.sh pattern) + vitest, then push.
- Running when cut off: live P1c (A5 MongoDB/Redis/Elasticsearch) on branch live/p1c-nosql (worktree .claude/worktrees/p1c) — resume, `git merge main`, finish, merge, test, push.
- Next per LIVE-E2E-PLAN.md "Re-ordering": A10 HTTP/crawl → A12 agent-generated → B1 → B2 → B7 → live re-checks P2/P4/P5/P7/P8 → P6 → P9 → P10 → P11 → deferred A7, B3, B8, C1–C5, A6.
- New findings: p8b.progress.json (raw exception text with bearer token in worker log), p478code.progress.json.
