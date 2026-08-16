# Workflow Automation Engine — Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` to execute this plan phase-by-phase. Every phase ends with a mandatory test command. Advance only when it passes.

**Spec:** `docs/superpowers/specs/2026-08-15-workflow-automation-engine-design.md`  
**Date:** 2026-08-16  
**Status:** Ready to implement

---

## Codebase Reality Check

### What ALREADY EXISTS (reuse, don't recreate)

| What | Location | Used For |
|---|---|---|
| LangGraph `StateGraph`, `START`, `END`, `MemorySaver` | `langgraph` (installed) | Workflow compiler — same pattern as `app/agent/graph.py` |
| `HITLGateway` (Redis BLPOP cross-replica) | `app/governance/hitl.py` | Base for `HITLWorkflowGateway` |
| `AuditLog` + `AuditEvent` | `app/governance/audit.py` | `AutoAuditMiddleware` |
| `CostController` | `app/governance/cost.py` | Per-run cost tracking |
| `PolicyEngine` | `app/governance/policies.py` | HITL policy enforcement |
| `CircuitBreaker` | `app/reliability/circuit_breaker.py` | HTTP step circuit breaker |
| `Idempotency` | `app/reliability/idempotency.py` | Webhook idempotency (adapt pattern) |
| `LLMProvider` + `CompletionRequest` | `app/providers/base.py` | LLM step + RAG step |
| `FakeProvider` | `app/providers/fake.py` | Test dry-runs |
| `VaultClient` | `app/providers/vault.py` | `{{vault://X}}` resolution |
| `MCPClient.call_tool()` | `app/mcp/client.py` | Tool step |
| `KnowledgeStore.retrieve()` | `app/knowledge/store.py` | RAG step |
| `LongTermMemoryStore` | `app/memory/long_term.py` | Org memory (Phase 7 AgentOrg) |
| `ExecutionEnvironment` (full sandbox) | `app/execution_environment/` | `code` step — Python/JS/Bash |
| `TenantContext`, RLS middleware | `app/tenancy/context.py` | All workflow tables |
| `GoalService` SSE broadcaster | `app/services/goal_service.py` | Adapt for workflow SSE events |
| `NotificationService` | `app/services/notification_service.py` | HITL notifications |
| `celery_app` + per-plan queue routing | `app/scaling/celery_app.py` | Add `workflows.*` queues |
| `TriggerType` enum | `app/triggers/models.py` | All trigger types already defined |
| `NLScheduler` | `app/triggers/nl_scheduler.py` | `NLTriggerResolver` base |
| `IntentRouter` (in chat) | `app/chat/intent.py` | NL trigger classification |

### What DOES NOT EXIST (must create)

The `app/workflow/` package is entirely new. The existing `app/agent/workflow_planner.py` and `app/agent/workflow_nodes.py` are **for the Goals Engine** (dynamic planning), not for the user-defined engine.

The existing `workflows` table (migration 0046) is a **canvas-only stub** — just stores ReactFlow JSON. The full engine needs 7 new tables.

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, LangGraph, Celery, Postgres+pgvector, Redis, `simpleeval`
- **Frontend:** React 19, TypeScript, React Flow (Xyflow), Monaco editor, TanStack Query, Zustand, Tailwind
- **Testing:** pytest + FakeProvider + MockToolRegistry (backend), Vitest + Playwright (frontend)
- **New deps:** `simpleeval` (safe expression evaluator for conditional steps)

---

## Migration Numbering

Last migration: `0107_channel_state_machine_tables.py`  
Next: `0108_workflow_engine_tables.py`

---

## Phase 1 — Core Engine Backend

**Goal:** Complete `app/workflow/` package. DSL parsing, state machine, all 14 step types, LangGraph compiler, WorkflowRunner, DB tables, core API. Tests pass with FakeProvider.

### New Files

