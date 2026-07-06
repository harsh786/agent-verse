# AgentVerse Playwright Live Test Plan
**Date:** 2026-07-06  
**Author:** Test Strategy Architect  
**Based on:** Direct analysis of `agent-verse-frontend/e2e/` (68 spec files), `playwright.config.ts`, and all feature page source code

---

## How to Read This Document

Each test entry specifies:
- **Test name** — the `test.describe` / `test` label
- **Playwright project** — which `playwright.config.ts` project runs it
- **Steps** — precise Playwright interaction sequence
- **Mock vs live backend** — whether `page.route()` intercepts are used
- **Needs running backend** — whether a real `uvicorn` instance is required

**Auth pattern (all authenticated tests):**  
The helper at `e2e/helpers/auth.ts` pre-seeds `localStorage` with `apiKey` and `tenantId` before navigation. No login form interaction needed.

---

## 1. Authentication

### 1.1 Auth page renders correctly

| Field | Value |
|-------|-------|
| Test name | `Auth / renders login form with API key input` |
| Playwright project | `smoke-live` |
| Steps | 1. Navigate to `/auth`; 2. Assert heading "Sign in" visible; 3. Assert `input[type=password]` present; 4. Assert "Sign in" button visible |
| Mock vs live | Mock — `page.route('**/tenants/me', ...)` not needed for render |
| Needs running backend | No |
| File | `e2e/auth.spec.ts` (existing) |

### 1.2 Successful API key login redirects to dashboard

| Field | Value |
|-------|-------|
| Test name | `Auth / valid API key redirects to /dashboard` |
| Playwright project | `smoke-live` |
| Steps | 1. Navigate to `/auth`; 2. Fill API key input; 3. Mock `GET /tenants/me` → 200 `{tenant_id, name, plan}`; 4. Click "Sign in"; 5. Assert URL `/dashboard` |
| Mock vs live | Mock (`page.route`) |
| Needs running backend | No |
| File | `e2e/auth.spec.ts` (existing) |

### 1.3 Invalid API key shows error

| Field | Value |
|-------|-------|
| Test name | `Auth / invalid API key shows error message` |
| Playwright project | `failure-states` |
| Steps | 1. Navigate to `/auth`; 2. Mock `GET /tenants/me` → 401; 3. Fill invalid key; 4. Click "Sign in"; 5. Assert error text visible |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/auth.spec.ts` (existing) |

### 1.4 MFA verify page flow

| Field | Value |
|-------|-------|
| Test name | `MFA / TOTP code entry advances to dashboard` |
| Playwright project | `security-smoke` |
| Steps | 1. Navigate to `/auth/mfa`; 2. Mock `POST /auth/mfa/verify` → 200; 3. Fill 6-digit code; 4. Click "Verify"; 5. Assert redirect |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/mfa.spec.ts` (existing) |

---

## 2. Goals — Critical Path

### 2.1 Submit goal and see it in list

