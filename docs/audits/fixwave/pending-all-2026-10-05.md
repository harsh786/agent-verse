# AgentVerse — every pending item (2026-10-05)

Read-only re-verification against local `main` at `3b5d35c13` (not pushed). Source: the 441 items still open/partial in `reverify-2026-10-05.json`, each re-checked in the code. Fixed since: 85; obsolete: 1.

**Pending: 355** (278 open, 77 partial) — high 0, medium 102, low 253. Plus the MongoDB list (section at the end): 49 items, none fixed on main.

## Summary by area

| Area | Pending | Medium | Low |
|---|---:|---:|---:|
| Agent core | 4 | 0 | 4 |
| Providers & model routing | 3 | 1 | 2 |
| Services & reliability | 41 | 11 | 30 |
| Workflows, triggers & channels | 20 | 5 | 15 |
| Knowledge: ingestion, chunking, embedding, retrieval | 13 | 1 | 12 |
| Memory, evals & self-improvement | 9 | 2 | 7 |
| Tools & connectors | 17 | 3 | 14 |
| Tenancy & security | 31 | 16 | 15 |
| Governance | 15 | 1 | 14 |
| Org & collaboration | 24 | 6 | 18 |
| Frontend & GitHub Action | 19 | 1 | 18 |
| Enterprise & ops | 64 | 22 | 42 |
| Critic group (cross-cutting) | 95 | 33 | 62 |
| **Total** | **355** | **102** | **253** |

## Agent core — 4 pending

### Dynamic orchestration: classification, pattern selection, runtime profile and GraphFactory (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 1 | low | partial | Errors are logged at warning and the summary now gets agent_config=strategy_runtime, but pattern_selection is still computed and stored from the goal text for every goal, whatever the runtime actually executes. | `app/services/goal_service.py:4626-4644` |

### Goal-tree decomposition (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 2 | low | partial | Unchanged: the fan-out ledger lets a resumed parent skip finished children, but each child still runs under a synthetic id (parent-subid-uuid8) with no goals row or own event stream, and an interrupted child restarts from scratch. | `app/agent/goal_tree.py:117-131` |

### Multi-agent: supervisor and debate (API workflow modes and in-graph nodes) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 3 | low | partial | Unchanged since the recert (no commits to supervisor.py): sub-goals use the goals.subgoals pool and a ledger, but the parent still holds its Celery slot while it streams each sub-goal's events under asyncio.timeout (up to 300 s per sub-task). | `app/agent/supervisor.py:255-276` |

### Pattern adapter library and dead or legacy modules (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 4 | low | open | rewoo, llm_compiler, codeact and lats are still absent from AGENT_GRAPH_STRATEGY_FLAGS and STRATEGY_RUNNER_STRATEGIES, so goal_execution_driver returns None for them. The catalogue reports them as not runnable, which is honest. | `app/orchestration/execution_drivers.py:36-55,89-100` |

## Providers & model routing — 3 pending

### Provider circuit breakers, health and model failover (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 5 | medium | open | Unchanged: ProviderCircuitBreaker keeps state in in-process dicts, and _provider_cb is a module-level singleton, so each replica and worker learns about provider failures on its own. | `app/providers/circuit_breaker.py:19-92` |

### LLM generation tracing (TracedProvider / GenAI spans) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 6 | low | partial | Unchanged: graph roles and complete_decision are traced, but no embedding or ingestion path wraps its embedder in TracedProvider (the only TracedProvider uses are profiled_graph.py and guarded_completion.py), so embeddings still emit no gen_ai span. | `app/orchestration/profiled_graph.py:30-45; app/providers/guarded_completion.py:276-290` |

### Per-role model routing (planner/executor/verifier) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 7 | low | partial | Unchanged: configured registry models are preferred, but _TIER_MODELS is still all OpenAI slugs (plus voyage-3-lite) and is still the last-resort fallback. | `app/ai_router/model_orchestrator.py:21-49,286,471,575` |

## Services & reliability — 41 pending

### Bulkhead (per-tenant concurrency) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 8 | medium | open | configure_tenant still has no callers outside bulkhead.py. Both registries use default_max_concurrent=20, so every tenant gets 20 regardless of plan. | `app/reliability/bulkhead.py:187; app/main.py:2283; app/scaling/tasks.py:2239-2242` |
| 9 | medium | open | The acquire Lua still EXPIREs the counter key with _SLOT_TTL=300 on every acquire, and release errors are still suppressed. Leaked slots persist under traffic, and a step longer than 300 s can reset the counter. RedisLeaseLimiter (bulkhead.py:209) is still not used for the tenant bulkhead. | `app/reliability/bulkhead.py:84,98,143-144` |
| 10 | low | open | RedisBulkhead.available_slots_sync still returns self._max, and the registry's available_slots still reads the local registry. | `app/reliability/bulkhead.py:146-148,205-206` |
| 11 | low | open | _worker_bulkhead_registry still builds a new RedisBulkheadRegistry and a new redis.asyncio client for every run_goal and never closes them, so its fallback semaphore is per goal. | `app/scaling/tasks.py:2232-2245,3513` |

### Circuit breakers (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 12 | medium | open | The executor still picks get('llm') or get(tool_name), so connector breakers are skipped whenever an 'llm' breaker exists. It calls the sync can_call/record_*, which RedisCircuitBreaker routes to its per-instance in-memory fallback. | `app/agent/nodes/executor_mixin.py:1669-1672,2290,2303; app/reliability/redis_circuit_breaker.py:195-202` |
| 13 | medium | open | OPEN->HALF_OPEN is still a plain SET with no ex or keepttl, and the HALF_OPEN branch still returns False unconditionally. A probe that never reports still leaves the breaker HALF_OPEN fleet-wide. PROV-28 fixed only the separate in-process ProviderCircuitBreaker. | `app/reliability/redis_circuit_breaker.py:121-134` |
| 14 | low | open | The worker graph services still have no circuit_breakers entry (grep finds none in tasks.py), unlike the API path. Celery-run goals get no executor-level breaker. | `app/scaling/tasks.py:3490-3520; app/services/goal_service.py:1830-1918` |
| 15 | low | open | _provider_cb is still a module-level, per-process ProviderCircuitBreaker. | `app/providers/circuit_breaker.py:91-92` |

### Cross-process goal lifecycle signals and execution locks (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 16 | medium | open | run_goal still wraps all of _run_with_signals, including time paused at the step gate, in wait_for(timeout=goal_timeout_s). A goal paused longer than its plan timeout is still failed as 'Goal timed out'. | `app/scaling/tasks.py:3972-3983,3998` |
| 17 | low | open | _SyncGoalLock still has only acquire and release. Its TTL is plan timeout plus 300 s, and correctness still relies on wait_for(goal_timeout_s) firing first; nothing renews the lock. | `app/scaling/tasks.py:194-233,2781-2786,3972-3983` |
| 18 | low | open | check_pause_cancel and GoalExecutionLock still have no callers in app/; the worker uses its own gates (tasks.py:871-890, 1042-1085). | `app/reliability/goal_lifecycle.py:179; app/reliability/distributed_lock.py:10` |
| 19 | low | open | With a non-Redis broker and REDIS_URL unset (or no broker URL at all), _redis_url is still '' and run_goal skips the lock with no warning. Only the comment says this is the eager/test path; the code does not check it. | `app/scaling/tasks.py:2759-2766` |
| 20 | low | partial | Unchanged: visibility_timeout is still one global value (longest plan timeout plus 1 h) for every task. The GOAL-STALL reaper requeues only goals that had no side effects, and fails the rest as runner_lost. | `app/scaling/celery_app.py:478-488` |

### Notification service (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 21 | medium | open | _loaded_tenants still hydrates each tenant once per process and is cleared only by set_db. Channels added on, or deleted by, another replica are never seen here until restart. | `app/services/notification_service.py:39,54-59,170-171` |
| 22 | medium | open | _persist_channel still catches every exception and logs notification_persist_failed. add_channel_async returns normally, so POST /governance/notifications answers 'created' for a channel that exists only in this pod's memory, under a comment that says the row exists for every replica. | `app/services/notification_service.py:135-167; app/api/governance.py:1220-1223` |
| 23 | low | open | CreateNotificationChannelRequest still accepts any channel_type string and any config dict, with no validation at create time. | `app/api/governance.py:51-53,1205-1223` |
| 24 | low | open | notify_goal_complete still has no callers in app/. | `app/services/notification_service.py:331` |

### Rollback engine and tool inverses (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 25 | medium | open | rollback_all_async is still called only on the verifier's permanent-failure branch (the only caller in app/). Timeout, operator cancel and emergency stop never roll back registered side effects. | `app/agent/nodes/verifier_mixin.py:877` |
| 26 | low | open | RollbackReport.record still counts any non-InverseResult return, including None, as rolled_back. The caller-less sync rollback_all still fire-and-forgets coroutine inverses. | `app/reliability/rollback.py:30-42,145-179` |
| 27 | low | open | The rollback stack is still an in-memory RollbackEngine() created per graph build, so it is lost on a crash or requeue. The GOAL-STALL reaper fails, rather than re-runs, goals that already ran a tool. | `app/services/goal_service.py:1909; app/scaling/tasks.py:3513` |

### Tenant LLM config store (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 28 | medium | open | _resolve_retrieval_llm still calls config_store.get_config() without strict=True. A DB read error returns None, which falls to the stale local dict or the platform _app_provider, so a BYOK tenant's RAG LLM traffic and cost silently go to the platform vendor. | `app/main.py:946-951,992-1000; app/services/llm_config_store.py:93-98` |
| 29 | low | open | _save_llm_config still writes app.state._llm_configs on every save, and that dict is still read whenever the durable store returns None (GET /me/llm, retrieval LLM, goal provider resolution). | `app/api/tenants.py:418-428,332; app/main.py:951; app/services/goal_service.py:1618-1624` |
| 30 | low | open | GET /tenants/me/llm still uses the non-strict _read_llm_config, so a DB error is reported as configured:false. | `app/api/tenants.py:325-333,431-438` |
| 31 | low | open | _cache_set still only logs a failed Redis overwrite, so other replicas keep serving the previous cached config, including the old key, for up to the 300 s TTL. | `app/services/llm_config_store.py:127-135` |
| 32 | low | open | delete_config still has no caller, and tenants.py exposes only GET/PUT /me/llm and /me/llm-config, so a tenant cannot remove a stored BYOK key. | `app/services/llm_config_store.py:104-115; app/api/tenants.py:431,441,495,503` |

### Tenant service (signup, API keys, auth resolution) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 33 | medium | partial | Unchanged: the keys load in one batched query when RLS bypass is allowed, otherwise one RLS query per tenant. sync_from_db still copies every active tenant and API key into each replica's memory, with no bound. | `app/services/tenant_service.py:1033-1080` |
| 34 | low | open | _get_tenant_from_db still logs and returns None on any error. Its only caller, get_tenant_cached, still has no callers in app/; no commits touched tenant_service.py since the recert. | `app/services/tenant_service.py:1171-1173,1175` |
| 35 | low | open | Redis cache read errors still hit 'except Exception: pass' and fall through to the DB-authoritative lookup. Benign. | `app/services/tenant_service.py:403-404` |
| 36 | low | open | _db_resolve_by_hash still returns None on any DB error, so a Postgres outage is a 401 for every uncached API key rather than a retryable 503. | `app/services/tenant_service.py:751-753` |
| 37 | low | open | Revoke still deletes api_key:{hash} after the DB write. A resolve that read the row before the commit then calls _cache_resolved_key (plain setex, 300 s, with no revocation tombstone or version check), so the revoked key can keep working for up to 5 minutes. | `app/services/tenant_service.py:356-358,412-421,462-481` |

### Usage metering, outbound webhooks, legacy persistence adapters (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 38 | medium | open | get_usage_summary still catches the DB rollup failure, logs usage_summary_db_failed and returns only the in-memory buffer, so /billing/usage reports zero usage instead of an error. | `app/services/usage_service.py:204-210` |
| 39 | low | open | Both modules still exist and nothing in app/ imports OutboundWebhookService or app.services.persistence. Dead code. | `app/services/webhook_service.py; app/services/persistence.py` |
| 40 | low | open | The unused module singleton _usage_service = UsageService() is still there; no commits touched usage_service.py. | `app/services/usage_service.py:309` |
| 41 | low | open | record_tool_call still passes no record_id, so every call gets a random uuid4 id and a redelivered run re-meters its tool calls. The worker still builds a throwaway UsageService per use. | `app/services/usage_service.py:69,124-142; app/scaling/tasks.py:744` |

### Durable goal event store (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 42 | low | open | list_events and list_events_since still filter only on tenant_id, goal_id and sequence, with no created_at bound, so each replay page probes every monthly partition. | `app/services/event_store.py:244-251,288-296` |

### Goal dedup and idempotency (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 43 | low | open | RedisDeduplicationCache still keys on the per-process hash(goal), and its only references are the docstring warning at goal_service.py:521-525. It is unused dead code. | `app/reliability/dedup.py:76,84,90` |

### Goal lifecycle service (GoalService) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 44 | low | partial | is_valid_transition still has no callers in app/. Only the only_if_active terminal guard is enforced (e.g. goal_service.py:5539, 5621), so non-terminal transitions are not checked. | `app/services/goal_lifecycle.py:41` |
| 45 | low | open | _recover_interrupted_goals still iterates only self._goals, which sync_from_db warms with goals from the last 24 h, capped at 500 per tenant. Older in-process orphans are left to the stuck-goal sweeper. | `app/services/goal_service.py:1274,6588-6590` |
| 46 | low | partial | Unchanged ordering: cancel_goal, pause_goal and resume each signal the runner before their conditional DB write. Only cancel withdraws its flag (best effort) on a failed write; pause and resume flags are not undone, so a runner that polls in that window acts on them. | `app/services/goal_service.py:5483,5525,5815-5821,5934` |

### SSE goal event delivery and cross-replica fanout (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 47 | low | partial | Unchanged: no stubs are created, but every API replica still psubscribes goal_events:* for all tenants and JSON-decodes every worker event in the fleet before dropping the ones for goals it does not hold. | `app/services/goal_service.py:987-1006` |
| 48 | low | partial | The bridge now carries _seq, but for worker-run goals it still feeds local subscriber queues the {type,payload,goal_id,tenant_id} wrapper. The local stream yields queue items unnormalized (no _normalize_bus_event), so the event shape still differs from the cross-replica path. | `app/services/goal_service.py:1009-1024,6024-6060` |

## Workflows, triggers & channels — 20 pending