| File | Responsibility |
|---|---|
| `app/workflow/__init__.py` | Package init + register all built-in step types |
| `app/workflow/dsl.py` | `WorkflowDefinition` Pydantic model — full YAML↔JSON parser + validator. Validates: step types exist in registry, depends_on references valid step IDs, no circular deps, input types match enum |
| `app/workflow/state.py` | `WorkflowState` TypedDict + `WorkflowRunStatus` + `StepStatus` enums |
| `app/workflow/registry.py` | `StepTypeRegistry` + `StepTypeMeta` — public plugin extension point |
| `app/workflow/context.py` | `ContextResolver` — resolves `{{inputs.X}}`, `{{steps.X.output.Y}}`, `{{vars.X}}`, `{{vault://Z}}`, `{{env.X}}`, `{{foreach.X}}`, `{{trigger.X}}`, `{{workflow.run_id}}` etc. |
| `app/workflow/expression_engine.py` | `ExpressionEngine` — `simpleeval`-based safe evaluator for conditional branches. Blocklist: import, exec, builtins, filesystem, network |
| `app/workflow/security.py` | `SSRFGuard` (private IP + hostname blocklist) + `SecretMasker` (redact vault values before DB persist) |
| `app/workflow/variables.py` | `WorkflowVariableStore` — set/get mutable `vars` dict within a run |
| `app/workflow/audit_middleware.py` | `AutoAuditMiddleware` — wraps every step with automatic `AuditEvent` emission (11 event types) |
| `app/workflow/storage.py` | `LargePayloadStore` — S3/MinIO offload when step output > 5MB |
| `app/workflow/otel.py` | `WorkflowOTELMiddleware` — root OTEL span per run, child span per step |
| `app/workflow/steps/__init__.py` | Step package |
| `app/workflow/steps/base.py` | `BaseStepNode` Protocol — the public extension point |
| `app/workflow/steps/tool_step.py` | `type: tool` → `MCPClient.call_tool()` |
| `app/workflow/steps/llm_step.py` | `type: llm` → `LLMProvider.complete()` with optional RAG |
| `app/workflow/steps/rag_step.py` | `type: rag` → `KnowledgeStore.retrieve()` + `LLMProvider` |
| `app/workflow/steps/http_step.py` | `type: http` → `httpx.AsyncClient` + `SSRFGuard` + `CircuitBreaker` |
| `app/workflow/steps/hitl_step.py` | `type: hitl` → `HITLWorkflowGateway.create_workflow_approval()` — suspend/resume pattern |
| `app/workflow/steps/parallel_step.py` | `type: parallel` → `asyncio.gather()` fan-out, merge into state |
| `app/workflow/steps/conditional_step.py` | `type: conditional` → `ExpressionEngine.evaluate()` → branch routing |
| `app/workflow/steps/foreach_step.py` | `type: foreach` → iterate array, `max_concurrency` batching, progress tracking |
| `app/workflow/steps/transform_step.py` | `type: transform` → pure data mapping using `ContextResolver` |
| `app/workflow/steps/sub_workflow_step.py` | `type: sub_workflow` → recursive `WorkflowRunner.run()` |
| `app/workflow/steps/wait_step.py` | `type: wait` → timer via Celery ETA or event-gate via Redis BLPOP |
| `app/workflow/steps/code_step.py` | `type: code` → delegates to `app/execution_environment/` sandbox (Python/JS, 30s/128MB) |
| `app/workflow/steps/set_variable_step.py` | `type: set_variable` → `WorkflowVariableStore.set()` |
| `app/workflow/steps/emit_event_step.py` | `type: emit_event` → Redis pub/sub channel publish |
| `app/workflow/compiler.py` | `WorkflowCompiler` — YAML `WorkflowDefinition` → compiled LangGraph `StateGraph`. Cache per `(workflow_id, version)`. Handles: deps, parallel fan-out/merge, conditional routing, foreach expansion |
| `app/workflow/runner.py` | `WorkflowRunner` — orchestrates: validate inputs (max 1MB), apply `trigger_transform`, create DB run record, dispatch to Celery, OTEL wrap, **emit callback URL POST on completion**, check operator pause before each step |
| `app/workflow/storage.py` | `LargePayloadStore` — enforce 5MB per-step-output limit; offload oversized payloads to S3/MinIO; `ContextResolver` fetches transparently on `{{steps.X.output}}` access |
| `app/workflow/audit_middleware.py` | `AutoAuditMiddleware` — wraps **every** step with automatic `AuditEvent` (11 types): `run_started`, `step_started`, `step_completed`, `step_failed`, `step_skipped`, `hitl_requested`, `hitl_decided`, `run_completed`, `run_failed`, `run_paused`, `run_resumed`. Hash-only storage (PII-safe). Zero manual `audit.record` steps needed |
| `app/workflow/variables.py` | `WorkflowVariableStore` — `set(state, name, value)` / `get(state, name, default)` for mutable `vars` dict; read via `{{vars.X}}`; written by `set_variable` steps |
| `app/workflow/otel.py` | `WorkflowOTELMiddleware` — root OTEL span per run, child span per step, LLM sub-span with token counts |
| `app/workflow/nl_trigger.py` | `NLTriggerResolver` — uses `IntentRouter` + fuzzy phrase match against `trigger.nl.phrases` → extract inputs via LLM → `WorkflowRunner.run()` |
| `app/workflow/registry.py` | `StepTypeRegistry` + `StepTypeMeta` — **the public plugin extension point**. All 14 built-in types registered at startup. Custom step types: `register(step_type, NodeClass, meta)`. Visual Builder palette + YAML validator both read from registry. Step-type API: `GET /workflow-step-types`, `GET /workflow-step-types/{type}`, `POST /workflow-step-types` (enterprise) |
| `app/workflow/celery_tasks.py` | `execute_workflow_run`, `resume_workflow_from_hitl`, `check_hitl_escalations` (every 15min), `retry_dead_letter_webhooks` (every 5min), `cleanup_expired_runs` (daily — enforces `run_retention_days`, GDPR purges run data for erased subjects) |
| `app/workflow/router.py` | FastAPI router — **full 76-endpoint API surface** (spec §"Full API Surface"). Includes: definitions CRUD, publish/unpublish, validate, check-connectors, **step-type registry** (`GET/POST /workflow-step-types`), YAML import/export, clone, RBAC endpoints, analytics, error recovery, **webhook DLQ**, run execution, **pause/resume**, label-filter on runs (`?label.KEY=VALUE`). **Webhook ingress** (`POST /webhooks/workflows/{token}`) with: HMAC-SHA256 + replay protection, **per-token rate-limit** (60 req/min via existing rate limiter), idempotency key check. **Testing API**: `POST /workflows/{id}/test` (dry-run), `POST /workflows/{id}/test/step-through`, `POST /workflow-runs/{run_id}/advance`, `GET/POST/PUT/DELETE /workflows/{id}/test-scenarios`, `POST /workflows/{id}/test-scenarios/run-all` |

