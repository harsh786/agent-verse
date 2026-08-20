# Platform Hardening — Complete 4-Phase Execution Plan

> **Status:** Master planning document for 4-phase platform hardening of AgentVerse.
> **Date:** 2026-08-20
> **Target Coverage:** Backend ≥85% · Frontend ≥80% · 0 type errors · 0 lint errors · 0 a11y violations

---

## Overview

The Platform Hardening initiative is a **systematic, phased approach** to harden AgentVerse's core systems through comprehensive testing, code fixes, and production-grade quality assurance.

**4 phases × 25 feature components (FCs) = Full product coverage**

| Phase | Focus | FCs | Backend/Frontend | Target |
|-------|-------|-----|------------------|--------|
| **Phase 1** | Connectivity & Data | FC-01..FC-07 | 95% Backend | Async ops, MCP, Knowledge, RAG, Voice |
| **Phase 2** | Reliability & Governance | FC-08..FC-13 | 85% Backend | Agent loop, Audit, Cost, Policies |
| **Phase 3** | Enterprise & Safety | FC-14..FC-19 | 85% Backend | HITL, Compliance, Red Team, Guardrails |
| **Phase 4** | Frontend UX Integration | FC-20..FC-25 | 80% Frontend | Dashboard, Builder, Observability, Chat |

---

## Phase 1: Connectivity & Data ✅ (Reference: `2026-08-20-platform-hardening-phase-1.md`)

**Focus:** Hardening all data ingestion, embedding, MCP, knowledge, and RAG layers.

### Feature Components (FC-01..FC-07)
- **FC-01:** MCP Registry & Tool Discovery
- **FC-02:** Embedding Engine (Voyage, OpenAI)
- **FC-03:** Knowledge Store (Hybrid Search)
- **FC-04:** Knowledge Graph (Neo4j-like)
- **FC-05:** RAG Pipeline (Semantic + BM25)
- **FC-06:** Voice Input (Whisper)
- **FC-07:** Chat Streaming (SSE + WebSocket)

### Key Deliverables
- Unit tests for all embedding, search, and RAG functions
- Integration tests with mocked MCP servers (testcontainers)
- E2E tests for voice input → text → embedding pipeline
- Chat streaming verified with SSE mock server
- Backend coverage: ≥95% on `app/mcp/`, `app/knowledge/`, `app/embedding/`, `app/rag/`

### Gate Criteria
- [ ] All 7 FCs have ≥95% coverage
- [ ] 0 mypy errors · 0 ruff errors
- [ ] No regressions from existing Phase 1 tests
- [ ] Phase 1 spec doc (`docs/superpowers/specs/platform-hardening/phase-1-connectivity-and-data.md`) committed

---

## Phase 2: Reliability & Governance ✅ (Reference: `2026-08-20-platform-hardening-phase-2.md`)

**Focus:** Hardening agent execution loop, audit trail, cost tracking, and policy enforcement.

### Feature Components (FC-08..FC-13)
- **FC-08:** Agent Loop Initialization
- **FC-09:** LLM Provider Abstraction
- **FC-10:** Agent Execution (Step + Verify)
- **FC-11:** Audit Trail (Append-only)
- **FC-12:** Cost Calculation (Per-token, Per-goal)
- **FC-13:** Policy Enforcement (Rate limit, Quota)

### Key Deliverables
- Unit tests for agent loop state machine (init → plan → execute → verify → complete/replan)
- LLM provider mock for deterministic testing
- Audit log verification (immutability, ordering)
- Cost tracking with Redis backend + quota enforcement
- Policy engine with rule matching and rate limiter
- Backend coverage: ≥85% on `app/agent/`, `app/governance/audit.py`, `app/governance/cost.py`, `app/governance/policies.py`

### Gate Criteria
- [ ] All 6 FCs have ≥85% coverage
- [ ] Agent loop passes 50+ scenarios (happy path, errors, retries, timeouts)
- [ ] Cost tracking verified with multi-currency support
- [ ] Policy enforcement blocks at-risk operations (detected via keywords)
- [ ] 0 mypy errors · 0 ruff errors
- [ ] Phase 2 spec doc committed

---

## Phase 3: Enterprise & Safety ✅ (Reference: `2026-08-20-platform-hardening-phase-3.md`)

**Focus:** HITL approvals, compliance checks, red team testing, simulation sandbox, and guardrails.

### Feature Components (FC-14..FC-19)
- **FC-14:** HITL Frontend Integration (Approval Queue UI + SSE Events)
- **FC-15:** Compliance Engine (GDPR, SOC2, PCI checks)
- **FC-16:** Red Teaming (Jailbreak detection)
- **FC-17:** Simulation & Sandbox (Mock tools, no real calls)
- **FC-18:** Cost Quota Dashboard (Budget bar, reset date)
- **FC-19:** Guardrails v2 (Input/output validation, injection prevention)