### Durable execution / LangGraph checkpointing (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 49 | medium | partial | Redelivered goals still resume from the latest GoalCheckpoint (graph.py:827-876) and fail closed when checkpoints are unreadable. The tool call in flight at the crash still re-runs without an idempotency key: idempotency_scope (mcp/client.py:43) is entered only by workflow tool_step.py:76, never on the agent goal tool path. | `agent-verse-backend/app/agent/graph.py:827-876; app/workflow/steps/tool_step.py:71-76` |
| 50 | low | partial | _WORKER_CHECKPOINTER is still None (tasks.py:111,138) and is passed to AgentGraph (tasks.py:3529) and workflow/celery_tasks.py:140, so LangGraph uses MemorySaver; durability still comes only from GoalCheckpoint rows (agent/graph.py:827-876) and persisted workflow step results. | `agent-verse-backend/app/scaling/tasks.py:111,138,3529` |

### Gateway channel Slack: inbound message → agent/goal path (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 51 | medium | partial | Unchanged. Pending claims route nothing until the code arrives on the channel, but legacy_unverified Slack mappings still route (verification.py:65, ingestion.py:215-221), and any workspace member who can post the code can verify and displace a rival legacy mapping (verification.py:405-439). | `agent-verse-backend/app/api/channels/verification.py:395-447,65` |

### Gateway channel Teams: inbound message → agent/goal path (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 52 | medium | partial | Teams inbound auth works (Bot Framework JWT per binding), but _send_chat_reply still has only telegram/whatsapp/slack branches (router.py:883-893) and MicrosoftTeamsAdapter has no send method, so reply_sent is always False for Teams (:1040). | `agent-verse-backend/app/gateway/router.py:875-894` |

### Trigger/schedule CRUD and ScheduleStore (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 53 | medium | partial | The SCHEDULE intent path validates and passes quota_plan (chat/service.py:853-879), but the compound-turn _fulfill_schedule (service.py:1884-1905) still calls create_async with no creatable_error and no quota_plan, so it bypasses PLAN_MAX_TRIGGERS and unsupported-spec checks; its bare except also answers 'I'll ...' on failure, so a failed create looks scheduled. | `agent-verse-backend/app/chat/service.py:1884-1905` |

### TriggerType discord_event: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 54 | medium | partial | Unchanged. Pending claims route nothing until the code arrives on the channel, but legacy_unverified mappings still route (verification.py:65, ingestion.py:215-221), and verify_from_inbound accepts a code from any member able to post in the guild/workspace and then displaces a rival legacy mapping (verification.py:405-439), so a guest member can supersede the owner's legacy mapping. | `agent-verse-backend/app/api/channels/verification.py:395-447,65` |

### Celery app, per-plan queue routing and worker topology (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 55 | low | partial | Unchanged: broker_transport_options visibility_timeout = longest plan goal timeout + 1h is still global for every task (celery_app.py:478-487). Workflow runs (workflow.redispatch_stuck_runs :462) and ingestion (ingestion.reap_stale_jobs :326) have sweepers; other acks_late tasks on a crashed worker still wait ~25h. | `agent-verse-backend/app/scaling/celery_app.py:478-487` |

### Event-bus trigger consumers (TriggerConsumerSupervisor) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 56 | low | open | Unchanged: _extended_specs still sets mqtt_client = None ('not wired by default', supervisor.py:271-283), so MQTTTriggerConsumer is always skipped. MQTT is absent from the BEAT/PUSH/CONSUMER sets in dispatch_map.py and resolves to UNSUPPORTED (:136,151), so creation is refused honestly. | `agent-verse-backend/app/triggers/supervisor.py:264-283` |

### Messaging gateway (Telegram / WhatsApp / Slack / Teams / generic webhook, /v1/gateway) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 57 | low | partial | No change since the recert (no main commits under app/api/channels or app/gateway). New claims are pending until an on-channel code or operator approval; legacy_unverified mappings (pre-fix, possibly squatted) still route (binding_store.py:40,146) until superseded or reviewed. | `agent-verse-backend/app/api/channels/verification.py:18-25; app/gateway/binding_store.py:40,146` |
| 58 | low | partial | Unchanged: relay commands use the tenant's real plan (router.py:433), but per-org /v1/gateway/{org}/{channel} webhooks still trust a tenant only via GATEWAY_INGRESS_SECRET + header (:186-200) and _tenant_ctx_for still seeds PlanTier.FREE (:203-210); self-service tenants must use /{channel}/chat bindings. | `agent-verse-backend/app/gateway/router.py:186-210,433` |

### TriggerDispatcher governance pipeline (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 59 | low | partial | The circuit is shared across replicas via Postgres goal outcomes (_goal_outcomes_open, dispatcher.py:306,467). The bulkhead is still acquired at :318 and released in the finally at :462-463 right after enqueue, so it bounds concurrent dispatch calls, not in-flight goals. | `agent-verse-backend/app/triggers/dispatcher.py:306,318,462-463` |

### TriggerType alertmanager: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 60 | low | partial | Unchanged: Alertmanager goes through TriggerDispatcher with episode dedup and real plan, but it answers 503 only when every firing alert failed (integrations.py:592-593); a partly failed batch returns 200 and the failed alerts wait for repeat_interval. | `agent-verse-backend/app/api/integrations.py:587-598` |

### TriggerType chat_command: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 61 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType chat_keyword: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 62 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType chat_mention: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 63 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType email_arrival: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 64 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType email_intent: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 65 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType form_submission: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 66 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

### TriggerType rest: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 67 | low | open | Unchanged: a REST trigger can only be fired by the authenticated POST /schedules/{id}/fire (schedules.py:445); the token webhook route maps unknown path types to 'webhook' (api/triggers.py:930-932) and the accepted set (:100-102) excludes REST, so REST has no token ingress. | `agent-verse-backend/app/api/schedules.py:445; app/api/triggers.py:930-932` |

### TriggerType sms_inbound: firing path (event → dispatcher → goal) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 68 | low | partial | No main commit touched app/api/channels or app/gateway since the recert. New mappings are pending until an on-channel one-time code (or operator approval for sms/email/form/meeting/voice) verifies them (api/channels/verification.py:1-25,395-447); legacy_unverified mappings, possibly squatted pre-fix, still route (ROUTABLE_STATUSES verification.py:65; ingestion.py:215-221; gateway/binding_store.py:40,146) until superseded or operator-reviewed. | `agent-verse-backend/app/api/channels/verification.py:65; app/api/channels/ingestion.py:215-221` |

## Knowledge: ingestion, chunking, embedding, retrieval — 13 pending

### Legacy per-source ingest endpoints (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 69 | medium | partial | Routes are honest 200s, but github/confluence/jira/slack still fetch, screen and embed the whole source synchronously in the API request (ingestor.ingest_* then _ingest_chunks_from_source), with no durable job or worker offload. | `app/api/knowledge.py:2543,2570,2602,2638` |
| 70 | low | partial | Count and total-byte budget remain enforced, but MediaIoBaseDownload is still built without chunksize and max_bytes is checked only after next_chunk(), so one oversized file is buffered up to the 100 MiB default chunk before the cap fires; the listed 'size' metadata is not used to pre-skip. | `app/ingestion/connectors/gdrive_connector.py:147-155; app/api/knowledge.py:3982-3984` |
| 71 | low | partial | Catch-all still uses _raise_upstream_error, but per-file failures still append str(file_exc) raw into 'failed' and 'errors', returned in 207 bodies and the 502 detail. | `app/api/knowledge.py:4015,4033` |

### Collection-scoped orchestrated ingestion + indexing strategies (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 72 | low | open | IndexingStrategy is still Literal['raptor','agentic_chunking']; other strategies get an honest 422 (scope limit, not fake behaviour). | `app/api/knowledge.py:259` |
| 73 | low | partial | Unimplemented strategies are refused and 'fixed' is real, but 'paragraph' and 'dom' still alias SemanticChunker and are listed in SUPPORTED_CHUNKING_STRATEGIES, so a Source can request them and silently get semantic chunks. | `app/ingestion/chunkers/__init__.py:47,53` |

### Embeddings platform (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 74 | low | partial | configure_usage_redis_from_env is still called only from the worker ingestion builder; worker_process_init does not enable it and re_embed_collection_async (embed_metered) never calls it, so a re-embed or agent retrieval in a worker child that ran no ingestion task records no Redis usage (charging still happens). | `app/scaling/celery_app.py:550-555; app/scaling/tasks.py:8494-8565; app/ingestion/worker_services.py:62` |

### Ingestion connectors (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 75 | low | open | Legacy routes still import app/knowledge/ingestors/{pdf,docx,github,confluence,jira,slack}_ingestor alongside app/ingestion/connectors/*; two parallel ingestor stacks remain. | `app/api/knowledge.py:2504,2531,2550,2577,2609,2645` |

### Parsers and chunkers (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 76 | low | partial | Same as F068-02: 'paragraph' and 'dom' still map to SemanticChunker; other unimplemented names are refused. | `app/ingestion/chunkers/__init__.py:47,53` |

### RAFT lifecycle (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 77 | low | partial | Unchanged since the audit: OpenAI plus one OpenAI-compatible vendor; _SERVING_PROVIDER_TYPES is still {'openai':'openai'} and Bedrock/Vertex/Anthropic fine-tuning are unsupported. | `app/rag/raft_inference.py:33,105-125` |
| 78 | low | partial | Opt-in real test (RAFT_REAL_FINE_TUNE=1) still exists with no evidence of a run; CI coverage remains fakes only. | `tests/real_e2e/test_raft_real_fine_tune.py` |

### Repository (git clone) ingestion + durable knowledge ingestion jobs (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 79 | low | partial | Durable-path tests (enqueue, 429 cap, failed enqueue, worker services, DLQ replay/permanent) plus tests/ingestion/test_repo_ingest_guardrail_rules_bound.py exist, but still no test of a worker dying mid-clone and an acks_late redelivery re-claiming the leased job (app/api/knowledge.py:1756-2020 lease path untested for that case). | `tests/api/test_repo_ingest_durable.py:41-158` |

### Rerankers (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 80 | low | partial | 'llm' is still an accepted RerankStrategy and is honestly reported as cross_encoder with a reason, but no LLM reranker exists behind it. | `app/context/rerank_policy.py:38,186-190` |
| 81 | low | partial | Unavailable/warming cross-encoder is counted (RERANK_DEGRADED_TOTAL) and flagged, but a CE inference error still silently falls back to TF-IDF (only last_reason set, last_strategy_used stays cross_encoder), so results are labelled rerank_strategy=cross_encoder with no metric. | `app/context/rerank_policy.py:410-415; app/rag/rerank_stage.py:169,178` |

## Memory, evals & self-improvement — 9 pending

### Self-optimizer v2 (Bayesian A/B config experiments) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 82 | medium | partial | on_goal_completed is still called only inside the `if success:` branch; the else branch (failed goals) never records the arm, so a candidate config that makes goals fail is not penalised. | `app/agent/nodes/verifier_mixin.py:468,833-866` |
| 83 | low | partial | The Postgres test still runs on the container superuser URL (RLS bypassed); no app-role/RLS test of the agents UPDATE done by apply_suggestion. | `tests/intelligence/test_self_optimizer_v2_postgres.py:56-66` |

### Verifier calibration, learning experiments, experiment registry, cost optimizer (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 84 | medium | partial | The Celery worker still uses _default_calibration_store, whose _db is set only in the API lifespan, so worker-verified goals are never persisted; feedback (api/goals.py:1378-1393) updates 0 rows with no signal and /intelligence/calibration omits them. | `app/scaling/tasks.py:3312-3314; app/main.py:2075; app/intelligence/verifier_calibration.py:168` |
| 85 | low | open | LearningExperimentService is still only instantiated onto app.state with no consumer in app/. | `app/main.py:1530-1546,2943-2950` |
| 86 | low | partial | false_confirm_rate is exposed via GET /intelligence/calibration, but app/intelligence/cost_optimizer.py is still referenced only by strategy_registry with no runtime caller. | `app/orchestration/strategy_registry.py:1660; app/api/enterprise.py:1621-1634` |

### A/B testing engine (app/optimization) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 87 | low | open | Still no caller of get_experiment_arm/get_arm_stats/can_promote_variant outside app/optimization; the engine is only DB-wired at startup (dead code, honestly listed as PLAN). | `app/main.py:2081-2091` |

### Canonical governed memory records and Reflexion service (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 88 | low | partial | The no-embedding lexical candidate query still ORDER BY func.similarity(safe_summary, query) DESC LIMIT with no '%' predicate, so the GIN trgm index cannot serve it; per-tenant scan+sort remains (only when no query embedding). | `app/memory/postgres_repository.py:122-132` |

### Eval suites and golden tasks, agent rollout gate (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 89 | low | partial | PUT re-runs the gate only when becoming fully-autonomous or changing eval_suite_id; an already fully-autonomous agent can change system_prompt/model/connectors (and SelfOptimizerV2.apply_suggestion can rewrite them) without the gate, even though the gate pins agent_config_hash. | `app/api/agents.py:1138-1150` |

### Meta-agent (NL command to agent config) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 90 | low | partial | Policy suggestions are still never applied; the response honestly says policy_suggestions_applied=False with a note. | `app/api/agents.py:1089-1093` |

## Tools & connectors — 17 pending

### Connector health monitoring (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 91 | medium | open | probe_connector still issues a fixed GET {base}/health and classify_health maps any 4xx (e.g. a standard MCP server's 404) to 'degraded'; no MCP initialize/tools-list probe. | `agent-verse-backend/app/mcp/health_sweep.py:77-104` |
| 92 | low | open | _probe_target returns None for builtin:// (or URL-less) connectors and _probe_row then skips them, so built-in connectors still get no health snapshot. | `agent-verse-backend/app/mcp/health_sweep.py:107-115, 127-128` |

### OpenAPI import and tool capability registry (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 93 | medium | open | /capabilities/search and /missing still wrap discover_all_tools in contextlib.suppress(Exception); discover_all_tools itself also swallows errors (client.py:693-695), so a failure reads as 'no tools'. | `agent-verse-backend/app/api/connectors.py:2033-2035, 2154-2156` |
| 94 | medium | open | _search_semantic still embeds every tool descriptor on every query with no embedding cache. | `agent-verse-backend/app/mcp/capability_search.py:188-190` |
| 95 | low | open | /capabilities/missing still does substring matching of catalog names against the goal, and can_proceed = no suggestions OR any tool exists (connectors.py:2183). | `agent-verse-backend/app/api/connectors.py:2148-2184` |
| 96 | low | open | GET /capabilities still selects every tool_capabilities row for the tenant with no LIMIT or pagination. | `agent-verse-backend/app/api/connectors.py:1975-2013` |
| 97 | low | open | discover_all_tools still wraps the whole per-connector loop in one try, so the first raising connector aborts the rest and a partial list is returned as complete. (The planner path no longer uses it: tool_context_builder.py:100-113 handles errors per connector.) | `agent-verse-backend/app/mcp/client.py:684-695` |
| 98 | low | open | /capabilities/search still runs live discovery for every connector serially and embeds all descriptors per request, with no budget charge or rate limit. | `agent-verse-backend/app/api/connectors.py:2026-2045; app/mcp/capability_search.py:180-190` |

