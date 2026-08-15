# AgentVerse Trigger Framework — World-Class Implementation Plan

> **Status:** Ready for Execution  
> **Date:** 2026-08-16  
> **Author:** AgentVerse Platform Team  
> **Spec Reference:** `docs/superpowers/specs/2026-08-15-trigger-architecture-specification.md`  
> **Baseline:** 10 trigger types, 563 lines, `app/triggers/` (models, store, nl_scheduler)  
> **Target:** 58 trigger types, 9 families, 47 supporting infrastructure features  
> **Sprints:** 10 sprints (5 phases × 2 sprints each) — ~20 weeks

---

## 0. Pre-Flight: Baseline Assessment

### What Already Exists

| File | Lines | Covers |
|---|---|---|
| `app/triggers/models.py` | 49 | 10 `TriggerType` enum values, minimal `TriggerSpec` dataclass |
| `app/triggers/store.py` | 445 | `ScheduleStore` — in-memory + Postgres + Redis CRUD, pause/resume, sync_from_db |
| `app/triggers/nl_scheduler.py` | 69 | Basic NLScheduler (cron/interval/once only) |
| `app/api/schedules.py` | ~200 | REST CRUD for triggers (schedules) |
| `app/api/agents.py` | ~300 | Partial trigger fire endpoint |

### What Is Missing (Gap Summary)

| Gap | Impact |
|---|---|
| 48 trigger types not defined | No goal chaining, no conversational, no typed webhooks, no data, no IoT |
| `TriggerDispatcher` not built | No unified dispatch pipeline |
| `TriggerEvent` table/log not built | No audit trail, no replay |
| No deduplication | Duplicate firings → duplicate goals |
| No CEL evaluator | No conditional triggers |
| No DLQ | Silent failures |
| No circuit breaker | Cascading failures |
| No bulkhead | Tenant resource starvation |
| No RBAC | Any authenticated user can fire any trigger |
| No rate limiting | Abuse possible |
| No OTel spans | Zero observability |
| No simulation mode | Cannot test without real side effects |
| `NLScheduler` covers 3/58 types | 95% of NL trigger config missing |

---

## 1. Architecture Principles (Enforcement Checklist)

Every deliverable in every phase MUST satisfy:

- [ ] **Single dispatch path** — all triggers go through `TriggerDispatcher._dispatch()`; zero type-specific goal creation elsewhere
- [ ] **Tenant isolation** — every new table has `tenant_id UUID NOT NULL` + RLS policy
- [ ] **Idempotency** — every firing writes an `idempotency_key`; `UNIQUE (tenant_id, idempotency_key)` enforced
- [ ] **Immutable event log** — `trigger_events` is append-only; no UPDATE or DELETE except via admin API
- [ ] **OTel spans** — every `_dispatch()` call opens a root span; child spans for each pipeline step
- [ ] **Prometheus metrics** — every new trigger type adds to `agentverse_trigger_fired_total` labels
- [ ] **No hardcoded secrets** — all credentials use `vault://` references
- [ ] **Deduplication** — 60-second window enforced before any goal creation
- [ ] **Rate limiting** — `max_firings_per_hour` checked against plan ceiling before dispatch
- [ ] **Circuit breaker** — per-trigger `TriggerCircuitBreaker` checked before dispatch
- [ ] **Bulkhead** — per-tenant `TriggerBulkhead` concurrency cap checked before enqueue
- [ ] **Test coverage ≥ 80%** — each phase ships with unit + integration tests

---

## 2. File & Module Map

```
app/triggers/
├── __init__.py
├── models.py              ← EXTEND: 58 TriggerType + full TriggerSpec (60+ fields)
├── store.py               ← EXTEND: rename ScheduleStore → TriggerStore, add versioning
├── nl_scheduler.py        ← EXTEND: NLScheduler covers all 58 types
├── dispatcher.py          ← CREATE: TriggerDispatcher (the core engine)
├── events.py              ← CREATE: TriggerEvent, TriggerAuditEvent dataclasses
├── dedup.py               ← CREATE: idempotency_key derivation per family
├── condition.py           ← CREATE: CEL evaluator, template renderer
├── rate_limiter.py        ← CREATE: per-trigger rate cap + plan ceiling
├── circuit_breaker.py     ← CREATE: TriggerCircuitBreaker state machine
├── bulkhead.py            ← CREATE: TriggerBulkhead per-tenant concurrency
├── quota.py               ← CREATE: TriggerQuotaEnforcer (CREATE-time check)
├── rbac.py                ← CREATE: TriggerPermissionMatrix
├── rotation.py            ← CREATE: WebhookSecretRotation
├── simulation.py          ← CREATE: SimulatedTriggerResult, TriggerChaosHarness
├── dlq.py                 ← CREATE: DLQ write + retry logic
├── channels/
│   ├── __init__.py
│   ├── gateway.py         ← CREATE: ChannelIngestionGateway router
│   ├── slack.py           ← CREATE: CHAT_COMMAND, CHAT_KEYWORD, CHAT_MENTION, SLACK_EVENT
│   ├── teams.py           ← CREATE: TEAMS_WEBHOOK, CHAT_COMMAND for Teams
│   ├── discord.py         ← CREATE: DISCORD_EVENT
│   ├── email.py           ← CREATE: EMAIL_INTENT, EMAIL_ARRIVAL
│   ├── sms.py             ← CREATE: SMS_INBOUND
│   ├── voice.py           ← CREATE: VOICE_TRANSCRIPT, MEETING_ENDED
│   └── form.py            ← CREATE: FORM_SUBMISSION
├── webhooks/
│   ├── __init__.py
│   ├── verifier.py        ← CREATE: WebhookSignatureVerifier (HMAC)
│   ├── github.py          ← CREATE: GITHUB_WEBHOOK parser
│   ├── jira.py            ← CREATE: JIRA_WEBHOOK parser
│   ├── stripe.py          ← CREATE: STRIPE_WEBHOOK parser
│   ├── salesforce.py      ← CREATE: SALESFORCE_EVENT parser
│   ├── confluence.py      ← CREATE: CONFLUENCE_WEBHOOK parser
│   └── linear.py          ← CREATE: LINEAR_WEBHOOK parser
├── data/
│   ├── __init__.py
│   ├── db_row_change.py   ← CREATE: pg_notify consumer for DB_ROW_CHANGE
│   ├── s3_event.py        ← CREATE: S3_EVENT consumer
│   ├── google_sheets.py   ← CREATE: GOOGLE_SHEETS polling
│   ├── sharepoint.py      ← CREATE: SHAREPOINT delta feed
│   ├── rss.py             ← CREATE: RSS_FEED polling
│   └── api_poll.py        ← CREATE: API_POLL generic poller
├── monitoring/
│   ├── __init__.py
│   ├── grafana.py         ← CREATE: GRAFANA_ALERT
│   ├── cloudwatch.py      ← CREATE: CLOUDWATCH
│   ├── sentry.py          ← CREATE: SENTRY_ISSUE
│   └── log_pattern.py     ← CREATE: LOG_PATTERN streaming
├── iot/
│   ├── __init__.py
│   ├── mqtt.py            ← CREATE: MQTT broker consumer
│   ├── geofence.py        ← CREATE: GEOFENCE evaluator
│   └── sensor.py          ← CREATE: SENSOR_THRESHOLD aggregator
└── advanced/
    ├── __init__.py
    ├── graphql_sub.py     ← CREATE: GRAPHQL_SUBSCRIPTION
    ├── websocket.py       ← CREATE: WEBSOCKET_MESSAGE
    └── price.py           ← CREATE: PRICE_THRESHOLD polling

app/api/
├── triggers.py            ← CREATE: full CRUD + fire + simulate + DLQ + rotate-secret
├── channels/
│   └── ingestion.py       ← CREATE: ChannelIngestionGateway routes
└── state_machines.py      ← CREATE: STATE_TRANSITION state machine API

app/db/migrations/
├── 0045_triggers_v2_core.py         ← Phase 0
├── 0046_trigger_events_table.py     ← Phase 0
├── 0047_trigger_dlq_table.py        ← Phase 0
├── 0048_trigger_state_machines.py   ← Phase 3
├── 0049_trigger_channel_mappings.py ← Phase 2
└── 0050_trigger_business_calendar.py ← Phase 5

tests/triggers/
├── test_dispatcher.py
├── test_dedup.py
├── test_condition.py
├── test_rate_limiter.py
├── test_circuit_breaker.py
├── test_bulkhead.py
├── test_quota.py
├── test_rbac.py
├── test_simulation.py
├── test_dlq.py
├── channels/
│   ├── test_slack.py
│   ├── test_teams.py
│   └── test_email.py
├── webhooks/
│   ├── test_verifier.py
│   ├── test_github.py
│   └── test_stripe.py
├── data/
│   └── test_db_row_change.py
└── iot/
    └── test_mqtt.py
```

