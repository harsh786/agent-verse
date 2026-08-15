# Workflow Automation Engine — Architecture Specification

**Date:** 2026-08-15  
**Status:** Approved for Implementation  
**Feature:** Predefined Workflow Automation Engine  
**Scope:** Backend (Python/FastAPI/LangGraph) + Frontend (React 19/TypeScript)

---

## Overview

The **Workflow Automation Engine** is a new first-class feature of AgentVerse that lets users and operators define, manage, test, and execute multi-step business workflows with a fixed, declared step structure.

It **complements** — and does not replace — the existing Goals Engine:

| | Goals Engine | Workflow Engine |
|---|---|---|
| **Step definition** | LLM plans steps at runtime | User defines steps at design time |
| **Structure** | Dynamic — every run may differ | Fixed — same structure every run |
| **Persona** | Autonomous AI agent | Reliable business process automator |
| **Best for** | Open-ended, exploratory tasks | Repeatable, auditable business operations |
| **LLM role** | Plans AND executes | Executes *within* predefined steps only |

Both engines share the same underlying infrastructure: `LLMProvider`, `MCPClient`, `KnowledgeStore`, `HITLGateway`, Redis checkpointer, SSE broadcaster, Celery queues, and `AuditLog`.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **`code` step sandboxing** | Delegates to `app/execution_environment/` (already exists) | Python/JS code steps run in existing sandbox: no fs/network, 30s CPU, 128MB RAM |
| **Data retention + GDPR** | `workflow_runs_retention_days` per tenant + cleanup Celery task | Step results containing PII have configurable TTL; GDPR erasure covers run data |
| **Webhook HMAC replay protection** | `X-Timestamp` + 5-minute window on HMAC webhooks | Prevents replay attacks on webhook ingress |
| **Max payload size** | 1MB input limit per trigger, 5MB per step output, S3 offload for large | Protects DB; large payloads stored in object storage, referenced by URL |
| **Definition publishing approval** | Optional `requires_approval: true` flag on `workflow_definitions` | Enterprise/regulated tenants require 2-person approval before a workflow goes live |
| **Custom step type plugins** | `BaseStepNode` protocol + `StepTypeRegistry` | Third parties register custom step types without forking the engine |
| **Workflow variables** | `vars:` section in DSL + `set_variable`/`get_variable` step types | Mutable state across steps; not tied to step outputs |
| **Auto-audit trail** | Framework auto-emits audit events for every step transition | Audit is a framework concern, not a step concern; no manual `audit.record` steps needed |
| **Trigger transform** | `trigger_transform:` DSL section | Reshape incoming trigger payload before it becomes workflow `inputs` |
| **Callback URL** | `callback:` DSL section | POST final outputs to a URL on run completion (async callers) |
| **Run labels/metadata** | `workflow_runs.labels JSONB`, `workflow_runs.run_metadata JSONB` | Filter runs by label; attach arbitrary context to a run |
| **Operator pause/resume** | `POST /workflow-runs/{id}/pause` + `resume` | Admin operational control distinct from HITL |
| **`foreach` progress tracking** | `WorkflowState.foreach_progress` dict | Track N/total iterations; frontend shows "5/25 items" |
| **Workflow definition format** | YAML (primary) + JSON (API-equivalent) | YAML for humans; JSON for API/SDK; UI generates both |
| **Execution engine** | LangGraph `StateGraph` — compiled from YAML at deploy time | Reuses existing LangGraph infra; checkpointing + SSE work unchanged |
| **YAML ↔ canvas sync** | Bidirectional — canvas generates YAML; YAML renders as canvas | No source-of-truth split; YAML is always canonical |
| **Parallel steps** | `asyncio.gather()` fan-out, merged into `WorkflowState` | Native Python concurrency; no external orchestration needed |
| **HITL transport** | Extend existing `HITLGateway` Redis BLPOP path | Already cross-replica; zero new infrastructure |
| **Template storage** | `system_workflow_templates` (seeded, read-only) + `workflow_definitions` (tenant copies) | Templates are versioned; forks track parent version |
| **Visual builder** | React Flow (Xyflow) canvas + right-panel config | Industry standard; used by n8n, Retool |
| **Test runner** | Separate `WorkflowTestRunner` with mock tool adapters | Dry runs never touch real MCP tools or send real emails |
| **Context propagation** | `{{steps.STEP_ID.output.FIELD}}` — Jinja2-style, resolved per-step | Predictable, inspectable, cacheable |
| **Secret references** | `{{vault://SECRET_NAME}}` — resolved by `VaultClient` at execution time | Secrets never appear in YAML/DB |
| **Multi-tenant isolation** | Postgres RLS on all workflow tables (matches existing pattern) | Zero trust; same pattern as goals, chat, knowledge |
| **Celery queuing** | Per-plan queue routing: `workflows.free / starter / professional / enterprise` | Reuses existing queue routing; enterprise tenants isolated |

---

## Backend Package Structure

```
app/workflow/
  __init__.py
  models.py            # SQLAlchemy ORM + Pydantic schemas
  dsl.py               # WorkflowDefinition Pydantic model (YAML ↔ JSON parser/validator)
  compiler.py          # WorkflowCompiler: DSL → LangGraph StateGraph
  runner.py            # WorkflowRunner: execute compiled graph, emit SSE events
  state.py             # WorkflowState TypedDict
  steps/
    __init__.py
    base.py            # BaseStepNode protocol
    tool_step.py       # type: tool  — delegates to MCPClient
    llm_step.py        # type: llm   — delegates to LLMProvider + optional RAG
    rag_step.py        # type: rag   — delegates to KnowledgeStore
    http_step.py       # type: http  — httpx with vault credential injection + SSRF guard
    security.py        # SSRFGuard: IP/hostname blocklist validation for http steps
                       # SecretMasker: redact vault-resolved values before DB persistence
    hitl_step.py       # type: hitl  — delegates to HITLGateway (extended)
    parallel_step.py   # type: parallel — asyncio.gather() fan-out
    conditional_step.py# type: conditional — expression evaluator → branch routing
    transform_step.py  # type: transform — pure data mapping, no AI/IO
    sub_workflow_step.py# type: sub_workflow — recursive WorkflowRunner.run()
    wait_step.py       # type: wait  — timer / event-gate
  context.py           # ContextResolver: {{steps.N.output.field}} + {{vault://X}}
  template_store.py    # SystemTemplateStore: read system templates
  hitl_extension.py    # HITLWorkflowGateway: extends governance/hitl.py
  test_runner.py       # WorkflowTestRunner: dry-run / step-through / mock-override
  router.py            # FastAPI router — all workflow API endpoints
  router_hitl.py       # FastAPI router — HITL approval inbox endpoints
  router_templates.py  # FastAPI router — template marketplace endpoints
  router_runs.py       # FastAPI router — run history, replay, logs endpoints
  celery_tasks.py      # Celery tasks: execute_workflow_run, resume_hitl_workflow
  otel.py             # OpenTelemetry: workflow run span, per-step child spans
  nl_trigger.py       # NLTriggerResolver: IntentRouter → workflow.run() dispatch
  registry.py         # StepTypeRegistry: register built-in + custom step types (PLUGIN SYSTEM)
  variables.py        # WorkflowVariableStore: set/get mutable variables within a run
  audit_middleware.py # AutoAuditMiddleware: auto-emit AuditEvent on every step transition

app/db/models/workflow.py  # Alembic migration models

tests/workflow/
  test_dsl.py
  test_compiler.py
  test_runner.py
  test_context.py
  test_hitl_extension.py
  test_template_store.py
  test_test_runner.py
  test_router.py
  test_router_hitl.py
  test_router_templates.py
  test_steps/
    test_tool_step.py
    test_llm_step.py
    test_http_step.py
    test_hitl_step.py
    test_parallel_step.py
    test_conditional_step.py
```

---

## DSL Specification

### Full YAML Schema

```yaml
# ─────────────────────────────────────────────────────
# Workflow Definition — canonical YAML format
# JSON equivalent accepted on all API endpoints
# ─────────────────────────────────────────────────────

id: kyc-automation                      # globally unique per tenant (or system template id)
name: KYC Document Verification
version: "1.2.0"                        # SemVer; bumped on publish
description: "Automate KYC verification with OCR, fraud detection, and HITL review"
tags: [compliance, identity, kyc]
forked_from: null                       # system-template-id:version when forked

# ── TRIGGER ──────────────────────────────────────────
trigger:
  type: webhook                         # webhook | schedule | api | nl | event
  
  # webhook trigger (reuses TriggerType.WEBHOOK from app/triggers/models.py)
  webhook:
    path: /webhooks/workflows/kyc       # mounted at /api/v1/webhooks/...
    auth: bearer                        # bearer | hmac | none
    hmac_secret: "{{vault://KYC_WEBHOOK_SECRET}}"
    payload_schema:                     # JSON Schema for validation at ingress
      document_url: { type: string }
      customer_id:  { type: string }
  
  # schedule trigger (reuses TriggerType.CRON)
  schedule:
    cron: "0 2 * * *"
    timezone: "Asia/Kolkata"
  
  # event trigger (reuses TriggerType.EVENT / Redis pub/sub)
  event:
    channel: "tenant.{tenant_id}.kyc_requested"
    filter: "payload.priority == 'high'"

# ── INPUTS ───────────────────────────────────────────
inputs:
  document_url:
    type: string
    required: true
    description: "Publicly accessible URL or base64-encoded document"
  customer_id:
    type: string
    required: true
  document_type:
    type: string
    required: false
    default: null
    enum: [pan, aadhaar, passport, driving_license, voter_id, null]

# ── STEPS ────────────────────────────────────────────
steps:

  # ── type: tool ──────────────────────────────────
  - id: ingest
    name: Extract Document Text
    type: tool
    tool: ocr.extract_document           # any MCP-registered tool
    input:
      document_url: "{{inputs.document_url}}"
    output_schema:                       # optional: validate output shape
      raw_text: string
      document_type: string
      fields: object
      confidence: number
    timeout: 30s
    retry:
      max_attempts: 3
      backoff: exponential               # fixed | linear | exponential
      base_delay_ms: 500
    on_failure: pause                    # pause | skip | abort | use_default
    on_failure_default: null             # value to inject into output if on_failure=use_default

  # ── type: llm ───────────────────────────────────
  - id: classify
    name: Classify Document Type
    type: llm
    depends_on: [ingest]
    model: null                          # null = use tenant default model
    prompt: |
      Given the following document text, classify it as one of:
      PAN, Aadhaar, Passport, Driving License, Voter ID.
      
      Document text:
      {{steps.ingest.output.raw_text}}
      
      Hint from user: {{inputs.document_type}}
      
      Return JSON: {"document_type": "<type>", "confidence": 0.0}
    output_schema:
      document_type: string
      confidence: number
    temperature: 0.1
    max_tokens: 200

  # ── type: llm + rag ─────────────────────────────
  - id: extract_fields
    name: Extract Identity Fields
    type: llm
    depends_on: [classify]
    rag:
      collection: kyc-field-schemas      # KnowledgeStore collection name
      top_k: 3
      strategy: hybrid                   # semantic | keyword | hybrid
    prompt: |
      Extract all identity fields from this {{steps.classify.output.document_type}} document.
      Use the schema reference below from our knowledge base.
      
      Document: {{steps.ingest.output.raw_text}}
      
      Return JSON with all available fields: name, dob, id_number, address, expiry.
      Missing fields should be null.
    output_schema:
      name: string
      dob: string
      id_number: string
      address: string
      expiry: string

  # ── type: parallel ──────────────────────────────
  - id: risk_checks
    name: Parallel Risk Assessment
    type: parallel
    depends_on: [extract_fields]
    branches:

      - id: fraud_scan
        type: llm
        rag:
          collection: fraud-patterns
          top_k: 5
        prompt: |
          Analyse these extracted fields for fraud signals.
          Customer ID: {{inputs.customer_id}}
          Fields: {{steps.extract_fields.output}}
          Known fraud patterns from knowledge base are provided in context.
          Return: {"risk_score": 0.0, "risk_factors": []}
        output_schema:
          risk_score: number
          risk_factors: array

      - id: sanctions_check
        type: http
        url: "https://api.sanctions-provider.com/v2/check"
        method: POST
        auth:
          type: bearer
          token: "{{vault://SANCTIONS_API_KEY}}"
        headers:
          Content-Type: application/json
          X-Request-ID: "{{workflow.run_id}}"
        body:
          name: "{{steps.extract_fields.output.name}}"
          dob: "{{steps.extract_fields.output.dob}}"
          id_number: "{{steps.extract_fields.output.id_number}}"
        timeout: 10s
        output_schema:
          match: boolean
          match_score: number
          match_details: object

  # ── type: conditional ───────────────────────────
  - id: decision_gate
    name: Auto / Manual Decision
    type: conditional
    depends_on: [risk_checks]
    branches:
      - condition: "{{steps.fraud_scan.output.risk_score}} > 0.8 and not {{steps.sanctions_check.output.match}}"
        next: auto_approve
      - condition: "{{steps.fraud_scan.output.risk_score}} >= 0.4 and {{steps.fraud_scan.output.risk_score}} <= 0.8"
        next: human_review
      - condition: "{{steps.sanctions_check.output.match}} == true"
        next: auto_reject
      - condition: default                # fallthrough — must be last
        next: auto_reject

  # ── type: hitl ──────────────────────────────────
  - id: human_review
    name: KYC Officer Manual Review
    type: hitl
    depends_on: [decision_gate]
    assignee:
      role: kyc_officer                  # must match tenant role definitions
      strategy: round_robin              # round_robin | least_busy | specific | skill_based
      specific_user: null                # set when strategy=specific
    timeout: 48h
    timeout_action: escalate             # auto_approve | auto_reject | escalate | pause
    escalation:
      after: 24h
      to_role: kyc_supervisor
      notify_channels: [slack, email]
    context:                             # rich context shown to reviewer
      - label: "Identity Document"
        value: "{{inputs.document_url}}"
        display_type: image              # image | json | table | diff | number | text | list | chart
      - label: "Risk Score"
        value: "{{steps.fraud_scan.output.risk_score}}"
        display_type: number
        threshold_red: 0.7
        threshold_yellow: 0.4
      - label: "Risk Factors"
        value: "{{steps.fraud_scan.output.risk_factors}}"
        display_type: list
      - label: "Extracted Fields"
        value: "{{steps.extract_fields.output}}"
        display_type: json
      - label: "Sanctions Result"
        value: "{{steps.sanctions_check.output}}"
        display_type: json
    actions:
      - id: approve
        label: "Approve KYC"
        style: success                   # success | danger | warning | default
        icon: "✓"
        next: auto_approve
      - id: reject
        label: "Reject"
        style: danger
        icon: "✗"
        next: auto_reject
        requires_note: true              # blocks action until reviewer writes a reason
      - id: request_docs
        label: "Request More Documents"
        style: warning
        icon: "📁"
        next: notify_request_docs
    allow_delegate: true                 # reviewer can reassign to a colleague
    allow_escalate: true

  # ── type: tool (CRM update) ─────────────────────
  - id: auto_approve
    name: Mark Customer Approved
    type: tool
    depends_on: [decision_gate, human_review]
    depends_on_any: true                 # run if ANY predecessor completes (not all)
    tool: crm.update_status
    input:
      customer_id: "{{inputs.customer_id}}"
      status: approved
      kyc_confidence: "{{steps.extract_fields.output.confidence}}"
      verified_at: "{{workflow.now_iso}}"

  - id: auto_reject
    name: Mark Customer Rejected
    type: tool
    depends_on: [decision_gate, human_review]
    depends_on_any: true
    tool: crm.update_status
    input:
      customer_id: "{{inputs.customer_id}}"
      status: rejected

  # ── type: parallel (notify) ─────────────────────
  - id: notifications
    name: Send Notifications
    type: parallel
    depends_on: [auto_approve, auto_reject]
    depends_on_any: true
    branches:
      - id: email_notify
        type: tool
        tool: email.send
        input:
          customer_id: "{{inputs.customer_id}}"
          template: kyc_result
          data:
            decision: "{{workflow.completed_branch}}"
            risk_score: "{{steps.fraud_scan.output.risk_score}}"
      - id: audit_record
        type: tool
        tool: audit.record
        input:
          entity_type: kyc_verification
          entity_id: "{{inputs.customer_id}}"
          workflow_run_id: "{{workflow.run_id}}"
          result: "{{workflow.completed_branch}}"

# ── OUTPUTS ──────────────────────────────────────────
outputs:
  kyc_status:
    from: "{{workflow.completed_branch}}"
  confidence:
    from: "{{steps.extract_fields.output.confidence}}"
  risk_score:
    from: "{{steps.fraud_scan.output.risk_score}}"
  customer_id:
    from: "{{inputs.customer_id}}"

# ── ERROR HANDLING ────────────────────────────────────
error_handling:
  on_step_failure: pause                 # global default — pause | skip | abort
  max_run_duration: 72h                  # force-abort after this wall-clock time
  notify_on_failure:
    - channel: slack
      target: "#workflow-alerts"
    - channel: email
      target: "{{workflow.tenant_admin_email}}"

# ── STEP-LEVEL ERROR TYPE ROUTING ────────────────────
# Per-step override: which exceptions trigger retry vs. immediate failure
# (Applied inside each step's on_failure handler)
#
# retry_on:   list of error_codes / exception class names that are transient
# fail_on:    list that are terminal — skip retry, go directly to on_failure action
#
# Example on any step:
#   retry:
#     max_attempts: 3
#     backoff: exponential
#     retry_on: [ConnectionError, TimeoutError, RateLimitError]
#     fail_on:  [ValidationError, AuthenticationError, NotFoundError]
```