| Field | Value |
|-------|-------|
| Test name | `Goals / submitting a goal adds it to the list` |
| Playwright project | `smoke-live` |
| Steps | 1. Auth setup; 2. Navigate to `/goals`; 3. Mock `GET /goals` → `{goals: []}`; 4. Mock `POST /goals` → `{goal_id: 'g-001', status: 'planning'}`; 5. Mock second `GET /goals` → `{goals: [{id:'g-001', goal:'test', status:'planning', created_at: now}]}`; 6. Type goal text; 7. Click "Submit"; 8. Assert row with "planning" badge visible |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/goals.spec.ts` (existing) |

### 2.2 Goal detail page shows live SSE events

| Field | Value |
|-------|-------|
| Test name | `Goal Detail / live events appear in execution tab via SSE` |
| Playwright project | `full-live` |
| Steps | 1. Auth setup; 2. Mock `GET /goals/g-001` → goal object with `status: 'executing'`; 3. Mock `GET /goals/g-001/stream` (SSE) → event sequence `goal_started` → `plan_ready` → `step_started` → `tool_call_complete`; 4. Navigate to `/goals/g-001`; 5. Assert "Pipeline steps" heading; 6. Assert "● Live" indicator; 7. Assert `tool_call_complete` event row visible |
| Mock vs live | Mock (SSE via `page.route` with `ReadableStream` fulfillment) |
| Needs running backend | No |
| File | `e2e/goal-lifecycle.spec.ts` (existing) |

### 2.3 HITL approval on waiting goal

| Field | Value |
|-------|-------|
| Test name | `Goal Detail / HITL approval panel shown for waiting_human status` |
| Playwright project | `governance-live` |
| Steps | 1. Auth setup; 2. Mock `GET /goals/g-hitl` → `{status:'waiting_human'}`; 3. Mock `GET /governance/approvals` → `[{request_id:'r-001', goal_id:'g-hitl', status:'pending', risk_level:'high', action:'deploy to prod'}]`; 4. Navigate to `/goals/g-hitl`; 5. Assert "Human approval required" panel; 6. Assert risk level "high" displayed; 7. Mock `POST /governance/approvals/r-001/approve` → 200; 8. Click "Approve"; 9. Assert approval call made |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/approvals.spec.ts` (existing) |

### 2.4 Goal search and filter round-trip

| Field | Value |
|-------|-------|
| Test name | `Goals / search by text filters visible rows` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Mock 10 goals with mixed statuses; 3. Navigate `/goals`; 4. Type "deploy" in search; 5. Assert URL has `q=deploy`; 6. Assert only matching rows visible; 7. Click "failed" filter pill; 8. Assert URL has `status=failed`; 9. Clear search button → all rows return |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/goals.spec.ts` (existing) |

### 2.5 Eval scorecard displays after goal completion

| Field | Value |
|-------|-------|
| Test name | `Goal Detail / eval scorecard renders 7 dimensions` |
| Playwright project | `eval-regression` |
| Steps | 1. Auth; 2. Mock complete goal; 3. Mock `GET /goals/g-001/evaluation` → `{average_score:0.82, passed:true, scores:{task_completion:0.9, efficiency:0.85, ...}}`; 4. Navigate to `/goals/g-001`; 5. Click "Eval" tab; 6. Assert "82%" overall score; 7. Assert "✓ PASSED" badge; 8. Assert 7 progress bars rendered |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/eval-scorecard.spec.ts` (existing) |

### 2.6 Live goal with real backend (provider-gated)

| Field | Value |
|-------|-------|
| Test name | `Goals Live / submit real goal and observe SSE events` |
| Playwright project | `provider-live` |
| Steps | 1. Real auth against running backend; 2. Navigate `/goals`; 3. Submit "Say hello and list your tools"; 4. Follow redirect to goal detail; 5. Wait for `● Live` indicator; 6. Wait up to 60s for `goal_complete` event; 7. Assert status badge shows "complete" |
| Mock vs live | **Live backend** |
| Needs running backend | **Yes** — `uvicorn`, Postgres, Redis, Celery worker |
| Gating env var | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| File | `e2e/goal-lifecycle.spec.ts` (extend with live section) |

---

## 3. Agents

### 3.1 Agent list loads and NL creation works