---

## 3. Database Migration Plan

All migrations are **forward-only, non-locking** (no table rewrites).

### Migration 0045 — `triggers` table v2 (Phase 0, Sprint 0)

```sql
-- Rename schedules table to triggers (backward-compatible alias view)
ALTER TABLE schedules RENAME TO triggers;
CREATE VIEW schedules AS SELECT * FROM triggers;  -- keep old API working

-- Add all new columns from TriggerSpec (58 types × fields)
ALTER TABLE triggers
  ADD COLUMN IF NOT EXISTS family              TEXT,
  ADD COLUMN IF NOT EXISTS priority            TEXT NOT NULL DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS max_firings_per_hour INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS condition           TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS goal_template       TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS expires_at          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS on_failure_notify   TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS tags                JSONB NOT NULL DEFAULT '[]',
  -- Family A
  ADD COLUMN IF NOT EXISTS business_calendar_id TEXT,
  ADD COLUMN IF NOT EXISTS relative_to_field    TEXT,
  ADD COLUMN IF NOT EXISTS deadline_field        TEXT,
  -- Family B
  ADD COLUMN IF NOT EXISTS watch_goal_id         UUID,
  ADD COLUMN IF NOT EXISTS watch_agent_id        UUID,
  ADD COLUMN IF NOT EXISTS score_threshold       FLOAT,
  ADD COLUMN IF NOT EXISTS score_dimension       TEXT,
  ADD COLUMN IF NOT EXISTS hitl_queue_id         UUID,
  ADD COLUMN IF NOT EXISTS memory_type           TEXT,
  -- Family C
  ADD COLUMN IF NOT EXISTS channel_type          TEXT,
  ADD COLUMN IF NOT EXISTS channel_id            TEXT,
  ADD COLUMN IF NOT EXISTS command_pattern       TEXT,
  ADD COLUMN IF NOT EXISTS keyword_pattern       TEXT,
  ADD COLUMN IF NOT EXISTS mention_bot_id        TEXT,
  ADD COLUMN IF NOT EXISTS email_sender_filter   TEXT,
  ADD COLUMN IF NOT EXISTS email_subject_pattern TEXT,
  ADD COLUMN IF NOT EXISTS phone_number_filter   TEXT,
  ADD COLUMN IF NOT EXISTS voice_language        TEXT,
  ADD COLUMN IF NOT EXISTS meeting_platform      TEXT,
  ADD COLUMN IF NOT EXISTS form_id               TEXT,
  -- Family D
  ADD COLUMN IF NOT EXISTS counter_key           TEXT,
  ADD COLUMN IF NOT EXISTS counter_threshold     INTEGER,
  ADD COLUMN IF NOT EXISTS counter_window_secs   INTEGER,
  ADD COLUMN IF NOT EXISTS compound_logic        TEXT,
  ADD COLUMN IF NOT EXISTS compound_trigger_ids  JSONB,
  ADD COLUMN IF NOT EXISTS state_machine_id      UUID,
  ADD COLUMN IF NOT EXISTS from_state            TEXT,
  ADD COLUMN IF NOT EXISTS to_state              TEXT,
  ADD COLUMN IF NOT EXISTS window_seconds        INTEGER,
  ADD COLUMN IF NOT EXISTS window_field          TEXT,
  ADD COLUMN IF NOT EXISTS window_aggregation    TEXT,
  ADD COLUMN IF NOT EXISTS window_threshold      FLOAT,
  -- Family E
  ADD COLUMN IF NOT EXISTS webhook_signature_secret TEXT,
  ADD COLUMN IF NOT EXISTS allowed_api_keys      JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS github_event_filter   TEXT,
  ADD COLUMN IF NOT EXISTS jira_project_filter   TEXT,
  ADD COLUMN IF NOT EXISTS stripe_event_filter   TEXT,
  ADD COLUMN IF NOT EXISTS salesforce_object     TEXT,
  -- Family F
  ADD COLUMN IF NOT EXISTS db_table              TEXT,
  ADD COLUMN IF NOT EXISTS db_operation          TEXT,
  ADD COLUMN IF NOT EXISTS db_filter             TEXT,
  ADD COLUMN IF NOT EXISTS s3_bucket             TEXT,
  ADD COLUMN IF NOT EXISTS s3_prefix             TEXT,
  ADD COLUMN IF NOT EXISTS s3_events             JSONB,
  ADD COLUMN IF NOT EXISTS rss_url               TEXT,
  ADD COLUMN IF NOT EXISTS sheets_spreadsheet_id TEXT,
  ADD COLUMN IF NOT EXISTS sharepoint_site_url   TEXT,
  ADD COLUMN IF NOT EXISTS sharepoint_library    TEXT,
  -- Family G
  ADD COLUMN IF NOT EXISTS alert_severity_filter TEXT,
  ADD COLUMN IF NOT EXISTS log_pattern_regex     TEXT,
  ADD COLUMN IF NOT EXISTS log_stream            TEXT,
  -- Family H
  ADD COLUMN IF NOT EXISTS poll_url              TEXT,
  ADD COLUMN IF NOT EXISTS poll_method           TEXT DEFAULT 'GET',
  ADD COLUMN IF NOT EXISTS poll_headers          JSONB,
  ADD COLUMN IF NOT EXISTS poll_body             JSONB,
  ADD COLUMN IF NOT EXISTS poll_jsonpath         TEXT,
  ADD COLUMN IF NOT EXISTS poll_expected_value   TEXT,
  ADD COLUMN IF NOT EXISTS graphql_endpoint      TEXT,
  ADD COLUMN IF NOT EXISTS graphql_subscription  TEXT,
  ADD COLUMN IF NOT EXISTS websocket_url         TEXT,
  ADD COLUMN IF NOT EXISTS price_symbol          TEXT,
  ADD COLUMN IF NOT EXISTS price_threshold       FLOAT,
  ADD COLUMN IF NOT EXISTS price_direction       TEXT,
  -- Family I
  ADD COLUMN IF NOT EXISTS mqtt_broker_url       TEXT,
  ADD COLUMN IF NOT EXISTS mqtt_topic            TEXT,
  ADD COLUMN IF NOT EXISTS geofence_polygon      JSONB,
  ADD COLUMN IF NOT EXISTS geofence_action       TEXT,
  ADD COLUMN IF NOT EXISTS sensor_device_id      TEXT,
  ADD COLUMN IF NOT EXISTS sensor_metric         TEXT,
  ADD COLUMN IF NOT EXISTS sensor_threshold      FLOAT,
  ADD COLUMN IF NOT EXISTS sensor_comparison     TEXT,
  -- Security
  ADD COLUMN IF NOT EXISTS allowed_roles         JSONB NOT NULL DEFAULT '["admin","developer","operator"]',
  ADD COLUMN IF NOT EXISTS version               INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS version_history       JSONB NOT NULL DEFAULT '[]';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_triggers_tenant_type ON triggers (tenant_id, trigger_type);
CREATE INDEX IF NOT EXISTS idx_triggers_tenant_enabled ON triggers (tenant_id, enabled);
CREATE INDEX IF NOT EXISTS idx_triggers_watch_goal ON triggers (watch_goal_id) WHERE watch_goal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_triggers_expires_at ON triggers (expires_at) WHERE expires_at IS NOT NULL;

-- Enable RLS (if not already)
ALTER TABLE triggers ENABLE ROW LEVEL SECURITY;
CREATE POLICY trigger_tenant_isolation ON triggers
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### Migration 0046 — `trigger_events` table (Phase 0)

```sql
CREATE TABLE trigger_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL,
    trigger_id       UUID NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
    trigger_type     TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    fired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload          JSONB NOT NULL DEFAULT '{}',
    goal_created     BOOLEAN NOT NULL DEFAULT FALSE,
    goal_id          UUID,
    skip_reason      TEXT,          -- 'dedup' | 'rate_limit' | 'condition_false' | 'circuit_open' | 'bulkhead_full' | NULL
    processing_ms    INTEGER,
    CONSTRAINT uq_trigger_event_idempotency UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX idx_trigger_events_trigger_id ON trigger_events (trigger_id, fired_at DESC);