### Key Deliverables
- **FC-14:** Approval queue component + HITL event SSE stream integration
- **FC-15:** Compliance checks on every goal (blocking violations, reporting)
- **FC-16:** Jailbreak detector with prompt escape, SQL injection patterns
- **FC-17:** Simulation engine that mocks all external calls (no real data mutation)
- **FC-18:** Cost quota UI with color-coded progress bar + reset countdown
- **FC-19:** Input/output validation with comprehensive test coverage
- Backend coverage: ≥85% on `app/governance/hitl.py`, `app/enterprise/compliance.py`, `app/enterprise/red_team.py`, `app/enterprise/simulation.py`, `app/guardrails_v2/`
- Frontend component coverage: ≥80% on approval queue, cost quota dashboard

### Gate Criteria
- [ ] All 6 FCs have ≥85% backend / ≥80% frontend coverage
- [ ] HITL approval flow end-to-end (pause → SSE event → user approval → resume)
- [ ] Compliance violations block goal execution
- [ ] Red team patterns reliably detected
- [ ] Simulation runs without real effects
- [ ] Cost quota enforced + UI reflects changes in real-time
- [ ] Guardrails prevent top injection/XSS patterns
- [ ] 0 mypy errors · 0 ruff errors
- [ ] Phase 3 spec doc committed

---

## Phase 4: Frontend UX Integration (Reference: `2026-08-20-platform-hardening-phase-4.md`)

**Focus:** Comprehensive frontend component testing, E2E flows, and accessibility audits.

### Feature Components (FC-20..FC-25)
- **FC-20:** Dashboard & Org Settings (Stats cards, refresh polling, error states)
- **FC-21:** Agent Builder (Form validation, config creation, error feedback)
- **FC-22:** Observability Viewer (Trace list, filtering, span expansion, timing)
- **FC-23:** Chat & Goals Pages (Timeline rendering, SSE integration, keyboard shortcuts)
- **FC-24:** Settings Management (API key CRUD, masking, revocation)
- **FC-25:** E2E Critical Flows (Full user journeys, mobile viewport, keyboard navigation)

### Key Deliverables
- **FC-20:** Dashboard stats component + polling hook + skeleton loaders + error boundaries
- **FC-21:** Agent config form with field validation + async submission + toast notifications
- **FC-22:** Trace viewer with operation filtering, span hierarchy, duration display
- **FC-23:** Goal timeline + chat message thread with SSE real-time updates
- **FC-24:** API key manager with generation, revocation, copy-to-clipboard, masking
- **FC-25:** Playwright E2E tests for: auth → agent create → goal submit → approval → chat
- Frontend coverage: ≥80% on all 6 features
- E2E critical flows: 4+ flows passing
- Accessibility: 0 axe violations on critical pages

### Gate Criteria
- [ ] All 6 FCs have ≥80% frontend coverage
- [ ] 0 tsc errors · 0 eslint errors
- [ ] E2E critical flows all passing (auth, agent, approval, chat, mobile)
- [ ] Accessibility audits (axe-core) show 0 violations
- [ ] No regressions from Phase 1-3
- [ ] Phase 4 spec doc committed

---

## Execution Model

Each phase follows this **TDD loop:**

```
1. Deep-read (read actual source code, identify gaps)
   ↓
2. Spec gap table (document all findings)
   ↓
3. Write failing test (red)
   ↓
4. Fix code to pass (green)
   ↓
5. Refactor for quality (clean)
   ↓
6. Phase gate (coverage ≥X%, type/lint clean, no regressions)
   ↓
7. Commit + merge
```

### Tools & Workflows
- **Backend testing:** pytest-asyncio, mock/monkeypatch, testcontainers (Docker)
- **Frontend testing:** Vitest, @testing-library/react, MSW, Playwright, axe-core
- **Code quality:** ruff (lint), mypy (types), eslint (frontend), tsc (types)
- **Git workflow:** One branch per phase (`feature/phase-X-...`), squash merge to main

### Quality Gates (All Phases)

| Metric | Threshold | Tool |
|--------|-----------|------|
| Backend coverage | ≥85% | pytest --cov |
| Frontend coverage | ≥80% | vitest --coverage |
| Type errors | 0 | mypy (backend) / tsc (frontend) |
| Lint errors | 0 | ruff (backend) / eslint (frontend) |
| a11y violations | 0 | axe-core |
| E2E regressions | 0 | Playwright |

---

## Timeline & Sequencing

**Assumption:** Each phase takes ~5-7 days (1-2 weeks per phase with breaks).