# ── STEP-LEVEL ERROR TYPE ROUTING ────────────────────
# Per-step override: which exceptions trigger retry vs. immediate failure
# (Applied inside each step's on_failure handler)
#
# retry_on:   list of error_codes / exception class names that are transient
# fail_on:    list that are terminal — skip retry, go directly to on_failure action
#
# Example on any step:
#   retry:
#     max_attempts: 3
#     backoff: exponential
#     retry_on: [ConnectionError, TimeoutError, RateLimitError]
#     fail_on:  [ValidationError, AuthenticationError, NotFoundError]

# ── WORKFLOW VARIABLES ────────────────────────────────
# Mutable variables that live for the duration of a run.
# Unlike step outputs (immutable), variables CAN be overwritten.
# Set with type: set_variable, read via {{vars.VAR_NAME}}
vars:
  accumulated_total: 0        # initial value (optional — can be set by set_variable step)
  last_decision: null

# ── TRIGGER TRANSFORM ─────────────────────────────────
# Reshape the raw trigger payload → workflow inputs BEFORE step execution begins.
# Uses the same {{...}} context syntax — {{trigger.FIELD}} refers to raw payload.
# Applied once on ingress; result becomes {{inputs.FIELD}} for all steps.
trigger_transform:
  document_url: "{{trigger.data.file_location}}"
  customer_id:  "{{trigger.metadata.user_id}}"

# ── CALLBACK URL ──────────────────────────────────────
# POST the final workflow outputs to this URL when the run completes.
# Useful for async callers who trigger a run and don't want to poll or stream.
# Body: { "run_id": "...", "status": "complete|failed", "outputs": {...} }
callback:
  url: "{{inputs.callback_url}}"    # or hardcoded URL
  auth:
    type: bearer
    token: "{{vault://CALLBACK_SECRET}}"
  on_failure: true                  # also POST on run failure

# ── RUN LABELS ────────────────────────────────────────
# Arbitrary key-value labels attached to every run of this workflow.
# Also overridable per-run-trigger via the API: POST /workflows/{id}/run { labels: {...} }
# Queryable: GET /workflow-runs?label.environment=prod
run_labels:
  environment: production
  domain: compliance
  owner: "{{workflow.tenant_id}}"

  # ── type: foreach ──────────────────────────────
  # MISSING FROM ORIGINAL — critical for batch operations
  - id: verify_employers
    name: Verify Each Employer
    type: foreach
    depends_on: [parse_resume]
    iterate_over: "{{steps.parse_resume.output.experience}}"  # must resolve to an array
    as: current_employer                                       # name of loop variable
    max_concurrency: 3                                         # run up to 3 iterations in parallel
    body:
      - id: verify_employer
        type: http
        url: "https://hr-verify-api.example.com/check"
        method: POST
        input:
          company: "{{foreach.current_employer.company}}"
          from_date: "{{foreach.current_employer.from_date}}"
          to_date: "{{foreach.current_employer.to_date}}"
        output_schema:
          verified: boolean
          discrepancies: array
    collect_output_as: employer_verifications    # array of each iteration's output
    on_item_failure: continue                    # continue | abort — continue collects error per item

  # ── type: code ──────────────────────────────────
  # MISSING FROM ORIGINAL — run sandboxed Python/JS for transforms too complex for {{}}
  - id: compute_risk_tier
    name: Compute Risk Tier
    type: code
    depends_on: [fraud_scan, credit_bureau]
    runtime: python                              # python | javascript
    timeout: 10s
    code: |
      score = inputs["fraud_scan"]["risk_score"]
      credit = inputs["credit_bureau"]["credit_score"]
      
      if credit > 750 and score < 0.3:
          tier = "A"
          rate = 8.5
      elif credit > 650 and score < 0.5:
          tier = "B"
          rate = 11.0
      else:
          tier = "C"
          rate = 14.5
      
      return {"risk_tier": tier, "interest_rate": rate}
    input:
      fraud_scan: "{{steps.fraud_scan.output}}"
      credit_bureau: "{{steps.credit_bureau.output}}"
    output_schema:
      risk_tier: string
      interest_rate: number

# ── CONCURRENCY CONTROL ───────────────────────────────
concurrency:
  max_concurrent_runs: 10         # max simultaneous runs of this workflow (per tenant)
  on_limit_reached: queue         # queue | reject | replace_oldest
  queue_timeout: 30m              # how long a queued run waits before timing out

# ── NOTIFICATIONS ─────────────────────────────────────
notifications:
  - event: run.started
    channels: []                  # silent by default
  - event: run.completed
    channels: [slack]
    target: "#workflow-runs"
  - event: run.failed
    channels: [slack, email]
    target: "{{workflow.tenant_admin_email}}"
  - event: hitl.requested
    channels: [slack, email, push]
    target: "{{hitl.assignee_email}}"
  - event: hitl.escalated
    channels: [slack, email, push]
    target: "{{hitl.escalation_email}}"
  - event: hitl.overdue
    channels: [slack]
    target: "#workflow-alerts"

# ── ENVIRONMENT VARIABLES ─────────────────────────────
# Tenant-level config values — not secrets, just config. Referenced via {{env.VAR_NAME}}
env:
  SANCTIONS_API_BASE: "https://sanctions.example.com/v2"
  DEFAULT_RISK_THRESHOLD: "0.7"
  NOTIFY_EMAIL: "compliance@mycompany.com"

### Context Variable Reference

Every template expression `{{...}}` is resolved by `ContextResolver` before a step executes:

| Variable | Resolves To |
|---|---|
| `{{inputs.FIELD}}` | Workflow input value |
| `{{steps.STEP_ID.output.FIELD}}` | Output field of a completed step |
| `{{steps.STEP_ID.output}}` | Full output dict of a completed step (as JSON) |
| `{{workflow.run_id}}` | UUID of the current run |
| `{{workflow.tenant_id}}` | Tenant UUID |
| `{{workflow.now_iso}}` | Current UTC timestamp ISO 8601 |
| `{{workflow.now_unix}}` | Current UTC timestamp Unix epoch |
| `{{workflow.trigger_type}}` | How this run was triggered |
| `{{workflow.trigger_payload.FIELD}}` | Field from raw trigger payload |
| `{{workflow.completed_branch}}` | ID of the branch taken at last conditional |
| `{{workflow.tenant_admin_email}}` | Tenant admin contact (from tenant record) |
| `{{vault://SECRET_NAME}}` | Secret from `VaultClient` — never stored in DB |
| `{{env.VAR_NAME}}` | Workflow environment variable (non-secret config) |
| `{{foreach.VARIABLE_NAME}}` | Current item in a `foreach` loop iteration |
| `{{foreach.index}}` | Zero-based index of current loop iteration |
| `{{foreach.total}}` | Total number of items in the loop |

### Conditional Expression Language (Security-Critical)

Conditional branches use **expressions** — but these **must never be `eval()`-based**. The expression language is a safe, sandboxed subset:

```python
# app/workflow/expression_engine.py
# Uses: simpleeval (safe Python expression evaluator, no imports, no builtins)

SAFE_FUNCTIONS = {
    "len": len, "str": str, "int": int, "float": float,
    "bool": bool, "lower": str.lower, "upper": str.upper,
    "contains": lambda s, sub: sub in s,
    "startswith": str.startswith, "endswith": str.endswith,
    "round": round, "abs": abs, "min": min, "max": max,
}

class ExpressionEngine:
    """
    Evaluates conditional branch expressions safely.
    
    Supported:
      - Arithmetic:   +, -, *, /, %, //
      - Comparison:   ==, !=, >, <, >=, <=
      - Logical:      and, or, not
      - Membership:   in, not in
      - Ternary:      X if COND else Y
      - Functions:    len(), str(), int(), contains(), etc.
      - Context vars: {{steps.N.output.field}} resolved BEFORE eval
    
    Forbidden (raises ExpressionSecurityError):
      - import, __import__, exec, eval, open, os, sys, subprocess
      - Attribute access on unknown objects
      - Any call not in SAFE_FUNCTIONS
    """
    
    def evaluate(self, expression: str, context: dict[str, Any]) -> bool:
        resolved = self.context_resolver.resolve_all(expression, context)
        try:
            result = simpleeval.simple_eval(resolved, functions=SAFE_FUNCTIONS)
            return bool(result)
        except simpleeval.FeatureNotAvailable as e:
            raise ExpressionSecurityError(f"Unsafe expression: {e}") from e
```

---

## Data Model

### New Tables (Alembic migration: `app/db/models/workflow.py`)