CREATE INDEX idx_trigger_events_tenant_fired ON trigger_events (tenant_id, fired_at DESC);
ALTER TABLE trigger_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY trigger_events_tenant ON trigger_events
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### Migration 0047 — `trigger_dlq` table (Phase 0)

```sql
CREATE TABLE trigger_dlq (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL,
    trigger_id     UUID NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
    failed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    failure_type   TEXT NOT NULL,
    error_message  TEXT,
    raw_payload    JSONB,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    resolved_at    TIMESTAMPTZ,
    resolved_by    TEXT
);
CREATE INDEX idx_trigger_dlq_tenant ON trigger_dlq (tenant_id, failed_at DESC);
ALTER TABLE trigger_dlq ENABLE ROW LEVEL SECURITY;
CREATE POLICY trigger_dlq_tenant ON trigger_dlq
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### Migration 0048 — `trigger_audit_events` table (Phase 0)

```sql
CREATE TABLE trigger_audit_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    trigger_id   UUID,
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    action       TEXT NOT NULL,  -- create|update|enable|disable|delete|fire_manual|rotate_secret
    before_state JSONB,
    after_state  JSONB,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address   TEXT,
    request_id   TEXT
);
CREATE INDEX idx_trigger_audit_tenant ON trigger_audit_events (tenant_id, occurred_at DESC);
ALTER TABLE trigger_audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY trigger_audit_tenant ON trigger_audit_events
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### Migration 0049 — `channel_tenant_mappings` (Phase 2)

```sql
CREATE TABLE channel_tenant_mappings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    channel_type TEXT NOT NULL,  -- 'slack' | 'teams' | 'discord' | 'email' | 'sms'
    channel_id   TEXT NOT NULL,  -- workspace_id / channel_id / phone_number
    verified_at  TIMESTAMPTZ,
    CONSTRAINT uq_channel_mapping UNIQUE (channel_type, channel_id)
);
ALTER TABLE channel_tenant_mappings ENABLE ROW LEVEL SECURITY;
```

### Migration 0050 — `state_machines` (Phase 3)

```sql
CREATE TABLE state_machines (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL,
    name        TEXT NOT NULL,
    states      JSONB NOT NULL DEFAULT '[]',
    transitions JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE state_machine_instances (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    machine_id      UUID NOT NULL REFERENCES state_machines(id),
    entity_id       TEXT NOT NULL,
    current_state   TEXT NOT NULL,
    history         JSONB NOT NULL DEFAULT '[]',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE state_machines ENABLE ROW LEVEL SECURITY;
ALTER TABLE state_machine_instances ENABLE ROW LEVEL SECURITY;
```

---

## 4. Phase 0 — Foundation Layer (Pre-Sprint)

> **Duration:** 3 days (prerequisite for all phases)  
> **Owner:** Platform Core  
> **Deliverables:** The infrastructure every phase depends on

### 4.1 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P0-01 | `app/triggers/models.py` | `TriggerType` enum: 58 values; `TriggerSpec` dataclass: 60+ fields |
| P0-02 | `app/db/migrations/0045_triggers_v2_core.py` | Alembic migration passes `upgrade head` and `downgrade -1` |
| P0-03 | `app/db/migrations/0046_trigger_events_table.py` | UNIQUE constraint on `(tenant_id, idempotency_key)` verified |
| P0-04 | `app/db/migrations/0047_trigger_dlq_table.py` | RLS policy applied and tested |
| P0-05 | `app/db/migrations/0048_trigger_audit_events.py` | Audit table created with indexes |
| P0-06 | `app/triggers/events.py` | `TriggerEvent`, `TriggerAuditEvent` dataclasses with full type hints |
| P0-07 | `app/triggers/dedup.py` | `derive_idempotency_key(spec, payload, context)` for all 9 families |
| P0-08 | `app/triggers/condition.py` | `CELEvaluator.evaluate(expression, payload)` — sandboxed, timeout 500ms |
| P0-09 | `app/triggers/condition.py` | `TemplateRenderer.render(template, payload)` — sandboxed Jinja2 |
| P0-09b | `app/triggers/models.py` | `validate_cron(expression, plan)` via `croniter` — syntax + plan-tier minimum interval |
| P0-10 | `app/triggers/rate_limiter.py` | `TriggerRateLimiter.check(trigger_id, max_per_hour)` via Redis INCR |
| P0-11 | `app/triggers/circuit_breaker.py` | `TriggerCircuitBreaker` — closed/open/half-open state machine |
| P0-12 | `app/triggers/bulkhead.py` | `TriggerBulkhead` — Redis-backed INCR/DECR concurrency counter |
| P0-13 | `app/triggers/quota.py` | `TriggerQuotaEnforcer.check_create(tenant_id)` |
| P0-14 | `app/triggers/rbac.py` | `TriggerPermissionMatrix` — 5 roles × 8 operations |
| P0-15 | `app/triggers/dispatcher.py` | `TriggerDispatcher._dispatch()` skeleton — pipeline steps as hooks |
| P0-16 | `app/triggers/dlq.py` | `write_to_dlq()`, `retry_from_dlq()`, `dismiss_dlq_entry()`; retry uses exponential backoff: 3 attempts at 5s/15s/45s delays |
| P0-17 | `app/triggers/store.py` | Rename `ScheduleStore → TriggerStore`; backward-compat alias |
| P0-18 | `app/api/triggers.py` | CRUD endpoints: POST/GET/PATCH/DELETE `/api/v1/triggers` |
| P0-19 | `app/api/triggers.py` | DLQ endpoints: GET/POST `/api/v1/triggers/dlq`, POST `.../retry`, POST `.../dismiss` |
| P0-20 | `tests/triggers/test_dispatcher.py` | `test_dispatch_happy_path`, `test_dedup_blocks_duplicate`, `test_rate_limit_blocks` |

### 4.2 `TriggerDispatcher` Pipeline (core contract)

