# Open gaps and new defects, per feature

Generated from `certification-matrix.json` (fresh re-certification of all 192 features against `main` at `a4d172588`, 2026-09-29).
Every item names the current `file:line`. Work top-down: FAIL features first, then items tagged `[high]`, then `[medium]`.

Status: 20 PASS · 149 PARTIAL · 9 FAIL · 14 NOT_IMPLEMENTED · 0 BLOCKED

> **2026-09-29 update — wave 7C merged:** Insights (real schema), golden datasets (honest 501), platform admin (usage from Postgres; incidents 501), marketplace paid purchase (501 before any charge), Stripe past-due downgrade, multimodal PDF failures, Google Drive partial/failed reporting, perception batch 501, full tenant export, civilization 503s. Re-certify these features before ticking their items.

Items already assigned to fix wave 7 (see `HANDOFF.md`) are still listed here; tick them off when the wave's branch is merged and re-verified.

## Agent core

### [FAIL] HITL pause/resume and checkpoint resume

_Step-level, policy and HITL-gate (7) supervised waits deny anything not APPROVED, and 15f74ea63 made approvals DB-first with Redis cross-replica delivery. But the supervised TOOL-call gate only denies REJECTED/TIMED_OUT, and wait_for_approval returns PENDING immediately on a Redis BLPOP error (verified by probe: status=pending after 0.00s), so a write_high tool is dispatched without approval. The non-supervised orphaned-request gap also remains._

- [ ] NEW app/agent/nodes/executor_mixin.py:2315-2330 — supervised write_high tool gate treats any status other than REJECTED/TIMED_OUT as approved and dispatches the tool; combined with app/governance/hitl.py:541-556 + 1102-1108 (BLPOP error -> _wait_for_result returns None -> FIRST_COMPLETED -> status stays PENDING) a transient Redis error bypasses human approval (probe confirmed PENDING returned in 0.00s) (severity: high)
- [ ] NEW app/governance/hitl.py:505-506 — wait_for_approval looks the request up only in the process-local dict; correct only because callers wait on requests they created in-process (low)
- [ ] Non-supervised modes still file approval requests nothing waits on: action-safety HITL_REQUIRED files a request and the step RUNS executor_mixin.py:969-978; policy REQUIRE_APPROVAL files then denies executor_mixin.py:1151-1165; HITL gate 7 files and runs executor_mixin.py:1190-1198; tool gate files then denies executor_mixin.py:2290-2365

### [PARTIAL] Agent loop kernel (AgentGraph LangGraph state machine)

_Checkpoint-read failure now fails the goal closed instead of silently re-running side effects, cancels propagate as GoalCancelledError (graph.py:867-871), and narrow-decision LLM calls are charged inside goal_charge_scope (graph.py:838-845, 64b262847). The production (Celery) path still compiles a plain AgentGraph that ignores the goal's runtime profile and agent pattern flags._

- [ ] Worker ignores runtime profile/GraphFactory/DistributedStrategyLoop: plain AgentGraph tasks.py:2661 (no runtime_profile kwarg; execution_context only read for persistence tasks.py:760-781)
- [ ] Worker ensure-row writes execution_context={} tasks.py:1933 (only when the row is missing)
- [ ] Goal-start latency stamp still except: pass graph.py:821-822 (low)

### [PARTIAL] Planner node

_Reasoning outputs (supervisor_result, debate_result/debate_consensus, tot_answer) are read into the planning prompt (planner_mixin.py:86-110) and the hard-coded gpt-5.2 fallback is gone (planner_mixin.py:515-525); planner spend is charged. Seven context-recall blocks still swallow exceptions silently._

- [ ] Recall/prompt-injection blocks except: pass (no log) planner_mixin.py:295,354,365,380,395,413,450 (low)

### [PARTIAL] Executor node (governed per-step pipeline and tool dispatch)

_The governed per-step pipeline, fail-closed safety profile/guardrails and policy approvals remain intact and tested (50+12 tests pass). Downgraded from PASS: the supervised write_high tool-approval branch dispatches the tool on any non-REJECTED/non-TIMED_OUT status, which a Redis BLPOP error produces (see HITL feature)._

- [ ] NEW app/agent/nodes/executor_mixin.py:2315-2330 — supervised tool approval: PENDING (e.g. Redis BLPOP error, hitl.py:1102-1108) falls through to _mcp_client.call_tool; must require status == APPROVED like the step/policy gates (severity: high)
- [ ] Minor/by design (SAFE-4): Guardrails-2.0 engine error fails closed only for high-risk steps executor_mixin.py:925-932 (_helpers.py:137-147)

### [PARTIAL] Verifier node, grounding, synthesis and evaluation

_Unchanged since the prior cert apart from the scorecard falling back to the observed profile (verifier_mixin.py:476-478, a723c3810). The high-risk grounding gate still engages only when tool evidence exists, so text-only ungrounded high-risk answers complete._

- [ ] Grounding gate fails closed only if _high_risk and _had_evidence verifier_mixin.py:300,349
- [ ] tests/e2e_full/test_hallucination_grounding_e2e.py:1-29 still documents the gap as xfail(strict)

### [PARTIAL] Optional reasoning nodes (CoT, ToT, self-refine, self-consistency, reflection, peer review)

_Node logic is correct and charged (ChargingProvider, reasoning_mixin.py:316,348,392,465) and 7+6 tests pass. Downgraded from PASS for production reachability: the nodes compile in only via runtime_profile or agent-config enable_* flags, both of which exist only on the in-process API path; the Celery worker (the path every goal takes when Redis is configured) builds AgentGraph with none of them, so CoT/ToT/self-refine/self-consistency/reflection/peer review never run for queued goals._

- [ ] NEW app/scaling/tasks.py:2661-2693 — worker AgentGraph gets no enable_cot/enable_reflection/enable_self_refine/enable_self_consistency/enable_tree_of_thoughts/enable_peer_review and no runtime_profile (API path passes them, goal_service.py:1563-1575,1675-1752), so optional reasoning nodes are inert in production queued execution (severity: high)
- [ ] Minor: self_consistency / ToT / peer_review exceptions only logged, no failed evidence recorded reasoning_mixin.py:328-332,357-361,408-412

### [PARTIAL] Multi-agent: supervisor and debate (API workflow modes and in-graph nodes)

_API debate consensus reaches the planner and API supervisor falls back honestly; in-graph supervisor/debate nodes work on the in-process path. Debate voting is still weak, the API modes run LLM work inside the HTTP request with the raw uncharged platform provider before the budget preflight, and on the worker the in-graph nodes are never enabled._

- [ ] NEW app/api/goals.py:329-337,359-365 — API debate/supervisor modes call DebateOrchestrator/SupervisorAgent with the raw platform provider (debate.py:86,114,143; supervisor.py:260,312 use provider.complete directly): N agents x rounds LLM calls are uncharged, not circuit-broken, ignore tenant BYOK, and run before GoalService's budget/readiness preflight, so an over-budget tenant still spends (severity: medium)
- [ ] NEW app/scaling/tasks.py:2661-2693 — worker never sets enable_supervisor/enable_debate or a runtime profile, so in-graph supervisor/debate are API-path-only (severity: medium)
- [ ] Critiques never included in vote prompt debate.py:120-140; unparseable vote -> proposals[0] while winning_agent reports raw string debate.py:150-158
- [ ] rounds clamped to 3 debate.py:49, only rounds>=2 changes behaviour debate.py:93; API accepts debate_rounds up to 10 goals.py:59
- [ ] API supervisor mode awaits sub-goals inside the HTTP request and returns id ''/no parent goal record goals.py:353-410; str(exc) leaked in 500 goals.py:366
- [ ] Worker: supervisor parent holds its Celery slot while awaiting sub-goals on the same queue (supervisor.py:94,175)

### [PARTIAL] Goal-tree decomposition

_Dependents of a failed sub-goal are now skipped and the planner's routed model is used (both pre-baseline fixes the prior audit missed). Sub-goals still run without a goal_id, so they have no DB rows/events/checkpoints and their LLM spend is charged under an empty goal id rather than the parent._

- [ ] NEW app/agent/goal_tree.py:86-90 + app/agent/nodes/llm_cost.py:53 — sub-goal graphs run with goal_id '' so their LLM spend is charged to goal '' (shared per-tenant bucket) instead of the parent goal: the parent's per-goal budget does not bound the subtree (severity: medium)
- [ ] NEW app/agent/goal_tree.py:53 — decomposition call uses the raw planner (not ChargingProvider), so the decomposition LLM call is uncharged (severity: low)
- [ ] Sub-goals run without goal_id (no DB rows/events/checkpoints) goal_tree.py:86-90

### [PARTIAL] Agent auto-routing (AgentRouter)

_Routing works and the LLM classifier call is now charged and circuit-broken (router.py:318-327, 64b262847). N+1 history queries over an unbounded agent list, the missing eval_store and silent LLM-scoring fallbacks remain._

- [ ] Per-agent DB history query inside loop (N+1) over unbounded list_all() router.py:374-393
- [ ] eval_store never passed: AgentRouter(agent_store, llm_provider) main.py:2710-2713 -> non-DB history 0.0 router.py:243
- [ ] LLM scoring errors swallowed; goal proceeds on keyword score router.py:347-348,422-423

### [PARTIAL] Tool governance helpers and event sanitization

_Unchanged since the prior cert. A re-run probe confirms bare Stripe sk_live_, Google AIza and GitLab glpat- keys pass sanitize_event unredacted while sk-proj- is redacted._

- [ ] Bare Stripe (sk_live_/sk_test_), Google (AIza...) and GitLab (glpat-) keys not in BARE_SECRET_PATTERNS sanitization.py:26-42 (re-verified by probe scratchpad/probe_sanit.py)
- [ ] Minor: rpa_click/rpa_type stay write_low tool_risk.py:264-266

### [PARTIAL] Goal persistence engine (retry until success)

_Unchanged: worker drives GoalPersistenceEngine with Redis operator controls between attempts. ESCALATE still only emits an event and breaks, and abort is not honoured mid-attempt._

- [ ] ESCALATE only emits persistence_escalating + break; nothing waits for a human persistence.py:478-487
- [ ] Abort only honoured between attempts/backoff, not mid-attempt persistence.py:459-462,502-507

### [PARTIAL] Agent WorkflowPlanner/WorkflowExecutor and the static multi_agent workflow

_Worker honours workflow_mode through the same static WorkflowPlanner/WorkflowExecutor with the governed tool gate (b6f3682ea, tests pass). DAG non-connector steps still complete from an unverified 'Execute this task' LLM reply, the UI never sends agent_ids, and the worker executor has no retrieval_gateway._

- [ ] DAG non-connector steps completed by unverified 'Execute this task' LLM reply workflow_executor.py:399-425
- [ ] UI Multi-Agent sends workflow_mode only, no agent_ids agent-verse-frontend/src/features/goals/components/MissionGoalComposer.tsx:127
- [ ] Worker WorkflowExecutor built without retrieval_gateway tasks.py:961-965 vs goal_service.py:3658-3666

### [PARTIAL] Dynamic orchestration: classification, pattern selection, runtime profile and GraphFactory

_All five prior gaps are closed: GraphFactory refuses undriven strategies, the builder rejects them, the profile persists JSON-safe under RLS, the rollout gates v2, the ReadinessGate sees real dependency health and blocks with a 503, and build failures are recorded. But every goal is queued to Celery whenever Redis is configured, and the worker never reads the profile, so an admitted v2 tenant's goal records strategy_runtime_path='v2' and a primary strategy while running a plain loop._

- [ ] NEW app/scaling/tasks.py:2661 + app/services/goal_service.py:4134-4170 — v2-admitted goals are enqueued to Celery and run a plain AgentGraph while execution_context records strategy_runtime_path='v2' and runtime_profile.primary_strategy; no strategy_execution record is written on the worker, so the recorded strategy never ran (severity: high)
- [ ] NEW app/services/goal_service.py:1996-2002 — patterns_used column is written from the requested profile for every tenant, including legacy-path goals that run the legacy graph (severity: low)
- [ ] NEW app/services/goal_service.py:4012-4029 — pattern_selection summary is recorded for every goal regardless of whether the worker runs it (severity: low)
- [ ] Worker ignores runtime profile entirely (plain AgentGraph tasks.py:2661; profile only read for persistence tasks.py:760-781) — the profile only drives the in-process path (task_queue=None)

### [PARTIAL] Strategy registry, readiness and certification catalogue

_Real readiness probes are registered for every declared dependency (ff2df9cba). Certification is derived from recorded runtime evidence persisted under RLS (c3667a30e), the catalogue reports availability/execution_driver so only runnable strategies can be ready (f8c8503b1), and adapter_paths are real (5a6f62914). Evidence is recorded only from GoalService's in-process terminal hook, so queued (production) goals contribute none, and the evidence table has no retention purge._

