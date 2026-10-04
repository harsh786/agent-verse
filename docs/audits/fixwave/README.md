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

## Resume procedure
1. Stabilize main (fix suite failures), merge the two g08 branches, rerun suite, push.
2. Resume each in-flight package from its worktree: commit/finish uncommitted work, rebase on main,
   continue remaining items of its scope, update its progress file.
3. Merge finished packages in batches; full suite green; push; redeploy; live baseline.