```python
class TriggerDispatcher:
    async def dispatch(
        self,
        trigger_spec: TriggerSpec,
        payload: dict,
        tenant_ctx: TenantContext,
        *,
        simulation: bool = False,
    ) -> TriggerEvent:
        with tracer.start_as_current_span("agentverse.trigger.fire") as span:
            # Step 1: RBAC check (caller must have 'fire' permission)
            await self._rbac_check(trigger_spec, tenant_ctx)
            # Step 2: Payload size enforcement
            self._check_payload_size(payload, tenant_ctx.plan)
            # Step 3: Signature verification (webhooks only)
            await self._verify_signature(trigger_spec, payload)
            # Step 4: Idempotency key derivation
            idempotency_key = derive_idempotency_key(trigger_spec, payload)
            # Step 5: Deduplication check
            if await self._is_duplicate(idempotency_key, tenant_ctx):
                return self._skip_event(idempotency_key, "dedup")
            # Step 6: Rate limit check
            if not await self._rate_limit_check(trigger_spec, tenant_ctx):
                return self._skip_event(idempotency_key, "rate_limit")
            # Step 7: Circuit breaker check
            if not self._circuit_breaker_check(trigger_spec):
                return self._skip_event(idempotency_key, "circuit_open")
            # Step 8: Bulkhead check
            if not await self._bulkhead_check(tenant_ctx):
                return self._skip_event(idempotency_key, "bulkhead_full")
            # Step 9: Condition evaluation
            if not await self._evaluate_condition(trigger_spec, payload):
                return self._skip_event(idempotency_key, "condition_false")
            # Step 10: Template rendering
            goal_text = await self._render_goal_template(trigger_spec, payload)
            # Step 11: Simulation short-circuit
            if simulation:
                return self._simulate_result(idempotency_key, goal_text)
            # Step 12: Goal creation
            goal_id = await self._create_goal(trigger_spec, goal_text, tenant_ctx)
            # Step 13: Persist TriggerEvent
            event = await self._persist_event(trigger_spec, payload, idempotency_key, goal_id)
            return event
```

### 4.3 OTel Span Hierarchy Implementation

```python
# In dispatcher.py — each step is a child span
with tracer.start_as_current_span("agentverse.trigger.validate_signature"): ...
with tracer.start_as_current_span("agentverse.trigger.evaluate_condition"): ...
with tracer.start_as_current_span("agentverse.trigger.dedup_check"): ...
with tracer.start_as_current_span("agentverse.trigger.rate_limit_check"): ...
with tracer.start_as_current_span("agentverse.trigger.circuit_breaker_check"): ...
with tracer.start_as_current_span("agentverse.trigger.dispatch_goal"): ...
```

### 4.4 Prometheus Metrics Registration

```python
# In a new app/triggers/metrics.py
from prometheus_client import Counter, Histogram, Gauge

TRIGGER_FIRED_TOTAL = Counter(
    "agentverse_trigger_fired_total", "Trigger firings",
    ["trigger_type", "tenant_plan", "result"]
)
TRIGGER_FIRE_LATENCY = Histogram(
    "agentverse_trigger_fire_latency_seconds", "Dispatch latency",
    ["trigger_type"], buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5]
)
TRIGGER_CIRCUIT_STATE = Gauge(
    "agentverse_trigger_circuit_state",
    "0=closed 1=half_open 2=open",
    ["trigger_id", "tenant_id"]
)
TRIGGER_DLQ_DEPTH = Gauge(
    "agentverse_trigger_dlq_depth", "DLQ entries",
    ["trigger_type", "tenant_id"]
)
```

---

## 5. Phase 1 — Goal Chaining (Sprint 1–2)

> **Duration:** 2 weeks  
> **Trigger families:** B (Goal/Agent Chain)  
> **Types:** GOAL_COMPLETED, GOAL_FAILED, GOAL_SCORE_BELOW, HITL_APPROVED, HITL_REJECTED, MEMORY_CREATED

### 5.1 Architecture

Goal chain triggers subscribe to Redis Pub/Sub channels that `GoalService` publishes to on state transitions.

```
GoalService.complete_goal()
  → redis.publish("goal.completed", {goal_id, tenant_id, score, output})
    → ChainTriggerConsumer.on_message()
      → TriggerStore.find_triggers(type=GOAL_COMPLETED, watch_goal_id=goal_id)
        → TriggerDispatcher.dispatch() for each matching trigger
```

### 5.2 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P1-01 | `app/triggers/consumers/chain.py` | `ChainTriggerConsumer(redis)` — async subscriber, deserializes `GoalEvent` |
| P1-02 | `app/triggers/consumers/chain.py` | On GOAL_COMPLETED: queries `TriggerStore` by `watch_goal_id` + `watch_agent_id` |
| P1-03 | `app/triggers/consumers/chain.py` | On GOAL_FAILED: dispatches GOAL_FAILED triggers; passes failure reason in payload |
| P1-04 | `app/triggers/consumers/chain.py` | On GOAL_SCORE_BELOW: evaluates `score_threshold` + `score_dimension` filter |
| P1-05 | `app/triggers/consumers/hitl.py` | `HITLTriggerConsumer` — subscribes to HITL approval/rejection Redis events |
| P1-06 | `app/triggers/consumers/memory.py` | `MemoryTriggerConsumer` — subscribes to memory creation events |
| P1-07 | `app/triggers/models.py` | `TriggerSpec.idempotency_key` for Family B: `trigger_id + source_goal_id + completion_event_id` |
| P1-08 | `app/main.py` / `lifespan` | Register `ChainTriggerConsumer` as background task on startup |
| P1-09 | `app/api/triggers.py` | Pipeline preview endpoint: `GET /api/v1/triggers/{id}/pipeline-preview` |
| P1-10 | `tests/triggers/test_chain.py` | `test_goal_completed_fires_trigger`, `test_dedup_blocks_duplicate_chain`, `test_score_threshold_filter` |

### 5.3 Goal-Chain Depth Limit

```python
# In ChainTriggerConsumer
MAX_CHAIN_DEPTH = 10  # configurable via Settings

async def on_message(self, event: GoalEvent) -> None:
    chain_depth = event.metadata.get("trigger_chain_depth", 0)
    if chain_depth >= MAX_CHAIN_DEPTH:
        logger.warning("trigger_chain_depth_exceeded", depth=chain_depth, goal_id=event.goal_id)
        return
    # ... dispatch with chain_depth+1 in goal metadata
```

### 5.4 Pipeline Example (from spec §5.6)

```
GOAL_COMPLETED(invoice-extraction) → GOAL_SCORE_BELOW(0.85) skips
GOAL_COMPLETED(invoice-extraction, score=0.92) → payment-processing
GOAL_COMPLETED(payment-processing) → HITL_APPROVED gate → notification-goal
```

---

## 6. Phase 2 — Conversational Triggers (Sprint 3–4)

> **Duration:** 2 weeks  
> **Trigger families:** C (Conversational/Chat)  
> **Types:** CHAT_COMMAND, CHAT_KEYWORD, CHAT_MENTION, EMAIL_INTENT, SMS_INBOUND, VOICE_TRANSCRIPT, MEETING_ENDED, FORM_SUBMISSION

### 6.1 Architecture

```
External platform → ChannelIngestionGateway
  → signature verification
  → channel_tenant_mappings lookup
  → NLIntentClassifier (embedding → trigger type)
    → TriggerStore.find_matching_triggers(type, channel, keyword)
      → TriggerDispatcher.dispatch()
```