### New Migration

| File | Tables Created + Key Columns |
|---|---|
| `app/db/migrations/versions/0108_workflow_engine_tables.py` | **9 tables** (all with RLS): `system_workflow_templates`, `workflow_definitions` (full — incl. `run_retention_days INT`, `requires_publish_approval BOOL`, `publish_approved_by UUID`, `publish_approved_at`), `workflow_definition_versions`, `workflow_runs` (incl. `labels JSONB`, `run_metadata JSONB`, `foreach_progress JSONB`, `paused_by UUID`, `paused_at`), `workflow_step_results`, `workflow_hitl_requests`, `workflow_test_scenarios`, `workflow_permissions`, `workflow_webhook_events` |

All tables: RLS enabled, `tenant_id = current_setting('app.tenant_id')`.

### Modified Files

| File | Change |
|---|---|
| `app/main.py` | Add `from app.workflow.router import router as workflow_router` + `app.include_router(workflow_router)`. Wire `workflow_compiler`, `workflow_runner`, `hitl_workflow_gateway` onto `app.state` in both in-memory and lifespan phases |
| `app/scaling/celery_app.py` | Add `"app.workflow.celery_tasks"` to `include`. Add `workflows.free/starter/professional/enterprise` queues to `task_routes`. Register beat schedule: `check_hitl_escalations`, `retry_dead_letter_webhooks`, `cleanup_expired_runs` |
| `app/db/migrations/versions/0046_workflows.py` | No change — migration 0108 adds the new tables alongside the existing `workflows` table |

### Test Files (Phase 1)

| File | Coverage Target |
|---|---|
| `tests/workflow/test_dsl.py` | YAML parse, field validation, circular dep detection, unknown step type rejection, missing required input, enum validation (15 cases) |
| `tests/workflow/test_context.py` | All `{{...}}` variants, vault masking, nested path resolution, missing ref returns None (20 cases) |
| `tests/workflow/test_expression_engine.py` | Arithmetic, comparison, logical, membership, blocked imports, blocked builtins, SAFE_FUNCTIONS (18 cases) |
| `tests/workflow/test_security.py` | SSRF: private IP, AWS metadata, loopback, IPv6. Secret masking: vault key redacted, env var not redacted. **HMAC-SHA256 replay protection**: valid sig passes, replayed request (old timestamp) rejected, bad sig rejected (14 cases) |
| `tests/workflow/test_registry.py` | Register custom type, get unknown type raises, list_all returns metadata, compiler uses registry for node resolution (8 cases) |
| `tests/workflow/test_compiler.py` | Simple linear, parallel fan-out, conditional routing, foreach expansion, sub-workflow nesting (12 cases) |
| `tests/workflow/test_runner.py` | Happy path, step failure + pause, HITL suspend/resume, parallel step, foreach progress, **callback URL POST on completion**, **trigger_transform reshapes payload**, **run labels stored + queryable**, **operator pause/resume**, S3 offload when output > 5MB (22 cases) |
| `tests/workflow/test_steps/test_http_step.py` | Success, SSRF blocked, circuit breaker tripped, timeout, **HMAC replay rejected**, valid HMAC passes (12 cases) |
| `tests/workflow/test_variables.py` | set/get variable, overwrite, `{{vars.X}}` resolved in context, initial value from DSL (6 cases) |
| `tests/workflow/test_audit_middleware.py` | step_started emitted, step_completed emitted, step_failed emitted, hitl events, run events — all without manual audit step (10 cases) |
| `tests/workflow/test_storage.py` | Output < 5MB stays in DB, output > 5MB offloaded to S3 ref, context resolver fetches from S3 (6 cases) |
| `tests/workflow/test_publishing_approval.py` | Submit for approval, approve, reject, publish blocked without approval when flag set (6 cases) |
| `tests/workflow/test_steps/test_tool_step.py` | Tool call success, tool error, timeout (6 cases) |
| `tests/workflow/test_steps/test_llm_step.py` | LLM with FakeProvider, with RAG, prompt template resolution (8 cases) |
| `tests/workflow/test_steps/test_http_step.py` | Success, SSRF blocked, circuit breaker tripped, timeout, HMAC webhook (10 cases) |
| `tests/workflow/test_steps/test_hitl_step.py` | Suspend on first call, resume with approve/reject/custom action (6 cases) |
| `tests/workflow/test_steps/test_parallel_step.py` | All success, partial failure with continue, max concurrency (6 cases) |
| `tests/workflow/test_steps/test_conditional_step.py` | True branch, false branch, default branch, multi-branch, expression error (8 cases) |
| `tests/workflow/test_steps/test_foreach_step.py` | Full iteration, partial failure (on_item_failure: continue), max_concurrency batching, progress tracking (8 cases) |
| `tests/workflow/test_steps/test_code_step.py` | Python hello world, JS, timeout, blocked import, sandbox isolation (8 cases) |
| `tests/workflow/test_router.py` | Create, get, publish, validate, trigger run, get run status, get steps, cancel run (20 cases) |
| `tests/workflow/test_nl_trigger.py` | Phrase match, input extraction, no match returns None (6 cases) |

### Phase 1 Acceptance Criteria

```bash
cd agent-verse-backend
uv run ruff check app/workflow/
uv run mypy app/workflow/
uv run pytest tests/workflow/ -q --no-cov -x
# Expected: all green, 0 errors
```

