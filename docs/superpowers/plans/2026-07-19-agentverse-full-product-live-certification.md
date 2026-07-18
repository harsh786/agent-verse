# AgentVerse Full-Product Live Certification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an evidence-backed live certification of every AgentVerse product area, all implemented agent/RAG/safety patterns, all 57 frontend routes, and representative scenarios for all 37 documented domains.

**Architecture:** Execute a layered certification against the current working copy because that copy contains the user-supplied live test and configured `.env`. Keep static, isolated, integrated, and live results separate; diagnose failures at the narrowest layer and rerun after minimal fixes. Persist run-tagged Jira records and never run Jira delete/cleanup tests.

**Tech Stack:** Python 3.12/uv, pytest, FastAPI, LangGraph, PostgreSQL 16/pgvector, Redis, Celery, Docker Compose/Colima, React 19, TypeScript, Vite, Vitest, Playwright, OpenAI, Jira REST API, Prometheus, Grafana, Loki, Jaeger, OpenTelemetry.

---

## File Map

- Create `agent-verse-backend/tests/real_e2e/test_all_domains_real_openai.py`: parameterized live model certification for all 37 domain scenarios.
- Create `docs/testing/AVCERT-20260719-003221-full-product-certification-report.md`: final human-readable evidence, defects, live records, and verdict.
- Use test-generated artifacts under `agent-verse-frontend/playwright-report/`, `agent-verse-frontend/test-results/`, and coverage directories.
- Preserve `agent-verse-backend/tests/real_e2e/test_truly_live_everything.py` as user-owned; invoke safe test nodes individually and exclude its delete/cleanup nodes.
- Modify product/test files only when a reproduced certification blocker has a narrow, reviewed fix.

### Task 1: Establish the Certification Baseline

**Files:**
- Reference: `docs/superpowers/specs/2026-07-19-agentverse-full-product-live-certification-design.md`
- Reference: `agent-verse-backend/.env`

- [ ] **Step 1: Generate the run identity in the session**

Use `AVCERT-20260719-003221` for all subsequent commands, prompts, Jira summaries, and reports.

- [ ] **Step 2: Capture the non-secret baseline**

Run from the repository root:

```bash
git status --short
git rev-parse HEAD
/Users/harsh.kumar01/.local/bin/uv --version
node --version
npm --version
docker version --format '{{.Server.Version}}'
docker-compose version
```

Expected: versions print successfully; dirty paths are recorded and preserved.

- [ ] **Step 3: Validate credential availability without printing values**

Check `.env` for non-empty `OPENAI_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `JIRA_BASE_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` using boolean output only.

Expected: all six certification dependencies report `configured`.

### Task 2: Restore and Certify Infrastructure

**Files:**
- Reference: `agent-verse-backend/infra/docker-compose.yml`
- Reference: `agent-verse-backend/infra/otel-collector-config.yml`
- Reference: `agent-verse-backend/infra/prometheus.yml`

- [ ] **Step 1: Inspect unhealthy services before mutation**

Run:

```bash
docker-compose -f agent-verse-backend/infra/docker-compose.yml ps
docker-compose -f agent-verse-backend/infra/docker-compose.yml logs --tail=200 keycloak jaeger otel-collector
```

Expected: root-cause evidence for unhealthy/restarting services.

- [ ] **Step 2: Start the complete stack without deleting volumes**

Run:

```bash
docker-compose -f agent-verse-backend/infra/docker-compose.yml up -d postgres redis pgbouncer keycloak-db keycloak minio mailpit searxng jaeger otel-collector prometheus grafana loki backend worker beat frontend
```

Expected: services start; no `down -v`, volume deletion, or database reset occurs.

- [ ] **Step 3: Apply and verify migrations**

Run from `agent-verse-backend`:

```bash
/Users/harsh.kumar01/.local/bin/uv run alembic upgrade head
/Users/harsh.kumar01/.local/bin/uv run alembic current
```

Expected: current revision equals the migration head.

- [ ] **Step 4: Verify service functionality**

Check API health, frontend HTTP response, Postgres `vector` extension, Redis `PING`, MinIO health, SearXNG health, Prometheus readiness, Grafana health, Loki readiness, Jaeger API, and OpenTelemetry Collector metrics.

Expected: each service has an explicit pass/fail result; container health alone is insufficient.

### Task 3: Backend Static and Isolated Quality Gates

**Files:**
- Reference: `agent-verse-backend/pyproject.toml`
- Test: `agent-verse-backend/tests/`

- [ ] **Step 1: Synchronize the locked backend environment**

Run from `agent-verse-backend`:

```bash
/Users/harsh.kumar01/.local/bin/uv sync --locked
```

Expected: dependencies resolve from `uv.lock` with Python 3.12.

- [ ] **Step 2: Run lint**

```bash
/Users/harsh.kumar01/.local/bin/uv run ruff check .
```

Expected: exit 0.

- [ ] **Step 3: Run strict typing**

```bash
/Users/harsh.kumar01/.local/bin/uv run mypy app
```

Expected: exit 0 with no type errors.

- [ ] **Step 4: Run the non-slow backend suite**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest -m 'not slow and not integration and not real_openai' --no-cov -q
```