### A2A protocol and agent directory (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 99 | low | open | Still an honest 501 for /.well-known/agents/{id}.json and the listing (no public-agent visibility model). | `agent-verse-backend/app/api/agent_directory.py:26-37` |
| 100 | low | open | A2ATaskRequest.context is still accepted but submit_goal is called with goal/priority/dry_run/tenant_ctx only, so the context is silently dropped. | `agent-verse-backend/app/api/a2a.py:375, 441-446` |
| 101 | low | open | A2ATaskRequest still has no bounds on goal, context, callback_url (column is VARCHAR(500), 0023_a2a_tasks.py:18), requester_agent_id (VARCHAR(255)) or priority; an oversized callback_url/requester id fails the INSERT in _persist_task with a 500 and the goal bypasses the /goals 10k cap. | `agent-verse-backend/app/api/a2a.py:374-379` |
| 102 | low | open | Without app.state._redis the replay cache is still the per-process _seen_signatures dict, so on multi-replica deployments without Redis each replica accepts a captured signature once within the window. | `agent-verse-backend/app/api/a2a.py:163-184` |

### Built-in MCP servers (registry_wiring) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 103 | low | open | certification.py / certification_manifest.py are still referenced only by each other and tests/mcp/test_connector_certification*.py; no app/ caller. | `agent-verse-backend/app/mcp/certification.py` |

### MCP client tool discovery and dispatch (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 104 | low | partial | The stale cache result served on an open circuit is now flagged ToolCallResult.stale=True, but it is still success=True and nothing in app/ reads .stale, so the planner/agent still consumes it as a live successful call. | `agent-verse-backend/app/mcp/client.py:1395-1402` |
| 105 | low | open | _dispatch_jira_rest_tool still returns success=False for any tool other than jira_search_issues. | `agent-verse-backend/app/mcp/client.py:1029-1035` |
| 106 | low | open | _circuit_breakers and _schema_cache are still plain unbounded dicts keyed by tenant x server with no eviction (and _schema_cache is never invalidated when a connector changes). | `agent-verse-backend/app/mcp/client.py:279, 298, 372-406, 1486-1494` |

### Native tool endpoints (tenant file workspace, email) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 107 | low | partial | Unchanged since last audit: operator/admin role, <=50 recipients per message, per-tenant daily recipient quota (fail-closed) and a durable audit row are in place, but there is still no per-tenant recipient allowlist or approval step before relaying from the platform sender. | `agent-verse-backend/app/api/tools.py:337-415` |

## Tenancy & security — 31 pending

### MFA (TOTP) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 108 | medium | open | mfa_enforcement_enabled still defaults to False and no compose/helm/k8s config sets MFA_ENFORCEMENT_ENABLED, so TenantMiddleware (middleware.py:581) and ws_auth (ws_auth.py:110) accept the API key alone for MFA-enrolled tenants. | `app/core/config.py:666-668` |
| 109 | medium | open | _check_rate_limit_global still swallows any Redis error and falls back to the per-process bucket, so during a Redis outage N replicas allow N x the TOTP attempt budget. | `app/api/mfa.py:104-110` |
| 110 | low | open | TenantMFA is still one row per tenant (tenant_id unique); no per-user TOTP secret even though SAML now creates per-user sessions. | `app/db/models/mfa.py:19` |
| 111 | low | partial | Legacy '.b64' rows still decrypt; reseal_if_needed re-seals them only on a successful verify, so tenants that never verify again keep plaintext rows (no migration). | `app/api/mfa_crypto.py:131-145; app/api/mfa.py:467,836` |

### Rate limiting and plan quotas (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 112 | medium | partial | Degraded budget is min(plan,120)/rate_limit_replica_count, but the setting still defaults to 1 and no compose/helm/k8s manifest sets RATE_LIMIT_REPLICA_COUNT, so N replicas still allow N x the capped limit during a Redis outage. | `app/tenancy/middleware.py:37-48; app/core/config.py:570` |

### SAML 2.0 SSO (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 113 | medium | open | Replay key is still f'{name_id}:{session_index}', not the assertion ID, and ACS still returns 404 'SAML not configured' vs 401. Severity raised low->medium because ACS now issues sessions: with an IdP that omits SessionIndex the key is 'name_id:' and the user's second legitimate login within 1h is rejected as a replay. | `app/auth/saml_provider.py:151-153; app/api/enterprise.py:2571-2572` |
| 114 | medium | open | _check_saml_replay is still EXISTS then SETEX (not SET NX). Severity raised low->medium: it is no longer latent, since ACS now mints login codes, so two concurrent posts of one assertion on different replicas both get sessions. | `app/auth/saml_provider.py:213-218` |

### SCIM 2.0 provisioning (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 115 | medium | partial | SCIM PATCH active=false/PUT/DELETE now revoke the user's sessions in the same transaction and purge the Redis cache (503 if the purge fails), and session resolve refuses non-active memberships. Still open: tenant API keys carry no creator (app/db/models/tenant.py:42-62), so keys a deprovisioned admin minted keep working, and the Keycloak JWT path never reads tenant_memberships. | `app/auth/scim_handler.py:152-175,382,445,482; app/auth/user_sessions.py:367-389` |
| 116 | low | open | scim_router still routes only /Users and /Users/{scim_id}; no /Groups, /ServiceProviderConfig, /Schemas or /ResourceTypes. | `app/api/enterprise.py:2718-2756` |
| 117 | low | open | count is still only clamped below (max(count,0)) and passed to .limit(count); the route takes any int, so a page can be unbounded. | `app/auth/scim_handler.py:249,266; app/api/enterprise.py:2722` |

### SSO: Keycloak OIDC and Google OAuth (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 118 | medium | open | map_realm_roles still defaults to ('viewer',) and _get_or_provision_tenant JIT-creates one personal tenant per SSO subject; no org/group-to-tenant mapping for Keycloak (SAML now has per-tenant membership, Keycloak does not). | `app/auth/keycloak.py:147-157,212-235` |
| 119 | medium | partial | Unchanged: startup still mirrors every active tenant and key per replica; one batched key query when a system session factory exists (:1107-1133), else one RLS query per tenant (:1069-1079). Key resolution itself is DB-authoritative (:405-423), so the mirror is memory/startup cost only. | `app/services/tenant_service.py:1033-1105; app/main.py:1598` |
| 120 | low | open | Google callback still verifies the account and answers an honest 501 with no session, even though a user-session store (app/auth/user_sessions.py) now exists for SAML and the /auth/session/exchange docstring mentions Google. | `app/auth/google_oauth.py:179-196` |

### Scope enforcement, RBAC and IP allowlist enforcement (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 121 | medium | open | No app code writes api_key_scopes or role_assignments (only migrations/seeder); _load_scopes therefore always falls back to ROLE_SCOPES and custom roles cannot be granted. | `app/auth/scope_enforcement.py:722-792` |
| 122 | medium | partial | user_roles is still consulted only for HITL reviewer auto-assignment; no authorization path reads it and load_roles_from_db has no caller. | `app/tenancy/rbac.py:90; app/workflow/approval_store.py:355-363` |
| 123 | medium | open | is_ip_allowed still returns True for any loopback source before checking CIDRs, so a same-host proxy/sidecar not in TRUSTED_PROXIES bypasses every tenant allowlist. | `app/auth/ip_allowlist.py:130-132` |
| 124 | low | partial | RoleResolver is gone, but ABACEvaluator is still referenced only by tests (tests/auth/test_scopes_rbac.py:24,367). | `app/auth/scope_enforcement.py:455` |
| 125 | low | partial | Unchanged since last check: role-less GET pass-through remains, but DB key resolution defaults empty roles to operator, so the branch is reachable only for stream-token contexts limited to GET /stream\|/events. | `app/auth/scope_enforcement.py:538-563; app/services/tenant_service.py:748` |

### Tenant lifecycle and API-key management (app/api/tenants.py + app/services/tenant_service.py) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 126 | medium | partial | load_roles_from_db still has no caller; user_roles is read only by approval_store.py:355-363 for HITL reviewer auto-assignment, so POST /tenants/me/roles grants no authorization. | `app/tenancy/rbac.py:90; app/api/tenants.py:685-718` |
| 127 | medium | open | AgentStore.list_async still catches every DB exception and returns the per-replica memory cache, so export_tenant_data's 503 branch (app/api/tenants.py:1281-1285) never fires on a DB failure and the export returns 200 with a partial agents list. | `app/api/agents.py:322-329` |
| 128 | medium | open | POST /tenants/me/export still OFFSET-pages (_collect_all_pages, :1216-1246) into one in-memory list and one JSON response. The new keyset-paged async job (a09-F212-03) is for the separate /enterprise/compliance export, not this route. | `app/api/tenants.py:1216-1295` |
| 129 | low | open | _api_key_limit_denial still counts is_active only; _db_list_api_keys (app/services/tenant_service.py:623-643) does not filter expires_at, so expired-but-active keys consume plan slots. | `app/api/tenants.py:180` |
| 130 | low | open | /tenants/me/export still returns only goal summaries and agents (counts goals/agents); no results, events, plans, memory or knowledge documents. | `app/api/tenants.py:1287-1293` |

### WebSocket authentication (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 131 | medium | open | resolve_ws_tenant still enforces IP allowlist, scopes and MFA but no per-tenant connect rate limit or concurrent-socket cap. | `app/tenancy/ws_auth.py:126-178` |
| 132 | medium | open | _key_from only takes X-API-Key/Bearer/av.v1 values and resolves them through resolve_api_key (main.py:3270-3276); Keycloak JWTs, agent JWTs and the new SAML avs_ session tokens are never tried, so SSO users cannot open write sockets. | `app/tenancy/ws_auth.py:58-80,143-151` |

### A2A protocol, agent directory and JWKS discovery (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 133 | low | open | Per-agent directory routes still answer an honest 501 (no public-agent visibility model). | `app/api/agent_directory.py:26-37` |

### API-key authentication pipeline (TenantMiddleware, stream tokens, security headers) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 134 | low | open | Bypass is still a static prefix tuple (/integrations/, /v1/gateway/, /channels/<x>/, /scim/v2, /enterprise/saml/acs/ ...) checked at :519; tests remain example-based (tests/tenancy/test_middleware_comprehensive.py:222, test_public_webhook_bypass.py) and nothing walks app.routes under the bypass prefixes. | `app/tenancy/middleware.py:112-162,519` |

### PostgreSQL Row-Level Security (app/db/rls.py + migrations) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 135 | low | open | Unchanged and by design: users has no RLS, and the agent_templates policy USING (tenant_id IS NULL OR own) has no WITH CHECK. A tenant could therefore write system rows, but the AgentTemplate model (app/db/models/intelligence.py:140) has no app callers, so this is latent. | `app/db/migrations/versions/0072_users_and_memberships.py:67; 0009_intelligence.py:216-219` |

### SSRF egress guard (app/net/ssrf_guard.py) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 136 | low | partial | The cited sites (mcp/client.py, a2a.py, knowledge.py) now use assert_public_url_async/to_thread, and an AST guard (tests/net/test_no_blocking_dns_in_async.py:33-40) blocks new ones. Five known async call sites still run blocking getaddrinfo on the event loop. | `app/agent/tools/a2a_call.py:147; app/gateway/router.py:290; app/ingestion/connectors/http_connector.py:109,134; app/rag_platform/hosted_reranker.py:75` |

### Unwired tenancy/auth modules (library-only) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 137 | low | open | None of the five modules has an importer anywhere in app/. | `app/tenancy/entitlements.py; app/auth/temp_elevation.py; app/tenancy/tenant_users.py; app/auth/custom_roles.py; app/tenancy/sub_tenants.py` |
| 138 | low | open | The docstring still claims workers mint per-goal tokens, but mint/verify have no caller outside the module, and it still carries a hard-coded fallback secret (unused, so latent). | `app/auth/goal_tokens.py:1-13,35` |

## Governance — 15 pending

### Trust approvals and compliance bundles (/trust) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 139 | medium | open | TrustApprovalStore is still used only by the /trust API; no agent, tool-gate or worker path reads trust approvals, so recorded goal_id/tool_name approvals never gate execution. | `app/main.py:2625-2629; app/api/trust_governance.py` |
| 140 | low | open | GET /trust/compliance-bundles still lists app.guardrails_v2.models.COMPLIANCE_BUNDLES while enable/disable use app.governance.compliance_bundles. | `app/api/trust_governance.py:400-410,483,508` |
| 141 | low | open | The process-local _approvals dict fallback is unchanged and still answers 200, not 503, when trust_approval_store is absent. | `app/api/trust_governance.py:33,233,286,347,368` |

### Audit trail (AuditLog, audit_log table, SIEM forwarding) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 142 | low | partial | Unchanged since the last check: sync record() writes are tracked, retried with backoff and flushed. A write that exhausts its retries is still only logged and counted (audit_write_lost) with no durable audit outbox, and the executor still uses the sync record(). | `app/governance/audit.py:185-249; app/agent/nodes/executor_mixin.py:3121,3445` |
| 143 | low | open | audit_admin_action still appears only at its definition and docstring example; no route applies it. | `app/governance/audit_v2.py:559,573` |

### Cost budgets and enforcement (CostController, RedisCostController, budget APIs) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 144 | low | open | The worker still builds CostController() per goal task, swaps in RedisCostController only when the REDIS_URL env var is set (no broker_url fallback like _worker_async_redis), and swallows construction errors with 'except Exception: pass'. | `app/scaling/tasks.py:3154-3175` |

### Grantex tool grants (grant store, enforce_tool_call, delegation, grant budgets) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 145 | low | open | grants router still exposes only create/list/get/revoke; delegation happens only inside delegate_active_grants (app/governance/grants/delegation.py:80-114), and no REST endpoint creates delegations or reads delegation chains. | `app/api/grants.py:131,170,177,187` |
| 146 | low | partial | _agent_grants_enforced now returns True on any exception (GRANT-07). The worker still initialises _worker_enforce_grants=False (:3453) and sets it only at :3467, after clamp_autonomy_mode/the ceiling lookup, so an exception in that try block leaves grants unenforced, despite the 'fail toward the restrictive side' comment. | `app/services/goal_service.py:143-154; app/scaling/tasks.py:3452-3474` |

### Guardrails v2 (content rules engine) plus v1 guardrail configs (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 147 | low | open | POST /bundles/{name} still mints uuid4 rule ids on every call and uses the sync in-memory add_rule rather than add_rule_durable (app/guardrails_v2/engine.py:295). Re-enabling duplicates rules, and the 'enabled' answer has no durable write behind it. | `app/api/guardrails_v2.py:327-351` |
| 148 | low | open | The RAG_INGEST screen still acts only on 'blocked' and 'redacted_content', so REQUIRE_HITL is ignored. A redacting rule triggers a second evaluate() on the PII-redacted text, which duplicates violations and LLM-judge charges. | `app/ingestion/pipeline.py:563-581` |