---

## Phase 2 — HITL Extension + Templates Backend

**Goal:** Full 20-feature HITL, magic links, escalation, delegation. 25 system template YAML files. Template marketplace API.

### New Files

| File | Responsibility |
|---|---|
| `app/workflow/hitl_extension.py` | `HITLWorkflowGateway` — extends `HITLGateway`. Full 20-feature HITL: rich context (7 display types: image/json/table/diff/chart/**number with threshold_red/yellow**/list), custom action buttons, **custom form schema** (reviewer fills structured input), **escalation chain** (after Xh → to_role), **delegation** to colleague, **bulk approval** (decide multiple at once), **magic link JWT** (single-use Redis jti guard), **4 assignment strategies** (round_robin, least_busy, skill_based, **specific_user**), **priority levels** (critical/high/medium/low), **deadline tracking** + SLA countdown, **timeout_action** (auto_approve \| auto_reject \| escalate \| pause), **discussion thread** (comments between reviewers), **audit trail** (`reviewed_by UUID`, `reviewed_at TIMESTAMPTZ` stored per decision) |
| `app/workflow/router_hitl.py` | Approval inbox API: `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/decide`, `POST /approvals/{id}/delegate`, `POST /approvals/{id}/escalate`, `POST /approvals/bulk-decide`, `GET /approvals/magic/{token}`, `POST /approvals/delegate-all`, `GET /approvals/stats`, `GET /approvals/stream` (SSE) |
| `app/workflow/template_store.py` | `SystemTemplateStore` — reads YAML files from `app/workflow/templates/`, returns `SystemTemplate` objects. Provides `get(id)`, `list(category)`, `search(q)`, `fork(id, tenant_id)` |
| `app/workflow/router_templates.py` | Template marketplace API: `GET /workflow-templates`, `GET /workflow-templates/{id}`, `GET /workflow-templates/{id}/preview-run`, `POST /workflow-templates/{id}/fork`, `GET /workflow-templates/categories` |
| `app/workflow/templates/` | 25 YAML template files — one per workflow from the catalog spec |

### 25 System Templates (YAML files in `app/workflow/templates/`)

```
kyc-automation.yaml
merchant-onboarding.yaml
aml-screening.yaml
gdpr-subject-request.yaml
employee-background-check.yaml
invoice-processing.yaml
expense-report.yaml
loan-prescreening.yaml
bank-reconciliation.yaml
sre-incident-response.yaml
production-bug-assistant.yaml
automated-code-review.yaml
release-notes-generator.yaml
security-vulnerability-response.yaml
email-auto-response.yaml
support-ticket-triage.yaml
churn-prediction-outreach.yaml
contract-generation.yaml
legal-contract-analysis.yaml
meeting-minutes-generator.yaml
research-paper-digest.yaml
job-application-screening.yaml
employee-offboarding.yaml
healthcare-patient-onboarding.yaml
ecommerce-return-processing.yaml
```

### New Script

| File | Purpose |
|---|---|
| `scripts/seed_workflow_templates.py` | Reads all YAML files from `app/workflow/templates/`, upserts into `system_workflow_templates` table |

### Modified Files

| File | Change |
|---|---|
| `app/main.py` | Add `hitl_workflow_router` and `workflow_templates_router` includes |
| `app/workflow/celery_tasks.py` | Add `check_hitl_escalations` Beat schedule |

### Test Files (Phase 2)

| File | Coverage Target |
|---|---|
| `tests/workflow/test_hitl_extension.py` | Magic link generate + consume (single-use Redis jti guard), **all 4 assignment strategies** (round_robin, least_busy, skill_based, **specific_user**), escalation after timeout, delegation, bulk decision, rich context payload construction (all 7 display types: image/json/table/diff/chart/**number*/**list**), **timeout_action** variants (auto_approve, auto_reject, escalate, pause), custom form schema validation, **reviewed_by + reviewed_at stored on decision**, discussion thread comment (22 cases) |
| `tests/workflow/test_template_store.py` | Load all 25 templates, validate each has required fields, fork creates tenant copy, popularity incremented on fork (12 cases) |
| `tests/workflow/test_router_hitl.py` | Inbox list, single item, decide (approve/reject), delegate, escalate, magic link, bulk decide, SSE stream (16 cases) |
| `tests/workflow/test_router_templates.py` | List categories, list by category, get template, fork, preview run (10 cases) |

### Phase 2 Acceptance Criteria

```bash
uv run pytest tests/workflow/test_hitl_extension.py tests/workflow/test_template_store.py tests/workflow/test_router_hitl.py tests/workflow/test_router_templates.py -q --no-cov
uv run python scripts/seed_workflow_templates.py --dry-run  # validates all 25 YAML files
```

---

## Phase 3 — NL Trigger, Extra Triggers, Version History, RBAC, Analytics, SDK

**Goal:** All trigger types wired. Version history with rollback. Workflow-level RBAC. Analytics API. SDK additions.

### New Files

| File | Responsibility |
|---|---|
| `app/workflow/router_versions.py` | `GET /workflows/{id}/versions`, `GET /workflows/{id}/versions/{ver}`, `POST /workflows/{id}/rollback/{ver}`, `GET /workflows/{id}/diff/{ver_a}/{ver_b}` |
| `app/workflow/test_runner.py` | `WorkflowTestRunner` — dry-run mode (MockToolAdapter), step-through mode (pause after each step), scenario runner (input fixture + assertions), replay mode |

