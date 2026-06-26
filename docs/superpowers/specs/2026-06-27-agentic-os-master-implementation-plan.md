# AgentVerse Agentic OS — Master Implementation Plan (All 25 Phases)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 25 Agentic OS features end-to-end — from Universal Capability Registry to world-class UI/UX — with full E2E testing, zero mocking in production paths, and production-grade code throughout.

**Source specs:**
- Phases 1–13: `docs/superpowers/specs/2026-06-27-agentic-os-phases-1-13-spec.md`
- Phases 14–25: `docs/superpowers/specs/2026-06-27-agentic-os-phases-14-25-spec.md`
- Gap analysis: `docs/superpowers/specs/2026-06-27-production-completion-spec.md`

**Execution order:** P0 items first (broken/blocking), then P1 (stubs → real), then P2 (world-class).

---

## Implementation Priority Order

### 🔴 P0 — Broken / Must Fix Before Anything Else (Execute in parallel batches)

#### Batch P0-A: Agent Execution Foundations
- [ ] **P0-A1** Phase 2: Create `app/agent/router.py` — auto-route goals to agents
- [ ] **P0-A2** Phase 5: Distributed lock (`app/reliability/distributed_lock.py`) — at-most-once execution
- [ ] **P0-A3** Phase 5: Hard timeout per goal (wrap `loop.run()` in `asyncio.wait_for`)
- [ ] **P0-A4** Phase 4: Step checkpoint write path (make `GoalCheckpoint` table actually used)
- [ ] **P0-A5** Phase 4: Goal pause / resume endpoints

#### Batch P0-B: Governance Durability
- [ ] **P0-B1** Phase 9: HITL approval persistence to DB (not in-memory)
- [ ] **P0-B2** Phase 9: Policy persistence to DB (not `app.state` dict)
- [ ] **P0-B3** Phase 10: Approval expiry enforcement (Celery task + `expires_at` set)
- [ ] **P0-B4** Phase 9: Real budget cost calculation (estimate_cost from token counts)
- [ ] **P0-B5** Phase 10: Approval notifications (Slack/webhook on new approval request)

#### Batch P0-C: Event Bus Completeness
- [ ] **P0-C1** Phase 18: ONCE trigger type actually fires in `fire_due_schedules`
- [ ] **P0-C2** Phase 18: REST trigger endpoint `POST /schedules/{id}/fire`
- [ ] **P0-C3** Phase 18: Real platform events SSE via Redis pub/sub (not heartbeat-only)
- [ ] **P0-C4** Phase 18: Outbound webhook subscriptions wired to goal completion

#### Batch P0-D: Memory and Knowledge Durability
- [ ] **P0-D1** Phase 7: DB write path for `ExecutionMemory` + `LongTermMemoryStore`
- [ ] **P0-D2** Phase 7: Wire failure memory into planner prompt
- [ ] **P0-D3** Phase 7: Memory REST API (`GET/DELETE /memory/long-term`)
- [ ] **P0-D4** Phase 8: Remove random embedding fallback — raise HTTP 503 instead
- [ ] **P0-D5** Phase 8: File upload endpoint `POST /knowledge/ingest/file` (PDF/DOCX/TXT)

#### Batch P0-E: RPA Statefulness
- [ ] **P0-E1** Phase 6: `BrowserSessionManager` — keep Playwright sessions alive
- [ ] **P0-E2** Phase 6: Real `rpa_click` / `rpa_type` / `rpa_extract_text` with stateful session
- [ ] **P0-E3** Phase 6: Wire `RPAArtifactStore` to executor (screenshots persisted, not ephemeral)

#### Batch P0-F: Observability Gaps
- [ ] **P0-F1** Phase 15: Per-step OTel spans (plan, execute, verify, tool call)
- [ ] **P0-F2** Phase 15: Wire `record_llm_tokens()` in Anthropic + OpenAI providers
- [ ] **P0-F3** Phase 15: Event timestamps (`ts` field in every SSE event)
- [ ] **P0-F4** Phase 15: PII detection applied to MCP tool call outputs
- [ ] **P0-F5** Phase 15: Persist `DecisionTrace` to DB + `GET /goals/{id}/traces` endpoint

#### Batch P0-G: Multi-Tenancy Correctness
- [ ] **P0-G1** Phase 19: Fix hardcoded `PROFESSIONAL` plan in Celery workers
- [ ] **P0-G2** Phase 19: Concurrent goal cap per tenant (Redis counter)
- [ ] **P0-G3** Phase 19: GDPR deletion cascade (actual DB DELETE, not in-memory flag)
- [ ] **P0-G4** Phase 24: SOC2 audit log fields (IP, user-agent, API key ID, request ID)
- [ ] **P0-G5** Phase 24: Retention policy Celery task (DELETE expired records)