| Field | Value |
|-------|-------|
| Test name | `Agents / NL creation modal submits and adds agent to list` |
| Playwright project | `smoke-live` |
| Steps | 1. Auth; 2. Mock `GET /agents` → `[]`; 3. Navigate `/agents`; 4. Assert empty state "Deploy your first agent"; 5. Click "Deploy New Agent"; 6. Fill NL description; 7. Mock `POST /agents/nl` → `{agent_id:'a-001', name:'GitHub Agent'}`; 8. Mock second `GET /agents` → `[{agent_id:'a-001',...}]`; 9. Click "Deploy Agent"; 10. Assert modal closes; 11. Assert "GitHub Agent" row visible |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/agents.spec.ts` (existing) |

### 3.2 Manual agent creation form

| Field | Value |
|-------|-------|
| Test name | `Agent Create / manual form creates agent with all fields` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/agents/create`; 3. Click "Manual Configuration" tab; 4. Fill name, select `fully-autonomous` autonomy mode; 5. Fill goal template; 6. Mock `POST /agents` → `{agent_id:'a-002'}`; 7. Click "Create Agent"; 8. Assert navigation to `/agents/a-002` |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/agents.spec.ts` (existing) |

### 3.3 Delete agent with confirm modal

| Field | Value |
|-------|-------|
| Test name | `Agents / delete agent shows confirm modal and removes from list` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Mock 1 agent; 3. Navigate `/agents`; 4. Click "Delete"; 5. Assert ConfirmModal with agent name; 6. Mock `DELETE /agents/a-001` → 200; 7. Click "Delete" in modal; 8. Assert agent removed from list |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/agents.spec.ts` (existing) |

---

## 4. Memory Explorer

### 4.1 Semantic recall returns results

| Field | Value |
|-------|-------|
| Test name | `Memory / semantic recall search returns confidence-ranked results` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/memory`; 3. Mock `POST /memory/recall` → `[{content:'Deploy via kubectl', memory_type:'skill', confidence:0.93, source:'goal-abc'}]`; 4. Fill recall input "deployment"; 5. Click "Recall"; 6. Assert result row with "93%" confidence bar; 7. Assert type badge "skill" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/memory.spec.ts` (existing) |

### 4.2 Add memory modal creates entry

| Field | Value |
|-------|-------|
| Test name | `Memory / add memory modal creates and shows new entry` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/memory`; 3. Mock `GET /memory` → `{items:[], total:0}`; 4. Click "Add" button; 5. Fill content; 6. Set type to "fact"; 7. Set confidence slider to 70; 8. Mock `POST /memory` → `{id:'m-001'}`; 9. Click "Create Memory"; 10. Assert modal closes; 11. Assert `qc.invalidateQueries` triggers list refresh |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/memory.spec.ts` (existing) |

---

## 5. Knowledge / RAG

### 5.1 Create collection and ingest document

| Field | Value |
|-------|-------|
| Test name | `Knowledge / create collection and ingest text content` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/knowledge`; 3. Mock `GET /knowledge/collections` → `[]`; 4. Click "New Collection"; 5. Fill name "test-docs"; 6. Mock `POST /knowledge/collections` → `{collection_id:'col-001'}`; 7. Click "Create"; 8. Click "Ingest" tab; 9. Select collection; 10. Select "Text" source type; 11. Paste content; 12. Mock `POST /knowledge/ingest` → `{chunks_created:3}`; 13. Click "Ingest"; 14. Assert toast "3 chunks indexed" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/knowledge.spec.ts` (existing) |

### 5.2 Ask AI returns RAG answer with citations

| Field | Value |
|-------|-------|
| Test name | `Knowledge / Ask AI returns answer with citation sources` |
| Playwright project | `rag-live` |
| Steps | 1. Auth; 2. Navigate `/knowledge`; 3. Click "Ask AI" tab; 4. Mock `POST /knowledge/chat` → `{answer:'Based on docs...', citations:[{index:1, excerpt:'Deploy via kubectl', score:0.91, source_url:'https://...'}], chunks_retrieved:3}`; 5. Type question; 6. Click "Ask"; 7. Assert answer text visible; 8. Assert `data-testid="citations-panel"` contains `[1]` citation; 9. Assert confidence score "91%" |
| Mock vs live | Mock (use `rag-live` project for live variant) |
| Needs running backend | No (mock) / Yes (rag-live project) |
| File | `e2e/knowledge-rag.spec.ts` (existing) |

### 5.3 Search with keyword highlight

