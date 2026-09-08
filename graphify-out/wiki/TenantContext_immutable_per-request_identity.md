# TenantContext (immutable per-request identity)

> God node · 482 connections · `agent-verse-backend/app/tenancy/context.py`

**Community:** [Persistence & Retry Services](Persistence_&_Retry_Services.md)

## Connections by Relation

### calls
- [create_app()](create_app.md) `EXTRACTED`
- run_goal (Celery task) `EXTRACTED`
- ._dispatch_event() `EXTRACTED`
- main() `EXTRACTED`
- .create_mission_and_execute() `EXTRACTED`
- receive_a2a_task() `EXTRACTED`
- resolve_tenant_from_jwt() `EXTRACTED`
- .run() `EXTRACTED`
- .run_goal() `EXTRACTED`
- _sync_source_async() `EXTRACTED`
- .check_tool_args() `EXTRACTED`
- slack_events() `EXTRACTED`
- slack_slash_command() `EXTRACTED`
- zapier_trigger() `EXTRACTED`
- .check_final_output() `EXTRACTED`
- email_approve_link() `EXTRACTED`
- email_reject_link() `EXTRACTED`
- .start() `EXTRACTED`
- _do_check_email_goals() `EXTRACTED`
- razorpay_webhook() `EXTRACTED`
- *…and 23 more `calls` connection(s) not listed (lowest-degree first to go)*

### consumes
- IdentityResolver `EXTRACTED`

### contains
- tenancy/context.py `EXTRACTED`

### extends
- SubTenantService (enterprise sub-tenant hierarchy) `INFERRED`

### imports
- app/main.py `EXTRACTED`
- tasks.py `EXTRACTED`
- api/knowledge.py `EXTRACTED`
- org/router.py `EXTRACTED`
- goal_service.py `EXTRACTED`
- rag/gateway.py `EXTRACTED`
- executor_mixin.py `EXTRACTED`
- chat/router.py `EXTRACTED`
- api/governance.py `EXTRACTED`
- agent/graph.py `EXTRACTED`
- agents.py `EXTRACTED`
- goals.py `EXTRACTED`
- tenants.py `EXTRACTED`
- rag/raft.py `EXTRACTED`
- rag_platform.py `EXTRACTED`
- triggers.py `EXTRACTED`
- schedules.py `EXTRACTED`
- collab.py `EXTRACTED`
- verifier_mixin.py `EXTRACTED`
- steps.py `EXTRACTED`
- *…and 88 more `imports` connection(s) not listed (lowest-degree first to go)*

### produces
- TenantMiddleware (API-key auth + rate limit + MFA gate) `EXTRACTED`

### rationale_for
- Immutable identity injected into every authenticated request. `EXTRACTED`

### references
- _tenant() `EXTRACTED`
- ._execute_step() `EXTRACTED`
- _require_tenant() `EXTRACTED`
- ._make_agent_loop_for_tenant() `EXTRACTED`
- .submit_goal() `EXTRACTED`
- .execute() `EXTRACTED`
- ._call_tool_impl() `EXTRACTED`
- .readiness_context() `EXTRACTED`
- .call_tool() `EXTRACTED`
- _require_tenant() `EXTRACTED`
- .discover_tools() `EXTRACTED`
- execute_goal_tree (DAG waves) `EXTRACTED`
- _require_tenant() `EXTRACTED`
- retrieve_web_results() `EXTRACTED`
- ._get_record() `EXTRACTED`
- .subscribe_events() `EXTRACTED`
- _require_tenant() `EXTRACTED`
- _require_tenant() `EXTRACTED`
- .ingest() `EXTRACTED`
- .route() `EXTRACTED`
- *…and 306 more `references` connection(s) not listed (lowest-degree first to go)*

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*