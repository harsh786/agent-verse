# AgentVerse Production Completion Spec

**Date:** 2026-06-26  
**Goal:** Complete every partially-implemented feature to production scale with full E2E test coverage.

---

## Summary of Gaps Identified

| Area | Gap | Fix |
|---|---|---|
| GoalDetailPage | HITL approve/reject buttons have no onClick handlers | Wire to `GET /governance/approvals` + `POST /approvals/{id}/approve\|reject` |
| DashboardPage | Avg Latency and Cost Today hardcoded | Add `GET /goals/metrics` endpoint; wire KPI cards |
| Red-team runner | Heuristic detection (string length) | Run actual payloads through GuardrailChecker |
| Simulation runner | Stub ("simulated steps") | Execute agent with FakeProvider + mock tool responses |
| Eval runner | Rule-based scoring only | Add per-goal eval API; wire eval to goal completion |
| Self-optimizer | Deterministic stubs; not triggered by real runs | Wire to eval completion; generate targeted suggestions |
| RPA execution | Metadata only; no execution endpoint | Add `POST /rpa/execute` with Playwright + fallback |
| Grafana dashboards | Provisioning dirs exist; no JSON | Add 3 dashboards: goals, LLM cost, tool calls |
| K8s manifests | Exist but unverified | Audit and complete all manifests |
| Frontend tests | Limited coverage | Vitest tests for every page component |
| Backend tests | Missing coverage for new endpoints | pytest tests for every new endpoint |

---

## Sprint 1: Core Execution Integrity

### 1a. `GET /goals/metrics` (backend)

New endpoint returning:
```json
{
  "active_goals": 3,
  "total_goals": 42,
  "success_rate": 0.81,
  "avg_latency_ms": 4200,
  "cost_today_usd": 1.23,
  "goals_today": 10
}
```

Computed from in-memory `GoalService._goals` dict (no external metrics required; cost from `CostController`).

### 1b. HITL wiring in GoalDetailPage (frontend)

When `goal.status === "waiting_human"`:
1. Query `GET /governance/approvals` (refetch every 5s)
2. Find the approval matching `goal_id`
3. Approve button calls `POST /governance/approvals/{request_id}/approve` with `{ approver: tenantId, note }`
4. Reject button calls `POST /governance/approvals/{request_id}/reject`
5. On success: invalidate goal query

### 1c. Dashboard KPI cards (frontend)

Wire `Avg Latency` and `Cost Today` to real `GET /goals/metrics` data.

---

## Sprint 2: Intelligence Completeness

### 2a. Real red-team runner (backend)

`RedTeamRunner.run()` now:
1. Instantiates `GuardrailChecker`
2. For each adversarial case, calls `guardrail_checker.check(payload)` 
3. Returns `passed` if guardrails blocked it, `failed` if it slipped through
4. Also tests structured injection patterns

### 2b. Real simulation runner (backend)

`SimulationRunner.start()` now:
1. Creates a `FakeProvider` that returns mock tool results from `mock_tools` dict
2. Runs a mini agent loop (plan → execute with mocks → verify)
3. Returns actual steps executed with mock outputs
4. Tracks cost estimate based on step count

### 2c. Eval scorecard endpoint (backend + frontend)

- Add `GET /goals/{goal_id}/eval` endpoint
- Wire eval to goal completion: after `goal_complete` event, run `EvalRunner.score(state)`, store result on app.state
- EvalPage: add "Eval Scorer" section that queries goals, lets user pick one, shows 5-dimension radar chart
- Add self-optimizer suggestions section to EvalPage

### 2d. Self-optimizer wiring

When a goal completes with eval score < 0.7, automatically call `SelfOptimizer.analyze_and_suggest()` and store suggestions. Wire the "Apply/Reject" buttons in EvalPage to `POST /intelligence/suggestions/{id}/apply|reject`.

---

## Sprint 3: RPA Execution

### 3a. `POST /rpa/execute` (backend)

```json
Request: { "session_id": "...", "tool_name": "rpa_open_url", "arguments": {"url": "..."} }
Response: { "success": true, "output": "...", "artifact_url": null, "duration_ms": 320 }
```

- When Playwright is installed: real browser execution
- Fallback: simulated execution with deterministic output
- High-risk tools (`rpa_click`, `rpa_type`) require governance approval in supervised mode
- Screenshots stored as base64 in response

### 3b. `GET /rpa/sessions` + `POST /rpa/sessions` (backend)

Session management for multi-step RPA workflows.

---

## Sprint 4: Infrastructure

### 4a. Grafana dashboard JSONs

Three dashboards in `infra/grafana/provisioning/dashboards/`:
- `goals.json` — goal submission rate, success rate, p95 latency, active goals gauge
- `llm-cost.json` — cost counter by scope, token usage by provider/model  
- `tool-calls.json` — tool call rate, error rate, duration p95 by tool category

### 4b. K8s manifests audit

Verify all manifests in `infra/k8s/` are complete and reference correct image tags, resource limits, and secret names.

---

## Test Strategy

### Backend (pytest)

Every new/modified endpoint gets:
- Happy path test
- Auth failure test (no API key → 401)
- Not found test (404)
- Edge case (empty data, invalid input)

New test files:
- `tests/api/test_goals_metrics.py` — metrics endpoint
- `tests/api/test_rpa_execute.py` — RPA execution
- `tests/enterprise/test_real_simulation.py` — real simulation
- `tests/enterprise/test_real_red_team.py` — real red-team
- `tests/intelligence/test_eval_endpoint.py` — eval scoring

### Frontend (Vitest)

New/updated test files:
- `src/features/goals/GoalDetailPage.test.tsx` — HITL approval flow
- `src/features/dashboard/DashboardPage.test.tsx` — real KPI rendering
- `src/features/eval/EvalPage.test.tsx` — eval scoring, simulation, red-team
- `src/features/governance/GovernancePage.test.tsx` — full tab coverage
- `src/features/enterprise/EnterprisePage.test.tsx` — export/delete/residency
- `src/features/marketplace/MarketplacePage.test.tsx` — browse + deploy
- `src/features/observability/ObservabilityPage.test.tsx` — health + metrics
- `src/features/agents/AgentsListPage.test.tsx` — list + create
- `src/features/connectors/ConnectorsCatalogPage.test.tsx` — catalog browse
- `src/features/knowledge/KnowledgePage.test.tsx` — collection CRUD + search
- `src/features/schedules/SchedulesPage.test.tsx` — existing (may need expansion)
- `src/features/settings/SettingsPage.test.tsx` — LLM provider config

### Playwright E2E

`tests/e2e/` (frontend):
- `auth.spec.ts` — login flow
- `goals.spec.ts` — submit → wait → cancel
- `hitl.spec.ts` — approve/reject flow
- `governance.spec.ts` — policy + budget