| Field | Value |
|-------|-------|
| Test name | `Knowledge / search tab highlights query words in results` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/knowledge`; 3. Click "Search" tab; 4. Type "kubectl deployment"; 5. Mock `GET /knowledge/search?q=kubectl+deployment&top_k=10` → results; 6. Click "Search"; 7. Assert `<mark>` elements in result content containing "kubectl" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/knowledge.spec.ts` (existing) |

---

## 6. Governance

### 6.1 Create and simulate policy

| Field | Value |
|-------|-------|
| Test name | `Governance / create policy and run simulator` |
| Playwright project | `governance-live` |
| Steps | 1. Auth; 2. Navigate `/governance`; 3. Policies tab open; 4. Click "New Policy"; 5. Fill name "block-shell"; 6. Fill pattern "shell:*"; 7. Select action "deny"; 8. Mock `POST /governance/policies` → `{policy_id:'p-001'}`; 9. Click "Save Policy"; 10. Assert new policy row; 11. Click "Simulate"; 12. Fill "shell:execute"; 13. Mock `POST /governance/simulate` → `{simulation_results:{'shell:execute':'DENIED'}}`; 14. Click "Run Simulation"; 15. Assert "DENIED" badge |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/governance.spec.ts` (existing) |

### 6.2 Emergency Stop and clear

| Field | Value |
|-------|-------|
| Test name | `Governance / emergency stop halts all goals` |
| Playwright project | `security-smoke` |
| Steps | 1. Auth; 2. Navigate `/governance`; 3. Click `data-testid="emergency-stop-btn"`; 4. Assert "Halt all agent execution?" confirm row; 5. Mock `POST /governance/emergency-stop` → `{cancelled_goals:3, rejected_approvals:1}`; 6. Click "Confirm Stop"; 7. Assert `data-testid="emergency-banner"` visible; 8. Assert "3 goals cancelled · 1 approvals rejected"; 9. Mock `POST /governance/emergency-stop/clear` → 200; 10. Click "Clear Emergency Stop"; 11. Assert banner gone |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/governance.spec.ts` (existing) |

### 6.3 Audit log filter and export

| Field | Value |
|-------|-------|
| Test name | `Governance / audit log filters by goal_id and exports JSON` |
| Playwright project | `governance-live` |
| Steps | 1. Auth; 2. Navigate `/governance`, click "Audit" tab; 3. Fill `audit-goal-id` input with "g-test"; 4. Mock `GET /audit?goal_id=g-test&limit=100` → `[{event_id:'e-001', goal_id:'g-test', action_level:'allow', tool_name:'github:list_repos'}]`; 5. Click `data-testid="apply-audit-filters"`; 6. Assert event row visible; 7. Click "JSON" export button; 8. Assert download initiated |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/audit-export.spec.ts` (existing) |

### 6.4 Batch approval

| Field | Value |
|-------|-------|
| Test name | `Governance / batch approve selected requests` |
| Playwright project | `governance-live` |
| Steps | 1. Auth; 2. Navigate `/governance/approvals`; 3. Mock 3 pending approvals; 4. Check first 2 approval cards; 5. Assert `data-testid="batch-toolbar"` shows "2 selected"; 6. Mock `POST /governance/approvals/batch` → `{approved:2, not_found:0}`; 7. Click "Batch Approve"; 8. Assert toast "2 approved" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/approvals.spec.ts` (existing) |

---

## 7. Observability

### 7.1 Health overview shows dependency status

| Field | Value |
|-------|-------|
| Test name | `Observability / system health displays dependency grid` |
| Playwright project | `observability-live` |
| Steps | 1. Auth; 2. Navigate `/observability`; 3. Mock `GET /health` → `{status:'healthy', checks:{postgres:{status:'up',latency_ms:2}, redis:{status:'up'}, celery:{status:'degraded'}}}`; 4. Assert `data-testid="deps-grid"` has 3 cards; 5. Assert Celery card has amber "degraded" status |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/observability.observability.spec.ts` (existing) |

### 7.2 Metrics charts render with Prometheus data

| Field | Value |
|-------|-------|
| Test name | `Observability / metrics tab renders Recharts charts` |
| Playwright project | `observability-live` |
| Steps | 1. Auth; 2. Navigate `/observability`; 3. Click "Metrics" tab; 4. Mock `GET /metrics` → Prometheus text with `agentverse_goal_success_total 0.87`; 5. Assert "87.0%" KPI card for "Success Rate"; 6. Assert Recharts SVG `<rect>` elements (bar chart) rendered |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/observability.observability.spec.ts` (existing) |