```
Week 1 (Aug 20-26):   Phase 1 Setup + Gap Analysis → Phase 1 Execution
Week 2 (Aug 27-Sep 2): Phase 1 Gate → Phase 2 Execution → Phase 2 Gate
Week 3 (Sep 3-9):      Phase 3 Execution → Phase 3 Gate
Week 4 (Sep 10-16):    Phase 4 Execution → Phase 4 Gate
```

**Milestone markers:**
- [ ] Phase 1 gate passed (Sept 2)
- [ ] Phase 2 gate passed (Sept 9)
- [ ] Phase 3 gate passed (Sept 16)
- [ ] Phase 4 gate passed (Sept 23)
- [ ] **All phases merged to main** (Sept 23)

---

## How to Use This Plan

### For Agentic Workers (Copilot / Subagents)

1. **Load the appropriate phase plan** from `/docs/superpowers/plans/`:
   - `2026-08-20-platform-hardening-phase-1.md` (for Phase 1)
   - `2026-08-20-platform-hardening-phase-2.md` (for Phase 2)
   - `2026-08-20-platform-hardening-phase-3.md` (for Phase 3)
   - `2026-08-20-platform-hardening-phase-4.md` (for Phase 4)

2. **Use the `executing-plans` skill** to run tasks with checkboxes.

3. **Follow TDD strictly:**
   - Write test first (red)
   - Implement to pass (green)
   - Refactor if needed (clean)
   - Commit with clear message

4. **Run phase gate** at the end of each phase before merging.

### For Humans

1. **Review the master plan** (this document) to understand scope.

2. **Review the phase spec** to see what gap analysis found.

3. **Monitor progress** via commit history and coverage reports.

4. **Approve merges** when phase gates pass.

5. **Use the phase plans as reference** for what work is in-flight.

---

## Post-Hardening: Production Readiness

Once all 4 phases pass their gates:

1. **Merge all phases to main** (one commit per phase, squash)
2. **Update release notes** with coverage metrics
3. **Tag release** (e.g., `v1.0.0-hardened`)
4. **Run production stage test suite** (no changes, just verify CI passes)
5. **Deploy to staging** and run 72-hour observation window
6. **Collect metrics:** error rates, performance, user feedback
7. **Deploy to production** with feature flag for gradual rollout

---

## Troubleshooting Guide

### Coverage Below Threshold
- [ ] Identify uncovered files: `coverage report --skip-covered`
- [ ] Write more tests for branches (use decision tree testing)
- [ ] Run coverage with `--cov-fail-under=X` to enforce minimum

### Type Errors
- [ ] Run `mypy app --show-error-codes` to see error categories
- [ ] Fix obvious issues first (missing imports, wrong types)
- [ ] Use type: ignore only as last resort + leave TODO

### Lint Errors
- [ ] Run `ruff check --fix` to auto-fix common issues
- [ ] Run `ruff check` to see remaining violations
- [ ] Refactor code to pass, don't disable rules

### Test Flakiness
- [ ] Add explicit waits: `waitFor(() => expect(...).toBeVisible())`
- [ ] Increase timeout for slow operations
- [ ] Mock time-dependent behavior (dates, randomness)
- [ ] Run flaky test 10x to confirm it passes consistently

### E2E Failures
- [ ] Check Playwright report: `npx playwright show-report`
- [ ] Use `--debug` mode to step through failure
- [ ] Update selectors if DOM changed
- [ ] Add explicit waits for async operations

---

## Success Metrics (All Phases Complete)

- [ ] **25 feature components** all have deep-read specs
- [ ] **Backend coverage** ≥85% on all packages (Phases 1-3)
- [ ] **Frontend coverage** ≥80% on all features (Phase 4)
- [ ] **Type safety** 0 mypy errors, 0 tsc errors
- [ ] **Code quality** 0 ruff errors, 0 eslint errors
- [ ] **Accessibility** 0 axe violations on critical pages
- [ ] **E2E flows** 4+ critical user journeys passing consistently
- [ ] **Production readiness** All CI checks green, no flaky tests
- [ ] **Documentation** All phase specs + this master plan committed

---

## Related Documents

- [Phase 1: Connectivity & Data](./2026-08-20-platform-hardening-phase-1.md)
- [Phase 2: Reliability & Governance](./2026-08-20-platform-hardening-phase-2.md)
- [Phase 3: Enterprise & Safety](./2026-08-20-platform-hardening-phase-3.md)
- [Phase 4: Frontend UX](./2026-08-20-platform-hardening-phase-4.md)
- [CLAUDE.md](../../CLAUDE.md) — Backend architecture
- [AGENTS.md](../../AGENTS.md) — Frontend structure

---

**Status:** Ready for Phase 1 execution. Load phase plan when ready. 🚀
