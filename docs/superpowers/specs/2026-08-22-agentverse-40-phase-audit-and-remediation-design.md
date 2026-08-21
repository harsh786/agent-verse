# AgentVerse 40-Phase Audit and Remediation Design

**Date:** 2026-08-22  
**Status:** Approved design, pending written-spec review  
**Scope:** `agent-verse-backend/`, `agent-verse-frontend/`, and `agent-verse-github-action/` only  
**Excluded:** `agent-verse-sdk-python/`, `agent-verse-sdk-typescript/`

## 1. Objective

Produce an audit-first, runtime-verified assessment of AgentVerse against the requested 40-phase
architecture breakdown and the feature-family checklist, then generate an ordered remediation
program that can be executed phase-by-phase without collapsing roadmap gaps, broken code paths,
and secret-blocked external dependencies into the same bucket.

The audit must not equate file presence with feature completion. A phase is only considered
implemented when its code path, tests, and local executable flow can all be traced successfully.
Anything less is classified as partial or missing, with secret-dependent external paths called out
explicitly as blocked by secrets rather than overstated.

## 2. Goals

- Audit the repository against all 40 requested architecture phases.
- Produce a second crosswalk that maps every requested feature family to backend files,
  frontend files, tests, runtime path, and owning phases.
- Verify the current baseline by actually running backend, frontend, Playwright, and GitHub
  Action paths where local infrastructure allows.
- Attempt to bring up all local infrastructure required for meaningful runtime verification.
- Generate all 40 follow-up phase plans in the same order supplied by the user.
- Bake backend, frontend, integration, functional, and Playwright testing expectations into the
  later remediation plans.
- Track a final program target of 95%+ backend coverage and 95%+ frontend coverage.

## 3. Non-Goals

- Implementing fixes during the audit/design stage.
- Counting external SaaS flows as implemented when local verification depends on unavailable
  credentials, tenants, or third-party accounts.
- Auditing either SDK package.
- Reordering the user’s 40-phase sequence based on engineering preference.
- Treating mocks, unit tests, or stale documentation alone as production evidence.

## 4. Scope and Decomposition

This work is intentionally decomposed into three sub-projects:

1. **Audit and evidence collection** — inspect code, tests, docs, runtime behavior, and local
   execution paths.
2. **Planning output** — write the master audit report, feature-family crosswalk, and all 40
   ordered remediation plans.
3. **Implementation execution** — later, after audit approval, execute Phase 1 through Phase 40
   in the exact user-specified order.

The current design covers sub-projects 1 and 2. Sub-project 3 will begin only after the written
spec is approved and the implementation-planning handoff is complete.

## 5. Chosen Decisions

| Decision Area | Selected Rule |
|---|---|
| Audit mode | Audit-first |
| Phase scope | Full target-state audit against all 40 phases |
| Completion rubric | Runtime-verified only |
| Secret-dependent paths | Try all local infra; do not count secret-blocked paths as implemented |
| Deliverable structure | Dual matrix: 40-phase matrix + feature-family crosswalk |
| Repo scope | Backend + frontend + GitHub Action only |
| Post-audit ordering | Exact 40-phase order supplied by the user |
| Audit output | Report + all 40 phase plans |
| Testing policy | Backend, frontend, integration, functional, and Playwright coverage expected |
| Mocking policy | Mocks allowed everywhere, including Playwright |
| Coverage enforcement | Per-phase touched-area gates + final repo-wide 95%+ backend and 95%+ frontend |
| Audit verification style | Run full existing suites where feasible during audit |

## 6. Evidence Model

Each audited phase will be judged through three evidence layers.

### 6.1 Source evidence
Map the requested capability to real implementation boundaries:
- backend packages, modules, routes, workers, models, migrations, and service wiring
- frontend pages, components, transport hooks, stores, and route surfaces
- GitHub Action inputs, entrypoint logic, output contract, and local execution path

### 6.2 Test evidence
Inspect and run relevant tests across these layers:
- backend unit tests
- backend integration tests
- backend functional/runtime tests
- frontend unit/component tests
- frontend integration tests
- Playwright E2E tests
- GitHub Action executable-path verification and any test coverage that exists