```sql
-- ─────────────────────────────────────────────────────────────────
-- system_workflow_templates
-- Seeded by AgentVerse team. Read-only for tenants.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE system_workflow_templates (
    id              TEXT        PRIMARY KEY,        -- e.g. "kyc-automation"
    name            TEXT        NOT NULL,
    category        TEXT        NOT NULL,           -- compliance | finance | devops | hr | ...
    subcategory     TEXT,
    description     TEXT,
    complexity      TEXT        NOT NULL,           -- beginner | intermediate | advanced
    tags            TEXT[]      DEFAULT '{}',
    required_connectors TEXT[]  DEFAULT '{}',
    optional_connectors TEXT[]  DEFAULT '{}',
    definition_yaml TEXT        NOT NULL,           -- canonical YAML source
    definition_json JSONB       NOT NULL,           -- pre-parsed for fast reads
    version         TEXT        NOT NULL DEFAULT '1.0.0',
    preview_image_url TEXT,
    sample_input    JSONB,
    sample_output   JSONB,
    popularity      INT         DEFAULT 0,          -- incremented on each fork/install
    is_active       BOOL        DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_system_templates_category ON system_workflow_templates (category, is_active);
CREATE INDEX idx_system_templates_tags     ON system_workflow_templates USING gin (tags);


-- ─────────────────────────────────────────────────────────────────
-- workflow_definitions
-- Tenant-owned workflow definitions (forked from templates or created from scratch)
-- RLS: tenant_id = current_setting('app.tenant_id')
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_definitions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    name            TEXT        NOT NULL,
    slug            TEXT        NOT NULL,           -- URL-safe identifier (unique per tenant)
    description     TEXT,
    tags            TEXT[]      DEFAULT '{}',
    definition_yaml TEXT        NOT NULL,
    definition_json JSONB       NOT NULL,
    version         TEXT        NOT NULL DEFAULT '1.0.0',
    status          TEXT        NOT NULL DEFAULT 'draft',  -- draft | published | archived
    forked_from_template_id   TEXT REFERENCES system_workflow_templates(id),
    forked_from_template_ver  TEXT,
    is_template     BOOL        DEFAULT false,       -- tenant-published template for their org
    trigger_config  JSONB       NOT NULL,
    created_by      UUID        NOT NULL,
    updated_by      UUID        NOT NULL,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    UNIQUE (tenant_id, slug)
);

ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY workflow_definitions_tenant ON workflow_definitions
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_definitions_tenant ON workflow_definitions (tenant_id, status, updated_at DESC);
CREATE INDEX idx_wf_definitions_tags   ON workflow_definitions USING gin (tags);


-- ─────────────────────────────────────────────────────────────────
-- workflow_definition_versions
-- Immutable version history — one row per published version
-- Enables rollback to any previous published version
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_definition_versions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID        NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL,
    version         TEXT        NOT NULL,
    definition_yaml TEXT        NOT NULL,
    definition_json JSONB       NOT NULL,
    change_summary  TEXT,                           -- user-written or auto-generated changelog
    published_by    UUID        NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (workflow_id, version)
);

ALTER TABLE workflow_definition_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_def_versions_tenant ON workflow_definition_versions
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_def_versions ON workflow_definition_versions (workflow_id, published_at DESC);


-- ─────────────────────────────────────────────────────────────────
-- workflow_permissions
-- Workflow-level RBAC — beyond tenant-level RLS
-- Allows sharing a workflow with specific users or roles within a tenant
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_permissions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID        NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL,
    subject_type    TEXT        NOT NULL,           -- user | role
    subject_id      TEXT        NOT NULL,           -- user UUID or role name
    permission      TEXT        NOT NULL,           -- view | run | edit | admin
    granted_by      UUID        NOT NULL,
    granted_at      TIMESTAMPTZ DEFAULT now(),

    UNIQUE (workflow_id, subject_type, subject_id, permission)
);

ALTER TABLE workflow_permissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_permissions_tenant ON workflow_permissions
    USING (tenant_id = current_setting('app.tenant_id')::uuid);


-- ─────────────────────────────────────────────────────────────────
-- workflow_webhook_events
-- Dead Letter Queue for failed webhook deliveries
-- Ensures no trigger is silently lost on transient failures
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_webhook_events (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    workflow_id     UUID        NOT NULL REFERENCES workflow_definitions(id),
    webhook_token   TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    headers         JSONB,
    status          TEXT        NOT NULL DEFAULT 'pending',
    -- pending | processing | complete | failed | dead_lettered
    attempts        INT         DEFAULT 0,
    last_error      TEXT,
    run_id          UUID REFERENCES workflow_runs(id),
    received_at     TIMESTAMPTZ DEFAULT now(),
    last_attempted_at TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

ALTER TABLE workflow_webhook_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_webhook_events_tenant ON workflow_webhook_events
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_webhook_pending ON workflow_webhook_events (status, received_at)
    WHERE status IN ('pending', 'failed');


-- ─────────────────────────────────────────────────────────────────
-- workflow_runs
-- One row per execution of a workflow_definition
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    workflow_id     UUID        NOT NULL REFERENCES workflow_definitions(id),
    trigger_type    TEXT        NOT NULL,           -- webhook | schedule | api | nl | test
    trigger_payload JSONB,
    inputs          JSONB       NOT NULL DEFAULT '{}',
    status          TEXT        NOT NULL DEFAULT 'pending',
    -- pending | running | waiting_hitl | complete | failed | cancelled | timed_out
    current_step_id TEXT,
    outputs         JSONB       DEFAULT '{}',
    error           TEXT,
    cost_usd        DECIMAL(12,6) DEFAULT 0,
    tokens_used     INT         DEFAULT 0,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),

    -- test run fields
    is_test_run     BOOL        DEFAULT false,
    test_scenario_id UUID,

    -- operator control
    paused_by       UUID,
    paused_at       TIMESTAMPTZ,
    pause_reason    TEXT,

    -- labels for filtering (from run_labels DSL + per-trigger overrides)
    labels          JSONB       DEFAULT '{}',
    run_metadata    JSONB       DEFAULT '{}',

    -- foreach progress: step_id → {"current": N, "total": M, "failed": K}
    foreach_progress JSONB      DEFAULT '{}'
);

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY workflow_runs_tenant ON workflow_runs
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_runs_workflow  ON workflow_runs (tenant_id, workflow_id, created_at DESC);
CREATE INDEX idx_wf_runs_status    ON workflow_runs (tenant_id, status, created_at DESC);


-- ─────────────────────────────────────────────────────────────────
-- workflow_step_results
-- Per-step outputs, timing, cost for every run
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_step_results (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID        NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL,
    step_id         TEXT        NOT NULL,
    step_type       TEXT        NOT NULL,
    step_name       TEXT,
    status          TEXT        NOT NULL DEFAULT 'pending',
    -- pending | running | complete | failed | skipped
    input           JSONB,
    output          JSONB,
    resolved_input  JSONB,                          -- input after {{}} resolution (for debug)
    error           TEXT,
    llm_prompt      TEXT,                           -- stored for debug/audit
    llm_response    TEXT,
    tokens_in       INT         DEFAULT 0,
    tokens_out      INT         DEFAULT 0,
    cost_usd        DECIMAL(12,6) DEFAULT 0,
    duration_ms     INT,
    attempt_number  INT         DEFAULT 1,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

ALTER TABLE workflow_step_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_step_results_tenant ON workflow_step_results
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_step_results_run ON workflow_step_results (run_id, step_id);


-- ─────────────────────────────────────────────────────────────────
-- workflow_hitl_requests
-- HITL approval requests created by hitl-type steps
-- Extends / parallels governance/hitl.py pattern
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_hitl_requests (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    run_id          UUID        NOT NULL REFERENCES workflow_runs(id),
    step_id         TEXT        NOT NULL,
    step_name       TEXT,
    workflow_name   TEXT,
    assignee_role   TEXT        NOT NULL,
    assignee_strategy TEXT      DEFAULT 'round_robin',
    assigned_to     UUID,                           -- specific user UUID when assigned
    status          TEXT        NOT NULL DEFAULT 'pending',
    -- pending | approved | rejected | escalated | delegated | timed_out | cancelled
    context_payload JSONB       NOT NULL,           -- rich context for reviewer
    actions_config  JSONB       NOT NULL,           -- action buttons config
    chosen_action   TEXT,                           -- action id chosen by reviewer
    reviewer_note   TEXT,
    reviewed_by     UUID,
    reviewed_at     TIMESTAMPTZ,
    delegated_to    UUID,
    delegated_at    TIMESTAMPTZ,
    escalated_to    UUID,
    escalated_at    TIMESTAMPTZ,
    deadline_at     TIMESTAMPTZ NOT NULL,
    escalation_at   TIMESTAMPTZ,
    priority        TEXT        DEFAULT 'medium',   -- low | medium | high | critical
    custom_form_schema JSONB,                       -- optional structured form for reviewer
    custom_form_data   JSONB,                       -- reviewer-submitted structured data
    created_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE workflow_hitl_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_hitl_requests_tenant ON workflow_hitl_requests
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_wf_hitl_pending   ON workflow_hitl_requests (tenant_id, status, priority, deadline_at)
    WHERE status = 'pending';
CREATE INDEX idx_wf_hitl_assignee  ON workflow_hitl_requests (assigned_to, status)
    WHERE assigned_to IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────
-- workflow_test_scenarios
-- Saved test cases for a workflow definition
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE workflow_test_scenarios (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    workflow_id     UUID        NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    description     TEXT,
    input_fixture   JSONB       NOT NULL,           -- sample inputs
    mock_overrides  JSONB       DEFAULT '{}',       -- step_id → mock output
    expected_outputs JSONB,                         -- assertions: field → expected value
    expected_steps_reached TEXT[],                  -- steps that must complete
    expected_branch TEXT,                           -- expected conditional branch taken
    last_run_id     UUID REFERENCES workflow_runs(id),
    last_run_status TEXT,
    last_run_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE workflow_test_scenarios ENABLE ROW LEVEL SECURITY;
CREATE POLICY wf_test_scenarios_tenant ON workflow_test_scenarios
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

---

## Execution Engine

### WorkflowState

```python
# app/workflow/state.py

from typing import Any, TypedDict

