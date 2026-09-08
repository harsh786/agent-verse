# create_app()

> God node · 183 connections · `agent-verse-backend/app/main.py`

**Community:** [Scope & Role Seeding](Scope_&_Role_Seeding.md)

## Connections by Relation

### calls
- [TenantContext (immutable per-request identity)](TenantContext_immutable_per-request_identity.md) `EXTRACTED`
- GoalService `EXTRACTED`
- get_settings() `EXTRACTED`
- KnowledgeStore `EXTRACTED`
- ContextResolver `EXTRACTED`
- PlanTier / PLAN_LIMITS `EXTRACTED`
- OpenAICompatibleProvider `EXTRACTED`
- get_session_factory() `EXTRACTED`
- HITLGateway (dual-mode asyncio/Redis approval) `EXTRACTED`
- FakeProvider `EXTRACTED`
- MCPClient `EXTRACTED`
- RetrievalGateway `EXTRACTED`
- SemanticCache (3-layer L1/L2/L3) `EXTRACTED`
- ScheduleStore `EXTRACTED`
- IngestionJobTracker `EXTRACTED`
- RAFTService `EXTRACTED`
- IngestionPipeline `EXTRACTED`
- MCPRegistry `EXTRACTED`
- WorkflowService `EXTRACTED`
- SelfOptimizerV2 `EXTRACTED`
- *…and 154 more `calls` connection(s) not listed (lowest-degree first to go)*

### contains
- app/main.py `EXTRACTED`

### module_level_instantiation_with_manage_pools_true
- app = create_app(manage_pools=True) `EXTRACTED`

### parameterizes
- Settings (pydantic-settings) `EXTRACTED`

### reads_flag
- manage_pools flag `EXTRACTED`

### references
- Any `EXTRACTED`
- HealthCheck `EXTRACTED`

### uses
- ScopeEnforcementMiddleware `INFERRED`
- TenantMiddleware (API-key auth + rate limit + MFA gate) `INFERRED`
- SecurityHeadersMiddleware (OWASP headers) `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*