Expected: zero unexplained failures, errors, warnings-as-errors, or collection failures.

- [ ] **Step 5: Run coverage once after functional stability**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest -m 'not slow and not real_openai' --cov=app --cov-report=term-missing
```

Expected: suite passes and actual coverage is recorded; no unsupported percentage claim is inferred.

### Task 4: Backend Integration, Security, and Persistence Gates

**Files:**
- Test: `agent-verse-backend/tests/integration/`
- Test: `agent-verse-backend/tests/db/`
- Test: `agent-verse-backend/tests/tenancy/`
- Test: `agent-verse-backend/tests/security/`
- Test: `agent-verse-backend/tests/governance/`
- Test: `agent-verse-backend/tests/reliability/`
- Test: `agent-verse-backend/tests/scaling/`

- [ ] **Step 1: Run integration-marked tests against Colima**

```bash
DOCKER_HOST='unix:///Users/harsh.kumar01/.colima/default/docker.sock' TESTCONTAINERS_RYUK_DISABLED=true /Users/harsh.kumar01/.local/bin/uv run pytest -m integration --no-cov -q
```

Expected: real container-backed integration tests pass.

- [ ] **Step 2: Run isolation and governance areas explicitly**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/db tests/tenancy tests/security tests/governance tests/reliability tests/scaling --no-cov -q
```

Expected: RLS, tenant boundaries, auth, policies, HITL, cost, audit, retry, rollback, circuit-breaker, bulkhead, and queue-routing assertions pass.