---

### 🟡 P1 — Stubs Must Become Real

#### Batch P1-A: Planner Upgrade
- [ ] **P1-A1** Phase 3: Structured JSON plan format (`StructuredPlan`, `STRUCTURED_PLANNER_SYSTEM`)
- [ ] **P1-A2** Phase 3: Parallel step detection from `depends_on` + `asyncio.gather`
- [ ] **P1-A3** Phase 3: Generic tool approval policy (all connectors, not just Jira)
- [ ] **P1-A4** Phase 1: Tool risk classification for non-Jira tools (GitHub, Slack, Stripe)
- [ ] **P1-A5** Phase 1: OAuth/PKCE/mTLS/HMAC auth header construction

#### Batch P1-B: Capability Registry
- [ ] **P1-B1** Phase 1: MCP health check task (actually ping servers, update status)
- [ ] **P1-B2** Phase 1: Connector health snapshot persistence + history endpoint
- [ ] **P1-B3** Phase 1: Semantic tool search using embeddings

#### Batch P1-C: Marketplace and Simulation
- [ ] **P1-C1** Phase 12: `deploy()` must create real agent via `AgentStore`
- [ ] **P1-C2** Phase 12: Template versioning (version field + `GET /marketplace/{id}/versions`)
- [ ] **P1-C3** Phase 13: Real simulation using `MockMCPClient` + real LLM planner
- [ ] **P1-C4** Phase 13: Side-effect preview in simulation results

#### Batch P1-D: Eval and Red Team
- [ ] **P1-D1** Phase 14: `EvalRunner.score_and_persist()` — write to `evaluations` table
- [ ] **P1-D2** Phase 14: Behavioral `BehavioralRedTeamRunner` — submit to live agent
- [ ] **P1-D3** Phase 14: LLM token cost in efficiency dimension
- [ ] **P1-D4** Phase 14: SLA dimension in `EvalScorecard`
- [ ] **P1-D5** Phase 14: `EvalSuiteRunner` + golden task set + eval suite API

#### Batch P1-E: Identity and Secrets
- [ ] **P1-E1** Phase 17: OAuth token persistence to `oauth_tokens` DB table
- [ ] **P1-E2** Phase 17: Auto-refresh expired OAuth tokens before tool calls
- [ ] **P1-E3** Phase 17: Credential use audit (connector_id, auth_type in AuditEvent)
- [ ] **P1-E4** Phase 17: API key expiry enforced at query time in middleware

#### Batch P1-F: Reliability Wiring
- [ ] **P1-F1** Phase 23: Wire circuit breaker to `MCPClient.call_tool()`
- [ ] **P1-F2** Phase 23: Stuck goal detector Celery task (5-minute interval)
- [ ] **P1-F3** Phase 23: Redis-backed idempotency keys for goal submissions
- [ ] **P1-F4** Phase 23: Bulkhead semaphore (per-tenant concurrent tool call limit)
- [ ] **P1-F5** Phase 5: DLQ routing after Celery `max_retries` exhausted

#### Batch P1-G: Knowledge Upgrades
- [ ] **P1-G1** Phase 8: Repo ingestion `POST /knowledge/ingest/repo` (git clone + walk)
- [ ] **P1-G2** Phase 8: OpenAPI spec ingestion `POST /knowledge/ingest/openapi`
- [ ] **P1-G3** Phase 8: Agent-to-knowledge binding (`allowed_collection_ids` on agent)
- [ ] **P1-G4** Phase 8: Source citations in search results (source_file, source_url)
- [ ] **P1-G5** Phase 8: Semantic chunking (sentence-boundary, code-aware)

---

### 🟢 P2 — World-Class Additions

#### Batch P2-A: World-Class Observability
- [ ] **P2-A1** Phase 15: Structured error classification (`ErrorClass` enum)
- [ ] **P2-A2** Phase 15: Phase-level Prometheus metrics (plan/verify/queue-wait histograms)
- [ ] **P2-A3** Phase 16: Artifact DB model + REST API + S3/GCS storage backend
- [ ] **P2-A4** Phase 16: Artifact events in goal SSE stream (`artifact_captured`)
- [ ] **P2-A5** Phase 16: Artifact retention policy enforcement

#### Batch P2-B: Agent Router Intelligence
- [ ] **P2-B1** Phase 2: LLM-based routing confidence (LLM classifies goal → domain)
- [ ] **P2-B2** Phase 2: Historical success rate routing (prefer agents with high past success)
- [ ] **P2-B3** Phase 4: Real rollback execution (call inverse MCP APIs on failure)
- [ ] **P2-B4** Phase 4: Runtime conditional branches in workflow
- [ ] **P2-B5** Phase 6: Login/session credential vault for RPA