class StepStatus(str, enum.Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETE  = "complete"
    FAILED    = "failed"
    SKIPPED   = "skipped"

class WorkflowRunStatus(str, enum.Enum):
    PENDING       = "pending"
    RUNNING       = "running"
    WAITING_HITL  = "waiting_hitl"
    COMPLETE      = "complete"
    FAILED        = "failed"
    CANCELLED     = "cancelled"
    TIMED_OUT     = "timed_out"

class WorkflowState(TypedDict):
    # Identity
    run_id:          str
    workflow_id:     str
    tenant_id:       str

    # Inputs and outputs
    inputs:          dict[str, Any]         # raw trigger / API inputs
    step_outputs:    dict[str, Any]         # step_id → output dict
    outputs:         dict[str, Any]         # final workflow-level outputs

    # Execution state
    status:          WorkflowRunStatus
    current_step_id: str | None
    completed_branch: str | None            # last conditional branch taken
    error:           str | None

    # HITL
    hitl_request_id: str | None            # pending HITL UUID
    hitl_action:     str | None            # action chosen by reviewer
    hitl_note:       str | None
    hitl_reviewer:   str | None
    hitl_form_data:  dict[str, Any] | None # structured data from custom HITL form

    # Telemetry
    cost_usd:        float
    tokens_used:     int
    step_timings:    dict[str, int]         # step_id → duration_ms

    # Control
    is_test_run:     bool
    mock_overrides:  dict[str, Any]         # step_id → mock output (test only)

    # Workflow Variables (mutable state, distinct from step outputs)
    vars:            dict[str, Any]         # set by set_variable steps, read via {{vars.X}}

    # foreach progress tracking
    foreach_progress: dict[str, dict]       # step_id → {"current": N, "total": M, "failed": K}

    # Operator control
    paused_by:       str | None             # user_id who paused the run
    paused_at:       str | None             # ISO timestamp

    # Labels and metadata (from run_labels DSL + per-trigger overrides)
    labels:          dict[str, str]
    run_metadata:    dict[str, Any]
```

### Plugin System: Custom Step Types

The `StepTypeRegistry` is the public extension point that makes this a **true framework**, not just a fixed-step automation tool:

```python
# app/workflow/registry.py

class StepTypeRegistry:
    """
    Singleton registry of all available step types.
    Built-in step types are pre-registered at startup.
    Third-party code can register custom step types via register().
    
    The Visual Builder's tool palette is generated from this registry.
    The YAML validator uses this registry to check step type validity.
    The WorkflowCompiler uses this registry to instantiate step nodes.
    """
    
    _registry: dict[str, type[BaseStepNode]] = {}
    _metadata: dict[str, StepTypeMeta] = {}
    
    @classmethod
    def register(
        cls,
        step_type: str,
        node_class: type[BaseStepNode],
        meta: StepTypeMeta,
    ) -> None:
        """Register a custom step type.
        
        Example (in app startup or plugin init):
            StepTypeRegistry.register(
                step_type="salesforce.query",
                node_class=SalesforceQueryNode,
                meta=StepTypeMeta(
                    display_name="Salesforce Query",
                    category="CRM",
                    icon="salesforce-icon",
                    input_schema={...},   # JSON Schema for step config form
                    output_schema={...},  # JSON Schema for step output
                    description="Execute a SOQL query against Salesforce",
                ),
            )
        """
        cls._registry[step_type] = node_class
        cls._metadata[step_type] = meta
    
    @classmethod
    def get(cls, step_type: str) -> type[BaseStepNode]:
        if step_type not in cls._registry:
            raise UnknownStepTypeError(f"Unknown step type: {step_type!r}")
        return cls._registry[step_type]
    
    @classmethod
    def list_all(cls) -> list[StepTypeMeta]:
        return list(cls._metadata.values())


@dataclass
class StepTypeMeta:
    step_type: str
    display_name: str
    category: str            # tool palette grouping in Visual Builder
    icon: str                # icon name or URL
    input_schema: dict       # JSON Schema — drives the right-panel config form
    output_schema: dict      # JSON Schema — shown in output preview
    description: str
    is_built_in: bool = True
    requires_connectors: list[str] = field(default_factory=list)


# app/workflow/compiler.py uses the registry:
def _build_node(self, step: StepDefinition) -> Callable:
    NodeClass = StepTypeRegistry.get(step.type)  # works for both built-in + custom
    return NodeClass(step, self._context_resolver, **self._service_deps).execute
```

**Built-in types registered at startup:**
```python
# app/workflow/__init__.py or app/main.py

StepTypeRegistry.register("tool",          ToolStepNode,          TOOL_META)
StepTypeRegistry.register("llm",           LLMStepNode,           LLM_META)
StepTypeRegistry.register("rag",           RAGStepNode,           RAG_META)
StepTypeRegistry.register("http",          HTTPStepNode,          HTTP_META)
StepTypeRegistry.register("hitl",          HITLStepNode,          HITL_META)
StepTypeRegistry.register("parallel",      ParallelStepNode,      PARALLEL_META)
StepTypeRegistry.register("conditional",   ConditionalStepNode,   CONDITIONAL_META)
StepTypeRegistry.register("foreach",       ForeachStepNode,       FOREACH_META)
StepTypeRegistry.register("transform",     TransformStepNode,     TRANSFORM_META)
StepTypeRegistry.register("sub_workflow",  SubWorkflowStepNode,   SUB_WORKFLOW_META)
StepTypeRegistry.register("wait",          WaitStepNode,          WAIT_META)
StepTypeRegistry.register("code",          CodeStepNode,          CODE_META)
StepTypeRegistry.register("set_variable",  SetVariableStepNode,   SET_VARIABLE_META)
StepTypeRegistry.register("emit_event",    EmitEventStepNode,     EMIT_EVENT_META)
```

**API to list available step types (drives the Visual Builder palette):**
```
GET /api/v1/workflow-step-types              # All step types (built-in + custom for tenant)
GET /api/v1/workflow-step-types/{step_type}  # Metadata + schema for one type
POST /api/v1/workflow-step-types             # Register custom step type (enterprise tier)
```

---

### Workflow Variables

```python
# app/workflow/variables.py

class WorkflowVariableStore:
    """
    Manages mutable `vars` in WorkflowState.
    Variables are distinct from step outputs:
    - Step outputs: immutable after step completes
    - Variables: can be overwritten by any set_variable step
    
    Read via: {{vars.VAR_NAME}}  in any step's input config
    Write via: type: set_variable step
    """
    
    def set(self, state: WorkflowState, name: str, value: Any) -> WorkflowState:
        return {**state, "vars": {**state["vars"], name: value}}
    
    def get(self, state: WorkflowState, name: str, default: Any = None) -> Any:
        return state["vars"].get(name, default)


# DSL for set_variable step:
# - id: accumulate_total
#   type: set_variable
#   name: running_total
#   value: "{{vars.running_total}} + {{steps.invoice.output.amount}}"
#   value_type: number    # cast after expression evaluation
```

---

### Auto-Audit Trail (Framework Responsibility)

The framework auto-emits an `AuditEvent` for every step transition. No workflow YAML needs an `audit.record` step.

```python
# app/workflow/audit_middleware.py

class AutoAuditMiddleware:
    """
    Wraps every step node execution with automatic audit logging.
    The AuditLog entries are immutable, append-only (existing AuditLog pattern).
    
    Events emitted automatically:
    - workflow.run_started    (run_id, workflow_id, tenant_id, trigger_type, inputs_hash)
    - step.started            (run_id, step_id, step_type, resolved_input_hash)
    - step.completed          (run_id, step_id, output_hash, duration_ms, cost_usd)
    - step.failed             (run_id, step_id, error_code, error_message)
    - step.skipped            (run_id, step_id, reason)
    - hitl.requested          (run_id, step_id, assignee_role, deadline)
    - hitl.decided            (run_id, step_id, action, reviewer_id, note)
    - workflow.completed      (run_id, outputs_hash, total_cost_usd, duration_ms)
    - workflow.failed         (run_id, error_code, failed_step_id)
    - workflow.paused         (run_id, paused_by, reason)
    - workflow.resumed        (run_id, resumed_by)
    
    Note: actual step output VALUES are stored in workflow_step_results.
    Audit trail stores HASHES only — PII-safe.
    """
    
    async def wrap(self, step_fn: Callable, state: WorkflowState) -> WorkflowState:
        await self.audit_log.record(AuditEvent(
            event_type="step.started",
            run_id=state["run_id"],
            step_id=state["current_step_id"],
            tenant_id=state["tenant_id"],
        ))
        try:
            result = await step_fn(state)
            await self.audit_log.record(AuditEvent(event_type="step.completed", ...))
            return result
        except Exception as e:
            await self.audit_log.record(AuditEvent(event_type="step.failed", ...))
            raise
```

---

### Operator Pause/Resume

Distinct from HITL (workflow-designed) — this is **operational control** by admins:

```python
# New API endpoints:
POST /api/v1/workflow-runs/{run_id}/pause    # Admin pauses a running workflow
# Body: { "reason": "connector outage" }
# Effect: sets WorkflowState.status = "paused", records paused_by + reason

POST /api/v1/workflow-runs/{run_id}/resume   # Admin resumes a paused workflow
# Effect: sets status = "running", re-dispatches to Celery

# Pause happens gracefully: current step completes, then the engine checks
# WorkflowState.paused_by before starting the next step.
```

---

### Callback URL

For callers who trigger a workflow and don't want to poll or stream:

```python
# WorkflowRunner emits callback after run completes/fails:
async def _emit_callback(self, state: WorkflowState, definition: WorkflowDefinition):
    if not definition.callback:
        return
    url = self.context_resolver.resolve(definition.callback.url, state)
    payload = {
        "run_id":  state["run_id"],
        "status":  state["status"],
        "outputs": state["outputs"],
        "labels":  state["labels"],
        "cost_usd": state["cost_usd"],
    }
    if state["status"] == "failed" and not definition.callback.on_failure:
        return
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, timeout=30)
```

---

### `code` Step: Sandbox via Existing `execution_environment`

```python
# app/workflow/steps/code_step.py
# Delegates to the existing app/execution_environment/ sandbox (already tested)

class CodeStepNode:
    async def execute(self, state: WorkflowState) -> dict:
        code = self.ctx_resolver.resolve(self.step.code, state)
        inputs = self.ctx_resolver.resolve_all(self.step.input, state)
        
        # Reuse existing ExecutionEnvironment — same sandbox as ChatCodeExecutor
        # Constraints: no filesystem, no network, 30s CPU timeout, 128MB RAM
        result = await execution_environment.run(
            runtime=self.step.runtime,   # python | javascript
            code=code,
            inputs=inputs,
            timeout_seconds=min(self.step.timeout_seconds or 30, 120),
        )
        return {"step_outputs": {self.step.id: result.output}}
```

### Data Retention + GDPR Erasure for Run Data

```sql
-- workflow_definitions already has: tenant_id (RLS enforced)
-- New: per-tenant retention configuration

ALTER TABLE workflow_definitions
    ADD COLUMN run_retention_days INT DEFAULT 90;  -- null = keep forever

-- Cleanup Celery task (added to beat_schedule, runs daily):
-- @celery_app.task(name="workflow.cleanup_expired_runs")
-- Deletes workflow_step_results + workflow_runs older than tenant's retention_days.
-- Also fires when a GDPR erasure request is processed for a customer:
-- WorkflowRunStore.delete_runs_containing_subject(subject_id, tenant_id)
```

**GDPR Integration:** The existing `GDPR Data Subject Request` workflow (Section 1.4 of catalog)
includes a step that calls `workflow.purge_runs_by_subject(subject_id)`. The framework
provides this as a built-in operation, not requiring a manual step in user workflows.

### Webhook HMAC Replay Protection

```python
# app/workflow/router.py — inbound webhook handler (addition to existing spec)

MAX_TIMESTAMP_SKEW_SECONDS = 300  # 5 minutes

def verify_hmac_webhook(
    payload_bytes: bytes,
    signature_header: str,    # X-Hub-Signature-256: sha256=...
    timestamp_header: str,    # X-Timestamp: Unix epoch seconds
    hmac_secret: str,
) -> None:
    # 1. Replay protection: reject if timestamp is >5 minutes old
    ts = int(timestamp_header)
    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW_SECONDS:
        raise WebhookReplayError("Timestamp too old — possible replay attack")
    
    # 2. HMAC-SHA256 verification: sign (timestamp + "." + payload)
    expected = hmac.new(
        hmac_secret.encode(),
        f"{timestamp_header}.".encode() + payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    
    received = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, received):
        raise WebhookSignatureError("HMAC signature mismatch")
```

### Max Payload Size Limits + Large Payload S3 Offload

```python
# Limits enforced at ingress and step output:

MAX_TRIGGER_PAYLOAD_BYTES  = 1 * 1024 * 1024    # 1 MB — trigger inputs
MAX_STEP_OUTPUT_BYTES      = 5 * 1024 * 1024    # 5 MB — per step output
MAX_STEP_INPUT_BYTES       = 2 * 1024 * 1024    # 2 MB — per step input (resolved)

# Large payload S3 offload strategy:
# If step output > MAX_STEP_OUTPUT_BYTES:
#   1. Upload to S3/MinIO: s3://workflow-artifacts/{tenant_id}/{run_id}/{step_id}.json
#   2. Store reference in workflow_step_results.output: {"_s3_ref": "s3://..."}
#   3. ContextResolver transparently fetches from S3 when {{steps.X.output}} is accessed
#
# This keeps the DB row size bounded while supporting large documents (OCR text,
# bank statements, research papers).
#
# app/workflow/storage.py: LargePayloadStore — handles upload/download transparently
```

### Workflow Definition Publishing Approval (Regulated Industries)

```sql
-- workflow_definitions table addition:
ALTER TABLE workflow_definitions
    ADD COLUMN requires_publish_approval BOOL DEFAULT false,
    ADD COLUMN publish_approved_by       UUID,
    ADD COLUMN publish_approved_at       TIMESTAMPTZ,
    ADD COLUMN publish_approval_note     TEXT;

-- Status flow when requires_publish_approval = true:
-- draft → pending_approval → published
-- (without flag: draft → published directly)
```

```python
# API additions:
# POST /api/v1/workflows/{id}/submit-for-approval   # Author submits draft
# POST /api/v1/workflows/{id}/approve-publish       # Role: workflow_approver
# POST /api/v1/workflows/{id}/reject-publish        # Role: workflow_approver + note required

# Configured per-tenant in tenant settings:
# "workflow_publish_requires_approval": true
# "workflow_approver_role": "compliance_officer"
```

---

### WorkflowCompiler

```python
# app/workflow/compiler.py

class WorkflowCompiler:
    """
    Compiles a WorkflowDefinition into a LangGraph StateGraph.
    
    One compiled graph is cached per (workflow_id, version).
    The cache is invalidated when a workflow is re-published.
    """
    
    def __init__(
        self,
        mcp_client: MCPClient,
        llm_provider: LLMProvider,
        knowledge_store: KnowledgeStore,
        hitl_gateway: HITLWorkflowGateway,
        vault_client: VaultClient,
        context_resolver: ContextResolver,
    ) -> None: ...

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        graph = StateGraph(WorkflowState)
        
        # 1. Register every step as a graph node
        for step in definition.steps:
            node = self._build_node(step)
            graph.add_node(step.id, node)
        
        # 2. Wire edges based on depends_on + step type
        for step in definition.steps:
            match step.type:
                case "conditional":
                    # Fan-out: one edge per branch to named target nodes
                    graph.add_conditional_edges(
                        step.id,
                        self._build_router(step),
                        {b.next: b.next for b in step.branches},
                    )
                case "parallel":
                    # Fan-out to all sub-branches → fan-in via merge node
                    merge_id = f"{step.id}.__merge__"
                    graph.add_node(merge_id, self._build_parallel_merge(step))
                    for branch in step.branches:
                        graph.add_node(f"{step.id}.{branch.id}", self._build_node(branch))
                        graph.add_edge(step.id, f"{step.id}.{branch.id}")
                        graph.add_edge(f"{step.id}.{branch.id}", merge_id)
                    # merge node → next downstream step
                    for downstream in self._find_downstream(step.id, definition):
                        graph.add_edge(merge_id, downstream)
                case _:
                    for dep in step.depends_on:
                        graph.add_edge(dep, step.id)
        
        # 3. Wire START → first step(s), terminal steps → END
        first_steps = self._find_entry_steps(definition)
        for s in first_steps:
            graph.add_edge(START, s)
        
        terminal_steps = self._find_terminal_steps(definition)
        for s in terminal_steps:
            graph.add_edge(s, END)
        
        # 4. Compile with existing Redis checkpointer (from app.state)
        return graph.compile(checkpointer=self._checkpointer)
    
    def _build_node(self, step: StepDefinition) -> Callable:
        """Returns the correct step executor for the given step type."""
        node_map = {
            "tool":         ToolStepNode,
            "llm":          LLMStepNode,
            "rag":          RAGStepNode,
            "http":         HTTPStepNode,
            "hitl":         HITLStepNode,
            "parallel":     ParallelStepNode,   # fan-out dispatcher only
            "conditional":  ConditionalStepNode,
            "transform":    TransformStepNode,
            "sub_workflow": SubWorkflowStepNode,
            "wait":         WaitStepNode,
        }
        NodeClass = node_map[step.type]
        return NodeClass(step, self._context_resolver, **self._service_deps).execute