- [ ] NEW app/services/goal_service.py:2838-2883 — strategy evidence is recorded only in GoalService._record_terminal_goal_metrics for in-process goals; Celery-executed goals (all goals when Redis is configured) never write strategy_execution/evidence, so production certification evidence is structurally empty (severity: medium)
- [ ] NEW app/orchestration/strategy_certification.py:167-173 + app/db/migrations/versions/0096_strategy_runtime_v2.py:58-62 — tenant catalogue read is one LIMIT 5000 over all strategies ordered by observed_at (a hot strategy starves others' counts); index lacks observed_at/expires_at; no purge job deletes expired rows (one row per strategy per goal, unbounded growth) (severity: medium)

### [PARTIAL] Distributed strategy tier (StrategyRunner and DistributedStrategyLoop)

_strategy_runner.py is unchanged in range. Real provider model and per-call cost charging remain, but results are cached unbounded per process, goal cancel never reaches the runner, budget reservation is a no-op lambda, and the tier runs only on the in-process API path._

- [ ] NEW app/scaling/tasks.py:2661 — DISTRIBUTED tier never runs on the Celery worker (no StrategyRunner/DistributedStrategyLoop wiring), so in production it is only reachable when task_queue is None (severity: medium)
- [ ] Results (incl. failures) cached unbounded per process by (tenant, idempotency_key); _locks/_cancel_events never evicted strategy_runner.py:84,179-186
- [ ] Goal cancel never calls StrategyRunner.cancel (no caller in app); outer CancelledError leaves the execution task orphaned — finally only releases budget strategy_runner.py:97,244-310
- [ ] reserve_budget default lambda: True, not overridden strategy_runner.py:73 (main.py:2598-2612)
- [ ] Runner checkpoint ignored ('del checkpoint'); in-memory per-process checkpoint/answer stores strategy_executor.py:106-107,126

### [NOT_IMPLEMENTED] Pattern adapter library and dead or legacy modules

_All listed modules still have zero importers (re-verified). The local ReWOO/LATS/CodeAct adapters still never execute on a live path, but they are now honestly catalogued as experimental/no driver and the profile builder rejects them (e45ed57fc, f8c8503b1), so they are no longer over-claimed._

- [ ] Zero importers: agent/{pattern_assembler,goal_classifier,semantic_entropy,errors}.py, agent/patterns/dynamic_graph_assembler.py, orchestration/workflow_compatibility.py
- [ ] Outbound A2A tool app/agent/tools/a2a_call.py has no importers
- [ ] Local reasoning/code adapters (rewoo/lats/llm_compiler...) have no goal driver execution_drivers.py:35-60

### [PASS] Model routing, adaptive execution strategy and LLM failover

_Per-role failover and model pinning are intact. 61cb4615e/1451def5d stop re-running after partial stream output and emit token_reset even when the last model fails (graph.py:927-960). Only the documented cold-start explore choice remains._

- [ ] Unknown models always probe richer modes (_EXPLORE_RATE_UNKNOWN=1.0) strategy_adaptivity.py:40,89 (low, documented)

## Providers & routing

### [PARTIAL] LLM / embedding provider adapters

_Anthropic streaming now returns tool_use blocks, usage and stop_reason. No provider re-runs complete() after partial output (anthropic_provider.py:227+, openai_compatible.py:575, gemini_provider.py:112-117), and Gemini streams now carry usage_metadata tokens (gemini_provider.py:108,118-130). Gemini still has no tool calling (it raises honestly), and observability cost still runs through the deprecated estimate_cost inside an except-pass._

- [ ] NEW app/providers/openai_compatible.py:146 / anthropic_provider.py:41 SDK clients built with default timeout and retries; only callers that route through complete_with_failover or _stream_with_failover get a bounded timeout, and many direct provider.complete() callers remain (see cost feature) [low]
- [ ] app/providers/openai_compatible.py:360-385 metrics cost via deprecated governance.pricing.estimate_cost inside except-pass (observability only)
- [ ] app/providers/base.py:163-178 embed_texts returns [] vectors on NotImplementedError (documented; callers must handle)
- [ ] app/providers/gemini_provider.py:44-51 Gemini rejects tool-bearing requests (honest, but Gemini cannot run executor tool steps)

### [PARTIAL] Provider resolution and deployment wiring (env registry, on-prem/NVIDIA cluster, worker parity)

_No registry change since d429f3dfc. The configured model and base_url are kept, and a malformed LLM_PROVIDERS fails in production. FakeProvider is refused in production (main.py:199-215, tasks.py:2833-2837). 'First healthy' still means 'first that instantiates', and the remaining gaps are unchanged._

- [ ] app/providers/registry.py:56,235-255 ProviderConfig.healthy never checked; first provider that instantiates wins
- [ ] app/providers/registry.py:155-162,342-351 Ollama models hardcoded (qwen3.8/qwen3-embedding/glm-ocr), no OLLAMA_MODEL
- [ ] app/providers/registry.py:339-340 gemini ImportError silently returns None (falls through to next/Fake)
- [ ] app/api/tenants.py:521-545 GET /me/providers 'configured' from process env only, ignores tenant BYOK
- [ ] app/main.py:2175-2178 probe_model_windows only at API startup; workers never probe (deployment_roles.py:52 map empty in workers)

### [PARTIAL] Tenant BYOK LLM configuration

_Both earlier gaps are fixed by 8d40fc820. The worker now reads the durable store with strict=True and raises TenantProviderError instead of falling back to the platform provider (tasks.py:484-529, 2082-2086). The API isolated path decrypts encrypted_key and fails the goal if decryption fails (goal_service.py:3474-3500). The worker's isolated-execution path still swallows key-resolution errors._

- [ ] NEW app/scaling/tasks.py:2960-2966 worker isolated-execution path: aget_llm_api_key_for_tenant errors are swallowed (warning) and _iso_llm_key stays '', and llm_config_store.py:258 reads get_config non-strict, so a BYOK tenant's isolated run can silently use the platform key (the API path fails the goal) [medium]
- [ ] NEW app/services/llm_config_store.py:239-245 + tasks.py:506-510: if the worker store cannot be built and Redis has no key (no cache error), config=None and the goal runs on the platform provider [low]

### [PARTIAL] Credential vault

_vault.py has not changed in the commit range. Fernet/PBKDF2 encryption and the production dev-key guard are tested. Key rotation is still dead code that would orphan Postgres ciphertext, and it reports success after a failure._

- [ ] app/providers/vault.py:208 rotate_key has no caller in app/
- [ ] app/providers/vault.py:257 rotation scans only mcp:connector_secrets:*; tenant_llm_configs/source credentials ciphertext in Postgres would become undecryptable
- [ ] app/providers/vault.py:306-307 Redis scan error swallowed yet returns status 'rotation_complete' (vault.py:352)
- [ ] app/providers/vault.py:26,74,85 per-process _CONNECTOR_SECRET_STORE fallback when no store passed
- [ ] app/api/tenants.py:974 BYOK vault key endpoint returns 'validated_not_persisted'

### [PARTIAL] Configured model registry and capability-based selection

_No ai_router or model_registry API change in the commit range. Provider health is updated only by the admin POST /models/test, per process. ProviderHealth defaults to is_healthy=True, so GET /models/health reports every provider healthy without evidence._

- [ ] app/ai_router/router.py:119 AIRouter.record_call has no callers; health only updated by POST /models/test (api/model_registry.py:201,209), per-process; ai_router/models.py:110 default is_healthy=True so GET /models/health (model_registry.py:151-160) reports unverified 'healthy'
- [ ] app/ai_router/registry.py:212-219 price_for returns (0.0,0.0) for unknown models while cost_tracker charges a non-zero fallback
- [ ] app/ai_router/selection.py:49-63 _lazy_seeded once per process; overrides reseed only the replica that received them

### [PARTIAL] Per-role model routing (planner/executor/verifier)

_Goal- and agent-level overrides and tenant routing policies still apply on both the API and worker paths. No routing code changed in the range, and all previously reported gaps remain at shifted lines._

- [ ] app/ai_router/model_orchestrator.py:427-440 override/role map/select_configured_model_id return before tier assignment, so plan caps and budget downgrade never constrain them
- [ ] app/ai_router/model_orchestrator.py:52-80 _TIER_MODELS/_MODEL_PROVIDER OpenAI slugs; unknown models map to 'openai' (line 72)
- [ ] app/scaling/tasks.py:2350-2363 tenant routing policies are read before the worker wires the Redis ModelRegistryStore (tasks.py:2610-2621), so the first goal in a fresh worker process ignores policies (registry.py:280-281 process-local dict)
- [ ] app/scaling/tasks.py:1652-1676 goal model_override lookup failure logs and returns '', silently dropping an explicit override; registry.py:293-296 falls back to process-local copy on store read errors

### [PARTIAL] Provider circuit breakers, health and model failover

_64b262847 routes narrow-decision calls (guardrail judges, intent/agent router, eval scorers, toxicity, corrective grading) through complete_with_failover, which adds a per-model circuit and a bounded timeout (guarded_completion.py:172-204). Budget refusals no longer open circuits (circuit_breaker.py:106-110). The executor stream still bypasses _provider_cb, circuits are process-local, and health circuits have no half-open state._

- [ ] NEW many LLM call sites still call provider.complete() directly with no circuit or timeout: app/knowledge_graph/extractor.py:92,154, app/triggers/nl_scheduler.py:482, app/orchestration/goal_classifier.py:325, app/workflow/steps/llm_step.py:101, app/org/*, app/memory/consolidation.py, app/mcp/tool_intelligence.py [medium]
- [ ] app/providers/circuit_breaker.py:118-133 platform circuits process-global per model (by design; per-replica state, not shared)
- [ ] app/agent/graph.py:909-941 executor _stream_with_failover calls stream_tokens under wait_for only, never via the per-model _provider_cb
- [ ] app/ai_router/provider_health_policy.py:17-37 health circuits have no time-based half-open; they close only on record_success
- [ ] app/agent/nodes/executor_mixin.py:1635 exception path still attributes failure to _exec_model only (the models actually attempted are recorded only on success)

### [PARTIAL] LLM generation tracing (TracedProvider / GenAI spans)

_Tracing code is unchanged in the range. TracedProvider wraps only the API goal path (goal_service.py), so Celery goals, workflows and embeddings emit no GenAI spans. Spans carry no cost_usd or cache_hit._

- [ ] No TracedProvider in app/scaling or app/workflow (only goal_service.py); Celery goals emit no GenAI spans; embeddings untraced
- [ ] app/observability/traced_provider.py:76,88 rec.set_response(resp) passes no cost_usd or cache_hit
- [ ] app/observability/traced_provider.py:45-54 MultiEndpointLLMProvider has no base_url, so gen_ai.system is the class name

### [PARTIAL] LLM cost and token accounting, budgets

_64b262847 now charges narrow-decision calls: inside a goal through charge_llm_call via goal_charge_scope (graph.py:835-842), and outside a goal against the tenant daily budget, with preflight and denial both failing closed (guarded_completion.py:107-167). charge_llm_call and the Redis controller now fail closed (llm_cost.py:62-76, cost.py:486-493). The executor budget gate still uses the deprecated estimate_cost, pricing uses a prefix match, and many LLM call sites remain unmetered._

- [ ] NEW LLM calls still made with provider.complete() and never charged to any budget or ledger: app/rag/indexing.py:425 (RAPTOR/agentic-chunking ingestion-time indexing, scales with document count), app/knowledge_graph/extractor.py:92,154 (/knowledge-graph/extract; the ingestion hook currently uses regex), app/triggers/nl_scheduler.py:482, app/orchestration/goal_classifier.py:325, app/workflow/steps/llm_step.py:101 (cost_usd read from the response, normally 0), app/org/service.py, app/memory/consolidation.py, app/mcp/tool_intelligence.py, app/api/insights.py [high]
- [ ] NEW app/providers/guarded_completion.py:98-104 _platform() swallows resolver exceptions to (None,None), so _charge (l.152-154) returns without charging: out-of-goal decision calls fail open [low]
- [ ] NEW app/providers/guarded_completion.py:113-114,151-152 with no goal scope and no tenant (e.g. chat intent classifier, intent.py:277, and guardrail_engine.py:396 when the caller omits tenant) the call is never charged [low]
- [ ] NEW app/governance/cost.py:632-690 try_record_and_check fails open on Redis errors (no callers today; dead code that would fail open) [low]
- [ ] NEW app/scaling/tasks.py (64b262847 hunk) set_platform_cost_services(lambda: (_decision_cost, None)) is a module global set per run_goal and passes no cost_tracker, so worker out-of-goal decision calls skip the ledger, and concurrent tasks in a threaded pool overwrite each other's controller [low]
- [ ] app/agent/nodes/executor_mixin.py:1646-1664 budget gate charges deprecated governance.pricing.estimate_cost while the ledger uses calculate_cost (the context total is reconciled at 1686-1695, but the controller's recorded spend is the estimate)
- [ ] app/intelligence/cost_tracker.py:83-88 prefix match `model.startswith(key) or key.startswith(model)`: an empty model matches the first key, and dated or unknown models are mispriced; the model_pricing table is never read
- [ ] app/governance/cost.py:483 LLM spend recorded as metric scope 'tool'

### [NOT_IMPLEMENTED] Unwired / legacy routing modules

_providers/model_router.py, ai_router/shadow_router.py and ai_router/complexity_scorer.py are still imported by nothing in app/ (grep matches only a graphify cache file). They are dead code covered only by unit tests._

- [ ] app/providers/model_router.py unwired; required_capabilities not enforced
- [ ] app/ai_router/shadow_router.py unwired (3 direct provider.complete calls; swallows shadow failures); no /shadow-log API
- [ ] app/ai_router/complexity_scorer.py unwired

## Tools & connectors

### [FAIL] Agent identity and credentials (scoped keys, RS256 service accounts, manifests)

_RS256 service accounts work end to end in the backend: the key is sealed in the vault, exchange is bound to the owned agent, and TenantMiddleware verifies agent JWTs against the non-revoked key with the 'agent' role ceiling (middleware.py:276-325, agent_identity.py:483-531). Nothing changed after a1e01aa3d, though: agent-scoped keys from /agents/{id}/keys are still minted and shown while no auth path accepts them, and the credentials page still reads fields the API does not return._

- [ ] NEW app/api/agent_credentials_api.py:13-55 — POST /agents/{id}/keys (owner-checked via require_owned_agent) returns an av_agent_* secret that looks valid but authenticates nothing: fake success for the caller (severity: medium)
- [ ] Agent-scoped keys (AgentCredentialStore) authenticate nothing: is_agent_key/resolve have no caller outside app/auth/agent_credentials.py, yet app/api/agent_credentials_api.py:28-55 issues them and the UI presents them
- [ ] Frontend/backend mismatch: src/features/agents/AgentCredentialsPage.tsx:10,42-48,146,185 expects credential_id/api_key; backend returns key_id/private_key_pem and revokes by key_id (app/api/agents.py:1338-1406), so the UI shows an undefined key and revoke 404s
- [ ] Exchange needs only key_id plus a tenant key, not proof of possession of the private key (app/api/agents.py:1409-1437)

### [PARTIAL] MCP connector registry and management API

_Management API works tenant-scoped and now SSRF-checks every stored URL at test time (13137f403, connectors.py:947-957) and reports 'not_tested' instead of reachable for builtin/no-URL connectors; cd6995022 adds GET /connectors/{id} and /{id}/tools (502 on discovery failure, not []). But the registry and its connector secrets live only in Redis (mcp_servers table still never written), so Postgres is not the source of truth, and the usage and health-history endpoints still swallow errors into zeros/[]._

- [ ] NEW app/api/connectors.py:1517-1545 — usage query is an unindexed LIKE scan over goals.execution_context; at millions of goals every connector detail view is a full-table seq scan (severity: medium)
- [ ] NEW app/api/connectors.py:1024 — generic reachability path calls the synchronous assert_public_url (blocking getaddrinfo) on the event loop (severity: low)
- [ ] No Postgres durability: MCPRegistry is Redis-only (app/mcp/registry.py:88-193); the mcp_servers table (app/db/models/mcp.py:14-35) is never written, so a Redis flush/eviction silently loses every tenant's connectors
- [ ] GET /{id}/health returns [] on any DB error, indistinguishable from 'no history' (app/api/connectors.py:1103-1104)
- [ ] GET /{id}/usage: LIKE '%id%' substring match on execution_context->>'connector_ids' and `except Exception: pass` returns zeros as if unused (app/api/connectors.py:1517-1576)
- [ ] Direct probes validate then connect on a plain httpx.AsyncClient, not the IP-pinned public_async_client (DNS-rebinding window) (app/api/connectors.py:624,718,845,1039)

### [PARTIAL] Connector secret storage and credential resolution

_Unchanged since the last rating: secrets are stored as vault-encrypted, tenant-scoped refs (RedisConnectorSecretStore, vault.py:98-135), production refuses the process-local store (connectors.py:134-154), and builtin/HTTP dispatch resolves only through the tenant-aware resolver. The durable copy of every connector secret is Redis-only, and the minor gaps (dev master key outside 'production', decrypt swallowing, dead OAuth copy in auth_config) remain._

- [ ] NEW app/providers/vault.py:98-135 — connector secrets are persisted only in Redis (mcp:connector_secrets:*), no Postgres copy or backup path; eviction/flush loses all tenant credentials, violating 'Postgres as source of truth' (severity: medium)
- [ ] OAuth GET callback still writes vault-encrypted _encrypted_access/_refresh_token into the Redis auth_config, which nothing reads (app/api/connectors.py:1466-1485)
- [ ] Dev-insecure master key fallback for any ENVIRONMENT other than exactly 'production' (e.g. staging), warning only (app/providers/vault.py:29-51)
- [ ] OAuthFlowManager._decrypt_token swallows decrypt errors and returns the ciphertext as the token (app/mcp/oauth.py:109-116)
- [ ] Resolver returning None becomes '' and the request is sent with no auth (fails loudly at the remote) (app/mcp/client.py:261-270)

### [PARTIAL] Connector OAuth (popup flow, PKCE flow, token manager)

_Unchanged since d121179a0: PKCE flows are shared via Redis GETDEL (oauth.py:161-210), tokens are durable in oauth_tokens under RLS (FK to mcp_servers dropped, f1a2b3c4d5e7) and read by API replicas and the worker. Persistence and refresh errors are still swallowed so the callback can answer 'connected' without a durable token, and the popup flow is an honest 501._

- [ ] NEW app/mcp/oauth.py:247-270 — exchange_code validates token_url then POSTs the code+verifier on a plain httpx.AsyncClient (not the pinned public_async_client), a DNS-rebinding window on a tenant-supplied URL (severity: low)
- [ ] NEW app/api/connectors.py:1450-1451 — exchange exceptions returned as 200 {status:'error', message:str(exc)} rather than an HTTP error (severity: low)
- [ ] _persist_token_to_db swallows errors (warning only) (app/mcp/oauth.py:570-573); callback still answers status 'connected' (app/api/connectors.py:1487-1494) even when the durable token the worker/other replicas need was not written
- [ ] MCPClient suppresses refresh/lookup exceptions and silently sends no bearer (app/mcp/client.py:1570-1581)
- [ ] Popup flow: process-local _oauth_states (app/api/connectors.py:1115-1126) and POST /oauth/callback honest 501 (app/api/connectors.py:1254-1300)
- [ ] Refresh lock is process-local; two replicas can both POST a rotating refresh token (DB re-read narrows the window) (app/mcp/oauth.py:418-470)

### [PARTIAL] MCP client tool discovery and dispatch

_The exfiltration-guard error path now fails closed (client.py:1320-1330), and SSRF checks run before builtin/WS/HTTP dispatch (client.py:986-1007). Tool stats are still a no-op, circuit-breaker check errors fail open, an open circuit serves stale cache as success, WS failures silently fall back to HTTP, and every HTTP dispatch validates-then-connects on a plain httpx client._

- [ ] NEW app/mcp/client.py:996 + 478,785,866,1058 — URL checked with synchronous assert_public_url (blocking DNS on the event loop) and then dialled on plain httpx.AsyncClient, leaving a DNS-rebinding TOCTOU on every tenant-supplied MCP/OpenAPI/Jira endpoint; the pinned public_async_client is not used (severity: medium)
- [ ] MCPClient._db is never assigned anywhere in app/ (only getattr(self,'_db',None) at app/mcp/client.py:1391,1411,1429), so _update_tool_stats is a no-op and tool_capabilities success_rate/latency never update
- [ ] WebSocket failure silently falls through to HTTP dispatch (app/mcp/client.py:1030-1036)
- [ ] Circuit-breaker check error fails open (app/mcp/client.py:1203-1204)
- [ ] Open circuit serves stale read cache as success=True (app/mcp/client.py:1180-1196)
- [ ] _dispatch_jira_rest_tool only supports jira_search_issues (app/mcp/client.py:830-840)

### [PARTIAL] Built-in MCP servers (registry_wiring)

_Unchanged: builtin handlers are stateless and resolve per-tenant credentials; MCPClient restores a missing handler from module configs, so dispatch works on the worker too (client.py:941-965). Credential-free builtins are still only inserted for tenants loaded at boot._

- [ ] NEW app/main.py:1428-1442 — every replica start loops every tenant x every builtin with sequential Redis round-trips; at millions of tenants this dominates startup (severity: low)
- [ ] register_builtin_servers only runs in lifespan over tenants in memory at boot (app/main.py:1418-1445); tenants created later get no credential-free builtin connectors until a restart; no other caller
- [ ] app/mcp/certification.py and certification_manifest.py remain unreferenced by app code

### [PARTIAL] OpenAPI import and tool capability registry

_Unchanged: imported operations become tool_definitions that the planner can dispatch, credentials go to secret refs, GET /capabilities returns 503 on failure, and tool_capabilities RLS was verified on real Postgres. The /capabilities/missing heuristic, swallowed discovery errors and the overstated tools_saved count remain._

- [ ] NEW app/api/connectors.py:1748-1758 — GET /capabilities has no LIMIT/pagination; returns every tool row for the tenant (severity: low)
- [ ] /capabilities/missing matches catalog names by substring against tool names; can_proceed true whenever any tool exists (app/api/connectors.py:1899-1923)
- [ ] /capabilities/search and /capabilities/missing wrap discover_all_tools in contextlib.suppress, so failure looks like 'no tools' (app/api/connectors.py:1790-1791, 1895-1896)
- [ ] POST /{id}/discover increments tools_saved inside a transaction that can roll back after a swallowed error, and returns 200 with the inflated count (app/api/connectors.py:1826-1886)
- [ ] CapabilitySearch re-embeds every tool on each query; no embedding cache (app/mcp/capability_search.py:188-190)

### [PARTIAL] Tool context and tool registry for the planner

_Unchanged on the web path, and the worker path now has an RPA executor but still no RPA tools in its ToolContext. Web and worker build different tool sets for the same goal (auto_approve, agent_id=None handling, RPA tools)._

- [ ] NEW app/scaling/tasks.py:2246-2265 — worker ToolContext never includes RPA_TOOLS, so queued goals' planner is never offered rpa_* tools even though 72fd3f064 wired an RPA executor (severity: medium)
- [ ] NEW app/scaling/tasks.py:2251-2253 — worker discover_tools per connector is not wrapped; one unreachable connector raises and aborts the goal's tool context, where the web path records connector_errors and continues (severity: medium)
- [ ] Web ToolRef never sets auto_approve (app/services/goal_service.py:2227-2235, 2269-2276) vs worker (app/scaling/tasks.py:2256-2263)
- [ ] agent_id=None -> RPA-only tools on the web path (app/services/goal_service.py:2238-2239) vs every tenant connector on the worker (app/scaling/tasks.py:2238-2244)
- [ ] RPA tools always injected on the web path even without Playwright (app/services/goal_service.py:2224-2236)
- [ ] ToolTrustStore process-local, keyed by tool_name only (app/tool_runtime/tool_trust_store.py:7-15)

### [PARTIAL] Connector health monitoring

_Unchanged since 273387a5f: check_mcp_health classifies status and writes a snapshot per check under tenant RLS, and GET /{id}/health reads them. The probe still assumes {base}/health, builtins are never probed, and the design does not scale: a 30-second beat drives a serial scan of up to 5000 connectors with 5 s timeouts and unbounded snapshot growth._

- [ ] NEW app/scaling/celery_app.py:148-151 + app/scaling/tasks.py:4227,4304 — scheduled every 30 s but probes up to 5000 connectors serially (5 s timeout each), so runs overlap and connectors beyond 5000 across all tenants are never checked (severity: medium)
- [ ] NEW app/scaling/tasks.py:4239-4280 — one snapshot row per connector every 30 s with no retention/pruning or partitioning of connector_health_snapshots (grep finds no delete path) (severity: medium)
- [ ] NEW app/scaling/tasks.py:4339-4342 — request_public on a plain httpx.AsyncClient re-validates hops but does not pin the connect IP (DNS-rebinding window) (severity: low)
- [ ] Probe is always GET {base}/health; standard MCP servers 404 and are recorded 'degraded' (app/scaling/tasks.py:4329-4345)
- [ ] Builtin connectors are never health-checked (app/scaling/tasks.py:4331-4332)
- [ ] _fallback still caps at 50 keys and writes no snapshots (app/scaling/tasks.py:4404-4407)
- [ ] History endpoint returns [] on any DB error (app/api/connectors.py:1103-1104)

### [PARTIAL] Code interpreter and sandbox execution

_The Docker sandbox is now verified for real on colima: tests confirm stdout capture, the deadline kill, no network and container cleanup, with network_mode=none, read_only, uid 1000, pids_limit, cap_drop=ALL and no-new-privileges (code_interpreter.py:200-214). The output cap still applies only after the whole log stream is read into API memory, and audit failures are swallowed._

- [ ] Output cap applied after container.logs() has read the full stream into memory (app/tools/code_interpreter.py:238-243)
- [ ] Audit failures on /tools/execute-code are swallowed (warning only) and nothing is recorded when app.state.audit_log is None (app/api/tools.py:63-97)

### [PARTIAL] Native tool endpoints (tenant file workspace, email)

_Unchanged: file ops are tenant-path-confined and email always sends from the platform sender with CR/LF checks. The workspace is still pod-local /tmp, which is wrong with multiple replicas, and there is no recipient policy or send quota._

- [ ] Workspace is pod-local /tmp/agentverse-workspace/{tenant}: not shared across replicas and lost on restart (app/tools/file_ops.py:12,22-24)
- [ ] No per-tenant recipient allowlist, send quota or approval on /tools/email/send; any tools:write key can relay mail from the platform sender (app/api/tools.py:178-179)

### [PARTIAL] RPA browser automation

_All three blocking gaps are fixed. 72fd3f064 wires a shared build_rpa_executor onto the Celery AgentGraph (tasks.py:2702-2712). 0b7c71694 makes takeover raise a persisted HITL approval (require_persisted=True, 503 when it can't be saved; rpa.py:298-380). e5aefccad routes every browser request through the IP-pinned client, closing DNS rebinding, and 2a253f544 returns 409 for sessions live on another replica. It stays PARTIAL because Playwright is still not installed in the venv, so the real-browser path is mock-tested only, and the worker planner is never offered RPA tools._

- [ ] NEW app/rpa/session_manager.py:132-137 — per-tenant browser cap (5) is counted per process, so a tenant can hold 5 x N replicas (plus workers) Chromium instances; no global cap (severity: medium)
- [ ] NEW app/net/browser_guard.py:319-340 — WebSocket guard validates the URL then lets Playwright connect_to_server resolve DNS itself (rebinding window for ws/wss only) (severity: low)
- [ ] NEW app/net/browser_guard.py:285 — guarded fetch buffers the whole response body (response.content) in API memory with no size cap (severity: low)
- [ ] NEW app/rpa/session_manager.py:310-333 — a crashed owner replica's registry record keeps other replicas answering 409 until its 1 h TTL expires (severity: low)
- [ ] BLOCKED: Playwright is not installed in the backend venv (ModuleNotFoundError; optional extra 'browser', pyproject.toml:86), so the real Chromium path is only mock-tested here
- [ ] Browser pages remain per-process; cross-replica access is an honest 409, not shared (app/rpa/session_manager.py:100,176-195)
- [ ] Worker ToolContext omits RPA_TOOLS (app/scaling/tasks.py:2246-2265), so queued goals never plan rpa_* calls

### [PARTIAL] Perception (headless browser plus vision)

_Two gaps were already fixed in code by 313e46819: goal-with-image now analyses image_b64 with the vision provider or answers 501/502 (perception.py:238-259), and /analyze answers 501 without a vision provider (perception.py:107-111). Batch analysis can still return 'No vision provider configured.' or 'Vision analysis failed: …' as a successful analysis, there is no global Chromium cap, and the real browser path is unverified._

- [ ] NEW app/perception/page_analyzer.py:57-61 + app/api/perception.py:206-213 — batch-analyze calls analyze_screenshot without raise_errors, so the strings 'No vision provider configured.' / 'Vision analysis failed: …' are returned as `analysis` with success=true (fake analysis) (severity: medium)
- [ ] NEW app/perception/browser_agent.py:243-261 — vision LLM calls go straight to provider.complete: not charged to the tenant budget and not circuit-broken, unlike the calls guarded in 64b262847 (severity: low)
- [ ] batch-analyze gathers up to 10 URLs concurrently per request with no global Chromium cap (app/perception/page_analyzer.py:75-82)
- [ ] BLOCKED: Playwright optional extra 'browser' (pyproject.toml:86) not installed in the venv; real path unverified

### [PARTIAL] A2A protocol and agent directory

_The callback now goes through the IP-pinned public_async_client and is re-validated at send time (ca5547797; a2a.py:277-301). The core path stays honest: terminal status comes from the goal, timestamped single-use signatures, and the tenant boundary is verified on Postgres. Execution is still a fire-and-forget asyncio task, the callback is unsigned, and context is dropped._

- [ ] NEW app/api/a2a.py:392-419 — when app.state.goal_service is None the task is persisted and answered 202 'accepted' but never executed (severity: low)
- [ ] NEW app/api/a2a.py:285,369 — synchronous assert_public_url (blocking DNS) in async handlers (severity: low)
- [ ] execute_and_callback (including submit_goal) is a fire-and-forget asyncio.create_task; a restart leaves the a2a_tasks row 'accepted' forever with no callback (app/api/a2a.py:394-414)
- [ ] Per-agent directory is an honest 501 (app/api/agent_directory.py:26-37)
- [ ] Callback POST is unsigned, so the receiver cannot authenticate it (app/api/a2a.py:290-299)
- [ ] Agent card advertises only X-A2A-Signature, not X-A2A-Timestamp or the '{ts}.body' signing base (app/api/a2a.py:321-326); A2ATaskRequest.context is accepted but never passed to submit_goal (app/api/a2a.py:305-311,398-403)

## Tenancy & security

### [PARTIAL] Tenant lifecycle and API-key management (app/api/tenants.py + app/services/tenant_service.py)

_Unchanged: key limits are enforced on create/rotate, DELETE /me records a durable admin-only erasure job, and notification prefs no longer fake success. POST /me/roles still writes user_roles that authorization never reads. The GDPR export silently drops errors and returns only the first page of goals._

- [ ] NEW app/api/tenants.py:1129 — 'Export all tenant data' calls goal_svc.list_goals() with its default limit=50 (app/services/goal_service.py:4319-4321), so a GDPR/DSAR export contains at most the 50 newest goals with no pagination or truncation flag (severity: medium)
- [ ] POST /me/roles writes user_roles, but nothing reads it (load_roles_from_db has no caller, app/tenancy/rbac.py:90), so role assignments have no effect (app/api/tenants.py:596-631)
- [ ] POST /me/export swallows goal/agent read errors ('except Exception: pass') and returns an incomplete export as if complete (app/api/tenants.py:1128-1142)
- [ ] Key limit counts expired-but-is_active keys as active (app/api/tenants.py:181)

### [PARTIAL] Rate limiting and plan quotas

_Most degraded-Redis gaps are fixed. On a Redis error the middleware enforces an in-process window instead of a 500. The concurrent-goal gate raises ConcurrencyLimitUnavailableError instead of passing, and the auth limiter uses enforce_ip_rate_limit with unique ZADD members and an in-process fallback. check_knowledge_collection_limit is still never called, and the concurrent-goal counter's rolling TTL makes it inaccurate across long goals and crashes._

- [ ] NEW app/tenancy/limits.py:114-121 — concurrent_goals counter gets EXPIRE 3600 on every INCR: a goal running >1 h after the last submission lets the key expire and the tenant exceed its limit, while a leaked slot from a crashed worker never decays while the tenant keeps submitting (severity: medium)
- [ ] NEW app/tenancy/limits.py:152-160 — decrement_concurrent_goals is a non-atomic GET-then-DECR that swallows errors (severity: low)
- [ ] check_knowledge_collection_limit is never called (app/tenancy/limits.py:78)
- [ ] RateLimiter class unused outside the package export (app/tenancy/rate_limiter.py:129; app/tenancy/__init__.py:5)
- [ ] Degraded mode is per pod: N replicas allow up to N x the plan limit during a Redis outage (app/tenancy/rate_limiter.py:110-127)

### [PARTIAL] Scope enforcement, RBAC and IP allowlist enforcement

_Unchanged since ebc32b355: enforcement fails closed (restricted keys denied on unregistered routes, 503 on scope-load or allowlist errors), and effective permission is key scopes intersected with role scopes. The DB-backed custom-RBAC layer is still inert, and the loopback and role-less-read edges remain._

- [ ] api_key_scopes/role_assignments tenant rows are never written (no INSERT outside migrations/seeder), so _load_scopes always falls back to ROLE_SCOPES; custom roles are non-functional (app/auth/scope_enforcement.py:750-800)
- [ ] user_roles assignments (POST /tenants/me/roles) never consulted (app/tenancy/rbac.py:90 no caller)
- [ ] ABACEvaluator/RoleResolver used only by tests (app/auth/scope_enforcement.py:442,486)
- [ ] Loopback source IPs always bypass the allowlist; a same-host proxy/sidecar not in TRUSTED_PROXIES bypasses every allowlist (app/auth/ip_allowlist.py:130-132)
- [ ] Role-less keys may GET every route regardless of scope (legacy rows only) (app/auth/scope_enforcement.py:567-583)

### [PARTIAL] MFA (TOTP)

_Unchanged since fa164d792/7a98102ca: replay is checked globally, enrollment works across replicas, and the frontend sends X-MFA-Token on HTTP, SSE and WS (ws_auth enforces it too). 5571f27d5 deleted the unmounted duplicate router app/auth/mfa.py, so only app/api/mfa.py remains. Enforcement is still off by default, which makes MFA a client-side gate in a default deployment._

- [ ] Enforcement off by default (mfa_enforcement_enabled=False, app/core/config.py:444-446) and set in no infra/deploy config; without it the server accepts the API key alone
- [ ] MFA rate limit falls back to in-process on Redis errors (app/api/mfa.py:104-110)
- [ ] One TOTP secret per tenant, not per user (TenantMFA keyed on tenant_id)
- [ ] Legacy '.b64' plaintext rows still decrypt until re-saved (app/api/mfa_crypto.py:61-62)

### [PARTIAL] SSO: Keycloak OIDC and Google OAuth

_Unchanged: Keycloak JIT provisioning persists via the ORM with a unique sso_sub index (a9d3e5f7b1c2), lookup errors propagate, and roles map from realm roles. Google OAuth is an honest 501, the key-id lookup is in-memory only, and every SSO request does linear scans over the in-process tenant mirror._

- [ ] NEW app/services/tenant_service.py:745-747,782-783 — every Keycloak-authenticated request linearly scans self._tenants (an in-process mirror of the whole tenants table) twice; O(tenants) per request at millions of tenants (severity: medium)
- [ ] Google OAuth issues no AgentVerse session: honest 501 (app/auth/google_oauth.py:180-195)
- [ ] get_key_by_sso_sub checks only in-memory state (app/services/tenant_service.py:775-796); on other replicas api_key_id falls back to ghost 'sso:{sub}' (app/auth/keycloak.py:196-202)
- [ ] JIT user with no mapped realm role is a 'viewer' of a personal tenant; one tenant per SSO subject, no org mapping (app/auth/keycloak.py:157)
- [ ] POST /auth/logout documented but not implemented (app/api/auth.py:7)

### [PARTIAL] SCIM 2.0 provisioning

_The filter gap is fixed. a080d9628 parses 'attr eq "value"' filters for id/userName/emails and returns 400 invalidFilter for anything else instead of listing all users (scim_handler.py:196-222, routed at enterprise.py:2324-2328). Users CRUD runs on the ORM under RLS and is verified on real Postgres. Groups, ServiceProviderConfig and Schemas endpoints still don't exist._

- [ ] NEW app/api/enterprise.py:2324 — `count` has no server-side maximum, so a SCIM client can request an unbounded page (severity: low)
- [ ] No Groups/ServiceProviderConfig/Schemas endpoints; only /Users and /Users/{scim_id} are routed (app/api/enterprise.py:26-27, scim_router)

### [PARTIAL] WebSocket authentication

_Most gaps are fixed by 8dfd8ad59 (already in the tree before this rating window, but not credited). One authenticator, ws_auth.resolve_ws_tenant, now serves the collab, org, civilization and voice sockets. It rejects ?api_key= and enforces the tenant IP allowlist, key scopes and roles, and MFA before accept, all fail closed. The coordination group-chat socket still has its own authenticator that skips those policies, and no socket is rate-limited._

- [ ] NEW app/api/coordination_group_chat.py:51-63,79-83 — group-chat socket uses its own _authenticate (raw _tenant_key_resolver): no IP allowlist, key-scope/role check or MFA, so a viewer or IP-restricted key can post messages; resolver exceptions propagate uncaught (severity: medium)
- [ ] No per-tenant rate limit on WebSocket connects/messages in the shared authenticator (app/tenancy/ws_auth.py:125-161)
- [ ] WebSockets accept only API keys and stream tokens, not agent or Keycloak JWTs, so SSO users cannot open sockets (app/tenancy/ws_auth.py:69-80)

### [PARTIAL] SSRF egress guard (app/net/ssrf_guard.py)

_Two of the three recorded holes are closed. e9a8abacb moves the agent http_request tool onto the pinned public_async_client (http_tool.py:119-121), and e5aefccad makes the browser guard fetch via the pinned client instead of route.fetch (browser_guard.py:205-297). The pinned backend validates at connect time and is tested. But about ten callers still validate with assert_public_url or request_public and then connect on a plain httpx client, which leaves a DNS-rebinding window. Among them are the MCP client dispatch, connector tests, OAuth code exchange, the A2A outbound tool, knowledge URL ingest and gateway file download._

- [ ] NEW app/mcp/client.py:996, app/api/a2a.py:285,369, app/api/knowledge.py:1475 — synchronous assert_public_url (blocking getaddrinfo) called on the event loop in async request paths (severity: low)
- [ ] Validate-then-connect on plain httpx (DNS-rebinding TOCTOU): app/tools/knowledge_ingest_tool.py:152, app/api/enterprise.py:2238, app/scaling/tasks.py:4339, app/mcp/client.py:478,785,866,1058, app/api/connectors.py:624,718,845,1039, app/mcp/oauth.py:258, app/agent/tools/a2a_call.py:52, app/api/knowledge.py:1486,1508, app/gateway/router.py:252, app/ingestion/connectors/http_connector.py:171, app/rag_platform/hosted_reranker.py:105, app/mcp/servers/jira_server.py:296, app/mcp/servers/github_server.py:144
- [ ] Allowlisted host that fails to resolve returns [] with no IP check (safe only with the pinned client) (app/net/ssrf_guard.py:162-170)
- [ ] Browser WebSocket guard validates then lets Playwright connect_to_server resolve DNS (app/net/browser_guard.py:319-340)

### [PARTIAL] A2A protocol, agent directory and JWKS discovery

_The callback DNS-pinning gap is fixed (ca5547797, a2a.py:290), JWKS reads are RLS-correct, and the tenant boundary is verified on real Postgres. The outcome watcher is still a fire-and-forget task in the API process, the agent card under-documents the signing scheme, and the per-agent directory is an honest 501._

- [ ] NEW app/api/a2a.py:392-419 — with no goal_service wired, the task is persisted and 202 'accepted' but never executed (severity: low)
- [ ] Outcome watcher is asyncio.create_task on the API replica; a restart leaves the a2a_tasks row 'accepted' forever with no callback (app/api/a2a.py:394-414)
- [ ] Agent card advertises only X-A2A-Signature, not X-A2A-Timestamp or the signed-string format (app/api/a2a.py:321-326)
- [ ] Per-agent directory NOT_IMPLEMENTED (honest 501) (app/api/agent_directory.py:26-37)
- [ ] Callback is unsigned (app/api/a2a.py:290-299)

### [NOT_IMPLEMENTED] SAML 2.0 SSO

_Unchanged: ACS is public and tenant-scoped, and replay protection fails closed, but a valid assertion answers an honest 501 because no session is issued. Signature validation is still BLOCKED here: python3-saml (onelogin) is not installed, and the identity RLS SAML case is skipped for the same reason._

- [ ] NEW app/auth/saml_provider.py:208-212 — replay check is EXISTS then SETEX (not SET NX), so two concurrent posts of the same assertion on different replicas can both pass (severity: low; latent while ACS is 501)
- [ ] No JIT provisioning / session issuance after a valid assertion: honest 501 (app/api/enterprise.py:2195-2210)
- [ ] BLOCKED: python3-saml absent (ModuleNotFoundError: onelogin), so SAMLProvider.process_acs signature/strict validation cannot be exercised (app/auth/saml_provider.py:122-160)
- [ ] Replay key is name_id:session_index, not the assertion ID (app/auth/saml_provider.py:149); unauthenticated ACS reveals whether a tenant has SAML configured (404 vs 401) (app/api/enterprise.py:2176)

### [NOT_IMPLEMENTED] Agent-scoped API keys and agent identity

_av_agent_* keys from /agents/{id}/keys (AgentCredentialStore) still authenticate nothing: is_agent_key/resolve/check_tool_allowed have no caller outside app/auth/agent_credentials.py. One part of the previous gap is fixed: verify_agent_token is now used on the auth path, via AgentIdentityService.authenticate_agent_jwt from TenantMiddleware (a1e01aa3d), so RS256 agent identity works (tracked under the tools-connectors identity feature)._

- [ ] Agent-scoped keys cannot authenticate: no caller of is_agent_key/resolve/check_tool_allowed outside app/auth/agent_credentials.py; TenantMiddleware ignores them, yet app/api/agent_credentials_api.py:29-55 mints them

### [NOT_IMPLEMENTED] Unwired tenancy/auth modules (library-only)

_Unchanged: entitlements, temp_elevation and tenant_users have zero importers in app/, and custom_roles, goal_tokens and sub_tenants are referenced only in comments or unrelated identifiers (e.g. local variables named goal_tokens in agent/router.py)._

- [ ] No importers of app/tenancy/entitlements.py, app/auth/temp_elevation.py, app/tenancy/tenant_users.py, app/auth/custom_roles.py, app/tenancy/sub_tenants.py outside themselves (grep of app/)
- [ ] goal_tokens docstring claims per-goal worker tokens; nothing mints/verifies them (app/auth/goal_tokens.py:35)

### [PASS] API-key authentication pipeline (TenantMiddleware, stream tokens, security headers)

_All three recorded gaps are closed in current code. /auth/refresh and /auth/userinfo are in _BYPASS_PREFIXES (ebc32b355, middleware.py:82-83). SSO resolution fails closed and logs the error (middleware.py:265-273), and CSP connect-src is 'self' (middleware.py:574). Agent JWTs are RS256-verified before SSO and API-key resolution. On real Postgres, the e2e security sweep confirms every non-public operation rejects unauthenticated callers and stream tokens cannot write._

- [ ] NEW app/tenancy/middleware.py:70-110 — bypass is prefix-based (e.g. '/integrations/', '/v1/gateway/', '/channels/*'); each handler must self-authenticate. Currently covered by tests/e2e_full/test_security_sweep_e2e.py, but a new route under those prefixes would be public by default (severity: low)

### [PASS] PostgreSQL Row-Level Security (app/db/rls.py + migrations)

_Re-verified on real Postgres with Docker: the catalog audit (test_every_tenant_table_has_forced_rls_with_a_policy) passes on the migrated head, so every tenant table has FORCE RLS and a policy, including the new chat_channel_sessions, hitl approval-votes and org_graph_versions tables. Behavioural isolation, identity-table and graph-version cross-tenant tests pass._

- [ ] users is a global table without RLS (by design); agent_templates exposes tenant_id IS NULL rows (intended) (app/db/migrations/versions/0009_intelligence.py:215)

## Governance

### [FAIL] Incident controls: emergency stop and legal holds

_Legal holds still work: LegalHoldManager runs under RLS, returns 503 on failure, and is_under_hold fails closed. The emergency stop is admin-only, but it is not a fleet-wide kill switch. It cancels only goals held in this replica's in-memory dict, and no subscriber exists for the 'emergency_stop' channel it publishes. The start-gate flag expires after 5 minutes, and without Redis, or on a Redis read error, nothing is stopped. Yet it answers 'All running goals cancelled. Celery workers will abort in-progress tasks.'_

- [ ] NEW app/api/governance.py:1319-1324 — e-stop enumerates only goal_service._goals, this replica's in-memory records. Goals submitted through other replicas are never cancelled, although cancel_goal itself works cross-replica, and cancelled_goals/partial report success (severity: high)
- [ ] NEW app/api/governance.py:1343-1348,1449 — it publishes to the Redis channel 'emergency_stop', but nothing in app/ subscribes to it. In-flight worker and remote goals are not aborted: the flag is only checked at goal start (app/agent/graph.py:826, app/scaling/tasks.py:1788). The response still claims 'Celery workers will abort in-progress tasks' (severity: high)
- [ ] NEW app/api/governance.py:1350-1355 — the stop flag is set with ex=300, so the kill switch lifts itself after 5 minutes and new goals run again, although DELETE /emergency-stop documents clearing as a manual admin action (severity: high)
- [ ] NEW app/api/governance.py:1339-1341,1432-1449 + app/governance/emergency_stop.py:71,92 + app/agent/graph.py:566-574 — with no Redis wired, celery_signal_sent=False is not treated as an error (partial=False, status 'emergency_stop_activated'). The start checks return 'not stopped' when Redis is None or raises, and the worker check swallows errors (scaling/tasks.py:1807-1808). The stop fails open (severity: high)
- [ ] NEW app/api/governance.py:1395-1426 — the e-stop audit entry goes through the fire-and-forget AuditLog.record, so audit_recorded=True means only that it was queued, not persisted (severity: low)

### [PARTIAL] Grantex tool grants (grant store, enforce_tool_call, delegation, grant budgets)

_Minting and revoking a grant need admin. The grantee must be a real agent of the caller's tenant, the cost cap is limited to 0..10k, GET /grants/{id} returns spent_usd, spend goes to the grant that authorised the call through an atomic SQL increment, and the worker path enforces grants. But sub-agent delegation cannot work: the parent id is read from an AgentState field that does not exist._

- [ ] NEW app/agent/nodes/executor_mixin.py:2026 — delegate_active_grants(parent_agent_id=str(getattr(state,'agent_id','') or '')). AgentState (app/agent/state.py:64) has no agent_id field, so the parent is always '' and delegation.py:88-89 returns [] every time. Spawned sub-agents never inherit grants. With enforcement ON by default, every child tool call is denied (fails closed, but the Grantex delegation feature is dead code) (severity: medium)
- [ ] NEW app/governance/grants/delegation.py:95-101 — each delegated child grant copies the parent's full max_cost_usd rather than taking a share of the parent's remaining budget, so N children could together spend N times the parent's cap if delegation worked (latent while the bug above stands) (severity: low)
- [ ] delegation is still inside contextlib.suppress(Exception), and the parent is getattr(state,'agent_id','') while the gate uses self._agent_id (app/agent/nodes/executor_mixin.py:2016-2029)
- [ ] there is no REST endpoint for delegation or delegation chains (app/api/grants.py exposes only issue/list/get/revoke)
- [ ] with several capped grants and no authorising id, spend is only logged as 'grant_spend_unattributed', so no cap binds (app/agent/nodes/executor_mixin.py:210-221)

### [PARTIAL] HITL approval gateway (governance/hitl.py + /governance approvals)

_15f74ea63 wires Redis into the API and worker gateways, persists required_approvers, and adds an approval_votes table with ENABLE and FORCE RLS plus a tenant USING/WITH CHECK policy. Slack, org-task, email, chat, goal-service and batch approvals now call the DB-first approve_async, and the main REST route uses the authenticated key as approver. However, the email-link HMAC has a hard-coded default secret. Batch and org approvals take the approver name from the request body. Slack still takes the tenant from an env var and always answers ok, and the MCP approve tool is still broken. No production caller raises a HITL gate with required_approvers>1, so the durable-vote path is effectively unused._

- [ ] NEW app/integrations/email/approval_sender.py:20 — the HMAC secret defaults to the public string 'changeme-please-set-HITL_EMAIL_SECRET' and nothing refuses it in production. The signature covers only (request_id, action) and never expires. GET /governance/hitl/{id}/approve (app/api/governance.py:1512-1534) has no require_role('approver') and resolves the tenant from the request id on the RLS-bypassing system session (hitl.py:770-799), not from the caller. So any authenticated key, even a viewer's (GET is not a write) or one from another tenant, can forge a link and approve any gate whose id it learns (severity: high)
- [ ] NEW app/api/governance.py:1717,1729 — /hitl/batch-approve records approver=body.approver, a free-text field, instead of _approver_identity(). One approver key can forge attribution in the approvals table and audit, and it can meet a required_approvers>1 quorum alone by sending different names (the single-item route fixed this at :838-848) (severity: medium)
- [ ] NEW app/org/router.py:977 — the org task approve and reject routes (:890-915, :1392, :1482) take the approver from the request body (default 'user'). The routes have no approver-role check. HITL errors are swallowed (:992) and the org task still moves to running/approved when the gate resolution failed or lost the race (severity: medium)
- [ ] NEW app/api/governance.py:1725-1731 — after approve_async, batch-approve also pushes {'action':'approve'} onto hitl_result:{id}, but the waiter compares against 'approved' (hitl.py:543-549). If the waiter pops this payload first, wait_for_approval returns PENDING and the step is denied, even though the DB row is APPROVED and the API said 'approved'. On a partial vote, approve_async publishes nothing and returns True, so this stray push always ends the wait early with PENDING (severity: medium)
- [ ] NEW app/governance/hitl.py:694-701 + app/api/governance.py:878 — a vote below the quorum returns True and the REST route answers {'status':'approved'} while the gate is still closed. This misreports the state; the response should say the vote was recorded and quorum is pending (severity: low; latent because no producer uses required_approvers>1)
- [ ] NEW app/governance/hitl.py:740-763 — quorum race: the INSERT vote and the COUNT(*) run under READ COMMITTED with no lock on the approval_requests row. Two final votes committed concurrently on two replicas each count only their own vote (1 < 2), so nobody runs the CAS and the gate stays pending until it expires, although quorum was met. It fails closed (severity: low; latent)
- [ ] NEW app/governance/hitl.py:536-540 — on timeout the waiter only sets the in-memory status to TIMED_OUT; it writes nothing to the DB. Until the beat expiry runs, an approver can still 'approve' the row (CAS succeeds, API says approved) for an action the agent has already abandoned. There is also no DB polling fallback: cross-replica delivery depends on Redis BLPOP alone (severity: low)
- [ ] the fire-and-forget request_approval() still swallows persist errors (app/governance/hitl.py:244-253, 480-485). The executor raises gates this way (executor_mixin.py:285-297), so if the write fails the gate is invisible to other replicas and the inbox. It fails closed with a timeout
- [ ] Slack /slack/events: tenant from the SLACK_TENANT_ID env var, or 'slack-events' (app/api/integrations.py:147); approver is the Slack display name (:151); the result of approve_async/reject is ignored and the handler always returns {'ok': True} (:162). /slack/interactive has the same env-tenant pattern and swallows errors (:213-233)
- [ ] MCP server approve tool still calls hitl.approve(approval_id=, approved_by=, comment=), the wrong signature, and passes no tenant_ctx. It always fails with a TypeError, which is reported as 'hitl_gateway_unavailable' (app/gateway/mcp_server/__init__.py:596-605)

### [PARTIAL] Trust approvals and compliance bundles (/trust)

_Nothing changed in this window. The compliance autonomy ceiling still fails closed and binds on both the API and worker paths (scaling/tasks.py:2640-2655). Trust approvals are durable (TrustApprovalStore) with distinct authenticated approvers, but no execution path reads them, and bundle required_hitl_for patterns are never enforced._

- [ ] NEW app/api/trust_governance.py:214-225 — when trust_approval_store is None, approvals go to the module dict _approvals, which is process-local and invisible to other replicas; there is no 503 (severity: low, only when the lifespan wiring fails)
- [ ] no execution gate reads trust approvals: the store is wired at app/main.py:2383-2387 and only app/api/trust_governance.py uses it. POST /trust/approvals (:194-231) records goal_id/tool_name that nothing consumes
- [ ] bundle required_hitl_for is never enforced: requires_hitl_for_tool_on (app/governance/compliance_bundles.py:314) has no caller anywhere in app/
- [ ] GET /trust/compliance-bundles lists the guardrails_v2 COMPLIANCE_BUNDLES catalogue (app/api/trust_governance.py:392-415, import at :396), a different catalogue from the one enable/disable accept (app/governance/compliance_bundles.py; :461-495)

### [PARTIAL] Audit trail (AuditLog, audit_log table, SIEM forwarding)

_6f7436498 fixed the evidence export: it now awaits query_db(tenant_ctx=), and query_db returns the SOC2 attribution columns. The DB write is still fire-and-forget and a failure is only logged. Each process also keeps an unbounded in-memory copy of every audit event._

- [ ] NEW app/governance/audit.py:64,80 — AuditLog.record appends every event to self._log[tenant_id] with no size limit, so each API replica's memory grows forever with audit volume. The comment at :307-330 bounds only the warm-up (severity: medium)
- [ ] the DB write is a fire-and-forget create_task (app/governance/audit.py:89-95), and a failure only logs 'DB audit record failed' (:165-166), so audit events can be lost silently
- [ ] audit_admin_action is still applied nowhere; it appears only in a docstring example (app/governance/audit_v2.py:559, 573)
- [ ] the CEF adapter uses blocking socket connect/send inside async send() on the event loop (app/governance/siem_adapters.py:192-198)

### [PARTIAL] Tamper-evident audit chains (PersistentAuditChain, AuditV3, deprecated audit_v2)

_Nothing changed. HashChainVerifier (GET /governance/audit/integrity/verify, app/api/governance.py:1977-2010) verifies the real audit_events chain under set_config RLS and fails closed with a 503. AuditV3.append still INSERTs columns that audit_events does not have. AuditV3 is the audit backend for the GDPR DeletionOrchestrator._

- [ ] NEW app/main.py:2360-2373 + app/lifecycle/deletion_orchestrator.py:397 — DeletionOrchestrator is wired with this AuditV3, so GDPR/DPDP deletion audit records are never persisted to Postgres (in-memory and logged only), and deletion is not provable in an audit (severity: medium)
- [ ] AuditV3.append INSERTs actor, actor_ip, delegation_chain_hash, previous_hash, entry_hash, event_timestamp, metadata_hash and sequence_num, none of which exist in audit_events (migration 0057_audit_rails_v2.py:22-49). It omits the NOT NULL event_type and uses ON CONFLICT (id) although the PK is (id, created_at). Every insert fails, and the failure is only logged as 'audit_v3_persist_failed' (app/governance/audit_v3.py:276-303)
- [ ] PersistentAuditChain (app/governance/audit_chain_store.py:34) has verify() but no route exposes it

### [PARTIAL] Policy engine (tool policies, propagation, versions, policy-as-code)

_71c10f6c0 fixed two gaps. A reload now rebuilds allowed_hours_utc/allowed_weekdays from the latest policy_versions snapshot, which is written in the same transaction as the policy, and a missing pattern becomes the fnmatch glob '*'. policy_rules are still CRUD/dry-run only, and TimePolicyEngine is still dead code._

- [ ] policy_rules have no use in app/agent or goal_service; they are only CRUD and dry-run (app/api/policy_rules.py)
- [ ] TimePolicyEngine is only a module singleton with no callers (app/governance/time_policy.py:170)
- [ ] Policy.timezone (app/governance/policies.py:49,146) is not included in the time windows rebuilt on reload (_time_windows_by_name returns only hours and weekdays), so a non-UTC window reverts to UTC on other replicas

### [PARTIAL] Permission matrix (default-deny tool permissions, per-agent permissions)

_fa379c2e6 now enforces daily_limit through reserve_daily_call (Redis INCR per tenant/agent/tool/day, failing closed on a Redis error). On the Celery worker path, however, the executor finds no Redis and falls back to a per-process counter. The workflow tool gate also never applies agent_permissions at all._

- [ ] NEW app/agent/nodes/executor_mixin.py:320-321 + app/scaling/tasks.py:2797-2802 — the executor reads Redis from _app_state.state._redis. The worker's SimpleNamespace _app_state has no _redis, so reserve_daily_call uses the process-local _LOCAL_DAILY dict (agent_permissions.py:188-194). On the worker path, which runs every queued production goal, 'N calls per day' becomes N per worker process and resets on restart (severity: medium)
- [ ] NEW app/agent/tool_gate.py:1-15 — GovernedToolGate (the workflow executor path, goal_service.py:3656-3662 and scaling/tasks.py:1000-1002) applies guardrails, the permission matrix, policies, grants, tool risk and cost, but never agent_permissions. Per-agent default-deny rules and per_goal_limit/daily_limit do not bind workflow tool calls (severity: medium)

### [PARTIAL] Cost budgets and enforcement (CostController, RedisCostController, budget APIs)

_The budget controllers now fail closed on a Redis error in every environment (80351a9bf). The Lua check-and-increment is atomic, and budget_configs are shared by all replicas and workers. However, the admin budget API accepts, persists and echoes per_agent_daily_usd and alert_pct_thresholds, and nothing ever enforces or evaluates either of them._

- [ ] NEW app/api/costs.py:43-46,289-310 + app/governance/cost.py:119-152 — PUT /costs/budgets (admin) persists per_agent_daily_usd and alert_pct_thresholds and echoes them back as if active. No code reads either one: per_agent_daily_usd appears only in the persist helper, and alert_pct_thresholds only in the API/migration. A per-agent daily cap never binds and configured alert thresholds never fire, so this is fake governance (severity: medium)
- [ ] try_record_and_check still fails open on non-budget Redis errors ('fail_open=True'; app/governance/cost.py:632-695). It has no callers, so it is dead but dangerous if anyone reuses it
- [ ] the budget alert only logs, at a hard-coded 79-81% band (app/governance/cost.py:280-290, 466-476). The Lua path used in production (:440-452) never evaluates the alert at all

### [PARTIAL] Guardrails v2 (content rules engine) plus v1 guardrail configs

_bbe8d8806 and 64b262847 fixed tenant regex (the regex engine with a timeout, failing closed) and made the toxicity rule able to trigger (a charged, circuit-broken LLM with the pattern classifier as floor). 96f171506 evaluates RAG_INGEST on the original text. Rule CRUD is still incomplete and best-effort. The RAG_INGEST gate fails open when the tenant's rules cannot load, and violations are process-local._

- [ ] NEW app/ingestion/pipeline.py:527-532 — the RAG_INGEST stage catches every exception and returns an allowed ScreenResult. guardrails_engine.evaluate raises GuardrailRulesUnavailableError when the tenant's rules cannot be loaded (engine.py:365-398, 454, 470), so on a rules-DB outage a document is indexed without the tenant's guardrail screening (fail-open) (severity: medium)
- [ ] NEW app/guardrails_v2/engine.py:242,494 + app/api/guardrails_v2.py:183-195 — violations are kept only in the process-local, unbounded _violations dict. GET /guardrails/v2/violations shows only this replica's violations since its last restart, and memory grows forever (severity: medium)
- [ ] NEW app/api/guardrails_v2.py:62-63,216-217 — POST /rules and POST /bundles/{name} have no require_role('admin'). Any write-capable (operator) key can add a tenant-wide BLOCK rule, such as a '.*' regex, that stops every agent step (severity: medium)
- [ ] NEW app/ingestion/pipeline.py:497-520 — REQUIRE_HITL results at RAG_INGEST are ignored (hitl_required is never checked), and a redacting rule causes a second evaluation that duplicates violations and LLM toxicity charges (severity: low)
- [ ] no update, delete or disable routes for v2 rules (app/api/guardrails_v2.py: only POST/GET /rules, evaluate, simulate, evaluate-corpus, violations, bundles, layers)
- [ ] POST /rules answers 'created' while persistence is a best-effort background flush (app/api/guardrails_v2.py:90-92; app/guardrails_v2/engine.py:280-297, 299-323)
- [ ] bundles mint a new uuid rule_id on every call, so re-enabling a bundle duplicates its rules (app/api/guardrails_v2.py:239)

## Knowledge & RAG

### [PARTIAL] Knowledge collections + direct (synchronous) text/file ingestion API

_Upload now streamed and capped at knowledge_max_upload_bytes=50MiB (413) and multi-format extraction with PDF page citations is real and fail-closed (415/422/503); 28+13 tests pass. Deletes still do not purge KG or semantic cache, and parse/embed of a 50MiB upload runs synchronously on the event loop in one request._

- [ ] NEW app/api/knowledge.py:814 - _extract_upload_segments (pypdf/openpyxl/docx parse of up to 50MiB) runs synchronously inside the async route, blocking the replica's event loop; no to_thread/worker offload [severity medium]
- [ ] NEW app/api/knowledge.py:847 - all chunks of an upload (a 50MiB text file ~ tens of thousands of chunks) are embedded in one _embed_texts_or_http call inside the request with no batching bound, per-tenant quota check or budget charge [severity medium]
- [ ] NEW app/ingestion/parsers/excel_parser.py:18-19,57-59 - XLSX silently truncated at 5,000 rows/sheet and 20 sheets; /ingest/file returns 201 with no truncation flag (c1f599ccd removed truncation from other parsers but not this one) [severity medium]
- [ ] NEW app/rag/store.py:580-586 - collection delete is one DELETE cascading all chunk rows in a single transaction; at millions of chunks this is a long lock/WAL spike with no batched purge [severity low]
- [ ] collection/document deletes do not purge knowledge_nodes/edges or semantic-cache entries (app/rag/store.py:561-592 delete_collection_async, 1465 delete_document_async)

### [PARTIAL] Repository (git clone) ingestion + durable knowledge ingestion jobs

_Repo ingestion is SSRF/limit-guarded with lease+heartbeat and stale-job reconciliation, but the work still runs as an in-process asyncio task on the API replica and repository DLQ rows are never replayed. 35 tests pass._

- [ ] repository DLQ rows (kind='repository') have no consumer; retry job marks them permanent_failure (app/api/knowledge.py:1089; app/ingestion/scheduler.py:374-382)
- [ ] repo ingestion runs as an in-process asyncio.create_task on the API replica, not a durable Celery task; a pod restart loses it (only lease reconciliation marks it stale) (app/api/knowledge.py:976-999)

### [PARTIAL] Legacy per-source ingest endpoints (PDF, DOCX, GitHub, Confluence, Jira, Slack, OpenAPI, URL, RPA-URL, Email, Notion, GDrive)

_email/notion/gdrive now build the orchestrator with the app embedder and embed_provider_resolver, and orchestrator screens PII/guardrail first. GitHub/Confluence/Jira/Slack still declare 202 but run synchronously with client-controlled, unbounded max_* counts; gdrive reports 'ingested' even when every file failed._

- [ ] NEW app/api/knowledge.py:118,130,140,148 - max_files/max_pages/max_issues/max_messages have no upper bound (plain int, no Field(le=)), so one synchronous request can fan out to arbitrarily many upstream fetches and embeddings [severity medium]
- [ ] NEW app/api/knowledge.py:2960-2985 - gdrive-folder swallows per-file exceptions into errors[] and returns status 'ingested' (HTTP 200) even when every file failed (fake success) [severity medium]
- [ ] NEW app/api/knowledge.py:2940-2946 - gdrive connector list_files/download_file are synchronous Google API calls on the event loop; service-account key written to a temp file on local disk [severity low]
- [ ] github/confluence/jira/slack declare status_code=202 but fetch+embed synchronously in the request (app/api/knowledge.py:1778-1900)

### [PARTIAL] Collection-scoped orchestrated ingestion + ingestion-time indexing strategies + collection sync

_a28ad4ec5 makes RAPTOR/agentic-chunking indexing work on real models (schema'd {items:[...]}, fence tolerant, 502 IndexingProviderError) and stops sending a foreign model id to the default embedder. Only two strategies are requestable, advanced chunkers still alias SemanticChunker, the indexing LLM is unmetered, and one integration test is now stale (fails)._

- [ ] NEW app/rag/indexing.py:425 - RAPTOR/agentic-chunking indexing calls dependency.provider.complete() directly (provider from app/api/knowledge.py:237-273): no tenant budget charge, no ledger, no timeout, no circuit breaker; max_tokens scales 400*batch. At millions of documents this is unmetered spend and a hung provider stalls the ingest request [severity high]
- [ ] NEW tests/rag/test_ingestion_time_strategies.py:330 - test_pipeline_rejects_incomplete_individual_completion_batch still expects 'incomplete batch' message; a28ad4ec5 changed it to IndexingProviderError('... returned N results for M inputs'), so the integration test fails [severity low]
- [ ] IndexingStrategy = Literal['raptor','agentic_chunking'] (app/api/knowledge.py:151)
- [ ] pipeline parent_child/sentence_window/fixed/agentic/agentic_chunking map to SemanticChunker (app/ingestion/chunkers/__init__.py:34-38)

### [PARTIAL] Ingestion framework: Sources CRUD/sync, 13-stage IngestionPipeline, job tracker, DLQ, Celery scheduler

_Framework is DB-backed with vault-encrypted source secrets, shared query embedder, PII identifier-only redaction (96f171506, guardrail now judges original text), and _index now raises instead of fake 'indexed'. Worker pipeline still lacks kg_hook, there are no sync-history/DLQ-retry routes, and the RAG_INGEST guardrail stage fails open on exception._

- [ ] NEW app/ingestion/pipeline.py:526-531 - RAG_INGEST guardrail evaluation exception is logged at warning and the document proceeds (fail-open), although _screen_or_http documents a scan failure as fail-closed 503; a guardrail outage lets GDPR/PCI-blocked content be embedded [severity high]
- [ ] NEW app/ingestion/pii.py:200-204,208-222 - redact rebuilds the whole string per finding (O(N*F)) and overlap check scans all prior findings (O(F^2)); pathological for large documents with many identifiers [severity medium]
- [ ] NEW app/ingestion/pii.py:170-177 - PHONE rule redacts any 10-15 digit run starting 6-9 (order/part numbers, IDs) [severity low]
- [ ] NEW app/api/ingestion.py:302 - manual sync runs via FastAPI BackgroundTasks in the API process, not a durable Celery task [severity medium]
- [ ] worker pipeline built without kg_hook, so scheduled/DLQ syncs never populate the KG (app/ingestion/scheduler.py:104-109)
- [ ] no sync history or DLQ retry routes; module docstring still advertises /sync/cancel, /reindex, /validate that do not exist (app/api/ingestion.py:1-18, routes 132-580)

### [PARTIAL] Ingestion connectors (app/ingestion/connectors: 42 modules, 48 registered source_type keys)

_Azure Blob is now egress-guarded: every BlobEndpoint/secondary/proxy URL and the account name/suffix are derived and checked with assert_source_url, and development storage is refused, in both validate_connection and get_delta (ca49331db, 24 tests). Every tenant URL/host connector is now guarded. Remaining: driver DNS-rebinding window, DuckDB hardening only mock-tested (duckdb still not installed), two parallel ingestor stacks, and the Azure connector downloads whole blobs with no size check._

- [ ] NEW app/ingestion/connectors/azure_blob_connector.py:185 - download_blob(...).readall() pulls each blob fully into memory with no max_doc_size_bytes check (S3 connector checks at s3_connector.py:137); the pipeline only rejects after download, and the sync SDK calls run on the event loop [severity medium]
- [ ] NEW app/ingestion/connectors/azure_blob_connector.py:158-160 - get_delta returns silently (zero documents, sync reported successful) when azure-storage-blob is not installed; validate_connection reports it but a scheduled sync does not [severity low]
- [ ] driver-level connectors (SQL/NoSQL/Kafka/MQTT/IMAP/Azure SDK) check the host, then the driver resolves again (validate-then-connect DNS-rebinding window) (app/ingestion/connector_egress.py; e.g. azure_blob_connector.py:157-172)
- [ ] DuckDB confinement verified only against a mocked duckdb module (duckdb not installed in this env) (app/ingestion/connectors/duckdb_connector.py:44-85)
- [ ] two parallel ingestor stacks remain (app/knowledge/ingestors/* used by app/api/knowledge.py:1778-1900 vs app/ingestion/connectors/*)

### [PARTIAL] Parsers and chunkers

_c1f599ccd removes the silent truncation from the HTML/CSV/notebook/YAML/JSON parsers and fixes pretty-printed JSON being parsed as JSONL; 66+15 tests pass. The notebook parser is still unused by the registry, advanced chunk strategies still alias SemanticChunker, the quality score is still binary, and ExcelParser still truncates silently._

- [ ] NEW app/ingestion/parsers/excel_parser.py:18-19,57-59 - ExcelParser still silently truncates at 5,000 rows/sheet and 20 sheets, contradicting c1f599ccd's no-truncation contract [severity medium]
- [ ] NEW app/ingestion/parsers/json_parser.py:69-73 - JSONL lines that fail to parse are skipped with no count or warning (silent data loss) [severity low]
- [ ] NOTEBOOK mapped to TextParser in the pipeline registry (the upload path now uses NotebookParser, the connector pipeline does not) (app/ingestion/parser_registry.py:165)
- [ ] dom/paragraph/fixed/parent_child/sentence_window/agentic/agentic_chunking -> SemanticChunker (app/ingestion/chunkers/__init__.py:16,22,34-38)
- [ ] _quality_score binary 1.0/0.1 and 1.0 on exception (app/ingestion/pipeline.py:576-586)
- [ ] late_chunker, contextual_enricher, context_manager still have zero importers in app/

### [PARTIAL] Retrieval gateway + 20 canonical RAG strategies (search, federated search, RAG chat, /rag/query)

_Corrective (confident single passage suffices, web outage falls back to graded KB evidence recorded as failed in the trace), speculative (drafts from evidence subsets, claims requested), web-augmented (rank interleave) and synthesis (fair per-citation budget) are fixed and tested (25+38+2+50 tests). RAFT no longer degrades to hybrid. Still open: federated fan-out uncapped, 1536-d fallback, and retrieval-time LLM calls have budget but no timeout or breaker._

- [ ] NEW app/rag/gateway.py:297-301 - _BudgetedProvider charges the budget but applies no timeout or circuit breaker, so flare/self_rag/speculative/modular/agentic retrieval LLM calls (app/rag/agentic/patterns/*.py) can hang a request indefinitely; only corrective uses complete_decision [severity medium]
- [ ] NEW app/rag/agentic/patterns/speculative.py:113-115 - each draft concatenates its whole evidence subset with no context-length bound (top_k up to 100) [severity low]
- [ ] federated search passes uncapped collection_ids to asyncio.gather fan-out (app/api/knowledge.py:1912,1933-1941 -> app/knowledge/federated_search.py:153); /knowledge/chat caps at 10 but /federated-search does not
- [ ] engine metadata failure/NULL assumes embedding_dim=1536 in non-strict mode (app/rag/engine.py:390,394)
- [ ] app/rag/evaluation.py, app/rag/certification.py still have zero importers in app/

### [PARTIAL] Rerankers

_No commit in range touched the rerankers; the default-path rerank tests pass. The 'llm' strategy still aliases the cross-encoder, and a default-path rerank failure still passes through, logged only at debug._

- [ ] _llm_rerank_sync just calls _cross_encoder_rerank (app/context/rerank_policy.py:513-515)
- [ ] default-path rerank failure passthrough logged at debug (app/rag/rerank_stage.py:114)
- [ ] without sentence-transformers auto/cross_encoder degrade to TF-IDF, only metadata records it (app/context/rerank_policy.py)

### [PARTIAL] Embeddings platform (embedder wiring, content-routed model selection, health/drift, re-embedding)

_Unchanged in range for embeddings. The re-embed task still uses the process-global embedding_router and has no API or beat caller. The router ignores the requested model, and /embeddings/usage serves process-local, cross-tenant counters._

- [ ] NEW app/api/embeddings.py:117-123 + app/embedding/router.py:67,134,164-165 - GET /embeddings/usage returns the process-global _usage/_errors dicts: aggregated across ALL tenants (cross-tenant disclosure of usage by model) and per-replica only, so numbers differ by pod and reset on restart [severity medium]
- [ ] re_embed_collection uses embedding_router with no worker provider; no API/beat caller (app/scaling/tasks.py:6815-6976, embedding_router at 6846,6911)
- [ ] embed_texts_report ignores requested model (EmbedRequest(texts=texts)) (app/embedding/router.py:127)
- [ ] non-production fallback still selects 'fake-embedding' dim 10 (app/embedding/orchestrator.py:190-197)

### [PARTIAL] Semantic cache, embedding cache, LLM response cache

_Unchanged in range. SemanticCache writes through to pgvector with TTL, but nothing consumes 'knowledge.updated', so cached answers survive ingest, delete, retention and re-embed. The per-replica in-process L1 is never invalidated across pods._

- [ ] no subscriber to 'knowledge.updated'; cached answers survive ingest/delete/re-embed/retention (publisher app/ingestion/pipeline.py:811-826; no consumer in app/)
- [ ] warm path stores response='' placeholder entries (app/rag/semantic_cache.py:506)
- [ ] per-tenant in-process L1 LRU (app/rag/semantic_cache.py:98-118) is replica-local and has no cross-replica invalidation

### [PARTIAL] Tenant knowledge graph + GraphRAG + ingestion auto-population

_Unchanged in range. The KG is SQL-backed and retention cascades to it. Auto nodes (doc:idx) still never match GraphRAG seeds, collection/document deletes and worker syncs skip the KG, and /extract makes unmetered LLM calls even when use_llm=false. Pipeline KG extraction is deterministic, because kg_provider is never wired, so ingestion itself incurs no KG LLM spend._

- [ ] NEW app/knowledge_graph/extractor.py:92,154 via app/api/knowledge_graph.py:51-68 - KG LLM extraction calls self._provider.complete() directly: no tenant budget charge, no timeout, no circuit breaker (the same path is used by KGIngestionHook.process whenever a provider is passed, app/knowledge_graph/ingestion_hook.py:225-230) [severity medium]
- [ ] NEW app/api/knowledge_graph.py:51-53 / app/knowledge_graph/ingestion_hook.py:226 - per-request set_provider() mutates the module-global entity_extractor / shared hook extractor; concurrent requests race on which provider is used [severity low]
- [ ] auto nodes source_id='{doc}:{idx}' never match GraphRAG seeds (app/knowledge_graph/ingestion_hook.py:221)
- [ ] collection/document deletes do not cascade to KG (app/rag/store.py:561,1465); worker syncs have no kg_hook (app/ingestion/scheduler.py:104-109)
- [ ] /extract always calls extract_relationships_llm even when use_llm=false (app/api/knowledge_graph.py:66-68)
- [ ] GraphAccessControl role matrix used only by tests; stale TODO app/knowledge_graph/ingestion_hook.py:18

### [PARTIAL] RAFT (retrieval-augmented fine-tuning) lifecycle

_RAFT can now serve end to end: build_raft_service wires fine-tune + inference providers for the in-memory API gateway (main.py:881-900), the DB lifespan gateway (main.py:1780-1804) and the Celery worker gateway (scaling/tasks.py:356-359, 2585); a deployed job is resolved per collection and the adapter raises instead of degrading to hybrid; poll-raft-fine-tune-jobs is in the beat schedule and routed to maintenance; load_chunks keyset-pages to a cap. Remaining: OpenAI-only and RAFT inference is unmetered/unbreakered._

- [ ] NEW app/rag/raft_inference.py:69-78 / app/rag/agentic/patterns/raft.py:94 - RAFT inference uses a provider from instantiate_configured_provider, not the gateway's _BudgetedProvider (app/rag/gateway.py:2208-2213): serving and evaluate_job (up to 50 calls) are not charged to the tenant budget and have no timeout or circuit breaker [severity medium]
- [ ] NEW app/api/rag_platform.py:278-345 - paid fine-tune submit and deploy have no per-route role check; only the unregistered-write fallback (admin/operator) in app/auth/scope_enforcement.py:322-331 guards them [severity low]
- [ ] OpenAI is the only fine-tune/serving provider (app/rag/raft_openai_provider.py:169-184; app/rag/raft_inference.py:33)

### [PARTIAL] Knowledge maintenance and delta re-ingest tasks

_Retention is bounded (scan_batch x max_batches) on the maintenance role with KG cascade, and delta re-ingest is honest; tests pass. re_embed_collection is still dead code in production, and retention does not invalidate the semantic cache._

- [ ] re_embed_collection has no production caller and no worker provider (app/scaling/tasks.py:6815-6976; see Embeddings)
- [ ] chunk expiry does not invalidate semantic-cache answers built from expired chunks (app/rag/retention.py:42-100; see Semantic cache)

## Memory & intelligence

### [PARTIAL] Long-term memory (LTM)

_store_async now fails closed. A guardrail evaluation error raises LongTermMemoryUnavailableError (long_term.py:428-436), and a DB write failure evicts the cached entry instead of returning a phantom id (long_term.py:519+). Embedding failures are logged, not silently swallowed (long_term.py:464-469). 0aa658fc9 adds a DB-authoritative PATCH /memory/{id} under RLS. GET /memory/long-term still lists only the per-process cache._

- [ ] NEW app/api/memory.py:397-431 PATCH rewrites long_term_memory.content with no MEMORY_WRITE guardrail screening (the single choke point store_async enforces) and without re-embedding, so edited content bypasses the guardrail and keeps a stale vector [medium]
- [ ] NEW app/memory/long_term.py:438-439 _publish_created fires before the DB write, so a 'memory.created' event is emitted for a memory that is then rolled back on DB failure [low]
- [ ] app/api/memory.py:263-284 GET /memory/long-term reads mem.list_all (per-process cache, long_term.py:155-173), so other replicas' or pre-restart memories are invisible

### [PARTIAL] Execution memory (winning plans and failures)

_execution.py is unchanged. Recall reads the DB with a degraded flag, but GET /memory/execution still reads the per-process dict, DB writes only log on failure, and startup hydration runs without a tenant GUC._

- [ ] app/api/memory.py:287-295 GET /memory/execution reads exec_mem._memories (per-process); the DB is never read
- [ ] app/memory/execution.py:155-158,201-204 DB write failures only logged (win/failure silently not persisted)
- [ ] app/main.py:1467-1478 hydration 'SELECT DISTINCT tenant_id FROM execution_memory LIMIT 50' on the app session with no RLS/system context (returns 0 rows under FORCE RLS; LIMIT 50 caps tenants hydrated)
- [ ] app/memory/execution.py:6 docstring still says 'in production this would be backed by PostgreSQL'

### [PARTIAL] Canonical governed memory records and Reflexion service

_b5ef7de50 closes the loop. Every terminal goal on the API path (goal_service.py:3121,3330,3340,3371 into 3377-3408) and the worker path (tasks.py:3090,3109 into 832-851) calls learn_from_goal_outcome. That call records effectiveness feedback, screens the lesson through MEMORY_WRITE (fails closed), and writes an idempotent lesson under RLS. b8ed5bc52 persists agent/collection/source scope, seals sensitive payloads with a tenant- and id-bound vault envelope plus a CHECK constraint, and replaces the 500-row Python scan with two bounded, ordered SQL candidate queries. 20dae356f gives the repository the app embedder on both paths, and the RLS integration test passes on real Postgres. Remaining: at-scale ANN/trigram index behaviour, and the backfill has no embedder or schedule._

- [ ] NEW app/db/migrations/versions/c8d2f4a6b1e3_memory_records_scope_sealed_recall.py:58-61 + postgres_repository.py:87-92: one global HNSW index over all tenants' vectors, with tenant/kind/lifecycle filters applied after the ANN scan. At multi-tenant scale a small tenant's recall can return few or zero rows (ef_search window exhausted by other tenants' vectors; no iterative scan or per-tenant partitioning) [medium]
- [ ] NEW app/memory/postgres_repository.py:93-103 lexical path ORDER BY similarity(safe_summary, query) has no pg_trgm GIN index on memory_records.safe_summary, so every recall without an embedder computes similarity for all of the tenant's eligible rows [medium]
- [ ] NEW app/memory/embedding.py:40-47 model_id is f'{type(provider).__name__}:{model}', so if the API and worker wrap the embedder differently their vectors are never compared and recall silently degrades to lexical [low]
- [ ] NEW app/memory/postgres_repository.py:165 a write whose embedding fails is stored vector-less, and nothing re-embeds it later [low]
- [ ] app/scaling/tasks.py:5846 backfill task builds PostgresMemoryRepository(db) without an embedder (backfilled lessons are lexical-only), and it is per-tenant with no beat entry or API caller (manual enqueue only)

### [PARTIAL] Episodic and procedural memory (plus voyager skills)

_Unchanged in the range. SQL recall runs under RLS and is bounded by LIMIT. A DB recall error still silently falls back to the per-process cache._

- [ ] NEW app/memory/episodic.py:300-316 relevance is a Python cosine re-rank over a quality/recency-ordered candidate window (max(limit*3, _CANDIDATE_WINDOW)), so at scale a relevant low-quality or old episode outside the window is never recalled (no SQL-side vector ordering) [low]
- [ ] app/memory/episodic.py:271-272 and procedural.py:182-183 DB recall error goes to except: pass and then the per-process cache (stale/empty on other replicas, not flagged degraded)
- [ ] app/memory/voyager_skills.py referenced by no app module (tests only)

### [PARTIAL] Department memory (org)

_Unchanged. Org CRUD uses DB-backed department memory, but planner and MCP injection still construct DepartmentMemory() with no DB and call retrieve() without a tenant_id._

- [ ] app/agent/graph.py:673-674 DepartmentMemory() without DB, retrieve() without tenant_id, so the planner gets no department memory
- [ ] app/gateway/mcp_server/__init__.py:640-641 same for the MCP search fallback
- [ ] app/memory/dept_memory.py:148-155 'semantic search' is keyword overlap
- [ ] app/memory/dept_memory.py:74,141,220 no-DB _store keyed by dept_id only (not tenant); module singleton at :415

### [PARTIAL] Tool reliability memory

_Unchanged. Reliability SQL runs under RLS, but the self-improvement BLACKLIST action still writes a synthetic 5000 ms failure into the real reliability stats._

- [ ] app/agent/nodes/verifier_mixin.py:648-651 BLACKLIST records a fake failure (latency_ms=5000.0, error='blacklisted_by_self_improvement') into real tool stats

### [PARTIAL] Context engineering pipeline (app/context)

_Unchanged. ContextPipeline still runs only when retrieved chunks or rag_context exist, its errors are swallowed with a warning, and the semantic-cache source is an exact text-key lookup._

- [ ] app/agent/nodes/planner_mixin.py:175-178 pipeline gated on retrieved_chunks, so memory/graph/semantic-cache context is skipped without RAG hits
- [ ] app/agent/nodes/planner_mixin.py:243-246 any pipeline error swallowed with a warning
- [ ] app/context/context_sources.py:75-85 semantic-cache source is an exact text-key lookup, no embedding

### [PARTIAL] Goal eval scoring (EvalRunner, 7 dimensions)

_Completion-path scoring persists through score_and_persist with the app provider (goal_service.py:2583-2593). abbc0b8d9 reports 'pending' while scoring runs, and eval LLM calls are now charged and circuit-broken through complete_decision (eval_runner.py:257,316; 64b262847). GET /eval still reads only the per-process dict, although scorecards are persisted._

- [ ] NEW app/services/goal_service.py:566,2591,4673 _eval_scores (and _eval_pending) grow per goal forever with no eviction: an unbounded per-replica memory leak at millions of goals [medium]
- [ ] app/services/goal_service.py:4665 POST /eval uses getattr(self,'_app_provider'), which is None on GoalService (heuristic-only scoring); result cached in memory only (4673)
- [ ] app/services/goal_service.py:4536,4564 GET /eval and /eval/suggestions read the _eval_scores dict only, although score_and_persist wrote the row, so another replica or a restart reports not_evaluated; _eval_pending is also per-process

### [PARTIAL] Eval suites and golden tasks, agent rollout gate

_Unchanged. Suites are durable and tenant-scoped with a fail-closed judge. A golden-task timeout is still swallowed and the task scored on partial events, and the rollout gate ignores eval_suite_id._

- [ ] NEW app/intelligence/eval_suite.py:327-336 on timeout the submitted golden goal is not cancelled and keeps running and spending after it has been scored [low]
- [ ] app/intelligence/eval_suite.py:327-336 60 s timeout swallowed ('except (TimeoutError, Exception)'), so the task is scored on partial events
- [ ] app/intelligence/eval_suite.py:591-625 check_agent_rollout_gate ignores eval_suite_id and averages any evaluations of the agent's goals (30 days); the pass threshold is hardcoded at 0.8 in SQL

### [PARTIAL] Self-optimizer v1 (suggestions, deprecated)

_Unchanged. The fake apply is gone (410), but persist_suggestion runs without an RLS context and swallows failures, and /suggestions reads per-process lists._

- [ ] app/intelligence/self_optimization.py:372-418 persist_suggestion opens a session with no RLS GUC (FORCE RLS rejects it under the app role); failure swallowed at :416
- [ ] app/intelligence/self_optimization.py:48,271-287 /suggestions reads per-process _suggestions
- [ ] app/intelligence/self_optimization.py:80-86 placeholder prompt text registered only in the in-memory registry

### [PARTIAL] Self-optimizer v2 (Bayesian A/B config experiments)

_No change since 2fbdcd86c. Experiments start and winners are written to real agents columns, but API-replica runs read system_prompt from the per-process AgentStore cache, so an applied winner has no effect there until restart. Tests are sqlite and mocks only._

- [ ] app/intelligence/self_optimizer_v2.py:~749 winner/rollback UPDATE agents directly, while AgentStore.get (api/agents.py:143) is memory-only and sync_from_db skips cached keys (api/agents.py:176), so there is no effect on API-replica runs until restart
- [ ] app/agent/nodes/verifier_mixin.py:769-795 on_goal_completed only called when an eval scorecard exists
- [ ] tests use sqlite/mocks; no Postgres/RLS test for improvement_experiments or the agents UPDATE

### [PARTIAL] Benchmarking

_Unchanged. Benchmarks query the real schema under tenant RLS, and missing data yields null/insufficient_data. The platform aggregate is unreachable under FORCE RLS, and your-metrics DB errors become nulls._

- [ ] app/api/enterprise.py:1109-1118 platform aggregate runs with no GUC under FORCE RLS, so it always reports insufficient_data on the app role
- [ ] app/api/enterprise.py:1106-1107 your-metrics DB error swallowed to nulls instead of 503
- [ ] app/intelligence/benchmarking.py unused by app code

### [PARTIAL] Verifier calibration, learning experiments, experiment registry, cost optimizer

_Unchanged. Feedback linkage still scans per-process calibration records and swallows errors, learning_experiment_service has no consumer, and false_confirm_rate is exposed by no route._

- [ ] app/api/goals.py:1285-1290 feedback scans per-process _default_calibration_store._records; errors swallowed
- [ ] app/main.py learning_experiment_service wired with no consumer
- [ ] verifier_calibration.py:145 false_confirm_rate exposed by no route; experiment_registry test-only

### [NOT_IMPLEMENTED] Prospective memory (deferred intentions)

_Unchanged. PostgresProspectiveMemoryService is wired (main.py:1316-1325) and read by the planner, but no API or agent tool creates intentions. process_due_memories/purge_expired_memories are plain async functions with no Celery task or beat entry._

- [ ] No API/agent tool creates ProspectiveMemory (only goal_service.py:1592 passes the service for reads)
- [ ] app/scaling/memory_tasks.py:19,40 not Celery tasks; no beat entry in celery_app.py, so the feature is inert

### [NOT_IMPLEMENTED] A/B testing engine (app/optimization)

_Unchanged. get_arm_stats, can_promote_variant and get_experiment_arm have no app callers outside app/optimization, so the engine is write-only telemetry._

- [ ] no app callers of get_arm_stats/can_promote_variant/get_experiment_arm, so no A/B decision is ever made
- [ ] app/agent/nodes/verifier_mixin.py:807-808 records every result as RAG_STRATEGY with arm=_experiment_arm (defaults to 'control')

### [PASS] Working memory, salience and consolidation helpers

_Stateless helpers, unchanged, with passing unit tests. knowledge_graph_memory.py is still referenced only by orchestration/strategy_registry.py:1584, which is dead code with no correctness impact._

- [ ] NEW app/memory/consolidation.py calls provider.complete() directly (no circuit/timeout/budget charge) [low]
- [ ] knowledge_graph_memory.py only referenced by orchestration/strategy_registry.py:1584 (dead code, non-blocking)

### [PASS] Runtime scorecard, regression gate and self-improvement engine (app/evals)

_a723c3810 fixed a regression: once the strategy rollout gate became real, scorecards had stopped persisting for tenants off the v2 allowlist. Scoring now uses an observe-only _observed_runtime_profile, while execution still reads the gated profile. Feedback lessons still go through the guardrail-vetted, RLS-scoped store_async._

- [ ] minor: the lesson is committed in its own transaction before the batch's processed_at UPDATE, so if the outer commit fails the row is reprocessed and a duplicate lesson stored
- [ ] minor: lessons stored without an embedder, so they are keyword-recall only

### [PASS] AI-Ops eval datasets, judges and drift

_Unchanged. Dataset runs execute real goals, judges fail closed (now also charged, with timeout, via guardrail_engine.py:396 complete_decision), and state is durable under RLS. Only the per-replica run body remains (stale runs are marked abandoned)._

- [ ] run body is a per-replica background task (stale runs are marked abandoned): acceptable but not durable

### [PASS] Prompt optimizer (prompt variants A/B)

_Unchanged. Variants are tenant-scoped under RLS with gated promotion, and the report returns honest nulls for metrics it does not compute._

- [ ] app/api/enterprise.py:1502 report hard-codes win_rate/statistical_significance None (honest, not computed)
- [ ] app/intelligence/prompt_optimizer.py legacy non-DB persist_variant/persist_outcome have no RLS and swallow errors

### [PASS] Meta-agent (NL command to agent config)

_Unchanged. Heuristic drafts are labelled and are not created without accept_heuristic. policy_suggestions are still only returned, never applied._

- [ ] app/api/agents.py:854 policy_suggestions returned but never applied

## Workflows & triggers

### [FAIL] Workflow run execution (runner, compiler, steps, Celery, run control)

_Runs now go to the tenant's real plan queue (b20954cf8). But I reproduced a fail-open gate in the compiler. For a hitl step whose actions have no `next`, plain edges run the downstream steps in the same invocation while the run is WAITING_HITL and the approval is still pending, and node_fn has no WAITING_HITL guard. Probe: gate(hitl approve/reject) → after(set_variable) gave status waiting_hitl, step_outputs=['after'], pending approvals=1._

- [ ] NEW workflow/compiler.py:186-190 + :215-253 — a hitl step with no per-action `next` gets plain edges and node_fn never checks status==WAITING_HITL, so downstream steps execute before (and regardless of) approval; a 'reject' also proceeds (severity: high)
- [ ] NEW workflow/steps/hitl_step.py:72-74 — without hitl_workflow_gateway it silently enters 'test mode' (no approval record) and suspends forever (severity: low)
- [ ] workflow/service.py:478-497 stream_run_events is a one-shot snapshot (emits 'run_failed' for non-terminal runs)
- [ ] workflow/compiler.py:73-96 per-process unbounded compiled-graph cache

### [FAIL] Workflow HITL approvals (approval inbox)

_The escalation sweep exists and is scheduled, and delegate/escalate are tenant-scoped (b20954cf8). But the approval gate itself is fail-open for the common hitl-step shape (actions without `next`): downstream steps run while the approval is still pending (reproduced). Round-robin and skill-based assignment still return None._

- [ ] NEW workflow/compiler.py:186-190 — the HITL gate doesn't block downstream steps when no action defines `next` (run shows waiting_hitl with downstream outputs already produced) (severity: high)
- [ ] NEW workflow/hitl_extension.py:641-651 — least_busy counts only the process-local _store, so assignment differs per replica (severity: low)
- [ ] workflow/hitl_extension.py:637-639,653-655 round_robin/skill_based assignment return None

### [PARTIAL] Celery app, per-plan queue routing and worker topology

_Both prior gaps are closed: the stuck-goal scan now uses each plan's goal_timeout_seconds, and beat_guard releases its lock with an owner-token compare-and-delete. Queue routing and worker coverage are enforced by tests. Two problems remain: the broker has no visibility_timeout although acks_late is on and goals run for hours, and beat_guard cannot parse a Sentinel URL._

- [ ] NEW scaling/celery_app.py:72-83 — task_acks_late=True with no broker_transport_options['visibility_timeout'] (Redis default is 1h, the Sentinel branch at :341-345 doesn't set it either), while plan goal timeouts are 2h/8h/24h, so every long goal's message is redelivered hourly (severity: medium)
- [ ] scaling/beat_guard.py:54-66 redis.from_url(celery broker_url) raises on the sentinel:// scheme (celery_app.py:15-36), so with Sentinel every guarded beat task runs unguarded

### [PARTIAL] Goal queue and Celery goal execution (run_goal / DLQ)

_The lock now fails closed (8dfc8c77b), cancel/pause reach workers from any replica and a cancel ends as cancelled rather than retry/DLQ (ec5a3c12a), and usage is metered (54384e45c). But run_goal never checks the goal's durable status before it starts: a redelivered or late duplicate of an already-finished goal re-executes it, and nothing prevents that redelivery (1h visibility timeout; cancel flag TTL is 2h)._

- [ ] NEW scaling/tasks.py:1938-1941,2051 — mark_worker_started writes 'executing' without only_if_active and there is no terminal-status guard, so a redelivered or duplicate run_goal message (acks_late + 1h visibility timeout; lock released in finally at :3238-3241 before ack) re-executes a completed or cancelled goal (severity: high)
- [ ] NEW reliability/goal_lifecycle.py:33 — the cancel flag TTL is 2h and the worker's pre-exec check (tasks.py:2861) reads only Redis, so a goal cancelled while queued behind a backlog longer than 2h runs anyway (severity: medium)
- [ ] NEW scaling/tasks.py:2084-2094 — early returns before the main try (BYOK failure, AgentGraph assembly failure, pre-exec cancel) never release the goal lock, so it stays held for plan timeout + 5 min (severity: low)
- [ ] scaling/tasks.py:1741-1742 run_goal docstring still says the status bridge is 'deferred until Phase 10' (stale)
- [ ] No e2e test runs a goal through the real Celery run_goal path (tests/integration/test_agent_graph_full_run.py drives AgentGraph directly)

### [PARTIAL] Durable execution / LangGraph checkpointing

_Unchanged. API-side DB checkpoint resume works, but Celery workers, which run every queued production goal, still use MemorySaver. A retry or restart therefore re-runs the goal from scratch. 8dfc8c77b improved orphan recovery; it does not make worker execution durable._

- [ ] scaling/tasks.py:91,95-111 _WORKER_CHECKPOINTER stays None (MemorySaver); passed at tasks.py:2689 and workflow/celery_tasks.py:36,123
- [ ] scaling/tasks.py:30-43 SIGTERM handler logs 'LangGraph checkpoint written after last completed step', which is false for MemorySaver
- [ ] agent/graph.py:269-282 silently swaps a sync-only or unusable saver for MemorySaver

### [PARTIAL] Trigger/schedule CRUD and ScheduleStore

_POST /schedules now calls is_supported and validate_spec (0d52e3caa). But NL creation still stores unvalidated specs, there is still no plan cron floor, and most /schedules routes read the per-process cache instead of Postgres. On multiple replicas, a schedule created on another pod 404s for GET, pause, resume, fire, the alert webhook and the token webhook, and is missing from the list._

- [ ] NEW api/schedules.py:161,321,346,359,373,478,521 — list/get/pause/resume/fire/alert/webhook use the sync process-local ScheduleStore cache (store.py:750-754) instead of get_async/list_all_async, so a schedule created on another replica is invisible or 404s until restart (severity: high)
- [ ] NEW api/schedules.py:95-99,234,446 — process-local app.state._webhook_tokens map is still written (dead, per-replica state) (severity: low)
- [ ] NEW api/schedules.py:161 — GET /schedules loads every tenant schedule and slices in Python (limit/offset not pushed to SQL) (severity: low)
- [ ] api/schedules.py:417-450 POST /nl/schedule stores nl.parse() specs without validate_spec/is_supported; nl_scheduler.py:504 last-resort ONCE spec has no fire_at
- [ ] triggers/models.py:246-258 validate_cron has no plan-tier minimum interval (docstring claims one) and is skipped entirely if croniter is missing

### [PARTIAL] TriggerDispatcher governance pipeline

_Unchanged since the prior rating (no commits touch app/triggers). The pipeline is real: two-layer dedup that fails closed on Redis errors, fail-closed conditions, skip rows audited, enqueue failure goes to the DLQ. The RBAC step is still a no-op and circuit breakers are still per process._

- [ ] NEW triggers/dispatcher.py:209-252 — the Redis dedup key (SET NX, 60s TTL) is claimed before the rate-limit, circuit and bulkhead checks, so a firing skipped as rate_limit/bulkhead_full/circuit_open burns its key and a legitimate redelivery within 60s is deduped (severity: low)
- [ ] triggers/dispatcher.py:91,153,168 caller_role defaults to 'operator' and no caller passes it, so the RBAC step always passes
- [ ] triggers/circuit_breaker.py:76-85 CircuitBreakerRegistry is an in-process dict (per replica); bulkhead released right after enqueue (dispatcher.py:376-377)

### [PARTIAL] Beat schedule loop (time family A plus polling/data triggers)

_Worker goals from scheduled triggers enqueue to run_goal, and interval, cron and missed-run logic is tested. The RRULE/SOLAR branches are still dead. Each tick does O(all schedules) work: a full Redis SCAN plus one GET per key, then an N+1 per-tenant load of every unpaused schedule. That won't finish at million-schedule scale, and Postgres discovery is off by default._

- [ ] NEW scaling/tasks.py:4509-4521,4096-4117 — every fire_due_schedules tick SCANs all schedule:* keys (one GET each) and runs one query per active tenant loading all unpaused schedules; no due-time predicate or index, so work is O(total schedules) per minute (severity: medium)
- [ ] NEW scaling/tasks.py:4156-4163 — Postgres schedule discovery is opt-in (AGENTVERSE_DB_SCHEDULE_DISCOVERY default false), so Redis is the default source of truth and evicted keys stop firing until an API restart re-syncs (severity: medium)
- [ ] scaling/tasks.py:4650-4680 rrule/solar branches are unreachable: RRULE/SOLAR are not TriggerType members (triggers/models.py:28-90)

### [PARTIAL] Event-bus trigger consumers (TriggerConsumerSupervisor)

_HITLGateway now gets the shared Redis in the lifespan (wire_hitl_runtime, 15f74ea63), so hitl.approved/rejected are published in production. The worker HITLGateway also gets an async Redis. Goal-chain, memory.created and state_machine.transition publishers are wired and dispatch is deduped. MQTT remains unwired, and worker-written memories and non-API state transitions still publish nothing._

- [ ] triggers/supervisor.py:207 mqtt_client: Any = None  # not wired by default, so the MQTT consumer is always skipped
- [ ] scaling/tasks.py:2290 worker LongTermMemoryStore() has no event redis, so worker-side memories never publish memory.created; triggers/state_machine.py transitions outside POST .../transition publish nothing

### [PARTIAL] Inbound webhook and channel trigger ingress (triggers/schedules/channels)

_The typed webhook path (/triggers/webhooks/{type}/{token}) is correct across replicas: it resolves the tenant from the token (in-memory, then a DB fallback), reads triggers from the DB, verifies Stripe t=/v1= signatures with a tolerance, and answers 503 when every dispatch fails. The legacy /schedules/webhooks/{token} ingress still reads only the local cache, so it 404s for triggers created on another replica._

- [ ] NEW api/schedules.py:521-529 — POST /schedules/webhooks/{token} scans the sync per-process store.list_all(), so a webhook trigger created on another replica returns 404 (severity: medium)
- [ ] NEW triggers/store.py:794-797 — every pre-auth typed-webhook request linearly scans ALL tenants' cached schedules with compare_digest before the indexed DB lookup: O(N) per delivery at scale (severity: low)

### [PARTIAL] External integrations: Slack, Zapier, Alertmanager/Datadog event bus, re-ingest webhooks, IMAP email

_Slack signature checks now fail closed in every environment (df8f6b7a0), Alertmanager needs a bearer token, Datadog an HMAC, and re-ingest webhooks a signature. The remaining problems: goals from these single-tenant env integrations run with a hard-coded PROFESSIONAL plan whatever the tenant's tier. Alertmanager and Datadog also bypass TriggerDispatcher (no dedup or rate limit), and Slack HITL buttons approve with no approver authorization._

- [ ] NEW api/integrations.py:92-96,147-151,276-280,351-355,421-425 — Slack/Zapier/Alertmanager/Datadog build TenantContext(plan=PROFESSIONAL), so a free tenant's goals get professional timeouts, queue and limits (severity: medium)
- [ ] NEW api/integrations.py:330-373 — Alertmanager goals are submitted directly (not via TriggerDispatcher), with no fingerprint dedup or rate limit, so each repeat_interval re-send creates duplicate autonomous goals (severity: medium)
- [ ] NEW api/integrations.py:133-160,203-230 — Slack approve_hitl/reject buttons decide HITL requests as any Slack user; there is no mapping to an AgentVerse principal and no approver role check (severity: medium)
- [ ] NEW api/integrations.py:429-445 — Datadog answers 200 'processed' with goal_id null when goal creation fails, so Datadog never retries (severity: low)

### [PARTIAL] Messaging gateway (Telegram / WhatsApp / Slack / Teams / generic webhook, /v1/gateway)

_Cross-tenant binding auth stays fail-closed. Channel-user to session mappings now persist in Postgres with ON CONFLICT (6fe83617f, 57736b7c9), and the command deduplicator is Redis-backed in the lifespan. The frontend wizards that called missing routes were deleted and the settings page reads the honest 501 (177a07812). Bindings are still env-only, and slack/teams bindings always 401._

- [ ] gateway/channel_registry.py:33-74 in-memory registry seeded from CHANNEL_TENANT_MAP env; no self-service binding management (backend 501 stubs at gateway/router.py:944-978)
- [ ] gateway/router.py:381 still reads state via `from app.main import app` rather than request.app
- [ ] gateway/router.py:99-103 _verify_binding_secret handles only telegram/whatsapp/webhook, so slack/teams bindings on /{channel}/chat always 401

### [PARTIAL] Workflow triggers (webhook, schedule beat, NL preview, DLQ retry)

_The dev webhook key is now used only when ENVIRONMENT is explicitly dev/test/local (30d112707). The webhook DLQ is still inert: nothing inserts into workflow_webhook_events, and the retry task never bumps attempts. Several DSL trigger types still have no firing path, and the NL resolver has no LLM._

- [ ] No INSERT into workflow_webhook_events anywhere (only SELECTs at run_store.py:865,1126,1137); retry_dead_letter_webhooks (workflow/celery_tasks.py:372-391) never updates attempts/status, so any row would re-run every 5 min forever
- [ ] workflow/dsl.py:62-73 trigger types nl/event/file_drop/alertmanager/datadog/pagerduty have no firing path
- [ ] main.py:2875 NLTriggerResolver() built without llm_provider, so non-regex descriptions 422
- [ ] workflow/webhook_tokens.py webhook tokens are stateless HMAC and cannot be revoked

### [PARTIAL] State machines (Family D STATE_TRANSITION backing store)

_Definitions and instances are DB-backed, and POST .../transition now publishes to trigger:event:state_machine.transition (42f2514e6). But transitions are an unlocked read-modify-write, a DB write failure is swallowed while the API still reports transitioned=true and publishes the event, and instances are still keyed without machine_id._

- [ ] NEW triggers/state_machine.py:503-519 — a failed DB write in transition_async is logged and swallowed, then it returns transitioned=True and the API publishes a trigger event for a transition that was never persisted (severity: medium)
- [ ] NEW triggers/state_machine.py:450-501 — transition reads the instance, then re-selects and updates it without SELECT ... FOR UPDATE or a version check; concurrent transitions across replicas lose updates and history (severity: medium)
- [ ] triggers/state_machine.py:413-416,493-497 + db/models/state_machine.py:48 instance key (tenant_id, entity_id) omits machine_id, so a second machine on the same entity reuses the first machine's state

### [PASS] Workflow definitions: CRUD, publish, versions, templates, permissions (two APIs)

_Unchanged: static routes are declared before /{workflow_id} and covered by a route-order test, and list endpoints are paginated (per_page le=100). Only cosmetic items remain._

- [ ] Duplicate GET /{workflow_id}/versions (workflow/router.py:485, router_versions.py:61); the second is unreachable
- [ ] tests/workflow/test_router.py still mocks wf_service with MagicMock

## Org & collaboration

### [PARTIAL] Org OS core CRUD (organizations, departments, teams, custom roles, tasks, missions, schedules, attachments, department memory)

_Org CRUD, ownership (router-wide _verify_org_ownership, app/org/router.py:127-128) and org RBAC are unchanged and tested. Attachments still go to the host-local FS, and org_artifacts is still unused. The per-tenant flag overrides are process-local, but no route writes them today (latent)._

- [ ] app/org/router.py:3275-3285 attachments are written to ORG_ATTACHMENTS_DIR or tempdir/av-attachments on the API host; a worker or replica on another host cannot read the returned server path
- [ ] org_artifacts table never read or written outside its migration (no app reference)
- [ ] Low/latent: app/org/feature_flags.py:86,115-122 per-tenant overrides are an in-process dict (no API route calls enable_for_tenant/disable_for_tenant today)

### [PARTIAL] Org mission execution, HITL approvals and brain proposals

_Per-task finalize isolation holds, and 15f74ea63 made HITL votes durable and DB-first across replicas. Mission approval-gate wiring still logs and continues on exception. This is partly mitigated: a gated mission still forces autonomy_mode='supervised' (service.py:3219-3233), so gated actions block in the HITL gateway. The approval-chain store still uses unbounded KEYS and falls back to process memory on Redis errors._

- [ ] app/org/service.py:3131-3135 (HITL register) and :3205-3209 (outer) gate-wiring exceptions are only logged and the mission is still dispatched (mitigated by the supervised autonomy override at :3219-3233)
- [ ] app/org/approval_chain.py:313 KEYS approval_chain:req:* is an unbounded scan across all tenants; :294,305,320-322 Redis errors fall back to the process-local _store
- [ ] app/org/router.py:3373-3395 mission preview is a keyword heuristic

### [PARTIAL] Org Brain autonomous loop and ambient team collaboration

_f1d72cec2 closed the last code gap: org_brain_loop and org_collaboration_loop now re-raise a failed org scan, so Celery marks the run failed. Per-org errors are still caught per org. The loops use system_session plus per-tenant RLS and a real provider. There is still no end-to-end run against real Postgres/Redis/LLM, and both loops are behind the org_autonomy_enabled allowlist and autonomy level >= 3._

- [ ] No e2e/integration run of either loop against real Postgres/Redis/LLM (unit tests patch the seams); loops gated by the org_autonomy_enabled allowlist and autonomy level >= 3

### [PARTIAL] Org realtime streams, Universal Command Gateway, Graphify job, org MCP WebSocket, emergency stop

_The shared emergency-stop reader honours tenant and org keys in the worker and AgentGraph.run, and the org stop 503s without Redis. The command history is persisted per org. UCG routing and the Graphify job are still fire-and-forget in-process tasks. Org e-stop and resume have no org-role check, unlike the other mutating org routes._

- [ ] NEW app/org/router.py:1970-1981,2030-2040 POST /v1/org/{id}/emergency-stop and /emergency-stop/resume have no require_org_role (other mutating org routes use it, e.g. :328,346), so any key of the owning tenant can lift an org safety stop [severity: medium]
- [ ] NEW app/org/router.py:2054-2058 resume answers {status: resumed} even when app.state._redis is None and nothing was cleared [severity: low]
- [ ] app/org/router.py:2214-2218 UCG command routed via fire-and-forget asyncio create_task; a replica crash loses a queued command with no resweep
- [ ] app/org/router.py:1658 Graphify job is an in-process task holding the Request; pub/sub only, no replay

### [PARTIAL] Org intelligence, digital twin, briefs, decision history, graph versioning, crons

_Twin capacity comes from real teams and tasks, and what-if is an honest 501 (788665494, 0b829c95a). The crons scan on the maintenance session and fail loudly (f190f03c9). Graph versions are tenant-scoped (5d175e497). Decision history and the capability graph still have no writer, so those endpoints always return empty lists, and the intelligence cron still stops at 50 orgs._

- [ ] No app caller of DecisionIntelligence.record() or CapabilityGraph.add_capability(): GET /v1/org/{id}/intelligence/decisions (app/org/router.py:2839) and /intelligence/capabilities (:2800) always return empty process-local data, and the capability graph is not org/tenant-keyed
- [ ] app/org/feature_flags.py:238 intelligence cron _scan_active_orgs(limit=50) with no pagination, so orgs past 50 are never scored

### [PARTIAL] Agent Civilization (society, governor, bus, blackboard, learning, spawn, replay, live stream)

_No civilization commits in the window. Discovery on system_session and the honest throttle/budget controls hold. The kill signal is still never read, pause/resume/kill still report success after swallowed writes, and a failed tick is still a Celery success. Listing endpoints also turn DB errors into []._

- [ ] NEW app/api/civilization.py:281-283 (list civilizations) and :523-525 (list members) swallow DB errors into [], indistinguishable from 'none' [severity: medium]
- [ ] app/civilization/governor.py:275 civ_kill_agent:{civ}:{agent} is written but never read (only occurrence in app/)
- [ ] app/civilization/governor.py:625-626 _set_civilization_status swallows a failed UPDATE, so pause/resume still answer success; unknown civ ids are not rejected
- [ ] app/civilization/governor.py:558-559 _retire_member_by_agent_id swallows errors; app/api/civilization.py:942-963 kill_agent always returns {killed} with no existence check
- [ ] app/scaling/tasks.py:6070 constitution falls back to the default Constitution(); :6156-6157 civilization_tick returns {error} on failure (Celery success)
- [ ] app/api/civilization.py:234-235,321,376,415,460-461,550,585 500 responses include raw exception text

### [PARTIAL] Coordination runtime: sessions, transcript, group-chat WebSocket, event replay, handoffs

_d814ad96c (landed before the prior rating, which did not credit it) fixed two gaps. Session create derives permissions from the caller's role and scopes (403 otherwise), and the required Idempotency-Key now deterministically names the session (uuid5, ON CONFLICT DO NOTHING). Handoff completion still resumes nothing, and group chat still has no cross-connection fan-out._

- [ ] app/coordination/handoffs/service.py:33-40 default emit_event/resume_parent are no-ops and app/main.py:1270-1273 wires none, so a completed handoff resumes nothing
- [ ] app/api/coordination_group_chat.py:110-155 messages are persisted and acked to the sender only, with no pub/sub fan-out; other connections see them only on reconnect replay
- [ ] Low: app/coordination/service.py:16-20,84-85 civilization_id/goal_id ownership is not validated at admission

### [PARTIAL] Human/agent collaboration sessions (operations, rounds and consensus, delegation, insights, WebSocket, org presence, Yjs CRDT)

_Three prior gaps are fixed. CRDT tokens are stored in Redis across replicas (c209c7a84), the shared WS authenticator no longer accepts ?api_key= (8dfd8ad59), and app.state.llm_provider is now set (main.py:2697), so insights can use the LLM. The CRDT relay still runs on the decode_responses=True runtime Redis, which breaks binary Yjs, and the 'snapshot' is a single incremental update._

- [ ] NEW app/api/collab.py:821-822 every 50th update saves that single incremental update as the room's 'full document snapshot', so late joiners never get full history [severity: medium]
- [ ] app/api/collab.py:776-791,800-803 CRDT snapshot/publish use the runtime Redis created with decode_responses=True (app/core/pools.py:44; wired at main.py:2150-2154), so binary Yjs frames fail to decode cross-replica and load_snapshot returns None

### [PARTIAL] Unified Chat (sessions, messages, streaming, QA and goal turns, memories, templates, connected services, artifacts, folders, search, code execution)

_The DB-backed session/message paths and sandbox-only code execution hold. Search, summarize, folders and usage still use in-memory structures, and connected-service 'complete' still flips the status with no OAuth exchange._

- [ ] app/chat/service.py:1967-1990 search_messages/summarize_session read the in-memory _messages dict, so they are empty in DB mode
- [ ] app/chat/service.py:1896-1918 folders are the in-memory _folders dict and move_session_to_folder uses sync update_session on _sessions (404 for DB sessions)
- [ ] app/chat/service.py:594 record_usage() has no caller, so GET /chat/sessions/{id}/usage (router.py:453) is always zero
- [ ] app/chat/router.py:948-952 + app/chat/services_api.py:128-150 POST /chat/services/{id}/complete sets status='connected' with no OAuth code exchange (simulated success)

### [PARTIAL] Voice (STT/TTS, greeting, persona cloning, real-time voice WebSocket, proactive voice alerts, phone calls)

_The fail-closed phone consent gate and the binary-safe persona store hold. The alerts stream now reads the lifespan-bound voice_alert_manager (main.py:1007-1011), but nothing publishes voice alerts, so it only emits keepalives. TTS still silently falls back to a 100ms silent WAV._

- [ ] app/voice/alerts.py:163 publish_voice_alert has no caller in app/, so /v1/voice/alerts/stream only emits keepalives
- [ ] app/voice/router.py:590-596 dev fallback builds VoiceAlertManager from app.state.redis (never set)
- [ ] app/voice/providers/__init__.py:98-110 silent auto-fallback to BrowserFallbackTTS; providers/tts/browser_fallback.py:28,42-44 is_ready and 100ms of silence, with no fallback signal in the TTS response
- [ ] app/voice/consent.py:21 TODO: voice consent not written to the consent ledger (fails closed; no durable record)
- [ ] app/voice/router.py:438 _fetch_wywa passes a redis resolved from app.state (minor)

### [NOT_IMPLEMENTED] Coordination pattern read models (Magentic ledger and human review, MoA layers, CAMEL, generative agents, swarm, sealed-bid auction)

_Only supervisor, goal_tree and debate have a distributed driver (app/orchestration/execution_drivers.py:60, used by strategy_executor.py:52). e45ed57fc/f8c8503b1 now make the goal profile and catalogue say honestly which patterns run, so goals no longer claim magentic/MoA. The Magentic/MoA/CAMEL/swarm/auction read models still have no production writer._

- [ ] app/orchestration/execution_drivers.py:60 STRATEGY_RUNNER_STRATEGIES = {supervisor, goal_tree, debate}; other patterns have no execution driver
- [ ] MagenticHumanReviewService.issue() has no app caller (app/main.py:2691 constructs it; only the submit route at app/api/coordination_magentic.py:61 uses it), so human review always 409s
- [ ] app/api/coordination_swarm.py:20 edges always []; sealed bids are never unsealed or allocated by any runtime path

### [PASS] Gateway conversations (channel messages → conversation continuity)

_Durable chat_channel_sessions/chat_principal_sessions (FORCE RLS) with INSERT ... ON CONFLICT make replicas converge on one session, and the integration test on real Postgres passes. The remaining items are dead code only._

- [ ] Low: app/gateway/conversation.py and gateway_conversations table unused by app/ (dead code)
- [ ] Low: app/chat/service.py:1540 sync handle_channel_message still uses the in-memory map (documented dormant)

## Services & reliability

### [PARTIAL] Goal lifecycle service (GoalService)

_Cancel/pause/resume/approve load the goal from Postgres on any replica and signal the runner through Redis, failing closed with 503 for remote runners (ec5a3c12a, goal_service.py:2114-2164, 4685-4745). Restart recovery claims atomically and enqueues on the real plan queue (8dfc8c77b). Still open: goal dedup is a non-atomic check-then-register, eval reads are memory-only, and lifecycle status writes have no terminal guard and swallow DB errors._

- [ ] NEW app/services/goal_service.py:4709 — cancel_goal writes CANCELLED with no only_if_active guard (pause_goal does the same at :4740), so a cancel racing a worker completion overwrites a COMPLETE row. Every call also writes iterations=0 (_db_update_goal_status default, goal_service.py:5437) (severity: medium)
- [ ] NEW app/services/goal_service.py:5451-5452 — _db_update_goal_status swallows DB errors, so cancel/pause report success and dispatch terminal events while Postgres keeps the old status; the next refresh reverts it (severity: medium)
- [ ] NEW app/services/goal_service.py:963-1010 — restart recovery only looks at self._goals, which sync_from_db warms with goals from the last 24h, capped at 500 per tenant and 50k overall (goal_service.py:5518-5533). Orphaned in-process goals outside that window (for example 24h enterprise goals) are never recovered at startup and rely on the stuck-goal sweeper (severity: low)
- [ ] NEW app/services/goal_service.py:566,2591,4673 — _eval_scores grows without bound per process (no eviction) (severity: low)
- [ ] Dedup still races: get_existing (goal_service.py:3802-3808) and register (goal_service.py:3834-3843) are separate, register()'s False (SET NX lost) is ignored and errors are swallowed by a bare 'except Exception: pass' (goal_service.py:3842-3843), so concurrent identical submits both run
- [ ] get_eval/get_eval_suggestions/run_eval still use the memory-only _get_record (goal_service.py:4535, 4563, 4632) and the process-local _eval_scores/_eval_pending dicts (goal_service.py:566-570, 2583-2593, 4673): 404 or 'not_evaluated' on any replica that didn't run the goal, and for every worker-run goal
- [ ] is_valid_transition is still imported with noqa F401 and never enforced (goal_service.py:95)
- [ ] clear_signals still has no callers (reliability/goal_lifecycle.py:113-119)

### [PARTIAL] SSE goal event delivery and cross-replica fanout

_The local path registers its queue before replay and shares subscribers across DB refreshes (goal_service.py:5129-5186). The cross-replica path is still replay-then-SUBSCRIBE with no gap closing, resume is capped at 100 events, and there is no heartbeat. New: the cross-replica stream only ends on goal_complete, goal_failed or goal_cancelled, so a worker goal that ends with worker_failed (timeout, crash, lock failure) leaves the stream hanging._

- [ ] NEW app/services/goal_service.py:5111-5118 — the cross-replica live loop stops only on goal_complete, goal_failed or goal_cancelled. Worker-only terminals (worker_failed from timeout, lock failure or crash, emitted at scaling/tasks.py:1972 and :2034) never match, so the SSE stream and a _WorkerSubgoalService parent waiting on a sub-goal (tasks.py:648-673) hang (severity: medium)
- [ ] NEW app/services/goal_service.py:714 — every API replica psubscribes to goal_events:* for ALL tenants and creates a stub per goal. Per-replica memory and CPU therefore scale with fleet-wide event volume, not with the replica's own subscribers (severity: medium)
- [ ] NEW app/services/goal_service.py:5098-5124 — the cross-replica path yields the worker's raw envelope {goal_id, tenant_id, type, payload} (tasks.py:1865-1873), not the event shape the local path yields, so clients get two schemas depending on which replica serves them (severity: low)
- [ ] SSE id = event._seq or a local counter; live/queued events carry no _seq, so Last-Event-ID drifts (api/goals.py:731-739)
- [ ] Resume replay capped at list_events_since default limit=100 (event_store.py:131); _list_events_since_persisted passes no limit (goal_service.py:2355-2357) and there is no pagination loop
- [ ] Cross-replica path replays persisted events (goal_service.py:5070-5078) BEFORE pubsub.subscribe (goal_service.py:5105), so events published in between are lost. It returns silently when no Redis URL is set (goal_service.py:5092-5093)
- [ ] No heartbeat/is_disconnected on the goal SSE stream (api/goals.py:730-749); a WAITING_HUMAN goal holds an idle connection indefinitely. Bridge errors are only logged (goal_service.py:794-798)
- [ ] Bridge stub GoalRecords (created_at='') are created for every unknown goal_id (goal_service.py:728-751). The bridge sets the terminal status but never completed_at (goal_service.py:787-791), so _evict_stale_goals (goal_service.py:821-831) never evicts them: unbounded growth

### [PARTIAL] Durable goal event store

_Sequences come from goals.event_seq through UPDATE..RETURNING in the same statement as the INSERT (event_store.py:47-73), which is race-free. goal_events is range-partitioned by created_at, with an index on (goal_id, sequence). A final append failure and a missing goal row are still only logged, so events can silently disappear from the durable stream._

- [ ] NEW app/services/event_store.py:110-126,160-168 — replay queries filter on goal_id/sequence but not on the created_at partition key, so every replay and resume probes every monthly partition's index; cost grows with retention length (severity: low)
- [ ] append_event swallows the final failure after 3 retries (event_store.py:83-89); missing goal row (rowcount 0) only logs (event_store.py:74-81)
- [ ] list_events has no try/except (event_store.py:93-126). The caller GoalService._list_persisted_events catches and returns [] (goal_service.py:2336-2346), so a DB outage replays an empty history as if the goal had no events

### [PARTIAL] Goal dedup and idempotency

_Idempotency-Key handling is claim, then complete or release, with fail-closed 503 on store errors and replays that return the goal_id (d606a7a3b, api/goals.py:188-262). The dedup key is scoped (8405d7357). Goal dedup still races (non-atomic, lost SET NX ignored), has only a 60s TTL, and the Idempotency-Key is not bound to the request body._

- [ ] NEW app/api/goals.py:200-202 — when app.state.idempotency_store is missing (Redis wiring failed at main.py:2082-2083) an Idempotency-Key header is silently ignored and the submission runs unguarded. It fails open, unlike the 503 path (severity: medium)
- [ ] NEW app/reliability/idempotency.py:12,55-57 — the pending claim TTL is 120s and release/complete are not owner-checked. A submission slower than 120s lets a retry claim and create a second goal, and the first request's release/complete then clobbers the second's claim (severity: low)
- [ ] NEW app/services/dedup.py:34 — dedup TTL is 60s, so dedup only covers the first minute of a goal. Worker-run goals never release the key: the Celery bridge does not call _dispatch_event (goal_service.py:2676-2690 only runs for local events). An identical resubmit within 60s of a worker goal finishing is answered 'running, deduplicated' for a finished goal (severity: low)
- [ ] Goal dedup race: get_existing (goal_service.py:3802-3808) then register (goal_service.py:3834-3843) is non-atomic; register()'s False is ignored and exceptions are swallowed (goal_service.py:3842-3843)
- [ ] Low: RedisDeduplicationCache still keys on per-process hash(goal) (reliability/dedup.py:76,84,90); unused in production
- [ ] Low: Idempotency-Key replay is not bound to the request body; reusing a key with a different body returns the first response (api/goals.py:215-219)

### [PARTIAL] Cross-process goal lifecycle signals and execution locks

_Signal writes are now strict for remote runners, and a failed flag write fails the API action with 503 instead of reporting cancelled (ec5a3c12a). Worker runs install a Redis-driven step-boundary pause gate (tasks.py:548-573) and are wrapped in wait_for(plan timeout) (tasks.py:3074-3086), so a pause can no longer outlive the lock TTL (timeout+300s, tasks.py:2011-2015). The flip side is that a pause silently becomes a timeout failure. No lock extension exists and dead helpers remain._

- [ ] NEW app/scaling/tasks.py:3074-3088 — plan timeout includes paused time, so a worker goal paused longer than its plan timeout (1h on free) is killed as 'Goal timed out' (worker_failed), even though the pause flag is kept 7 days (goal_lifecycle.py:37) (severity: medium)
- [ ] NEW app/scaling/tasks.py:1992-2021 — the execution lock is only taken when a Redis URL is available; a non-Redis broker without REDIS_URL runs goals lockless with no warning (severity: low)
- [ ] _SyncGoalLock still has no extend/renewal (scaling/tasks.py:114-146); correctness depends entirely on wait_for firing before TTL
- [ ] check_pause_cancel (reliability/goal_lifecycle.py:155-172) and clear_signals (goal_lifecycle.py:113-119) still have no callers; GoalExecutionLock (reliability/distributed_lock.py) unused
- [ ] Non-strict signal paths still only warn (goal_lifecycle.py:62-66) for locally-run goals; is_paused/is_cancelled read errors return False (goal_lifecycle.py:122-152), so a Redis blip hides a cancel from a worker

### [PARTIAL] Tenant service (signup, API keys, auth resolution)

_Signup, key creation, revoke and plan change are DB-first. Auth resolves keys against Postgres via the api_key_hash RLS policy, and the shared Redis cache is cleared on revoke and plan change (tenant_service.py:144-352, 680-737). The SSO lookup now raises on DB error, but it still reads per-pod memory first. Startup sync loads every tenant and key into each replica._

- [ ] NEW app/services/tenant_service.py:891-957 — sync_from_db loads EVERY active tenant and every API key into each replica's memory with one query per tenant. Unbounded memory and startup time at scale, and it is only used by memory-first helpers (severity: medium)
- [ ] NEW app/services/tenant_service.py:733-735 — a DB error in _db_resolve_by_hash returns None, so a Postgres outage shows up as 401 for all API-key auth rather than a retryable 503 (severity: low)
- [ ] NEW app/services/tenant_service.py:337-349 vs 395-406 — revoke deletes api_key:{hash}, but a concurrent resolve that read the row before the revoke committed re-caches it for 300s (_cache_resolved_key, tenant_service.py:444-464), so the revoked key keeps working up to 5 min (severity: low)
- [ ] _get_tenant_from_db swallows errors (tenant_service.py:959-995); get_tenant_cached has no callers (tenant_service.py:997-1062)
- [ ] get_tenant_by_sso_sub checks per-pod memory first, which skips the is_active check for cached tenants (tenant_service.py:745-747). get_key_by_sso_sub is memory-only (tenant_service.py:775-796), so on other replicas SSO contexts carry a ghost 'sso:{sub}' api_key_id (auth/keycloak.py:196-202)
- [ ] Redis cache read errors pass silently (tenant_service.py:385-386), which is benign because resolution falls through to the DB

### [PARTIAL] Tenant LLM config store

_The store is durable in Postgres behind a Redis cache, and the goal path reads it strictly: a DB error fails the goal instead of falling back to the platform provider (llm_config_store.py:80-102; goal_service.py:4748-4760, 3485). Downgraded because the RAG retrieval path reads it non-strictly, so a DB error sends a BYOK tenant's retrieval LLM calls to the platform provider without any signal. The per-process fallback dict is also still read._

- [ ] NEW app/main.py:819-822 — _resolve_retrieval_llm calls config_store.get_config() non-strictly. A DB read error returns None (llm_config_store.py:93-97), then the stale local dict or the platform provider is used (main.py:863-873), so a BYOK tenant's RAG LLM traffic and cost go to the platform vendor on a DB blip. This is the fail-open the goal path already fixed with strict=True (severity: medium)
- [ ] NEW app/api/tenants.py:328-334 — GET /tenants/me/llm uses a non-strict read, so a DB error reports configured:false (fake 'not configured') (severity: low)
- [ ] NEW app/services/llm_config_store.py:78,136-137 — set_config's Redis cache overwrite failure is only logged, so other replicas keep serving the previous cached config (including the old key) for up to _CACHE_TTL_SECONDS=300 (severity: low)
- [ ] Process-local app.state._llm_configs is still written on every save (api/tenants.py:373-383) and read when the store returns None (api/tenants.py:333-334; main.py:821-822; goal_service.py:1301-1306)

### [PARTIAL] Notification service

_ca5547797 closed the DNS-rebinding SSRF: delivery now goes through the IP-pinned public_async_client with redirects treated as failures (notification_service.py:360-373). Channel caches are still hydrated once per process, persistence errors are swallowed on create, and the magic links use a query parameter the approve endpoint does not accept._

- [ ] NEW app/services/notification_service.py:244-246 vs app/api/governance.py:1513-1521 — Slack/webhook magic links are built as ?token=, but GET /hitl/{id}/approve and /reject require an HMAC ?sig=, so every notification approve/reject link returns 403 (broken feature, fails closed) (severity: medium)
- [ ] NEW app/governance/hitl.py:257-268 — approval notifications are fire-and-forget create_task with no retained reference, so they can be GC'd or dropped and their failure is never surfaced (severity: low)
- [ ] _loaded_tenants hydrates each tenant once per process (notification_service.py:40,55-60): channels created or deleted on another replica are not seen, so deleted channels keep receiving notifications and new ones are missed
- [ ] _persist_channel swallows DB errors (notification_service.py:133-168), so POST /governance/notifications answers 201 'created' (api/governance.py:1178-1197) for a channel that exists only in one pod's memory
- [ ] No channel_type/URL validation at create (api/governance.py:51-53, 1178-1197)
- [ ] notify_goal_complete still has no callers (notification_service.py:323)
- [ ] approval_token defaults to '' and hitl.py:257-268 passes none (notification_service.py:229,242-246)

### [PARTIAL] Usage metering, outbound webhooks, legacy persistence adapters

_The API and worker paths both meter tool_call_complete events and terminal goals, flush eagerly, and use deterministic per-goal ids with ON CONFLICT DO NOTHING (54384e45c; usage_metering.py, usage_service.py:250-279). The outbound webhook service and the persistence adapters are still dead code. Tool-call records are not idempotent, and the usage summary silently drops the DB rollup on error._

- [ ] NEW app/services/usage_service.py:176-205 — get_usage_summary swallows a DB rollup failure and returns only the in-memory buffer (usually empty), so /billing/usage reports zero usage instead of an error (severity: medium)
- [ ] OutboundWebhookService (app/services/webhook_service.py) and app/services/persistence.py still have no importers in app/ (dead code)
- [ ] Unused module singleton remains (services/usage_service.py:309)
- [ ] tool_call records use random ids (usage_service.py:69,124-143), so a Celery redelivery re-meters tool calls; a flush failure in the worker's throwaway UsageService (scaling/tasks.py:443-456) re-buffers into an object that is then discarded, so the record is lost

### [PARTIAL] Circuit breakers

_The MCP client uses the async Redis breaker per connector (mcp/client.py:1174-1427), narrow-decision LLM calls now go through the per-model breaker plus timeout (64b262847), and an open circuit fails the step honestly. The executor-level breaker still calls sync can_call, which on RedisCircuitBreaker is the per-instance in-memory fallback, so it is per-goal and never shared. The Redis HALF_OPEN state still has no TTL._

- [ ] NEW app/scaling/tasks.py:2661-2696 — the worker AgentGraph is built without circuit_breakers, so worker-run goals get no executor-level breaker (MCP-client and provider breakers still apply) (severity: low)
- [ ] NEW app/providers/circuit_breaker.py:69 — the provider breaker is a module-level per-process singleton, so each replica and worker learns about a dead provider independently (severity: low)
- [ ] Executor picks get('llm') or get(tool), so connector breakers are never consulted there and tool-step failures are recorded on the 'llm' breaker. It calls the sync can_call/record_*, which on RedisCircuitBreaker delegate to a per-instance in-memory fallback (executor_mixin.py:1036-1044, 1621, 1634; redis_circuit_breaker.py:187-200). The Redis state is never read or written from the executor
- [ ] OPEN->HALF_OPEN is a SET without TTL, which also drops the key's earlier EXPIRE (redis_circuit_breaker.py:121-124). HALF_OPEN then returns False forever for the whole fleet if the prober never reports (redis_circuit_breaker.py:128-132); the half_open_claim TTL does not help because state stays HALF_OPEN

### [PARTIAL] Bulkhead (per-tenant concurrency)

_RedisBulkhead's Lua acquire/release is atomic. When Redis is unreachable it now degrades to the per-replica semaphore or denies, and the executor fails closed on acquire errors (2badc79fb, which landed before the prior rating, so that fail-open gap was already stale). Limits are still per-process and never plan-derived, slot TTL handling can leak or overrun, and the Celery worker path has no bulkhead at all._

- [ ] NEW app/scaling/tasks.py:2661-2696 — the worker AgentGraph is built without bulkhead_registry, so goals run by Celery workers (the production path when a task queue is configured) are not counted against the tenant bulkhead (API/worker parity gap) (severity: medium)
- [ ] available_slots_sync returns max; registry available_slots reads the local registry (bulkhead.py:139-141, 197-198)
- [ ] configure_tenant limits live in a per-process _limits dict and nothing calls configure_tenant (bulkhead.py:176-190). Every tenant gets default 20 regardless of plan (main.py:2052-2055)
- [ ] EXPIRE on every acquire (bulkhead.py:81) keeps leaked slots (crashed holder) alive while traffic continues; a step longer than _SLOT_TTL=300s lets the key expire mid-flight, resetting the count and allowing more than the limit

### [PARTIAL] Rollback engine and tool inverses

_Production rollback runs through rollback_all_async in stack mode from the verifier's permanent-failure path, with honest RollbackReport counts and the goal's real tenant context (verifier_mixin.py:821-835; rollback.py:20-54, 237-249). The legacy sync paths flagged earlier have no production callers. The undo stack is still in memory per AgentGraph and is not run on a timeout or cancel._

- [ ] NEW app/scaling/tasks.py:3087-3104 and app/services/goal_service.py:4695-4713 — rollback only runs on the verifier's permanent-failure branch (verifier_mixin.py:821). Goals that end by timeout (wait_for cancel) or operator cancel never roll back side effects already registered (severity: medium)
- [ ] Legacy None-returning inverses would still be counted rolled_back (rollback.py:42, 130-143) and the sync paths fire-and-forget (rollback.py:145-179; tool_inverses.py:123-133): dead but unremoved
- [ ] Rollback stack is in memory per AgentGraph (graph.py:196; RollbackEngine() per build at goal_service.py:1603, tasks.py:2673), so it is lost on worker crash or redelivery

## Frontend, SDKs & CI

### [PARTIAL] Frontend app shell and routing (agent-verse-frontend/src/app/App.tsx)

_e50b74d6a makes the banner's Clear lift local state only after a 2xx and show the server's reason otherwise. 29c5aa886 maps snake_case is_built_in and filters the built-ins, and 080dfb399 removed the SDK rows from CLAUDE.md/AGENTS.md. The e-stop state itself is still browser-local: the backend has no GET status, and its tenant flag now expires after 300s while the banner stays up._

- [ ] NEW app/api/governance.py:1351-1355 the tenant emergency_stop:{tid} Redis flag is set with ex=300, so workers and AgentGraph resume new goals after 5 minutes while the UI banner (emergency.ts persist) still says 'All goal execution halted' [severity: medium]
- [ ] NEW app/api/governance.py:1315-1325 POST /governance/emergency-stop enumerates only this replica's in-memory goal_service._goals; goals owned by other replicas or already queued are covered only by the 5-minute flag [severity: medium]
- [ ] agent-verse-frontend/src/stores/emergency.ts:13-37 e-stop state is still zustand persist (localStorage). The backend has only POST/DELETE /governance/emergency-stop (app/api/governance.py:1293,1454) and no GET status, so a stop set or lifted by another admin, the CLI or the API is not reflected

### [PARTIAL] Frontend realtime transports (SSE / WebSocket)

_4686f6ebe moved GraphifyProgress onto API_BASE, so it works cross-origin. The goal/chat/collab/org-event streams use short-lived HMAC stream tokens, and production refuses the dev secret (auth/stream_tokens.py:39-57). CursorPresence still builds a same-origin WS URL that the production nginx (which proxies nothing) cannot serve, and the dead useOrgStream/useMissionStream hooks remain._

- [ ] NEW agent-verse-frontend/src/features/org/components/CursorPresence.tsx:68 sends the permanent API key as the WebSocket subprotocol instead of a short-lived stream token, unlike the SSE paths [severity: low]
- [ ] agent-verse-frontend/src/features/org/components/CursorPresence.tsx:67 WS URL is window.location.origin + /collab/presence/{org}/ws; agent-verse-frontend/nginx.conf has only SPA/static locations (no proxy), so org presence cannot connect in the containerised deployment
- [ ] agent-verse-frontend/src/features/org/hooks/useOrg.ts:266,291 dead useOrgStream/useMissionStream: relative EventSource with no API base or auth (only tests use them)
- [ ] No Last-Event-ID replay on the org event stream (OrgRealtimeManager.ts:120-140 reconnects with a fresh token only)

### [PARTIAL] Frontend feature pages: browser-local state standing in for backend state

_Unchanged at HEAD (the only commit in these folders, 88ae599b9, dropped phantom calls). The A2A registry, marketplace installed markers, eval/ghost-run/OCR/perception/training history and playground scenarios still use localStorage as their source of truth. The backend now serves GET /marketplace/installs (ed880500e), but the marketplace page still ignores it._

- [ ] agent-verse-frontend/src/features/a2a/A2APage.tsx:351-360 remote-agent registry in localStorage (no backend registry)
- [ ] agent-verse-frontend/src/features/marketplace/MarketplacePage.tsx:720-735 installed markers from a per-tenant localStorage Set although GET /marketplace/installs exists (app/api/enterprise.py:726-742)
- [ ] agent-verse-frontend/src/features/eval/EvalPage.tsx:154-165 eval history in localStorage
- [ ] agent-verse-frontend/src/features/goals/GhostRunPage.tsx:69-77, ocr/OcrPage.tsx:114-122, perception/PerceptionPage.tsx:68-75 history only in localStorage
- [ ] agent-verse-frontend/src/features/training/TrainingExportPage.tsx:89-98, playground/PlaygroundPage.tsx:176-183 localStorage is the source of truth

### [PARTIAL] Org OS / Gateway frontend (features/org, features/gateway)

_177a07812 points GatewaySettingsPage at the real per-org /v1/gateway/{org_id}/config (an honest 501, with the reason shown) and reads orgId via useParams. The connect wizard no longer takes tokens, and the four dead managers were deleted. 4686f6ebe switched useUpdateTaskStatus to POST and made ArtifactGallery say artifacts are unavailable. Channel configuration is still not possible from the UI (env-only), the Kanban board is display-only, and org e2e specs are skipped by default._

- [ ] NEW agent-verse-frontend/e2e/gateway.spec.ts:35,53,130 still mocks the removed GET /v1/gateway/config and POST /v1/gateway/channels/{id}/connect routes, so it tests UI behaviour that no longer exists [severity: low]
- [ ] Gateway per-org channel config/status remain 501 on the backend (app/gateway/router.py), so channels can only be configured via env; the settings page is informational
- [ ] agent-verse-frontend/src/features/org/KanbanBoard.tsx:88,154 onStatusChange is never wired and useUpdateTaskStatus has no caller, so the board is display-only
- [ ] agent-verse-frontend/e2e/org/*.spec.ts:10-12 test.skip unless TEST_ORG_ID or E2E_FULL is set

### [PARTIAL] Playwright e2e suites and CI coverage (agent-verse-frontend/e2e)

_No change to playwright.config.ts or the CI workflows since the last rating. full-live/mobile still match **/*.spec.ts and sweep in e2e/real-backend/*.realbe.spec.ts, which has no skip gate. PR CI runs only smoke-live (dev and production build), and nightly runs 9 mocked projects plus real-backend._

- [ ] agent-verse-frontend/playwright.config.ts:66-75 full-live/mobile testMatch **/*.spec.ts include e2e/real-backend/*.realbe.spec.ts (no skip gate; only real-e2e/** is ignored at :14)
- [ ] .github/workflows/ci.yml:113,124 PR runs only smoke-live; .github/workflows/nightly.yml:194,330 run 9 mocked projects plus real-backend; full-live/real-e2e config never run in CI
- [ ] Mocked specs still fulfil routes that do not exist (e2e/gateway.spec.ts:35,53,130 /v1/gateway/config and /channels/{id}/connect)
- [ ] agent-verse-frontend/e2e/sdk-integration.spec.ts:2-9 still claims Python/TypeScript SDK parity but is mocked REST, and the SDKs were removed

### [PARTIAL] GitHub Action (agent-verse-github-action)

_No commits touched agent-verse-github-action since the last rating. Outputs come from result_artifact.summary and /goals/{id}/cost-metrics, and 31 tests pass. It is still not run by any CI workflow, waiting_human is not recognised, and polling never checks the HTTP status._

- [ ] No workflow in .github/workflows/ runs agent-verse-github-action/tests (grep 'github-action' finds nothing)
- [ ] agent-verse-github-action/entrypoint.py:43-50 SSE handles only goal_complete/goal_failed; a goal parked in waiting_human (app/agent/state.py:20) ends as status=timeout
- [ ] agent-verse-github-action/entrypoint.py:125-129 polling does resp.json() with no status check, so a 401/5xx becomes 'unknown' and then a misleading timeout
- [ ] agent-verse-github-action/entrypoint.py:30-34,125 blocking urllib inside async main; the SSE read (timeout=TIMEOUT) plus the polling loop can wait up to 2x wait-timeout

### [NOT_IMPLEMENTED] Python SDK (agent-verse-sdk-python) - REMOVED

_Removed by the owner in cabce1238 (2026-08-17): there is no agent-verse-sdk-python directory and no agentverse-sdk/[tool.uv.sources] entry in agent-verse-backend/pyproject.toml. 080dfb399 corrected CLAUDE.md/AGENTS.md. This is a deliberate removal, not a defect._

- [ ] Low: agent-verse-backend/tests/_paths.py:38-41,55-59 SDK_PYTHON_ROOT is None, so tests/intelligence/test_benchmarking.py, tests/compliance/test_async_gdpr.py and tests/agent/test_medium_fixes.py SDK cases always skip (dead coverage)

### [NOT_IMPLEMENTED] TypeScript SDK (agent-verse-sdk-typescript) - REMOVED

_Removed in cabce1238: there is no agent-verse-sdk-typescript directory and the frontend imports no SDK. 080dfb399 updated CLAUDE.md/AGENTS.md. This is a deliberate removal, not a defect._

- [ ] Low: agent-verse-frontend/e2e/sdk-integration.spec.ts:1-12 still claims 'Python SDK / TypeScript SDK' parity but only exercises mocked REST
- [ ] Low: tests/_paths.py:42-45,62-66 SDK_TS_ROOT None, so tests/agent/test_phase_completeness.py SDK checks skip; .github/copilot-instructions.md:16 still names the directory (as out of scope)

### [PASS] Frontend API client and contract drift vs backend OpenAPI

_c245b4eaf's contract test still passes: every literal frontend HTTP/EventSource call resolves to a method+path in app.openapi(), and EXCEPTIONS is empty. The phantom-route fixes (020b54664 ... 177a07812, 4686f6ebe) hold at HEAD. The test does not check request/response shapes, non-literal paths or WebSocket URLs._

- [ ] Low: tests/frontend/test_api_contract.py checks method+path only. Payload shapes, non-literal paths and `new WebSocket(...)` URLs (for example CursorPresence.tsx:67 /collab/presence) are unchecked
- [ ] Low: BillingPage.tsx rzp_test_placeholder and is_mock client path remain (backend-gated by allow_mock_payments)

### [PASS] Backend SDK helpers (agent-verse-backend/app/sdk)

_app/sdk/manifest.py and mock_server.py are unchanged, and the CLI's `manifest validate` is still wired. This is a dev/test helper with no HTTP exposure, by design._

- [ ] Informational: MockMCPServer is not wired into any runtime path (used by the CLI/tests only)

## Enterprise & ops

### [FAIL] Marketplace monetization (paid templates, Stripe Connect)

_Unchanged: app/api/marketplace_monetization.py:166-222 creates a real Stripe PaymentIntent and returns its client_secret, so the buyer can be charged. No payment_intent.succeeded handler exists anywhere (the only occurrence is the triggers/simulation.py:41 sample), so purchases stay 'pending', installs are never gated on payment and authors are never paid. The Stripe billing webhook (billing.py:746-806) handles only subscription checkout events._

- [ ] No payment_intent.succeeded handler in app/; marketplace_purchases rows stay 'pending' (api/marketplace_monetization.py:3-5, insert :209-218)
- [ ] Install/deploy never checks price_usd or purchases (no price/purchase reference in enterprise/marketplace_v2.py), so paid templates install for free
- [ ] api/marketplace_monetization.py:192-197 PaymentIntent.create has no transfer_data/application_fee_amount/on_behalf_of; revenue_share_pct is stored but never applied
- [ ] onboarding_complete / total_earned_usd never updated (no account.updated handling)
- [ ] api/marketplace_monetization.py:166-222 no review-status, self-purchase or existing-purchase check, so every click creates another PaymentIntent

### [PARTIAL] Compliance and data-subject rights (GDPR v1/v2, SOC2/HIPAA status, contracts, consent, DPDP)

_No compliance code changed in the window. 020b54664 pointed the frontend privacy page at real routes, and consent, async export and contract signing are still fail-closed (503/404). The synchronous GDPR export still reports 'ready' on incomplete data, v2 controls have no in-app writer, residency is hardcoded, and the contracts list swallows DB errors into []._

- [ ] NEW app/enterprise/compliance.py:72,388-401 export requests are cached in the process-local _export_requests dict and retention_sweep (:454-465) iterates it, so the retention report is per-replica [severity: low]
- [ ] app/enterprise/compliance.py:265,307-310,329-343,381 sync export sets status='ready' although goal DB read errors are only logged, audit is capped at entries[:100] and knowledge_collections is hardcoded []
- [ ] app/api/enterprise.py:116-124 GET /enterprise/compliance/export creates and persists an export request (a side-effecting GET)
- [ ] app/scaling/tasks.py:5927,5937 async export still LIMIT 10000 on goals/audit_log (silent truncation for large tenants)
- [ ] app/enterprise/compliance_v2.py:442 compliance_certifications is only read; no INSERT into it or tenant_settings exists in app/, so SOC2 cert, HIPAA training and GDPR retention controls pass only via manual SQL
- [ ] app/enterprise/compliance_v2.py:326,341,361,379,393,411,429,452 helpers catch all exceptions with no savepoint, so one failure cascades into false 'fail' controls
- [ ] app/enterprise/compliance.py:483-484 residency hardcoded us-east-1/eu-west-1; app/api/enterprise.py:188-195 fixed region list compares residency.get('region') (the key is primary_region); compliance.py:454-465 retention_sweep only counts in-memory _export_requests
- [ ] app/main.py:729 ComplianceChecker(db_factory=None) is bound at :2819 unconditionally, so without pools GET /enterprise/compliance/{fw} calls None() -> 500 instead of 503 (enterprise.py:1813-1815 only checks checker is None)
- [ ] app/api/enterprise.py:1887-1891 GET /enterprise/contracts swallows DB errors and returns []

### [PARTIAL] Enterprise SSO and provisioning (SAML 2.0, SCIM 2.0)

_No SSO/SCIM code changed. ACS is public and fail-closed, config is admin-only, and SCIM fails closed on an unreadable config (enterprise.py:2310-2313). A verified SAML assertion still ends in an honest 501 with no session or JIT user, and SCIM exposes only /Users._

- [ ] NEW app/api/enterprise.py:2215-2266 POST /enterprise/saml/test is tenant-level, not admin-gated, and returns 'Connection failed: {exc}' / 'Invalid XML: {exc}' raw error text (outbound fetch is SSRF-guarded) [severity: low]
- [ ] app/api/enterprise.py:2140-2210 no SAML session issuance or JIT provisioning; a valid assertion returns 501
- [ ] app/api/enterprise.py:2087-2090 SP-initiated /enterprise/saml/login still calls _require_tenant, so a browser user needs a tenant API key
- [ ] app/api/enterprise.py:2320-2360 SCIM has only /Users; no /Groups, /ServiceProviderConfig, /Schemas or /ResourceTypes
- [ ] app/api/enterprise.py:2305-2310 with no scim_configs row the defaults are permissive (allow_user_create/update True); minor, since provisioning a token is admin-only
- [ ] app/api/enterprise.py:2083,2396 500 responses leak raw exception text ('SAML configuration failed: {exc}', 'Token provisioning failed: {exc}')

### [PARTIAL] Simulation sandbox (mock-tool runs) and Lab

_No simulation/lab code changed in the window. The real provider is injected, /lab/run starts a real run, and comparison/live are honest 501s. /enterprise/simulation/available-tools still swallows MCP discovery failures into an empty 200, cost is a len(steps)*0.001 placeholder, and the no-LLM streaming path emits a stub plan that ends in final_status 'complete'._

- [ ] NEW app/enterprise/simulation.py:430-458 run_streaming with no real LLM streams a keyword stub plan with '[simulated: ...]' outputs and simulation_complete final_status='complete' (flagged only by used_real_llm=False) [severity: low]
- [ ] app/api/enterprise.py:240-253 GET /enterprise/simulation/available-tools swallows MCP discovery exceptions (except: pass) into {tools: [], total: 0} with 200
- [ ] app/enterprise/simulation.py:242,253,374,381,450,456 cost_estimate/cost_usd = len(steps)*0.001 placeholder, not token cost
- [ ] app/api/enterprise.py:296-304 streaming path resolves the agent via sync agent_store.get and swallows errors, silently dropping the override

### [PARTIAL] Red-team adversarial testing

_No change. POST /enterprise/red-team runs a fixed payload list through the regex GuardrailChecker.check_goal only and never exercises the agent, tools or verifier. BehavioralRedTeamRunner and red_team_corpus are unwired, and reports live in a process dict._

- [ ] NEW app/enterprise/red_team.py:110 self._reports grows without bound (every run is retained, never evicted) on a long-lived API process [severity: low]
- [ ] app/api/enterprise.py:342-356 and app/enterprise/red_team.py:78-111 the endpoint runs only regex check_goal
- [ ] app/enterprise/red_team.py:118 BehavioralRedTeamRunner is not wired to any route
- [ ] app/enterprise/red_team_corpus.py has no app importer
- [ ] app/enterprise/red_team.py:74,110-115 reports are in-memory only; get_report is unrouted and ignores tenant_ctx

### [PARTIAL] Agent template marketplace (v2 DB-backed plus legacy v1)

_The v2 DB path under RLS holds: built-ins are visible (fab345bf9), counters use SECURITY DEFINER functions (483945326, 9016cb233), /installs and /domains/counts read real data (ed880500e), bundles report honestly (e46641a01), and review listing no longer swallows DB errors (8d0ca90e5). The secondary gaps are unchanged._

- [ ] app/enterprise/marketplace_v2.py:1776-1792 required-connector check is dead: AgentStore has no list_connectors (hasattr guard at :1782), and errors are swallowed
- [ ] app/api/enterprise.py:539 sort_by is accepted but never passed to list_templates; there is no uninstall endpoint
- [ ] app/enterprise/marketplace.py:322-361 v1 publish_version swallows DB errors (except at :356) and returns success; get_version_history returns [] on error
- [ ] No admin approve/reject endpoint for 'pending' (medium+ risk) templates (marketplace_v2.py:1605-1610), so they can never become visible to other tenants
- [ ] Low: scaling/tasks.py embed_marketplace_templates counts only WHERE embedding IS NULL; search is FTS only

### [PARTIAL] Goal/tool/cost/agent/eval analytics

_The API layer (139a06c63) now uses _require_tenant, answers 503 when goal_metrics raises, and runs /analytics/evals under the RLS GUC with a 503 on failure. But the aggregator swallows DB errors itself (_get_goals_from_db returns [], and the tools/costs/agents *_db methods fall back to memory), so a DB outage silently serves this replica's in-memory goals and the 503 is unreachable. The DB path still ignores agent_id and reports zero averages._

- [ ] app/analytics/aggregator.py:108-110 _get_goals_from_db swallows every DB error into [], and goal_metrics (:164-181) then falls back to the replica-local goal_service._goals, so /analytics/goals and /costs never 503 and return per-replica data during a DB fault
- [ ] app/analytics/aggregator.py:164-179 DB path ignores agent_id (filter only in the memory path :141-142), leaves avg_duration_s/avg_cost_usd/total_cost_usd at 0 and counts total=len(LIMIT 10000)
- [ ] app/analytics/aggregator.py:286-289,353-356,390-392,490-492 tool/cost/model/agent DB queries fall back to in-memory data or {} on error

### [PARTIAL] Billing and subscriptions (Razorpay, legacy Stripe, plan changes)

_33b6edac8 added POST /billing/webhook/stripe (billing.py:746-806). It checks Stripe-Signature against STRIPE_WEBHOOK_SECRET with a replay tolerance, upgrades the tenant on a paid checkout.session.completed whose client_reference_id and metadata agree, downgrades on customer.subscription.deleted, and answers 503 on a failed write. The Razorpay webhook now 400s on a malformed signed body, /subscription reports 'not_tracked' instead of a fake 'active', and /billing/usage is fed by the metering added in 54384e45c. There is still no subscription record, no cancel/downgrade API and no dunning handling._

- [ ] NEW app/api/billing.py:784-806 the Stripe webhook ignores customer.subscription.updated and invoice.payment_failed, so a past_due/unpaid subscription keeps its paid plan until Stripe finally deletes it [severity: medium]
- [ ] NEW app/core/config.py:471-477 allow_mock_payments has no production guard: if set in production while Razorpay is unconfigured, create-order/verify-payment (billing.py:494-515,576-596) upgrade any tenant for free [severity: low]
- [ ] NEW app/api/billing.py:235 checkout returns 500 'Billing error: {exc}' with raw Stripe exception text [severity: low]
- [ ] No subscription record/period data, and no cancel or downgrade endpoint (app/api/billing.py:136-156)
- [ ] Mock order/verify and demo invoices remain behind allow_mock_payments/development (app/api/billing.py:249-265,494-515,576-596)

### [PARTIAL] GST tax invoicing (India)

_Unchanged: issuance is platform-admin-gated with hmac.compare_digest, the INSERT runs under RLS, and a persist failure returns 503. The seller GSTIN placeholder, random invoice numbers and the 3-entry SAC table remain, and the invoice-number format is also non-compliant._

- [ ] NEW app/api/gst_billing.py:76 invoice numbers look like 'AV/2026-27/ABCD/1A2B3C' (22 chars), over the 16-character GST Rule 46 limit, and the FY label uses the calendar year, so Jan-Mar invoices get the next FY [severity: medium]
- [ ] app/core/config.py:484 seller_gstin defaults to placeholder 27AAAAA0000A1Z5 with no production guard (used at app/api/gst_billing.py:88)
- [ ] app/api/gst_billing.py:74-76 invoice numbers end in a random uuid suffix, not a sequential series
- [ ] app/api/gst_billing.py:198-208 SAC lookup is a hardcoded 3-entry table
- [ ] app/api/gst_billing.py:125-160 invoice not linked to a payment/billing_orders record

### [PARTIAL] Observability API and telemetry pipeline (logs, metrics, traces)

_The 6aba78fbe log feed and the SQL metrics/timeseries under RLS hold, and the trace is an honest 501 (86fb71f35 makes the UI show it as 'not available'). The log store is still wired to Redis only in the API lifespan (main.py:2161-2166), so worker-run goal logs never reach /observability/logs. SLO tracking and AlertRouter still have no production caller._

- [ ] app/main.py:2161-2166 only the API lifespan calls log_store.set_redis; nothing in app/scaling/ does, so feed_tenant_log_store (app/observability/logging.py:80-99) in Celery workers writes to worker memory and those logs never appear in /observability/logs or its SSE stream
- [ ] Goal trace is 501 on the multi-process path; the span timeline is process-local (app/observability/tracing.py:42-44 InMemoryRunTimelineStore)
- [ ] app/observability/slo_tracker.py platform_slo_tracker and app/observability/alert_router.py:38 AlertRouter have no production callers

### [PARTIAL] Health, readiness, public status page and SLA

_ed4dd728d fixed the headline gap. submit_goal now runs a ReadinessGate pre-flight (goal_service.py:2078-2086, called at :3769) fed by live HealthRegistry postgres/redis probes with a timeout, provider/BYOK and embedder status. It refuses with 503 PLATFORM_NOT_READY and fails closed on a readiness error. /status is mounted and honest. Still missing: a liveness/readiness split (the k8s liveness probe hits /health, which 503s on a DB outage), health checks beyond postgres/redis, and a measured SLA._

- [ ] NEW app/services/goal_service.py:3769 + app/runtime_readiness/health_probe.py:83-94 every goal submission runs fresh postgres/redis probes (no cached health), which adds latency and DB/Redis load per submit at high QPS [severity: low]
- [ ] No separate liveness endpoint: /health (app/api/system.py:16-27) 503s when postgres/redis is down, and infra/k8s/backend-deployment.yaml:56, infra/helm/agentverse/templates/app-workloads.yaml:91 and helm/agentverse/templates/deployment.yaml:69 use it as the livenessProbe, so a DB outage restarts every API pod
- [ ] HealthRegistry is seeded only with ConnectionPools.health_checks() (postgres, redis); no Celery/MCP/LLM registry checks (the readiness probe checks the LLM/embedder only by configuration presence)
- [ ] app/api/sla.py:13-64 SLA is a static per-plan table with no uptime measurement or credit computation

### [PARTIAL] agentverse CLI (app/cli/main.py)

_No CLI commits in the window. The bd7a8d5c6 fixes hold (logs uses GET /goals/{id}/timeline, and the key file is 0600 in a 0700 directory). --watch still has no reconnect, and HTTP errors print raw tracebacks._

- [ ] app/cli/main.py:144-178 _stream_goal opens one SSE stream with no reconnect/Last-Event-ID and ignores goal_cancelled
- [ ] app/cli/main.py:75-430 client CLI only; no migration, tenant-admin or maintenance commands
- [ ] app/cli/main.py:60,71,115,133 raise_for_status without handling, so HTTP errors print a raw traceback

### [NOT_IMPLEMENTED] Domain solutions catalog

_Unchanged: app/api/solutions.py serves a static catalog, and POST /solutions/{slug}/install returns status 'planned' and creates nothing. The frontend has no caller._

- [ ] app/api/solutions.py:33-56 install is a stub returning 'planned' (creates nothing), yet answers 200 rather than 501
- [ ] app/api/solutions.py:29-30,48 install takes tenant_id from the request body, not the auth context (echoed only; no write)

### [PASS] Usage metering (UsageService)

_54384e45c producers hold: in-process goals meter via GoalService._dispatch_event and worker goals via scaling/tasks.py, with deterministic per-goal ids (ON CONFLICT DO NOTHING) and flushes under the RLS context. Tests are mocked but cover both paths._

- [ ] Low: no Postgres-backed test of the usage_records insert/rollup (tests/services/test_usage_metering_hooks.py is mocked)
- [ ] Low: tool_call records have random ids (re-metered on Celery redelivery), and a worker flush failure drops the record after logging (app/scaling/tasks.py:437-454 builds a throwaway UsageService)

## Other API surfaces

### [FAIL] Insights API

_Benchmarks still run cross-tenant on a plain RLS session, so they always report insufficient_data. /insights/estimate and /insights/agents/{id}/health query goals.cost_usd, duration_s and embedding, which do not exist on the goals table (app/db/models/goal.py:29-75; no migration adds them). Every call therefore raises, the error is swallowed, and the endpoints return invented defaults (success_probability 0.82, all health axes 0.7). Two headline endpoints can never return real data, and a third is always empty under RLS._

- [ ] NEW app/api/insights.py:143-156 + :115-123 /insights/estimate selects goals.cost_usd/duration_s/embedding (columns absent from app/db/models/goal.py), so it always fails into hardcoded defaults such as success_probability=0.82 [severity: high]
- [ ] NEW app/api/insights.py:686-699 + :666-676,787-788 agent health SQL selects goals.cost_usd/duration_s (absent), so every agent always reports fabricated 0.7 on all six axes with the error swallowed [severity: medium]
- [ ] app/api/insights.py:797-900 benchmarks run cross-tenant on a plain session with no maintenance role; under RLS they always return insufficient_data (:912)
- [ ] app/api/insights.py:134-160 estimate's pgvector query and its fallback share one session with no savepoint; :216-217 except returns defaults

### [FAIL] Golden datasets (eval promotion)

_Unchanged, and downgraded because this is fake success, not an honest 501. The router is mounted (bootstrap/routers.py:397). list always returns [] and create, items and promote-goal return fresh uuid ids and 'promoted' while persisting nothing (golden_datasets.py:28-75). Migration 0076 golden_datasets tables are never written._

- [ ] NEW app/api/golden_datasets.py:64 - POST /promote-goal/{id} answers 'promoted' for any string, including nonexistent goals or another tenant's goal ids [severity: medium]
- [ ] NEW tests/api/test_golden_datasets.py - the 6 tests assert the stub responses, so the fake success is locked in [severity: low]
- [ ] Every endpoint is a stub with fake success: list returns [] (app/api/golden_datasets.py:28-33); create and items return fabricated ids (:36-61); promote-goal returns status 'promoted' (:64-75); nothing touches golden_datasets or golden_dataset_items (0076_golden_datasets.py:21-41)
- [ ] promote-goal never checks that the goal exists, belongs to the tenant or has finished (app/api/golden_datasets.py:64-75)

### [FAIL] Platform admin (cross-tenant, X-Admin-Key)

_PUT plan is durable and the admin key is checked in constant time. But list and detail read the per-process TenantService._tenants cache, and two endpoints return fabricated data: /admin/usage reads GoalService._active_goals, which does not exist (always 0), and /admin/incidents reads guardrail_engine._incidents, which no engine defines (always []). The frontend AdminPage shows /admin/usage._

- [ ] NEW app/api/admin.py:174 - active_goals = len(goal_svc._active_goals). GoalService has no _active_goals attribute (grep app/ finds only this line), so the platform usage returned to AdminPage (client.ts:3078) always shows 0 active goals [severity: medium]
- [ ] NEW app/api/admin.py:186-200 - /admin/incidents reads guardrail_engine._incidents, which app/intelligence/guardrail_engine.py never defines, and swallows errors, so the incident feed is always empty (fake 'no incidents') [severity: medium]
- [ ] NEW app/api/admin.py:113-116 - tenant detail swallows cost_controller errors into usage {} [severity: low]
- [ ] Tenant list, detail and usage read in-process TenantService._tenants, not the DB, so tenants created on another replica after startup are missing or stale (app/api/admin.py:74, 106, 178)
- [ ] No e2e_full/integration test covers admin reads under RLS or the maintenance role

### [PARTIAL] Proactive outreach engine (app/proactive)

_The wired audit holds (4fdf2be7b). The engine is still built without count or preference providers, so the daily cap is per process and opt-out/quiet hours never apply. Delivery still picks the thread from the in-memory map, although a durable chat_principal_sessions table now exists (57736b7c9)._

- [ ] app/bootstrap/routers.py:213 ProactiveEngine(deliver, audit) has no count_provider/count_recorder, so the cap is the per-process self._sent dict (app/proactive/engine.py:72-89)
- [ ] app/bootstrap/routers.py:213 no preferences_provider, so default ProactivePreferences(enabled=True, quiet_hours=None) applies to everyone (no opt-out)
- [ ] app/chat/service.py:1001-1006 deliver_proactive uses the in-memory _principal_sessions (the durable chat_principal_sessions from 57736b7c9 is not consulted); :1015-1017 channel push failures are suppressed while the engine reports delivered=True
- [ ] app/proactive/signals.py:50-54 SignalBus in-process only (limited impact)

### [PARTIAL] Skills: composable instruction packs and Skills Runtime

_04516cf0e fixed fake success: POST /skills-runtime/{id}/execute now 503s without an LLM and records nothing. Enablement, execution log and version history are still module dicts. The per-tenant disable switch that execute enforces is also process-local._

- [ ] NEW app/skills_runtime/executor.py:90-100,123 + app/api/skills_runtime.py:427-446 a tenant's skill disable (ScopedPermissionChecker._disabled) is process-local, so a disabled skill still executes on other replicas and after a restart [severity: medium]
- [ ] app/api/skills_runtime.py:43 _enabled_skills module dict: per-replica and lost on restart
- [ ] app/api/skills_runtime.py:42,44,611,632,669 _executions/_skill_versions unpersisted; versions keyed by skill_id, not tenant
- [ ] No e2e_full test runs skills under the least-privilege app role

### [PARTIAL] Builder (site/app generation)

_Unchanged since edb3bfac9. POST /builder/projects submits a real goal (503 when none started), and project status, preview and assets are auth-gated honest 501s. Generated artifacts are not linked to a project, so the site cannot be served._

- [ ] app/api/builder.py:118-150 live preview/project status are NOT IMPLEMENTED (501); nothing links goal artifacts to project_id/workspace_id

### [PARTIAL] Goal API sub-features not in inventory (ghost-run, batch, lineage, attempts, feedback, explain, traces)

_The b7d30f187 fixes hold: one /explain handler, feedback 404/503, ghost-run 503, and batch lookup errors reported as 'unknown'. The batch_id returned by POST /goals/batch still cannot be resolved by the status route, lineage/attempts/traces still swallow DB errors, and feedback calibration is replica-local._

- [ ] app/api/goals.py:966,996 POST /goals/batch returns a random uuid batch_id, but GET /goals/batch/{batch_id}/status (:1004-1033) splits it as comma-separated goal ids, so polling the returned id yields not_found and all_complete=true immediately
- [ ] app/api/goals.py:1080,1130,1212 lineage/attempts/traces swallow DB exceptions into empty/root-only results; lineage/attempts skip the goal-existence check
- [ ] app/api/goals.py:1285-1290 feedback calibration writes to the in-process _default_calibration_store._records (replica-local); ghost-run docstring (:884) still says 'always returns HTTP 202'

### [PARTIAL] Agent definitions lifecycle (/agents CRUD, versions, snapshots, clone, export)

_The only change since the last rating is a1e01aa3d (agent-identity key sealing, 7 lines in agents.py). The core CRUD stays DB-authoritative and its tests pass. The snapshot, version and rollback gaps are unchanged._

- [ ] _save_snapshot_to_db swallows every exception with a warning, so POST /agents/{id}/snapshot returns the snapshot as saved when it was not (app/api/agents.py:28-56; called at :1173). _load_snapshots_from_db returns [] on error (:59-82), which restarts version numbering and makes GET /versions (:1144) look empty.
- [ ] Rollback ignores update_async's result and always reports 'rolled_back' (app/api/agents.py:1206-1207)
- [ ] get_async falls back to the replica cache on any DB error (app/api/agents.py:245-250)
- [ ] Snapshot version = len(existing)+1 races under concurrent snapshots (app/api/agents.py:1169). There is no unique (tenant_id, agent_id, version) constraint; migration 0025_agent_snapshots.py has only a non-unique index.

### [PARTIAL] Agent Runtime 2.0 API (/agent-runtime)

_The store from 51ec6a32a (bounded LRU in front of tenant-namespaced Redis) is in place and its tests pass. Trace completion is still updated only in _dispatch_event. The Celery bridge sets record.status without touching the trace, so traces of worker-run goals never finish. Cost, token and role-call fields are never populated._

- [ ] NEW app/agent_runtime/models.py:82-88 - AgentRunTrace total_cost_usd, total_tokens, role_calls and model_selections are never written by any runtime path (only the defaults), so GET /agent-runtime/traces/{id} (api/agent_runtime.py:133-150) reports $0 and 0 tokens for every goal [severity: medium]
- [ ] NEW app/api/agent_runtime.py:28-90,116-130 - POST /plans and /traces accept any goal_id without checking that the goal exists in the tenant; clients can create orphan or misleading plans [severity: low]
- [ ] NEW app/api/agent_runtime.py:153-190 - /strategies is a static list that ignores the strategy registry's runnable and certification evidence from f8c8503b1/c3667a30e [severity: low]
- [ ] NEW app/agent_runtime/store.py:121-147 - Redis write/read failures degrade silently to the per-process copy, so traces are not durable (Redis TTL 7d, no Postgres) [severity: low]
- [ ] Trace success/failure is updated only in _dispatch_event (app/services/goal_service.py:2507-2520, 2633-2647). The Celery->SSE bridge sets record.status at goal_service.py:776-793 but never calls agent_runtime_store.update_trace, and app/scaling/tasks.py has no agent_runtime reference, so worker-run goals' traces never record success, failure or duration.
- [ ] Trace update errors are swallowed with bare 'except Exception: pass' (app/services/goal_service.py:2519-2520, 2646-2647)

### [PARTIAL] Memory 2.0 API (/memory-v2)

_The DB is still authoritative for memories: RLS on every path, 503 on errors, FOR UPDATE on PATCH/DELETE/consolidate. Conflicts are still a process-local dict, never persisted and unbounded. The recent memory commits (b8ed5bc52, b5ef7de50, f14dbbe24) changed canonical memory, not /memory-v2._

- [ ] NEW app/api/memory_v2.py:65,671 - _conflicts grows without bound per tenant (no cap or TTL), a slow memory leak [severity: low]
- [ ] NEW app/api/memory_v2.py:253-272 - every POST re-reads up to _V2_SCAN_LIMIT memories and runs an O(N) Python word-overlap scan on each create [severity: low]
- [ ] _conflicts is process-local and never persisted: list and resolve are per-replica and lost on restart (app/api/memory_v2.py:65, 319-356, 671)

### [PARTIAL] Sandbox goal submission (/sandbox)

_POST /sandbox/goals really runs through SimulationRunner.start against mock tools, and the dry-run fallback is honestly labelled (sandbox.py:16-86). The router now mounts through the guarded registry (bootstrap/routers.py:100). /config is still static and partly false, and the sandbox reports a fabricated cost._

- [ ] NEW app/enterprise/simulation.py:242,374 - cost_estimate/cost_usd is a fabricated len(steps)*0.001 even when real LLM calls were made (used_real_llm=True at :243,375), so sandbox runs report invented cost [severity: medium]
- [ ] GET /sandbox/config is hardcoded (sandbox_enabled True, 'Cost is simulated', 'Results are deterministic mock data') even when SimulationRunner uses a real LLM (used_real_llm=True) (app/api/sandbox.py:89-106)
- [ ] Still a separate surface from /enterprise/simulation and /lab/run (app/api/sandbox.py)

### [PARTIAL] Multimodal asset ingestion (/multimodal)

_Mounted through the guarded registry (bootstrap/routers.py:105) and uses the DI'd app.state pipeline with a tenant-keyed job store and a 25 MB cap. But PDF ingestion reports false success: an extraction error, a missing pypdf or a text-free PDF comes back as job status 'completed' with a placeholder span such as '[PDF extraction error: ...]' as the content._

- [ ] NEW app/multimodal/pipeline.py:487-515 - _extract_pdf turns exceptions, a missing pypdf and 'no extractable text' into placeholder spans ('[PDF extraction error: ...]', confidence 0.0/0.1), and ingest_pdf (:135-140) then marks the job 'completed', so a broken PDF reports success with fabricated content [severity: medium]
- [ ] NEW app/multimodal/pipeline.py:130 - job.source_base64 keeps the full upload (up to 25 MB) on the job saved to the job store [severity: low]
- [ ] pipeline.set_provider() mutates the shared app.state pipeline on every request (app/api/multimodal.py:45-47)

### [PARTIAL] Data lifecycle and retention maintenance (app/lifecycle + beat tasks)

_Retention still runs batched deletes on the system session, skips partition drops under a tenant-wide legal hold (tasks.py:5436-5447), and the Docker-backed cascade and partition e2e tests pass. Errors are still stringified into the result so the Celery task never fails. The five lifecycle policy modules still have no importers._

- [ ] Per-table errors become 'error: ...' strings and the outer except returns {'error': ...}, so the retention task never fails or alerts (app/scaling/tasks.py:5449-5462)
- [ ] app/lifecycle archive_policy, deletion_receipt, export_policy, legal_hold_policy and retention_policy have zero importers outside app/lifecycle (grep)
- [ ] _ensure_future_partitions collects per-partition errors and returns {'error': ...} instead of raising (app/scaling/tasks.py:5524-5586)

### [PARTIAL] Maintenance tasks not claimed by any area

_The DPDP erasure and feedback batch beats remain wired (celery_app.py:180) and pass their tests. scan_cost_anomalies is unchanged: it runs a blocking KEYS scan, silently caps at 50 tenants, swallows per-tenant errors, and misreports tenants_scanned._

- [ ] NEW app/scaling/tasks.py:6337 - returns tenants_scanned=len(tenant_ids) even though only the first 50 are scanned, overstating coverage [severity: low]
- [ ] scan_cost_anomalies uses redis.keys('cost:daily:*') (O(N), blocking), caps at 50 tenants per run and does per-tenant 'except Exception: pass' (app/scaling/tasks.py:6308-6341)

### [PARTIAL] Unwired trigger-family implementations (TriggerType families F/G/H/I)

_POST /schedules now applies the same is_supported and validate_spec guards as POST /triggers (0d52e3caa, app/api/schedules.py:204-221), so unsupported types can no longer be stored silently. Ten TriggerTypes are still honestly UNSUPPORTED, and the data/, monitoring/ and advanced/ family modules are still unimported._

- [ ] NEW app/api/schedules.py:95-99,234,446 - webhook tokens are also written to a process-local app.state._webhook_tokens that nothing reads (firing resolves via the DB in triggers/store.py:774-812): dead per-replica state that grows without bound [severity: low]
- [ ] app/triggers/data/, monitoring/ and advanced/ still have zero importers outside their packages, and iot/geofence.py and iot/sensor.py are unimported by any runtime path (grep app/)
- [ ] geofence, google_sheets, graphql_subscription, log_pattern, mqtt, price_threshold, s3_event, sensor_threshold, sharepoint and websocket_message classify as UNSUPPORTED (app/triggers/dispatch_map.py:125-137); they are rejected honestly but have no runtime

### [PARTIAL] Unwired gateway channel adapters and webhook delivery

_Unchanged. The live channel paths under the /v1/gateway/ auth bypass are signature-gated: per-binding secrets, fail-closed voice verification, and x-tenant-id trusted only with GATEWAY_INGRESS_SECRET (gateway/router.py:133-160, 785-840, 900-918). Config and status routes are honest 501s. The Email and VoiceWebhook adapters, WebhookDeliverySystem and OutboundWebhookService still have no importers._

- [ ] NEW app/bootstrap/routers.py:263 - ChannelRegistry.from_env(): Telegram/WhatsApp tenant bindings exist only as deploy-time env, with no per-tenant self-service or DB store. Consistent across replicas, but it does not scale to many tenants [severity: low]
- [ ] EmailChannelAdapter (app/gateway/channels/email.py) and VoiceWebhookAdapter (app/gateway/channels/voice_webhook.py) have no importer outside their own file
- [ ] WebhookDeliverySystem (app/gateway/webhook_delivery.py) and OutboundWebhookService (app/services/webhook_service.py) are dead duplicates with zero importers
- [ ] Per-org gateway channel config/status is not implemented (honest 501) (app/gateway/router.py:944-980)

### [PARTIAL] Dual-mode identity linking (app/identity)

_No change to app/identity. a1e01aa3d changed app/auth/agent_identity (agent JWTs), not channel identity linking. PostgresIdentityStore stays RLS-scoped and its integration test passes. resolve_principal is called only from chat/service.py:1598; link_identity has no caller; the first-contact race is unchanged._

- [ ] link_identity() has no caller in app/, so cross-channel principal unification has no entry point (app/identity/service.py:100-125)
- [ ] First-contact race: get_link miss -> create_principal -> create_link ON CONFLICT DO NOTHING (app/identity/repository.py:66-82). The losing request returns an orphan principal that was never linked (app/identity/service.py:81-98), and link_identity likewise returns a link that may not have been stored (:115-125).
- [ ] No e2e_full test for cross-channel continuity under RLS
- [ ] Stale comment 'In-memory now; a Postgres-backed store swaps in' (app/bootstrap/routers.py:244-246)

### [PARTIAL] Data classification and redaction (app/data_classification)

_Unchanged. The only live classifier use is few_shot_cot.py:17; state_runtime/state_context.py:88 has no importer; redaction.py is unimported; runtime_flags.data_classification is never read. The strategy catalogue now honestly lists data_classification as not_available/planned (strategy_registry.py:1462-1473; strategy_availability -> 'planned')._

- [ ] Not applied on the main executor or result pipeline; the only live use is few-shot CoT (app/agent/patterns/few_shot_cot.py:17)
- [ ] app/state_runtime/state_context.py (classifier at L88) has no importer, so that path is dead
- [ ] app/data_classification/redaction.py has no importer outside the package; runtime_flags.data_classification (app/core/runtime_flags.py:31,72,98) is never read (default True, suggesting it is on)

### [PARTIAL] Minor routers missing from inventory

_The guarded router registry (df515a292) and the /status mount hold, and their tests pass. Data residency is still fabricated: a hardcoded us-east-1/eu-west-1 is returned for every tenant, and the frontend shows it (client.ts:2081, 2241-2242). /compliance/regions still compares a non-existent 'region' key._

- [ ] Residency hardcodes primary_region 'us-east-1' and backup_region 'eu-west-1' for every tenant regardless of the actual deployment (app/enterprise/compliance.py:477-489). /compliance/regions compares residency.get('region'), a key that does not exist, so us-east-1 is listed twice (app/api/enterprise.py:188-197).
- [ ] GET /v1/health always returns ok and /v1/info is static (app/api/v1/router.py:8-19); cosmetic

### [PARTIAL] Frontend feature folders with no inventory entry

_StatusPage is fixed: /status is mounted and non-2xx responses surface as errors (df515a292). c245b4eaf added tests/frontend/test_api_contract.py, which fails when a frontend API call has no route in app.openapi(). The e2e specs still mock or ignore backend payloads, and some pages render fabricated backend values (e.g. the simulation stub's $0.001 per-step cost)._

- [ ] NEW app/enterprise/simulation.py:450 - the SimulationPage live feed (SimulationPage.tsx:292-294) shows a hardcoded +$0.0010 cost_increment per stub step, and the real-graph path emits no cost_increment at all [severity: low]
- [ ] e2e/status-page.spec.ts never asserts backend data, so it passes on any response (agent-verse-frontend/e2e/status-page.spec.ts:4-28)
- [ ] No payload-level backend-contract verification for the other pages; route existence only, plus unit tests and e2e with mocked routes

### [NOT_IMPLEMENTED] Login session management (/auth/sessions)

_Unchanged: every /auth/sessions endpoint answers 401 when unauthenticated, otherwise an honest 501 (app/api/sessions.py:27-50). The frontend does not call it. No login-session recording or enforcement exists._

- [ ] No login-session recording and no per-request enforcement; every endpoint returns 501 (app/api/sessions.py:1-50)

### [NOT_IMPLEMENTED] Canonical routing runtime (app/routing_runtime)

_Unchanged. main.py:1292-1300 (Postgres) and 2621-2625 (in-memory) build the canonical model, skill and embedding routers and the decision store onto app.state. Grep finds no reader outside main.py, so no request path routes through them._

- [ ] canonical_{model,skill,embedding}_router and routing_decision_store are set on app.state but never read (app/main.py:1292-1300, 2621-2625)
- [ ] app/routing_runtime/tool_router.py and optimizer.py have zero importers

### [NOT_IMPLEMENTED] Orphan top-level runtime packages (zero importers outside their own package)

_explainability_runtime, recovery, capabilities, collaboration_runtime and ai_ops have zero importers outside their packages. provenance, plan_runtime and sandbox_runtime are referenced only as adapter_path strings in strategy_registry.py, which classifies them honestly as not_available/planned. The ai_ops API's module dicts are only a no-DB fallback; the lifespan wires AIOpsStore (main.py:2381-2388)._

- [ ] app/provenance, explainability_runtime, recovery, capabilities, plan_runtime, sandbox_runtime, collaboration_runtime and ai_ops are never imported by a runtime path. strategy_registry.py:1450-1480 names provenance, plan_runtime and sandbox_runtime as PLANNED/not_available (grep app/)

### [PASS] Goal templates (/templates)

_Both prior gaps were fixed by 40db7b298, which landed before the last rating but was not credited. A DB failure in list() now propagates, the route answers 503 (app/api/templates.py:321-327,573-578) instead of listing phantom built-ins, and a content-pack load failure is logged at ERROR. The DB store runs under tenant RLS with real {{param}} substitution and validation._

- [ ] Low: a broken content pack still yields [] built-ins (logged, not surfaced); seeding is best-effort and retried per tenant (templates.py:315-319)

### [PASS] Training data export

_No change since 78bb34870. Both endpoints resolve the tenant (training_export.py:23-35); the DB path runs one RLS-scoped CTE over evaluations/eval_scorecards/goals plus a single goal_steps query (training_export.py:166-249); DB errors become 503 (:53-63); the POST export needs a write role through scope_enforcement's unregistered-write guard (scope_enforcement.py:596-613)._

- [ ] NEW app/api/training_export.py:66 - GET /intelligence/export-training-data/preview (goal text and step outputs) has no registered scope, so any read-only or role-less key in the tenant can read it [severity: low]
- [ ] NEW app/api/training_export.py:246 - every example reports model 'unknown'; the model used is never recorded [severity: low]

### [PASS] OCR document extraction (/ocr)

_Fixed: persist_to_kb now passes request=request into _ingest_chunks_from_source, so the tenant's RAG_INGEST guardrails run (b3ef0907d, app/api/ocr.py:131-136). The router mounts through the guarded registry (routers.py:114). The batch endpoint is capped at 10 documents and reports succeeded/failed counts honestly._

- [ ] POST /ocr/batch turns a per-item exception into a None result with no per-item error reason; the failed count is still reported (app/api/ocr.py:333-335, 340-346)

### [PASS] Public status page router is never mounted

_FIXED by df515a292. public_status.router is included at app/bootstrap/routers.py:406-411. It reads the real HealthRegistry (app.state.health, with postgres/redis checks from pools) and reports degraded when failed_routers is non-empty (public_status.py:21-54). StatusPage now throws on !r.ok (StatusPage.tsx:25-28) and renders the error._

- [ ] NEW app/observability/health.py:34-58 - HealthRegistry.run has no per-check timeout or cache, so each anonymous GET /status (auth bypass, no per-tenant rate limit) pings Postgres and Redis live, and a hung dependency hangs /status instead of reporting 'degraded' [severity: low]
- [ ] NEW agent-verse-frontend/e2e/status-page.spec.ts:4-28 - the e2e spec still asserts only the heading and button, never the backend payload [severity: low]