### HITL approval gateway (governance/hitl.py + /governance approvals) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 149 | low | open | ChatHITLCard still builds /hitl/{id}/approve?token=<uuid>, which the sig+exp HitlLinkDecisionPage rejects, and stream_hitl (app/chat/stream.py:147) still has no caller. | `agent-verse-frontend/src/features/chat/ChatHITLCard.tsx:50-52` |
| 150 | low | partial | REST now answers 'vote_recorded' via _vote_outcome, but that reads the process-local get_request. aget_request/_merge_row does not cache a request this replica did not raise, which is the normal case since worker-raised gates are never cached on the API replica. There get_request returns None and a below-quorum vote is still answered 'approved'. | `app/api/governance.py:895-906; app/governance/hitl.py:769-777,1116-1126` |

### Permission matrix (default-deny tool permissions, per-agent permissions) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 151 | low | open | _LOCAL_DAILY is still an unbounded module dict keyed by (tenant, agent, tool, UTC day) with no eviction. It is used only when no Redis is wired. | `app/governance/agent_permissions.py:192-220` |

### Policy engine (tool policies, propagation, versions, policy-as-code) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 152 | low | open | app.governance.time_policy / TimePolicyEngine is still imported nowhere in app/. | `app/governance/time_policy.py` |
| 153 | low | open | _time_windows_by_name still returns only (hours, weekdays), so a non-UTC Policy.timezone (policies.py:56) reverts to UTC on the strict reload. The API exposes no timezone field. | `app/governance/policies.py:77-101` |

## Org & collaboration — 24 pending

### Human/agent collaboration sessions (operations, rounds and consensus, delegation, insights, WebSocket, org presence, Yjs CRDT) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 154 | medium | open | _crdt_manager.set_redis(redis_for_runtime) (main.py:2396) still gets the pool client created with decode_responses=True (pools.py:44; direct fallback main.py:1297-1301), so non-UTF-8 Yjs bytes fail in load_snapshot/pubsub and the errors are swallowed (collab.py:802-803,856-858). | `agent-verse-backend/app/main.py:2394-2396; app/core/pools.py:44; app/api/collab.py:794-803,844-858` |
| 155 | medium | open | Every 50th update still saves that single incremental update as crdt:snapshot (collab.py:832-841), and late joiners get it as 'full history' (:941-945), i.e. 1 of N updates. | `agent-verse-backend/app/api/collab.py:832-841,941-945` |

### Org intelligence, digital twin, briefs, decision history, graph versioning, crons (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 156 | medium | partial | Both endpoints now read tenant+org scoped rows (org_capabilities plus department capability_domains; org_decisions newest-first). But OrgService.create_capability has no caller in app/ and record_decision is only called by the voice approve intent (voice/intent_router.py:206; the org_decision workflow step only logs), so capabilities show only dept domains and decisions are almost always empty. | `agent-verse-backend/app/org/router.py:3012-3121; app/org/service.py:894,1537` |

### Unified Chat (sessions, messages, streaming, QA and goal turns, memories, templates, connected services, artifacts, folders, search, code execution) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 157 | medium | open | Parked by owner. Folders are still the in-memory self._folders dict (service.py:387,1980-1990) and move_session_to_folder still calls sync update_session (service.py:1997-1999; route router.py:537-548), so DB-mode sessions 404. | `agent-verse-backend/app/chat/service.py:387,1980-1999; app/chat/router.py:512-548` |
| 158 | low | open | Parked by owner. record_usage (service.py:583) still has no caller in app/, so session_usage_summary behind GET /chat/sessions/{id}/usage (router.py:472-479) is always zero. | `agent-verse-backend/app/chat/service.py:583,606; app/chat/router.py:472-479` |

### Voice (STT/TTS, greeting, persona cloning, real-time voice WebSocket, proactive voice alerts, phone calls) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 159 | medium | open | No app/voice commit since the recert; publish_voice_alert still has no caller in app/ (only its own docstring at alerts.py:175), so /v1/voice/alerts/stream emits keepalives only. | `agent-verse-backend/app/voice/alerts.py:163` |
| 160 | medium | open | TTS still auto-falls back to kokoro/browser on ImportError (providers/__init__.py:97-110); browser_fallback.is_ready() is True (:28-29) and synthesize returns 100ms of silence (:42-44), with no signal in the TTS response. | `agent-verse-backend/app/voice/providers/__init__.py:97-110; app/voice/providers/tts/browser_fallback.py:28-44` |
| 161 | low | open | The dev fallback still builds VoiceAlertManager from app.state.redis (never set by create_app); the lifespan sets voice_alert_manager at main.py:1159, so only non-lifespan apps hit it. | `agent-verse-backend/app/voice/router.py:590-595` |
| 162 | low | open | TODO(D-24) still present; voice consent stays in-memory per session (fails closed, no durable record). | `agent-verse-backend/app/voice/consent.py:21-24` |
| 163 | low | open | _fetch_wywa still reads app.state.redis (never set) instead of the _voice_redis helper, so the digest gets no Redis cache. | `agent-verse-backend/app/voice/router.py:428-430` |

### Agent Civilization (society, governor, bus, blackboard, learning, spawn, replay, live stream) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 164 | low | open | civilization_tick still swallows a failed constitution read (except: pass, running the default Constitution(), tasks.py:7645-7671) and returns {'error': str(exc)} instead of raising (:7728-7733), so Celery records success. | `agent-verse-backend/app/scaling/tasks.py:7645-7671,7728-7733` |
| 165 | low | open | HTTPException(500, str(exc)) / f'...{exc}' still at api/civilization.py:241,329,384,423,469,560,595, and the SSE stream_error still echoes str(exc) at :1049. | `agent-verse-backend/app/api/civilization.py:241,329,384,423,469,560,595,1049` |
| 166 | low | open | The society graph still wraps the bus read in 'except Exception: pass' (api/civilization.py:637-660), so a bus failure returns a graph with no bus edges, indistinguishable from 'no messages'. | `agent-verse-backend/app/api/civilization.py:637-660` |

### Coordination runtime: sessions, transcript, group-chat WebSocket, event replay, handoffs (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 167 | low | open | create_session still passes admission.civilization_id/goal_id straight to the store with no ownership check (coordination/service.py:81-88); no commit touched coordination/service.py since the recert. | `agent-verse-backend/app/coordination/service.py:66-88` |

### Org Brain autonomous loop and ambient team collaboration (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 168 | low | open | Parked by owner. No main commit touched app/org/brain*.py or the brain tests since the recert; the loop wiring test still monkeypatches OrgBrain.run_tick and the only integration-marked brain test is the store test. Loops stay gated by org_autonomy_enabled (scaling/tasks.py:8871,9109). | `agent-verse-backend/tests/org/test_brain_loop_wiring.py:96-105; tests/org/test_brain_store.py:32` |

### Org OS core CRUD (organizations, departments, teams, custom roles, tasks, missions, schedules, attachments, department memory) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 169 | low | open | grep org_artifacts/OrgArtifact over app/ (excluding migrations and the graphify cache) still finds nothing; the table remains unused. | `agent-verse-backend/app (no reference)` |
| 170 | low | open | _tenant_overrides is still a per-process dict (feature_flags.py:87) and enable_for_tenant/disable_for_tenant (:116-123) still have no caller in app/ (latent only). | `agent-verse-backend/app/org/feature_flags.py:87,116-123` |
| 171 | low | partial | Custom-role create/update/delete now require ORG_ADMIN (router.py:1969,1995,2019), but OrgRoleStore is still only listed/written; no enforcement path reads custom-role permissions. | `agent-verse-backend/app/org/router.py:1969-2035; app/org/runtime_store.py:55` |

### Org mission execution, HITL approvals and brain proposals (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 172 | low | open | HITL register and outer gate-wiring exceptions still only log (service.py:3162-3166, 3236-3240) and the mission is still dispatched (submit_goal :3282); still mitigated by forcing autonomy_mode='supervised' when approval_gates is set (:3258). | `agent-verse-backend/app/org/service.py:3162-3166,3236-3240,3282` |
| 173 | low | open | org_preview_mission is still a documented keyword heuristic with similar_missions_count omitted (honest, cosmetic). | `agent-verse-backend/app/org/router.py:3639-3670` |
| 174 | low | partial | approve_task/reject_task require TEAM_LEAD (router.py:923,1041), but there is still no task_kind=='approval_gate' or status=='approval_required' check before update_task_status(..., 'running') (:942-947). | `agent-verse-backend/app/org/router.py:907-950,1041` |
| 175 | low | open | update_task_status still checks only membership in TASK_STATUSES (service.py:1498), not the transition, so approve_task can move a completed/failed/cancelled task back to running. | `agent-verse-backend/app/org/service.py:1489-1509` |

### Org realtime streams, Universal Command Gateway, Graphify job, org MCP WebSocket, emergency stop (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 176 | low | open | The graphify job is still launched with asyncio.get_event_loop().create_task(_run_graphify_job(org_id, tenant_id, job_id, request)) holding the Request (router.py:1725); progress is pub/sub only. | `agent-verse-backend/app/org/router.py:1725` |
| 177 | low | open | High-risk commands are still matched by a substring list and stored as 'pending_2fa' (router.py:2376-2389) with the response promising 2FA confirmation (:2436-2440); no route confirms or expires them. | `agent-verse-backend/app/org/router.py:2376-2389,2436-2440` |

## Frontend & GitHub Action — 19 pending

### Frontend realtime transports (SSE / WebSocket) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 178 | medium | open | _openEventSource still builds a new EventSource with only a fresh stream token (OrgRealtimeManager.ts:124-138); no Last-Event-ID or since cursor, so org events emitted during a disconnect are lost. | `agent-verse-frontend/src/features/org/OrgRealtimeManager.ts:115-140` |
| 179 | low | open | useOrgStream/useMissionStream still open relative EventSource URLs with no API base or auth (useOrg.ts:266,291) and still have no non-test caller. | `agent-verse-frontend/src/features/org/hooks/useOrg.ts:261-291` |
| 180 | low | open | Both sockets still send the permanent API key as the av.v1.<base64url(apiKey)> subprotocol (FE-04 only changed the WS origin), not a short-lived token like the SSE path. | `agent-verse-frontend/src/features/org/components/CursorPresence.tsx:18-21,58-78; src/lib/ws/useCollabSocket.ts:46-63` |

### Backend SDK helpers (agent-verse-backend/app/sdk) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 181 | low | open | Informational, unchanged: MockMCPServer has no runtime wiring; the only app/ import of app.sdk outside the package is app/cli/main.py:346 (AgentManifest). | `agent-verse-backend/app/sdk/mock_server.py; app/cli/main.py:346` |

### Frontend API client and contract drift vs backend OpenAPI (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 182 | low | open | No commit touched the contract test since the recert; it still checks method+path only (payload shapes, non-literal paths and WebSocket URLs unchecked). | `agent-verse-backend/tests/frontend/test_api_contract.py` |
| 183 | low | open | razorpay_key_id 'rzp_test_placeholder' (:131) and the is_mock branch (:143) are unchanged (backend-gated by allow_mock_payments). | `agent-verse-frontend/src/features/settings/BillingPage.tsx:131,143` |

### Frontend feature pages: browser-local state standing in for backend state (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 184 | low | open | Eval history is still localStorage (av_eval_history_{tenant}, EvalPage.tsx:152,163). | `agent-verse-frontend/src/features/eval/EvalPage.tsx:149-165` |
| 185 | low | open | History is still localStorage-only in GhostRunPage, OcrPage and PerceptionPage. | `agent-verse-frontend/src/features/goals/GhostRunPage.tsx:69-77; src/features/ocr/OcrPage.tsx:115-123; src/features/perception/PerceptionPage.tsx:68-75` |
| 186 | low | partial | OPS-37 added durable server-side background export jobs (TrainingExportJobsPanel, TrainingExportPage.tsx:675), but the inline export history is still localStorage-only (:86-101, comment: no backend export-history store) and Playground scenarios are still localStorage (PlaygroundPage.tsx:176-183). | `agent-verse-frontend/src/features/training/TrainingExportPage.tsx:86-101,675; src/features/playground/PlaygroundPage.tsx:176-183` |

### Org OS / Gateway frontend (features/org, features/gateway) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 187 | low | open | onStatusChange is still optional and neither caller passes it; useUpdateTaskStatus (useOrg.ts:242) still has no non-test caller, so the board is display-only. | `agent-verse-frontend/src/features/org/KanbanBoard.tsx:88,154; src/features/org/MissionPage.tsx:237; src/features/org/TeamPage.tsx:211` |
| 188 | low | open | All three org specs still test.skip unless TEST_ORG_ID/E2E_FULL. | `agent-verse-frontend/e2e/org/agent-profile.spec.ts:10; e2e/org/mission-creation.spec.ts:10; e2e/org/command-bar.spec.ts:10` |
| 189 | low | open | Still mocks the removed GET /v1/gateway/config and POST /v1/gateway/channels/{id}/connect. | `agent-verse-frontend/e2e/gateway.spec.ts:35,53,130` |

### Playwright e2e suites and CI coverage (agent-verse-frontend/e2e) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 190 | low | open | PRs still run only --project=smoke-live; nightly runs the 9 mocked projects (:194) and real-backend (:364). full-live, mobile and real-e2e are still never run in CI. | `.github/workflows/ci.yml:136,147; .github/workflows/nightly.yml:194,364` |
| 191 | low | open | Still fulfils the removed /v1/gateway/config and /v1/gateway/channels/{id}/connect routes. | `agent-verse-frontend/e2e/gateway.spec.ts:35,53,130` |
| 192 | low | open | Header still claims Python/TypeScript SDK parity while exercising mocked REST only. | `agent-verse-frontend/e2e/sdk-integration.spec.ts:1-14` |

### Python SDK (agent-verse-sdk-python) - REMOVED (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 193 | low | partial | Unchanged: SDK_PYTHON_ROOT resolves to None (no SDK dirs exist), so the SDK cases in test_async_gdpr.py and test_medium_fixes.py always skip. | `agent-verse-backend/tests/_paths.py:37-40,55-59; tests/compliance/test_async_gdpr.py:112-127; tests/agent/test_medium_fixes.py:114` |
| 194 | low | open | conftest still prepends the non-existent ../../../Archived/agent-verse-sdk-python to sys.path, and test_phase5_api.py:301 still skips 'agentverse SDK not available'. | `agent-verse-backend/tests/api/conftest.py:9-14; tests/api/test_phase5_api.py:301` |

### TypeScript SDK (agent-verse-sdk-typescript) - REMOVED (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 195 | low | open | Still claims 'Python SDK / TypeScript SDK' parity while exercising mocked REST only. | `agent-verse-frontend/e2e/sdk-integration.spec.ts:1-14` |
| 196 | low | open | SDK_TS_ROOT is still None so test_phase_completeness.py:59,69 always skip; .gitignore:28 still names agent-verse-sdk-typescript (copilot-instructions.md:17 now only lists it as removed). | `agent-verse-backend/tests/_paths.py:41-44,62-66; tests/agent/test_phase_completeness.py:59,69; .gitignore:28` |