```

### Step Node Protocol

```python
# app/workflow/steps/base.py

class BaseStepNode(Protocol):
    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """
        Execute this step.
        Returns a dict with keys to merge into WorkflowState:
          - step_outputs: {step_id: output_dict}
          - status: WorkflowRunStatus (if changing)
          - hitl_request_id: str (if type=hitl, while waiting)
          - cost_usd: float (additional cost)
          - tokens_used: int (additional tokens)
          - error: str (if failed)
        """
        ...
```

### Key Step Implementations

```python
# app/workflow/steps/hitl_step.py

class HITLStepNode:
    async def execute(self, state: WorkflowState) -> dict:
        if state.get("hitl_request_id") == self.step.id:
            # Resuming after human decision — extract decision from state
            return self._process_decision(state)
        
        # First pass — create the approval request and suspend
        resolved_context = [
            {
                "label": ctx.label,
                "value": self.ctx_resolver.resolve(ctx.value, state),
                "display_type": ctx.display_type,
                "threshold_red": getattr(ctx, "threshold_red", None),
                "threshold_yellow": getattr(ctx, "threshold_yellow", None),
            }
            for ctx in self.step.context
        ]
        
        request_id = await self.hitl_gateway.create_workflow_approval(
            run_id=state["run_id"],
            step_id=self.step.id,
            step_name=self.step.name,
            workflow_name=state["workflow_name"],
            assignee_role=self.step.assignee.role,
            strategy=self.step.assignee.strategy,
            context_payload=resolved_context,
            actions_config=self.step.actions,
            deadline_hours=self._parse_timeout_hours(self.step.timeout),
            escalation_hours=self._parse_timeout_hours(self.step.escalation.after),
            escalation_to_role=self.step.escalation.to_role,
            custom_form_schema=getattr(self.step, "custom_form_schema", None),
            priority=self._compute_priority(state),
        )
        
        # Emit SSE: workflow paused for HITL
        await self.sse_broadcaster.publish(
            state["tenant_id"], state["run_id"],
            {"event": "workflow.hitl_requested", "step_id": self.step.id,
             "request_id": request_id, "assignee_role": self.step.assignee.role}
        )
        
        # Return waiting status — LangGraph checkpoints here
        return {
            "status": WorkflowRunStatus.WAITING_HITL,
            "hitl_request_id": self.step.id,
        }


# app/workflow/steps/parallel_step.py