- [ ] **Step 3: Run backend E2E suites excluding live providers**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/e2e -m 'not slow and not real_openai' --no-cov -q
```

Expected: API, agent graph, governance, RAG, RPA, tenancy, infrastructure, and multi-agent E2E tests pass.

### Task 5: Frontend and SDK Quality Gates

**Files:**
- Reference: `agent-verse-frontend/package.json`
- Reference: `agent-verse-frontend/playwright.config.ts`
- Test: `agent-verse-frontend/src/**/*.test.*`
- Test: `agent-verse-frontend/e2e/*.spec.ts`
- Test: `agent-verse-sdk-python/tests/`
- Test: `agent-verse-sdk-typescript/tests/`
- Reference: `agent-verse-github-action/Dockerfile`

- [ ] **Step 1: Install locked frontend dependencies**

Run from `agent-verse-frontend`:

```bash
npm ci
```

Expected: installation matches `package-lock.json`.

- [ ] **Step 2: Run frontend lint, typing, build, and component tests**

```bash
npm run lint
npm run typecheck
npm run build
npm run test
```

Expected: all four commands exit 0.

- [ ] **Step 3: Run Python SDK tests**

Run from `agent-verse-sdk-python`:

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest -q
```

Expected: exit 0.

- [ ] **Step 4: Run TypeScript SDK build/tests**

Run from `agent-verse-sdk-typescript`:

```bash
npm ci
npm run build
npm test
```

Expected: exit 0.

- [ ] **Step 5: Build and smoke-test the GitHub Action**

Run from `agent-verse-github-action` using the Dockerfile and a deliberately unreachable local API URL.

Expected: image builds; entrypoint starts and returns the documented bounded connection error without leaking a token.

### Task 6: Real OpenAI Agent and RAG Pattern Suites

**Files:**
- Test: `agent-verse-backend/tests/real_e2e/test_agent_patterns_real_openai.py`
- Test: `agent-verse-backend/tests/real_e2e/test_full_pipeline_real_world.py`
- Test: `agent-verse-backend/tests/real_e2e/test_rag_patterns_real_openai.py`

- [ ] **Step 1: Run each committed real-provider module separately**

Run from `agent-verse-backend`:

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_agent_patterns_real_openai.py -v -s --no-cov
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_rag_patterns_real_openai.py -v -s --no-cov
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_full_pipeline_real_world.py -v -s --no-cov
```

Expected: 40 committed real-provider tests produce attributable outcomes.

- [ ] **Step 2: Verify assertions test behavior, not only non-empty output**

Inspect failures and passing assertions for terminal lifecycle, feature activation, retrieved evidence, pattern metadata, and domain correctness.

Expected: weak assertions are listed as evidence-quality gaps rather than silently treated as certification.

### Task 7: Truly Live Postgres, Redis, Embedding, and Jira Suite

**Files:**
- Test: `agent-verse-backend/tests/real_e2e/test_truly_live_everything.py`

- [ ] **Step 1: Run all safe non-Jira nodes**

Invoke the B1, B2, B3, and B5 test nodes explicitly. Do not invoke `test_zzz_cleanup`.

Expected: real embedding/vector retrieval, DB knowledge, Redis checkpointing, all RAG patterns, and complete agent pipelines pass.

- [ ] **Step 2: Run read-only Jira discovery/search nodes**

Invoke `test_b4_jira_list_projects` and `test_b4_jira_search_via_post`.

Expected: configured Jira responds and searches can locate the certification tag.

- [ ] **Step 3: Replace the destructive Jira node with persistent creation evidence**

Do not invoke `test_b4_jira_create_fetch_delete`. Use the same authenticated Jira REST boundary to create a run-tagged issue, fetch it, add a run-tagged comment, search it, and retain it.

Expected: issue key, URL, fetch status, and comment verification are recorded; no delete request is sent.

- [ ] **Step 4: Run autonomous Jira-agent execution**

Invoke `test_b5_full_pipeline_jira_goal_real_agent` only after verifying its plan and tool path contains no delete/cleanup action.

Expected: autonomous execution reaches a terminal state and its created Jira evidence remains present.

### Task 8: Add and Run All 37 Domain Scenarios

**Files:**
- Create: `agent-verse-backend/tests/real_e2e/test_all_domains_real_openai.py`
- Test: `agent-verse-backend/tests/real_e2e/test_all_domains_real_openai.py`

- [ ] **Step 1: Write the failing collection test**

Create a parameterized module containing a frozen `DomainScenario` with `slug`, `goal`, `required_terms`, and `forbidden_claims`. Define all 37 scenarios from section 6 of the approved design and assert:

```python
def test_domain_matrix_is_complete() -> None:
    assert len(DOMAIN_SCENARIOS) == 37
    assert len({scenario.slug for scenario in DOMAIN_SCENARIOS}) == 37
    assert all(scenario.goal.strip() for scenario in DOMAIN_SCENARIOS)
    assert all(scenario.required_terms for scenario in DOMAIN_SCENARIOS)
```

- [ ] **Step 2: Run the matrix-completeness test and verify it fails before the matrix exists**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_all_domains_real_openai.py::test_domain_matrix_is_complete -q --no-cov
```

Expected: FAIL because `DOMAIN_SCENARIOS` is missing or incomplete.

- [ ] **Step 3: Implement the 37-scenario matrix and live runner**

Use `OpenAICompatibleProvider(default_model='gpt-4o-mini')`, `AgentGraph`, an enterprise `TenantContext`, `asyncio.wait_for(..., 240)`, and one test parameter per scenario. Require a terminal status, at least 150 output characters, one domain-required term, no explicit forbidden fabricated claim, and no hidden chain-of-thought marker.

- [ ] **Step 4: Run matrix validation, then the live scenarios**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_all_domains_real_openai.py::test_domain_matrix_is_complete -q --no-cov
/Users/harsh.kumar01/.local/bin/uv run pytest tests/real_e2e/test_all_domains_real_openai.py -m real_openai -v -s --no-cov
```

Expected: the static matrix check passes and all 37 live cases have explicit results.

- [ ] **Step 5: Commit the scenario harness only after verification**

```bash
git add agent-verse-backend/tests/real_e2e/test_all_domains_real_openai.py
git commit -m 'test(real-e2e): certify all documented domains'
```

### Task 9: Hallucination, Guardrail, Eval, and Recovery Matrix

**Files:**
- Test: `agent-verse-backend/tests/agent/test_hallucination_fixes.py`
- Test: `agent-verse-backend/tests/agent/test_grounding.py`
- Test: `agent-verse-backend/tests/guardrails/`
- Test: `agent-verse-backend/tests/evals/`
- Test: `agent-verse-backend/tests/enterprise/test_real_red_team*.py`
- Test: `agent-verse-backend/tests/recovery/`
- Test: `agent-verse-backend/tests/reliability/`

- [ ] **Step 1: Run the focused safety and evaluation suites**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/agent/test_hallucination_fixes.py tests/agent/test_grounding.py tests/guardrails tests/evals tests/recovery tests/reliability -q --no-cov
```

Expected: grounding, citation, safety scoring, refusal, redaction, retry, rollback, and recovery checks pass.

- [ ] **Step 2: Run real red-team tests separately**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/enterprise/test_real_red_team.py tests/enterprise/test_real_red_team_behavioral.py -v -s --no-cov
```

Expected: adversarial prompts produce explicit blocked/allowed decisions with no secret leakage.

- [ ] **Step 3: Run eval regression and golden-dataset API tests**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/evals tests/api/test_golden_datasets.py tests/api/test_goals_eval.py tests/enterprise/test_eval_suite_api.py -q --no-cov
```

Expected: scorecards, golden datasets, regression gates, and API results pass.

### Task 10: Frontend Playwright and Browser Certification

**Files:**
- Test: `agent-verse-frontend/e2e/*.spec.ts`
- Reference: `agent-verse-frontend/src/app/App.tsx`

- [ ] **Step 1: Run every configured Playwright project once**

Run from `agent-verse-frontend`:

```bash
npx playwright test --project=full-live
npx playwright test --project=mobile
npx playwright test --project=accessibility
npx playwright test --project=security-smoke
npx playwright test --project=failure-states
npx playwright test --project=eval-regression
npx playwright test --project=rag-live
npx playwright test --project=governance-live
npx playwright test --project=observability-live
npx playwright test --project=provider-live
npx playwright test --project=multimodal-live
```

Expected: every spec has an attributable result; duplicate project execution is reported separately from unique-test coverage.

- [ ] **Step 2: Verify all 57 routes against the live frontend**

Use the browser runtime to load each route declared in `src/app/App.tsx`, authenticate where required, and record route crash, console error, failed request, navigation, and accessibility state.

Expected: every route has a pass/fail result and critical failures have screenshots.

- [ ] **Step 3: Run live journeys through the UI**

Exercise login, onboarding, agent creation, goal submission, SSE execution, HITL, knowledge ingestion/search/chat, eval scorecard, observability, Jira connector, workflow builder, schedules, collaboration/WebSocket, memory, artifacts, and training export.

Expected: visible UI state agrees with backend/database/audit evidence.

### Task 11: Observability, Resilience, and Recovery

**Files:**
- Test: `agent-verse-backend/tests/observability/`
- Test: `agent-verse-backend/tests/qos/`
- Test: `agent-verse-backend/tests/scaling/`
- Reference: `agent-verse-backend/infra/`

- [ ] **Step 1: Validate metrics, logs, and traces by run ID**

Query Prometheus, Loki, Jaeger, and backend observability endpoints for the run ID.

Expected: request, goal, model/tool, queue, cost, latency, and failure evidence can be correlated without secret leakage.

- [ ] **Step 2: Run focused QoS/scaling tests**

```bash
/Users/harsh.kumar01/.local/bin/uv run pytest tests/qos tests/scaling tests/observability -q --no-cov
```

Expected: routing, backpressure, limits, metrics, and observability tests pass.

- [ ] **Step 3: Perform bounded service-interruption scenarios**

Pause only one in-scope service at a time, submit a run-tagged operation, verify bounded failure/retry, resume the service, and verify recovery. Do not stop Postgres during a write or remove volumes.

Expected: Redis, worker, provider/API, and telemetry interruptions have explicit recovery outcomes.

### Task 12: Defect Loop and Final Certification Report

**Files:**
- Create: `docs/testing/AVCERT-20260719-003221-full-product-certification-report.md`
- Modify/Test: only files implicated by reproduced certification blockers.

- [ ] **Step 1: Classify every non-pass result**

Use one of: `product defect`, `test defect`, `configuration`, `dependency`, `credential`, `provider behavior`, or `capability unavailable`.

- [ ] **Step 2: Fix only safe, unambiguous blockers with TDD**

For each approved blocker: write/reproduce the failing test, make the minimal change, rerun the focused test, then rerun its containing certification layer.

- [ ] **Step 3: Write the report with complete matrices**

The report must include commit/run identity, environment, commands, counts, backend/frontend/SDK results, infrastructure health, agent/RAG pattern matrix, 37-domain matrix, hallucination/guardrail/eval matrix, Jira issue ledger, screenshots/traces, defect ledger, fixes/reruns, model cost summary, residual risks, and launch-readiness verdict.

- [ ] **Step 4: Self-review the report**

Search for placeholders, unexplained skip/xfail/timeout states, claims without evidence, missing feature packages/routes, secrets, and any Jira delete action.

Expected: no placeholder or unsupported certification claim remains.

- [ ] **Step 5: Commit only certification-owned artifacts**

```bash
git add docs/testing/AVCERT-20260719-003221-full-product-certification-report.md
git commit -m 'docs(testing): publish full-product live certification'
```

Expected: pre-existing user files remain untracked/unchanged unless an explicitly verified product fix required a separate commit.