## Enterprise & ops — 64 pending

### Agent template marketplace (v2 DB-backed plus legacy v1) (8)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 197 | medium | open | Required-connector check still guarded by hasattr(agent_store,'list_connectors') (no store defines it) inside 'except Exception: pass', so required connectors are never verified. | `app/enterprise/marketplace_v2.py:1861-1868` |
| 198 | medium | open | Still no approve/reject route; review_status is only written by publish_template's upsert and org_steps.py:127 ('pending'), so pending community templates never become visible. | `app/enterprise/marketplace_v2.py:1649-1654,1730` |
| 199 | medium | open | list_templates still logs any DB error and answers 200 from the built-in catalogue only, hiding tenant/community templates instead of 503. | `app/enterprise/marketplace_v2.py:1595-1626` |
| 200 | low | open | sort_by is still accepted and never passed to list_templates (ORDER BY fixed in marketplace_v2.py:1578); no uninstall endpoint under app/api. | `app/api/enterprise.py:561-573` |
| 201 | low | open | marketplace.py unchanged: publish_version logs DB errors and returns success; history [] on error and the route synthesises a current-version record (app/api/enterprise.py:779-790). | `app/enterprise/marketplace.py:358-371,396-397` |
| 202 | low | open | embed_marketplace_templates still only counts NULL embeddings and embeds nothing; search_templates is FTS via list_templates (marketplace_v2.py:2202-2219). | `app/scaling/tasks.py:7981-7982` |
| 203 | low | open | Integration test still exists and needs real Postgres; not run in this static audit, so marketplace RLS remains unverified. | `tests/enterprise/test_marketplace_rls_integration.py` |
| 204 | low | open | GET /marketplace/templates page/page_size are still unbounded ints (only POST /search model bounds them at :446-447). | `app/api/enterprise.py:559-560` |

### Billing and subscriptions (Razorpay, legacy Stripe, plan changes) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 205 | medium | open | billing.py has no commits since baseline; GET /billing/subscription still 'not_tracked' with current_period_end None; no subscription table or cancel/downgrade endpoint. | `app/api/billing.py:137-157` |
| 206 | medium | open | checkout.session.completed and customer.subscription.deleted still act on the event payload without _sync_plan_from_subscription (used only for .updated/.payment_failed, :893-904), so out-of-order delivery can re-upgrade or wrongly downgrade. | `app/api/billing.py:867-891` |
| 207 | low | open | create-order still writes an is_mock order whenever Razorpay is unconfigured regardless of allow_mock_payments; verify then 503s (:577). | `app/api/billing.py:492-518` |
| 208 | low | open | allow_mock_payments still has no validator or production guard in get_settings (config.py:741+); only the MOCK_PAYMENT_ACCEPTED warning (billing.py:593) remains. | `app/core/config.py:693-699` |
| 209 | low | open | Raw exception text still returned in 500/502 details. | `app/api/billing.py:236,537,612,622` |

### Compliance and data-subject rights (GDPR v1/v2, SOC2/HIPAA status, contracts, consent, DPDP) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 210 | medium | open | Still no INSERT/UPDATE of compliance_certifications or tenant_settings anywhere in app/ (only migrations and SELECTs in compliance_v2.py), so SOC2/HIPAA certification/retention controls pass only via manual SQL. | `app/enterprise/compliance_v2.py:432-453` |
| 211 | medium | open | compliance_v2.py has no commits since baseline; helper queries still 'except Exception' inside one session with no begin_nested savepoint, so one failing query aborts the transaction and later controls read as fail. | `app/enterprise/compliance_v2.py:326,341,361,379,393,411,429,452` |
| 212 | medium | open | _export_requests is still a process-local dict and retention_sweep reports only this replica's cache; status/download read DB first (:515-528), so only the sweep/no-DB path is per-replica. | `app/enterprise/compliance.py:77,335,578-590` |
| 213 | low | open | GET /enterprise/compliance/export still runs the whole export and persists a compliance_requests row (compliance.py:334); it now just also returns error/failed_sections. | `app/api/enterprise.py:128-141` |
| 214 | low | open | get_data_residency still hardcodes us-east-1/eu-west-1; /compliance/regions still compares residency.get('region') vs key primary_region (app/api/enterprise.py:210-219); retention_sweep (compliance.py:578-590) still counts only the in-memory dict and deletes nothing. | `app/enterprise/compliance.py:601-613` |
| 215 | low | open | ComplianceChecker(db_factory=None) is still published on app.state (main.py:3179) and only re-wired when the DB lifespan succeeds (:2124); GET /enterprise/compliance/{fw} checks only checker is None (app/api/enterprise.py:2137-2139), so without DB check_hipaa calls None() (compliance_v2.py:89) -> 500. | `app/main.py:855,3179` |

### Domain solutions catalog (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 216 | medium | open | solutions.py unchanged: POST /solutions/{slug}/install creates nothing and returns 200 status 'planned' despite the 'Atomically install' docstring. | `app/api/solutions.py:33-56` |
| 217 | low | open | InstallRequest still takes tenant_id from the body and echoes it; no write happens, so no cross-tenant effect. | `app/api/solutions.py:29-30,48` |

### Enterprise SSO and provisioning (SAML 2.0, SCIM 2.0) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 218 | medium | open | scim_router still exposes only /Users routes; no /Groups, /ServiceProviderConfig, /Schemas or /ResourceTypes in app/. | `app/api/enterprise.py:2718-2756` |
| 219 | low | open | With no active scim_configs row the handler still defaults allow_user_create/update/group_sync to True; DB read failure fails closed 503 (:2705-2708). | `app/api/enterprise.py:2697-2704` |
| 220 | low | partial | The new ACS path no longer echoes exception text (generic 401 'Invalid SAML response', :2586), but metadata, configure, login and SCIM token provisioning still return f'...: {exc}' in 500 details. | `app/api/enterprise.py:2348,2410,2458,2794` |
| 221 | low | open | POST /enterprise/saml/test is still _require_tenant only (no _require_admin) and still echoes 'Invalid XML: {exc}' (:2651) and 'Connection failed: {exc}' (:2659); outbound fetch remains SSRF-pinned. | `app/api/enterprise.py:2610-2662` |
| 222 | low | open | list_users still clamps count only from below (max(count,0)) and applies .limit(count), so a huge count returns the whole tenant user table. | `app/auth/scim_handler.py:249,266` |

### GST tax invoicing (India) (7)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 223 | medium | open | seller_gstin still defaults to placeholder '27AAAAA0000A1Z5' with no production check; used at app/api/gst_billing.py:88. | `app/core/config.py:706` |
| 224 | medium | open | Invoice number is still 22 chars (>16 Rule 46 limit) and FY uses now.year (Jan-Mar mislabelled). | `app/api/gst_billing.py:75-76` |
| 225 | low | open | gst_billing.py unchanged: invoice number still ends in uuid4().hex[:6]; no sequential per-FY series. | `app/api/gst_billing.py:74-76` |
| 226 | low | open | hsn_lookup is still a hardcoded 3-entry dict. | `app/api/gst_billing.py:198-208` |
| 227 | low | open | generate_gst_invoice still takes an arbitrary amount_inr with no payment/order linkage. | `app/api/gst_billing.py:79-162` |
| 228 | low | open | CGST/SGST still each round(gst_total/2,2); odd-paisa totals mismatch by 0.01. | `app/api/gst_billing.py:96-97` |
| 229 | low | open | GET /billing/gst/invoices still has unbounded limit and returns [] when db_session_factory is missing instead of 503. | `app/api/gst_billing.py:166-171` |

### Goal/tool/cost/agent/eval analytics (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 230 | medium | partial | aggregator.py unchanged since baseline: agent filter/duration/cost are real, but the query still has LIMIT 10000 and total=len(goals), silently capping totals/success rate/cost for large tenants. | `app/analytics/aggregator.py:140,271` |

### Health, readiness, public status page and SLA (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 231 | medium | partial | Pools still provide only postgres/redis health checks; celery stays UNKNOWN and LLM/embedder health is configuration-judged (unchanged since baseline). | `app/core/pools.py:129-133` |
| 232 | low | open | sla.py unchanged: static per-plan table, no uptime measurement or credit computation. | `app/api/sla.py:13-64` |
| 233 | low | open | Every goal submit still calls collect_dependency_health with fresh probes and no cache (health_probe.py unchanged). | `app/services/goal_service.py:2273-2275` |

### Observability API and telemetry pipeline (logs, metrics, traces) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 234 | medium | open | log_store.set_redis is still only called in the API lifespan; nothing in app/scaling wires it, so worker record_nowait (app/observability/log_store.py:104-124) falls to _append_memory and worker logs never reach /observability/logs. | `app/main.py:2404-2406` |
| 235 | medium | open | GET /observability/goals/{id}/trace still 501s whenever a DB or task queue is configured; observability.py has no commits since baseline. | `app/api/observability.py:450-475` |
| 236 | low | open | No production importer of slo_tracker/AlertRouter anywhere in app/ (only a graphify cache file matches). | `app/observability/slo_tracker.py` |

### Red-team adversarial testing (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 237 | medium | open | POST /enterprise/red-team still calls RedTeamRunner.run, which only runs GuardrailChecker.check_goal regex per payload (app/enterprise/red_team.py:86); tenant guardrail/policy config is not exercised. | `app/api/enterprise.py:364-380` |
| 238 | medium | open | BehavioralRedTeamRunner still has no importer in app/. | `app/enterprise/red_team.py:118` |
| 239 | medium | open | self._reports is never evicted; every POST /enterprise/red-team grows API memory. | `app/enterprise/red_team.py:111` |
| 240 | low | open | red_team_corpus is still not imported anywhere in app/ (tests only). | `app/enterprise/red_team_corpus.py` |
| 241 | low | open | Reports still live in per-process self._reports; get_report is unrouted and ignores tenant_ctx. | `app/enterprise/red_team.py:74,111-115` |

### Simulation sandbox (mock-tool runs) and Lab (8)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 242 | medium | open | No-LLM streaming path still emits a keyword stub plan ending in simulation_complete, distinguishable only by used_real_llm=False. | `app/enterprise/simulation.py:437-470` |
| 243 | medium | open | run_streaming still builds AgentGraph without cost_controller/policy_engine/audit_log (start() passes them at :190-200), so streamed simulations bypass tenant budget and policy. | `app/enterprise/simulation.py:484-491` |
| 244 | medium | open | start() still falls back to _stub_simulation on any pipeline exception, which sets 'completed' / 'success (simulated)' and used_real_llm = provider is not None. | `app/enterprise/simulation.py:265-273,378-401` |
| 245 | low | open | available-tools still wraps discover_all_tools in 'except Exception: pass' and answers 200 {tools: [], total: 0}. | `app/api/enterprise.py:250-275` |
| 246 | low | open | simulation.py has no commits since baseline; cost is still len(steps)*0.001 placeholders. | `app/enterprise/simulation.py:242,253,385,392,461,467` |
| 247 | low | open | stream_simulation still resolves agent_id with sync agent_store.get inside 'except Exception: pass', and the result is unused by run_streaming. | `app/api/enterprise.py:318-327` |
| 248 | low | open | run_streaming accepts agent_override but never reads it. | `app/enterprise/simulation.py:414` |
| 249 | low | open | SimulationRequest.goal is still a bare 'goal: str' with no max_length (stream model caps at 10000, :300). | `app/api/enterprise.py:225-227` |

### Cost analytics, budgets and per-goal cost breakdown (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 250 | low | open | Still an integration-marked real-Postgres test; not run in this static audit. | `tests/costs/test_cost_ledger_rls.py` |
| 251 | low | open | Any DB error is still logged (get_metrics_db_failed) and answered from this replica's in-memory self._goals instead of 503. | `app/services/goal_service.py:5126-5130` |
| 252 | low | open | charge_llm_call still re-targets goal_id to context['_budget_goal_id'] for both budget and ledger, so sub-goal cost breakdowns are empty. | `app/agent/nodes/llm_cost.py:56-57` |
| 253 | low | open | get_goal exceptions still map to owned=False -> 404 instead of 503. | `app/observability/cost_breakdown_api.py:26-33` |

### Usage metering (UsageService) (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 254 | low | open | usage_records still has only mocked tests (test_usage_service.py, test_usage_metering_hooks.py); no real-Postgres insert/rollup test. | `tests/services/test_usage_service.py` |
| 255 | low | open | record_tool_call still calls record() with no record_id (uuid4 id at :69), so redelivery re-meters; the worker builds a throwaway UsageService per call (app/scaling/tasks.py:733-747), so a failed flush re-buffers (:276) into a discarded instance. | `app/services/usage_service.py:124-143` |
| 256 | low | open | meter_goal_completion still records 0 tokens/$0 when aget_breakdown raises, under the deterministic goal record id with ON CONFLICT DO NOTHING; no retry. | `app/services/usage_metering.py:62-75` |

### agentverse CLI (app/cli/main.py) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 257 | low | open | _stream_goal still one client.stream with no reconnect/Last-Event-ID; goal_cancelled not handled as terminal. | `app/cli/main.py:144-178` |
| 258 | low | partial | Maintenance commands (vault-rotate, tenant-key-compact, mfa-rotate, connectors-backfill) exist, but still no migration or tenant-admin commands. | `app/cli/main.py:459-653` |
| 259 | low | open | raise_for_status still unhandled; no typer handler for httpx.HTTPStatusError, so users see tracebacks. | `app/cli/main.py:60,71,115,133,196,205,226` |
| 260 | low | open | _stream_goal still never checks resp.status_code and exits 0 when the stream ends without a terminal event. | `app/cli/main.py:144-178` |

## Critic group (cross-cutting) — 95 pending

### Agent Runtime 2.0 API (/agent-runtime) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 261 | medium | open | update_trace is only called in _dispatch_event (:3172,3281); the Celery->SSE bridge sets record.status at :1057 without update_trace and app/scaling/tasks.py has no agent_runtime reference, so worker-run goal traces never get outcome/duration. | `app/services/goal_service.py:3172,3281,1057` |
| 262 | medium | open | file unchanged since 05991cf3c (no commits); no runtime path assigns total_cost_usd/total_tokens/role_calls/model_selections on AgentRunTrace. | `app/agent_runtime/models.py` |
| 263 | low | open | Both trace updates still end in bare 'except Exception: pass'. | `app/services/goal_service.py:3178-3179,3288-3289` |
| 264 | low | open | file unchanged since 05991cf3c (no commits); POST /plans and /traces accept any goal_id with no tenant goal lookup. | `app/api/agent_runtime.py:28-72,116-130` |
| 265 | low | open | file unchanged since 05991cf3c (no commits); /strategies is a static list. | `app/api/agent_runtime.py:153-190` |
| 266 | low | open | file unchanged since 05991cf3c (no commits); silent per-process LRU fallback on Redis errors; Redis TTL only. | `app/agent_runtime/store.py:121-147` |