### 7.3 Logs tab streams live entries

| Field | Value |
|-------|-------|
| Test name | `Observability / logs tab shows live SSE entries` |
| Playwright project | `observability-live` |
| Steps | 1. Auth; 2. Navigate `/observability`; 3. Click "Logs" tab; 4. Mock `GET /observability/logs/stream` (SSE) → `{id:'log-1', level:'info', message:'Goal started', timestamp:...}`; 5. Assert log entry row appears; 6. Assert `[INFO]` badge visible; 7. Hover over log panel; 8. Assert "Paused" indicator |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/observability.observability.spec.ts` (existing) |

---

## 8. Workflow Builder

### 8.1 Drag node and connect, then save

| Field | Value |
|-------|-------|
| Test name | `Workflow Builder / drag trigger node, add tool node, connect and save` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/workflow-builder`; 3. Drag "Trigger / Start" from palette to canvas; 4. Drag "Tool Call" from palette; 5. Connect trigger to tool call node (click source handle, drag to target handle); 6. Fill workflow name; 7. Mock `POST /workflows` → `{id:'wf-001'}`; 8. Click "Save"; 9. Assert toast "Workflow saved" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/workflow-builder.spec.ts` (existing) |

### 8.2 Load template and validate

| Field | Value |
|-------|-------|
| Test name | `Workflow Builder / load Incident Response template and pass validation` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/workflow-builder`; 3. Click "⚡ Templates" button; 4. Click "Incident Response" card; 5. Assert 5 nodes on canvas; 6. Click "✓ Validate"; 7. Assert toast "Workflow is valid" |
| Mock vs live | No API needed for template load |
| Needs running backend | No |
| File | `e2e/workflow-builder.spec.ts` (existing) |

### 8.3 NL generation from goal description

| Field | Value |
|-------|-------|
| Test name | `Workflow Builder / NL generation creates nodes from goal text` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/workflow-builder`; 3. Fill NL textarea "Monitor GitHub issues and create Jira tickets"; 4. Mock `POST /workflows/generate` → `{nodes:[...3 nodes...], edges:[...2 edges...]}`; 5. Click "Generate"; 6. Assert 3 nodes on canvas; 7. Assert toast "Generated 3 nodes" |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/workflow-builder.spec.ts` (existing) |

---

## 9. Settings

### 9.1 LLM provider configuration

| Field | Value |
|-------|-------|
| Test name | `Settings / LLM provider tab saves new config` |
| Playwright project | `full-live` |
| Steps | 1. Auth; 2. Navigate `/settings?tab=llm`; 3. Mock `GET /tenants/me/llm` → `{provider:'openai',default_model:'gpt-4o'}`; 4. Click "Edit"; 5. Change provider to "anthropic"; 6. Fill model "claude-opus-4-5"; 7. Fill API key; 8. Mock `PUT /tenants/me/llm` → 200; 9. Mock `PUT /tenants/me/llm-config` → 200; 10. Click "Save"; 11. Assert "anthropic" / "claude-opus-4-5" displayed |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/settings.spec.ts` (existing) |

### 9.2 API key creation shows one-time reveal

| Field | Value |
|-------|-------|
| Test name | `Settings / create API key shows raw key once` |
| Playwright project | `security-smoke` |
| Steps | 1. Auth; 2. Navigate `/settings?tab=apikeys`; 3. Click "+ New Key"; 4. Fill name "production"; 5. Mock `POST /tenants/me/keys` → `{key_id:'k-001', raw_key:'av_live_abc123...', name:'production'}`; 6. Click "Create"; 7. Assert "Key created — copy it now" banner visible; 8. Assert raw key value in `<code>` element; 9. Click copy button; 10. Click "Dismiss"; 11. Assert banner gone |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/settings.spec.ts` (existing) |