### 6.2 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P2-01 | `app/triggers/channels/gateway.py` | `ChannelIngestionGateway` — routes by `channel_type` to correct handler |
| P2-02 | `app/api/channels/ingestion.py` | POST `/api/v1/channels/slack/events` — Slack Events API endpoint |
| P2-03 | `app/api/channels/ingestion.py` | POST `/api/v1/channels/teams/events` — Teams webhook endpoint |
| P2-04 | `app/api/channels/ingestion.py` | POST `/api/v1/channels/email/inbound` — SendGrid Inbound Parse |
| P2-05 | `app/api/channels/ingestion.py` | POST `/api/v1/channels/sms/inbound` — Twilio webhook |
| P2-06 | `app/api/channels/ingestion.py` | POST `/api/v1/channels/forms/{form_id}` — form submission |
| P2-07 | `app/triggers/channels/slack.py` | Parse Slack Events API → `CHAT_COMMAND`, `CHAT_KEYWORD`, `CHAT_MENTION` |
| P2-08 | `app/triggers/channels/teams.py` | Parse Teams webhook → `TEAMS_WEBHOOK`, `CHAT_COMMAND` |
| P2-09 | `app/triggers/channels/email.py` | Parse SendGrid inbound → `EMAIL_INTENT` via `NLIntentClassifier` |
| P2-10 | `app/triggers/channels/sms.py` | Parse Twilio webhook → `SMS_INBOUND` |
| P2-11 | `app/triggers/channels/voice.py` | Parse transcript webhook → `VOICE_TRANSCRIPT`, `MEETING_ENDED` |
| P2-12 | `app/triggers/nl_classifier.py` | `NLIntentClassifier` — embedding cosine similarity + LLM fallback |
| P2-13 | `app/db/migrations/0049_trigger_channel_mappings.py` | `channel_tenant_mappings` table with UNIQUE constraint |
| P2-14 | `app/api/triggers.py` | POST `/api/v1/triggers/channel-mappings` — register Slack workspace to tenant |
| P2-15 | `tests/triggers/channels/test_slack.py` | `test_slack_command_fires_trigger`, `test_signature_verification_rejects_invalid` |

### 6.3 `NLIntentClassifier` Design

```python
class NLIntentClassifier:
    """Classify a natural-language message → trigger type + parameters."""

    async def classify(self, text: str, channel: str) -> NLClassificationResult:
        # 1. Command prefix detection (fast path)
        if text.startswith("/"):
            return NLClassificationResult(type=TriggerType.CHAT_COMMAND, confidence=1.0)
        # 2. Keyword matching (medium path)
        for keyword in self._keywords:
            if keyword.lower() in text.lower():
                return NLClassificationResult(type=TriggerType.CHAT_KEYWORD, confidence=0.9)
        # 3. Embedding similarity (slow path, fallback)
        embedding = await self._embedder.embed(text)
        best_match = self._find_nearest(embedding, self._intent_index)
        if best_match.score < 0.7:
            # 4. LLM fallback (last resort)
            return await self._llm_classify(text)
        return best_match
```

---

## 7. Phase 3 — Condition & State Machine Triggers (Sprint 5–6)

> **Duration:** 2 weeks  
> **Trigger families:** D (Condition/State)  
> **Types:** CONDITION, COUNTER_THRESHOLD, COMPOUND, STATE_TRANSITION, WINDOW_AGGREGATE

### 7.1 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P3-01 | `app/triggers/condition.py` | `CELEvaluator` — wraps `cel-python`, 500ms timeout, deny-list of dangerous attributes |
| P3-02 | `app/triggers/condition.py` | `CounterThresholdEvaluator` — Redis INCR with TTL sliding window |
| P3-03 | `app/triggers/condition.py` | `WindowAggregateEvaluator` — Redis sorted set for time-window aggregation |
| P3-04 | `app/triggers/condition.py` | `CompoundTriggerEvaluator` — AND/OR/NOT over child trigger states |
| P3-05 | `app/triggers/state_machine.py` | `StateMachine` — create, transition, query state; emits events on state change |
| P3-06 | `app/db/migrations/0050_trigger_state_machines.py` | `state_machines` + `state_machine_instances` tables with RLS |
| P3-07 | `app/api/state_machines.py` | CRUD for state machine definitions + transitions |
| P3-08 | `app/api/state_machines.py` | POST `/api/v1/state-machines/{id}/transition` — trigger state change |
| P3-09 | `app/triggers/consumers/state.py` | `StateTriggerConsumer` — subscribes to state change events |
| P3-10 | `tests/triggers/test_condition.py` | `test_cel_evaluates_condition`, `test_counter_threshold_fires_at_n`, `test_compound_and_logic` |

### 7.2 CEL Evaluator Safety

```python
from celpy import celtypes, Environment, Runner

class CELEvaluator:
    BLOCKED_ATTRIBUTES = frozenset(["__class__", "__import__", "eval", "exec", "open"])
    TIMEOUT_SECONDS = 0.5

    def evaluate(self, expression: str, payload: dict) -> bool:
        env = Environment()
        ast = env.compile(expression)
        prog = env.program(ast)
        activation = {
            "payload": celtypes.MapType({
                celtypes.StringType(k): _to_cel(v)
                for k, v in payload.items()
                if k not in self.BLOCKED_ATTRIBUTES
            })
        }
        with signal_timeout(self.TIMEOUT_SECONDS):
            result = prog.evaluate(activation)
        return bool(result)
```

---

## 8. Phase 4 — Typed Webhooks + Data Triggers (Sprint 7–8)

> **Duration:** 2 weeks  
> **Trigger families:** E (External Events), F (Data/File), G (Monitoring/Alerting)  
> **Types:** GITHUB_WEBHOOK, JIRA_WEBHOOK, STRIPE_WEBHOOK, SLACK_EVENT, SALESFORCE_EVENT, CONFLUENCE_WEBHOOK, LINEAR_WEBHOOK, FILE_DROP (extend), DB_ROW_CHANGE, S3_EVENT, EMAIL_ARRIVAL, RSS_FEED, GOOGLE_SHEETS, SHAREPOINT, ALERTMANAGER (extend), DATADOG (extend), PAGERDUTY (extend), GRAFANA_ALERT, CLOUDWATCH, SENTRY_ISSUE, LOG_PATTERN

### 8.1 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P4-01 | `app/triggers/webhooks/verifier.py` | `WebhookSignatureVerifier.verify()` — HMAC-SHA256 + Ed25519 support |
| P4-02 | `app/triggers/rotation.py` | `WebhookSecretRotation` — dual-secret grace period, `POST .../rotate-secret` |
| P4-03 | `app/triggers/webhooks/github.py` | Parse GitHub events → `GITHUB_WEBHOOK` + `github_event_filter` |
| P4-04 | `app/triggers/webhooks/jira.py` | Parse Jira webhook → `JIRA_WEBHOOK` + `jira_project_filter` |
| P4-05 | `app/triggers/webhooks/stripe.py` | Parse Stripe events → `STRIPE_WEBHOOK` + `stripe_event_filter` |
| P4-06 | `app/triggers/webhooks/salesforce.py` | Parse Salesforce outbound messages → `SALESFORCE_EVENT` |
| P4-07 | `app/triggers/webhooks/confluence.py` | Parse Confluence webhooks → `CONFLUENCE_WEBHOOK` |
| P4-08 | `app/triggers/webhooks/linear.py` | Parse Linear webhooks → `LINEAR_WEBHOOK` |
| P4-09 | `app/triggers/data/db_row_change.py` | `pg_notify` listener → `DB_ROW_CHANGE` — table + operation + filter |
| P4-10 | `app/triggers/data/s3_event.py` | AWS S3 event notification consumer → `S3_EVENT` |
| P4-11 | `app/triggers/data/api_poll.py` | Generic poller — configurable interval, JSONPath extraction → `API_POLL` |
| P4-12 | `app/triggers/data/rss.py` | RSS/Atom feed poller → `RSS_FEED` |
| P4-13 | `app/triggers/data/google_sheets.py` | Google Sheets change webhook → `GOOGLE_SHEETS` |
| P4-14 | `app/triggers/data/sharepoint.py` | SharePoint delta webhook → `SHAREPOINT` |
| P4-15 | `app/triggers/monitoring/grafana.py` | Grafana Alertmanager-format webhook → `GRAFANA_ALERT` |
| P4-16 | `app/triggers/monitoring/cloudwatch.py` | CloudWatch SNS notification → `CLOUDWATCH` |
| P4-17 | `app/triggers/monitoring/sentry.py` | Sentry webhook → `SENTRY_ISSUE` |
| P4-18 | `app/triggers/monitoring/log_pattern.py` | Regex pattern match on log stream → `LOG_PATTERN` |
| P4-19 | `app/api/triggers.py` | POST `/api/v1/webhooks/{type}/{token}` — unified typed webhook endpoint |
| P4-20 | `tests/triggers/webhooks/test_verifier.py` | `test_hmac_valid`, `test_hmac_invalid_rejected`, `test_dual_secret_rotation` |