### Agent definitions lifecycle (/agents CRUD, versions, snapshots, clone, export) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 267 | medium | open | _save_snapshot_to_db still catches every exception and only logs a warning (:58-61), so POST /agents/{id}/snapshot reports a snapshot that was never stored; _load_snapshots_from_db returns [] on error (:84-88), resetting version numbering. RV-02 (fb9434271) touched only routing_candidates. | `app/api/agents.py:33-61,64-88` |
| 268 | medium | open | rollback_agent still ignores update_async's bool and always returns 'rolled_back'. | `app/api/agents.py:1475-1476` |
| 269 | medium | open | get_async still falls back to the replica's in-memory _data on any DB error (warning then return self._data.get). | `app/api/agents.py:274-279` |
| 270 | medium | open | Version still len(existing)+1; no migration since base adds a unique (tenant_id, agent_id, version) on agent_snapshots (only ix_agent_snapshots_tenant_agent, 0025:25). | `app/api/agents.py:1438` |
| 271 | medium | open | list_async still swallows DB errors and returns this replica's cached subset; only routing_candidates now raises (RV-02). | `app/api/agents.py:322-330` |
| 272 | medium | open | clone_agent still checks plan limit against store.list_all() (replica cache) instead of count_async (:417). | `app/api/agents.py:1577-1578` |

### Builder (site/app generation) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 273 | medium | open | file unchanged since 05991cf3c (no commits); GET /builder/projects/{id}, /preview/{ws}, /assets/... still raise 501, so a generated site cannot be served. audit/server-errors@6c56310b0 (2026-09-28) only touches submit errors. | `app/api/builder.py:118-150` |
| 274 | medium | open | file unchanged since 05991cf3c (no commits); random project_id/workspace_id stored only in the goal execution_context; no store maps them. | `app/api/builder.py:56-57,80` |

### Data classification and redaction (app/data_classification) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 275 | medium | open | Only live DataClassifier use is few_shot_cot.py:17; other references are dead state_context.py:88 and strategy_registry.py:1463-1466 adapter strings. | `app/agent/patterns/few_shot_cot.py:17` |
| 276 | medium | open | redaction.py has no importer outside the package; runtime_flags.data_classification (default True) is never read in app/. | `app/data_classification/redaction.py; app/core/runtime_flags.py:31,72,98` |
| 277 | low | open | state_context still has no importer. | `app/state_runtime/state_context.py:88` |
| 278 | low | open | file unchanged since 05991cf3c (no commits); zero importers; data_classification_lte never evaluated. | `app/tenancy/domain_role_templates.py:14-21` |

### Dual-mode identity linking (app/identity) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 279 | medium | open | file unchanged since 05991cf3c (no commits); first-contact race: create_link ON CONFLICT DO NOTHING without RETURNING/re-read returns an orphan principal to the losing request. | `app/identity/service.py:81-98; app/identity/repository.py:66-82` |
| 280 | low | open | app/identity unchanged; rg '.link_identity(' in app/ -> none. | `app/identity/service.py:100-125` |
| 281 | low | open | Still no identity/cross-channel test under tests/e2e_full. | `` |
| 282 | low | open | Stale 'In-memory now; a Postgres-backed store swaps in...' comment still present although app/main.py:1624,1638 installs PostgresIdentityStore. | `app/bootstrap/routers.py:247-249` |

### Goal API sub-features not in inventory (ghost-run, batch, lineage, attempts, feedback, explain, traces) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 283 | medium | open | POST /goals/batch still returns uuid batch_id (:1063,1093); status splits it as comma-separated goal ids (:1108), the uuid resolves to one not_found (:1116) which counts as done (:1123), so all_complete=true immediately. | `app/api/goals.py:1063,1093,1108,1116,1123` |
| 284 | low | partial | Traces 404 on a missing goal (:1142) but return [] on any DB error (:1177-1178); lineage and attempts still skip the goal-existence check and swallow DB errors into root-only/[] (:1191-1196,1293,1309-1310). | `app/api/goals.py:1177-1178,1181-1196,1286-1310` |
| 285 | low | partial | file unchanged since 05991cf3c (no commits); calibration feedback persisted to Postgres under RLS; only the in-process _records buffer is replica-local (benign). | `app/intelligence/verifier_calibration.py:148-185; app/main.py:2069-2077` |
| 286 | low | open | No commits to goals tests since 05991cf3c; still no batch submit->status round trip or real lineage/attempts test. | `tests/api/test_goals_honest_responses.py:59` |
| 287 | low | open | Still 'float(r[3]) if r[3] else 0.5', so stored 0.0 confidence reads as 0.5. | `app/api/goals.py:1172` |
| 288 | low | open | BatchGoalRequest.max_parallel (:1054) unused; goals submitted sequentially in the loop at :1066. | `app/api/goals.py:1054,1066` |

### Insights API (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 289 | medium | open | file unchanged since 05991cf3c (no commits); similarity(goal_text,:goal) >= threshold scan; no migration since base adds a trigram index on goals (new gin_trgm_ops index is on episodic_memories). | `app/api/insights.py:116,131` |
| 290 | medium | open | file unchanged since 05991cf3c (no commits); _goal_cost_subquery unbounded over goal_cost_breakdowns; cross-tenant benchmarks aggregate uncached. | `app/api/insights.py:97-105,196,932-946` |
| 291 | low | open | Integration test (needs Docker) not run in this static pass; unverified. | `tests/integration/test_insights_postgres.py` |
| 292 | low | open | file unchanged since 05991cf3c (no commits); 'except Exception: events = []' returns a start-only graph with 200 on NotFound/DB outage. | `app/api/insights.py:377-380` |
| 293 | low | open | file unchanged since 05991cf3c (no commits); LLM-parsed days unbounded into timedelta -> OverflowError 500. | `app/api/insights.py:720,766,805` |

### Maintenance tasks not claimed by any area (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 294 | medium | open | scan_cost_anomalies unchanged (shifted): blocking r.keys('cost:daily:*') (:7958), list(set)[:50] (:7965), per-tenant 'except Exception: pass' (:7969-7970). | `app/scaling/tasks.py:7958,7965,7969-7970` |
| 295 | medium | open | Only counts detect_anomaly() results; nothing persisted/alerted; errors returned as {'error':...} so Celery records SUCCESS. | `app/scaling/tasks.py:7965-7968,7974-7975` |
| 296 | medium | partial | Column/RLS fix holds, but the UPDATE still only bumps updated_at (:8023) so nothing is concluded, and exceptions return {'status':'error'} (:8032-8033), recorded as Celery SUCCESS. | `app/scaling/tasks.py:8021-8028,8032-8033` |
| 297 | low | open | Returns tenants_scanned=len(tenant_ids) though at most 50 are scanned. | `app/scaling/tasks.py:7973` |
| 298 | low | open | embed_marketplace_templates still only SELECT COUNT(*) WHERE embedding IS NULL and returns 'ok'; nothing embedded. | `app/scaling/tasks.py:7982-8001` |
| 299 | low | open | Redis client closed only on the success path (:7972); outer except (:7974) has no finally/aclose. | `app/scaling/tasks.py:7952,7972,7974` |

### Memory 2.0 API (/memory-v2) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 300 | medium | open | file unchanged since 05991cf3c (no commits); _conflicts is a module dict; per-replica and lost on restart. | `app/api/memory_v2.py:65,320-343,671` |
| 301 | medium | open | file unchanged since 05991cf3c (no commits); memory_v2 INSERT/UPDATE long_term_memory without app.memory.screening; long_term.py also unchanged, so unscreened v2 content reaches recall. | `app/api/memory_v2.py:94-122; app/memory/long_term.py:880-884` |
| 302 | medium | open | file unchanged since 05991cf3c (no commits); DELETE only rewrites lifecycle_state in the JSON content; LTM recall/listing filter neither memory_type nor lifecycle, so 'deleted' v2 memories are retained and recalled. | `app/api/memory_v2.py:557-585; app/memory/long_term.py:880-884,304-306` |
| 303 | low | open | file unchanged since 05991cf3c (no commits); no cap/TTL on _conflicts. | `app/api/memory_v2.py:671` |
| 304 | low | open | file unchanged since 05991cf3c (no commits); each POST rescans up to 5000 rows with an O(N) overlap scan. | `app/api/memory_v2.py:69,255-262,272` |
| 305 | low | open | file unchanged since 05991cf3c (no commits); total=len(page). | `app/api/memory_v2.py:291-316` |

### Minor routers missing from inventory (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 306 | medium | open | get_data_residency still hardcodes primary_region 'us-east-1' / backup_region 'eu-west-1' for every tenant (NATIVE-01/04 commits only shifted lines). | `app/enterprise/compliance.py:601-608` |
| 307 | low | open | /compliance/regions still compares residency.get('region') (key absent) so us-east-1 is listed twice. | `app/api/enterprise.py:210-219` |
| 308 | low | open | file unchanged since 05991cf3c (no commits); /v1/health always ok; /v1/info static. | `app/api/v1/router.py:8-19` |

### Multimodal asset ingestion (/multimodal) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 309 | medium | open | file unchanged since 05991cf3c (no commits); no-vision path still stores '[Image content - vision provider not configured]' as a completed IMAGE span. Branch fix/fake-success-multimodal-export@7aaa19f42 fixes only PDFs (already on main as 87a685320) and keeps the image placeholder. | `app/multimodal/pipeline.py:468,116-127` |
| 310 | medium | open | file unchanged since 05991cf3c (no commits); every job incl. source_base64 cached in per-process _memory even with Redis; only delete() evicts. | `app/multimodal/job_store.py:93,100` |

### OCR document extraction (/ocr) (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 311 | medium | open | P1a-2/P1a-6 commits changed Tesseract preprocessing only; _llm_vision_ocr still returns ('',0.0,'llm_vision') with no provider (:431) or on any non-budget error (:469), and extract() joins pages regardless (:159-166), so /ocr/extract returns 200 with empty/partial text. | `app/ocr/engine.py:431,465-469,159-166` |
| 312 | low | open | file unchanged since 05991cf3c (no commits); batch per-item exception -> None with no per-item reason. | `app/api/ocr.py:344-346,294-298` |
| 313 | low | open | file unchanged since 05991cf3c (no commits); batch _extract_one ignores persist_to_kb/collection_id. | `app/api/ocr.py:319-341` |
| 314 | low | open | file unchanged since 05991cf3c (no commits); multipart read fully with no cap and base64-inflated; body_limit.py also unchanged and does not cover /ocr. | `app/api/ocr.py:184,194,199` |

### Platform admin (cross-tenant, X-Admin-Key) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 315 | medium | open | file unchanged since 05991cf3c (no commits); list/detail read in-process TenantService._tenants. | `app/api/admin.py:77,109` |
| 316 | medium | open | app.state.cost_controller is still the plain CostController (main.py:708,3094), which lacks get_budget_status (only RedisCostController, app/governance/cost.py:839); admin detail usage is always {} via the swallowing except. | `app/main.py:708,3094; app/api/admin.py:114-119` |
| 317 | low | open | Integration test not run (Docker); no integration test of list/detail. | `tests/integration/test_admin_usage_postgres.py` |
| 318 | low | open | Docstring still advertises POST .../keys/revoke (no such route) and 'NOT protected by the standard tenant middleware'; /admin absent from _BYPASS_PREFIXES. | `app/api/admin.py:4,10; app/tenancy/middleware.py:112` |
| 319 | low | open | file unchanged since 05991cf3c (no commits); whole-table goal aggregates on every /admin/usage call. | `app/api/admin.py:178-201` |

### Proactive outreach engine (app/proactive) (6)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 320 | medium | open | routers.py:216 still builds ProactiveEngine(deliver=_deliver, audit=_audit) with no count_provider/count_recorder, so the daily cap uses the per-replica self._sent dict (engine.py:72,80,88); app/proactive unchanged since 05991cf3c. | `app/bootstrap/routers.py:216; app/proactive/engine.py:72,80,88` |
| 321 | medium | open | No preferences_provider passed at routers.py:216, so engine.py:64 gives every principal default ProactivePreferences() (no stored opt-out/quiet hours). | `app/bootstrap/routers.py:216; app/proactive/engine.py:64` |
| 322 | medium | open | deliver_proactive still uses the in-memory self._principal_sessions (service.py:1021,1026) instead of _get_principal_session_id/chat_principal_sessions (:1750), and the channel push is under contextlib.suppress(Exception) (:1035-1036) while engine.handle reports delivered=True. | `app/chat/service.py:1021-1036` |
| 323 | medium | open | No attach_engine caller (app/main.py:1640,1776,2862; app/bootstrap/routers.py:275) passes channel_deliver, so _channel_deliver stays None (service.py:425) and channel-targeted signals land only in the web thread while POST /v1/proactive/signals returns delivered=true. | `app/chat/service.py:425,776-777,1034` |
| 324 | low | open | SignalBus still in-process only; only importer is app/proactive/__init__.py:16. Limited impact (router calls engine.handle directly). | `app/proactive/signals.py:49` |
| 325 | low | open | Cap key still (principal_id, day) and _principal_sessions keyed by principal_id only; an explicit principal_id shared across tenants shares one per-replica cap/session map. | `app/proactive/engine.py:72,88; app/chat/service.py:419,1021` |

### Sandbox goal submission (/sandbox) (5)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 326 | medium | open | file unchanged since 05991cf3c (no commits); any AgentGraph exception (incl. budget refusal) silently reruns the keyword stub which reports completed/'success (simulated)'; sandbox reports executed=True. | `app/enterprise/simulation.py:265-273,378,390,397` |
| 327 | low | open | file unchanged since 05991cf3c (no commits); static 'Cost is simulated'/'deterministic mock data' limitations even when a real LLM is used. | `app/api/sandbox.py:87-102` |
| 328 | low | open | file unchanged since 05991cf3c (no commits); separate surface from /enterprise/simulation and /lab/run. | `app/api/sandbox.py` |
| 329 | low | open | file unchanged since 05991cf3c (no commits); cost fabricated as len(steps)*0.001. | `app/enterprise/simulation.py:242,253,385,392` |
| 330 | low | open | file unchanged since 05991cf3c (no commits); used_real_llm = provider is not None ignores the local failure flag. | `app/enterprise/simulation.py:386,401` |

### Canonical routing runtime (app/routing_runtime) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 331 | low | open | canonical_*_router and routing_decision_store only assigned in main.py, never read elsewhere in app/. | `app/main.py:1508-1516,2927-2929` |
| 332 | low | open | file unchanged since 05991cf3c (no commits); tool_router.py/optimizer.py have zero importers. | `app/routing_runtime/tool_router.py` |

### Data lifecycle and retention maintenance (app/lifecycle + beat tasks) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 333 | low | open | archive_policy, deletion_receipt, export_policy, legal_hold_policy, retention_policy still have no importer outside app/lifecycle; only deletion_orchestrator is used (main.py:2603,3099; tasks.py:8199; api/dpdp.py:133). | `app/lifecycle/archive_policy.py` |