#### Batch P2-C: Collaboration and Multi-Agent
- [ ] **P2-C1** Phase 11: LLM-based consensus synthesis (replace rule-based)
- [ ] **P2-C2** Phase 11: Agent-to-agent task delegation endpoint
- [ ] **P2-C3** Phase 9: Policy simulation endpoint `POST /governance/policies/simulate`
- [ ] **P2-C4** Phase 9: Time-window policies (`allowed_hours`, `allowed_weekdays`)
- [ ] **P2-C5** Phase 9: Emergency stop kill switch `POST /governance/emergency-stop`

#### Batch P2-D: Developer Experience
- [ ] **P2-D1** Phase 20: Agent manifest YAML format (`AgentManifest.from_yaml()`)
- [ ] **P2-D2** Phase 21: Missing CLI commands (login, goals, cancel, approve, connectors, eval, logs)
- [ ] **P2-D3** Phase 20: MockMCPServer for local development
- [ ] **P2-D4** Phase 20: `agentverse manifest validate` CLI command
- [ ] **P2-D5** Phase 10: Multi-person approval (N-of-M `required_approvers`)

#### Batch P2-E: World-Class UI
- [ ] **P2-E1** Phase 25: `ExecutionTimeline` component in `GoalDetailPage`
- [ ] **P2-E2** Phase 25: Tool Call Inspector panel in `GoalDetailPage`
- [ ] **P2-E3** Phase 25: Dedicated Approval Inbox page (`/approvals`) with sidebar badge
- [ ] **P2-E4** Phase 25: Agent Detail page (`/agents/:agentId`)
- [ ] **P2-E5** Phase 25: In-App Cost Dashboard (`/observability/cost`)
- [ ] **P2-E6** Phase 25: Knowledge Workbench enhancements (chunk viewer, file upload)
- [ ] **P2-E7** Phase 25: Fix `next_run_at` always showing `—` in SchedulesPage
- [ ] **P2-E8** Phase 25: Onboarding flow (`/onboarding`) for new tenants

#### Batch P2-F: Production Infra
- [ ] **P2-F1** Phase 22: KEDA ScaledObject for worker autoscaling
- [ ] **P2-F2** Phase 22: Frontend Dockerfile + K8s Deployment/Service
- [ ] **P2-F3** Phase 22: PostgreSQL backup CronJob
- [ ] **P2-F4** Phase 22: ClusterSecretStore + ClusterIssuer manifests
- [ ] **P2-F5** Phase 24: Compliance report download endpoint

---

## Task Template (Use for Every Item Above)

```markdown
### Task {batch}-{number}: {Title}

**Spec reference:** `docs/superpowers/specs/2026-06-27-agentic-os-phases-{N}-{M}-spec.md` Phase {X}.{Y}

**Files:**
- Create: `exact/path/to/new_file.py`
- Modify: `exact/path/to/existing.py`
- Test: `tests/exact/path/test_feature.py`
- Frontend: `src/features/.../Component.tsx`

- [ ] **Step 1: Write failing test**
  ```python
  def test_feature(): assert False, "Not implemented"
  ```
  Run: `uv run pytest tests/path/test_feature.py -v`
  Expected: FAIL

- [ ] **Step 2: Implement**
  (Complete code from spec)

- [ ] **Step 3: Run tests**
  Run: `uv run pytest tests/path/test_feature.py -v`
  Expected: PASS

- [ ] **Step 4: Run full suite**
  Run: `uv run pytest --no-cov -q -m "not integration and not slow"`
  Expected: All tests pass (no regressions)

- [ ] **Step 5: Frontend (if applicable)**
  Run: `cd agent-verse-frontend && npm run build && npm run test -- --run`
  Expected: Build succeeds, all tests pass

- [ ] **Step 6: Commit**
  ```bash
  git add . && git commit -m "feat({phase}): {description}"
  ```
```

---

## Acceptance Criteria per Phase

