You are re-certifying part of the AgentVerse monorepo (repo root /Users/harsh/Documents/Learning/agent-verse,
backend in agent-verse-backend/, Python via `uv run`). This is an in-depth, evidence-driven,
honest re-certification after fix wave 7 (7A HITL/e-stop/security, 7B correctness, 7C fake-success,
7D LLM-guard/SSRF/ingest) was merged to `main`. The scale bar is world-class production: millions of
documents/goals, multiple API replicas + Celery workers, Postgres as source of truth, RLS tenant isolation.

YOUR GROUP: {GROUP}  — areas: {AREAS}
OUTPUT FILE: docs/audits/2026-09-29-recert/{GROUP}.json   (shape: {"group": "...", "code": "<git short HEAD>", "features": [...]})
Per-feature schema is in docs/audits/2026-09-29-recert/README.md.

Inputs: docs/audits/2026-09-29-reaudit/certification-matrix.json — take every feature whose "area" is in
your areas. Each has previous status, evidence, open_gaps, new_defects and tests. HANDOFF.md in that folder
describes what wave 7 claims to have fixed.

For EACH feature, in order:
1. Re-verify EVERY open_gap and new_defect against the CURRENT code. Read the code at the cited location
   (lines may have moved; find the symbol). Classify each as verified_fixed (cite the file:line that now
   makes it correct, and the commit if you can find it with `git log -S`/`git log -L`) or still_open (cite the
   current file:line). Do not accept a fix because a commit message says so — read the code path end to end,
   including the Celery worker path (app/scaling/tasks.py) and the multi-replica path, not only the API path.
2. Hunt for NEW defects in the feature, especially ones wave 7 may have introduced (fail-open vs fail-closed,
   swallowed exceptions reporting success, per-process state that must be shared across replicas, unbounded
   scans/memory, missing tenant scoping/RLS, missing auth/role checks, SSRF, uncharged LLM calls).
   Only report defects you can point at with file:line and a concrete failure scenario.
3. Run the feature's targeted tests (`cd agent-verse-backend && uv run pytest <paths> -q --no-cov -p no:cacheprovider -m "not slow"`).
   Record command → result. If a test is missing for a real-world use case, say so in still_open.
   If something needs infra you cannot get (Docker not up, library absent), mark that item BLOCKED with the
   exact reason — never pretend it passed.
4. Assign status: PASS only if no gap of medium+ severity remains and the behaviour is proven by a test or a
   direct probe; PARTIAL if it works with open gaps; FAIL if the core behaviour is broken or fakes success;
   NOT_IMPLEMENTED if absent/stubbed (honest 501 counts as NOT_IMPLEMENTED, not PASS).
5. IMMEDIATELY rewrite the output file with all features done so far (valid JSON) before starting the next
   feature. A usage cut-off must lose at most one feature. If the output file already has features, skip them.

Rules: READ-ONLY for app code and tests — do not edit, fix, or commit anything; the only file you write is your
output file (plus scratch files under /private/tmp). Do not run the whole test suite. Do not start/stop Docker
or colima. Use at most one helper sub-agent. Keep evidence concise (one or two sentences per item).
Never print or write secrets/API keys.

Final reply (≤15 lines): per-feature status table (previous → new), counts of verified_fixed / still_open /
new_defects, and the 3 most severe new defects.