### Frontend feature folders with no inventory entry (3)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 334 | low | open | Unchanged; never asserts backend /status payload. | `agent-verse-frontend/e2e/status-page.spec.ts:1-29` |
| 335 | low | partial | No frontend e2e commits since 05991cf3c; opt-in live-stack suite covers some pages but payload-level contract checks for uninventoried folders are still not systematic. | `agent-verse-frontend/e2e/real-world/real-world.spec.ts` |
| 336 | low | open | Stub stream still emits hardcoded cost_increment 0.001; real path reads context 'last_step_cost' (executor_mixin.py:1192) which nothing in app/ sets, so always 0.0. | `app/enterprise/simulation.py:461,467; app/agent/nodes/executor_mixin.py:1192` |

### Goal replay and timeline (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 337 | low | open | file unchanged since 05991cf3c (no commits); goal_events/goal_steps/decision_traces fetched with no LIMIT/pagination. | `app/api/replay.py:65-95` |

### Goal templates (/templates) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 338 | low | open | file unchanged since 05991cf3c (no commits); content-pack load failure logged at ERROR yielding no built-ins; seeding tracked per process in _seeded_tenants. | `app/api/templates.py:97-99,212,247-249,315-318` |
| 339 | low | open | file unchanged since 05991cf3c (no commits); _list_db unbounded; only list maps DB errors to 503. | `app/api/templates.py:430-440,578-580` |

### Golden datasets (eval promotion) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 340 | low | open | file unchanged since 05991cf3c (no commits); honest 501; golden_datasets tables unused. | `app/api/golden_datasets.py:1-30` |

### Login session management (/auth/sessions) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 341 | low | open | file unchanged since 05991cf3c (no commits); all three endpoints honest 501. (SAML-01 added user sessions elsewhere but these /auth/sessions endpoints remain 501.) | `app/api/sessions.py:28-48` |

### Orphan top-level runtime packages (zero importers outside their own package) (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 342 | low | open | No runtime importer for provenance, explainability_runtime, recovery, capabilities, plan_runtime, sandbox_runtime, collaboration_runtime, ai_ops outside their packages (adapter strings only). | `app/orchestration/strategy_registry.py:1442,1454,1478` |

### Public status page router is never mounted (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 343 | low | partial | file unchanged since 05991cf3c (no commits); per-check timeout exists; still no result cache, each anonymous GET /status runs every check. | `app/observability/health.py:33,43-51; app/api/public_status.py:29-39` |
| 344 | low | open | No e2e commits since 05991cf3c; spec still never asserts the /status payload. | `agent-verse-frontend/e2e/status-page.spec.ts:1-29` |

### Skills: composable instruction packs and Skills Runtime (1)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 345 | low | partial | Postgres app-role tests still exist, but tests/e2e_full still has no skills test (ls tests/e2e_full \| rg skill -> none). | `tests/api/test_skills_runtime_disabled_pg.py:42` |

### Training data export (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 346 | low | open | OPS-37 rewrote the module but GET /intelligence/export-training-data/preview still has no registered scope (no 'intelligence' entry in app/auth), and unregistered GETs pass for any role (scope_enforcement.py:568-585). | `app/api/training_export.py:113; app/auth/scope_enforcement.py:568-585` |
| 347 | low | open | After OPS-37 (03a897c8c) the DB-path stream still hardcodes 'model': 'unknown' (stream.py:135). | `app/training_export/stream.py:135` |
| 348 | low | open | Integration test (needs Docker) not run; unverified. | `tests/integration/test_billing_dsr_rls_integration.py` |
| 349 | low | open | min_score still accepts ge=0.0 (:115) while both memory (:89-97) and DB (stream.py:158,175 'score < 0.85' -> '0.80-0.85') histograms put every score below 0.85 in the 0.80-0.85 bucket. | `app/api/training_export.py:115,89-103; app/training_export/stream.py:158,175` |

### Unwired gateway channel adapters and webhook delivery (4)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 350 | low | open | file unchanged since 05991cf3c (no commits); EmailChannelAdapter/VoiceWebhookAdapter have no importer outside app/gateway/channels. | `app/gateway/channels/email.py:36` |
| 351 | low | open | OutboundWebhookService still has zero importers, so WebhookDeliverySystem is dead. | `app/gateway/webhook_delivery.py:89,290; app/services/webhook_service.py:35` |
| 352 | low | partial | file unchanged since 05991cf3c (no commits); config/status endpoints still honest 501; bindings live in /channels/bindings but per-channel status is not implemented. | `app/gateway/router.py:1084-1125` |
| 353 | low | open | file unchanged since 05991cf3c (no commits); _download_command_file buffers r.content with no size cap. | `app/gateway/router.py:298-301` |

### Unwired trigger-family implementations (TriggerType families F/G/H/I) (2)

| # | Sev | Status | Pending item | Where |
|---|---|---|---|---|
| 354 | low | open | file unchanged since 05991cf3c (no commits); data/monitoring/advanced and iot geofence/sensor unimported; mqtt consumer built with mqtt_client=None (:274), always skipped. | `app/triggers/supervisor.py:272-283` |
| 355 | low | open | file unchanged since 05991cf3c (no commits); 10 types still UNSUPPORTED (honestly rejected at creation, validation.py:127-142). | `app/triggers/dispatch_map.py:126-137` |

## MongoDB connector & sources — 49 items (separate list; none fixed on main)

Status per half-item: {'OPEN': 14, 'FIXED_ON_BRANCH': 24, 'PARTIAL': 16}. FIXED_ON_BRANCH = committed on fix/mongo-mcp, fix/mongo-fe or fix/mongo-ingestion, not merged yet.