### 8.2 `WebhookSignatureVerifier` Implementation

```python
class WebhookSignatureVerifier:
    async def verify(
        self,
        payload_bytes: bytes,
        signature_header: str,
        secret_ref: str,           # vault://key-name
        algorithm: str = "sha256",
    ) -> bool:
        secret = await self._vault.get_secret(secret_ref)
        expected = hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        received = signature_header.split("=")[-1]
        return hmac.compare_digest(expected, received)
```

---

## 9. Phase 5 — Advanced Triggers + IoT (Sprint 9–10)

> **Duration:** 2 weeks  
> **Trigger families:** H (API/Polling), I (IoT/Edge), remaining Family A, C, E  
> **Types:** MQTT, GEOFENCE, SENSOR_THRESHOLD, GRAPHQL_SUBSCRIPTION, WEBSOCKET_MESSAGE, PRICE_THRESHOLD, BUSINESS_CALENDAR, DEADLINE, RELATIVE_DELAY, MEETING_ENDED, VOICE_TRANSCRIPT, SMS_INBOUND, TEAMS_WEBHOOK, DISCORD_EVENT

### 9.1 Task List

| Task | File(s) | Acceptance Criteria |
|---|---|---|
| P5-01 | `app/triggers/iot/mqtt.py` | `MQTTConsumer(MQTTClient)` — paho-mqtt subscriber, `mqtt_topic` pattern matching |
| P5-02 | `app/triggers/iot/geofence.py` | `GeofenceEvaluator` — GeoJSON polygon, lat/lon events, Shapely check |
| P5-03 | `app/triggers/iot/sensor.py` | `SensorThresholdEvaluator` — INCR/compare against threshold, units conversion |
| P5-04 | `app/triggers/advanced/graphql_sub.py` | `GraphQLSubscriptionConsumer` — websocket connection to GraphQL endpoint |
| P5-05 | `app/triggers/advanced/websocket.py` | `WebSocketMessageConsumer` — persistent WS connection, pattern matching |
| P5-06 | `app/triggers/advanced/price.py` | `PriceThresholdPoller` — crypto/stock price polling, direction (above/below) |
| P5-07 | `app/triggers/models.py` | `BUSINESS_CALENDAR` — `BusinessCalendar` model, skip weekends/holidays |
| P5-08 | `app/triggers/models.py` | `DEADLINE` — date math from `deadline_field`, alert N days/hours before |
| P5-09 | `app/triggers/models.py` | `RELATIVE_DELAY` — `relative_to_field` + offset, one-shot ETA task |
| P5-10 | `app/triggers/nl_scheduler.py` | Extend `NLScheduler` for all 58 types — NL → `TriggerSpec` for each family |
| P5-11 | `app/triggers/simulation.py` | `SimulationEngine` — `simulation_mode=True` path; `SimulatedTriggerResult` |
| P5-12 | `app/triggers/simulation.py` | `test_payload_factory(trigger_type)` — realistic test payloads per type |
| P5-13 | `app/triggers/simulation.py` | `TriggerChaosHarness` — inject signature failures, condition timeouts, goal-service down |
| P5-14 | `app/api/triggers.py` | POST `/api/v1/triggers/{id}/simulate` — runs simulation, returns `SimulatedTriggerResult` |
| P5-15 | `app/api/triggers.py` | POST `/api/v1/triggers/{id}/rotate-secret` — `WebhookSecretRotation` |
| P5-16 | `tests/triggers/iot/test_mqtt.py` | `test_mqtt_message_fires_trigger`, `test_topic_pattern_filter` |
| P5-17 | `tests/triggers/test_simulation.py` | `test_simulation_mode_no_goals_created`, `test_chaos_harness_dlq_writes` |

### 9.2 Business Calendar Model

```python
@dataclass
class BusinessCalendar:
    calendar_id: str
    tenant_id: str
    timezone: str
    working_days: list[str]       # ["mon","tue","wed","thu","fri"]
    working_hours_start: str      # "09:00"
    working_hours_end: str        # "17:00"
    holidays: list[str]           # ["2026-12-25", "2027-01-01"]

    def is_business_time(self, dt: datetime) -> bool:
        local_dt = dt.astimezone(ZoneInfo(self.timezone))
        if local_dt.strftime("%a").lower() not in self.working_days:
            return False
        if local_dt.strftime("%Y-%m-%d") in self.holidays:
            return False
        start = time.fromisoformat(self.working_hours_start)
        end   = time.fromisoformat(self.working_hours_end)
        return start <= local_dt.time() <= end
```

---

## 10. Cross-Cutting: Security Hardening (All Phases)

These tasks run **in parallel** with phases 1–5, one security review per sprint.

| Task | Sprint | What |
|---|---|---|
| SEC-01 | Sprint 1 | Penetration test: webhook HMAC bypass attempts |
| SEC-02 | Sprint 2 | RBAC matrix unit tests — 5 roles × 8 operations = 40 test cases |
| SEC-03 | Sprint 3 | Jinja2 sandbox escape attempts — fuzzing with `jinja2-sandbox-escape-pocs` |
| SEC-04 | Sprint 4 | CEL sandbox escape attempts — timing attacks, resource exhaustion |
| SEC-05 | Sprint 5 | Secret rotation grace period tests — verify old secret rejected after grace |
| SEC-06 | Sprint 6 | Audit log completeness — every state change produces audit event |
| SEC-07 | Sprint 7 | API-key allowlist enforcement — trigger fires rejected without valid key |
| SEC-08 | Sprint 8 | Injection prevention — goal template `{{payload.field}}` with malicious payloads |
| SEC-09 | Sprint 9 | Channel tenant mapping isolation — Slack workspace A cannot trigger tenant B |
| SEC-10 | Sprint 10 | Full OWASP scan — ZAP scan against trigger API endpoints |

---

## 11. Cross-Cutting: NLScheduler Extension

The `NLScheduler` must cover all 58 types. This is a Phase 5 deliverable but planned from the start.

### 11.1 NLScheduler Coverage Map

| Family | NL Example | `TriggerType` |
|---|---|---|
| A | "Every weekday at 9am London time" | `CRON` |
| A | "3 days before the contract expires" | `RELATIVE_DELAY` |
| A | "Only during business hours" | `BUSINESS_CALENDAR` |
| B | "When the invoice agent finishes" | `GOAL_COMPLETED` |
| B | "If the eval score drops below 0.8" | `GOAL_SCORE_BELOW` |
| B | "When a human approves the action" | `HITL_APPROVED` |
| C | "When someone types /run in Slack" | `CHAT_COMMAND` |
| C | "When someone emails with subject 'urgent'" | `EMAIL_INTENT` |
| D | "When CPU usage exceeds 90% for 5 minutes" | `WINDOW_AGGREGATE` |
| D | "After 100 failed logins" | `COUNTER_THRESHOLD` |
| E | "When a GitHub PR is merged to main" | `GITHUB_WEBHOOK` |
| E | "When Stripe receives a payment" | `STRIPE_WEBHOOK` |
| F | "When a file drops into the S3 bucket" | `S3_EVENT` |
| F | "When the database row changes" | `DB_ROW_CHANGE` |
| G | "When PagerDuty fires a P1 alert" | `PAGERDUTY` |
| G | "When Sentry creates a new issue" | `SENTRY_ISSUE` |
| H | "Poll the API every 5 minutes" | `API_POLL` |
| H | "When Bitcoin price drops below $60k" | `PRICE_THRESHOLD` |
| I | "When the temperature sensor exceeds 80°C" | `SENSOR_THRESHOLD` |
| I | "When the device enters the geofence" | `GEOFENCE` |