### 6.3 Runtime evidence
Attempt to execute the path locally where possible:
- app startup and lifespan wiring
- DB/Redis-backed service upgrade paths
- queue, SSE, WebSocket, and worker flows where feasible
- frontend build and test paths
- Playwright flows against local runtime if needed
- GitHub Action against a local backend target

## 7. Classification Rubric

Each 40-phase item will be classified as one of:

- **Implemented** — code path exists, tests are meaningful, and a local end-to-end or equivalent
  executable path is verifiably working.
- **Partial** — some implementation exists, but runtime proof, integration proof, completeness,
  or stability is missing.
- **Missing** — no meaningful implementation path exists in the repo for the requested phase.

Secret- or SaaS-dependent limitations are not a fourth status in the matrix. They are recorded as
explicit evidence notes such as **blocked by secrets** or **blocked by external dependency**, and
those phases still remain partial or missing unless local verification is sufficient.

## 8. Audit Workflow

### 8.1 Environment bring-up
Attempt the local environment required for runtime verification:
- use `uv` for backend Python commands
- start `colima` if required
- use `docker-compose` for local infrastructure
- bring up at least Postgres and Redis
- prepare integration-test environment variables when required
- verify frontend dependencies and Playwright environment

### 8.2 Baseline verification
Establish the current state before making any claims:
- backend lint, type-check, test, and coverage baseline
- frontend lint, type-check, test, and coverage baseline
- frontend Playwright baseline
- GitHub Action executable-path baseline
- major collection errors, flaky suites, dead tests, and blocked external paths

### 8.3 Evidence mapping
For every phase and every requested feature family:
- identify the owning code paths
- identify current tests
- run executable verification where feasible
- record exact gaps, blockers, and confidence notes

### 8.4 Planning output
After classification is complete:
- write one master audit report
- write one feature-family crosswalk
- write all 40 ordered remediation plans
- preserve exact phase ordering from the user’s architecture list

## 9. Deliverables

### 9.1 Master audit report
A single report containing the 40-phase matrix with:
- phase name
- requested capability summary
- repo evidence
- runtime evidence
- test evidence
- blockers
- final status
- follow-up remediation pointer

### 9.2 Feature-family crosswalk
A second report mapping each requested feature to:
- backend files
- frontend files
- GitHub Action relevance
- existing tests
- missing test layers
- runtime path
- owning phase(s)
- final status and gap notes

### 9.3 Verification baseline appendix
A reproducible snapshot of:
- commands run
- infra state reached
- backend/frontend/action results
- coverage baselines
- blocked-by-secrets notes

### 9.4 Forty ordered remediation plans
One plan per requested architecture phase, all written up front, all ordered exactly from Phase 1
through Phase 40.

## 10. Testing and Coverage Policy

The later remediation program must enforce both of these rules:

1. **Per-phase gate:** every touched area gets the appropriate backend/frontend/integration/
   functional/Playwright test layers.
2. **Final repo-wide gate:** backend coverage must reach 95%+ and frontend coverage must reach
   95%+ before the overall program is considered complete.

Mocks are allowed everywhere, including Playwright, because that was the user’s selected policy.
That choice affects how tests may be written during remediation, but it does not lower the audit’s
runtime-verification standard for classifying a feature as implemented.

## 11. Error Handling and Risk Boundaries

- Dirty-worktree files unrelated to this effort must not be swept into spec or planning commits.
- Existing docs may be used as hints, but every important claim must be checked against code and
  runtime evidence.
- Secret-dependent connectors and provider flows must be reported honestly rather than assumed.
- “Not built” and “built but broken” must remain separate findings.
- Coverage targets are program-level goals, not grounds to inflate current audit classifications.

## 12. Acceptance Criteria for the Audit Stage

The audit stage is complete only when all of the following are true:

- local infrastructure bring-up has been attempted and documented
- backend, frontend, Playwright, and GitHub Action baselines have been captured
- all 40 architecture phases have evidence-backed classifications
- the feature-family crosswalk covers the full requested checklist
- coverage baselines are recorded for backend and frontend
- blocked-by-secrets and blocked-by-external-dependency items are explicitly called out
- all 40 ordered remediation plans are written

## 13. Handoff Boundary

Once the written spec is approved, the next step is not direct coding. The next step is to create
formal implementation plans from this design, then execute them phase-by-phase in the user’s exact
Phase 1 → Phase 40 order with verification at every phase boundary.