class ParallelStepNode:
    async def execute(self, state: WorkflowState) -> dict:
        # Fan-out: run all branches concurrently
        tasks = [
            self._execute_branch(branch, state)
            for branch in self.step.branches
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        merged_outputs = {}
        merged_cost = 0.0
        merged_tokens = 0
        
        for branch, result in zip(self.step.branches, results):
            if isinstance(result, Exception):
                if self.step.on_failure == "abort":
                    raise result
                # else: skip or use_default per config
                merged_outputs[branch.id] = {}
            else:
                merged_outputs[branch.id] = result["output"]
                merged_cost += result.get("cost_usd", 0)
                merged_tokens += result.get("tokens_used", 0)
        
        return {
            "step_outputs": {self.step.id: merged_outputs},
            "cost_usd": state["cost_usd"] + merged_cost,
            "tokens_used": state["tokens_used"] + merged_tokens,
        }
```

### WorkflowRunner

```python
# app/workflow/runner.py

class WorkflowRunner:
    """
    Entry point for executing a compiled workflow.
    Wraps LangGraph invocation with DB persistence + SSE emission.
    """
    
    async def run(
        self,
        workflow_id: str,
        tenant_id: str,
        inputs: dict[str, Any],
        trigger_type: str,
        trigger_payload: dict | None = None,
        is_test_run: bool = False,
        mock_overrides: dict | None = None,
    ) -> str:  # returns run_id
        
        # 1. Validate inputs against workflow input schema
        definition = await self.definition_store.get(workflow_id, tenant_id)
        self._validate_inputs(definition, inputs)
        
        # 2. Create workflow_run record
        run_id = await self.run_store.create(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            trigger_type=trigger_type,
            trigger_payload=trigger_payload,
            inputs=inputs,
            is_test_run=is_test_run,
        )
        
        # 3. Dispatch to Celery (per-plan queue)
        plan_tier = await self.tenant_service.get_plan_tier(tenant_id)
        execute_workflow_run.apply_async(
            args=[run_id, workflow_id, tenant_id],
            kwargs={"mock_overrides": mock_overrides or {}},
            queue=f"workflows.{plan_tier}",
        )
        
        return run_id
    
    async def resume_from_hitl(
        self,
        run_id: str,
        step_id: str,
        action: str,
        actor_id: str,
        note: str | None,
        form_data: dict | None,
        tenant_id: str,
    ) -> None:
        """Called when a reviewer submits a HITL decision."""
        
        # 1. Load checkpointed state from Redis
        compiled = await self.compiler_cache.get(run_id)
        checkpoint_config = {"configurable": {"thread_id": run_id}}
        
        # 2. Inject the decision into state
        state_update = {
            "status": WorkflowRunStatus.RUNNING,
            "hitl_request_id": None,
            "hitl_action": action,
            "hitl_note": note,
            "hitl_reviewer": actor_id,
            "hitl_form_data": form_data,
        }
        
        # 3. Resume the LangGraph graph from checkpoint
        await compiled.aupdate_state(checkpoint_config, state_update)
        await execute_workflow_run.apply_async(
            args=[run_id], kwargs={"resume": True},
            queue=f"workflows.{await self._get_plan(run_id)}",
        )
```

---

## HITL Integration Architecture

### HITLWorkflowGateway (extends existing HITLGateway)

```python
# app/workflow/hitl_extension.py

class HITLWorkflowGateway:
    """
    Extends the existing HITLGateway for workflow-specific features:
    - Rich context payloads (image, json, table, diff, chart display types)
    - Custom action buttons (not just approve/reject)
    - Custom form schema (reviewer fills structured input)
    - Escalation chains
    - Delegation
    - Batch approval
    - Magic link tokens (single-use JWT for email/Slack approval)
    """
    
    async def create_workflow_approval(
        self, run_id, step_id, step_name, workflow_name,
        assignee_role, strategy, context_payload, actions_config,
        deadline_hours, escalation_hours, escalation_to_role,
        custom_form_schema, priority,
    ) -> str:  # returns request_id
        ...
    
    async def assign_reviewer(
        self, request_id: str, role: str, strategy: str, tenant_id: str
    ) -> str:  # returns assigned user_id
        match strategy:
            case "round_robin":   return await self._round_robin(role, tenant_id)
            case "least_busy":    return await self._least_busy(role, tenant_id)
            case "skill_based":   return await self._skill_match(role, context, tenant_id)
            case "specific":      return specific_user_id
    
    async def generate_magic_link(self, request_id: str, action: str) -> str:
        """Single-use signed JWT embedded in notification email/Slack message."""
        payload = {
            "request_id": request_id,
            "action": action,
            "exp": time.time() + 86400,  # 24h
            "jti": str(uuid.uuid4()),     # single-use, stored in Redis on first use
        }
        return jwt.encode(payload, self.signing_key, algorithm="HS256")
    
    async def process_magic_link(self, token: str) -> ApprovalResult:
        payload = jwt.decode(token, self.signing_key, algorithms=["HS256"])
        # Check jti not already used (Redis key: hitl:used_jti:{jti})
        if await self.redis.get(f"hitl:used_jti:{payload['jti']}"):
            raise TokenAlreadyUsedError
        await self.redis.setex(f"hitl:used_jti:{payload['jti']}", 86400, "1")
        return await self.submit_decision(payload["request_id"], payload["action"])
    
    async def check_and_escalate_overdue(self) -> None:
        """Called by Celery Beat every 15 minutes."""
        overdue = await self.store.get_overdue_requests()
        for req in overdue:
            if req.escalation_at <= now() and req.status == "pending":
                await self._escalate(req)
```

### Approval Inbox API

```
GET    /api/v1/approvals                    # List pending + recent (assignee's view)
GET    /api/v1/approvals/{id}               # Full detail with rich context
POST   /api/v1/approvals/{id}/decide        # Submit decision {action, note, form_data}
POST   /api/v1/approvals/{id}/delegate      # Delegate to {user_id}
POST   /api/v1/approvals/{id}/escalate      # Manual escalate
POST   /api/v1/approvals/bulk-decide        # Bulk approve/reject {request_ids[], action}
GET    /api/v1/approvals/magic/{token}      # Magic link — process JWT from email/Slack
POST   /api/v1/approvals/delegate-all       # "I'm on leave" → delegate all to {user_id}
GET    /api/v1/approvals/stats              # Inbox stats: pending count, avg resolution time
```

---

## Template Marketplace Architecture

### System Template Seeding

```python
# scripts/seed_workflow_templates.py
# Run once at deploy time / on migration

SYSTEM_TEMPLATES = [
    # Identity & Compliance
    "templates/kyc-automation.yaml",
    "templates/merchant-onboarding.yaml",
    "templates/aml-screening.yaml",
    "templates/gdpr-subject-request.yaml",
    "templates/employee-background-check.yaml",
    # Financial Operations
    "templates/invoice-processing.yaml",
    "templates/expense-report.yaml",
    "templates/loan-prescreening.yaml",
    "templates/bank-reconciliation.yaml",
    # Dev & Engineering
    "templates/sre-incident-response.yaml",
    "templates/production-bug-assistant.yaml",
    "templates/automated-code-review.yaml",
    "templates/release-notes-generator.yaml",
    "templates/security-vulnerability-response.yaml",
    # Customer & Support
    "templates/email-auto-response.yaml",
    "templates/support-ticket-triage.yaml",
    "templates/churn-prediction-outreach.yaml",
    "templates/contract-generation.yaml",
    # Document & Knowledge
    "templates/legal-contract-analysis.yaml",
    "templates/meeting-minutes-generator.yaml",
    "templates/research-paper-digest.yaml",
    # HR
    "templates/job-application-screening.yaml",
    "templates/employee-offboarding.yaml",
    # Industry-Specific
    "templates/healthcare-patient-onboarding.yaml",
    "templates/ecommerce-return-processing.yaml",
    "templates/marketing-campaign-automation.yaml",
]
```

### Template Fork Flow

```python
# POST /api/v1/workflow-templates/{template_id}/fork

async def fork_template(
    template_id: str, name: str, tenant_id: str, user_id: str
) -> WorkflowDefinition:
    
    template = await template_store.get(template_id)
    
    # Increment popularity counter
    await template_store.increment_popularity(template_id)
    
    # Create tenant-owned copy
    definition = await definition_store.create(
        tenant_id=tenant_id,
        name=name or template.name,
        slug=slugify(name or template.name),
        definition_yaml=template.definition_yaml,
        definition_json=template.definition_json,
        forked_from_template_id=template_id,
        forked_from_template_ver=template.version,
        status="draft",
        created_by=user_id,
    )
    
    return definition

# Template update notification (on new template version publish):
# → query all workflow_definitions WHERE forked_from_template_id = X
# → create notification: "A new version of 'KYC Automation' is available (v1.3.0)"
# → show update banner in Visual Builder with changelog diff
```

### Template API

```
GET    /api/v1/workflow-templates                        # List with filters
GET    /api/v1/workflow-templates/{id}                   # Detail + step preview
GET    /api/v1/workflow-templates/{id}/preview-run       # Dry-run with sample_input
POST   /api/v1/workflow-templates/{id}/fork              # Fork into tenant workspace
GET    /api/v1/workflow-templates/categories             # Category tree
GET    /api/v1/workflow-templates/search                 # Full-text + tag search
```

---

## Full API Surface

### Workflow Definitions

```
POST   /api/v1/workflows                                 # Create from YAML or JSON
GET    /api/v1/workflows                                 # List (tenant)
GET    /api/v1/workflows/{id}                            # Get definition
PUT    /api/v1/workflows/{id}                            # Full update (creates new version)
PATCH  /api/v1/workflows/{id}                            # Partial update (draft only)
DELETE /api/v1/workflows/{id}                            # Archive (soft delete)
POST   /api/v1/workflows/{id}/publish                    # Draft → Published
POST   /api/v1/workflows/{id}/unpublish                  # Published → Draft
GET    /api/v1/workflows/{id}/yaml                       # Export canonical YAML
GET    /api/v1/workflows/{id}/versions                   # Version history
POST   /api/v1/workflows/{id}/validate                   # Validate DSL without saving
POST   /api/v1/workflows/{id}/check-connectors           # Verify required MCP tools are connected
```

### Version History & Rollback

```
GET    /api/v1/workflows/{id}/versions                   # List all published versions
GET    /api/v1/workflows/{id}/versions/{ver}             # Get a specific version's YAML
POST   /api/v1/workflows/{id}/rollback/{ver}             # Rollback to a previous version (creates new version)
GET    /api/v1/workflows/{id}/diff/{ver_a}/{ver_b}       # YAML diff between two versions
```

### Import / Export

```
GET    /api/v1/workflows/{id}/export                     # Download canonical YAML file (Content-Disposition: attachment)
POST   /api/v1/workflows/import                          # Upload YAML file → create workflow (multipart/form-data)
POST   /api/v1/workflows/{id}/clone                      # Clone within same tenant (new name)
```

### Workflow RBAC

```
GET    /api/v1/workflows/{id}/permissions                # List granted permissions
POST   /api/v1/workflows/{id}/permissions                # Grant {subject_type, subject_id, permission}
DELETE /api/v1/workflows/{id}/permissions/{perm_id}      # Revoke a permission
```

### Analytics

```
GET    /api/v1/workflows/{id}/analytics                  # Run stats: total runs, success rate, avg duration, cost trend
GET    /api/v1/workflows/{id}/analytics/steps            # Per-step failure heatmap + avg duration
GET    /api/v1/workflows/{id}/analytics/cost             # Cost breakdown by step type
GET    /api/v1/workflows/analytics/overview              # Tenant-wide: total runs, cost, active workflows, pending HITL count
```

### Error Recovery

```
POST   /api/v1/workflow-runs/{run_id}/recover
# Body: { "step_id": "fraud_scan", "injected_output": {...} }
# Effect: inject manual output for failed step and resume graph from that step
```

### Webhook Dead Letter

```
GET    /api/v1/workflows/{id}/webhook-events             # List received webhook events
POST   /api/v1/webhook-events/{event_id}/retry           # Retry a failed webhook event
GET    /api/v1/webhook-events/{event_id}                 # Inspect raw payload + error
```

### Workflow Execution

```
POST   /api/v1/workflows/{id}/run                        # Trigger run (returns run_id)
GET    /api/v1/workflows/{id}/runs                       # List runs (filterable: status, date range, trigger)
GET    /api/v1/workflow-runs/{run_id}                    # Run status + outputs
GET    /api/v1/workflow-runs/{run_id}/stream             # SSE stream of live events
GET    /api/v1/workflow-runs/{run_id}/steps              # All step results
GET    /api/v1/workflow-runs/{run_id}/steps/{step_id}    # Single step detail (prompt, I/O)
POST   /api/v1/workflow-runs/{run_id}/cancel             # Cancel a running run
POST   /api/v1/workflow-runs/{run_id}/replay             # Re-run from a specific step
POST   /api/v1/workflow-runs/{run_id}/resume             # Resume after HITL decision
POST   /api/v1/workflow-runs/{run_id}/pause              # Operator pause (admin)
POST   /api/v1/workflow-runs/{run_id}/resume-paused      # Operator resume from pause
GET    /api/v1/workflow-runs?label.KEY=VALUE             # Filter runs by label
```

### Step Type Registry API

```
GET    /api/v1/workflow-step-types                       # All step types (built-in + custom)
GET    /api/v1/workflow-step-types/{step_type}           # Metadata + input/output schema
POST   /api/v1/workflow-step-types                       # Register custom step type (enterprise)
```

### Testing

```
POST   /api/v1/workflows/{id}/test                       # Dry-run with mock data
POST   /api/v1/workflows/{id}/test/step-through          # Execute one step, pause
POST   /api/v1/workflow-runs/{run_id}/advance            # Advance one step (step-through)
GET    /api/v1/workflows/{id}/test-scenarios             # List saved scenarios
POST   /api/v1/workflows/{id}/test-scenarios             # Create scenario
PUT    /api/v1/workflows/{id}/test-scenarios/{sid}       # Update scenario
DELETE /api/v1/workflows/{id}/test-scenarios/{sid}       # Delete scenario
POST   /api/v1/workflows/{id}/test-scenarios/run-all     # Run all scenarios
```

### Webhook Ingress (external triggers)

```
POST   /api/v1/webhooks/workflows/{token}                # Inbound webhook (auth: bearer/hmac)
GET    /api/v1/workflows/{id}/webhook-config             # Get webhook URL + token
POST   /api/v1/workflows/{id}/webhook-config/rotate      # Rotate webhook secret
```

**Webhook Security & Reliability (Critical):**

```python
# app/workflow/router.py — webhook ingress handler

@router.post("/webhooks/workflows/{token}")
@rate_limit(per_token_rpm=60)                    # per-token rate limit (existing rate limiter)
async def inbound_webhook(
    token: str,
    payload: dict,
    x_idempotency_key: str | None = Header(None),  # optional but recommended
) -> dict:
    workflow = await definition_store.get_by_token(token)
    
    # Idempotency: reject duplicate deliveries within 24h
    if x_idempotency_key:
        if await redis.get(f"webhook:idem:{token}:{x_idempotency_key}"):
            return {"status": "already_processed", "idempotency_key": x_idempotency_key}
        await redis.setex(f"webhook:idem:{token}:{x_idempotency_key}", 86400, "1")
    
    # Record in DLQ table before dispatching (ensures at-least-once delivery)
    event_id = await webhook_event_store.create(workflow.id, token, payload)
    await workflow_runner.run(workflow.id, inputs=payload, trigger_type="webhook")
    await webhook_event_store.mark_complete(event_id)
    return {"status": "accepted", "event_id": event_id}
```

### NL Trigger Implementation

```python
# app/workflow/nl_trigger.py
# Bridges the chat IntentRouter → WorkflowRunner
# Activated when user types: "Run KYC for customer 123"

class NLTriggerResolver:
    """
    1. User sends NL message in chat: "Run the expense report for emp_456"
    2. ChatService calls NLTriggerResolver.resolve(message, tenant_id)
    3. Resolver uses IntentRouter to classify: is this a workflow trigger?
    4. Matches against published workflows' nl_trigger.phrases (fuzzy)
    5. Extracts inputs using LLM with workflow's input_schema as context
    6. Calls WorkflowRunner.run() with extracted inputs
    7. Returns run_id to ChatService for SSE streaming
    """
    
    async def resolve(
        self, message: str, tenant_id: str
    ) -> WorkflowRun | None:  # None if no workflow matches
        ...
```

**NL trigger YAML config:**
```yaml
trigger:
  type: nl
  nl:
    phrases:                         # fuzzy-matched trigger phrases
      - "run kyc for {customer_id}"
      - "verify customer {customer_id}"
      - "start kyc verification"
    input_extraction_prompt: |       # LLM extracts inputs from user message
      Extract the customer_id from the user's message.
      Return JSON: {"customer_id": "..."}
```

### Extended Trigger Types

The following `TriggerType` values already exist in `app/triggers/models.py` and are now
wired into the workflow engine (not available in the initial spec):

| Trigger Type | DSL `type` value | Use Case |
|---|---|---|
| `TriggerType.FILE_DROP` | `file_drop` | Document uploaded to S3/GCS/MinIO → trigger document processing workflow |
| `TriggerType.ALERTMANAGER` | `alertmanager` | Prometheus/Alertmanager alert → trigger SRE Incident workflow |
| `TriggerType.DATADOG` | `datadog` | Datadog monitor alert → trigger SRE Incident workflow |
| `TriggerType.PAGERDUTY` | `pagerduty` | PagerDuty incident created → trigger Incident Response workflow |

```yaml
# file_drop trigger example
trigger:
  type: file_drop
  file_drop:
    bucket: "documents-input"
    prefix: "kyc/"
    extensions: [".pdf", ".jpg", ".png"]
    payload_mapping:
      document_url: "{{event.file_url}}"
      customer_id: "{{event.metadata.customer_id}}"
```

### SSE Event Schema

```json
{
  "event": "workflow.step_started",
  "run_id": "uuid",
  "step_id": "fraud_scan",
  "step_name": "Fraud Scan",
  "step_type": "llm",
  "timestamp": "2026-08-15T10:30:00Z"
}

{
  "event": "workflow.step_completed",
  "run_id": "uuid",
  "step_id": "fraud_scan",
  "output": {"risk_score": 0.73, "risk_factors": ["address_mismatch"]},
  "duration_ms": 1243,
  "cost_usd": 0.0004,
  "tokens_used": 969
}

{
  "event": "workflow.hitl_requested",
  "run_id": "uuid",
  "step_id": "human_review",
  "request_id": "hitl-uuid",
  "assignee_role": "kyc_officer",
  "deadline_at": "2026-08-17T10:30:00Z"
}

{
  "event": "workflow.completed",
  "run_id": "uuid",
  "outputs": {"kyc_status": "approved", "confidence": 0.94},
  "total_cost_usd": 0.0021,
  "total_tokens": 3847,
  "total_duration_ms": 8400
}
```

---

## Frontend Architecture

### Package Structure

```
src/features/workflow/
  index.ts
  types.ts                          # WorkflowDefinition, WorkflowRun, HITLRequest, etc.
  api.ts                            # TanStack Query hooks: useWorkflows, useWorkflowRuns, etc.

  # ── Pages ────────────────────────────────────────────────────────
  WorkflowListPage.tsx              # /workflows — list all workflows
  WorkflowBuilderPage.tsx           # /workflows/:id/edit — visual builder
  WorkflowRunsPage.tsx              # /workflows/:id/runs — run history
  WorkflowRunDetailPage.tsx         # /workflow-runs/:runId — live run view
  WorkflowTemplatesPage.tsx         # /workflow-templates — marketplace
  WorkflowTemplateDetailPage.tsx    # /workflow-templates/:id — template preview
  ApprovalsPage.tsx                 # /approvals — HITL inbox
  ApprovalDetailPage.tsx            # /approvals/:id — full approval context

  # ── Builder Components ───────────────────────────────────────────
  builder/
    WorkflowCanvas.tsx              # React Flow canvas (infinite, pan/zoom)
    WorkflowToolPalette.tsx         # Left: draggable node types + tool catalog search
    WorkflowStepConfig.tsx          # Right panel: step configuration form
    WorkflowTopBar.tsx              # Name, version, Save/Test/Publish buttons
    WorkflowMiniMap.tsx             # Bottom-right minimap
    WorkflowExecutionOverlay.tsx    # Live execution status overlaid on nodes

    nodes/
      TriggerNode.tsx               # Green pill, webhook/schedule/api config
      ToolNode.tsx                  # Blue rect, tool selector + input mapper
      LLMNode.tsx                   # Purple rect, prompt editor (Monaco)
      RAGNode.tsx                   # Orange rect, collection + top_k config
      ConditionalNode.tsx           # Yellow diamond, branch condition builder
      ParallelNode.tsx              # Teal fork, manage sub-branches
      HITLNode.tsx                  # Red rect, reviewer + context + actions config
      HTTPNode.tsx                  # Dark blue rect, URL/method/auth/body config
      TransformNode.tsx             # Light blue, field mapping editor
      SubWorkflowNode.tsx           # Gray nested, workflow selector
      WaitNode.tsx                  # Gray clock, duration/event-gate config

    config-panels/
      ToolConfigPanel.tsx           # Tool selector + input mapping
      LLMConfigPanel.tsx            # Prompt editor + RAG toggle + model selector
      HITLConfigPanel.tsx           # Context builder + actions designer + HITL settings
      ConditionalConfigPanel.tsx    # Visual condition builder + expression editor
      HTTPConfigPanel.tsx           # URL, method, headers, auth, body editor
      InputMapperWidget.tsx         # Drag previous-step outputs to current step inputs

    canvas-utils/
      useCanvasKeyboardShortcuts.ts # Ctrl+Z, Space+drag, Ctrl+D, etc.
      useAutoLayout.ts              # Dagre auto-layout algorithm
      useYamlSync.ts                # Canvas state ↔ YAML bidirectional sync
      nodeStyles.ts                 # Shared node visual config

  # ── Run Viewer ───────────────────────────────────────────────────
  run-viewer/
    RunTimeline.tsx                 # Vertical timeline of steps, click to inspect
    StepOutputInspector.tsx         # JSON tree / formatted view of step I/O
    LLMPromptViewer.tsx             # Full prompt + response viewer
    RunCostSummary.tsx              # Total cost, tokens, duration breakdown
    RunSSEStream.ts                 # SSE subscription hook for live runs

  # ── HITL Inbox ───────────────────────────────────────────────────
  hitl/
    ApprovalInbox.tsx               # List: priority-sorted cards with urgency badges
    ApprovalDetailView.tsx          # Full context + action buttons
    HITLContextRenderer.tsx         # Renders context by display_type
    HITLContextImage.tsx            # Pan-zoomable image viewer
    HITLContextJsonTree.tsx         # Collapsible JSON tree
    HITLContextTable.tsx            # Data table renderer
    HITLContextDiff.tsx             # Before/after diff viewer
    HITLContextChart.tsx            # Chart renderer (recharts)
    HITLCustomForm.tsx              # Dynamic form from custom_form_schema
    HITLActionButtons.tsx           # Renders dynamic action buttons from config
    HITLDiscussion.tsx              # Comment thread between reviewers
    ApprovalBadge.tsx               # Nav badge with pending count (real-time via SSE)
    useMagicLinkHandler.ts          # Process magic link token on page load
    useApprovalSSE.ts               # SSE subscription for new HITL requests

  # ── Template Marketplace ─────────────────────────────────────────
  marketplace/
    TemplateGrid.tsx                # Card grid with category filter + search
    TemplateCard.tsx                # Preview card with complexity, connectors, popularity
    TemplatePreviewModal.tsx        # Read-only canvas + "Try It" sample run
    TemplateInstallModal.tsx        # Name + connector check → fork
    TemplateCategoryNav.tsx         # Category sidebar

  # ── Dark Mode + Accessibility ──────────────────────────────────────
  # All workflow UI components MUST support:
  # - Dark mode: Tailwind `dark:` prefix on all color classes; canvas uses
  #   CSS variables for node backgrounds/borders toggled by the app theme.
  # - WCAG 2.2 AA: canvas nodes have aria-label; keyboard nav (Tab → node
  #   selection, Enter → open config, Escape → deselect); focus ring visible
  #   in both light and dark mode; HITL modal traps focus and restores on close.
  # - prefers-reduced-motion: animated flow edges and skeleton pulses disabled.

  # ── Skeleton / Loading States ─────────────────────────────────────
  skeletons/
    WorkflowCanvasSkeleton.tsx      # Skeleton canvas while definition loads
    RunTimelineSkeleton.tsx         # Skeleton for step list in run detail
    TemplateCardSkeleton.tsx        # Skeleton card during marketplace load
    WorkflowListSkeleton.tsx        # Skeleton rows in workflow list page
    ApprovalInboxSkeleton.tsx       # Skeleton cards while HITL inbox loads

  # ── SSE Reconnection ─────────────────────────────────────────────
  # app/workflow/runner.py emits SSE with `id:` field = sequential event_id.
  # Frontend EventSource sends `Last-Event-ID` on reconnect.
  # Server replays events from that id so no events are lost during network blip.
  # Implementation in RunSSEStream.ts:
  #
  #   const es = new EventSource(`/api/v1/workflow-runs/${runId}/stream`, {
  #     headers: lastEventId ? { 'Last-Event-ID': lastEventId } : undefined
  #   });
  #   es.addEventListener('error', () => { setTimeout(reconnect, 2000); });

  # ── Test Runner UI ───────────────────────────────────────────────
  testing/
    TestRunnerPanel.tsx             # Bottom drawer: test mode controls
    StepThroughControls.tsx         # Pause/advance/inspect/edit-output controls
    StepOutputEditor.tsx            # Override step output in step-through mode
    TestScenarioManager.tsx         # Create/run/view saved test scenarios
    ScenarioAssertionBuilder.tsx    # Define expected outputs + branches
    MockOverrideEditor.tsx          # Per-step mock output editor
    TestResultSummary.tsx           # Pass/fail per scenario with diff view

  # ── Analytics ─────────────────────────────────────────────────────
  # MISSING FROM ORIGINAL
  analytics/
    WorkflowAnalyticsPage.tsx       # /workflows/:id/analytics — per-workflow stats
    GlobalActivityPage.tsx          # /workflows/activity — all runs across all workflows
    RunSuccessRateChart.tsx         # Line chart: success rate over time
    StepFailureHeatmap.tsx          # Grid: step_id × date → failure rate
    CostTrendChart.tsx              # Area chart: daily cost by step type
    AvgDurationChart.tsx            # Bar chart: avg duration per step
    WorkflowOverviewStats.tsx       # Stat cards: total runs, cost this month, pending HITL

  # ── YAML Code Editor ──────────────────────────────────────────────
  # MISSING FROM ORIGINAL — power user "Code" tab in builder
  code-editor/
    WorkflowYamlEditor.tsx          # Monaco editor with YAML, schema validation, autocomplete
    useYamlValidation.ts            # Real-time schema validation as user types
    YamlErrorPanel.tsx              # Inline error list below editor

  # ── Version History ───────────────────────────────────────────────
  # MISSING FROM ORIGINAL
  versions/
    VersionHistoryPanel.tsx         # Slide-out panel: list of versions with rollback button
    VersionDiffModal.tsx            # Side-by-side YAML diff between two versions
    RollbackConfirmDialog.tsx       # Confirm rollback with impact warning

  # ── Workflow Settings ─────────────────────────────────────────────
  # MISSING FROM ORIGINAL
  settings/
    WorkflowSettingsPage.tsx        # /workflows/:id/settings — general settings tab
    WorkflowPermissionsPanel.tsx    # RBAC: grant/revoke per-user or per-role permissions
    WorkflowSecretsPanel.tsx        # Manage {{vault://X}} references (name + description, no values)
    WorkflowEnvVarsPanel.tsx        # Manage {{env.X}} configuration variables
    WorkflowNotificationsPanel.tsx  # Per-event notification rules config
    WorkflowConcurrencyPanel.tsx    # Max concurrent runs + queue behavior
    WorkflowWebhookPanel.tsx        # Webhook URL display + token rotation + DLQ viewer

  # ── Canvas Collaboration ──────────────────────────────────────────
  # MISSING FROM ORIGINAL — multi-user real-time editing
  collaboration/
    useCanvasCollabSocket.ts        # WebSocket connection for co-editing (reuses app/collab/)
    CollaboratorCursors.tsx         # Show other users' canvas cursors (colored, named)
    CollaboratorAvatars.tsx         # Online user avatars in top bar
    CollabConflictBanner.tsx        # "Someone else saved changes" conflict warning

  # ── Mobile HITL (PWA) ─────────────────────────────────────────────
  # MISSING FROM ORIGINAL — approval inbox must work on mobile
  # Same ApprovalsPage.tsx + ApprovalDetailPage.tsx but with responsive layout
  # Additional mobile-specific:
  hitl/
    usePushNotifications.ts         # Register service worker for push notifications
    MobileApprovalCard.tsx          # Touch-optimized approval card (swipe to approve/reject)
    ApprovalBadge.tsx               # (existing, but add nav badge with count)
    useMagicLinkHandler.ts          # (existing)
```

### React Flow Canvas — Key Implementation Details

```typescript
// src/features/workflow/builder/WorkflowCanvas.tsx

const nodeTypes: NodeTypes = {
  trigger:      TriggerNode,
  tool:         ToolNode,
  llm:          LLMNode,
  rag:          RAGNode,
  conditional:  ConditionalNode,
  parallel:     ParallelNode,
  hitl:         HITLNode,
  http:         HTTPNode,
  transform:    TransformNode,
  sub_workflow: SubWorkflowNode,
  wait:         WaitNode,
};

// Execution status overlay on nodes (during live/test runs)
const executionStatus: Record<string, StepStatus> = useWorkflowRunStatus(runId);

// Animated edges: show flowing dots when run is active
const edgeTypes: EdgeTypes = {
  default: AnimatedFlowEdge,   // dots move along edge when step is running
};

// Bidirectional YAML sync
const { yaml, updateFromCanvas, canvasNodes, canvasEdges } = useYamlSync(workflowId);
```

### HITL Inbox — Real-time Updates

```typescript
// src/features/workflow/hitl/useApprovalSSE.ts

export function useApprovalSSE(tenantId: string) {
  const queryClient = useQueryClient();
  
  useEffect(() => {
    const es = new EventSource(`/api/v1/approvals/stream`);
    
    es.addEventListener("hitl.new_request", (e) => {
      const request = JSON.parse(e.data);
      // Update inbox list + badge count
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      // Show toast notification
      toast.info(`New approval request: ${request.step_name}`, {
        action: { label: "Review", onClick: () => navigate(`/approvals/${request.id}`) }
      });
    });
    
    es.addEventListener("hitl.escalated", (e) => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      toast.warning(`Approval escalated to you: ${JSON.parse(e.data).step_name}`);
    });
    
    return () => es.close();
  }, [tenantId]);
}
```

---

## Testing & Debugging — Test Runner

### WorkflowTestRunner

```python
# app/workflow/test_runner.py

