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