### 9.3 MFA setup toggle

| Field | Value |
|-------|-------|
| Test name | `Settings / security tab MFA section renders` |
| Playwright project | `security-smoke` |
| Steps | 1. Auth; 2. Navigate `/settings?tab=security`; 3. Assert "Two-Factor Authentication" heading; 4. Assert MFASettings component renders |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/mfa.spec.ts` (existing) |

---

## 10. Onboarding Flow

### 10.1 Full 4-step wizard completion

| Field | Value |
|-------|-------|
| Test name | `Onboarding / completes 4-step wizard and redirects to dashboard` |
| Playwright project | `smoke-live` |
| Steps | 1. Navigate to `/onboarding` (no auth pre-seeded); 2. Step 1: Fill OpenAI key; 3. Mock `POST /settings/llm` → 200; 4. Click "Save & Continue"; 5. Assert green checkmark on step 1; 6. Step 2: Click "Skip for now"; 7. Step 3: Fill agent description; 8. Mock `POST /agents/nl` → `{agent_id:'a-001'}`; 9. Click "Create Agent"; 10. Step 4: Keep default goal; 11. Mock `POST /goals` → `{goal_id:'g-001'}`; 12. Click "Run Goal"; 13. Click "Go to Dashboard"; 14. Assert URL `/dashboard` |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/app.smoke.spec.ts` (extend) |

---

## 11. Navigation & Routing

### 11.1 All top-level routes load without error

| Field | Value |
|-------|-------|
| Test name | `Navigation / all primary sidebar routes render without console errors` |
| Playwright project | `smoke-live` |
| Steps | For each route in `['/dashboard', '/goals', '/agents', '/memory', '/knowledge', '/governance', '/observability', '/workflow-builder', '/settings']`: 1. Navigate; 2. Assert no error boundary message; 3. Assert `<h1>` or page heading present |
| Mock vs live | Mock all API calls via wildcard `page.route('**/api/**', ...)` returning empty success responses |
| Needs running backend | No |
| File | `e2e/missing-routes.spec.ts` (existing) |

### 11.2 404 page for unknown routes

| Field | Value |
|-------|-------|
| Test name | `Navigation / unknown route shows NotFoundPage` |
| Playwright project | `smoke-live` |
| Steps | 1. Auth; 2. Navigate `/this-does-not-exist`; 3. Assert NotFoundPage content visible; 4. Assert back navigation link present |
| Mock vs live | No API needed |
| Needs running backend | No |
| File | `e2e/navigation.spec.ts` (existing) |

---

## 12. Accessibility

### 12.1 Goal detail tabs keyboard navigation

| Field | Value |
|-------|-------|
| Test name | `Accessibility / goal detail tab bar supports arrow key navigation` |
| Playwright project | `accessibility` |
| Steps | 1. Auth; 2. Navigate to goal with result artifact; 3. Tab-key focus to first tab; 4. Press `ArrowRight`; 5. Assert second tab has focus and is selected; 6. Press `End`; 7. Assert last tab has focus; 8. Press `Home`; 9. Assert first tab active |
| Mock vs live | Mock goal with `result_artifact` |
| Needs running backend | No |
| File | `e2e/accessibility.a11y.spec.ts` (existing) |

### 12.2 Axe scan on primary pages