class WorkflowTestRunner:
    """
    Orchestrates dry-run and step-through execution.
    All tool calls use MockToolAdapter — no real side effects.
    """
    
    async def dry_run(
        self,
        workflow_id: str,
        tenant_id: str,
        inputs: dict,
        mock_overrides: dict | None = None,  # step_id → mock output dict
    ) -> WorkflowRunResult:
        """Execute all steps with mocked tool calls. LLM calls are real by default."""
        return await self.runner.run(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=inputs,
            trigger_type="test",
            is_test_run=True,
            mock_overrides=mock_overrides or self._default_mocks(workflow_id),
        )
    
    async def start_step_through(
        self,
        workflow_id: str,
        tenant_id: str,
        inputs: dict,
        mock_overrides: dict | None = None,
    ) -> str:  # returns run_id
        """Start a paused run. Client calls /advance to proceed one step at a time."""
        run_id = await self.runner.run(..., mode="step_through")
        return run_id
    
    async def advance_one_step(
        self,
        run_id: str,
        tenant_id: str,
        output_override: dict | None = None,  # user manually edits step output
    ) -> StepResult:
        """Execute next pending step and pause again."""
        ...
    
    async def run_scenario(
        self,
        scenario: WorkflowTestScenario,
        tenant_id: str,
    ) -> ScenarioResult:
        """Run a saved test scenario and evaluate assertions."""
        result = await self.dry_run(
            workflow_id=scenario.workflow_id,
            tenant_id=tenant_id,
            inputs=scenario.input_fixture,
            mock_overrides=scenario.mock_overrides,
        )
        
        assertions = []
        for field, expected in (scenario.expected_outputs or {}).items():
            actual = result.outputs.get(field)
            assertions.append(AssertionResult(
                field=field,
                expected=expected,
                actual=actual,
                passed=(actual == expected),
            ))
        
        if scenario.expected_branch:
            assertions.append(AssertionResult(
                field="completed_branch",
                expected=scenario.expected_branch,
                actual=result.completed_branch,
                passed=(result.completed_branch == scenario.expected_branch),
            ))
        
        steps_reached = {r.step_id for r in result.step_results if r.status == "complete"}
        for expected_step in (scenario.expected_steps_reached or []):
            assertions.append(AssertionResult(
                field=f"step.{expected_step}.reached",
                expected=True,
                actual=(expected_step in steps_reached),
                passed=(expected_step in steps_reached),
            ))
        
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=all(a.passed for a in assertions),
            assertions=assertions,
            run_result=result,
        )
```

---

## Integration With Existing Platform

### `app/main.py` — Service Wiring

```python
# In create_app() — in-memory phase:
from app.workflow.compiler import WorkflowCompiler
from app.workflow.runner import WorkflowRunner
from app.workflow.template_store import SystemTemplateStore
from app.workflow.hitl_extension import HITLWorkflowGateway

app.state.workflow_compiler = WorkflowCompiler(...)
app.state.workflow_runner = WorkflowRunner(...)
app.state.workflow_template_store = SystemTemplateStore()
app.state.hitl_workflow_gateway = HITLWorkflowGateway(...)

# In lifespan — DB/Redis-upgraded phase:
# WorkflowRunner gets the live DB store + Redis checkpointer
# HITLWorkflowGateway gets the live Redis client

# Router registration:
app.include_router(workflow_router,          prefix="/api/v1")
app.include_router(workflow_hitl_router,     prefix="/api/v1")
app.include_router(workflow_templates_router, prefix="/api/v1")
app.include_router(workflow_runs_router,     prefix="/api/v1")
```

### Celery Tasks

```python
# app/workflow/celery_tasks.py

@celery_app.task(
    bind=True,
    queue_selector=lambda args, kwargs, options: f"workflows.{get_plan_tier(args[2])}",
    max_retries=0,  # workflow manages its own retries
    acks_late=True,
)
async def execute_workflow_run(
    self,
    run_id: str,
    workflow_id: str,
    tenant_id: str,
    mock_overrides: dict | None = None,
    resume: bool = False,
) -> None: ...


@celery_app.task(name="workflow.check_hitl_escalations")
async def check_hitl_escalations() -> None:
    """Run every 15 minutes via Celery Beat to auto-escalate overdue approvals."""
    await app.state.hitl_workflow_gateway.check_and_escalate_overdue()


@celery_app.task(name="workflow.retry_dead_letter_webhooks")
async def retry_dead_letter_webhooks() -> None:
    """Run every 5 minutes to retry failed webhook deliveries from DLQ."""
    failed = await webhook_event_store.get_retryable(max_attempts=3)
    for event in failed:
        await workflow_runner.run(event.workflow_id, inputs=event.payload, trigger_type="webhook")