---

## 12. API Design (Complete)

### 12.1 Trigger CRUD

```
POST   /api/v1/triggers                     Create trigger (with quota check)
GET    /api/v1/triggers                     List triggers (paginated, filterable)
GET    /api/v1/triggers/{id}                Get trigger by ID
PATCH  /api/v1/triggers/{id}                Update trigger (with If-Match ETag)
DELETE /api/v1/triggers/{id}                Delete trigger
POST   /api/v1/triggers/{id}/enable         Enable trigger
POST   /api/v1/triggers/{id}/disable        Disable / pause trigger
POST   /api/v1/triggers/{id}/fire           Manual fire (REST type)
POST   /api/v1/triggers/{id}/simulate       Simulation mode fire → SimulatedTriggerResult
POST   /api/v1/triggers/{id}/rotate-secret  Initiate webhook secret rotation
GET    /api/v1/triggers/{id}/rotation-status Check rotation progress
GET    /api/v1/triggers/{id}/history        Firing history (trigger_events)
GET    /api/v1/triggers/{id}/version-history Version snapshots
```

### 12.2 DLQ API

```
GET    /api/v1/triggers/dlq                 List DLQ entries (tenant-scoped)
POST   /api/v1/triggers/dlq/{id}/retry      Retry failed firing
POST   /api/v1/triggers/dlq/{id}/dismiss    Mark as resolved
```

### 12.3 Channel Ingestion Gateway

```
POST   /api/v1/channels/slack/events        Slack Events API
POST   /api/v1/channels/teams/events        Teams webhook
POST   /api/v1/channels/discord/events      Discord Interactions
POST   /api/v1/channels/email/inbound       SendGrid Inbound Parse
POST   /api/v1/channels/sms/inbound         Twilio SMS
POST   /api/v1/channels/forms/{form_id}     Generic form submission
POST   /api/v1/channels/voice/transcript    Voice transcript ingest
```

### 12.4 Typed Webhooks

```
POST   /api/v1/webhooks/github/{token}      GitHub webhook
POST   /api/v1/webhooks/jira/{token}        Jira webhook
POST   /api/v1/webhooks/stripe/{token}      Stripe webhook
POST   /api/v1/webhooks/salesforce/{token}  Salesforce outbound
POST   /api/v1/webhooks/confluence/{token}  Confluence webhook
POST   /api/v1/webhooks/linear/{token}      Linear webhook
POST   /api/v1/webhooks/alertmanager/{token} Alertmanager
POST   /api/v1/webhooks/datadog/{token}     Datadog webhook
POST   /api/v1/webhooks/pagerduty/{token}   PagerDuty webhook
POST   /api/v1/webhooks/grafana/{token}     Grafana alert
POST   /api/v1/webhooks/cloudwatch/{token}  CloudWatch notification
POST   /api/v1/webhooks/sentry/{token}      Sentry issue
POST   /api/v1/webhooks/{type}/{token}      Generic webhook (fallback)
```

### 12.5 State Machine API

```
POST   /api/v1/state-machines               Create state machine definition
GET    /api/v1/state-machines               List definitions
GET    /api/v1/state-machines/{id}          Get definition
DELETE /api/v1/state-machines/{id}          Delete definition
POST   /api/v1/state-machines/{id}/instances Create instance for entity
POST   /api/v1/state-machines/{id}/instances/{entity_id}/transition  Trigger transition
GET    /api/v1/state-machines/{id}/instances/{entity_id}  Get current state + history
```

---

## 13. Test Strategy (Per Phase)

### 13.1 Test Pyramid

| Layer | Count Target | Tools |
|---|---|---|
| Unit tests (per trigger type) | 2 per type = 116 | pytest, FakeProvider |
| Integration tests (dispatcher pipeline) | 30 | testcontainers (postgres + redis) |
| Contract tests (webhook parsers) | 1 per webhook type = 12 | recorded fixtures |
| Chaos tests (TriggerChaosHarness) | 10 | custom harness |
| Simulation tests | 1 per type = 58 | simulation_mode=True |
| Security tests (RBAC, HMAC, injection) | 40 | pytest + zap |
| **Total** | **~270** | |

### 13.2 Mandatory Test Cases (Phase 0 Baseline)

```python
# test_dispatcher.py
def test_dispatch_happy_path()
def test_dedup_blocks_duplicate_within_window()
def test_rate_limit_blocks_when_exceeded()
def test_circuit_breaker_opens_after_5_failures()
def test_circuit_breaker_half_open_allows_probe()
def test_bulkhead_rejects_when_full()
def test_condition_false_skips_goal_creation()
def test_template_render_interpolates_payload()
def test_simulation_mode_returns_result_no_goal()
def test_rbac_rejects_viewer_role_fire()
def test_otel_spans_created_for_all_steps()
def test_prometheus_counter_incremented_on_fire()
def test_dlq_written_on_goal_enqueue_failure()
def test_audit_event_written_on_trigger_create()
```

### 13.3 Integration Test Template

```python
@pytest.mark.integration
async def test_chain_trigger_fires_on_goal_completion(
    trigger_store, goal_service, redis_client, db_session
):
    # Arrange
    tenant = make_tenant()
    trigger = await trigger_store.create_async(TriggerSpec(
        trigger_type=TriggerType.GOAL_COMPLETED,
        watch_agent_id="invoice-agent",
        goal_template="Process payment for {{payload.invoice_id}}"
    ), tenant_ctx=tenant)

    # Act — simulate goal completion
    await redis_client.publish(
        "goal.completed",
        json.dumps({"goal_id": "g-123", "agent_id": "invoice-agent",
                    "tenant_id": tenant.tenant_id, "score": 0.95,
                    "output": {"invoice_id": "INV-001"}})
    )
    await asyncio.sleep(0.1)  # let consumer process

    # Assert
    events = await trigger_store.list_events(trigger.id, tenant_ctx=tenant)
    assert len(events) == 1
    assert events[0].goal_created is True
    goals = await goal_service.list(tenant_ctx=tenant)
    assert any("INV-001" in g.goal_text for g in goals)
```

---

## 14. Observability Runbook

### 14.1 Grafana Dashboard Panels

1. **Trigger Firing Rate** — heatmap by type, last 24h
2. **Error Rate by Type** — % firings ending in DLQ
3. **Dedup Rate** — high dedup = flaky source, investigate
4. **Circuit Breaker State Map** — gauge per trigger
5. **DLQ Depth Trend** — gauge + trend line per tenant

### 14.2 Alert Rules (Prometheus)

```yaml
groups:
  - name: triggers
    rules:
      - alert: TriggerDLQDepthHigh
        expr: agentverse_trigger_dlq_depth > 100
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "DLQ depth {{ $value }} for trigger {{ $labels.trigger_type }}"

      - alert: TriggerCircuitBreakerCascade
        expr: count(agentverse_trigger_circuit_state == 2) by (tenant_id) > 10
        for: 2m
        labels: { severity: critical }
        annotations:
          runbook: "https://runbooks.agentverse.ai/trigger-circuit-cascade"

      - alert: TriggerDispatchLatencyHigh
        expr: histogram_quantile(0.99, agentverse_trigger_fire_latency_seconds_bucket) > 2
        for: 5m
        labels: { severity: warning }

      - alert: TriggerFireRateDrop
        expr: rate(agentverse_trigger_fired_total[5m]) < 0.1
          and rate(agentverse_trigger_fired_total[1h]) > 1
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Trigger firing rate dropped unexpectedly"
```

---

## 15. Rollout Plan