| Field | Value |
|-------|-------|
| Test name | `Accessibility / Goals list page passes axe audit` |
| Playwright project | `accessibility` |
| Steps | 1. Auth; 2. Mock goals list; 3. Navigate `/goals`; 4. Run `await new AxeBuilder({page}).analyze()`; 5. Assert `violations` array empty |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/accessibility.a11y.spec.ts` (existing, extend) |

---

## 13. Failure States

### 13.1 Backend down — goals page error state

| Field | Value |
|-------|-------|
| Test name | `Failure States / goals list shows error when backend unreachable` |
| Playwright project | `failure-states` |
| Steps | 1. Auth; 2. Mock `GET /goals` → network error (abort); 3. Navigate `/goals`; 4. Assert error message visible (once error state bug #1 from UX audit is fixed); 5. Assert "Retry" button present |
| Mock vs live | Mock (abort) |
| Needs running backend | No |
| File | `e2e/failure-states.failure.spec.ts` (existing, extend) |

### 13.2 RouteErrorBoundary catches page-level crash

| Field | Value |
|-------|-------|
| Test name | `Failure States / crashed page shows RouteErrorBoundary without losing navbar` |
| Playwright project | `failure-states` |
| Steps | 1. Auth; 2. Mock a route that causes component throw; 3. Navigate to that route; 4. Assert error boundary message visible; 5. Assert sidebar navigation still functional; 6. Navigate to another route; 7. Assert normal page renders |
| Mock vs live | Mock (route that returns malformed JSON causing parse error) |
| Needs running backend | No |
| File | `e2e/failure-states.failure.spec.ts` (existing) |

---

## 14. Security

### 14.1 Unauthenticated access redirects to /auth

| Field | Value |
|-------|-------|
| Test name | `Security / unauthenticated access to /goals redirects to /auth` |
| Playwright project | `security-smoke` |
| Steps | 1. Clear localStorage (no auth); 2. Navigate to `/goals`; 3. Assert URL redirected to `/auth` |
| Mock vs live | No API needed |
| Needs running backend | No |
| File | `e2e/security.security.spec.ts` (existing) |

### 14.2 Expired API key forces logout

| Field | Value |
|-------|-------|
| Test name | `Security / expired API key triggers session invalidation and logout` |
| Playwright project | `security-smoke` |
| Steps | 1. Auth with expired key; 2. Mock `GET /tenants/me` → 401; 3. App mounts `RequireAuth`; 4. Assert localStorage cleared; 5. Assert redirected to `/auth` |
| Mock vs live | Mock |
| Needs running backend | No |
| File | `e2e/security.security.spec.ts` (existing) |

---

## Summary Table

| Feature | Smoke | Full | Mobile | A11y | Security | Failure | Provider | Needs Backend |
|---------|-------|------|--------|------|----------|---------|----------|---------------|
| Auth | ✅ | ✅ | — | — | ✅ | ✅ | — | No (mock) |
| Goals | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (live) | Live only |
| Agents | ✅ | ✅ | ✅ | — | — | — | — | No |
| Memory | — | ✅ | — | — | — | ✅ | — | No |
| Knowledge | — | ✅ | — | — | — | — | ✅ (rag) | RAG live |
| Governance | — | ✅ | — | — | ✅ | — | — | No |
| Observability | — | ✅ | — | — | — | ✅ | — | No |
| Workflow Builder | — | ✅ | — | — | — | — | — | No |
| Settings | — | ✅ | — | — | ✅ | — | — | No |
| Onboarding | ✅ | ✅ | ✅ | — | — | — | — | No |
| Navigation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | No |

---

## `page.route()` Mock Patterns Reference

```typescript
// Standard empty success for feature flags
await page.route('**/goals', route => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ goals: [] })
}));

// SSE event stream mock
await page.route('**/goals/g-001/stream', async route => {
  const events = [
    'data: {"type":"goal_started","status":"executing"}\n\n',
    'data: {"type":"plan_ready","steps":["step1","step2"]}\n\n',
    'data: {"type":"goal_complete","status":"complete"}\n\n',
  ];
  await route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body: events.join(''),
  });
});

// Network failure simulation
await page.route('**/goals', route => route.abort('failed'));
```