# ── CELERY BEAT SCHEDULE REGISTRATION ─────────────────
# Must be added to app/scaling/celery_app.py beat_schedule dict:
#
# app.conf.beat_schedule = {
#     ...(existing schedules)...
#     "workflow-hitl-escalation-check": {
#         "task": "workflow.check_hitl_escalations",
#         "schedule": crontab(minute="*/15"),   # every 15 minutes
#     },
#     "workflow-webhook-dlq-retry": {
#         "task": "workflow.retry_dead_letter_webhooks",
#         "schedule": crontab(minute="*/5"),    # every 5 minutes
#     },
# }
```

### OTEL Tracing Per Run/Step

```python
# app/workflow/otel.py
# Each workflow run emits:
#   - Root span: workflow_run (run_id, workflow_id, trigger_type, tenant_id)
#     - Child span: step.{step_id} (step_type, tool_name, duration, cost_usd)
#       - For LLM steps: llm.call child span with prompt_tokens, completion_tokens
#       - For HTTP steps: http.request child span with url (host only), method, status
#
# Traces appear in Jaeger at OTEL_EXPORTER_OTLP_ENDPOINT.

from opentelemetry import trace
tracer = trace.get_tracer("agentverse.workflow")

class WorkflowOTELMiddleware:
    @contextmanager
    def run_span(self, state: WorkflowState):
        with tracer.start_as_current_span("workflow.run") as span:
            span.set_attribute("workflow.id", state["workflow_id"])
            span.set_attribute("workflow.run_id", state["run_id"])
            span.set_attribute("workflow.trigger_type", state["trigger_type"])
            span.set_attribute("tenant.id", state["tenant_id"])
            yield span
    
    @contextmanager  
    def step_span(self, step_id: str, step_type: str, parent_span):
        with tracer.start_as_current_span(f"workflow.step.{step_id}",
                                          context=trace.set_span_in_context(parent_span)) as span:
            span.set_attribute("step.id", step_id)
            span.set_attribute("step.type", step_type)
            yield span
```

### HTTP Step: SSRF Prevention + Circuit Breaker

```python
# app/workflow/security.py

PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # AWS/GCP metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

SSRF_BLOCKLIST_HOSTNAMES = [
    "metadata.google.internal",
    "169.254.169.254",              # AWS IMDSv1
    "fd00:ec2::254",                # AWS IMDSv2 IPv6
]

class SSRFGuard:
    def validate(self, url: str) -> None:
        """Raise SSRFBlockedError if URL resolves to a private/internal address."""
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if hostname in SSRF_BLOCKLIST_HOSTNAMES:
            raise SSRFBlockedError(f"Blocked SSRF attempt to {hostname}")
        for addr in socket.getaddrinfo(hostname, None):
            ip = ipaddress.ip_address(addr[4][0])
            for private_range in PRIVATE_IP_RANGES:
                if ip in private_range:
                    raise SSRFBlockedError(f"Blocked SSRF: {hostname} resolves to private IP {ip}")


# app/workflow/security.py — Secret Masking

SECRET_PLACEHOLDER = "[REDACTED]"

class SecretMasker:
    """
    Before persisting resolved_input to workflow_step_results,
    replace all values that came from vault:// references with [REDACTED].
    Vault ref names are tracked in WorkflowState.vault_refs_used per step.
    """
    def mask(self, resolved_input: dict, vault_refs_used: set[str]) -> dict:
        return self._deep_redact(resolved_input, vault_refs_used)
```

### SDK Additions (Python + TypeScript)

Both `agentverse-sdk-python` and `agentverse-sdk-typescript` need workflow methods:

```python
# agentverse/workflows.py (Python SDK addition)

class WorkflowClient:
    def run(self, workflow_id: str, inputs: dict, idempotency_key: str | None = None) -> WorkflowRun:
        """Trigger a workflow run. Returns immediately with run_id."""
        ...
    
    def get_run(self, run_id: str) -> WorkflowRun:
        """Get current run status and outputs."""
        ...
    
    def stream_run(self, run_id: str) -> Generator[WorkflowEvent, None, None]:
        """Stream SSE events for a live run."""
        ...
    
    def list_runs(self, workflow_id: str, status: str | None = None) -> list[WorkflowRun]:
        ...
    
    def cancel_run(self, run_id: str) -> None:
        ...
```

```typescript
// src/workflows.ts (TypeScript SDK addition)
export class WorkflowClient {
  run(workflowId: string, inputs: Record<string, unknown>, idempotencyKey?: string): Promise<WorkflowRun>;
  getRun(runId: string): Promise<WorkflowRun>;
  streamRun(runId: string): AsyncGenerator<WorkflowEvent>;
  listRuns(workflowId: string, options?: { status?: string }): Promise<WorkflowRun[]>;
  cancelRun(runId: string): Promise<void>;
}
```

### Shared Services Used (Zero New Infrastructure)

| Service | Where Defined | How Used by Workflow Engine |
|---|---|---|
| `LLMProvider` | `app/providers/base.py` | LLM step, RAG step — same interface |
| `MCPClient` | `app/mcp/client.py` | Tool step — call any registered MCP tool |
| `KnowledgeStore` | `app/knowledge/store.py` | RAG step — retrieve chunks |
| `HITLGateway` | `app/governance/hitl.py` | Extended by `HITLWorkflowGateway` |
| `AuditLog` | `app/governance/audit.py` | Every step result + HITL decision audited |
| `CostController` | `app/governance/cost.py` | Per-run cost tracking |
| `Redis checkpointer` | `app/agent/graph.py` | Same LangGraph checkpointer |
| `SSE broadcaster` | `app/services/goal_service.py` | Run events streamed to frontend |
| `Celery app` | `app/scaling/celery_app.py` | Per-plan queue routing |
| `TenantContext` | `app/tenancy/context.py` | RLS enforcement on all DB ops |
| `VaultClient` | `app/providers/vault.py` | `{{vault://X}}` secret resolution |
| `TriggerType` | `app/triggers/models.py` | Webhook/schedule/event/file_drop/alertmanager/pagerduty triggers |
| `CircuitBreaker` | `app/reliability/circuit_breaker.py` | HTTP steps — trip on repeated failures, auto-recover |
| `OpenTelemetry SDK` | `opentelemetry-sdk` (existing dep) | OTEL spans per workflow run + per step |

---

## Implementation Phases

### Phase 1 — Core Engine (Backend)
- [ ] `app/workflow/dsl.py` — Pydantic DSL schema including: `vars`, `trigger_transform`, `callback`, `run_labels`, `retry_on`/`fail_on`
- [ ] `app/workflow/state.py` — `WorkflowState` TypedDict with `vars`, `foreach_progress`, `labels`, `run_metadata`, `paused_by`
- [ ] `app/workflow/registry.py` — `StepTypeRegistry` + `StepTypeMeta` — the public plugin extension point
- [ ] `app/workflow/variables.py` — `WorkflowVariableStore` (set/get vars within a run)
- [ ] `app/workflow/audit_middleware.py` — `AutoAuditMiddleware` (auto-emit AuditEvent per step transition)
- [ ] `app/workflow/context.py` — `ContextResolver` supporting `{{inputs.X}}`, `{{steps.X.output.Y}}`, `{{vars.X}}`, `{{vault://Z}}`, `{{env.X}}`, `{{foreach.X}}`, `{{trigger.X}}`
- [ ] `app/workflow/expression_engine.py` — `ExpressionEngine` using `simpleeval` (safe, no eval())
- [ ] `app/workflow/security.py` — `SSRFGuard` + `SecretMasker`
- [ ] `app/workflow/steps/` — All **14** step types registered via `StepTypeRegistry` (incl. `set_variable`, `emit_event`)
- [ ] `app/workflow/compiler.py` — LangGraph compiler using `StepTypeRegistry.get()` for all node types
- [ ] `app/workflow/runner.py` — `WorkflowRunner`: Celery dispatch, concurrency control, OTEL, trigger_transform, callback URL, operator pause
- [ ] `app/workflow/otel.py` — OTEL spans per run/step
- [ ] `app/db/models/workflow.py` — All **7** tables + `labels`/`run_metadata`/`foreach_progress`/`paused_by` columns + Alembic migration
- [ ] `app/workflow/router.py` — All endpoints incl. pause/resume, label filter, step-type registry, HMAC replay protection on webhook ingress
- [ ] `app/workflow/storage.py` — `LargePayloadStore`: S3/MinIO offload for step outputs > 5MB
- [ ] `app/workflow/celery_tasks.py` — execute, resume, check_escalations, retry_dead_letter, **cleanup_expired_runs** + Beat schedule
- [ ] Enforce max payload size limits (1MB trigger input, 5MB step output) at ingress + step execution
- [ ] Add `run_retention_days` + `requires_publish_approval` columns to `workflow_definitions` migration
- [ ] `tests/workflow/` — 80%+ coverage (incl. plugin system, variables, auto-audit, callback, pause/resume, HMAC replay, payload limits)

### Phase 2 — HITL & Templates (Backend)
- [ ] `app/workflow/hitl_extension.py` — `HITLWorkflowGateway` (all 20 HITL features)
- [ ] `app/workflow/router_hitl.py` — Approval inbox + magic link endpoint
- [ ] `app/workflow/template_store.py` — `SystemTemplateStore`
- [ ] `app/workflow/router_templates.py` — Marketplace API
- [ ] All 25 system template YAML files in `app/workflow/templates/`
- [ ] `scripts/seed_workflow_templates.py`
- [ ] Webhook DLQ: `workflow_webhook_events` processing + retry Celery task

### Phase 3 — NL Trigger, Extra Triggers, Version History, RBAC, Analytics (Backend)
- [ ] `app/workflow/nl_trigger.py` — `NLTriggerResolver` (IntentRouter → workflow.run())
- [ ] Wire `file_drop`, `alertmanager`, `datadog`, `pagerduty` trigger types into `WorkflowRunner`
- [ ] Version history: save version on every publish, `workflow_definition_versions` table
- [ ] Rollback API: copy old version YAML → new draft version
- [ ] YAML diff endpoint: use `difflib.unified_diff` on YAML strings
- [ ] Workflow RBAC: `workflow_permissions` table + permission check middleware
- [ ] Import/Export: YAML file upload (parse + validate) + download endpoint
- [ ] Error recovery endpoint: inject step output + resume LangGraph from checkpoint
- [ ] Analytics queries: per-workflow + tenant-wide aggregations
- [ ] `app/workflow/test_runner.py` — `WorkflowTestRunner`
- [ ] SDK additions: `workflow.run()`, `workflow.get_run()`, `workflow.stream_run()` in both Python and TypeScript SDKs

### Phase 4 — Visual Builder (Frontend)
- [ ] React Flow canvas with all 12 node types (including foreach, code)
- [ ] Tool palette + step config panels for all types
- [ ] `useYamlSync.ts` — bidirectional YAML↔canvas sync
- [ ] YAML code editor tab (`WorkflowYamlEditor.tsx` with Monaco + schema validation)
- [ ] Keyboard shortcuts, auto-layout (Dagre), undo/redo stack
- [ ] Execution overlay (animated edges, per-node status badges)
- [ ] Canvas collaboration cursors (`useCanvasCollabSocket.ts`)
- [ ] **Dark mode**: all node colors, canvas background, panels via CSS variables + `dark:` Tailwind classes
- [ ] **Accessibility (WCAG 2.2 AA)**: `aria-label` on all nodes, keyboard nav (Tab/Enter/Escape), focus management
- [ ] **Skeleton loading states**: `WorkflowCanvasSkeleton`, `WorkflowListSkeleton`
- [ ] **SSE reconnection**: `Last-Event-ID` header on `EventSource` reconnect in `RunSSEStream.ts`
- [ ] Vitest unit tests + Playwright e2e tests

### Phase 5 — HITL Inbox + Marketplace (Frontend)
- [ ] Approval inbox (priority sort, SLA badges, bulk actions)
- [ ] Approval detail with all 7 context display types + custom form renderer
- [ ] Discussion thread, delegate, escalate UI
- [ ] Magic link handler (`useMagicLinkHandler.ts`)
- [ ] Push notifications PWA (`usePushNotifications.ts`)
- [ ] **Mobile-responsive approval cards** (`MobileApprovalCard.tsx`) — swipe to approve/reject, touch targets ≥44px
- [ ] **Skeleton loading states**: `ApprovalInboxSkeleton.tsx`, `TemplateCardSkeleton.tsx`
- [ ] Template marketplace grid + preview modal + "Try It" dry-run
- [ ] `ApprovalBadge.tsx` in global nav (SSE-updated count)
- [ ] **Dark mode** on all HITL + marketplace components

### Phase 6 — Settings, Analytics, Testing UI (Frontend)
- [ ] `WorkflowSettingsPage.tsx` — RBAC panel, secrets panel, env vars panel, notifications panel, concurrency panel, webhook panel
- [ ] `VersionHistoryPanel.tsx` + `VersionDiffModal.tsx` + rollback flow
- [ ] `WorkflowAnalyticsPage.tsx` — success rate chart, step failure heatmap, cost trend
- [ ] `GlobalActivityPage.tsx` — tenant-wide all-runs feed
- [ ] Test runner panel — step-through controls, output editor, scenario manager
- [ ] Import YAML (file upload) + Export button in workflow list
- [ ] **Accessibility audit pass**: run axe-core in CI; all WCAG 2.2 AA violations must be zero before merge
- [ ] **Dark mode** + `prefers-reduced-motion` support across all 6 phases of UI