### Modified Files

| File | Change |
|---|---|
| `app/workflow/runner.py` | Wire `file_drop`, `alertmanager`, `datadog`, `pagerduty` trigger types to `WorkflowRunner.run()`. Auto-save version on every publish. Apply `trigger_transform`. **POST to callback URL on run complete/fail**. Check `operator_paused` before each step. **Enforce max 1MB trigger input** at ingress |
| `app/workflow/router.py` | Add: version history, rollback, diff, YAML import/export, clone, RBAC grant/revoke, analytics, error recovery, label-filter on runs, **`POST /workflows/{id}/submit-for-approval`**, **`POST /workflows/{id}/approve-publish`**, **`POST /workflows/{id}/reject-publish`** |
| `agentverse-sdk-python/agentverse/workflows.py` | New: `WorkflowClient.run()`, `get_run()`, `stream_run()`, `list_runs()`, `cancel_run()` |
| `agent-verse-sdk-typescript/src/workflows.ts` | New: `WorkflowClient` class with same 5 methods |

### Test Files (Phase 3)

| File | Coverage Target |
|---|---|
| `tests/workflow/test_test_runner.py` | Dry-run (all tools mocked), step-through advance, scenario pass/fail assertions, replay from failed step (16 cases) |
| `tests/workflow/test_versions.py` | Publish saves version, rollback restores, diff shows changes (8 cases) |
| `tests/workflow/test_import_export.py` | Export valid YAML, import round-trips, import invalid YAML rejected (6 cases) |
| `tests/workflow/test_trigger_types.py` | file_drop payload mapping, alertmanager routing, pagerduty routing, NL phrase match (10 cases) |

### Phase 3 Acceptance Criteria

```bash
uv run pytest tests/workflow/ -q --no-cov
# Should be: ~200 tests, all green
uv run ruff check app/workflow/ agentverse-sdk-python/agentverse/workflows.py
```

---

## Phase 4 — Visual Builder Frontend

**Goal:** n8n-quality canvas. All 14 node types. Bidirectional YAML↔canvas sync. Step config panels. Execution overlay. Dark mode. WCAG 2.2 AA.

> **UI/UX Design System:** All Phase 4–6 frontend work MUST implement the full design system from spec §"UI/UX Design System". Key requirements:
> - **Design tokens** (`src/features/workflow/design/tokens.ts`) — node-type colors (14), status colors, priority colors, Inter/JetBrains Mono fonts, 4-level shadow system, z-index stack
> - **Motion system** (`src/features/workflow/design/motion.ts`) — 4 spring presets (snappy/bouncy/gentle/smooth), Framer Motion for components, CSS transitions for canvas edges, `prefers-reduced-motion` compliance
> - **Animation catalogue** — 22 named animations (node bounce on drop, edge flowing dots, panel slide, toast stack, SLA countdown pulse, approval swipe tint, etc.)
> - **Canvas context menu** — right-click variants on node/edge/empty-space (spec §Canvas UX)
> - **Component states** — all 8 states on every interactive element: default, hover, focus, active, loading, disabled, error, success
> - **Empty states** — dedicated illustration + CTA for all 6 screens (workflow list, run history, canvas new, HITL inbox, marketplace no-results, analytics no-data)
> - **Error states** — 8 specific error treatments (failed node badge, YAML squiggle, network toast, form inline, etc.)
> - **Toast system** — `src/features/workflow/components/Toast.tsx` — stacked, auto-dismiss (3–8s), progress bar, pause-on-hover, 6 types, spring bounce enter

### New Frontend Files

**Pages:**
| File | Route |
|---|---|
| `src/features/workflow/WorkflowListPage.tsx` | `/workflows` |
| `src/features/workflow/WorkflowBuilderPage.tsx` | `/workflows/:id/edit` |
| `src/features/workflow/WorkflowRunsPage.tsx` | `/workflows/:id/runs` |
| `src/features/workflow/WorkflowRunDetailPage.tsx` | `/workflow-runs/:runId` |

**Canvas Core:**
| File | Responsibility |
|---|---|
| `src/features/workflow/builder/WorkflowCanvas.tsx` | React Flow canvas — 14 node types, animated edges, minimap, undo/redo (Ctrl+Z), auto-layout (Dagre), `dark:` Tailwind support |
| `src/features/workflow/builder/WorkflowToolPalette.tsx` | Left panel — draggable step types grouped by category, MCP tool catalog search |
| `src/features/workflow/builder/WorkflowStepConfig.tsx` | Right panel — config form per selected node type |
| `src/features/workflow/builder/WorkflowTopBar.tsx` | Name edit, version badge, Save/Test/Publish buttons, YAML toggle |
| `src/features/workflow/builder/WorkflowMiniMap.tsx` | Bottom-right minimap |
| `src/features/workflow/builder/WorkflowExecutionOverlay.tsx` | Live per-node status: ✅ complete, 🔄 running, ⏳ HITL, ❌ failed |