| # | Item | Side | Status | Sev | Detail | Where |
|---|---|---|---|---|---|---|
| 1 | A1 | MCP connector | open | high | AuthType still has no CONNECTION_STRING on main/fix-mongo-mcp/fix-mongo-fe (registry.py:34-44; RegisterConnectorRequest.auth_type: AuthType connectors.py:210). Probe on fix/mongo-mcp: POST auth_type=connection_string -> 422 enum error. fix/mongo-fe now always sends auth_type 'connection_string' for MongoDB, so every UI registration/edit of MongoDB still 422s end to end. | `main (no branch touches it)` |
| 2 | A3 | MCP connector | open | medium | Catalog MongoDB spec still has no builtin_server_id (unchanged on both branches); probe on fix/mongo-mcp: catalog has_builtin=False builtin_server_id=''; POST name=orders-db without type -> builtin_type ''. FE ConnectorsCatalogPage.tsx:288 still sends type only when has_builtin. The FE fixture pretends has_builtin=true ('fix/mongo-mcp contract') but that branch never implemented it. | `main catalog.py:394-400` |
| 3 | A8 | MCP connector | open | low | fix/mongo-mcp adds display_url but _public_connector still sets upstream_url from the catalog default; probe: a builtin MongoDB connection on 8.8.4.4 returns upstream_url 'mongodb://localhost:27017'. FE (table and ConnectorDetailPage displayUrl) prefers upstream_url and never reads display_url, so the UI still shows localhost. | `main connectors.py:401-415,442-443` |
| 4 | C2 | MCP connector | open | low | Still a new MongoClient per call on fix/mongo-mcp (_call_sync, mongodb_server.py:644, closed in finally). | `main mongodb_server.py:492` |
| 5 | C3 | MCP connector | open | low | fix/mongo-mcp still wraps private pymongo.pool_shared._create_connection; pyproject still 'pymongo>=4.10' with no upper bound; the only canary still calls the private function directly. | `main mongodb_server.py:431-454; pyproject.toml:94` |
| 6 | MDB-10 | MCP connector | open | low | Unchanged on fix/mongo-mcp (same private-function patch, no version ceiling, no behavioural canary through a real MongoClient). | `main mongodb_server.py:431-454; pyproject.toml:94` |
| 7 | MDB-20 | MCP connector | open | low | fix/mongo-mcp call_tool still returns {'error': str(exc)} (mongodb_server.py:437) — full TopologyDescription with member hosts reaches the API and the agent/LLM; only the browser sanitises it (fe c36763c36). | `main mongodb_server.py:334` |
| 8 | TG-07 | Sources/ingestion | open | medium | No update/delete re-sync test and no fix: default cursor is _id $gt (mongodb_connector.py:484-486) so an updated document is never re-read, and MongoDBConnector does not override list_live_doc_ids/iter_live_doc_ids (base_connector.py:378 default None) so deleted documents are never reconciled. Real-MongoDB probe: after update_one(Ada) + delete_one(Linus) the re-sync read 0 docs, Paris never ingested, list_live_doc_ids -> None. | `main` |
| 9 | TG-08 | MCP connector | open | medium | MCP half: still only the test that calls pool_shared._create_connection directly; _fake_mongod.py on fix/mongo-mcp is used only for the stall test, no member-advertised-after-discovery test through a real MongoClient Pool/monitor. | `main tests/mcp/test_mongodb_builtin_credentials.py:244` |
| 10 | TG-09 | MCP connector | open | low | MCP half: no Decimal128/Binary/Regex/Int64/null/large-array test; _serialize still only stringifies _id on fix/mongo-mcp. | `main mongodb_server.py:337-341` |
| 11 | TG-09 | Sources/ingestion | open | low | No BSON-type test on the ingestion path (only ObjectId/datetime cursor round-trip). _flatten_doc (mongodb_connector.py:415-432) drops array items past 20 and anything nested deeper than 5 silently, and renders Binary as a Python bytes repr (b'\x00\x01secret') and Regex as Regex('^a.*', re.IGNORECASE). Probe: tags[25] -> 20 kept; deep.a.b.c.d.e.f dropped. No branch touches it. | `main` |
| 12 | TG-12 | Sources/ingestion | open | medium | The sync lock is process-local: every IngestionJobTracker is built without redis (app/main.py:801, lifespan only adds _db at :1797-1800; app/ingestion/scheduler.py:132 builds a fresh tracker per task), so acquire_lock (job_tracker.py:62-92) uses a per-instance dict. Probe: two worker trackers both acquire the lock for one source concurrently. No DB unique constraint on running jobs (migration 0109). No concurrency test on any branch. | `main` |
| 13 | TG-13 | Sources/ingestion | open | low | doc_id = uuid5(NAMESPACE_URL, mongodb://{display_host}/{db}/{coll}/{_id}) (mongodb_connector.py:568-570; display_host from the URI seed list :225-226). Probe: adding a second seed host to the same URI changes every doc_id (equal? False), so a URI/host edit (now possible from the UI via B3/B4) followed by a full re-sync duplicates every document. No stable_doc_id use, no test, no branch change. | `main` |
| 14 | TG-15 | Sources/ingestion | open | low | @register('mongodb', feature_flag='ingestion_connector_mongodb_enabled') (mongodb_connector.py:523) names a field Settings never declares (config.py has only ingestion_connector_duckdb_enabled; extra='ignore' at :24), and get_connector reads getattr(settings, flag, True) (connector_registry.py:101-102), so INGESTION_CONNECTOR_MONGODB_ENABLED=false is silently ignored - not a kill switch. No test; no branch adds the field. | `main` |
| 15 | A10 | MCP connector | partial | low | New fixture uses auth_type connection_string and the real auth field, but sets has_builtin:true/builtin_server_id:'builtin-mongodb' and upstream_url 'cluster0.example.mongodb.net', none of which the real backend returns (catalog has_builtin False, upstream_url localhost). POST/PUT are mocked 201/200, so the real 422 (A1) and A3/A8 still go unnoticed. | `fix/mongo-fe@eff5f310b` |
| 16 | A4 | MCP connector | partial | medium | MONGODB_AUTH_FIELDS adds uri/username/password/auth_source/auth_mechanism/database/tls/tls_ca_pem/tls_allow_invalid_certificates plus checkbox/file/select/textarea renderers. Remaining: it offers 'Allow invalid certificates' which fix/mongo-mcp (MDB-07) refuses at call time, has no tls_client_cert/tls_client_private_key fields nor MONGODB-X509 option (backend added them in 226d1fc25), and all of it is behind auth_type connection_string (A1 422). | `fix/mongo-fe@99df48e59` |
| 17 | A5 | MCP connector | partial | medium | Backend unchanged on both branches: _extract_credentials_from_server keeps only str values (client.py:180), so API callers sending tls:true (bool) still silently lose it. The new UI checkbox sends the string 'true', so UI-entered TLS now reaches the handler. | `fix/mongo-fe@99df48e59 (workaround); main client.py:180` |
| 18 | A6 | MCP connector | partial | medium | UI half fixed: table testMutation has onError and renders a failed result. Backend half open: a non-builtin MongoDB connection (A3 case) still falls to the generic HTTP probe -> 400 'SSRF protection: disallowed URL' (probe on fix/mongo-mcp, connectors.py test_connector step 3). | `fix/mongo-fe@cc0586b16; backend main connectors.py (generic check)` |
| 19 | C1 | Sources/ingestion | partial | medium | _settings now sets socketTimeoutMS (INGESTION_MONGODB_SOCKET_TIMEOUT_MS, default 60s) and maxTimeMS on ping/listCollections/find, kwargs override URI socketTimeoutMS=0, tenant timeout_ms may only lower (mongodb_connector.py _driver_bounds/_settings/_probe/_fetch_page). BUT a tenant URI option timeoutMS (CSOT) is not refused and overrides socketTimeoutMS: probe with the branch's FakeMongod(stall=True) -> no option 2.3s NetworkTimeout; ?timeoutMS=12000 12.0s; ?timeoutMS=0 still blocked at the 40s probe limit (unbounded). No Celery soft_time_limit on ingestion.sync_source either. | `fix/mongo-ingestion@783be46f1` |
| 20 | C5 | MCP connector | partial | medium | Backend: _tls_files writes tls_ca_pem and tls_client_cert+key (+password) to 0600 temp files, MONGODB-X509 allowed only with a client cert; TLS container test covers CA, mTLS and X.509. UI: CA field added, but no client cert/key fields and no MONGODB-X509 mechanism option, so mTLS/X.509 cannot be configured from the Connectors page. | `fix/mongo-mcp@226d1fc25 (backend); fix/mongo-fe@99df48e59 (CA field)` |
| 21 | C6 | MCP connector | partial | medium | MCP half: assert_tls_not_weakened refuses tlsInsecure/tlsAllowInvalid*/tlsDisable*/ssl_cert_reqs, tls=false and the config fields, on the URI and the SRV-expanded DSN. Bypass: options joined with ';' (pymongo's alternate delimiter) are not seen by parse_qsl -> '?appName=a;tlsInsecure=true' passes the policy and the driver receives tlsInsecure/tlsAllowInvalidCertificates=True (probe through call_tool). Also only checked at call time, not at register/update. | `fix/mongo-mcp@95708faa8` |
| 22 | C6 | Sources/ingestion | partial | medium | Ingestion _settings now calls app/net/mongodb_policy.assert_tls_not_weakened(uri, cc): refuses tlsInsecure/tlsAllowInvalid*/tlsDisable*/ssl* aliases/ssl_cert_reqs, tls=false\|ssl=false (unless dev-only MONGODB_ALLOW_NON_TLS outside production) and the tls_allow_invalid_certificates/hostnames/tls_insecure fields; the tlsAllowInvalidCertificates kwarg is gone. BUT the policy reads options with urllib parse_qsl ('&' only) while pymongo also accepts ';' as the separator: mongodb://h/?appname=x;tlsInsecure=true is ACCEPTED by _settings and pymongo parses tlsInsecure/tlsAllowInvalidCertificates/tlsAllowInvalidHostnames=True (probe). Also only an explicit tls=false is refused; a URI with no tls option still connects in plaintext. Refusal happens at validate/health/sync, not at save. | `fix/mongo-mcp@95708faa8 (ingestion half)` |
| 23 | MDB-05 | MCP connector | partial | medium | Backend now supports CA + client certificate + X.509 (see C5), and the insecure workaround is refused. But the FE branch still offers only the 'Allow invalid certificates' workaround (now refused) and no client-cert fields, so a UI user needing mTLS has no working path. | `fix/mongo-mcp@226d1fc25` |
| 24 | MDB-07 | MCP connector | partial | medium | Same as C6 (MCP half): the obvious '?tlsInsecure=true' is refused, but '?appName=x;tlsInsecure=true' bypasses the check (';' delimiter) and the driver runs with verification off. Same parser gap also lets ';tlsCAFile=/path' and ';authMechanism=MONGODB-AWS' past _check_uri_options (pre-existing on main mongodb_server.py:270-278). | `fix/mongo-mcp@95708faa8` |
| 25 | MDB-07 | Sources/ingestion | partial | medium | Same as C6: ?tlsInsecure=true is refused on the ingestion path, but ?appname=x;tlsInsecure=true (';' separator) bypasses assert_tls_not_weakened and pymongo disables certificate and hostname checks. The same parse_qsl approach is used on the MCP path (mongodb_server.py:272 on fix/mongo-mcp), so the MCP half is likely affected too (not my scope; flag for the MCP auditor). | `fix/mongo-mcp@95708faa8 (ingestion half)` |
| 26 | MDB-12 | Sources/ingestion | partial | medium | Same code as C1: a stalled server now fails the sync/health within ~2x socket timeout as a failed job (test_stalled_server_fails_the_sync_with_an_error), but a tenant can still hang a worker / the GET /sources/{id}/health request indefinitely with ?timeoutMS=0 in the URI (CSOT overrides socketTimeoutMS; timeoutMS is not in _FORBIDDEN_URI_OPTIONS and kwargs do not pin it). | `fix/mongo-ingestion@783be46f1` |
| 27 | TG-05 | Sources/ingestion | partial | medium | tests/net/test_mongodb_tls_policy.py test_ingestion_refuses_tls_weakening_uri/_fields and test_ingestion_never_sets_an_invalid_certificate_kwarg parametrise every option and field on _settings - but only in the '&' form; no ';'-separated or mixed-case-duplicate case, which is exactly the bypass that works. No integration test with a requireTLS server on the ingestion side. | `fix/mongo-mcp@95708faa8 (ingestion half)` |
| 28 | TG-06 | MCP connector | partial | medium | Register+edit tests with connection_string exist (ConnectorsMongoCatalogFlow.test.tsx) but against a fixture with has_builtin/builtin_server_id the backend does not send, and mocked 201/200 responses; they cannot catch the A1 422 or A3. | `fix/mongo-fe@eff5f310b` |
| 29 | TG-11 | MCP connector | partial | low | Unchanged: cross-tenant tests exist for /test (jira) and tool discovery, none for tenant B calling tenant A's MongoDB server_id on GET /connectors/{id}/tools or MCPClient.call_tool. Behaviour was already correct (earlier probe). | `main` |
| 30 | TG-14 | Sources/ingestion | partial | low | Behaviour is correct on main: the connector-agnostic worker loop sends a document whose pipeline step raises to tracker.add_to_dlq (scheduler.py USR-4 hunk) and the job is failed/partial with docs_failed counted (USR-1). Real-MongoDB probe of a manual sync (POST /sources/{id}/sync -> _sync_source_async) with one exploding document: docs_failed=1 and one DLQ entry carrying the raw MongoDB doc. But the only committed tests are generic (tests/ingestion/test_unhandled_doc_error_dlq.py uses an http source; test_sync_failure_never_completed.py's mongodb case is a connection failure, not a per-document DLQ entry) - no MongoDB test. | `main (aaa391223 USR-4, 79c90f4c2 USR-1)` |
| 31 | A2 | MCP connector | fixed_on_branch | medium | connection_string entry added to AUTH_TYPE_CONFIGS; unknown stored type kept selected (AuthTypeSelector extra <option>); type switch keeps shared keys and confirms before dropping (requestAuthTypeChange/applyAuthType). UI-only: unusable in practice until A1 (no connection_string connector can be created). | `fix/mongo-fe@0fa7199de` |
| 32 | A7 | MCP connector | fixed_on_branch | high | FE: one masked URI input under auth_config, top-level url 'builtin://', DSN never a link (isHttpUrl), table/detail mask userinfo. MCP: seal_connector_dsns keeps one sealed copy (vault ref), url=builtin://, display_url masked; API test asserts one ref and no userinfo in responses/Redis. With fe alone the main backend still returns auth_config.uri in clear; register path still blocked by A1. | `fix/mongo-fe@165e34841 + fix/mongo-mcp@24044b98d (both needed)` |
| 33 | A9 | MCP connector | fixed_on_branch | low | Table shows 4xx as a failed test; friendlyConnectionError maps driver errors to short reasons, details toggle sanitises URIs/hosts/IPs; detail page toasts use it. The API itself still returns raw driver text (see MDB-20). | `fix/mongo-fe@cc0586b16, c36763c36` |
| 34 | B1 | Sources/ingestion | fixed_on_branch | medium | SourceCreateWizard.tsx handleSubmit now passes onError -> parseApiFieldErrors (src/lib/apiFieldErrors.ts) and renders field errors next to fields plus a role=alert footer with the general detail (egress 422 / quota 429 / unknown family). Backend create refusals (app/api/ingestion.py:317,207-210,329) all carry string or pydantic detail that the parser handles. | `fix/mongo-fe@97e338405` |
| 35 | B2 | Sources/ingestion | fixed_on_branch | medium | New useValidateSource (hooks.ts) calls POST /sources/validate?check_connection=true with the unsaved config (backend app/api/ingestion.py:238-299 runs egress check + connector validate_connection); result rendered by ConnectionTestResult. After create a Preview step calls POST /sources/{id}/preview and treats a 200 {error} as failure. Preview itself still needs a saved source (backend has no unsaved preview) - acceptable. | `fix/mongo-fe@0086395a6` |
| 36 | B3 | Sources/ingestion | fixed_on_branch | medium | SourceDetailDrawer.tsx SettingsTab gains ConnectionEditor reusing FamilyFormRouter; masked secrets ("********", app/ingestion/source_secrets.py MASK) show as 'saved' and restoreMaskedSecrets (src/features/ingestion/sourceSecrets.ts) sends the mask back so PATCH merge_masked_update keeps them (app/api/ingestion.py:378-385, re-egress-checked). Limitation: a secret cannot be cleared (an emptied secret is restored to the mask), and the whole uri is masked so the host is not visible while editing. | `fix/mongo-fe@02a1f6f26` |
| 37 | B4 | Sources/ingestion | fixed_on_branch | low | DatabaseForm.tsx MongoAdvanced (collapsed) adds host, port, replica_set, batch_size, max_documents_per_sync, timeout_ms with the backend's keys (mongodb_connector.py _mongo_uri/_settings); numbers sent as numbers, emptied fields dropped. Multi-host host is accepted at save time on main since USR-2 60fd9fa11. timeout_ms hint ('connect and server-selection') matches both main and the ingestion branch semantics. | `fix/mongo-fe@197aae2db` |
| 38 | B5 | Sources/ingestion | fixed_on_branch | low | Choosing MONGODB-X509 sets tls:true and TlsFields gets requiredReason (TLS checkbox locked on), so CA / client cert / private key fields render immediately (fields.tsx TlsFields enabled = value.tls \|\| requiredReason). | `fix/mongo-fe@955f68220` |
| 39 | B6 | Sources/ingestion | fixed_on_branch | low | Drawer health error, health title, sync-start error, reindex error and job.error_message now go through FriendlyErrorMessage / friendlyConnectionError (src/lib/friendlyError.ts): short reason + sanitised Details (URIs, ('host',port) tuples, IPs, host:port, dotted names stripped). Remaining: sanitising is UI-only (API still returns the raw TopologyDescription), DLQPanel.tsx:40 still renders entry.error_message raw, and the job-message hunk conflicts with main USR-1/USR-5 (see new_findings NF-3). | `fix/mongo-fe@39b7d9c9b` |
| 40 | C4 | Sources/ingestion | fixed_on_branch | medium | mongodb_connector.py _client now installs the MCP _MemberGuard (app/mcp/servers/mongodb_server.py _install_member_guard wraps pymongo.pool_shared._create_connection) with allowed = checked seeds + checked/pinned members, on both the discovery client and the main client; any later-advertised member is refused before a socket exists; selector kept. Depends on the private pymongo hook (MDB-10) and imports the MCP module from ingestion. | `fix/mongo-ingestion@010bf58e3` |
| 41 | C7 | MCP connector | fixed_on_branch | medium | _find_limit clamps none->100, 0/negative/>max->1000 (settings); aggregate appends {$limit:max+1}, allowDiskUse False, streams cursor to max; every tool runs under pymongo.timeout(15s) so commands carry maxTimeMS. Residual: cap is by document count only (1000 x up to 16MB docs). | `fix/mongo-mcp@34cfaff65` |
| 42 | C8 | Sources/ingestion | fixed_on_branch | low | hooks.ts useSourceHealth: retry:false, refetchOnWindowFocus:false, no background refetch; only the open drawer polls ({poll:true}) at 5 min with backoff to 60 min on failures and pauses while the tab is hidden; SourceCard checks once (staleTime 4 min). Backend still has no shared health cache (app/api/ingestion.py:415-436 runs validate_connection per call), so each mounted card still opens one connection per page load. | `fix/mongo-fe@f0ab58fd7` |
| 43 | MDB-01 | MCP connector | fixed_on_branch | high | DSNs in url or any auth_config key are sealed in the connector secret store (dsn_secrets.seal_connector_dsns) on register and update; _mask_auth_config redacts DSN values; _public_connector masks legacy rows; startup migration seals existing Postgres/Redis rows (CAS, advisory lock); MCPClient resolves the ref for the handler. Low residual: mask_dsn cuts at '#', so a pymongo-accepted 'mongodb://alice:p#w@h' displays as 'mongodb://alice:p' (username + password prefix). | `fix/mongo-mcp@24044b98d` |
| 44 | MDB-02 | MCP connector | fixed_on_branch | high | classify_tool_risk now declares MongoDB risk by operation (_MONGODB_TOOL_RISK; base name after '__' or '/'), aggregate is read only for a verified write-stage-free pipeline (args now passed from executor_mixin and tool_gate), connector names can only raise (high-risk override via _max_risk; generic path max(alone, combined)). budget-db insert_one -> write_high, prod-db delete_one stays destructive. | `fix/mongo-mcp@b3994890d` |
| 45 | MDB-03 | MCP connector | fixed_on_branch | high | call_tool runs assert_safe_mongo_arguments over all arguments before connecting: refuses $out/$merge/$where/$function/$accumulator/$code, BSON Code values, $currentOp/$listSessions/$listLocalSessions at any depth (depth cap 64). | `fix/mongo-mcp@4de10c794` |
| 46 | MDB-08 | MCP connector | fixed_on_branch | medium | See C7: limit 0/negative clamped, aggregate bounded server-side, per-call timeout; stalled fake mongod returns an error in <6s for every tool. | `fix/mongo-mcp@34cfaff65` |
| 47 | MDB-11 | Sources/ingestion | fixed_on_branch | medium | MCP path was already guarded on main; the ingestion path now uses the same socket-factory guard (see C4), so a member advertised after discovery is never dialled through _connected or get_delta. | `fix/mongo-ingestion@010bf58e3 (ingestion) + main 8d6335c53 (MCP)` |
| 48 | TG-01 | MCP connector | fixed_on_branch | high | tests/api/test_connectors_dsn_secrets.py: register/list/get/update over 5 payload shapes assert no userinfo in responses or Redis and one vault ref; tests/mcp/test_connector_dsn_secrets_integration.py covers rows at rest and the migration. Tests use auth_type 'none' (connection_string would 422, A1). | `fix/mongo-mcp@24044b98d` |
| 49 | TG-02 | MCP connector | fixed_on_branch | high | tests/agent/test_tool_risk_mongodb.py parametrises every MongoDB tool over budget/target/widget/settings/prod names, qualified names and the governed gate. | `fix/mongo-mcp@b3994890d` |
| 50 | TG-03 | MCP connector | fixed_on_branch | high | tests/mcp/test_mongodb_operator_guard.py (one refusal per operator, before connecting) and test_mongodb_operator_guard_integration.py (real mongod: refused calls write/run nothing). | `fix/mongo-mcp@4de10c794` |
| 51 | TG-04 | MCP connector | fixed_on_branch | high | tests/mcp/test_mongodb_tls_integration.py: requireTLS container, server cert verified, bypass refused, client cert presented, X.509 auth, X.509 without cert refused. | `fix/mongo-mcp@226d1fc25` |
| 52 | TG-05 | MCP connector | fixed_on_branch | medium | tests/net/test_mongodb_tls_policy.py parametrises every weakening URI option/field on the MCP handler (and ingestion). Gap: no case for ';'-delimited options, which is exactly the live bypass (C6/MDB-07). | `fix/mongo-mcp@95708faa8` |
| 53 | TG-08 | Sources/ingestion | fixed_on_branch | medium | New tests drive a real MongoClient (monitors + pool) against tests/ingestion/fake_mongod.py FakeMongod(set_name='rs0', advertise='localhost:<victim>') that starts advertising an unchecked member after discovery; they assert the topology learned the member and the Victim listener got zero connections, through _connected and through MongoDBConnector.get_delta. | `fix/mongo-ingestion@010bf58e3 (ingestion half)` |
| 54 | TG-10 | MCP connector | fixed_on_branch | medium | tests/mcp/test_mongodb_bounds.py (clamp matrix, non-integer refused, stalled fake server bounded for all 8 tools) + test_mongodb_bounds_integration.py (real mongod: find/aggregate caps, maxTimeMS on every command). | `fix/mongo-mcp@34cfaff65` |