| Phase | Acceptance Command | Pass Condition |
|---|---|---|
| 1 — Capability Registry | `uv run pytest tests/mcp/ tests/agent/test_tool_risk.py -v` | All pass |
| 2 — Agent Router | `uv run pytest tests/agent/test_router.py -v` | All pass; `agent_id` auto-selected in goals |
| 3 — Structured Planner | `uv run pytest tests/agent/test_structured_plan.py -v` | Structured plan produced from LLM |
| 4 — DAG Engine | `uv run pytest tests/agent/ -v` | Parallel waves execute; checkpoint written |
| 5 — Durable Kernel | `uv run pytest tests/scaling/ tests/reliability/ -v` | Lock acquired; timeout fires; DLQ routes |
| 6 — RPA | `uv run pytest tests/rpa/ -v` | Stateful sessions; real click/type/extract |
| 7 — Memory | `uv run pytest tests/api/test_memory.py -v` | DB writes verified; recall works |
| 8 — Knowledge | `uv run pytest tests/api/test_knowledge_api.py -v` | File upload ingests; citations returned |
| 9 — Governance | `uv run pytest tests/api/test_governance_api.py -v` | Policies survive restart; real budget |
| 10 — HITL | `uv run pytest tests/governance/ -v` | Approvals in DB; expiry fires; Slack notified |
| 11 — Collaboration | `uv run pytest tests/api/test_collab.py -v` | LLM consensus synthesized |
| 12 — Marketplace | `uv run pytest tests/enterprise/test_enterprise_api.py -v` | Deploy creates real agent |
| 13 — Simulation | `uv run pytest tests/enterprise/test_real_simulation.py -v` | Real LLM planner used |
| 14 — Eval | `uv run pytest tests/intelligence/ -v` | Scores persisted to DB; behavioral red team |
| 15 — Observability | `uv run pytest tests/observability/ -v` + check Jaeger traces | Per-step spans visible |
| 16 — Artifacts | `uv run pytest tests/api/test_artifacts.py -v` | Artifacts stored + retrievable |
| 17 — Identity | `uv run pytest tests/mcp/ tests/providers/ -v` | OAuth tokens survive restart |
| 18 — Event Bus | `uv run pytest tests/api/test_schedules_api.py -v` | ONCE/REST/EVENT trigger fire |
| 19 — Isolation | `uv run pytest tests/tenancy/ -v` | Plan enforced in workers; deletion cascades |
| 20 — SDK | `uv run pytest tests/sdk/ -v` | Manifest validates; MockMCPServer works |
| 21 — CLI/API | `uv run python -m app.cli.main --help` | All commands listed and functional |
| 22 — Deployment | `docker build -t test . && kubectl apply --dry-run=client -f infra/k8s/` | Build + manifests valid |
| 23 — Reliability | `uv run pytest tests/reliability/ -v` | CB fires on repeated failures; stuck goals detected |
| 24 — Compliance | `uv run pytest tests/enterprise/test_compliance_download.py -v` | Real DB deletion; SOC2 fields |
| 25 — UI/UX | `npm run test -- --run && npx playwright test --list` | 106+ Vitest pass; 97+ Playwright registered |

---

## Final Verification

After all phases are complete:

```bash
# Backend: full test suite
cd agent-verse-backend
uv run pytest --no-cov -q \
  --ignore=tests/core/test_pools_integration.py \
  --ignore=tests/db/test_migrations.py \
  --ignore=tests/db/test_tenancy_integration.py \
  --ignore=tests/db/test_rls.py \
  --ignore=tests/e2e/test_integration_with_real_redis.py \
  --ignore=tests/infra/ \
  -m "not integration and not slow"
# Expected: 1200+ tests pass, 0 failures

# Frontend: build + unit tests
cd agent-verse-frontend
npm run build
npm run test -- --run
# Expected: Build succeeds, 150+ Vitest tests pass

# Playwright E2E count
npx playwright test --list | grep "›" | wc -l
# Expected: 97+ tests listed

# Linting
cd agent-verse-backend
uv run ruff check app/
uv run mypy app/ --ignore-missing-imports
# Expected: 0 errors

# OpenAPI schema completeness
uv run python scripts/export_openapi.py
python -c "import json; s=json.load(open('openapi.json')); print(f'{len(s[\"paths\"])} paths')"
# Expected: 90+ paths
```

---

## Commit Message Convention

```
feat(phase-{N}): {short description}

- {bullet points of what was implemented}
- Tests: {N} new tests added
- Files: {list of key files changed}
```

---

## Cross-Phase Dependencies

```
Phase 1 (Registry)  ──► Phase 2 (Router)   ──► Phase 3 (Planner)
Phase 3 (Planner)   ──► Phase 4 (DAG)      ──► Phase 5 (Kernel)
Phase 5 (Kernel)    ──► Phase 6 (RPA)
Phase 7 (Memory)    ──► Phase 14 (Eval)
Phase 8 (Knowledge) ──► Phase 14 (Eval)
Phase 9 (Governance)──► Phase 10 (HITL)   ──► Phase 11 (Collab)
Phase 12 (Market)   ──► Phase 13 (Sandbox)──► Phase 14 (Eval)
Phase 15 (Obs)      ──► Phase 16 (Artifacts)
Phase 17 (Identity) ──► Phase 18 (Events)
Phase 19 (Isolation)──► Phase 24 (Compliance)
Phase 20 (SDK)      ──► Phase 21 (CLI)
Phase 22 (Deploy)   ──► Phase 23 (Reliability)
All phases          ──► Phase 25 (UI/UX)
```