**Node Components (14):**
| File | Step Type |
|---|---|
| `nodes/TriggerNode.tsx` | Trigger — webhook/schedule/event/NL/file_drop/etc. |
| `nodes/ToolNode.tsx` | `tool` — MCP tool selector |
| `nodes/LLMNode.tsx` | `llm` — Monaco prompt editor, RAG toggle |
| `nodes/RAGNode.tsx` | `rag` — collection picker, strategy selector |
| `nodes/ConditionalNode.tsx` | `conditional` — diamond shape, branch condition builder |
| `nodes/ParallelNode.tsx` | `parallel` — fork icon, sub-branch manager |
| `nodes/HITLNode.tsx` | `hitl` — context builder, action buttons designer |
| `nodes/HTTPNode.tsx` | `http` — URL/method/headers/auth/body |
| `nodes/ForeachNode.tsx` | `foreach` — iterate_over, body, max_concurrency |
| `nodes/TransformNode.tsx` | `transform` — field mapping |
| `nodes/SubWorkflowNode.tsx` | `sub_workflow` — workflow selector |
| `nodes/WaitNode.tsx` | `wait` — duration/event-gate |
| `nodes/CodeNode.tsx` | `code` — Monaco editor, runtime selector |
| `nodes/SetVariableNode.tsx` | `set_variable` — var name + value expression |

**Config Panels:**
| File | For |
|---|---|
| `config-panels/ToolConfigPanel.tsx` | Tool selector + input mapper (drag step outputs → inputs) |
| `config-panels/LLMConfigPanel.tsx` | Prompt editor, model override, RAG config |
| `config-panels/HITLConfigPanel.tsx` | Context builder, actions designer, escalation settings |
| `config-panels/ConditionalConfigPanel.tsx` | Visual condition builder + raw expression editor |
| `config-panels/HTTPConfigPanel.tsx` | URL, method, headers, auth, body editor |
| `config-panels/InputMapperWidget.tsx` | Drag previous-step outputs to current step inputs |

**Canvas Utilities:**
| File | Responsibility |
|---|---|
| `canvas-utils/useCanvasKeyboardShortcuts.ts` | Ctrl+Z (undo), Ctrl+Y (redo), Space+drag (pan), Ctrl+D (duplicate), Delete (remove) |
| `canvas-utils/useAutoLayout.ts` | Dagre algorithm — arrange nodes automatically |
| `canvas-utils/useYamlSync.ts` | Bidirectional: canvas state ↔ YAML string. YAML is canonical. |
| `canvas-utils/nodeStyles.ts` | Per-node-type colors, shapes, icons |

**YAML Editor:**
| File | Responsibility |
|---|---|
| `code-editor/WorkflowYamlEditor.tsx` | Monaco editor — YAML mode, schema validation against DSL, `{{` autocomplete |
| `code-editor/useYamlValidation.ts` | Real-time validation as user types, error squiggles |
| `code-editor/YamlErrorPanel.tsx` | Error list below editor |

**Run Viewer:**
| File | Responsibility |
|---|---|
| `run-viewer/RunTimeline.tsx` | Vertical step timeline with status icons, click to inspect |
| `run-viewer/StepOutputInspector.tsx` | JSON tree view of step input/output |
| `run-viewer/LLMPromptViewer.tsx` | Full prompt + response, token count |
| `run-viewer/RunCostSummary.tsx` | Total cost, token count, duration breakdown |
| `run-viewer/RunSSEStream.ts` | SSE hook with `Last-Event-ID` reconnection |

**Testing UI (Phase 4 stub):**
| File | Responsibility |
|---|---|
| `testing/TestRunnerPanel.tsx` | Bottom drawer: mode selector (dry-run / step-through), controls |
| `testing/StepOutputEditor.tsx` | Override step output in step-through mode |
| `testing/MockOverrideEditor.tsx` | Per-step mock output editor |

**Skeletons:**
| File | For |
|---|---|
| `skeletons/WorkflowCanvasSkeleton.tsx` | Loading canvas |
| `skeletons/WorkflowListSkeleton.tsx` | Loading list |
| `skeletons/RunTimelineSkeleton.tsx` | Loading run detail |

**Collaboration:**
| File | Responsibility |
|---|---|
| `collaboration/useCanvasCollabSocket.ts` | WebSocket for multi-user cursor positions (reuses `app/collab/`) |
| `collaboration/CollaboratorCursors.tsx` | Other users' cursors on canvas |

**Shared:**
| File | Responsibility |
|---|---|
| `src/features/workflow/types.ts` | TypeScript types for all workflow entities |
| `src/features/workflow/api.ts` | TanStack Query hooks for all workflow API calls |

### Modified Frontend Files

| File | Change |
|---|---|
| `src/app/App.tsx` | Add lazy routes: `/workflows`, `/workflows/:id/edit`, `/workflows/:id/runs`, `/workflow-runs/:runId` |

### Frontend Tests (Phase 4)

| File | Tests |
|---|---|
| `src/features/workflow/__tests__/WorkflowListPage.test.tsx` | Renders list, create button, search (6 cases) |
| `src/features/workflow/__tests__/WorkflowCanvas.test.tsx` | Renders nodes, drag drop, undo/redo, keyboard shortcuts (12 cases) |
| `src/features/workflow/__tests__/useYamlSync.test.ts` | Canvas → YAML, YAML → canvas, invalid YAML shows errors (8 cases) |
| `src/features/workflow/__tests__/ExpressionEngine.test.ts` | Safe expressions, blocked terms, context resolution (10 cases) |
| `e2e/workflow-builder.spec.ts` | Create workflow, add nodes, connect edges, publish, trigger run, see SSE events (15 scenarios) |
| `e2e/workflow-yaml-editor.spec.ts` | Toggle to YAML view, edit, save, toggle back to canvas (8 scenarios) |