### 15.1 Feature Flag Strategy

All phases are behind feature flags to enable safe incremental rollout:

```python
# In app/core/config.py
class Settings:
    trigger_phase_1_enabled: bool = False   # Goal chaining
    trigger_phase_2_enabled: bool = False   # Conversational
    trigger_phase_3_enabled: bool = False   # Condition/State
    trigger_phase_4_enabled: bool = False   # Typed webhooks + data
    trigger_phase_5_enabled: bool = False   # IoT + advanced
```

### 15.2 Plan-Tier Quota Enforcement

Quotas are checked at CREATE time and enforced at runtime dispatch:

| Quota | Free | Starter | Professional | Enterprise |
|---|---|---|---|---|
| Max triggers per tenant | 5 | 25 | 200 | Unlimited |
| Max firings per trigger per hour | 10 | 60 | 600 | Custom |
| Max concurrent in-flight goals | 2 | 10 | 100 | Custom |
| DLQ retention (days) | 3 | 14 | 30 | 90 |
| Max payload size (KB) | 64 | 256 | 1024 | 4096 |
| Cron minimum interval | 1h | 15min | 1min | 1sec |
| Goal chain depth | 3 | 5 | 10 | 50 |
| Simulation mode | No | Yes | Yes | Yes |

**Existing trigger types kept** (CRON, INTERVAL, ONCE, WEBHOOK, REST, EVENT, FILE_DROP, ALERTMANAGER, DATADOG, PAGERDUTY) — these 10 types are already production; Phase 0 migrates their schema columns and plugs them into the new `TriggerDispatcher` pipeline. No behavioral changes until `trigger_phase_1_enabled=True`.

### 15.2 Migration Safety

Each Alembic migration is:
1. **Additive only** — no column renames, no type changes in Phase 0–2
2. **Non-locking** — no `NOT NULL` without `DEFAULT`, no index on large table without `CONCURRENTLY`
3. **Tested** — run against a copy of production data in staging before deploy

### 15.3 Backward Compatibility

- `ScheduleStore` kept as alias for `TriggerStore` — existing code continues to work
- Old 10-type `TriggerType` values preserved with same string values
- `app/api/schedules.py` kept alive; routes to `app/api/triggers.py` internally
- `TriggerSpec` old fields remain with same defaults

### 15.4 Rollout Sequence

```
Sprint 0 (Phase 0):   Deploy migrations 0045–0048; dark-launch dispatcher with no traffic
Sprint 1–2 (Phase 1): Enable trigger_phase_1_enabled=True in staging; 5% canary production
Sprint 3–4 (Phase 2): Enable phase 2 for early-access tenants; Slack/Teams beta
Sprint 5–6 (Phase 3): Enable phase 3; CEL evaluator stress test under load
Sprint 7–8 (Phase 4): Enable phase 4; typed webhook endpoints; existing webhook endpoints unchanged
Sprint 9–10 (Phase 5): Enable phase 5; MQTT/IoT opt-in per tenant; full GA
```

---

## 16. Completion Audit Checklist

Run this checklist before declaring any phase complete:

### Phase 0
- [ ] All 4 migrations pass `alembic upgrade head` and `alembic downgrade -1`
- [ ] `TriggerDispatcher._dispatch()` pipeline has 12 ordered steps
- [ ] `TriggerType` enum has exactly 58 entries
- [ ] `TriggerSpec` dataclass has ≥60 fields
- [ ] All 7 Prometheus metrics registered
- [ ] OTel spans emitted (verified in Jaeger dev)
- [ ] `test_dispatcher.py` — 14 mandatory tests pass
- [ ] RLS policies verified on all 4 new tables
- [ ] No hardcoded secrets — `vault://` refs only

### Phase 1
- [ ] `ChainTriggerConsumer` subscribes to `goal.completed`, `goal.failed`, `goal.score_below`, `hitl.approved`, `hitl.rejected`, `memory.created`
- [ ] Goal chain depth limit enforced at 10
- [ ] Deduplication blocks replay within 60s
- [ ] Integration test: GOAL_COMPLETED → goal created end-to-end

### Phase 2
- [ ] `ChannelIngestionGateway` routes all 6 channel types
- [ ] Slack signature verification (X-Slack-Signature) tested
- [ ] `channel_tenant_mappings` migration deployed
- [ ] `NLIntentClassifier` accuracy ≥ 85% on test set
- [ ] Integration test: Slack event → `CHAT_COMMAND` trigger → goal created

### Phase 3
- [ ] CEL evaluator sandboxed (no `__import__`, `eval`, `exec`)
- [ ] CEL timeout enforced (500ms)
- [ ] `COUNTER_THRESHOLD` uses Redis sliding window
- [ ] State machine API CRUD works with RLS
- [ ] Integration test: state transition → `STATE_TRANSITION` trigger → goal created

### Phase 4
- [ ] HMAC verification covers GitHub, Jira, Stripe, Slack, Salesforce, Confluence, Linear formats
- [ ] Secret rotation grace period: old secret accepted during grace, rejected after
- [ ] `pg_notify` listener reliably receives `DB_ROW_CHANGE` events
- [ ] All 11 typed webhook endpoints return 401 on bad signature
- [ ] Integration test: GitHub push → `GITHUB_WEBHOOK` → goal created

### Phase 5
- [ ] MQTT consumer connects + reconnects on broker failure
- [ ] Geofence evaluation correct for polygon edge cases
- [ ] `NLScheduler` parses all 58 trigger types from NL
- [ ] Simulation mode creates no goals (verified by count before/after)
- [ ] `TriggerChaosHarness` produces DLQ entries and circuit breaker opens
- [ ] Full test suite: ≥270 tests passing, 0 failures

### Global Invariants (any phase)
- [ ] No trigger type fires without writing a `trigger_events` row
- [ ] No trigger type fires without `UNIQUE (tenant_id, idempotency_key)` check
- [ ] No trigger modifies another tenant's data (RLS verified by cross-tenant test)
- [ ] `trigger_audit_events` written for every CREATE/UPDATE/DELETE/ENABLE/DISABLE
- [ ] `agentverse_trigger_fired_total` incremented on every dispatch (success or skip)

---

## 17. Dependency Graph

```
Phase 0 (Foundation)
  ↓
Phase 1 (Goal Chaining) — depends on: Phase 0 + GoalService Redis pub/sub
  ↓
Phase 2 (Conversational) — depends on: Phase 0 + channel_tenant_mappings migration + NLIntentClassifier
  ↓
Phase 3 (Condition/State) — depends on: Phase 0 + Redis for counters + pg for state machines
  ↓
Phase 4 (Typed Webhooks + Data) — depends on: Phase 0 + WebhookSignatureVerifier + pg_notify
  ↓
Phase 5 (IoT + Advanced) — depends on: Phase 0 + Phase 1–4 (NL coverage of all types)
```

---

## 18. Open Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `pg_notify` misses events under high write load | Medium | Medium | Use WAL-based CDC (Debezium) as fallback |
| CEL Python library performance under load | Low | Medium | Benchmark at 1k conditions/s; cache parsed ASTs |
| Redis memory pressure from counter windows | Medium | Low | Set `EXPIRE` on all counter keys; monitor Redis memory |
| Slack/Teams rate limits on inbound events | Low | Low | Back-pressure; queue overflow policy |
| MQTT broker availability for IoT | Medium | High | Use managed AWS IoT Core; abstract behind `MQTTClient` interface |
| NLScheduler accuracy below 85% | Low | Medium | Fallback to structured form-based trigger creation |
| Webhook secret exposure in logs | Low | Critical | Secrets stripped by `_strip_secret_redis_fields()` from Phase 0 |

---

*Plan created: 2026-08-16 | Spec: 2026-08-15-trigger-architecture-specification.md | Target: 58 trigger types, 47 supporting features, ~270 tests*