### Phase 4 Acceptance Criteria

```bash
cd agent-verse-frontend
npm run typecheck  # 0 errors
npm run test -- --run src/features/workflow/
npx playwright test e2e/workflow-builder.spec.ts e2e/workflow-yaml-editor.spec.ts
```

---

## Phase 5 — HITL Inbox + Template Marketplace Frontend

**Goal:** World-class approval experience. 7 context display types. Mobile-responsive. PWA push. Template marketplace with preview.

### New Frontend Files

**Approvals:**
| File | Responsibility |
|---|---|
| `src/features/workflow/WorkflowTemplatesPage.tsx` | `/workflow-templates` — marketplace grid |
| `src/features/workflow/WorkflowTemplateDetailPage.tsx` | `/workflow-templates/:id` — preview canvas + sample run |
| `src/features/workflow/ApprovalsPage.tsx` | `/approvals` — priority-sorted inbox |
| `src/features/workflow/ApprovalDetailPage.tsx` | `/approvals/:id` — full context + actions |

**HITL Components:**
| File | Responsibility |
|---|---|
| `hitl/ApprovalInbox.tsx` | List: priority sort, SLA countdown, bulk select |
| `hitl/ApprovalDetailView.tsx` | Full context, action buttons, discussion |
| `hitl/HITLContextRenderer.tsx` | Dispatches to correct renderer by `display_type` (7 types: image, json, table, diff, chart, **number**, **list**) |
| `hitl/HITLContextImage.tsx` | Pan-zoomable image viewer |
| `hitl/HITLContextJsonTree.tsx` | Collapsible JSON tree |
| `hitl/HITLContextTable.tsx` | Data table renderer |
| `hitl/HITLContextDiff.tsx` | Before/after diff viewer |
| `hitl/HITLContextChart.tsx` | Recharts renderer |
| `hitl/HITLContextNumber.tsx` | Number display with `threshold_red` / `threshold_yellow` color bands |
| `hitl/HITLContextList.tsx` | Bulleted list renderer for arrays |
| `hitl/HITLCustomForm.tsx` | Dynamic form from `custom_form_schema` |
| `hitl/HITLActionButtons.tsx` | Dynamic action buttons with `requires_note` support |
| `hitl/HITLDiscussion.tsx` | Comment thread between reviewers |
| `hitl/ApprovalBadge.tsx` | Nav badge with real-time count (SSE) |
| `hitl/MobileApprovalCard.tsx` | Touch-optimized card, swipe-to-approve/reject (44px targets) |
| `hitl/usePushNotifications.ts` | Service worker registration for PWA push |
| `hitl/useMagicLinkHandler.ts` | Process JWT token in URL query param |
| `hitl/useApprovalSSE.ts` | SSE subscription for new HITL requests |

**Marketplace:**
| File | Responsibility |
|---|---|
| `marketplace/TemplateGrid.tsx` | Card grid with category filter + tag search |
| `marketplace/TemplateCard.tsx` | complexity badge, connectors, popularity count |
| `marketplace/TemplatePreviewModal.tsx` | Read-only canvas + "Try It" dry-run |
| `marketplace/TemplateInstallModal.tsx` | Name + connector check → fork → open in builder |
| `marketplace/TemplateCategoryNav.tsx` | Category sidebar with counts |

### Modified Frontend Files

| File | Change |
|---|---|
| `src/app/App.tsx` | Add routes: `/approvals`, `/approvals/:id`, `/workflow-templates`, `/workflow-templates/:id` |
| `src/app/Navigation.tsx` (or equivalent) | Add `ApprovalBadge` to nav |

### Frontend Tests (Phase 5)

| File | Tests |
|---|---|
| `src/features/workflow/__tests__/ApprovalInbox.test.tsx` | Renders items, priority sort, bulk select, SLA countdown (8 cases) |
| `src/features/workflow/__tests__/HITLContextRenderer.test.tsx` | Each display_type renders correct component (7 cases) |
| `src/features/workflow/__tests__/MobileApprovalCard.test.tsx` | Touch targets ≥44px, swipe gesture (4 cases) |
| `e2e/approvals.spec.ts` | View inbox, approve with note, delegate, escalate, magic link flow (12 scenarios) |
| `e2e/template-marketplace.spec.ts` | Browse, preview, try sample run, fork, opens in builder (10 scenarios) |

### Phase 5 Acceptance Criteria

```bash
npm run typecheck
npm run test -- --run src/features/workflow/__tests__/Approval
npx playwright test e2e/approvals.spec.ts e2e/template-marketplace.spec.ts
```

---

## Phase 6 — Settings, Analytics, Testing UI Frontend

**Goal:** Full workflow settings panel. Analytics dashboard. Step-through test runner UI. Version history with YAML diff.

### New Frontend Files

**Settings:**
| File | Responsibility |
|---|---|
| `src/features/workflow/WorkflowSettingsPage.tsx` | `/workflows/:id/settings` — 6-panel settings page |
| `settings/WorkflowPermissionsPanel.tsx` | RBAC: grant/revoke per-user or per-role |
| `settings/WorkflowSecretsPanel.tsx` | List `{{vault://X}}` references (names only, no values) |
| `settings/WorkflowEnvVarsPanel.tsx` | `{{env.X}}` key-value editor |
| `settings/WorkflowNotificationsPanel.tsx` | Per-event notification rules |
| `settings/WorkflowConcurrencyPanel.tsx` | Max concurrent runs + queue behavior |
| `settings/WorkflowWebhookPanel.tsx` | Webhook URL, token rotation, DLQ viewer |

**Analytics:**
| File | Responsibility |
|---|---|
| `src/features/workflow/WorkflowAnalyticsPage.tsx` | `/workflows/:id/analytics` |
| `analytics/RunSuccessRateChart.tsx` | Line chart: success rate over time |
| `analytics/StepFailureHeatmap.tsx` | Grid: step_id × date → failure rate |
| `analytics/CostTrendChart.tsx` | Area chart: daily cost by step type |
| `analytics/AvgDurationChart.tsx` | Bar chart: avg duration per step |
| `analytics/WorkflowOverviewStats.tsx` | Stat cards: total runs, cost this month, pending HITL |
| `src/features/workflow/GlobalActivityPage.tsx` | `/workflows/activity` — tenant-wide run feed |

**Version History:**
| File | Responsibility |
|---|---|
| `versions/VersionHistoryPanel.tsx` | Slide-out: all versions with rollback button |
| `versions/VersionDiffModal.tsx` | Side-by-side YAML diff (monaco diff editor) |
| `versions/RollbackConfirmDialog.tsx` | Confirm with impact warning |

**Test Runner (complete):**
| File | Responsibility |
|---|---|
| `testing/TestScenarioManager.tsx` | Create/run/view saved test scenarios |
| `testing/ScenarioAssertionBuilder.tsx` | Define expected outputs + branches |
| `testing/TestResultSummary.tsx` | Pass/fail per scenario with diff view |

### Modified Frontend Files

| File | Change |
|---|---|
| `src/app/App.tsx` | Add routes: `/workflows/:id/settings`, `/workflows/:id/analytics`, `/workflows/activity` |

### Frontend Tests (Phase 6)

| File | Tests |
|---|---|
| `src/features/workflow/__tests__/WorkflowAnalyticsPage.test.tsx` | Charts render, stat cards correct (6 cases) |
| `src/features/workflow/__tests__/VersionHistoryPanel.test.tsx` | List versions, diff modal, rollback confirm (6 cases) |
| `src/features/workflow/__tests__/TestRunnerPanel.test.tsx` | Step-through advance, output override, scenario assertions (8 cases) |
| `e2e/workflow-settings.spec.ts` | RBAC grant/revoke, webhook rotation, concurrency change (8 scenarios) |
| `e2e/workflow-analytics.spec.ts` | Charts render, activity feed, global stats (6 scenarios) |

### Phase 6 Acceptance Criteria

```bash
npm run typecheck  # 0 errors
npm run test -- --run src/features/workflow/
npx playwright test e2e/workflow-*.spec.ts
# All green
```

---

## Final Acceptance: Full Suite

```bash
# Backend
cd agent-verse-backend
uv run ruff check app/workflow/
uv run mypy app/workflow/
uv run pytest tests/workflow/ -q --no-cov
# Target: ~300 tests, all green

# Frontend
cd agent-verse-frontend
npm run typecheck
npm run test -- --run src/features/workflow/
npx playwright test e2e/workflow-*.spec.ts e2e/approvals.spec.ts e2e/template-marketplace.spec.ts
# Target: ~100 unit + ~80 E2E scenarios, all green

# Regression: existing tests must still pass
cd agent-verse-backend
uv run pytest tests/ --ignore=tests/real_e2e --ignore=tests/integration -q --no-cov
# Must be same pass count as before this feature branch
```

---

## Dependency Order (Critical Path)

```
Phase 1 (core backend)
  ↓
Phase 2 (HITL extension + templates)     Phase 4 (visual builder frontend)
  ↓                                           ↓
Phase 3 (extra triggers, versions, SDK)  Phase 5 (HITL inbox + marketplace frontend)
                                              ↓
                                         Phase 6 (settings, analytics, testing UI)
```

Phases 2 and 4 can start in parallel after Phase 1 completes.
Phases 3 and 5 depend on 2 and 4 respectively.
Phase 6 depends on Phase 5.

---

## Key Reuse Patterns

```python
# Pattern 1: Step node reuses existing services (same interface as Goals Engine)
class ToolStepNode:
    async def execute(self, state):
        result = await self.mcp_client.call_tool(tool_name, inputs)  # same MCPClient

# Pattern 2: HITL suspend/resume reuses Redis BLPOP pattern from HITLGateway
class HITLStepNode:
    async def execute(self, state):
        if state["hitl_request_id"] == self.step.id:
            return self._process_decision(state)           # resuming
        request_id = await self.hitl_gateway.create_approval(...)
        return {"status": "waiting_hitl", "hitl_request_id": self.step.id}  # suspending

# Pattern 3: LangGraph compiler follows exact same pattern as app/agent/graph.py
def compile(self, definition):
    graph = StateGraph(WorkflowState)
    for step in definition.steps:
        node_fn = StepTypeRegistry.get(step.type)(step, ...).execute
        graph.add_node(step.id, node_fn)
    return graph.compile(checkpointer=app.state.langgraph_checkpointer)  # SAME checkpointer

# Pattern 4: Celery task routing reuses PLAN_QUEUE_MAP pattern
@celery_app.task(queue=f"workflows.{plan_tier}")
async def execute_workflow_run(run_id, workflow_id, tenant_id): ...
```
